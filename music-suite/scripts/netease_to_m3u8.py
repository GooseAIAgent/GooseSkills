#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网易云音乐歌单 → 本地 m3u8 歌单，端到端导出工具。

一条命令完成四件事：
  1. 抓取歌单完整曲目（利用 trackIds 突破详情接口只回 6 首的限制）
  2. 扫描本地音乐库，逐首比对是否已拥有
  3. 导出 CSV 清单（完整清单 + 未在本地库中的歌曲）
  4. 生成两份 m3u8：
     - 完整版：本地已拥有的写绝对路径，**本地没有的路径行留空**
       （不写平台外链——VIP/版权歌外链多数返回网页，放进播放器全是废条目）
     - 本地可播版：只含本地文件，去重后全部可直接播放

用法:
    # 全流程（联网抓取 + 本地比对 + 导出）
    python3 netease_to_m3u8.py "https://music.163.com/playlist?id=331332108" \
        --music-root ~/Music --outdir ./歌单导出

    # 离线模式：基于已有清单 CSV 重新比对本地库并导出（不联网）
    python3 netease_to_m3u8.py --csv 歌单完整清单.csv \
        --music-root ~/Music --outdir ./歌单导出

    # 小样本联网测试（只抓前 N 首，验证 API 可用性）
    python3 netease_to_m3u8.py <url> --limit 5 --outdir /tmp/test

    # 日后补全留空条目（可反复重跑，见 fill_playlist_paths.py）
    python3 fill_playlist_paths.py 歌单.m3u8 --music-root ~/Music

依赖：仅 Python 3.8+ 标准库（无需 cookie，无需 mutagen）。
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

AUDIO_EXT = {'.flac', '.mp3', '.m4a', '.wav', '.ape', '.ogg', '.opus',
             '.dsf', '.dff', '.aac', '.wma', '.aiff', '.aif'}
SKIP_DIR = {'#recycle', '.@#thumbnails', '@eaDir', '.Trash', '重复副本'}
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Referer': 'https://music.163.com/',
}


# ---------------------------------------------------------------- 基础工具
def norm(s):
    """严格归一：NFC + 全半角括号统一 + 去空白 + 小写。"""
    s = unicodedata.normalize('NFC', s)
    s = s.replace('（', '(').replace('）', ')').replace('【', '[').replace('】', ']')
    return re.sub(r'\s+', '', s).lower()


def norm_title(s):
    """宽松归一：再去掉括号说明与标点，仅用于兜底匹配。"""
    s = unicodedata.normalize('NFC', s)
    s = re.sub(r'[\(（\[【].*?[\)）\]】]', '', s)
    return re.sub(r'[\s\-_·、,，.。!！?？"\'“”‘’]', '', s).lower()


