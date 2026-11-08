#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量写入内嵌标签：专辑名/发行日期从音乐平台核实后回写 title/artist/album/date。

与 fix_tags.py 的分工：
- fix_tags.py  —— 修水印/从文件名反写，专辑名取自「年份-专辑名」文件夹。
- album_tags.py—— 目录是**纯年份**（如 2019/、2020/）或专辑信息缺失时，
                  逐首查平台补全专辑名与发行日期，再连同文件名反写的
                  title/artist 一起写入。

实测要点（真实踩坑，勿改）：
1. QQ 音乐 search 会被**连续限流**，表现为「成片返回空列表」而不是报错。
   判据：空结果集中在连续编号区间。对策：空结果退避重试，间隔递增。
2. 匹配排序必须先比「专辑名 == 歌名」（单曲），再看年份，最后看日期。
   否则《浪子心》会被配到更早的合辑《海来阿木的电台》。
3. 查不到就留空并在报告里点名，**不要拿文件夹年份冒充专辑名**。
4. 缓存 JSON 支持断点续跑，限流中断后重跑只补缺口。

用法:
    python3 album_tags.py /path/to/歌手 --dry-run        # 只预览
    python3 album_tags.py /path/to/歌手                  # 查询+写入
    python3 album_tags.py /path/to/歌手 --write-only     # 用已有缓存直接写
    python3 album_tags.py /path/to/歌手 --cache my.json  # 指定缓存
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from music_api import qq_search  # noqa: E402

