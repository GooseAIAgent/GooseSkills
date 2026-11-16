#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量给音乐库补「歌词 + 封面」，并封装进音频文件标签（同时落盘同名 .lrc）。

写入位置：
  歌词 —— FLAC: LYRICS + UNSYNCEDLYRICS (Vorbis comment)
          MP3 : USLT（UTF-8，lang=chi）
          M4A : ©lyr
          并且始终写出同名 .lrc（UTF-8），供只读伴生文件的播放器/音乐服务器识别
  封面 —— FLAC: PICTURE (type=3 front cover)
          MP3 : APIC (type=3)
          M4A : covr

数据源与回退链（宁缺勿错，任何一步都要「歌名一致 + 歌手有交集 + 时长接近」三重校验）：
  歌词：网易云 /api/song/lyric（含翻译合并） → lrclib.net（免费、不限流）
  封面：同目录 .cover/ 已有图 → 网易云专辑图 → QQ 音乐 photo_new 直链 → iTunes Search

用法：
  python3 scan_tag_state.py /path/to/music                    # 先扫状态
  python3 lyrics_cover_embed.py --lib /path/to/music --workers 4
  python3 lyrics_cover_embed.py --lib /path/to/music --only cover
  python3 lyrics_cover_embed.py --lib /path/to/music --alias alias.json --limit 20

幂等：成功与「确定性无源」的结果写入日志，重跑自动跳过；「失败」类会重试。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DEFAULT_STATE = os.environ.get("MUSIC_LIB_STATE") or os.path.expanduser(
    "~/.cache/music-library-organizer/lib_state.json")
DEFAULT_CFG = os.path.expanduser("~/.config/music-library-organizer/config.json")
AUDIO_EXT = (".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus")

# 这些状态是「已经给出确定结论」，重跑不必再试；其余（失败）会重试
SKIP_ST = {"完成", "无歌词源", "无封面源", "无源", "无ID", "跳过"}
# 平台对纯音乐返回的占位歌词，不能当歌词写进标签
PLACEHOLDER = re.compile(r"纯音乐|没有填词的|此歌曲为|instrumental|请欣赏", re.I)


# ---------------------------------------------------------------- 基础工具

def norm(s):
    """标题/歌手归一化：NFC + 小写 + 去掉空格标点，用于跨平台名比对。"""
    s = unicodedata.normalize("NFC", s or "").lower()
    return re.sub(r"[\s\-_·、,，.。!！?？\"'“”‘’()（）\[\]【】《》&/\\+~:：]+", "", s)


def artist_hit(cand_artists, artist):
    """歌手有交集即算命中（支持「A、B」多歌手、feat. 等写法）。"""
    if not artist:
        return False
    parts = [p.strip() for p in re.split(r"[、,，&/;；]|feat\.?", artist) if p.strip()]
    for cand in cand_artists or []:
        c = norm(cand)
        if not c:
            continue
        for p in parts:
            n = norm(p)
            if n and (n in c or c in n):
                return True
    return False


def img_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return ""


def load_config(path):
    """配置优先级：命令行参数 > 环境变量 > 配置文件。全部可不填（匿名也能跑）。"""
    cfg = {}
    if path and os.path.exists(os.path.expanduser(path)):
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    ne = os.environ.get("NETEASE_COOKIE") or cfg.get("netease_cookie") or ""
    qq_ck = os.environ.get("QQ_COOKIE") or cfg.get("qq_cookie") or ""
    qq_uin = str(os.environ.get("QQ_UIN") or cfg.get("qq_uin") or "").strip()
    qq_keyst = os.environ.get("QQ_KEYST") or cfg.get("qq_keyst") or ""
    if qq_keyst:
        if qq_uin and not re.search(r"(?:^|;\s*)uin=", qq_ck):
            qq_ck = f"uin={qq_uin if qq_uin.startswith('o') else 'o' + qq_uin}; " + qq_ck
        if not re.search(r"(?:^|;\s*)qm_keyst=", qq_ck):
            qq_ck = f"qm_keyst={qq_keyst}; " + qq_ck
    cfg["netease_cookie"] = ne
    cfg["qq_cookie"] = qq_ck
    return cfg


