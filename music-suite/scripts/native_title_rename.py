#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外语歌名统一为「本国原名（中文译名）」，同步改文件名 / .lrc / title 标签。

命名规范（本 skill 的默认约定）：
    歌手 - 原名（中文译名） [原版本后缀]
    例：IU - 분홍신（粉红色高跟鞋）
        IU - 크레파스（蜡笔） (电视剧《漂亮男人》插曲)
        米津玄師 - 打上花火（烟花）

只改名，不做翻译创作：原名与译名都取自平台数据或用户维护的 overrides 表，
匹配不上的保持原样并列入报告，绝不猜测。

数据源：
  QQ 音乐：`name` = 本国原名，`title` = 「原名 (中文译名)」，是最好用的单一来源
  网易云  ：`name` 常已被译成中文，`tns` 里才有原名（部分曲目有），作补充

用法：
  python3 native_title_rename.py /path/to/music --scan-only        # 只列候选，不联网
  python3 native_title_rename.py /path/to/music --artist IU        # 预览某个歌手
  python3 native_title_rename.py /path/to/music --artist IU --apply
  python3 native_title_rename.py /path/to/music --overrides ov.json --apply

默认 dry-run；--apply 才真正改名。记录表默认写到脚本目录 rename-record.csv。
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request

AUDIO_EXT = (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".ape", ".wv")

# 各语言的「本国文字」区段。macOS 会把韩文存成 NFD（Jamo），所以两个区段都要覆盖。
SCRIPTS = {
    "hangul": re.compile(r"[\u1100-\u11FF\uA960-\uA97F\uAC00-\uD7A3\uD7B0-\uD7FF]"),
    "kana": re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u31F0-\u31FF]"),
    "cyrillic": re.compile(r"[\u0400-\u04FF]"),
    "thai": re.compile(r"[\u0E00-\u0E7F]"),
    "arabic": re.compile(r"[\u0600-\u06FF]"),
    "latin": re.compile(r"[A-Za-z]"),
}
HAN = re.compile(r"[\u4e00-\u9fff]")
BRACKET_TAIL = re.compile(r"\s*[（(\[][^（()）\[\]]*[)）\]]\s*$")
CN_PAREN_TAIL = re.compile(r"\s*[（(]([^（()）]*[\u4e00-\u9fff][^（()）]*)[)）]\s*$")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ------------------------------------------------------------------ 文本工具

def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def nrm(s):
    """去标点/空白/大小写，用于比对同名。"""
    return re.sub(r"[\s\-_·、,，.。!！?？\"'“”‘’()（）\[\]【】《》&/\\+~]+", "", nfc(s).lower())


def overlap(a, b):
    A = set(a)
    return len(A & set(b)) / len(A) if A else 0.0


def has_script(text, script):
    pat = SCRIPTS.get(script)
    return bool(pat and pat.search(nfc(text)))


def split_suffix(title):
    """拆出尾部的版本后缀：`(Live)`、`(Inst.)`、`(Acoustic Ver.)`、`(电视剧《…》插曲)`…"""
    t, suf = title.strip(), ""
    while True:
        m = BRACKET_TAIL.search(t)
        if not m:
            break
        suf = t[m.start():].strip() + (" " + suf if suf else "")
        t = t[:m.start()].strip()
    return t, suf.strip()


def strip_cn_parens(name):
    """去掉原名尾部的中文括号——那通常是译名，会和我们要加的括号重复。"""
    out = name.strip()
    while True:
        m = CN_PAREN_TAIL.search(out)
        if not m:
            break
        out = out[:m.start()].strip()
    return out


def build_title(native, zh, suffix):
    base = f"{strip_cn_parens(nfc(native))}（{nfc(zh)}）"
    if suffix:
        core = nrm(suffix)
        if core and core not in nrm(base):
            base = f"{base} {suffix}"
    return base


def parse_name(filename):
    """`歌手 - 歌名.ext` → (artist, title)。无分隔符时 artist 为空。"""
    stem = nfc(os.path.splitext(os.path.basename(filename))[0])
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


# ------------------------------------------------------------------ 平台查询