AUDIO_EXTS = (".flac", ".mp3")
SKIP_DIRS = {"重复文件待核对", "#recycle", "@eaDir", ".Trash"}


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def norm_match(s):
    """宽松归一：去括号说明、去标点空格、统一全半角。用于歌名相等判定。"""
    s = nfc(s)
    s = s.replace("（", "(").replace("）", ")").replace("【", "[").replace("】", "]")
    s = re.sub(r"[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"[\s\-_·、,，.。!！?？\"'“”‘’]", "", s).lower()
    return s


def norm_album(s):
    """专辑名归一：只去空格，保留括号里的版本标记（民谣版/Live 等）。"""
    return re.sub(r"\s+", "", nfc(s)).lower()


def clean_album(s):
    """写盘用：去掉中文与左括号之间的多余空格，其余保持平台原样。"""
    s = nfc(s).strip()
    s = re.sub(r"\s+([\(（\[])", r"\1", s)
    return re.sub(r"\s{2,}", " ", s)


def parse_base(base):
    if " - " in base:
        a, t = base.split(" - ", 1)
        return a.strip(), t.strip()
    if "-" in base:
        a, t = base.split("-", 1)
        return a.strip(), t.strip()
    return None


def collect(root):
    items = []
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(fns):
            if not fn.lower().endswith(AUDIO_EXTS):
                continue
            base = nfc(os.path.splitext(fn)[0])
            parsed = parse_base(base)
            if not parsed:
                print(f"无法解析文件名(跳过): {os.path.relpath(os.path.join(dp, fn), root)}")
                continue
            artist, title = parsed
            items.append({
                "path": os.path.join(dp, fn),
                "base": base,
                "artist": artist,
                "title": title,
                "folder_year": os.path.basename(dp),
            })
    return items


VERSION_MARK = re.compile(r"(DJ|Remix|Live|伴奏|纯音乐|Instrumental|翻唱|Cover|片段|铃声)", re.I)
DATE_OK = re.compile(r"^(19|20)\d{2}(-\d{2}(-\d{2})?)?$")


def valid_date(s):
    """平台常回 '0000-00-00' 或空串，只接受真实年月。"""
    return bool(s and DATE_OK.match(s.strip()))


def has_version_mark(s):
    return bool(VERSION_MARK.search(nfc(s)))


def pick(results, artist, title, folder_year, lead_artist):
    """从搜索结果挑最可信一条。返回 (记录, 候选数) 或 (None, 0)。"""
    nt = norm_match(title)
    at = norm_album(title)
    want_ver = has_version_mark(title)     # 目标本身是否带版本标记
    cands = []
    for r in results:
        if norm_match(r["song"]) != nt:
            continue
        # 目标不带版本标记时，排除 DJ/Remix/Live/伴奏 等衍生版专辑
        if not want_ver and has_version_mark(r["album"]):
            continue
        arts = [norm_match(a) for a in re.split(r"[/、,，&]", r["artists"])]
        if not any(norm_match(lead_artist) in a for a in arts):
            continue
        cands.append({**r, "year": (r["date"] or "")[:4]})

    if not cands:
        return None, 0

    def key(c):
        # 1) 专辑名与歌名一致（单曲）最优  2) 年份与所在目录一致  3) 日期最早（原创首发）
        return (norm_album(c["album"]) != at,
                c["year"] != folder_year,
                c["date"] or "9999",
                not c["album"])

    cands.sort(key=key)
    return cands[0], len(cands)


def lookup(items, cache, sleep_s, lead_artist_of):
    todo = [it for it in items if it["path"] not in cache]
    print(f"共 {len(items)} 首，已缓存 {len(items) - len(todo)}，待查 {len(todo)}", flush=True)

    for i, it in enumerate(todo, 1):
        lead = lead_artist_of(it)
        q = f"{lead} {it['title']}"
        got, n = None, 0
        for attempt in range(4):
            try:
                res = qq_search(q, n=10)
            except Exception as e:
                print(f"  [{i}/{len(todo)}] {it['base']} 查询异常: {e}", flush=True)
                time.sleep(5 * (attempt + 1))
                continue
            if not res:                      # 空列表=限流，退避重试
                time.sleep(4 * (attempt + 1))
                continue
            got, n = pick(res, it["artist"], it["title"], it["folder_year"], lead)
            break

        if got:
            cache[it["path"]] = {"album": got["album"], "date": got["date"],
                                 "artists": got["artists"], "song": got["song"],
                                 "n_cands": n}
            print(f"  [{i}/{len(todo)}] {it['base']}  =>  {got['album']} ({got['date']})", flush=True)
        else:
            cache[it["path"]] = {"album": "", "date": "", "artists": "", "song": "", "n_cands": 0}
            print(f"  [{i}/{len(todo)}] {it['base']}  =>  未找到", flush=True)
        time.sleep(sleep_s)
    return cache


def write_tags(path, title, artist, album, date, albumartist):
    if path.lower().endswith(".mp3"):
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TPE2
        tags = ID3(path)
        tags["TIT2"] = TIT2(encoding=3, text=title)
        tags["TPE1"] = TPE1(encoding=3, text=artist)
        if album:
            tags["TALB"] = TALB(encoding=3, text=album)
        if date:
            tags["TDRC"] = TDRC(encoding=3, text=date)
        if albumartist:
            tags["TPE2"] = TPE2(encoding=3, text=albumartist)
        tags.save(path)
    else:
        from mutagen.flac import FLAC
        f = FLAC(path)
        f["title"] = title
        f["artist"] = artist
        if album:
            f["album"] = album
        if date:
            f["date"] = date
        if albumartist:
            f["albumartist"] = albumartist
        f.save()


def main():
    ap = argparse.ArgumentParser(description="从平台补全专辑信息并批量写入内嵌标签")
    ap.add_argument("root", help="歌手目录，如 /Volumes/media-2/music/海来阿木")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不查询不写盘")
    ap.add_argument("--write-only", action="store_true", help="跳过查询，直接用缓存写标签")
    ap.add_argument("--cache", default=None, help="缓存 JSON 路径（默认 <root>/../.<歌手>_album_cache.json）")
    ap.add_argument("--sleep", type=float, default=2.2, help="每首查询间隔秒数，默认 2.2")
    ap.add_argument("--albumartist", default=None,
                    help="albumartist 字段，默认取目录名（如 海来阿木）")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        sys.exit(f"目录不存在: {args.root}")
    try:
        import mutagen  # noqa: F401
    except ImportError:
        sys.exit("缺少 mutagen，请先 pip3 install --user mutagen")

    root = os.path.abspath(args.root)
    cache_path = args.cache or os.path.join(
        os.path.dirname(root), f".{os.path.basename(root)}_album_cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    items = collect(root)
    if not items:
        sys.exit("未找到音频文件")

    def lead_of(it):
        return it["artist"].split("、")[0].strip()

    if not args.dry_run and not args.write_only:
        cache = lookup(items, cache, args.sleep, lead_of)
        json.dump(cache, open(cache_path, "w"), ensure_ascii=False, indent=1)
        print(f"\n缓存已保存: {cache_path}")

    albumartist = args.albumartist if args.albumartist is not None else os.path.basename(root)

    plan, miss = [], []
    for it in items:
        info = cache.get(it["path"])
        if not info or not info.get("album"):
            miss.append(it)
            continue
        date = info.get("date") if valid_date(info.get("date")) else ""
        if not date and re.match(r"^(19|20)\d{2}$", it["folder_year"]):
            date = it["folder_year"]      # 仅当目录名本身是 4 位年份才兜底
        plan.append((it, clean_album(info["album"]), date))

    print(f"\n计划写标签 {len(plan)} 首，缺专辑信息 {len(miss)} 首")
    for it, album, date in plan:
        if args.dry_run:
            print(f"  {os.path.relpath(it['path'], root)}")
            print(f"      title={it['title']!r} artist={it['artist']!r} album={album!r} date={date!r}")
    for it in miss:
        print(f"  [缺专辑] {os.path.relpath(it['path'], root)}")

    if args.dry_run:
        print("\n（--dry-run：未查询、未写盘）")
        return

    fixed = 0
    for it, album, date in plan:
        try:
            write_tags(it["path"], it["title"], it["artist"], album, date, albumartist)
            fixed += 1
        except Exception as e:
            print(f"  写入失败 {os.path.basename(it['path'])}: {e}")

    print(f"\n已写入 {fixed} 首 | 缺专辑信息 {len(miss)} 首")
    if miss:
        print("以下歌曲需人工核实专辑（脚本不会用文件夹年份冒充）:")
        for it in miss:
            print("  ", os.path.relpath(it["path"], root))


if __name__ == "__main__":
    main()
