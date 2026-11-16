#!/usr/bin/env python3
"""多音源兜底下载：酷狗 → 酷我 → 咪咕，严格版本校验，幂等可重跑。

适用场景：网易云 / QQ 音乐都无音源（多为 VIP、下架、独家），且 B 站也没合适上传时试这一档。
注意：这三家的匿名接口对近年新歌往往只给 11 秒试听片段（文件头仍合法），
      因此下载后必须调用本脚本内置的时长校验/外部复核，切勿只看文件大小。

用法：
  python3 alt_source_dl.py --artist 毛不易 --todo missing.json --out /music/毛不易
  python3 alt_source_dl.py --artist 毛不易 --todo missing.json --out /music/毛不易 --dry-run

todo 文件格式：
  [{"t": "歌名", "a": "歌手", "dur": 253}, ...]

输出（默认写到 --workdir）：
  alt_downloaded.jsonl   台账（幂等用）
"""
import argparse
import ast
import glob
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

BAD_WORDS = ["dj", "remix", "cover", "翻唱", "现场", "live", " youtube", "tiktok", "speed", "sped", "instrumental"]
DUR_TOL = 5          # 与平台时长允许偏差（秒）
MIN_BYTES = 500_000  # 小于约 500KB 视为试听片段，直接丢弃


def norm(s):
    s = unicodedata.normalize("NFC", str(s))
    s = s.replace("（", "(").replace("）", ")").replace("｜", "|")
    return re.sub(r"\s+", "", s).lower()


def http_get(url, hdr=None, timeout=15):
    h = {"User-Agent": UA}
    if hdr:
        h.update(hdr)
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)


def local_have(out_dir, also_scan):
    idx = {}
    for root in [out_dir] + also_scan:
        for pat in ("*.flac", "*.mp3", "*.m4a", "*.ogg"):
            for f in glob.glob(os.path.join(root, "**", pat), recursive=True):
                idx.setdefault(norm(os.path.splitext(os.path.basename(f))[0]), []).append(f)
    return idx


def fname_for(artist, title):
    base = f"{artist} - {title}"
    if len(base) > 180:
        base = f"{artist} - {title[:80]}"
    return re.sub(r'[\\/:*?"<>|]', "_", base)


def dur_ok(src, want):
    return want and abs(int(src or 0) - int(want)) <= DUR_TOL


def title_match(name, title):
    nn = norm(name)
    return nn == norm(title) or nn == norm(re.sub(r"\(.*\)$", "", title))


def bad_version(name):
    return any(w in (name or "").lower() for w in BAD_WORDS)


def kugou_find(artist, title, want_dur):
    kw = urllib.parse.quote(f"{artist} {title}")
    d = json.load(http_get(
        f"http://mobilecdn.kugou.com/api/v3/search/song?format=json&keyword={kw}&page=1&pagesize=30",
        {"Referer": "https://www.kugou.com/"}))
    for s in d.get("data", {}).get("info", []):
        nm, sg = s.get("songname", ""), s.get("singername", "")
        if artist not in sg:
            continue
        if not title_match(nm, title) or bad_version(nm):
            continue
        if not dur_ok(s.get("duration"), want_dur):
            continue
        return s
    return None


def kugou_url(item, mid):
    u = (f"https://wwwapi.kugou.com/yy/index.php?r=play/getdata&hash={item['hash']}"
         f"&dfid={mid}&appid=1014&mid={mid}&platid=4&album_id={item.get('album_id', '')}"
         f"&album_audio_id={item.get('album_audio_id', '')}")
    try:
        d = json.load(http_get(u, {"Referer": "https://www.kugou.com/", "Cookie": f"kg_mid={mid}"}))
        data = d.get("data") or {}
        pu = data.get("play_url") or ""
        # privilege >= 8 才有完整播放权限，<8 多为试听
        if pu and int(data.get("privilege") or 0) >= 8:
            return pu, (data.get("ext_name") or "mp3").lower(), int(data.get("timelength") or 0) // 1000
    except Exception:
        pass
    return None, None, None


def kuwo_find(artist, title, want_dur):
    kw = urllib.parse.quote(f"{artist} {title}")
    raw = http_get(
        f"http://search.kuwo.cn/r.s?all={kw}&ft=music&itemset=web_2019&client=kt&pn=0&rn=30&rformat=json&encoding=utf8",
        {"Referer": "http://www.kuwo.cn/"}).read().decode("utf-8", "ignore")
    try:
        lst = ast.literal_eval(html.unescape(raw)).get("abslist", [])
    except Exception:
        return None
    for s in lst:
        nm = s.get("SONGNAME", "").replace("\\u0026", "、")
        sg = s.get("ARTIST", "").replace("\\u0026", "、")
        if artist not in sg:
            continue
        if not title_match(nm, title) or bad_version(nm):
            continue
        if not dur_ok(s.get("DURATION"), want_dur):
            continue
        return s
    return None


