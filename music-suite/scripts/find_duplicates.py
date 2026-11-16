#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音乐库查重：找出重复文件并按「危险程度」分级列出。

四层判定（从强到弱）：
  L1 字节相同   —— 文件 SHA-256 一致（内容完全一样，可安全删其一）
  L2 音频流相同 —— FLAC 比 STREAMINFO 的 PCM MD5；MP3 等用 ffmpeg 解出音频流再算 MD5
                  （同一份音频，只是标签/封面不同：删前需确认保留哪份标签）
  L3 同名不同版 —— 归一化「歌手+歌名」相同、时长差 ≤2s，但音频不同（多为不同码率/格式/来源）
  L4 疑似重复   —— 同上但时长差 2–8s（可能是不同版本，需人工判断）

只读扫描，不移动、不删除任何文件。

用法：
  python3 find_duplicates.py /path/to/music
  python3 find_duplicates.py /path/to/music --report dup.csv --min-level 2
  python3 find_duplicates.py /path/to/music --no-ffmpeg    # 跳过 L2 的 MP3 音频流比对（更快）
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import defaultdict

AUDIO_EXT = (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".ape", ".wv", ".aac", ".wma")
HAN = re.compile(r"[\u4e00-\u9fff]")
NATIVE = re.compile(r"[\u1100-\u11FF\uA960-\uA97F\uAC00-\uD7A3\uD7B0-\uD7FF\u3040-\u309F\u30A0-\u30FF]")


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def nrm(s):
    """归一化：去标点/空白/大小写，但**保留版本后缀**。

    不能把 `(Live)`、`(Inst.)`、`(Tomorrow Version)` 剥掉——录音室版与现场版时长
    经常只差 1–2 秒，剥掉括号会把「不同版本」误判成「重复文件」。
    """
    return re.sub(r"[\s\-_·、,，.。!！?？\"'“”‘’()（）\[\]【】《》&/\\+~]+", "", nfc(s).lower())


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def audio_md5(path):
    """用 ffmpeg 解出音频流算 MD5（跳过标签），失败返回空串。"""
    try:
        out = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path, "-map", "0:a",
                              "-f", "md5", "-"], capture_output=True, timeout=180)
        txt = out.stdout.decode(errors="ignore").strip()
        return txt.split("=")[-1] if txt.startswith("MD5=") else ""
    except Exception:
        return ""


_FFMPEG = None


def ffmpeg_bin():
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = shutil.which("ffmpeg") or ""
    return _FFMPEG


def probe(path):
    """读标签/时长/FLAC PCM MD5，不读整个文件。"""
    from mutagen import File as MF
    info = {"dur": 0, "bitrate": 0, "artist": "", "title": "", "pcm": "", "size": os.path.getsize(path)}
    try:
        a = MF(path)
    except Exception:
        return info
    if a is None:
        return info
    if getattr(a, "info", None):
        info["dur"] = int(getattr(a.info, "length", 0) or 0)
        info["bitrate"] = int(getattr(a.info, "bitrate", 0) or 0)
    if path.lower().endswith(".flac"):
        sig = getattr(getattr(a, "info", None), "md5_signature", None)
        if isinstance(sig, int):
            info["pcm"] = f"{sig:032x}"
        elif isinstance(sig, bytes) and sig:
            info["pcm"] = sig.hex()
    try:
        e = MF(path, easy=True)
        info["artist"] = nfc((e.get("artist") or [""])[0]) if e else ""
        info["title"] = nfc((e.get("title") or [""])[0]) if e else ""
    except Exception:
        pass
    return info


def parse_name(path):
    stem = nfc(os.path.splitext(os.path.basename(path))[0])
    return (stem.split(" - ", 1)[0].strip(), stem.split(" - ", 1)[1].strip()) if " - " in stem else ("", stem)