def load_cfg(path=""):
    cfg = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    cookie = os.environ.get("QQ_COOKIE") or cfg.get("qq_cookie") or ""
    uin = str(os.environ.get("QQ_UIN") or cfg.get("qq_uin") or "").strip()
    keyst = os.environ.get("QQ_KEYST") or cfg.get("qq_keyst") or ""
    if keyst:
        if uin and not re.search(r"(?:^|;\s*)uin=", cookie):
            cookie = f"uin={uin if uin.startswith('o') else 'o' + uin}; " + cookie
        if not re.search(r"(?:^|;\s*)qm_keyst=", cookie):
            cookie = f"qm_keyst={keyst}; " + cookie
    cfg["qq_cookie"] = cookie
    return cfg


def qq_search(query, n=8, cookie="", retries=3):
    """返回 [{name(原名), title(原名+译名), singers, interval, album}]。"""
    payload = {"req": {"method": "DoSearchForQQMusicDesktop",
                       "module": "music.search.SearchCgiService",
                       "param": {"search_type": 0, "query": query,
                                 "page_num": 1, "num_per_page": n}}}
    headers = {"User-Agent": UA, "Content-Type": "application/json",
               "Referer": "https://y.qq.com/"}
    if cookie:
        headers["Cookie"] = cookie
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request("https://u.y.qq.com/cgi-bin/musicu.fcg",
                                         data=json.dumps(payload).encode(), headers=headers)
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
            lst = ((((d.get("req") or {}).get("data") or {}).get("body") or {})
                   .get("song", {}).get("list") or [])
            out = []
            for s in lst:
                out.append({
                    "name": nfc(s.get("name") or ""),
                    "title": nfc(s.get("title") or ""),
                    "singers": [nfc(a.get("name") or "") for a in s.get("singer") or []],
                    "interval": int(s.get("interval") or 0),
                    "album": nfc((s.get("album") or {}).get("name") or ""),
                })
            return out
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    print(f"  [warn] QQ 搜索失败：{query} ({last})", file=sys.stderr)
    return []


def ne_search(query, n=8, limit_interval=1.5):
    """网易云：name 多为中文，tns 里可能有原名。仅作补充。"""
    import urllib.parse
    url = "https://music.163.com/api/search/pc?" + urllib.parse.urlencode(
        {"s": query, "type": 1, "limit": n})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Referer": "https://music.163.com/"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return []
    if d.get("code") not in (200, None):
        return []
    time.sleep(limit_interval)
    songs = (d.get("result") or {}).get("songs") or []
    out = []
    for s in songs:
        tns = s.get("tns") or s.get("transNames") or []
        out.append({"name": nfc(s.get("name") or ""),
                    "alias": [nfc(x) for x in tns if x],
                    "singers": [nfc(a.get("name") or "") for a in s.get("artists") or []],
                    "interval": int((s.get("duration") or 0) / 1000),
                    "album": nfc((s.get("album") or {}).get("name") or "")})
    return out


# ------------------------------------------------------------------ 匹配

def artist_tokens(artist):
    return [x for x in re.split(r"[、/&,，]|\bfeat\.?\b|\bwith\b", nfc(artist), flags=re.I) if x.strip()]


def artist_hit(singers, artist):
    toks = [nrm(t) for t in artist_tokens(artist) if nrm(t)]
    if not toks:
        return True
    for s in singers:
        ns = nrm(s)
        if any(t == ns or t in ns or ns in t for t in toks):
            return True
    return False


def pick_candidate(title, artist, cands, dur=0, scripts=("hangul", "kana")):
    """在候选里挑一条可信的原名。返回 (cand, confidence, reason)。"""
    zh = nrm(title)
    best, best_key = None, None
    for c in cands:
        if not c.get("name"):
            continue
        if not any(has_script(c["name"], sc) for sc in scripts):
            continue
        # 原名是否真的和本地标题不同（否则无需改）
        if nrm(c["name"]) == zh:
            continue
        t = nrm(c.get("title") or "")
        exact = bool(zh) and zh in t
        ov = overlap(zh, t)
        singer = artist_hit(c.get("singers") or [], artist)
        dd = abs((c.get("interval") or 0) - dur) if dur else 99
        key = (0 if exact else (1 if ov >= 0.6 else 2),
               0 if singer else 1,
               dd)
        if best_key is None or key < best_key:
            best, best_key = c, key
    if not best:
        return None, "", "无符合语言条件的候选"
    sem, sing, dd = best_key
    if sem == 2:
        return None, "", "候选与本地名不符"
    if sing == 1 and sem != 0:
        return None, "", "歌手不符"
    if dur and dd > 6:
        return None, "", f"时长差 {dd}s"
    conf = "high" if (sem == 0 and sing == 0 and (not dur or dd <= 2)) else "medium"
    return best, conf, ""


