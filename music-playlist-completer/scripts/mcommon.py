#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享工具模块：配置加载、文件名清洗、文件头嗅探、标签写入、时长读取。

配置查找顺序（后者覆盖前者不存在的情况）：
  1. 命令行 --config 指定
  2. 环境变量 MUSIC_DL_CONFIG
  3. ~/.config/music-downloader/config.json

输出目录查找顺序：
  1. 命令行 --out 指定
  2. 环境变量 MUSIC_DL_OUT
  3. 当前工作目录下 ./music-downloads

配置文件格式（config.json，切勿提交到 git）：
  {
    "netease_cookie": "MUSIC_U=xxxx; ...",   # 网易云 VIP cookie（可选，匿名仅 128k）
    "qq_uin": "123456789",                   # QQ 号（可选）
    "qq_keyst": "Q_H_L_xxx",                 # QQ 音乐登录票据（可选）
    "qq_cookie": ""                          # 或完整 QQ cookie，二选一
  }
"""
import json, os, re, unicodedata

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 网易云音质阶梯：无损优先，之后按码率从高到低
NE_LADDER = [("lossless", "flac"), ("exhigh", "mp3"), ("higher", "mp3"), ("standard", "mp3")]
# QQ 音质阶梯：F000=FLAC, M800=320k, C400=128k
QQ_LADDER = [("F000", "flac", 1000), ("M800", "mp3", 320), ("C400", "mp3", 128)]
# 网易云备用 CDN 节点（签名与主机无关，可互换；部分节点会 403，需轮换）
NE_HOSTS = ["m801", "m802", "m803", "m701", "m800", "m700", "m702", "m703"]

DEFAULT_CONFIG = "~/.config/music-downloader/config.json"
DEFAULT_OUT = "music-downloads"


def resolve_config(cli_path=None):
    if cli_path:
        return os.path.expanduser(cli_path)
    env = os.environ.get("MUSIC_DL_CONFIG")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser(DEFAULT_CONFIG)


def load_config(cli_path=None):
    p = resolve_config(cli_path)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f), p
    except FileNotFoundError:
        return {}, p


def resolve_out(cli_out=None):
    if cli_out:
        return os.path.abspath(os.path.expanduser(cli_out))
    env = os.environ.get("MUSIC_DL_OUT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.getcwd(), DEFAULT_OUT)


def safe(name, maxlen=110):
    """清洗为跨平台安全的文件/目录名。"""
    s = unicodedata.normalize("NFC", name or "").strip()
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:maxlen] or "未命名"


def norm(s):
    """标准化字符串用于比较：去空白/标点/括号注释，转小写。"""
    s = unicodedata.normalize("NFC", s or "").lower()
    return re.sub(r"[\s\-_·、,，.。!！?？\"'“”‘’()（）\[\]【】《》&/\\+~]+", "", s)


def magic_ext(head):
    """按文件头嗅探真实音频格式，防止把 HTML 错误页当音频保存。"""
    b = head[:16]
    if b[:4] == b"fLaC": return "flac"
    if b[:3] == b"ID3": return "mp3"
    if b[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"): return "mp3"
    if b[:4] == b"OggS": return "ogg"
    if b[4:8] == b"ftyp": return "m4a"
    return ""


def existing(base, dirpath):
    """目录中是否已有同名（任意音频扩展名）文件，返回其路径。"""
    for ext in (".flac", ".mp3", ".m4a", ".ogg"):
        p = os.path.join(dirpath, base + ext)
        if os.path.exists(p):
            return p
    return ""


def tag(path, title, artist, album):
    """写入标签并返回实际时长（秒）。mutagen 缺失时静默降级，只返回 0。"""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".flac":
            from mutagen.flac import FLAC
            a = FLAC(path); a["title"] = title; a["artist"] = artist
            if album: a["album"] = album
            a.save(); return round(a.info.length, 2)
        if ext == ".mp3":
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, ID3NoHeaderError
            from mutagen.mp3 import MP3
            try:
                t = ID3(path)
            except ID3NoHeaderError:
                MP3(path).add_tags(); t = ID3(path)
            t.delall("TIT2"); t.delall("TPE1"); t.delall("TALB")
            t.add(TIT2(encoding=3, text=title)); t.add(TPE1(encoding=3, text=artist))
            if album: t.add(TALB(encoding=3, text=album))
            t.save(path); return round(MP3(path).info.length, 2)
        from mutagen import File as MF
        f = MF(path)
        return round(f.info.length, 2) if f and f.info else 0
    except Exception:
        return 0


def file_len(path):
    try:
        from mutagen import File as MF
        f = MF(path)
        return round(f.info.length, 2) if f and f.info else 0
    except Exception:
        return 0


def download(url, tmp, headers, min_ratio=0.98):
    """流式下载到 tmp，校验 Content-Length 完整性与文件头，返回 (head_bytes, size)。"""
    import urllib.request
    r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90)
    total = int(r.headers.get("Content-Length") or 0)
    got, head = 0, b""
    with open(tmp, "wb") as f:
        while True:
            b = r.read(262144)
            if not b:
                break
            if not head:
                head = b[:16]
            f.write(b)
            got += len(b)
    r.close()
    if total and got < total * min_ratio:
        raise IOError(f"不完整 {got}/{total}")
    if not magic_ext(head):
        raise IOError("非音频内容")
    return head, got


def add_common_args(ap):
    """为各脚本注册统一的 --config / --out 参数。"""
    ap.add_argument("--config", default=None,
                    help=f"配置文件路径（默认 env MUSIC_DL_CONFIG 或 {DEFAULT_CONFIG}）")
    ap.add_argument("--out", default=None,
                    help=f"输出根目录（默认 env MUSIC_DL_OUT 或 ./music-downloads）")
    return ap
