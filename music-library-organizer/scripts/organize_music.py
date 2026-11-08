#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「映射表」把歌曲移动/归类到目标文件夹，带完整安全防护。

映射表为 JSON 文件，格式：
    {
      "歌手 - 歌名.flac": "2018-平凡的一天",
      "歌手 - 歌名2.flac": "2020-合辑",
      ...
    }
目标文件夹自动创建在 root 下。

安全防护（实战踩坑经验）：
- 未出现在映射表中的文件 → 报错退出，防止遗漏。
- 移动一律用 /bin/mv 子进程（部分卷宗的 os.rename 有幻影副本问题）。
- 移动后 MD5 校验源与目标：若源仍存在且内容与目标相同（幻影副本），
  自动移入「重复文件待核对」文件夹，不静默删除。
- --dry-run 只打印计划。

用法:
    python3 organize_music.py /Volumes/media-2/music/毛不易 map.json --dry-run
    python3 organize_music.py /Volumes/media-2/music/毛不易 map.json
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys


def md5(path, bs=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(bs), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("map", help="映射表 JSON 文件: 文件名 -> 目标文件夹名")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dup-dir", default="重复文件待核对")
    args = ap.parse_args()

    M = json.load(open(args.map, encoding="utf-8"))
    files = [f for f in os.listdir(args.root)
             if os.path.isfile(os.path.join(args.root, f)) and f != ".DS_Store"]
    unmapped = [f for f in files if f not in M]
    if unmapped:
        print("!! 以下文件不在映射表中，请补充后再执行:")
        for f in unmapped:
            print("   ", f)
        sys.exit(1)

    dup_dir = os.path.join(args.root, args.dup_dir)
    moved = ghosts = failed = 0
    for fn, folder in sorted(M.items()):
        src = os.path.join(args.root, fn)
        if not os.path.exists(src):
            continue  # 之前轮次已移动
        fd = os.path.join(args.root, folder)
        dst = os.path.join(fd, fn)
        print(f"{fn}  ->  {folder}/")
        if args.dry_run:
            moved += 1
            continue
        os.makedirs(fd, exist_ok=True)
        if os.path.exists(dst):
            print("  目标已存在，跳过:", folder, "/", fn)
            continue
        r = subprocess.run(["/bin/mv", src, dst], capture_output=True, text=True)
        if r.returncode != 0:
            failed += 1
            print("  mv失败:", r.stderr.strip())
            continue
        moved += 1
        # 幻影副本检测：源未消失且与目标内容相同
        if os.path.exists(src):
            if md5(src) == md5(dst):
                os.makedirs(dup_dir, exist_ok=True)
                subprocess.run(["/bin/mv", src, os.path.join(dup_dir, fn)],
                               check=False)
                ghosts += 1
                print("  幻影副本已移入", args.dup_dir)
            else:
                print("  !! 源仍存在且内容不同，请人工核查:", src)

    print(f"\n移动: {moved} | 幻影副本: {ghosts} | 失败: {failed}")


if __name__ == "__main__":
    sys.exit(main())