def nearest_zh(cand, fallback):
    """从候选 title 里抽出中文译名（`원제 (中文)` 结构），失败则用本地标题。"""
    m = CN_PAREN_TAIL.search(nfc(cand.get("title") or ""))
    if m:
        zh = m.group(1).strip()
        if zh:
            return zh
    return fallback


# ------------------------------------------------------------------ 扫描 / 执行

def scan(root, scripts, skip_latin=False, artists=()):
    items = []
    for dp, ds, fs in os.walk(root):
        ds[:] = [d for d in ds if d not in (".cover", ".git", "__pycache__")]
        for f in fs:
            if not f.lower().endswith(AUDIO_EXT):
                continue
            path = os.path.join(dp, f)
            artist, title = parse_name(f)
            if artists and not any(a.lower() in nfc(artist).lower() or a.lower() in nfc(dp).lower()
                                   for a in artists):
                continue
            # 已经是「原名（译名）」格式 → 跳过
            if any(has_script(title, sc) for sc in scripts):
                continue
            if not HAN.search(title):
                continue          # 纯英文/纯外文原名，不动
            if skip_latin and SCRIPTS["latin"].search(title):
                continue
            items.append({"path": path, "dir": os.path.basename(dp),
                          "artist": artist, "title": title,
                          "ext": os.path.splitext(f)[1]})
    return items


def duration_of(path):
    try:
        from mutagen import File as MF
        a = MF(path)
        return int(a.info.length) if a and a.info else 0
    except Exception:
        return 0


