#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复音频内嵌标签：从文件名反写 标题/艺术家/专辑，清除下载站水印。

典型场景：播放器显示「ifulicn.com-ifulicn.com」等水印——播放器读取的是
内嵌标签而非文件名，需用干净的文件名反写标签。

处理逻辑：
- MP3(ID3)：写 TIT2/TPE1/TALB(UTF-8)，删除 COMM/TCOM/TCON/TPE2 水印字段，保留 APIC 封面。
- FLAC：写 vorbis comment 的 title/artist/album，并**扫描全部字段**清除水印
  （下载站常把 GENRE/COMMENT/ALBUMARTIST/COMPOSER 一起污染，只改前三个会残留）。
- APE(APEv2)：写 Title/Artist/Album，同样扫描全部字段清水印（ALBUM ARTIST/Copyright 常被塞站点名）。
- 专辑名默认取所在「年份-专辑名」文件夹去掉年份前缀的部分；无该结构则不写专辑。
- 文件名分隔符支持 " - " 和 "-"（前者优先）；--check 只检查不修改。

用法:
    python3 fix_tags.py ~/Music/IU李知恩            # 修复
    python3 fix_tags.py ~/Music/IU李知恩 --check    # 只检查
    python3 fix_tags.py /path --ext mp3                            # 只处理mp3
    python3 fix_tags.py /path --albumartist auto                   # 按文件名补 albumartist
"""
import argparse
import os
import re
import sys

WATERMARKS = ["ifulicn"]


def parse_base(base):
    """文件名 base -> (artist, title)。"""
    if " - " in base:
        a, t = base.split(" - ", 1)
        return a.strip(), t.strip()
    if "-" in base:
        a, t = base.split("-", 1)
        return a.strip(), t.strip()
    return None


def album_of_dir(dirpath, root):
    """从「年份-专辑名」文件夹名提取专辑名；顶层目录返回 None。"""
    name = os.path.basename(dirpath)
    if os.path.abspath(dirpath) == os.path.abspath(root):
        return None
    m = re.match(r"^\d{4}-(.+)$", name)
    return m.group(1) if m else (name if name != os.path.basename(root) else None)


def polluted(val):
    """命中任一站名关键字即视为污染。"""
    s = str(val).lower()
    return any(w in s for w in WATERMARKS)


def all_text(f):
    """FLAC/MP3 easy 接口下所有字段的值拼一起，用于水印检测。"""
    out = []
    for k in ("title", "artist", "album", "albumartist", "genre", "comment", "composer"):
        out.extend(f.get(k) or [])
    return " ".join(out).lower()


def fix_mp3(path, artist, title, album, albumartist):
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2
    tags = ID3(path)
    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags["TPE1"] = TPE1(encoding=3, text=artist)
    if album:
        tags["TALB"] = TALB(encoding=3, text=album)
    # 水印字段一律删除（TPE2 若需保留 albumartist，下面重建）
    for key in list(tags.keys()):
        if key.startswith("COMM") or key in ("TCOM", "TCON", "TPE2"):
            del tags[key]
    if albumartist:
        tags["TPE2"] = TPE2(encoding=3, text=albumartist)
    tags.save(path)


def fix_flac(path, artist, title, album, albumartist):
    from mutagen.flac import FLAC
    f = FLAC(path)
    f["title"] = title
    f["artist"] = artist
    if album:
        f["album"] = album

    # 关键：扫描全部 vorbis 字段，带水印的删掉（GENRE/COMMENT/COMPOSER/ALBUMARTIST…）
    for key in list(f.keys()):
        if key.lower() in ("title", "artist", "album"):
            continue
        if any(polluted(v) for v in f[key]):
            del f[key]

    if albumartist:
        f["albumartist"] = albumartist
    f.save(path)


def fix_ape(path, artist, title, album, albumartist):
    """APEv2 标签（.ape 单文件无损）。字段名用 Title/Artist/Album/Year/Album Artist。"""
    from mutagen.apev2 import APEv2, APEValue
    t = APEv2(path)
    t["Title"] = APEValue(title, 0)
    t["Artist"] = APEValue(artist, 0)
    if album:
        t["Album"] = APEValue(album, 0)
    for key in list(t.keys()):
        if key.lower() in ("title", "artist", "album"):
            continue
        if polluted(t[key]):
            del t[key]
    if albumartist:
        t["Album Artist"] = APEValue(albumartist, 0)
    t.save(path)


def main():
    global WATERMARKS
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--check", action="store_true", help="只检查不修改")
    ap.add_argument("--ext", action="append", default=[],
                    help="限定扩展名(可多次)，默认 flac+mp3+ape")
    ap.add_argument("--watermark", default="ifulicn",
                    help="水印关键字，多个用逗号分隔，如 ifulicn,chenjiebiao.cn,雅音FM")
    ap.add_argument("--albumartist", default="",
                    help="写入 albumartist；传 auto 则取文件名里的歌手；留空不写")
    args = ap.parse_args()

    WATERMARKS = [w.strip().lower() for w in args.watermark.split(",") if w.strip()]
    exts = tuple(e.lower() if e.startswith(".") else "." + e.lower()
                 for e in (args.ext or ["flac", "mp3", "ape"]))

    fixed = checked = bad = 0
    for dp, dn, fns in os.walk(args.root):
        if "/." in dp:
            continue
        album = album_of_dir(dp, args.root)
        for fn in sorted(fns):
            if not fn.lower().endswith(exts):
                continue
            path = os.path.join(dp, fn)
            base = fn.rsplit(".", 1)[0]
            r = parse_base(base)
            checked += 1
            if not r:
                bad += 1
                print("无法解析文件名(跳过):", os.path.relpath(path, args.root))
                continue
            artist, title = r
            aa = artist if args.albumartist == "auto" else args.albumartist

            dirty = False
            try:
                from mutagen import File
                f = File(path, easy=True)
                cur = {k: (f.get(k) or [""])[0] for k in ("title", "artist", "album")}
                dirty = (polluted(all_text(f))
                         or not cur["title"] or not cur["artist"]
                         or cur["title"] != title or cur["artist"] != artist)
            except Exception as e:
                print("读取失败:", fn, e)
                continue
            if not dirty:
                continue
            bad += 1
            print(f"{'[需修复]' if args.check else '[修复中]'} "
                  f"{os.path.relpath(path, args.root)} "
                  f"-> title={title!r} artist={artist!r} album={album!r}")
            if args.check:
                continue
            try:
                if fn.lower().endswith(".mp3"):
                    fix_mp3(path, artist, title, album, aa)
                elif fn.lower().endswith(".ape"):
                    fix_ape(path, artist, title, album, aa)
                else:
                    fix_flac(path, artist, title, album, aa)
                fixed += 1
            except Exception as e:
                print("  写入失败:", e)

    print(f"\n检查: {checked} | 需处理: {bad} | 已修复: {fixed}")


if __name__ == "__main__":
    sys.exit(main())
