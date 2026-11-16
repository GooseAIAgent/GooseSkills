#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 m3u8 歌单中「路径留空」的条目，自动补全本地音乐文件路径。

典型场景：从音乐平台导出歌单时，本地还没下载的曲目只保留 #EXTINF 标题、
路径留空；日后把歌下载进本地库后，重新跑一次本脚本即可批量补全。

用法:
    python3 fill_playlist_paths.py 歌单.m3u8
    python3 fill_playlist_paths.py 歌单.m3u8 --music-root ~/Music
    python3 fill_playlist_paths.py 歌单.m3u8 --dry-run     # 只看能补多少，不写文件

匹配顺序（由严到宽）:
    1. 文件名（不含扩展名）完全等于 「歌手 - 歌名」
    2. 归一化后相等（去空格/全半角/大小写/书名号括号差异）
    3. 歌名（去掉歌手前缀）完全相等且候选唯一
音频扩展名: .flac .mp3 .m4a .wav .ape .ogg .opus .dsf .dff .aac .wma .aiff
"""
import argparse
import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher

AUDIO_EXT = {'.flac', '.mp3', '.m4a', '.wav', '.ape', '.ogg', '.opus',
             '.dsf', '.dff', '.aac', '.wma', '.aiff', '.aif'}
SKIP_DIR = {'#recycle', '.@#thumbnails', '@eaDir', '.Trash', '重复副本'}


def norm(s):
    s = unicodedata.normalize('NFC', s)
    s = s.replace('（', '(').replace('）', ')').replace('【', '[').replace('】', ']')
    s = re.sub(r'\s+', '', s).lower()
    return s


def norm_title(s):
    """去掉常见括号说明与标点，用于宽松兜底匹配。"""
    s = unicodedata.normalize('NFC', s)
    s = re.sub(r'[\(（\[【].*?[\)）\]】]', '', s)
    s = re.sub(r'[\s\-_·、,，.。!！?？"\'“”‘’]', '', s).lower()
    return s


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


def read_entries(path):
    """返回 (header_lines, entries)；entries = [[extinf, uri], ...]

    header 指第一个 #EXTINF 之前的说明行，原样保留。
    """
    with open(path, 'r', encoding='utf-8-sig') as f:
        raw = f.read().split('\n')

    header, entries, cur = [], [], None
    for line in raw:
        if line.startswith('#EXTINF'):
            if cur is not None:
                entries.append(cur)
            cur = [line, '']
        elif cur is None:
            if line.strip():
                header.append(line)
        elif line.strip() == '' or line.startswith('#'):
            continue
        else:
            if cur[1] == '':
                cur[1] = line
    if cur is not None:
        entries.append(cur)
    return header, entries


def write_entries(path, header, entries):
    out = list(header)
    for extinf, uri in entries:
        out.append(extinf)
        out.append(uri)          # 未匹配到的保持空行
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out) + '\n')


def artist_tokens(s):
    """歌手名归一为排序后的 token 集，容忍「A、B」与「B、A」顺序差异。"""
    return sorted(norm_title(t) for t in re.split(r'[、,/]', s) if t.strip())


def same_artist(a, b):
    """歌手集合兼容：完全相等，或一方是另一方的子集（合唱署名差异）。"""
    ta, tb = set(artist_tokens(a)), set(artist_tokens(b))
    return bool(ta and tb) and (ta == tb or ta <= tb or tb <= ta)


def match_one(label, idx, used_paths):
    """label 形如「歌手 - 歌名」，返回最佳路径或 None。

    宽松层要求歌手一致：简弘亦的歌不会误配到田馥甄的同名歌。
    严格层不做拆括号处理，「夜曲」不会误配「夜曲(Live)」。
    """
    key = norm(label)
    artist, _, rest = label.partition(' - ')
    title = norm_title(rest if ' - ' in label else label)

    cands = [p for p, k, _ in idx if k == key]
    cands = [p for p in cands if p not in used_paths] or cands
    if cands:
        return cands[0]

    # 宽松：歌名归一后相同 且 歌手一致，取相似度最高的唯一候选
    loose = []
    for p, _, t in idx:
        if t != title:
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        fa = stem.split(' - ', 1)[0] if ' - ' in stem else ''
        if fa and same_artist(artist, fa):
            loose.append(p)
    loose = [p for p in loose if p not in used_paths] or loose
    if len(loose) == 1:
        return loose[0]
    if len(loose) > 1:
        best = max(loose, key=lambda p: SequenceMatcher(None, key, norm(p)).ratio())
        if SequenceMatcher(None, key, norm(best)).ratio() > 0.9:
            return best
    return None


def main():
    ap = argparse.ArgumentParser(description='补全 m3u8 歌单中留空条目的本地路径')
    ap.add_argument('playlist')
    ap.add_argument('--music-root', default=os.path.expanduser("~/Music"),
                    help='本地音乐库根目录，默认 ~/Music')
    ap.add_argument('--dry-run', action='store_true', help='只报告，不写回文件')
    args = ap.parse_args()

    if not os.path.isdir(args.music_root):
        sys.exit(f'音乐库目录不存在: {args.music_root}')

    header, entries = read_entries(args.playlist)
    idx = scan_library(args.music_root)
    print(f'歌单条目 {len(entries)}  本地音频 {len(idx)}')

    used, filled, still = set(), 0, 0
    for e in entries:
        extinf, uri = e
        # 已有且文件存在的路径不重复处理
        if uri and not uri.startswith('#'):
            if os.path.exists(uri):
                used.add(uri)
                continue
            if uri.startswith(('http://', 'https://')):
                continue
        label = extinf.split(',', 1)[1].strip() if ',' in extinf else ''
        hit = match_one(label, idx, used)
        if hit:
            e[1] = hit
            used.add(hit)
            filled += 1
            print(f'  ✓ {label}  ->  {os.path.basename(hit)}')
        else:
            e[1] = ''
            still += 1

    print(f'\n本次补全 {filled} 首，仍留空 {still} 首')
    if args.dry_run:
        print('（--dry-run，未写回文件）')
        return
    write_entries(args.playlist, header, entries)
    print(f'已写回: {args.playlist}')


if __name__ == '__main__':
    main()
