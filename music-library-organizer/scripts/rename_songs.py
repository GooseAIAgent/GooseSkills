#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一音频文件名为「歌手 - 歌名」格式（多歌手用「、」分隔）。

核心能力（全部来自实战验证）：
- 默认规则：文件名 base 含 " - " → 左侧为歌手、右侧为歌名；
  含无空格 "-" → 同样切分（顺序由 --flip 规则或内嵌标签校正）。
- --tags 以内嵌标签为准解析（解决文件名顺序混乱/歧义）。
- 伴生文件同步改名：同名 .lrc 歌词、.cover/<原名>.<ext>.<img> 封面。
- NFC 归一化处理 macOS 文件名，冲突自动加 " (2)" 后缀。
- --dry-run 只打印计划不实际改名。

用法:
    python3 rename_songs.py /Volumes/media-2/music --dry-run
    python3 rename_songs.py /Volumes/media-2/music
    python3 rename_songs.py /path/to/dir --skip-dir "重复副本" --dry-run
"""
import argparse
import os
import re
import sys
import unicodedata

AUDIO_EXTS = (".flac", ".mp3", ".ape", ".m4a", ".wav", ".ogg")
IMG_EXTS = (".jpeg", ".jpg", ".png", ".webp")

# 显式映射表：无法按通用规则解析的文件，在此登记 (旧文件名base -> (歌手, 歌名))
# 例如倒序命名「歌名-歌手」且无内嵌标签可依的文件。
EXPLICIT_MAP = {
    # '一曲相思-阿悠悠': ('阿悠悠', '一曲相思'),
}

# 倒序规则：某些文件夹整体是「歌名-歌手」或「歌名 - 歌手」命名，
# 按顶层文件夹名登记，对其中所有文件反转切分顺序。
FLIP_DIRS = set()  # 例: {'海来阿木'}


def norm_singers(raw):
    """把各种多歌手分隔符统一为「、」。"""
    parts = re.split(r"\s*[&、,，;；_]\s*", raw.strip())
    return [p.strip() for p in parts if p.strip()]


def parse(base, top_dir, use_tags=False, file_path=None):
    """解析出 (singers_list, title)。返回 None 表示无法解析。"""
    if base in EXPLICIT_MAP:
        s, t = EXPLICIT_MAP[base]
        return s.split("、"), t

    # 优先用内嵌标签判定
    if use_tags and file_path:
        try:
            from mutagen import File
            f = File(file_path, easy=True)
            t = (f.get("title") or [""])[0].strip()
            a = (f.get("artist") or [""])[0].strip()
            if t and a and "ifulicn" not in (t + a).lower():
                return norm_singers(a), t
        except Exception:
            pass

    flip = top_dir in FLIP_DIRS
    if " - " in base:
        left, right = base.split(" - ", 1)
    elif "-" in base:
        left, right = base.split("-", 1)
    else:
        return None
    if flip:
        return norm_singers(right), left.strip()
    return norm_singers(left), right.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tags", action="store_true",
                    help="用内嵌标签替代文件名切分来判定歌手/歌名")
    ap.add_argument("--skip-dir", action="append", default=[],
                    help="跳过这些名字的文件夹(可多次)")
    args = ap.parse_args()

    renamed = kept = failed = 0
    for dp, dn, fns in os.walk(args.root):
        if "/." in dp:
            continue
        rel_top = os.path.relpath(dp, args.root).split(os.sep)[0]
        if any(s in dp for s in args.skip_dir):
            continue
        for fn in sorted(fns):
            if not fn.lower().endswith(AUDIO_EXTS):
                continue
            p = os.path.join(dp, fn)
            base = unicodedata.normalize("NFC", fn.rsplit(".", 1)[0])
            r = parse(base, rel_top, use_tags=args.tags, file_path=p)
            if not r:
                print("SKIP-PARSE:", os.path.relpath(p, args.root))
                continue
            singers, title = r
            ext = os.path.splitext(fn)[1]
            newbase = "、".join(singers) + " - " + title
            if newbase == base:
                kept += 1
                continue
            dst = os.path.join(dp, newbase + ext)
            n = 2
            while os.path.exists(dst):
                dst = os.path.join(dp, f"{newbase} ({n}){ext}")
                n += 1
            final = os.path.basename(dst).rsplit(".", 1)[0]
            print(f"{os.path.relpath(p, args.root)}  ->  {final}{ext}")
            if args.dry_run:
                continue
            try:
                # 伴生 .lrc
                lrc = os.path.join(dp, base + ".lrc")
                if os.path.exists(lrc):
                    os.rename(lrc, os.path.join(dp, final + ".lrc"))
                # 伴生 .cover 封面
                cvd = os.path.join(dp, ".cover")
                if os.path.isdir(cvd):
                    for cf in os.listdir(cvd):
                        if cf.startswith(base + "."):
                            rest = cf[len(base):]
                            if rest.lower().endswith(IMG_EXTS) or rest.lower().endswith(AUDIO_EXTS + IMG_EXTS):
                                try:
                                    os.rename(os.path.join(cvd, cf),
                                              os.path.join(cvd, final + rest))
                                except OSError as e:
                                    print("  封面改名失败(跳过):", cf, e)
                os.rename(p, dst)
                renamed += 1
            except OSError as e:
                failed += 1
                print("  FAIL:", e)

    print(f"\n改名: {renamed} | 保持不变: {kept} | 失败: {failed}")


if __name__ == "__main__":
    sys.exit(main())
