#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描音乐库的「内嵌歌词 / 内嵌封面」状态，产出断点续扫的状态文件。

为什么需要它：批量补歌词/封面是一个可能跑几小时的长任务。状态文件记录
「每首歌是否已有歌词/封面」以及文件 mtime，于是：
  - 任务中断后重跑只处理剩下的，不重复耗时；
  - 文件被改名/重新下载（mtime 变化）后会自动重扫这一首；
  - 下游 embed 脚本不需要每首都打开音频去读标签，直接吃状态文件。

用法：
  python3 scan_tag_state.py /path/to/music
  python3 scan_tag_state.py /path/to/music --state ~/lib_state.json

输出：状态 JSON，形如
  {"items": [{"p": 绝对路径, "t": 标题, "a": 艺术家, "alb": 专辑,
              "dur": 秒, "ly": 有歌词, "cv": 有封面}],
   "done": {路径: mtime}}
"""
import argparse
import json
import os
import sys

AUDIO_EXT = (".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wma")
DEFAULT_STATE = os.environ.get("MUSIC_LIB_STATE") or os.path.expanduser(
    "~/.cache/music-library-organizer/lib_state.json")


def probe(path):
    """读取单首歌曲的标签与内嵌歌词/封面状态。失败时保留空值，不中断扫描。"""
    ext = path.lower().rsplit(".", 1)[-1]
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path, easy=True)
        title = (audio.get("title") or [""])[0] if audio else ""
        artist = (audio.get("artist") or [""])[0] if audio else ""
        album = (audio.get("album") or [""])[0] if audio else ""
        dur = round(audio.info.length) if audio and audio.info else 0
    except Exception as e:
        return {"p": path, "t": "", "a": "", "alb": "", "dur": 0,
                "ly": False, "cv": False, "err": str(e)[:60]}

    has_ly = has_cv = False
    try:
        if ext == "flac":
            from mutagen.flac import FLAC
            fl = FLAC(path)
            has_ly = bool(fl.get("LYRICS") or fl.get("lyrics")
                          or fl.get("UNSYNCEDLYRICS") or fl.get("UNSYNCED LYRICS"))
            has_cv = len(fl.pictures) > 0
        elif ext == "mp3":
            from mutagen.id3 import ID3
            try:
                tags = ID3(path)
            except Exception:
                tags = None
            if tags:
                has_ly = bool(tags.getall("USLT") or tags.getall("SYLT"))
                has_cv = bool(tags.getall("APIC"))
        elif ext in ("m4a", "mp4"):
            from mutagen.mp4 import MP4
            mp4 = MP4(path)
            has_ly = bool(mp4.tags and (mp4.tags.get("\xa9lyr") or mp4.tags.get("----:com.apple.iTunes:LYRICS")))
            has_cv = bool(mp4.tags and mp4.tags.get("covr"))
        elif ext in ("ogg", "opus"):
            from mutagen import File as MF2
            o = MF2(path)
            has_ly = bool(o and (o.get("LYRICS") or o.get("lyrics")))
            has_cv = bool(o and (o.get("metadata_block_picture") or o.get("METADATA_BLOCK_PICTURE")))
    except Exception:
        pass

    return {"p": path, "t": title, "a": artist, "alb": album, "dur": dur,
            "ly": has_ly, "cv": has_cv}


def main():
    ap = argparse.ArgumentParser(description="扫描音乐库内嵌歌词/封面状态（增量）")
    ap.add_argument("root", nargs="?", default=".", help="音乐库根目录")
    ap.add_argument("--state", default=DEFAULT_STATE, help="状态文件路径")
    ap.add_argument("--full", action="store_true", help="忽略 mtime 缓存，全量重扫")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    state_path = os.path.abspath(os.path.expanduser(args.state))
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    state.setdefault("items", [])
    done = {} if args.full else state.get("done", {})

    # 索引现有 items，避免每首 O(n) 重建列表
    index = {x["p"]: x for x in state["items"] if x.get("p")}

    # 联动操作台账：台账里记过且文件指纹未变的项直接沿用缓存（probe 一次都不用打开）
    ledger = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import library_ledger
        ledger = library_ledger.load()
    except Exception:
        pass
    ledger_ly = (ledger or {}).get("ops", {}).get("lyrics", {})
    ledger_cv = (ledger or {}).get("ops", {}).get("cover", {})

    todo = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".cover", "@eaDir", "$RECYCLE.BIN")]
        for name in filenames:
            if not name.lower().endswith(AUDIO_EXT):
                continue
            path = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if done.get(path) != mtime:
                todo.append((path, mtime))
                continue
            # mtime 缓存命中，但条目可能还不完整：台账认定歌词+封面均已完成的直接跳过
            cached = index.get(path)
            if cached and cached.get("ly") and cached.get("cv"):
                continue
            if (ledger is not None
                    and library_ledger.entry_valid(ledger_ly.get(path), path)
                    and library_ledger.entry_valid(ledger_cv.get(path), path)):
                continue
            todo.append((path, mtime))

    print(f"待扫 {len(todo)} / 已有缓存 {len(done)}", flush=True)
    for i, (path, mtime) in enumerate(todo, 1):
        index[path] = probe(path)
        done[path] = mtime
        if i % 200 == 0:
            state["items"] = list(index.values())
            state["done"] = done
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            print(f"  {i}/{len(todo)}", flush=True)

    state["items"] = list(index.values())
    state["done"] = done
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

    items = state["items"]
    ly = sum(1 for x in items if x.get("ly"))
    cv = sum(1 for x in items if x.get("cv"))
    both = sum(1 for x in items if x.get("ly") and x.get("cv"))
    notag = sum(1 for x in items if not x.get("t") or not x.get("a"))
    print(f"共 {len(items)} 首 | 有歌词 {ly} | 有封面 {cv} | 双全 {both} | 缺标题/艺术家 {notag}")
    print(f"状态文件：{state_path}")


if __name__ == "__main__":
    sys.exit(main())