# ------------------------------------------------------------ 网易云客户端
# 网易云对匿名高频请求会返回 405（IP 级限流，可能持续数小时）。
# 对策：限速 + 连续 3 次 405 即熔断 5 分钟，期间直接把封面任务让给 QQ/iTunes。

class Netease:
    def __init__(self, cookie=""):
        self.cookie = cookie
        self._lock = threading.Lock()
        self._streak = 0
        self._until = 0.0
        self._last = 0.0

    def _guard(self):
        if time.time() < self._until:
            raise RuntimeError("netease 熔断中(405 限流)")

    def _hit(self, code):
        with self._lock:
            if code == 405:
                self._streak += 1
                if self._streak >= 3:
                    self._until = time.time() + 300
                    self._streak = 0
            else:
                self._streak = 0

    def throttle(self, gap=0.6):
        with self._lock:
            wait = self._last + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def get(self, url, params, tries=3, timeout=20):
        qs = urllib.parse.urlencode(params)
        last = None
        for i in range(tries):
            self.throttle()
            try:
                req = urllib.request.Request(url + "?" + qs, headers={
                    "User-Agent": UA, "Referer": "https://music.163.com/",
                    "Cookie": self.cookie})
                data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
                self._hit(data.get("code") or 200)
                return data
            except Exception as e:
                last = e
                time.sleep(1.5 * (i + 1))
        self._hit(405)
        raise last if last else RuntimeError("netease request failed")

    def lyric(self, sid):
        """返回 (原文 lrc, 翻译 lrc)；纯音乐占位文本会被过滤成空串。"""
        self._guard()
        d = self.get("https://music.163.com/api/song/lyric",
                     {"id": sid, "lv": 1, "kv": 1, "tv": -1})
        lrc = ((d.get("lrc") or {}).get("lyric") or "").strip()
        trans = ((d.get("tlyric") or {}).get("lyric") or "").strip()
        if not lrc or PLACEHOLDER.search(lrc) and len(lrc) < 120:
            return "", ""
        return lrc, trans

    def cover_url(self, sid):
        """返回专辑封面原始地址（未加尺寸参数）。"""
        self._guard()
        d = self.get("https://music.163.com/api/song/detail",
                     {"id": sid, "ids": json.dumps([int(sid)])})
        if d.get("code") not in (200, None):
            raise RuntimeError(f"detail code={d.get('code')}")
        songs = d.get("songs") or []
        if not songs:
            return ""
        return (songs[0].get("album") or {}).get("picUrl") or ""

    def search_sid(self, query, title, artist, dur, dur_tol=2.0):
        """严格搜索：歌名归一后必须全等 + 歌手有交集 + 时长差 ≤ dur_tol。"""
        try:
            d = self.get("https://music.163.com/api/search/pc",
                         {"s": query, "type": 1, "limit": 10}, tries=2)
        except Exception:
            return ""
        best, best_diff = "", 9e9
        for s in (d.get("result") or {}).get("songs") or []:
            if norm(s.get("name") or "") != norm(title):
                continue
            if not artist_hit([a.get("name", "") for a in s.get("artists") or []], artist):
                continue
            cand_dur = (s.get("duration") or 0) / 1000.0
            if not dur or not cand_dur or abs(cand_dur - dur) > dur_tol:
                continue
            diff = abs(cand_dur - dur)
            if diff < best_diff:
                best, best_diff = str(s["id"]), diff
        return best


def fetch(url, headers=None, tries=3, timeout=60):
    last = None
    hd = {"User-Agent": UA}
    hd.update(headers or {})
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=hd),
                                          timeout=timeout).read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


# --------------------------------------------------------------- 备用数据源

