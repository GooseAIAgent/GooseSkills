#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""artist_songs.py — 抓取某歌手在网易云的全部歌曲，产出 build_missing_list 可直接消费的 CSV。

流程: 搜索歌手 ID → 专辑列表(/api/artist/albums, 分页) → 逐张专辑详情(/api/album/<id>)
     → 按歌手过滤 → 应用长期过滤规则 → 同录音去重 → 导出 CSV。

长期过滤规则（用户明确要求，勿删）:
  1. 不下载「(伴奏)」「(和声伴奏)」「(纯音乐)」「(Instrumental)」等伴奏类版本——抓清单阶段即过滤。
  2. 同一录音既有录音室版又有「(Live)」「(现场)」版 → 只保留录音室版；仅 Live 独有(无录音室发行)的可保留。

用法:
  python3 artist_songs.py 毛不易 --out ./毛不易下载
  python3 artist_songs.py 12138269 --out ./out --csv 我的清单.csv
"""
import argparse, csv, datetime, json, os, re, sys, time, unicodedata, urllib.request, urllib.parse

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
CFG = os.path.expanduser("~/.config/music-downloader/config.json")

ACCENT_RE = re.compile(r"[（(](伴奏|和声伴奏|纯音乐|Instrumental|Inst\.?|纯音乐版)[）)]", re.I)
LIVE_RE = re.compile(r"[（(](live|现场|演唱会版|现场版)[）)]", re.I)


def norm(s):
    s = unicodedata.normalize("NFC", s)
    s = s.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", s).lower()


def cookie():
    try:
        return json.load(open(CFG)).get("netease_cookie", "")
    except Exception:
        return ""


def get(url, tries=6):
    """带 -462 限流退避的 GET。退避 10s 起步上限 60s，cookie 必带。"""
    hdr = {"Referer": "https://music.163.com/", "User-Agent": UA, "Cookie": cookie()}
    for i in range(tries):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30))
            if d.get("code") == 200:
                return d
            if d.get("code") in (-462, -431):
                w = min(10 * (i + 1), 60)
                print(f"  限流 {d.get('code')}，等 {w}s 重试 {i+1}/{tries}", file=sys.stderr)
                time.sleep(w)
                continue
            print(f"  code {d.get('code')}，重试", file=sys.stderr)
            time.sleep(3)
        except Exception as e:
            print(f"  err {e}，重试 {i+1}/{tries}", file=sys.stderr)
            time.sleep(2 ** i)
    raise RuntimeError("failed: " + url)


def resolve_artist_id(keyword):
    d = get("https://music.163.com/api/search/get/web?s=" + urllib.parse.quote(keyword) + "&type=100&limit=5")
    arts = (d.get("result") or {}).get("artists") or []
    if not arts:
        raise SystemExit(f"搜不到歌手: {keyword}")
    a = arts[0]
    print(f"歌手: {a['name']} (id={a['id']}, 专辑 {a.get('albumSize')}, 歌曲 {a.get('musicSize')})")
    return a["id"], a["name"]


def fetch_albums(artist_id):
    albums, offset = [], 0
    while True:
        d = get(f"https://music.163.com/api/artist/albums/{artist_id}?limit=100&offset={offset}")
        batch = d.get("hotAlbums", [])
        albums += batch
        if not batch or len(batch) < 100:
            break
        offset += len(batch)
        time.sleep(2)
    print(f"专辑: {len(albums)} 张", file=sys.stderr)
    return albums


def collect_songs(artist_id, artist_name, albums):
    songs, failed = {}, []
    for n, al in enumerate(albums, 1):
        try:
            ad = get(f"https://music.163.com/api/album/{al['id']}")
        except RuntimeError:
            print(f"[{n}/{len(albums)}] FAIL {al['name']}", file=sys.stderr)
            failed.append((al["id"], al["name"]))
            continue
        ab = ad.get("album", {})
        ts = ab.get("songs", []) or ad.get("songs", [])
        kept = 0
        for s in ts:
            artists = s.get("artists", [])
            if any(a.get("id") == artist_id for a in artists) or any(a.get("name") == artist_name for a in artists):
                s = dict(s)
                s["_album"] = ab.get("name") or al["name"]
                songs[s["id"]] = s
                kept += 1
        print(f"[{n}/{len(albums)}] {ab.get('name') or al['name']}: {len(ts)} 首, 本歌手 {kept} 首", file=sys.stderr)
        time.sleep(2)
    return songs, failed


def apply_rules(songs, filter_live=True):
    """长期过滤规则 + 同录音去重。返回 (rows, dropped_live, dropped_dupe)"""
    # 按剥离 Live 后缀后的规范名分组，判断该录音是否同时存在录音室版
    groups = {}
    for sid, s in songs.items():
        key = LIVE_RE.sub("", norm(s["name"]))
        groups.setdefault(key, []).append((sid, s))

    dropped_live = []
    keep_ids = set()
    for key, items in groups.items():
        has_studio = any(not LIVE_RE.search(s["name"]) for _, s in items)
        for sid, s in items:
            if filter_live and has_studio and LIVE_RE.search(s["name"]):
                dropped_live.append(s["name"])
                continue
            if ACCENT_RE.search(s["name"]):
                continue
            keep_ids.add(sid)

    rows, seen = [], set()
    for sid in sorted(keep_ids):
        s = songs[sid]
        name = s["name"]
        ar = "、".join(a["name"] for a in s.get("artists", []))
        dur = round((s.get("duration") or 0) / 1000)
        key = (norm(name), norm(ar), dur)  # 同录音多专辑收录去重
        if key in seen:
            continue
        seen.add(key)
        rows.append({"序号": len(rows) + 1, "歌名": name, "歌手": ar,
                     "专辑": s.get("_album", ""), "时长": dur, "fee": s.get("fee"),
                     "网易云链接": f"https://music.163.com/song?id={sid}"})
    return rows, dropped_live, len(songs) - len(rows) - len(dropped_live)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artist", help="歌手名或网易云歌手 ID")
    ap.add_argument("--out", default=".", help="CSV 输出目录")
    ap.add_argument("--csv", default=None, help="CSV 文件名（默认 <歌手>-全部歌曲.csv）")
    ap.add_argument("--keep-live", action="store_true", help="不过滤有录音室对应的 Live 版")
    a = ap.parse_args()

    if a.artist.isdigit():
        artist_id, artist_name = a.artist, a.artist
        d = get(f"https://music.163.com/api/artist/{artist_id}")
        artist_name = ((d.get("artist") or {}).get("name")) or artist_name
    else:
        artist_id, artist_name = resolve_artist_id(a.artist)

    albums = fetch_albums(artist_id)
    songs, failed = collect_songs(artist_id, artist_name, albums)
    if failed:
        print(f"⚠ {len(failed)} 张专辑抓取失败: {[x[1] for x in failed]}", file=sys.stderr)

    rows, dropped_live, dropped_dupe = apply_rules(songs, filter_live=not a.keep_live)

    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, a.csv or f"{artist_name}-全部歌曲.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["序号", "歌名", "歌手", "专辑", "时长", "fee", "网易云链接"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nAPI 唯一 ID: {len(songs)} | 过滤 Live(有录音室对应): {len(dropped_live)} | 同录音去重等: {dropped_dupe}")
    print(f"导出 {len(rows)} 首 -> {out}")


if __name__ == "__main__":
    main()