def kuwo_url(rid, fmt, br):
    u = f"http://antiserver.kuwo.cn/anti.s?type=convert_url&rid={rid}&format={fmt}&br={br}&response=url"
    try:
        out = http_get(u).read().decode("utf-8", "ignore").strip()
        return out if out.startswith("http") else None
    except Exception:
        return None


def migu_find(artist, title, want_dur):
    kw = urllib.parse.quote(f"{title} {artist}")
    u = f"https://m.music.migu.cn/migu/remoting/scr_search_tag?keyword={kw}&type=2&rows=20&pgc=1"
    try:
        d = json.load(http_get(u, {"Referer": "https://m.music.migu.cn/migu/music/search"}))
    except Exception:
        return None
    for s in d.get("musics", []):
        nm, sg = s.get("songName", ""), s.get("singerName", "")
        if artist not in sg:
            continue
        if not title_match(nm, title) or bad_version(nm):
            continue
        dur = s.get("duration") or s.get("length")
        if dur and int(dur) > 10000:
            dur = int(dur) // 1000  # 毫秒转秒
        if not dur_ok(dur, want_dur):
            continue
        return s
    return None


def migu_url(item):
    for k in ("fullcopyContentUrl", "copyContentUrl", "contentUrl"):
        if item.get(k):
            return item[k], "mp3"
    return None, None


def download(url, path):
    r = http_get(url, timeout=120)
    body = r.read()
    head = body[:4]
    ok = (head == b"fLaC" or head[:3] == b"ID3"
          or (len(body) > 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
          or head == b"ftyp" or b"ftyp" in body[:16])
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "text/html" in ctype or not ok:
        return False, len(body)
    if len(body) < MIN_BYTES:
        return False, len(body)  # 试听片段
    open(path, "wb").write(body)
    return True, len(body)


def main():
    ap = argparse.ArgumentParser(description="酷狗/酷我/咪咕 多音源兜底下载")
    ap.add_argument("--artist", required=True, help="歌手名（用于搜索与歌手一致性校验）")
    ap.add_argument("--todo", required=True, help="缺失清单 JSON：[{t,a,dur}]")
    ap.add_argument("--out", required=True, help="音频落盘目录")
    ap.add_argument("--workdir", default=".", help="台账目录，默认当前目录")
    ap.add_argument("--also-scan", action="append", default=[], help="额外扫描的已有音乐库根目录（可多次）")
    ap.add_argument("--sleep", type=float, default=1.2, help="每首间隔秒数，默认 1.2")
    ap.add_argument("--dry-run", action="store_true", help="只探查音源，不下载")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    log = os.path.join(a.workdir, "alt_downloaded.jsonl")
    todo = json.load(open(a.todo, encoding="utf-8"))
    idx = local_have(a.out, a.also_scan)
    done_keys = set()
    if os.path.exists(log):
        for line in open(log, encoding="utf-8"):
            try:
                done_keys.add(json.loads(line)["key"])
            except Exception:
                pass

    n_ok = 0
    for i, it in enumerate(todo, 1):
        title = it.get("t") or it.get("title")
        artist = it.get("a") or it.get("artist") or a.artist
        dur = it.get("dur") or it.get("duration")
        key = norm(f"{artist} - {title}")
        if key in done_keys or idx.get(key):
            print(f"[{i}/{len(todo)}] 已有，跳过 {title}")
            continue

        got = False
        for src, finder, url_getter in (
            ("酷狗", kugou_find, None),
            ("酷我", kuwo_find, None),
            ("咪咕", migu_find, None),
        ):
            try:
                item = finder(a.artist, title, dur)
                if not item:
                    continue
                if src == "酷狗":
                    pu, ext, _ = kugou_url(item, uuid.uuid4().hex)
                elif src == "酷我":
                    rid = item["MUSICRID"]
                    pu = kuwo_url(rid, "flac", "2000k")
                    ext = "flac"
                    if not pu:
                        pu = kuwo_url(rid, "mp3", "320k")
                        ext = "mp3"
                else:
                    pu, ext = migu_url(item)
                if not pu:
                    continue
                if a.dry_run:
                    print(f"[{i}/{len(todo)}] 可下 {src} {title} ({ext})")
                    got = True
                    break
                fn = os.path.join(a.out, f"{fname_for(artist, title)}.{ext or 'mp3'}")
                ok, size = download(pu, fn)
                if ok:
                    print(f"[{i}/{len(todo)}] 完成 {src} {title} {ext} ({size // 1024 // 1024}MB)")
                    open(log, "a", encoding="utf-8").write(
                        json.dumps({"key": key, "src": src, "t": title, "file": fn}, ensure_ascii=False) + "\n")
                    n_ok += 1
                    got = True
                    break
                else:
                    print(f"  {src} 疑似试听/无效（{size // 1024}KB），换下一个源")
                    if os.path.exists(fn):
                        os.remove(fn)
            except Exception as e:
                print(f"  {src} err {title}: {str(e)[:60]}")
        if not got:
            print(f"[{i}/{len(todo)}] 无音源 {title}")
        time.sleep(a.sleep)
    print(f"\n本轮新增: {n_ok}")


if __name__ == "__main__":
    main()