def lrclib_lyric(title, artist, dur, dur_tol=3.0, tries=2):
    """lrclib.net：免费开放歌词库，返回同步 LRC；不受网易限流影响。"""
    if not title:
        return ""
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    if dur:
        params["duration"] = str(int(dur))
    qs = urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            raw = fetch("https://lrclib.net/api/search?" + qs,
                        {"User-Agent": "music-library-organizer/1.0"}, timeout=20)
            arr = json.loads(raw)
        except Exception:
            time.sleep(1.5 * (i + 1))
            continue
        best, best_diff = "", 9e9
        for it in arr or []:
            synced = (it.get("syncedLyrics") or "").strip()
            if not synced or PLACEHOLDER.search(synced) and len(synced) < 120:
                continue
            if norm(it.get("trackName") or "") != norm(title):
                continue
            if artist and not artist_hit([it.get("artistName") or ""], artist):
                continue
            diff = abs((it.get("duration") or 0) - (dur or 0)) if dur else 0
            if dur and diff > dur_tol:
                continue
            if diff < best_diff:
                best, best_diff = synced, diff
        return best
    return ""


def itunes_cover(title, artist, size=600, tries=2):
    """iTunes Search API：覆盖面广、几乎不限流，是封面最稳的兜底。"""
    q = " ".join(x for x in (title, artist) if x)
    if not q:
        return None, ""
    qs = urllib.parse.urlencode({"term": q, "entity": "song", "limit": 5})
    for i in range(tries):
        try:
            d = json.loads(fetch("https://itunes.apple.com/search?" + qs, timeout=20))
        except Exception:
            time.sleep(1.5 * (i + 1))
            continue
        results = d.get("results") or []
        results.sort(key=lambda r: 0 if artist and artist_hit([r.get("artistName") or ""], artist) else 1)
        for r in results:
            art = r.get("artworkUrl100") or ""
            if not art:
                continue
            if not artist_hit([r.get("artistName") or ""], artist) \
                    and norm(r.get("trackName") or "") != norm(title):
                continue
            url = art.replace("100x100bb", f"{size}x{size}bb")
            try:
                img = fetch(url, timeout=30)
            except Exception:
                continue
            mime = img_mime(img)
            if mime:
                return img, mime
        return None, ""
    return None, ""


def qq_cover(title, artist, cookie=""):
    """QQ 音乐封面：搜索拿专辑 mid，再走 photo_new 直链（独立服务器，不受网易限流）。"""
    q = " ".join(x for x in (title, artist) if x)
    if not q:
        return None, ""
    payload = {"req": {"method": "DoSearchForQQMusicDesktop",
                       "module": "music.search.SearchCgiService",
                       "param": {"search_type": 0, "query": q, "page_num": 1, "num_per_page": 5}}}
    headers = {"Content-Type": "application/json", "Referer": "https://y.qq.com/"}
    if cookie:
        headers["Cookie"] = cookie
    for attempt in range(2):
        try:
            req = urllib.request.Request("https://u.y.qq.com/cgi-bin/musicu.fcg",
                                         data=json.dumps(payload).encode(), headers=headers)
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception:
            time.sleep(1.5 * (attempt + 1))
            continue
        songs = (((d.get("req") or {}).get("data") or {}).get("body") or {}) \
            .get("song", {}).get("list") or []
        songs.sort(key=lambda s: 0 if artist_hit([a.get("name", "") for a in s.get("singer") or []],
                                                 artist) else 1)
        for s in songs:
            album_mid = (s.get("album") or {}).get("mid") or ""
            if not album_mid:
                continue
            hit = artist_hit([a.get("name", "") for a in s.get("singer") or []], artist)
            if not hit and norm(s.get("name") or "") != norm(title):
                continue
            url = f"https://y.qq.com/music/photo_new/T002R500x500M000{album_mid}.jpg"
            try:
                img = fetch(url, {"Referer": "https://y.qq.com/"}, timeout=30)
            except Exception:
                continue
            mime = img_mime(img)
            if mime:
                return img, mime
        return None, ""
    return None, ""


# ------------------------------------------------------------------ 写标签

