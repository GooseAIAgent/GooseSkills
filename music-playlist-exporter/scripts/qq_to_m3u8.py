#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 音乐歌单 → 本地 m3u8 歌单，端到端导出工具。

与 netease_to_m3u8.py 同一套流程与匹配规则，只换「抓取」这一层：
  1. 抓取 QQ 歌单完整曲目（c.y.qq.com fcg 接口，公开歌单无需登录）
  2. 扫描本地音乐库，逐首比对是否已拥有（严格 + 宽松三层匹配，歌手一致性校验）
  3. 导出 CSV 清单（完整清单 + 未在本地库中的歌曲）
  4. 生成两份 m3u8：完整版（本地缺失的路径留空）+ 本地可播版

用法:
    # 全流程（联网抓取 + 本地比对 + 导出）
    python3 qq_to_m3u8.py "https://y.qq.com/n/ryqq/playlist/8888050823" \
        --music-root /Volumes/media-2/music --outdir ./歌单导出

    # 离线模式：基于已有清单 CSV 重新比对本地库并导出（不联网）
    python3 qq_to_m3u8.py --csv 歌单完整清单.csv --name "歌单名" \
        --music-root /Volumes/media-2/music --outdir ./歌单导出

    # 小样本联网测试
    python3 qq_to_m3u8.py <链接> --limit 5 --outdir /tmp/test

支持的歌单链接形式：
    https://y.qq.com/n/ryqq/playlist/{disstid}
    https://y.qq.com/n/ryqq/playsquare/{disstid}
    https://i.y.qq.com/n2/m/share/details/taoge.html?id={disstid}
    纯数字 disstid

依赖：仅 Python 3.8+ 标准库。公开歌单无需登录；私密歌单无法匿名抓取。
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# 复用网易云版的三层匹配与导出逻辑（同一套 CSV 字段与 m3u8 约定）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netease_to_m3u8 import (match_all, write_csvs, write_m3u8, sanitize,
                             load_csv)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAGE_SIZE = 100          # 分页大小；接口一次回全量时首屏即结束
POLITE_GAP = 1.0         # 分页请求间隔


def qq_get_json(url, referer="https://y.qq.com/", retries=4):
    """带退避的 GET。QQ 接口异常码或网络错误时指数退避重试。"""
    delay = 3
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": referer})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if data.get("code") == 0:
                return data
            last = f"code={data.get('code')}"
        except Exception as e:
            last = str(e)[:80]
        print(f"  接口异常({last})，退避 {delay}s 后重试…")
        time.sleep(delay)
        delay = min(delay * 2, 30)
    sys.exit(f"多次失败（{last}），请稍后再跑")


def parse_playlist_id(text):
    """从链接或纯数字中解析 disstid。"""
    text = text.strip()
    m = (re.search(r"[?&]id=(\d+)", text)
         or re.search(r"/(?:playlist|playsquare)/(\d+)", text)
         or re.search(r"^(\d{5,})$", text))
    if not m:
        sys.exit(f"无法从输入中解析 QQ 歌单 disstid: {text!r}")
    return m.group(1)


def _song_row(s):
    """从 songlist 条目提取统一字段；兼容 trackInfo 嵌套等形状。"""
    ti = s.get("trackInfo") or s
    name = html.unescape(ti.get("songname") or ti.get("name") or "").strip()
    singers = "、".join(html.unescape(x.get("name", ""))
                        for x in ti.get("singer") or [])
    album = html.unescape(ti.get("albumname")
                          or (ti.get("album") or {}).get("name") or "").strip()
    interval = int(ti.get("interval") or 0)
    mid = ti.get("songmid") or ti.get("mid") or ""
    link = f"https://y.qq.com/n/ryqq/songDetail/{mid}" if mid else ""
    return {"歌曲名": name, "歌手": singers, "专辑": album,
            "时长(秒)": str(interval), "歌曲链接": link}


