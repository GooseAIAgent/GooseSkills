#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音乐平台查询库：QQ音乐 + 网易云音乐。

从命令行直接调用可快速查询单首歌曲；也可 import 后在脚本中使用。

用法示例:
    python3 music_api.py search "薛之谦 演员"          # QQ音乐搜索
    python3 music_api.py album 003j9ta912S5XX          # QQ音乐专辑详情(albummid)
    python3 music_api.py netease-artist 毛不易          # 网易云歌手ID
    python3 music_api.py netease-albums 13159024        # 网易云歌手全部专辑
"""
import json
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _get(url, referer="https://music.163.com/", timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    return urllib.request.urlopen(req, timeout=timeout)


# ---------------------------------------------------------------- QQ 音乐

def qq_search(query, n=10, retries=3):
    """QQ音乐搜索。返回歌曲列表，每项含 name/artists/album/albumMid/timePublic。"""
    body = {"req_1": {"method": "DoSearchForQQMusicDesktop",
                      "module": "music.search.SearchCgiService",
                      "param": {"search_type": 0, "query": query,
                                "page_num": 1, "num_per_page": n}}}
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                "https://u.y.qq.com/cgi-bin/musicu.fcg",
                data=json.dumps(body).encode(),
                headers={"User-Agent": UA, "Content-Type": "application/json",
                         "Referer": "https://y.qq.com/"})
            d = json.load(urllib.request.urlopen(req, timeout=15))
            songs = (d.get("req_1", {}).get("data", {}).get("body", {})
                       .get("song", {}).get("list", []))
            return [{"song": s.get("name"),
                     "artists": "/".join(x["name"] for x in s.get("singer", [])),
                     "album": (s.get("album") or {}).get("name") or "",
                     "albumMid": (s.get("album") or {}).get("mid"),
                     "date": s.get("timePublic") or s.get("time_public") or ""}
                    for s in songs]
        except Exception as e:  # 限流/网络抖动时退避重试
            last = e
            time.sleep(2 * (i + 1))
    raise last


def qq_album(album_mid):
    """QQ音乐专辑详情。返回 {date, n, tracks:[歌名]}。"""
    url = ("https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg"
           f"?albummid={album_mid}&format=json")
    d = json.load(_get(url, referer="https://y.qq.com/")).get("data", {})
    tracks = [t.get("songname") for t in d.get("list", [])]
    return {"date": d.get("aDate") or d.get("timePublic"),
            "n": len(tracks), "tracks": tracks}


# ---------------------------------------------------------------- 网易云音乐

def _netease_get_json(url, tries=4):
    """网易云 GET，自动处理 -462 验证码限流（退避重试）。"""
    for i in range(tries):
        try:
            d = json.load(_get(url))
            if d.get("code") in (-462, 431):
                time.sleep(8 * (i + 1))
                continue
            return d
        except Exception:
            time.sleep(5 * (i + 1))
    return None


def netease_artist_id(name):
    """按名字搜索歌手，返回 [(id, name, 专辑数, 歌曲数)]。"""
    q = urllib.parse.quote(name)
    d = _netease_get_json(
        f"https://music.163.com/api/search/get/web?s={q}&type=100&limit=5")
    return [(a["id"], a["name"], a.get("albumSize"), a.get("musicSize"))
            for a in d.get("result", {}).get("artists", [])] if d else []


def netease_artist_albums(artist_id):
    """歌手全部专辑（列表接口自带 publishTime，不易限流）。
    返回 [{name, id, date(YYYY-MM-DD UTC+8)}]。"""
    import datetime
    albums, offset = [], 0
    while True:
        d = _netease_get_json(
            f"https://music.163.com/api/artist/albums/{artist_id}"
            f"?limit=50&offset={offset}")
        if not d:
            break
        hot = d.get("hotAlbums", [])
        albums += hot
        if len(hot) < 50:
            break
        offset += 50
        time.sleep(0.5)
    tz = datetime.timezone(datetime.timedelta(hours=8))
    out = []
    for al in albums:
        pt = al.get("publishTime")
        date = (datetime.datetime.fromtimestamp(pt / 1000, tz).strftime("%Y-%m-%d")
                if pt else "?")
        out.append({"name": al["name"], "id": al["id"], "date": date})
    return out


def netease_album(album_id):
    """网易云专辑详情（曲目+日期）。易限流，失败返回 None，请放慢调用节奏(>=1.5s)。"""
    import datetime
    for url in (f"https://music.163.com/api/v1/album/{album_id}",
                f"https://music.163.com/api/album/{album_id}"):
        d = _netease_get_json(url)
        if d and (d.get("album") or d.get("songs")):
            alb = d.get("album") or d
            pt = alb.get("publishTime")
            tz = datetime.timezone(datetime.timedelta(hours=8))
            date = (datetime.datetime.fromtimestamp(pt / 1000, tz).strftime("%Y-%m-%d")
                    if pt else "?")
            songs = alb.get("songs") or d.get("songs") or []
            return {"name": alb.get("name"), "date": date,
                    "tracks": [{"name": s["name"],
                                "artists": "/".join(a["name"] for a in s.get("artists", []))}
                               for s in songs]}
        time.sleep(1.5)
    return None


# ---------------------------------------------------------------- CLI

def main():
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "search":
        for s in qq_search(arg):
            print(s["date"], "|", s["album"], "|", s["song"], "|", s["artists"])
    elif cmd == "album":
        info = qq_album(arg)
        print("发行:", info["date"], "|", info["n"], "首")
        print(info["tracks"])
    elif cmd == "netease-artist":
        for a in netease_artist_id(arg):
            print(a)
    elif cmd == "netease-albums":
        for a in netease_artist_albums(int(arg)):
            print(a["date"], "|", a["name"], "| id=", a["id"])
    elif cmd == "netease-album":
        info = netease_album(int(arg))
        print(json.dumps(info, ensure_ascii=False, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