def embed(path, lrc="", img=None, mime=""):
    """把歌词/封面写进音频标签。歌词为空串、封面为 None 表示该项不动。"""
    ext = path.lower().rsplit(".", 1)[-1]

    if ext == "flac":
        from mutagen.flac import FLAC, Picture
        fl = FLAC(path)
        if lrc:
            fl["LYRICS"] = lrc
            fl["UNSYNCEDLYRICS"] = lrc      # 兼容只认这个字段的播放器
        if img and mime:
            pic = Picture()
            pic.type = 3                     # 3 = front cover
            pic.mime = mime
            pic.desc = ""
            pic.data = img
            fl.clear_pictures()
            fl.add_picture(pic)
        fl.save()

    elif ext == "mp3":
        from mutagen.id3 import ID3, ID3NoHeaderError, USLT, APIC
        from mutagen.mp3 import MP3
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            MP3(path).add_tags()
            tags = ID3(path)
        if lrc:
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="chi", desc="", text=lrc))
        if img and mime:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="", data=img))
        tags.save(path)

    elif ext in ("m4a", "mp4"):
        from mutagen.mp4 import MP4, MP4Cover
        mp4 = MP4(path)
        if lrc:
            mp4["\xa9lyr"] = [lrc]
        if img and mime:
            fmt = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
            mp4["covr"] = [MP4Cover(img, imageformat=fmt)]
        mp4.save()

    else:
        raise RuntimeError(f"不支持的格式：{ext}")


def merge_translation(lrc, trans):
    """把翻译按时间轴插入原文下一行（同时间戳），播放器会同时显示中/外文。"""
    tmap = {}
    for line in trans.splitlines():
        m = re.match(r"(\[\d+:\d+(?:[.:]\d+)?\])(.*)", line)
        if m and m.group(2).strip():
            tmap[m.group(1)] = m.group(2).strip()
    if not tmap:
        return lrc
    out = []
    for line in lrc.splitlines():
        out.append(line)
        m = re.match(r"(\[\d+:\d+(?:[.:]\d+)?\])", line)
        if m and m.group(1) in tmap:
            out.append(m.group(1) + tmap[m.group(1)])
    return "\n".join(out)


