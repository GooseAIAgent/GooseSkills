#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网易云音乐下载器：严格按 song id 取曲，音质 FLAC > 320k > 192k > 128k。

要点
- 只用清单里的 song id 请求，绝不用标题搜索，从机制上避免下错版本。
- 音质逐级尝试，优先无损；无无损时取平台可给的最高码率。
- 网易 CDN 部分节点会返回 403/503，自动轮换备用节点重试。
- 下载后读实际时长与清单时长比对，偏差 >3s 记入「版本待核对」。

用法：
  python3 netease_dl.py --list missing.json [--workers 4] [--out DIR] [--config CFG]

missing.json 每项格式：{"t": 歌名, "a": 歌手, "alb": 专辑, "dur": 秒, "sid": "网易云ID或链接"}
"""
import argparse, json, os, re, sys, time
import urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcommon import (UA, NE_LADDER, NE_HOSTS, load_config, resolve_out, safe,
                     magic_ext, existing, tag, download, add_common_args)

DIR_NE = None      # 运行时由 --out 注入
MANIFEST = None
REVIEW = None


def cookie(cfg):
    return (cfg.get("netease_cookie") or "").strip()


def post(url, form, ck, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(),
                headers={"User-Agent": UA, "Referer": "https://music.163.com/",
                         "Content-Type": "application/x-www-form-urlencoded", "Cookie": ck})
            return json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception as e:
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def get_url(sid, ck, level, enc):
    d = post("https://music.163.com/api/song/enhance/player/url/v1",
             {"ids": json.dumps([int(sid)]), "level": level, "encodeType": enc}, ck)
    it = (d.get("data") or [{}])[0]
    return it.get("url") or "", it.get("br") or 0, (it.get("type") or "").lower(), it.get("size") or 0


def candidate_urls(url):
    """原地址优先，失败时换 CDN 节点重试。"""
    out = [url]
    m = re.match(r"(https?)://([^./]+)(\.music\.126\.net)(.*)$", url)
    if m:
        scheme, host, suffix, rest = m.groups()
        for h in NE_HOSTS:
            if h != host:
                out.append(f"{scheme}://{h}{suffix}{rest}")
    return out


def fetch(url, tmp, ck):
    hd = {"User-Agent": UA, "Referer": "https://music.163.com/", "Cookie": ck}
    last = None
    for u in candidate_urls(url):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=90)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                r.close(); last = "HTML响应"; continue
            r.close()
            head, got = download(u, tmp, hd)
            return head, got
        except urllib.error.HTTPError as e:
            last = f"HTTP{e.code}"
            if e.code in (403, 503, 404, 502, 504):
                continue
            raise
        except Exception as e:
            last = str(e); continue
    raise IOError(f"所有节点失败({last})")


def handle(it, ck):
    title, artist, dur = it["t"], it["a"], it.get("dur") or 0
    album = it.get("alb") or ""
    raw = str(it.get("sid") or "")
    m = re.search(r"(\d+)", raw)
    sid = m.group(1) if m else None
    base = safe(f"{artist} - {title}")
    if existing(base, DIR_NE):
        return ("已存在", "", None)
    if not sid:
        return ("失败", "无ID", None)

    tried = []
    for level, enc in NE_LADDER:
        url, br, typ, size = "", 0, "", 0
        for attempt in range(2):
            try:
                url, br, typ, size = get_url(sid, ck, level, enc)
                break
            except Exception as e:
                if attempt == 1:
                    return ("失败", f"取流失败 {e}", None)
                time.sleep(1.5)
        if not url:
            tried.append(f"{level}:无")
            continue
        tmp = os.path.join(DIR_NE, base + ".part")
        try:
            head, got = fetch(url, tmp, ck)
        except Exception as e:
            tried.append(f"{level}:{e}")
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass
            continue
        real = magic_ext(head)
        if not real:
            os.remove(tmp); tried.append(f"{level}:格式未知"); continue
        dst = os.path.join(DIR_NE, base + "." + real)
        os.replace(tmp, dst)
        flen = tag(dst, title, artist, album)
        diff = abs(flen - dur) if (flen and dur) else 0
        rec = {"t": title, "a": artist, "sid": sid, "file": os.path.basename(dst),
               "level": level, "kbps": round(br / 1000) if br else 0, "fmt": real,
               "mb": round(got / 1048576, 1), "dur_list": dur, "dur_file": flen,
               "diff": round(diff, 1)}
        return ("待核" if diff > 3 else "完成", f"{real.upper()} {rec['kbps']}k {rec['mb']}MB", rec)
    return ("无音源", "/".join(tried)[:60], None)


def main():
    ap = argparse.ArgumentParser(description="网易云音乐按ID批量下载")
    add_common_args(ap)
    ap.add_argument("--list", required=True, help="缺失曲目清单 JSON")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    global DIR_NE, MANIFEST, REVIEW
    root = resolve_out(a.out)
    DIR_NE = os.path.join(root, "网易云音乐")
    MANIFEST = os.path.join(root, "下载记录.jsonl")
    REVIEW = os.path.join(root, "版本待核对.jsonl")

    cfg, cfg_path = load_config(a.config)
    ck = cookie(cfg)
    if not ck:
        print(f"缺少登录态：请在 {cfg_path} 配置 netease_cookie（MUSIC_U=...）。\n"
              "匿名请求仅能取得 128k 音质；VIP cookie 才能取得 FLAC/320k。")
        sys.exit(1)
    os.makedirs(DIR_NE, exist_ok=True)
    items = json.load(open(a.list, encoding="utf-8"))
    if a.offset: items = items[a.offset:]
    if a.limit: items = items[:a.limit]
    st = {"完成": 0, "无音源": 0, "已存在": 0, "失败": 0, "待核": 0}
    fmt = {}
    t0 = time.time(); done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(handle, it, ck): it for it in items}
        for fu in as_completed(futs):
            it = futs[fu]
            try:
                status, msg, rec = fu.result()
            except Exception as e:
                status, msg, rec = "失败", str(e)[:40], None
            done += 1
            st[status] = st.get(status, 0) + 1
            if rec:
                fmt[rec["fmt"]] = fmt.get(rec["fmt"], 0) + 1
                with open(MANIFEST, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if status == "待核":
                    with open(REVIEW, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if status != "已存在":
                print(f"[{done}/{len(items)}] {status:<4} {msg:<26} {it['a']} - {it['t']}", flush=True)
            if done % 50 == 0:
                el = time.time() - t0
                print(f"--- 进度 {done}/{len(items)} 已用{el/60:.0f}min 剩余约{el/done*(len(items)-done)/60:.0f}min "
                      f"{st} {fmt}", flush=True)
    print(f"\n完成 {st} 格式 {fmt} 用时 {(time.time()-t0)/60:.1f}min", flush=True)


if __name__ == "__main__":
    main()
