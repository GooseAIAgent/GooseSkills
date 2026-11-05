#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 音乐兜底下载器：用于网易云无音源（下架/版权/云盘）的曲目。

- 搜索后必须「歌名一致 + 歌手有交集（或歌名括号内点名歌手）+ 时长差≤5s」三重校验。
- 音质阶梯 F000(FLAC) > M800(320k) > C400(128k)，取到哪个用哪个。
- 同一首歌在 QQ 常有多条 mid（不同合辑），必须逐条试 vkey，只试第一条会漏掉有版权的那条。
- --relax 宽松档：仅认同歌手（或合辑按时长）版本，match 标记为「同歌手」需人工复核。

用法：
  python3 qq_dl.py --list missing.json [--workers 3] [--relax] [--out DIR] [--config CFG]
"""
import argparse, json, os, re, sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcommon import (UA, QQ_LADDER, load_config, resolve_out, safe, norm,
                     magic_ext, existing, tag, download, add_common_args)

DIR_QQ = None
MANIFEST = None
FAIL_LOG = None

LOOSE_ARTIST_NAMES = {"群星", "未知", "佚名", "网络歌手", ""}


def qq_cookie(cfg):
    """支持两种配置：完整 qq_cookie，或 qq_uin + qq_keyst 分开填（更省事）。"""
    ck = (cfg.get("qq_cookie") or "").strip()
    uin = str(cfg.get("qq_uin") or "").strip()
    keyst = str(cfg.get("qq_keyst") or "").strip()
    if keyst:
        if uin and not re.search(r"(^|;\s*)uin=", ck):
            ck = f"uin={uin if uin.startswith('o') else 'o' + uin}; " + ck
        if not re.search(r"(^|;\s*)qm_keyst=", ck):
            ck = f"qm_keyst={keyst}; " + ck
    return ck


def qq_uin(ck):
    m = re.search(r"(?:^|;\s*)uin=o?(\d+)", ck or "")
    return m.group(1) if m else "0"


def post(payload, ck=""):
    hd = {"User-Agent": UA, "Content-Type": "application/json", "Referer": "https://y.qq.com/"}
    if ck: hd["Cookie"] = ck
    req = urllib.request.Request("https://u.y.qq.com/cgi-bin/musicu.fcg",
                                 data=json.dumps(payload).encode(), headers=hd)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def search(q, ck="", n=8):
    d = post({"req": {"method": "DoSearchForQQMusicDesktop", "module": "music.search.SearchCgiService",
                      "param": {"search_type": 0, "query": q, "page_num": 1, "num_per_page": n}}}, ck)
    out = []
    for s in d["req"]["data"]["body"]["song"]["list"]:
        out.append({"name": s["name"], "mid": s["mid"], "interval": s.get("interval") or 0,
                    "singers": [a["name"] for a in s.get("singer") or []],
                    "album": (s.get("album") or {}).get("name") or ""})
    return out


def vkey(mid, code, ext, ck="", uin="0"):
    """取播放地址。注意 uin 必须传真实 QQ 号：VIP 无损绑定 uin，传 0 会全部失败。"""
    try:
        d = post({"req": {"module": "vkey.GetVkeyServer", "method": "CgiGetVkey",
                          "param": {"guid": "10000", "songmid": [mid], "songtype": [0], "uin": uin,
                                    "loginflag": 1, "platform": "20",
                                    "filename": [f"{code}{mid}{mid}.{ext}"]}}}, ck)
        info = d["req"]["data"]["midurlinfo"][0]
        purl = info.get("purl") or ""
        if not purl: return ""
        sip = d["req"]["data"].get("sip") or ["https://dl.stream.qqmusic.qq.com/"]
        return sip[0] + purl
    except Exception:
        return ""


def fetch(url, tmp, ck=""):
    hd = {"User-Agent": UA, "Referer": "https://y.qq.com/", "Cookie": ck}
    return download(url, tmp, hd)


def title_variants(title):
    """歌单里常带 <别名> 或结尾（合作者）注释，生成去掉注释的核心歌名。"""
    vs = [title]
    core = re.sub(r"[<＜][^<>＜＞]*[>＞]", "", title)
    core = re.sub(r"[（(][^（）()]*[）)]\s*$", "", core).strip(" -_·")
    if core and norm(core) != norm(title):
        vs.append(core)
    return vs


def bracket_text(title):
    """取歌名里 <别名> 或（合作者）中的文字，常用于标注真实演唱者。"""
    segs = re.findall(r"[<＜]([^<>＜＞]*)[>＞]|[（(]([^（）()]*)[）)]", title or "")
    return " ".join(a or b for a, b in segs)


def artist_variants(artist):
    parts = [p.strip() for p in re.split(r"[、,，&/;；\+\s]+|feat\.?|ft\.?", artist or "", flags=re.I)]
    out = [artist] + [p for p in parts if p and p != artist]
    return out[:3]


def handle(it, ck, uin="0", relax=False):
    title, artist, dur = it["t"], it["a"], it.get("dur") or 0
    album = it.get("alb") or ""
    base = safe(f"{artist} - {title}")
    if existing(base, DIR_QQ): return ("已存在", "", None)
    bracket = bracket_text(title)
    cands, seen = [], set()
    queries = [f"{ar} {tv}" for ar in artist_variants(artist) for tv in title_variants(title)]
    if bracket: queries += [f"{title_variants(title)[-1]} {bracket}"]
    if relax: queries += title_variants(title)
    for q in queries:
        try:
            for c in search(q, ck):
                if c["mid"] in seen: continue
                seen.add(c["mid"]); cands.append(c)
        except Exception:
            pass
        if cands and not relax: break
    if not cands: return ("失败", "搜索无结果", None)
    tvs = [norm(v) for v in title_variants(title)]
    picked = []
    for c in cands:
        n = norm(c["name"])
        if n not in tvs: continue
        loose = n != norm(title)
        hit_artist = any(x and (x in artist or artist in x) for x in c["singers"])
        hit_bracket = bool(bracket) and any(
            x and (x in bracket or bracket in x) for x in c["singers"])
        if relax:
            # 宽松档：只认同歌手版本（或歌单标注为合辑/群星时按时长锁定）
            if hit_artist:
                if dur and c["interval"] and abs(c["interval"] - dur) > 8: continue
                picked.append((True, True, c))
                continue
            if artist.strip() in LOOSE_ARTIST_NAMES and dur and c["interval"] \
                    and abs(c["interval"] - dur) <= 3:
                picked.append((True, False, c))
            continue
        if not (hit_artist or hit_bracket): continue
        lim = 3 if loose else 5   # 歌名靠核心词匹配时，时长要求更严
        if dur and c["interval"] and abs(c["interval"] - dur) > lim: continue
        picked.append((loose, hit_artist, c))
    if not picked: return ("无音源", "QQ无匹配版本", None)
    picked.sort(key=lambda t: (t[0], not t[1], abs(t[2]["interval"] - dur)))
    for loose, hit_artist, c in picked[:3]:
        for code, ext, kbps in QQ_LADDER:
            u = vkey(c["mid"], code, ext, ck, uin)
            if not u: continue
            tmp = os.path.join(DIR_QQ, base + ".part")
            try:
                head, got = fetch(u, tmp, ck)
            except Exception:
                if os.path.exists(tmp):
                    try: os.remove(tmp)
                    except Exception: pass
                continue
            real = magic_ext(head)
            if not real:
                os.remove(tmp); continue
            dst = os.path.join(DIR_QQ, base + "." + real)
            os.replace(tmp, dst)
            flen = tag(dst, title, artist, album)
            diff = abs(flen - dur) if (flen and dur) else 0
            rec = {"t": title, "a": artist, "src": "qq", "file": os.path.basename(dst),
                   "level": code, "kbps": kbps, "fmt": real, "mb": round(got / 1048576, 1),
                   "dur_list": dur, "dur_file": flen, "diff": round(diff, 1),
                   "match": ("同歌手" if relax and loose else ("核心名" if loose else "精确")),
                   "qq_name": c["name"], "qq_singer": "/".join(c["singers"])}
            return ("待核" if diff > 3 else "完成", f"{real.upper()} {kbps}k {rec['mb']}MB", rec)
    return ("无音源", "QQ无可用音源(多为VIP)", None)


def main():
    ap = argparse.ArgumentParser(description="QQ 音乐兜底批量下载")
    add_common_args(ap)
    ap.add_argument("--list", required=True, help="缺失曲目清单 JSON")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--relax", action="store_true",
                    help="宽松档：只认同歌手(或合辑按时长)版本，结果需人工复核")
    a = ap.parse_args()

    global DIR_QQ, MANIFEST, FAIL_LOG
    root = resolve_out(a.out)
    DIR_QQ = os.path.join(root, "QQ音乐")
    MANIFEST = os.path.join(root, "下载记录.jsonl")
    FAIL_LOG = os.path.join(root, "QQ未获取.jsonl")

    cfg, cfg_path = load_config(a.config)
    os.makedirs(DIR_QQ, exist_ok=True)
    ck = qq_cookie(cfg)
    uin = qq_uin(ck)
    if not ck:
        print(f"缺少登录态：请在 {cfg_path} 配置 qq_uin + qq_keyst（或完整 qq_cookie）。")
        sys.exit(1)
    print(f"登录 uin={uin} cookie长度={len(ck)}", flush=True)
    items = json.load(open(a.list, encoding="utf-8"))
    st = {}; fmt = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(handle, it, ck, uin, a.relax): it for it in items}
        for i, fu in enumerate(as_completed(futs), 1):
            it = futs[fu]
            try: status, msg, rec = fu.result()
            except Exception as e: status, msg, rec = "失败", str(e)[:40], None
            st[status] = st.get(status, 0) + 1
            if status in ("无音源", "失败"):
                with open(FAIL_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": it["t"], "a": it["a"], "reason": msg,
                                        "dur": it.get("dur")}, ensure_ascii=False) + "\n")
            if rec:
                fmt[rec["fmt"]] = fmt.get(rec["fmt"], 0) + 1
                with open(MANIFEST, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(items)}] {status:<4} {msg:<24} {it['a']} - {it['t']}", flush=True)
    print(f"\nQQ 完成 {st} 格式 {fmt}", flush=True)


if __name__ == "__main__":
    main()