def sanitize(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip() or 'playlist'


def get_json(url, retries=4):
    """带限流退避的 GET。网易云限流码 -462/-431 时指数退避重试。"""
    delay = 3
    for i in range(retries):
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
        code = data.get('code')
        if code == 200:
            return data
        if code in (-462, 431, -460):
            print(f'  限流(code={code})，退避 {delay}s 后重试…')
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        sys.exit(f'接口异常: code={code} url={url[:100]}')
    sys.exit('多次限流，请稍后再跑（建议间隔 10 分钟以上）')


# ---------------------------------------------------------------- 歌单抓取
def parse_playlist_id(text):
    m = re.search(r'[?&]id=(\d+)', text) or re.search(r'/playlist/(\d+)', text)
    if not m:
        sys.exit(f'无法从输入中解析歌单 ID: {text!r}')
    return m.group(1)


def fetch_playlist(pid, limit=0):
    print(f'抓取歌单 {pid} …')
    d = get_json(f'https://music.163.com/api/v6/playlist/detail?id={pid}&n=1000')
    pl = d['playlist']
    name = pl.get('name', f'歌单{pid}').strip()
    tids = [t['id'] for t in pl.get('trackIds', [])]
    total = len(tids)
    print(f'  「{name}」共 {total} 首')
    if limit:
        tids = tids[:limit]
        print(f'  --limit={limit}，仅取前 {limit} 首（测试模式）')

    rows, seen = [], set()
    for i in range(0, len(tids), 100):
        batch = tids[i:i + 100]
        sd = get_json('https://music.163.com/api/song/detail/?ids='
                      + urllib.parse.quote(json.dumps(batch)))
        for s in sd.get('songs', []):
            if s['id'] in seen:
                continue
            seen.add(s['id'])
            artists = '、'.join(a['name'] for a in s.get('artists', []))
            rows.append({
                '歌曲名': s.get('name', ''),
                '歌手': artists,
                '专辑': (s.get('album') or {}).get('name', ''),
                '时长(秒)': str(int(s.get('duration', 0) // 1000)),
                '歌曲链接': f"https://music.163.com/song?id={s['id']}",
            })
        print(f'  已获取 {len(rows)}/{len(tids)} 首')
        time.sleep(1.5)          # 礼貌间隔，防限流
    return name, rows


# ---------------------------------------------------------------- 本地比对
def scan_library(root):
    """返回 [(绝对路径, 归一化文件名, 归一化歌名)]"""
    idx = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith('.') and d not in SKIP_DIR]
        for fn in filenames:
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in AUDIO_EXT:
                continue
            title = stem.split(' - ', 1)[1] if ' - ' in stem else stem
            idx.append((os.path.join(dirpath, fn), norm(stem), norm_title(title)))
    return idx


def artist_tokens(s):
    """歌手名归一为排序后的 token 集，容忍「A、B」与「B、A」顺序差异。"""
    return sorted(norm_title(t) for t in re.split(r'[、,/]', s) if t.strip())


def same_artist(a, b):
    """歌手集合兼容：完全相等，或一方是另一方的子集。

    覆盖合唱署名差异：歌单「周杰伦、梁心颐」 vs 文件「周杰伦 - 珊瑚海 (feat. 梁心颐)」；
    但「Sarah McLachlan - Angel」不会误配「周杰伦 - Angel」（无交集）。
    """
    ta, tb = set(artist_tokens(a)), set(artist_tokens(b))
    return bool(ta and tb) and (ta == tb or ta <= tb or tb <= ta)


def match_one(row, idx, used_paths):
    """三层匹配：文件名全等 → 归一化相等 → 歌名宽松且歌手一致且候选唯一。

    严格层不做任何拆括号处理，因此「夜曲」只会精确命中录音室版，
    不会误配「夜曲(Live)」；Live 版需歌单本身标注 Live 才会命中。
    """
    label = f"{row['歌手']} - {row['歌曲名']}".strip(' -')
    key, title = norm(label), norm_title(row['歌曲名'])

    cands = [p for p, k, _ in idx if k == key]
    cands = [p for p in cands if p not in used_paths] or cands
    if cands:
        return cands[0]

    loose = []
    for p, _, t in idx:
        if t != title:
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        fa = stem.split(' - ', 1)[0] if ' - ' in stem else ''
        if fa and same_artist(row['歌手'], fa):
            loose.append(p)
    loose = [p for p in loose if p not in used_paths] or loose
    if len(loose) == 1:
        return loose[0]
    if len(loose) > 1:
        best = max(loose, key=lambda p: SequenceMatcher(None, key, norm(p)).ratio())
        if SequenceMatcher(None, key, norm(best)).ratio() > 0.9:
            return best
    return None


def match_all(rows, music_root):
    idx = scan_library(music_root)
    print(f'本地音频 {len(idx)} 个（根目录: {music_root}）')
    for r in rows:
        r['本地文件'] = ''       # 清空旧值，全量重新评估，避免残留
    used, hits = set(), 0
    for r in rows:
        p = match_one(r, idx, used)
        if p:
            r['本地文件'] = os.path.relpath(p, music_root)
            used.add(p)
            hits += 1
    print(f'本地命中 {hits}/{len(rows)}（未命中保持留空）')
    return rows


# ---------------------------------------------------------------- 导出
CSV_FIELDS = ['序号', '歌曲名', '歌手', '专辑', '时长(秒)', '本地文件', '歌曲链接']


def extinf(r):
    return f"#EXTINF:{r['时长(秒)']},{r['歌手']} - {r['歌曲名']}".strip()


def abs_path(r):
    return os.path.join(os.path.normpath(r['_root']), r['本地文件'])


def write_csvs(outdir, base, rows):
    for i, r in enumerate(rows, 1):
        r['序号'] = str(i)
    with open(os.path.join(outdir, f'{base}-歌单完整清单.csv'), 'w',
              encoding='utf-8-sig', newline='') as f:          # BOM 方便 Excel
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)

    missing = [r for r in rows if not r.get('本地文件')]
    with open(os.path.join(outdir, f'{base}-未在本地库中的歌曲.csv'), 'w',
              encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['序号', '歌曲名', '歌手', '专辑', '歌曲链接'],
                           extrasaction='ignore')
        w.writeheader()
        w.writerows(missing)
    print(f'CSV: 完整清单 {len(rows)} 行，未在本地 {len(missing)} 行')


def write_m3u8(outdir, base, rows):
    # 完整版：本地有 → 绝对路径；没有 → 留空
    lines = ['#EXTM3U',
             f'#PLAYLIST:{base}（完整 {len(rows)} 首）',
             '# 本地已具备的曲目写入绝对路径；本地暂无的曲目路径留空，待下载后补充']
    for r in rows:
        lines.append(extinf(r))
        lines.append(abs_path(r) if r.get('本地文件') else '')
    p1 = os.path.join(outdir, f'{base}-完整{len(rows)}首.m3u8')
    with open(p1, 'w', encoding='utf-8', newline='\n') as f:   # m3u8 无 BOM
        f.write('\n'.join(lines) + '\n')

    # 本地可播版：去重后只含本地文件
    seen, local = set(), []
    for r in rows:
        lf = r.get('本地文件', '')
        if lf and lf not in seen:
            seen.add(lf)
            local.append(r)
    lines = ['#EXTM3U', f'#PLAYLIST:{base}']
    for r in local:
        lines.append(extinf(r))
        lines.append(abs_path(r))
    p2 = os.path.join(outdir, f'{base}-本地可播{len(local)}首.m3u8')
    with open(p2, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'm3u8: {os.path.basename(p1)}（留空 {len(rows) - len([r for r in rows if r.get("本地文件")])} 条），'
          f'{os.path.basename(p2)}（去重后 {len(local)} 条）')


# ---------------------------------------------------------------- 主流程
def load_csv(path):
    rows = []
    with open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            r.setdefault('本地文件', '')
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser(description='网易云歌单 → 本地 m3u8 导出')
    ap.add_argument('playlist', nargs='?', help='歌单链接或纯数字 ID（联网模式）')
    ap.add_argument('--csv', help='已有清单 CSV（离线模式，跳过抓取）')
    ap.add_argument('--name', help='歌单名（离线模式下 CSV 文件名不含歌单名时用它命名输出）')
    ap.add_argument('--music-root', default=os.path.expanduser("~/Music"),
                    help='本地音乐库根目录，默认 ~/Music')
    ap.add_argument('--outdir', default='./歌单导出', help='输出目录')
    ap.add_argument('--limit', type=int, default=0, help='只抓前 N 首（测试用）')
    args = ap.parse_args()

    if not args.playlist and not args.csv:
        ap.error('需要提供歌单链接，或用 --csv 指定已有清单')
    if not os.path.isdir(args.music_root):
        sys.exit(f'本地音乐库目录不存在: {args.music_root}')
    os.makedirs(args.outdir, exist_ok=True)

    t0 = time.time()
    if args.csv:
        rows = load_csv(args.csv)
        print(f'离线模式：读取清单 {len(rows)} 行')
        if args.name:
            base = sanitize(args.name)
        else:
            stem = os.path.splitext(os.path.basename(args.csv))[0]
            base = sanitize(stem.replace('-歌单完整清单', '')
                            .replace('-完整清单', '').replace('-清单', ''))
    else:
        pid = parse_playlist_id(args.playlist)
        base, rows = fetch_playlist(pid, args.limit)
        base = sanitize(base)
    for r in rows:
        r.setdefault('本地文件', '')
        r['_root'] = args.music_root

    rows = match_all(rows, args.music_root)
    write_csvs(args.outdir, base, rows)
    write_m3u8(args.outdir, base, rows)
    print(f'完成，用时 {time.time() - t0:.0f}s，输出目录: {os.path.abspath(args.outdir)}')


if __name__ == '__main__':
    main()
