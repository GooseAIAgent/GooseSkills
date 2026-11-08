#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对剩余曲目做宽松探查：只按歌名搜，列出候选与其可获取的最高音质。
输出 CSV 供人工决策，不下载任何文件。

用法：
  python3 qq_probe.py missing.json [--out DIR] [--config CFG]
"""
import argparse, csv, json, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcommon import load_config, resolve_out, norm, title_variants, add_common_args
from qq_dl import search, vkey, qq_cookie, qq_uin

CODES = [("F000", "flac", "FLAC"), ("M800", "mp3", "320k"), ("C400", "mp3", "128k")]


def best_quality(c, ck, uin):
    for code, ext, name in CODES:
        if vkey(c["mid"], code, ext, ck, uin):
            return name
    return "无权"


def probe(it, ck, uin):
    a, t, dur = it["a"], it["t"], it.get("dur") or 0
    rows = []
    for tv in title_variants(t)[:2]:
        try:
            cands = search(tv, ck, 10)
        except Exception:
            continue
        seen = set()
        for c in cands:
            if c["mid"] in seen: continue
            seen.add(c["mid"])
            if norm(tv) not in norm(c["name"]) and norm(c["name"]) not in norm(tv):
                continue
            if dur and c["interval"] and abs(c["interval"] - dur) > 15:
                continue
            rows.append({"歌手": a, "歌名": t, "时长": dur,
                         "QQ歌名": c["name"], "QQ歌手": "/".join(c["singers"]),
                         "QQ时长": c["interval"], "音质": best_quality(c, ck, uin)})
        if rows: break
    if not rows:
        rows.append({"歌手": a, "歌名": t, "时长": dur, "QQ歌名": "—无搜索结果—",
                     "QQ歌手": "", "QQ时长": "", "音质": ""})
    return rows


def main():
    ap = argparse.ArgumentParser()
    add_common_args(ap)
    ap.add_argument("list", help="缺失曲目清单 JSON")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    root = resolve_out(a.out)
    out_path = os.path.join(root, "QQ候选对照.csv")
    items = json.load(open(a.list, encoding="utf-8"))
    cfg, cfg_path = load_config(a.config)
    ck = qq_cookie(cfg)
    if not ck:
        print(f"缺少登录态：请在 {cfg_path} 配置 QQ 凭证。")
        sys.exit(1)
    uin = qq_uin(ck)
    out = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(probe, it, ck, uin) for it in items]
        for i, fu in enumerate(as_completed(futs), 1):
            try: out.extend(fu.result())
            except Exception: pass
            if i % 10 == 0: print(f"  {i}/{len(items)}", flush=True)
    os.makedirs(root, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["歌手", "歌名", "时长", "QQ歌名", "QQ歌手", "QQ时长", "音质"])
        w.writeheader(); w.writerows(out)
    print("写出:", out_path, "行数:", len(out))


if __name__ == "__main__":
    main()
