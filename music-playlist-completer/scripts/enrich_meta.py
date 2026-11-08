#!/usr/bin/env python3
"""enrich_meta.py — 为已下载歌曲补全歌词(.lrc 侧车 + 写入标签)、封面(写入标签 + 存 folder)。

用法:
  python3 enrich_meta.py --dir <音乐目录> --csv <全量清单.csv> [--newermt "2026-11-08"] [--force]

CSV 需含列: 歌名 / 歌手 / 专辑 / 网易云链接(含 song?id=)。按规范化文件名匹配 CSV 行获取网易云歌曲 ID,
再取歌词 (api/song/lyric, 无则 QQ 兜底) 与专辑封面 (api/v3/song/detail -> al.picUrl), 用 mutagen 写入:
  FLAC: tags["lyrics"] + Picture
  MP3:  USLT + APIC
  M4A:  ©lyr + covr
同时写 <同名>.lrc 侧车文件; 专辑目录无封面图时另存 cover.jpg。

坑（勿回退）:
- 封面必须用 api/v3/song/detail（旧 api/song/detail 返回 album 字段且常缺 picUrl）。
- QQ 搜索接口勿加 new_json=1（会返回空列表）；lyric 接口返回 base64。
- 网易云对纯音乐返回「纯音乐，请欣赏」式元信息歌词，属正常。
- 与 music-library-organizer 技能如有同名脚本，保持同步。
"""
import argparse, base64, csv, glob, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
CFG = os.path.expanduser("~/.config/music-downloader/config.json")

def norm(s):
    s = unicodedata.normalize("NFC", s)
    s = s.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", s).lower()

def cookie():
    try:
        return json.load(open(CFG)).get("netease_cookie", "")
    except Exception:
        return ""

