#!/usr/bin/env python3
"""B 站音源兜底下载：搜索上传 → 版本过滤 → 时长校验 → DASH 音频 → ffmpeg 重封装 → mutagen 复核。

适用场景：网易云 / QQ / 酷狗 / 酷我 / 咪咕 都拿不到音源的曲目（多为 VIP、下架、数字专辑）。
幂等可重跑：已成功的记入台账 jsonl，已存在于音乐库的自动跳过。

用法：
  python3 bilibili_dl.py --artist 毛不易 --todo missing.json --out /music/毛不易
  python3 bilibili_dl.py --artist 毛不易 --todo missing.json --out /music/毛不易 --dry-run

todo 文件格式（build_missing_list.py 输出即兼容）：
  [{"t": "歌名", "a": "歌手", "dur": 253}, ...]      dur 为平台时长（秒），用于校验

输出（默认写到 --workdir）：
  alt_downloaded.jsonl   台账（幂等用）
  B站需复核.jsonl        时长偏差 > 3s 的条目，需人工试听
"""
import argparse
import glob
import json
import os
import re
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# 标题里出现即视为错误版本（翻唱/现场/改编等）
BAD_WORDS = ["翻唱", "cover", "live", "现场", "dj", "reaction", "吉他", "钢琴版", "翻弹", "合唱团", "教学"]
# 标题里出现视为正向信号
GOOD_WORDS = ["无损", "官方", "完整版", "mv"]
DUR_TOL = 8  # 与平台时长允许偏差（秒）


def norm(s):
    s = unicodedata.normalize("NFC", str(s))
    s = s.replace("（", "(").replace("）", ")").replace("｜", "|")
    return re.sub(r"\s+", "", s).lower()


def http_json(url, hdr=None, timeout=15):
    h = {"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"}
    if hdr:
        h.update(hdr)
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout))


def http_raw(url, hdr=None, timeout=120):
    h = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if hdr:
        h.update(hdr)
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)


def get_session():
    """B 站搜索接口 -412 时需要 buvid3/buvid4。"""
    d = http_json("https://api.bilibili.com/x/frontend/finger/spi")
    return {"Cookie": f"buvid3={d['data']['b_3']}; buvid4={d['data']['b_4']}"}


def parse_dur(s):
    s = str(s)
    if ":" in s:
        p = [int(x) for x in s.split(":")]
        return p[0] * 60 + p[1] if len(p) == 2 else p[0] * 3600 + p[1] * 60 + p[2]
    return int(s or 0)


def find_video(artist, title, want_dur, sess):
    """返回 (bvid, uploader, dur, title) 或 None。标题须含完整歌名，取时长最接近者。"""
    kw = urllib.parse.quote(f"{artist} {title}")
    d = http_json(
        f"https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={kw}&page=1", sess)
    cands = []
    for v in (d.get("data", {}).get("result") or []):
        t = re.sub(r"<[^>]+>", "", v.get("title", ""))
        low = t.lower()
        if norm(title) not in norm(t.replace(" ", "")):
            continue
        if any(w in low for w in BAD_WORDS):
            continue
        vd = parse_dur(v.get("duration", 0))
        if want_dur and abs(vd - want_dur) > DUR_TOL:
            continue
        score = abs(vd - want_dur) if want_dur else 0
        bonus = 0
        if any(k in low for k in GOOD_WORDS):
            bonus = 5
        if artist and artist in (v.get("author") or ""):
            bonus = 10  # 官方/本人上传优先
        cands.append((score - bonus, v.get("bvid"), v.get("author"), vd, t))
    if not cands:
        return None
    cands.sort()
    _, bvid, up, vd, t = cands[0]
    return bvid, up, vd, t