def write_record(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["目录", "原名", "新名", "来源", "置信度", "状态"])
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="外语歌名统一为「本国原名（中文译名）」")
    ap.add_argument("root", help="音乐库根目录（可只传某个歌手目录）")
    ap.add_argument("--scripts", default="hangul,kana",
                    help="要识别的本国文字，逗号分隔：hangul,kana,cyrillic,thai,arabic,latin（默认 hangul,kana）")
    ap.add_argument("--artist", action="append", default=[],
                    help="只处理指定歌手（可多次传，匹配目录名或文件名里的歌手）")
    ap.add_argument("--overrides", help="人工核实表 JSON：{\"歌手|中文名\": {\"native\":\"原名\",\"zh\":\"译名\"}}")
    ap.add_argument("--config", default=os.environ.get("MUSIC_DL_CONFIG", ""),
                    help="凭证 JSON（qq_cookie / qq_uin / qq_keyst），也可用环境变量")
    ap.add_argument("--scan-only", action="store_true", help="只列本地候选，不联网")
    ap.add_argument("--apply", action="store_true", help="实际执行（默认预览）")
    ap.add_argument("--skip-latin", action="store_true", help="标题含拉丁字母的一律跳过（保守模式）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少首（调试用）")
    ap.add_argument("--min-conf", choices=("high", "medium"), default="medium",
                    help="执行门槛：high=只改三重校验全过的；medium=允许语义重合度达标但歌手/时长稍弱的")
    ap.add_argument("--interval", type=float, default=0.8, help="平台查询间隔秒数")
    ap.add_argument("--record", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "rename-record.csv"))
    args = ap.parse_args()

    scripts = tuple(s.strip() for s in args.scripts.split(",") if s.strip())
    overrides = {}
    if args.overrides and os.path.exists(args.overrides):
        with open(args.overrides, encoding="utf-8") as f:
            overrides = json.load(f)

    items = scan(args.root, scripts, skip_latin=args.skip_latin, artists=args.artist)
    print(f"本地候选（标题为中文、文件名无本国文字）：{len(items)} 首")
    if args.limit:
        items = items[:args.limit]
    if args.scan_only or not items:
        for it in items[:60]:
            print(f'  [{it["dir"][:20]:22s}] {it["artist"][:18]:20s} | {it["title"]}')
        if len(items) > 60:
            print(f"  … 其余 {len(items) - 60} 首")
        write_record(args.record, [[it["dir"], it["title"], "", "", "", "候选"] for it in items])
        print(f"\n记录表：{args.record}")
        return 0

    cfg = load_cfg(args.config)
    cookie = cfg.get("qq_cookie", "")
    plan, skipped = [], []

    for i, it in enumerate(items, 1):
        artist, title = it["artist"], it["title"]
        key = f"{artist}|{title}"
        if key in overrides:
            ov = overrides[key]
            native, zh, src, conf = ov["native"], ov.get("zh") or title, "人工核实", "high"
        else:
            cands = qq_search(f"{title} {artist}".strip(), cookie=cookie)
            time.sleep(args.interval)
            cand, conf, why = pick_candidate(title, artist, cands, duration_of(it["path"]), scripts)
            if not cand:
                if not cands:
                    ne = ne_search(f"{title} {artist}".strip())
                    for c in ne:
                        if c["alias"] and any(has_script(a, scripts[0]) for a in c["alias"]):
                            cand, conf = {"name": c["alias"][0], "title": "", "singers": c["singers"],
                                          "interval": c["interval"]}, "medium"
                            break
                if not cand:
                    skipped.append([it["dir"], title, "", "", "", why or "无匹配"])
                    continue
            native, zh = cand["name"], nearest_zh(cand, title)
            src = "QQ匹配" if cand.get("title") else "网易补充"

        base, suffix = split_suffix(title)
        new_title = build_title(native, zh, suffix)
        if not any(has_script(new_title, sc) for sc in scripts):
            skipped.append([it["dir"], title, "", "", "", "原名无本国文字"])
            continue
        if nrm(new_title) == nrm(title):
            skipped.append([it["dir"], title, "", "", "", "无需改名"])
            continue
        if args.min_conf == "high" and conf != "high":
            skipped.append([it["dir"], title, new_title, src, conf, "置信度不足(high 门槛)"])
            continue
        plan.append({**it, "new": new_title, "src": src, "conf": conf})

    # 同目录重名 → (2)(3)
    seen = {}
    for p in plan:
        k = (p["dir"], nrm(p["new"]))
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            p["new"] = f'{p["new"]} ({seen[k]})'

    print(f"\n可改名 {len(plan)} 首 | 跳过 {len(skipped)} 首")
    for p in plan:
        print(f'  [{p["conf"]:6s}] {p["title"][:30]:32s} → {p["new"]}')
    if skipped:
        print("\n--- 跳过（保持原样）---")
        for s in skipped[:40]:
            print(f'  {s[1][:40]:42s} {s[5]}')

    rows = [[p["dir"], p["title"], p["new"], p["src"], p["conf"], "待执行"] for p in plan]
    rows += [s for s in skipped]
    write_record(args.record, rows)
    print(f"\n记录表：{args.record}")

    if not args.apply:
        print("\n（预览模式；加 --apply 生效）")
        return 0

    ok = fail = 0
    for p in plan:
        old = p["path"]
        d = os.path.dirname(old)
        new_base = f'{p["artist"]} - {p["new"]}' if p["artist"] else p["new"]
        new = os.path.join(d, new_base + p["ext"])
        try:
            if os.path.exists(new):
                raise RuntimeError("目标已存在")
            os.rename(old, new)
            old_lrc = os.path.splitext(old)[0] + ".lrc"
            if os.path.exists(old_lrc):
                os.rename(old_lrc, os.path.join(d, new_base + ".lrc"))
            try:
                from mutagen import File as MF
                a = MF(new, easy=True)
                if a is not None:
                    a["title"] = p["new"]
                    a.save()
            except Exception:
                pass
            p["status"] = "已执行"
            ok += 1
        except Exception as e:
            p["status"] = f"失败:{e}"
            fail += 1
            print(f'  失败 {p["title"]}: {e}')

    write_record(args.record, [[p["dir"], p["title"], p["new"], p["src"], p["conf"], p["status"]]
                               for p in plan] + skipped)
    print(f"\n完成：成功 {ok} / 失败 {fail}　记录表：{args.record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