def http_json(url, data=None):
    hdr = {"User-Agent": UA, "Referer": "https://music.163.com/", "Cookie": cookie(),
           "Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(url, data=(urllib.parse.urlencode(data).encode() if data else None), headers=hdr)
    for i in range(4):
        try:
            return json.load(urllib.request.urlopen(req, timeout=20))
        except Exception as e:
            print(f"  retry {i+1}: {e}", file=sys.stderr); time.sleep(2 ** i)
    return None

def http_bytes(url, referer="https://music.163.com/"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    return urllib.request.urlopen(req, timeout=60).read()

def load_index(csv_path):
    """返回 {norm("歌手 - 歌名"): (sid, album, 歌名, 歌手)} 与 {norm(歌名): ...}"""
    k1, k2 = {}, {}
    for r in csv.DictReader(open(csv_path, encoding="utf-8-sig")):
        link = r.get("网易云链接", "")
        m = re.search(r"id=(\d+)", link)
        if not m:
            continue
        sid = m.group(1)
        v = (sid, r.get("专辑", ""), r.get("歌名", ""), r.get("歌手", ""))
        k1.setdefault(norm(f"{r['歌手']} - {r['歌名']}"), v)
        k2.setdefault(norm(r["歌名"]), v)
    return k1, k2

_cover_cache = {}   # album key -> (bytes, mime)
_lyric_fail = set()

def fetch_lyric(sid):
    d = http_json(f"https://music.163.com/api/song/lyric?id={sid}&lv=1&kv=1&tv=-1")
    lrc = ((d or {}).get("lrc") or {}).get("lyric", "").strip()
    if lrc and not lrc.startswith("[00:00.000]纯音乐"):
        return lrc
    return None

_QQ_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

def fetch_lyric_qq(title, artist="毛不易"):
    """QQ 歌词兜底: 旧参数搜索(勿加 new_json=1, 会返回空) + lyric 接口(base64)"""
    def qget(url, referer="https://c.y.qq.com/"):
        req = urllib.request.Request(url, headers={"User-Agent": _QQ_UA, "Referer": referer})
        return json.load(urllib.request.urlopen(req, timeout=15))
    try:
        u = ("https://c.y.qq.com/soso/fcgi-bin/client_search_cp?ct=24&qqmusic_ver=5&remoteplace=txt.yqq.top"
             f"&t=0&agg=0&cr=1&p=1&n=8&w={urllib.parse.quote(artist + ' ' + title)}&format=json")
        hit = None
        for it in qget(u).get("data", {}).get("song", {}).get("list") or []:
            name = it.get("songname") or it.get("name") or ""
            singers = "、".join(x.get("name", "") for x in it.get("singer") or [])
            if norm(title) == norm(name) and artist.split("、")[0] in singers and "DJ" not in name:
                hit = it; break
        if not hit:
            return None
        mid = hit.get("songmid") or hit.get("mid")
        u2 = (f"https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?songmid={mid}&g_tk=5381"
              "&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8&notice=0&platform=yqq.json&needNewCode=0")
        raw = qget(u2).get("lyric")
        lrc = base64.b64decode(raw).decode("utf-8", "ignore") if raw else None
        return lrc if lrc and len(lrc) > 30 else None
    except Exception as e:
        print(f"  qq lyric fail: {e}", file=sys.stderr)
        return None

def fetch_cover(sid):
    d = http_json(f"https://music.163.com/api/v3/song/detail?id={sid}&c=%5B%7B%22id%22%3A%20{sid}%7D%5D")
    songs = (d or {}).get("songs") or []
    if not songs:
        return None
    al = songs[0].get("al") or songs[0].get("album") or {}
    pic = al.get("picUrl")
    if not pic:
        return None
    key = al.get("pic") or al.get("pic_str") or pic
    if key in _cover_cache:
        return _cover_cache[key]
    try:
        body = http_bytes(pic + "?param=1500y1500")
        mime = "image/png" if body[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        _cover_cache[key] = (body, mime)
        return _cover_cache[key]
    except Exception as e:
        print(f"  cover fail: {e}", file=sys.stderr)
        return None

def write_tags(path, lrc, cover, meta=None):
    """cover=(bytes,mime) or None; meta=(title,artist,album) 可选补全"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".flac":
        from mutagen.flac import FLAC, Picture
        f = FLAC(path)
        if meta:
            t, ar, al = meta
            f["title"], f["artist"], f["album"] = t, ar, al
        if lrc:
            f["lyrics"] = lrc
        if cover:
            pic = Picture()
            pic.type = 3; pic.mime = cover[1]; pic.desc = "Cover"
            pic.data = cover[0]; pic.width = pic.height = 1500
            f.clear_pictures(); f.add_picture(pic)
        f.save()
    elif ext == ".mp3":
        from mutagen.id3 import ID3, USLT, APIC, TIT2, TPE1, TALB
        try:
            f = ID3(path)
        except Exception:
            f = ID3()
        if meta:
            t, ar, al = meta
            f.setall("TIT2", [TIT2(encoding=3, text=t)])
            f.setall("TPE1", [TPE1(encoding=3, text=ar)])
            f.setall("TALB", [TALB(encoding=3, text=al)])
        if lrc:
            f.setall("USLT", [USLT(encoding=3, lang="chi", desc="", text=lrc)])
        if cover:
            f.setall("APIC", [APIC(encoding=3, mime=cover[1], type=3, desc="Cover", data=cover[0])])
        f.save(path)
    elif ext == ".m4a":
        from mutagen.mp4 import MP4, MP4Cover
        f = MP4(path)
        if meta:
            t, ar, al = meta
            f["\xa9nam"], f["\xa9ART"], f["\xa9alb"] = [t], [ar], [al]
        if lrc:
            f["\xa9lyr"] = [lrc]
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
            f["covr"] = [MP4Cover(cover[0], imageformat=fmt)]
        f.save()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--newermt", default=None, help="仅处理此时间之后修改的文件, 如 2026-11-08")
    ap.add_argument("--force", action="store_true", help="已有歌词/封面也重写")
    a = ap.parse_args()

    k1, k2 = load_index(a.csv)
    # 后缀兜底索引: 处理「歌手-歌名」无空格等不规整文件名
    k2_by_suffix = sorted(k2.items(), key=lambda x: -len(x[0]))
    pats = ("*.flac", "*.mp3", "*.m4a")
    files = []
    for p in pats:
        files += glob.glob(os.path.join(a.dir, "**", p), recursive=True)
    if a.newermt:
        files = [f for f in files if os.path.getmtime(f) >= time.mktime(time.strptime(a.newermt, "%Y-%m-%d"))]
    print(f"待处理: {len(files)} 个文件")

    ok_lrc = ok_cover = skip = fail = 0
    for n, path in enumerate(sorted(files), 1):
        stem = os.path.splitext(os.path.basename(path))[0]
        base = norm(stem)
        hit = k1.get(base) or k2.get(base) or k1.get(norm(re.sub(r"^[^-]+-", "", base)))
        if not hit:
            for key, v in k2_by_suffix:  # 文件名以歌名结尾的兜底
                if base.endswith(key):
                    hit = v; break
        if not hit:
            print(f"[{n}] ✗ 无法匹配 CSV: {stem}"); fail += 1; continue
        sid, alb, hit_title, hit_artist = hit
        lrc = cover = None
        need_lrc = need_cover = True
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".flac":
                from mutagen.flac import FLAC
                cur = FLAC(path)
                need_lrc = a.force or not cur.get("lyrics")
                need_cover = a.force or not cur.pictures
            elif ext == ".mp3":
                from mutagen.id3 import ID3
                cur = ID3(path)
                need_lrc = a.force or not cur.getall("USLT")
                need_cover = a.force or not cur.getall("APIC")
            elif ext == ".m4a":
                from mutagen.mp4 import MP4
                cur = MP4(path)
                need_lrc = a.force or not cur.get("\xa9lyr")
                need_cover = a.force or not cur.get("covr")
        except Exception as e:
            print(f"[{n}] ✗ 读取失败 {stem}: {e}"); fail += 1; continue

        if need_lrc and sid not in _lyric_fail:
            lrc = fetch_lyric(sid)
            if not lrc:  # 网易云无歌词时 QQ 兜底
                lrc = fetch_lyric_qq(hit_title, hit_artist)
            if not lrc:
                _lyric_fail.add(sid)
        if need_cover:
            cover = fetch_cover(sid)
        if not (lrc or cover):
            if not need_lrc and not need_cover:
                skip += 1
                continue
            print(f"[{n}] ▲ 无资源: {stem} (lrc={bool(lrc)}, cover={bool(cover)})")

        try:
            write_tags(path, lrc, cover)
        except Exception as e:
            print(f"[{n}] ✗ 写标签失败 {stem}: {e}"); fail += 1; continue
        ok_lrc += bool(lrc); ok_cover += bool(cover)

        # .lrc 侧车
        if lrc:
            lrc_path = os.path.splitext(path)[0] + ".lrc"
            if a.force or not os.path.exists(lrc_path):
                open(lrc_path, "w", encoding="utf-8").write(lrc)

        # 专辑目录封面图
        folder = os.path.dirname(path)
        cover_path = os.path.join(folder, "cover.jpg")
        if cover and cover[1] == "image/jpeg" and not os.path.exists(cover_path):
            open(cover_path, "wb").write(cover[0])

        time.sleep(0.6)
        print(f"[{n}] ✓ {stem} (lrc={bool(lrc)}, cover={bool(cover)})")

    print(f"\n完成: 歌词 {ok_lrc}, 封面 {ok_cover}, 已有跳过 {skip}, 失败 {fail}")

if __name__ == "__main__":
    main()