def get_audio(bvid, sess):
    d = http_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", sess)
    cid = d["data"]["cid"]
    p = http_json(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16&fnver=0&platform=pc", sess)
    dash = (p.get("data") or {}).get("dash") or {}
    aud = None
    if (dash.get("flac") or {}).get("audio"):
        aud = dash["flac"]["audio"]  # Hi-Res
    elif dash.get("audio"):
        aud = max(dash["audio"], key=lambda a: a.get("id", 0))
    if not aud:
        return None, None
    return aud["base_url"], aud.get("codecs", "")


def fname_for(artist, title):
    base = f"{artist} - {title}"
    if len(base) > 180:
        base = f"{artist} - {title[:80]}"
    return re.sub(r'[\\/:*?"<>|]', "_", base)


def verify_dur(path):
    try:
        from mutagen import File as MFile
        m = MFile(path)
        if m and m.info:
            return round(m.info.length)
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser(description="B 站音源兜底下载")
    ap.add_argument("--artist", required=True, help="歌手名，同时用于搜索关键词与上传者加分")
    ap.add_argument("--todo", required=True, help="缺失清单 JSON：[{t,a,dur}]")
    ap.add_argument("--out", required=True, help="音频落盘目录（该歌手的音乐库目录）")
    ap.add_argument("--workdir", default=".", help="台账/复核文件目录，默认当前目录")
    ap.add_argument("--also-scan", action="append", default=[], help="额外扫描的已有音乐库根目录（可多次）")
    ap.add_argument("--sleep", type=float, default=2.0, help="每首间隔秒数，默认 2")
    ap.add_argument("--dry-run", action="store_true", help="只搜索匹配，不下载不写文件")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    log = os.path.join(a.workdir, "alt_downloaded.jsonl")
    review_path = os.path.join(a.workdir, "B站需复核.jsonl")

    todo = json.load(open(a.todo, encoding="utf-8"))
    done = set()
    if os.path.exists(log):
        for line in open(log, encoding="utf-8"):
            try:
                done.add(json.loads(line)["key"])
            except Exception:
                pass

    idx = {}
    roots = [a.out] + a.also_scan
    for root in roots:
        for pat in ("*.flac", "*.mp3", "*.m4a", "*.ogg"):
            for f in glob.glob(os.path.join(root, "**", pat), recursive=True):
                idx.setdefault(norm(os.path.splitext(os.path.basename(f))[0]), []).append(f)

    sess = get_session()
    n_ok = 0
    for i, it in enumerate(todo, 1):
        title = it.get("t") or it.get("title")
        artist = it.get("a") or it.get("artist") or a.artist
        dur = it.get("dur") or it.get("duration") or 0
        key = norm(f"{artist} - {title}")
        if key in done or idx.get(key):
            print(f"[{i}] 已有，跳过  {title}")
            continue
        try:
            hit = find_video(a.artist, title, dur, sess)
        except Exception as e:
            print(f"[{i}] 搜索失败 {title}: {str(e)[:70]}")
            continue
        if not hit:
            print(f"[{i}] B站无匹配  {title}")
            continue
        bvid, up, vd, t = hit
        if a.dry_run:
            print(f"[{i}] 可下  {title} → {bvid} | up:{up} | {vd}s vs 平台{dur}s")
            continue
        try:
            url, codec = get_audio(bvid, sess)
            if not url:
                print(f"[{i}] 无音频流  {title} ({bvid})")
                continue
            tmp = os.path.join(a.out, f".bilibili_tmp_{int(time.time())}")
            body = http_raw(url).read()
            open(tmp, "wb").write(body)
            is_flac = body[:4] == b"fLaC"
            ext = "flac" if is_flac else "m4a"
            fn = os.path.join(a.out, f"{fname_for(artist, title)}.{ext}")
            if not is_flac:
                rc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-c", "copy", fn],
                                    capture_output=True)
                if rc.returncode != 0 or not os.path.exists(fn):
                    os.rename(tmp, fn)  # 兜底：DASH 音频流直接当 m4a 保存
            else:
                os.rename(tmp, fn)
            if os.path.exists(tmp):
                os.remove(tmp)
            real = verify_dur(fn)
            need_review = real and dur and abs(real - dur) > 3
            print(f"[{i}] 完成  {title} | up:{up} | {ext} {real}s vs 平台{dur}s{' ⚠需复核' if need_review else ''}")
            rec = {"key": key, "src": "bilibili", "t": title, "file": fn, "bvid": bvid, "up": up}
            open(log, "a", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False) + "\n")
            if need_review:
                open(review_path, "a", encoding="utf-8").write(
                    json.dumps({**rec, "plat_dur": dur, "real_dur": real}, ensure_ascii=False) + "\n")
            n_ok += 1
        except Exception as e:
            print(f"[{i}] 失败 {title}: {str(e)[:70]}")
        time.sleep(a.sleep)
    print(f"\n本轮 B 站新增: {n_ok}")


if __name__ == "__main__":
    main()