def fetch_playlist(dissid, limit=0):
    print(f"抓取 QQ 歌单 {dissid} …")
    rows, seen_mids, seen_titles = [], set(), set()
    begin, total, name = 0, None, f"歌单{dissid}"

    while True:
        qs = urllib.parse.urlencode({
            "type": 1, "disstid": dissid, "loginUin": 0, "hostUin": 0,
            "format": "json", "inCharset": "utf8", "outCharset": "utf-8",
            "notice": 0, "platform": "yqq.json", "needNewCode": 0,
            "g_tk": 5381, "begin": begin, "num": PAGE_SIZE})
        url = ("https://c.y.qq.com/qzone/fcg-bin/"
               "fcg_ucc_getcdinfo_byids_cp.fcg?" + qs)
        d = qq_get_json(url, referer=f"https://y.qq.com/n/ryqq/playlist/{dissid}")
        cd = (d.get("cdlist") or [{}])[0]
        if not cd:
            sys.exit("接口未返回歌单内容（私密歌单或已下架？）")
        name = html.unescape(cd.get("dissname") or name).strip()
        total = cd.get("total_song_num")
        songlist = cd.get("songlist") or []
        got_new = 0
        for s in songlist:
            r = _song_row(s)
            if not r["歌曲名"]:
                continue
            mid = r["歌曲链接"].rsplit("/", 1)[-1]
            key = mid or f'{r["歌曲名"]}|{r["歌手"]}'
            if key in seen_mids or (not mid and key in seen_titles):
                continue
            seen_mids.add(key)
            if not mid:
                seen_titles.add(key)
            rows.append(r)
            got_new += 1
        print(f"  已获取 {len(rows)} 首（本页 {len(songlist)}，新增 {got_new}）"
              + (f" / 共 {total}" if total else ""), flush=True)
        # 接口一次回全量、或分页取完、或无新增（防死循环）、或已达 limit
        if total and len(rows) >= total:
            break
        if not songlist or got_new == 0:
            break
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            print(f"  --limit={limit}，截取前 {limit} 首（测试模式）")
            break
        begin += len(songlist)
        if total and begin >= total:
            break
        time.sleep(POLITE_GAP)

    if limit and len(rows) > limit:
        rows = rows[:limit]
        print(f"  --limit={limit}，截取前 {limit} 首（测试模式）")
    print(f"「{name}」共获取 {len(rows)} 首")
    return name, rows


def main():
    ap = argparse.ArgumentParser(description="QQ 音乐歌单 → 本地 m3u8 导出")
    ap.add_argument("playlist", nargs="?", help="歌单链接或纯数字 disstid（联网模式）")
    ap.add_argument("--csv", help="已有清单 CSV（离线模式，跳过抓取）")
    ap.add_argument("--name", help="歌单名（离线模式下用它命名输出）")
    ap.add_argument("--music-root", default="/Volumes/media-2/music",
                    help="本地音乐库根目录，默认 /Volumes/media-2/music")
    ap.add_argument("--outdir", default="./歌单导出", help="输出目录")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 首（测试用）")
    args = ap.parse_args()

    if not args.playlist and not args.csv:
        ap.error("需要提供歌单链接，或用 --csv 指定已有清单")
    if not os.path.isdir(args.music_root):
        sys.exit(f"本地音乐库目录不存在: {args.music_root}")
    os.makedirs(args.outdir, exist_ok=True)

    t0 = time.time()
    if args.csv:
        rows = load_csv(args.csv)
        print(f"离线模式：读取清单 {len(rows)} 行")
        if args.name:
            base = sanitize(args.name)
        else:
            stem = os.path.splitext(os.path.basename(args.csv))[0]
            base = sanitize(stem.replace("-歌单完整清单", "")
                            .replace("-完整清单", "").replace("-清单", ""))
    else:
        dissid = parse_playlist_id(args.playlist)
        base, rows = fetch_playlist(dissid, args.limit)
        base = sanitize(base)
    for r in rows:
        r.setdefault("本地文件", "")
        r["_root"] = args.music_root

    rows = match_all(rows, args.music_root)
    write_csvs(args.outdir, base, rows)
    write_m3u8(args.outdir, base, rows)
    print(f"完成，用时 {time.time() - t0:.0f}s，输出目录: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()
