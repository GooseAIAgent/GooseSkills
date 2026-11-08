#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对网易云「原 ID 无音源」（下架/云盘/合集）的曲目，搜索同平台同一条录音的替代 ID。

认定条件（四重，缺一不可，宁缺勿错）：
  1) 歌名标准化后相等（或候选仅多 Live/翻唱等版本标记，且双方标记性一致）
  2) 歌手集合有交集
  3) 时长差 <= 1.5 秒（专辑名严格相等可替代时长条件，是版本最强标识）
  4) 排除伴奏/翻唱/演唱会等版本词污染

用法：
  python3 netease_reid.py --list missing.json [--workers 3] [--out DIR] [--config CFG]
"""
import argparse, json, os, re, sys, time
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcommon import (UA, NE_LADDER, NE_HOSTS, load_config, resolve_out, safe, norm,
                     magic_ext, existing, tag, download, add_common_args,
                     ledger_init, ledger_save, ledger_skip, ledger_mark)

DIR_NE = None
MANIFEST = None
REVIEW = None
VER = re.compile(r"(live|现场|演唱会|伴奏|inst|纯音乐|cover|翻唱|remix|demo|小样)", re.I)


def post(url, form, ck, tries=3):
    import urllib.error
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


def search(q, ck, n=20):
    """搜索接口限流严重：/api/search/pc 可用，/api/search/get 会 405。
    非 200 或空结果时退避重试。"""
    qs = urllib.parse.urlencode({"s": q, "type": 1, "limit": n})
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request("https://music.163.com/api/search/pc?" + qs,
                headers={"User-Agent": UA, "Referer": "https://music.163.com/", "Cookie": ck})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
            if d.get("code") == 200:
                songs = (d.get("result") or {}).get("songs") or []
                if songs:
                    return songs
                last = "空结果"
            else:
                last = f"code={d.get('code')}"
        except Exception as e:
            last = str(e)[:40]
        time.sleep(2.5 * (attempt + 1))
    return []


def get_url(sid, ck, level, enc):
    d = post("https://music.163.com/api/song/enhance/player/url/v1",
             {"ids": json.dumps([int(sid)]), "level": level, "encodeType": enc}, ck)
    it = (d.get("data") or [{}])[0]
    return it.get("url") or "", it.get("br") or 0, (it.get("type") or "").lower()


def candidate_urls(url):
    out = [url]
    m = re.match(r"(https?)://([^./]+)(\.music\.126\.net)(.*)$", url)
    if m:
        sch, h, suf, rest = m.groups()
        for x in NE_HOSTS:
            if x != h: out.append(f"{sch}://{x}{suf}{rest}")
    return out


def fetch(url, tmp, ck):
    hd = {"User-Agent": UA, "Referer": "https://music.163.com/", "Cookie": ck}
    last = None
    for u in candidate_urls(url):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=90)
            if "text/html" in (r.headers.get("Content-Type") or "").lower():
                r.close(); last = "HTML"; continue
            r.close()
            return download(u, tmp, hd)
        except Exception as e:
            last = str(e); continue
    raise IOError(f"节点失败({last})")


def handle(it, ck):
    title, artist, alb, dur = it["t"], it["a"], it.get("alb") or "", it.get("dur") or 0
    base = safe(f"{artist} - {title}")
    if existing(base, DIR_NE):
        return ("已存在", "", None)
    try:
        res = search(f"{title} {artist}", ck)
    except Exception as e:
        return ("失败", f"搜索异常 {e.__class__.__name__}", None)
    hits = []
    pl_live = bool(VER.search(title))
    for s in res:
        nm = s.get("name") or ""
        if norm(nm) != norm(title):
            # 允许候选标题带版本后缀、而去掉后缀后与歌单一致
            if not (norm(re.sub(r"\(.*?\)|（.*?）|\[.*?\]", "", nm)) ==
                    norm(re.sub(r"\(.*?\)|（.*?）|\[.*?\]", "", title))
                    and bool(VER.search(nm)) == pl_live):
                continue
        else:
            if bool(VER.search(nm)) != pl_live:
                continue
        singers = [a.get("name", "") for a in s.get("artists") or []]
        if not any(x and (x == p or x in p or p in x)
                   for p in re.split(r"[、,，&/]", artist) for x in singers):
            continue
        dm = (s.get("duration") or 0) / 1000.0
        salb = s.get("album") or {}
        same_album = alb and norm((salb.get("name") or "")) == norm(alb)
        if not (same_album or (dur and dm and abs(dm - dur) <= 1.5)):
            continue
        hits.append((abs(dm - dur), s))
    if not hits:
        return ("无替换", "同名/同专辑/同时长 均无", None)
    hits.sort(key=lambda z: z[0])
    s = hits[0][1]
    for level, enc in NE_LADDER:
        try:
            url, br, typ = get_url(s["id"], ck, level, enc)
        except Exception:
            continue
        if not url: continue
        tmp = os.path.join(DIR_NE, base + ".part")
        try:
            head, got = fetch(url, tmp, ck)
        except Exception:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass
            continue
        real = magic_ext(head)
        if not real:
            os.remove(tmp); continue
        dst = os.path.join(DIR_NE, base + "." + real)
        os.replace(tmp, dst)
        flen = tag(dst, title, artist, alb)
        rec = {"t": title, "a": artist, "sid": str(s["id"]), "orig_sid": it.get("sid"),
               "file": os.path.basename(dst), "level": level, "kbps": round(br / 1000) if br else 0,
               "fmt": real, "mb": round(got / 1048576, 1), "album": alb,
               "dur_list": dur, "dur_file": flen, "diff": round(abs(flen - dur), 1), "reid": True}
        with open(REVIEW, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return ("完成", f"{real.upper()} {rec['kbps']}k 差{rec['diff']}s", rec)
    return ("无替换", "找到候选但无音源", None)


def main():
    ap = argparse.ArgumentParser(description="网易云替代ID搜索下载")
    add_common_args(ap)
    ap.add_argument("--list", required=True, help="缺失曲目清单 JSON")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()

    global DIR_NE, MANIFEST, REVIEW
    root = resolve_out(a.out)
    DIR_NE = os.path.join(root, "网易云音乐")
    MANIFEST = os.path.join(root, "下载记录.jsonl")
    REVIEW = os.path.join(root, "替代ID记录.jsonl")

    cfg, cfg_path = load_config(a.config)
    ck = (cfg.get("netease_cookie") or "").strip()
    if not ck:
        print(f"缺少登录态：请在 {cfg_path} 配置 netease_cookie。")
        sys.exit(1)
    os.makedirs(DIR_NE, exist_ok=True)
    items = json.load(open(a.list, encoding="utf-8"))

    # 台账过滤：之前已下载且文件仍在的曲目直接跳过
    ledger = ledger_init()
    if ledger is not None:
        before = len(items)
        items = [it for it in items if not ledger_skip(ledger, it["a"], it["t"])]
        skipped = before - len(items)
        if skipped:
            print(f"台账命中：跳过已下载 {skipped} 首", flush=True)

    st = {}; fmt = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(handle, it, ck): it for it in items}
        for i, fu in enumerate(as_completed(futs), 1):
            it = futs[fu]
            try: status, msg, rec = fu.result()
            except Exception as e: status, msg, rec = "失败", str(e)[:40], None
            st[status] = st.get(status, 0) + 1
            if rec:
                fmt[rec["fmt"]] = fmt.get(rec["fmt"], 0) + 1
                with open(MANIFEST, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if ledger is not None and status == "完成":
                    ledger_mark(ledger, it["a"], it["t"],
                                os.path.join(DIR_NE, rec["file"]),
                                detail=f"reid {rec['fmt']} {rec['kbps']}k")
                    ledger_save(ledger)
            print(f"[{i}/{len(items)}] {status:<4} {msg:<24} {it['a']} - {it['t']}", flush=True)
    print(f"\n替代ID完成 {st} 格式 {fmt}", flush=True)


if __name__ == "__main__":
    main()