def main():
    ap = argparse.ArgumentParser(description="音乐库查重（只读）")
    ap.add_argument("root")
    ap.add_argument("--report", default="duplicates.csv", help="CSV 报告路径")
    ap.add_argument("--min-level", type=int, default=4, choices=(1, 2, 3, 4),
                    help="输出到第几层（1=仅字节相同 2=含音频流相同 3=含同名同长 4=含疑似，默认 4）")
    ap.add_argument("--no-ffmpeg", action="store_true", help="跳过非 FLAC 的音频流比对")
    ap.add_argument("--json", help="额外导出 JSON 明细")
    ap.add_argument("--cache", help="元数据缓存 JSON（默认报告路径 + .meta.json，可加速重跑）")
    args = ap.parse_args()

    files = []
    for dp, ds, fs in os.walk(args.root):
        ds[:] = [d for d in ds if d not in (".cover", ".git", "__pycache__")]
        for f in fs:
            if f.lower().endswith(AUDIO_EXT):
                files.append(os.path.join(dp, f))
    files.sort()
    print(f"扫描 {len(files)} 个音频文件…")

    cache_path = args.cache or (args.report + ".meta.json")
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    meta = {}
    for i, p in enumerate(files, 1):
        try:
            st = os.stat(p)
        except OSError:
            continue
        c = cache.get(p)
        if c and c.get("size") == st.st_size and abs(c.get("mtime", 0) - st.st_mtime) < 1:
            meta[p] = c
        else:
            meta[p] = probe(p)
            meta[p]["mtime"] = st.st_mtime
        if i % 500 == 0:
            print(f"  读取元数据 {i}/{len(files)}")
    files = [p for p in files if p in meta]
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:
        pass

    groups = []   # (level, key_desc, [paths])

    # ---- L1 字节相同：只在同尺寸组内算 SHA-256，省 IO
    by_size = defaultdict(list)
    for p in files:
        by_size[meta[p]["size"] or -1].append(p)
    l1_seen = set()
    for size, ps in by_size.items():
        if size <= 0 or len(ps) < 2:
            continue
        by_hash = defaultdict(list)
        for p in ps:
            by_hash[sha256(p)].append(p)
        for h, ps2 in by_hash.items():
            if len(ps2) > 1:
                groups.append((1, f"SHA256 {h[:12]}", sorted(ps2)))
                l1_seen.update(ps2)

    # ---- L2 音频流相同（排除已在 L1 的）
    if args.min_level >= 2:
        by_stream = defaultdict(list)
        for p in files:
            m = meta[p]
            if p.lower().endswith(".flac"):
                key = m["pcm"]
            elif args.no_ffmpeg or not ffmpeg_bin():
                key = ""
            else:
                key = m.get("stream") or audio_md5(p)
                m["stream"] = key
            if key:
                by_stream[(os.path.splitext(p)[1].lower(), key)].append(p)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
        except Exception:
            pass
        for (ext, key), ps in by_stream.items():
            if len(ps) > 1 and not set(ps) <= l1_seen:
                groups.append((2, f"{ext} PCMMD5 {key[:12]}", sorted(ps)))

    # ---- L3/L4 同名不同版（跳过已被 L1/L2 完整覆盖的文件组合，避免同一对文件报两次）
    covered = [set(g[2]) for g in groups]

    def already(ps):
        s = set(ps)
        return any(s <= c for c in covered)

    if args.min_level >= 3:
        by_name = defaultdict(list)
        for p in files:
            m = meta[p]
            artist, title = parse_name(p)
            artist = m["artist"] or artist
            title = m["title"] or title
            if not title:
                continue
            key = (nrm(artist), nrm(title))
            if key != ("", ""):
                by_name[key].append(p)
        for (a, t), ps in by_name.items():
            if len(ps) < 2 or already(ps):
                continue
            # 按格式/码率区分，不同版本可能本来就该共存
            durs = [meta[p]["dur"] for p in ps if meta[p]["dur"]]
            if not durs:
                continue
            spread = max(durs) - min(durs)
            if spread <= 2:
                groups.append((3, f"同名同长 {a or '?'} - {t or '?'}", sorted(ps)))
            elif spread <= 8:
                groups.append((4, f"同名近长(差{spread}s) {a or '?'} - {t or '?'}", sorted(ps)))

    groups.sort(key=lambda g: (g[0], -sum(meta[p]["size"] for p in g[2])))

    # ---- 输出
    rows = []
    total_waste = 0
    for i, (lvl, desc, ps) in enumerate(groups, 1):
        sizes = [meta[p]["size"] for p in ps]
        waste = sum(sizes) - max(sizes)
        total_waste += waste
        rows.append([i, f"L{lvl}", desc, len(ps), human(max(sizes)), human(waste)] +
                    [os.path.relpath(p, args.root) for p in ps])
    width = max([len(r) for r in rows], default=6)
    with open(args.report, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["组号", "级别", "判定依据", "文件数", "单份大小", "可回收"] +
                   [f"文件{i}" for i in range(1, width - 5)])
        for r in rows:
            w.writerow(r + [""] * (width - len(r)))

    lv = defaultdict(int)
    for g in groups:
        lv[g[0]] += 1
    print(f"\n重复组：L1(字节相同) {lv[1]} | L2(音频流相同) {lv[2]} | "
          f"L3(同名同长) {lv[3]} | L4(同名近长) {lv[4]}")
    print(f"可回收空间（按每组保留一份估算）：{human(total_waste)}")
    for lvl, desc, ps in groups[:40]:
        print(f"\n[L{lvl}] {desc}　{len(ps)} 个")
        for p in ps:
            print(f"    {human(meta[p]['size']):>8s}  {meta[p]['dur']:>4d}s  "
                  f"{os.path.relpath(p, args.root)}")
    if len(groups) > 40:
        print(f"\n… 其余 {len(groups) - 40} 组见报告 {args.report}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([{"level": l, "reason": d,
                        "items": [{"path": os.path.relpath(p, args.root),
                                   "size": meta[p]["size"], "dur": meta[p]["dur"]} for p in ps]}
                       for l, d, ps in groups], f, ensure_ascii=False, indent=1)
    print(f"\n报告：{args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
