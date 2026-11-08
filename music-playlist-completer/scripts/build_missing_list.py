#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析歌单（m3u8 / CSV），与本地音乐目录比对，产出缺失曲目清单 missing.json。

支持输入：
  1. m3u8 文件：#EXTINF:dur,歌手 - 歌名 + 本地路径行 或 网易云链接行（id=NNN）
  2. CSV 文件：需含「歌名」「歌手」列，可选「专辑」「时长」「链接/网易云链接」列
     （网易云收藏歌单导出 CSV 即为此格式）

本地匹配规则：
  - 以「歌手 - 歌名」标准化后（去空白标点、忽略大小写）作为主键
  - 递归扫描 --lib 指定的所有目录（可多次），收集 flac/mp3/m4a/ogg

用法：
  python3 build_missing_list.py --playlist 歌单.m3u8 --lib /path/to/music [--out DIR]
  输出 <out>/missing.json（统计同时打印到终端）
"""
import argparse, csv, json, os, re, sys, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcommon import norm, safe, resolve_out, add_common_args

AUDIO_EXT = (".flac", ".mp3", ".m4a", ".ogg")


def parse_m3u8(path):
    """返回条目列表 {t,a,alb,dur,sid,local}。"""
    items = []
    meta = None
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("#EXTINF:"):
            body = line[len("#EXTINF:"):]
            dur = 0
            m = re.match(r"(-?[\d.]+)\s*,(.*)", body)
            if m:
                try: dur = float(m.group(1))
                except ValueError: pass
                body = m.group(2)
            if " - " in body:
                a, t = body.split(" - ", 1)
            else:
                a, t = "", body
            meta = {"t": t.strip(), "a": a.strip(), "alb": "", "dur": int(dur or 0),
                    "sid": "", "local": ""}
        elif line and not line.startswith("#") and meta is not None:
            if re.match(r"https?://", line):
                m = re.search(r"id=(\d+)", line)
                meta["sid"] = m.group(1) if m else ""
            elif os.path.exists(line):
                meta["local"] = os.path.abspath(line)
            items.append(meta)
            meta = None
    return items


def parse_csv(path):
    items = []
    for row in csv.DictReader(open(path, encoding="utf-8-sig")):
        cols = {re.sub(r"\s", "", k): (v or "").strip() for k, v in row.items()}
        t = cols.get("歌名") or cols.get("歌曲") or cols.get("title") or ""
        a = cols.get("歌手") or cols.get("艺术家") or cols.get("artist") or ""
        if not t:
            continue
        alb = cols.get("专辑") or cols.get("album") or ""
        durs = cols.get("时长") or cols.get("长度") or ""
        try: dur = int(float(durs))
        except ValueError: dur = 0
        link = cols.get("网易云链接") or cols.get("链接") or cols.get("url") or ""
        m = re.search(r"id=(\d+)", link) if link else None
        sid = m.group(1) if m else ""
        items.append({"t": t, "a": a, "alb": alb, "dur": dur, "sid": sid, "local": ""})
    return items


def scan_lib(dirs):
    """返回 {标准化主键: 本地绝对路径}，键包括「歌手-歌名」与「歌名」两种。"""
    idx = {}
    def norm_key(s):
        return norm(s).casefold()
    for d in dirs:
        for dp, _, fs in os.walk(d):
            for f in fs:
                if not f.lower().endswith(AUDIO_EXT):
                    continue
                stem = os.path.splitext(f)[0]
                p = os.path.abspath(os.path.join(dp, f))
                if " - " in stem:
                    a, t = stem.split(" - ", 1)
                    idx.setdefault(norm_key(f"{a} - {t}"), p)
                idx.setdefault(norm_key(stem), p)
    return idx


def main():
    ap = argparse.ArgumentParser(description="生成歌单缺失曲目清单")
    add_common_args(ap)
    ap.add_argument("--playlist", required=True, help="歌单文件：.m3u8 或 .csv")
    ap.add_argument("--lib", action="append", default=[],
                    help="本地音乐目录，可重复多次")
    ap.add_argument("--keep-local", action="store_true",
                    help="missing.json 中保留 local 字段（默认省略）")
    a = ap.parse_args()

    pl_path = a.playlist
    items = parse_m3u8(pl_path) if pl_path.lower().endswith((".m3u8", ".m3u")) else parse_csv(pl_path)
    print(f"歌单条目: {len(items)}")
    if not items:
        print("歌单解析结果为空，请检查文件格式。"); sys.exit(1)

    idx = scan_lib(a.lib) if a.lib else {}
    missing, hit_local = [], 0
    for x in items:
        if x["local"]:
            hit_local += 1; continue
        key = norm(f'{x["a"]} - {x["t"]}').casefold()
        if key in idx:
            hit_local += 1; continue
        key2 = norm(x["t"]).casefold()
        if not x["a"] and key2 in idx:
            hit_local += 1; continue
        rec = {k: x[k] for k in ("t", "a", "alb", "dur", "sid")}
        if a.keep_local: rec["local"] = x["local"]
        missing.append(rec)

    root = resolve_out(a.out)
    os.makedirs(root, exist_ok=True)
    out_path = os.path.join(root, "missing.json")
    json.dump(missing, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    with_sid = sum(1 for x in missing if x.get("sid"))
    print(f"本地已有: {hit_local} | 缺失: {len(missing)}（其中含网易云ID {with_sid} 条）")
    print(f"写出: {out_path}")
    if missing and not with_sid:
        print("提示：缺失条目均无网易云 ID，netease_dl.py 需要 sid（建议用带链接的 CSV/歌单来源）。")


if __name__ == "__main__":
    main()