def write_lrc(path, lrc):
    lrc_path = os.path.splitext(path)[0] + ".lrc"
    with open(lrc_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(lrc)
    return lrc_path


# ------------------------------------------------------------------ 主流程

def load_log(path):
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                done[rec.get("p", "")] = rec.get("st", "")
    return done


def load_alias(path):
    """别名表：本地标签名对不上平台名时（如中文译名 vs 韩文原名）显式指定检索词。

    alias.json 支持两种写法：
      { "IU - 唠叨": "잔소리",                          // 值 = 平台上的真实歌名
        "IU - 好日子": {"q": "좋은 날 IU", "title": "좋은 날"} }
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for k, v in (raw or {}).items():
        if isinstance(v, str):
            out[norm(k)] = {"q": f"{v}", "title": v}
        elif isinstance(v, dict):
            out[norm(k)] = {"q": v.get("q") or f'{v.get("title", "")}', "title": v.get("title") or ""}
    return out


def main():
    ap = argparse.ArgumentParser(description="批量补歌词+封面并写入音频标签")
    ap.add_argument("--lib", default=".", help="音乐库根目录")
    ap.add_argument("--state", default=DEFAULT_STATE, help="scan_tag_state.py 生成的状态文件")
    ap.add_argument("--log", default=os.environ.get("MUSIC_LIB_LOG") or os.path.expanduser(
        "~/.cache/music-library-organizer/embed_log.jsonl"), help="处理日志（断点续跑用）")
    ap.add_argument("--config", default=DEFAULT_CFG, help="配置 JSON（netease_cookie / qq_cookie 等）")
    ap.add_argument("--alias", default="", help="别名表 JSON（本地名 → 平台真实名/检索词）")
    ap.add_argument("--only", choices=["both", "lyrics", "cover"], default="both")
    ap.add_argument("--workers", type=int, default=4, help="并发数，建议 3–6（过高易触发限流）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少首（0=全部）")
    ap.add_argument("--min-interval", type=float, default=0.6, help="网易云请求最小间隔秒数")
    ap.add_argument("--dur-tol", type=float, default=2.0, help="匹配允许的时长差（秒）")
    ap.add_argument("--cover-size", type=int, default=1000, help="优先请求的封面边长（像素）")
    ap.add_argument("--cover-max-kb", type=int, default=1500,
                    help="封面体积上限(KB)，超了自动换更小尺寸或换源，避免给每首歌塞几 MB 图")
    ap.add_argument("--no-sidecar", action="store_true", help="不额外写同名 .lrc 文件")
    ap.add_argument("--dry-run", action="store_true", help="只统计待处理数量，不联网不写入")
    args = ap.parse_args()

    state_path = os.path.abspath(os.path.expanduser(args.state))
    if not os.path.exists(state_path):
        sys.exit(f"找不到状态文件 {state_path}\n请先运行：python3 scan_tag_state.py {args.lib}")
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    items = [x for x in state.get("items", []) if x.get("p")]
    if not items:
        sys.exit("状态文件为空，请先运行 scan_tag_state.py")

    need_ly = args.only in ("lyrics", "both")
    need_cv = args.only in ("cover", "both")
    todo = [x for x in items
            if (need_ly and not x.get("ly")) or (need_cv and not x.get("cv"))]
    done_log = load_log(os.path.abspath(os.path.expanduser(args.log)))
    todo = [x for x in todo if done_log.get(x["p"]) not in SKIP_ST]

    miss_ly = sum(1 for x in todo if need_ly and not x.get("ly"))
    miss_cv = sum(1 for x in todo if need_cv and not x.get("cv"))
    print(f"库内 {len(items)} 首 | 待处理 {len(todo)} 首（缺歌词 {miss_ly} / 缺封面 {miss_cv}）", flush=True)
    if args.dry_run:
        for x in todo[:20]:
            print(f"  - {x.get('a') or '?'} - {x.get('t') or os.path.basename(x['p'])}")
        if len(todo) > 20:
            print(f"  … 其余 {len(todo) - 20} 首")
        return

    cfg = load_config(args.config)
    ne = Netease(cfg.get("netease_cookie", ""))
    qq_cookie = cfg.get("qq_cookie", "")
    alias = load_alias(args.alias)
    os.makedirs(os.path.dirname(os.path.abspath(os.path.expanduser(args.log))) or ".", exist_ok=True)

    lock = threading.Lock()
    stats = {}

    def handle(item):
        path = item["p"]
        title = item.get("t") or ""
        artist = item.get("a") or ""
        dur = item.get("dur") or 0
        rec = {"p": path, "t": title, "a": artist}

        # 标题/艺术家缺失时退化为文件名「歌手 - 歌名」
        if not title:
            stem = os.path.splitext(os.path.basename(path))[0]
            if " - " in stem:
                artist = artist or stem.split(" - ", 1)[0]
                title = stem.split(" - ", 1)[1]
            else:
                title = stem

        if not dur:
            try:
                from mutagen import File as MF
                f = MF(path)
                dur = round(f.info.length) if f and f.info else 0
            except Exception:
                dur = 0

        alias_key = norm(f"{artist} - {title}")
        al = alias.get(alias_key) or alias.get(norm(os.path.splitext(os.path.basename(path))[0]))
        query_title = (al or {}).get("title") or title
        query = (al or {}).get("q") or " ".join(x for x in (query_title, artist) if x)

        got_ly = bool(item.get("ly"))
        got_cv = bool(item.get("cv"))
        try:
            sid = ""
            need_sid = (need_ly and not got_ly) or (need_cv and not got_cv)
            if need_sid:
                try:
                    sid = ne.search_sid(query, query_title, artist, dur, args.dur_tol)
                except Exception:
                    sid = ""
                if not sid:
                    rec["no_sid"] = True     # 无网易 ID 不阻断：歌词走 lrclib、封面走 QQ/iTunes

            if need_ly and not got_ly:
                lrc, src = "", ""
                if sid:
                    try:
                        lrc, trans = ne.lyric(sid)
                    except Exception:
                        lrc, trans = "", ""
                    if lrc:
                        src = "netease"
                        if trans:
                            lrc = merge_translation(lrc, trans)
                if not lrc:
                    lrc = lrclib_lyric(query_title, artist, dur, max(args.dur_tol, 1.0) + 1)
                    src = "lrclib" if lrc else ""
                if lrc:
                    if not args.no_sidecar:
                        write_lrc(path, lrc)
                    embed(path, lrc=lrc)
                    got_ly = True
                    rec.update(lrc=True, lrc_src=src)

            if need_cv and not got_cv:
                img, mime, src = None, "", ""
                cover_dir = os.path.join(os.path.dirname(path), ".cover")
                stem = os.path.basename(path)
                for cand in (stem + ".jpeg", stem + ".jpg", stem + ".png"):
                    fp = os.path.join(cover_dir, cand)
                    if os.path.exists(fp):
                        data = open(fp, "rb").read()
                        mime2 = img_mime(data)
                        if mime2:
                            img, mime, src = data, mime2, "sidecar"
                            break
                if not img and sid:
                    try:
                        ne.throttle(args.min_interval)
                        base = ne.cover_url(sid)
                        # 专辑图可能是超大 PNG；超过体积上限就换小尺寸或换源
                        for size in (args.cover_size, 600, 300):
                            if not base:
                                break
                            data = fetch(f"{base}?param={size}y{size}",
                                         {"Referer": "https://music.163.com/", "Cookie": ne.cookie},
                                         timeout=60)
                            if not img_mime(data):
                                continue
                            img, mime, src = data, img_mime(data), "netease"
                            if len(data) <= args.cover_max_kb * 1024:
                                break
                        if img and len(img) > args.cover_max_kb * 1024:
                            img, mime, src = None, "", ""   # 实在太大，交给 QQ/iTunes
                    except Exception:
                        img, mime, src = None, "", ""
                if not img:
                    img, mime = qq_cover(title, artist, qq_cookie)
                    src = "qq" if img else ""
                if not img:
                    img, mime = itunes_cover(title, artist)
                    src = "itunes" if img else ""
                if img and mime:
                    embed(path, img=img, mime=mime)
                    got_cv = True
                    rec.update(cover=True, cover_src=src)

            unmet_ly = need_ly and not got_ly
            unmet_cv = need_cv and not got_cv
            if not unmet_ly and not unmet_cv:
                rec["st"] = "完成"
            elif unmet_ly and unmet_cv:
                rec["st"] = "无源"
            elif unmet_ly:
                rec["st"] = "无歌词源"
            else:
                rec["st"] = "无封面源"
            rec.update(ly=got_ly, cv=got_cv)
            return rec
        except Exception as e:
            rec.update(st="失败", reason=str(e)[:80], ly=got_ly, cv=got_cv)
            return rec

    t0 = time.time()
    # 操作台账：成功项记入统一 ledger，之后其他工具/重跑可直接跳过这些文件
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import library_ledger
        ledger = library_ledger.load()
        ledger_dirty = False
    except Exception:
        ledger = None
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(handle, x): x for x in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            item = futures[fut]
            try:
                rec = fut.result()
            except Exception as e:
                rec = {"p": item["p"], "t": item.get("t"), "a": item.get("a"),
                       "st": "失败", "reason": str(e)[:80]}
            stats[rec.get("st", "失败")] = stats.get(rec.get("st", "失败"), 0) + 1
            with lock:
                with open(os.path.abspath(os.path.expanduser(args.log)), "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                item["ly"] = bool(rec.get("ly", item.get("ly")))
                item["cv"] = bool(rec.get("cv", item.get("cv")))
                if ledger is not None:
                    try:
                        if rec.get("lrc"):
                            library_ledger.record(ledger, "lyrics", rec["p"],
                                                  detail=str(rec.get("lrc_src") or ""))
                            ledger_dirty = True
                        if rec.get("cover"):
                            library_ledger.record(ledger, "cover", rec["p"],
                                                  detail=str(rec.get("cover_src") or ""))
                            ledger_dirty = True
                    except Exception:
                        pass
            if i % 25 == 0 or i == len(todo):
                elapsed = time.time() - t0
                eta = elapsed / i * (len(todo) - i) / 60 if i else 0
                print(f"[{i}/{len(todo)}] {stats} 已用 {elapsed / 60:.0f}min 剩余约 {eta:.0f}min", flush=True)

    # 回写状态，避免重跑时重复打开这些文件
    try:
        state["items"] = items
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass
    if ledger is not None and ledger_dirty:
        try:
            library_ledger.save(ledger)
            print(f"操作台账：{library_ledger.DEFAULT_LEDGER}")
        except Exception:
            pass

    print(f"\n完成：{stats}")
    print(f"日志：{os.path.abspath(os.path.expanduser(args.log))}（断点续跑用，重跑自动跳过已完成项）")


if __name__ == "__main__":
    sys.exit(main())
