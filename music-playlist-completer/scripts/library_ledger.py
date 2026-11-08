#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音乐库操作台账：记录「哪种操作对哪个文件已完成」，避免下次重复全库扫描。

核心思想：每个批量操作（改名 / 写标签 / 补歌词 / 补封面 / 分类移动……）完成后，
把成功处理的文件记入台账，同时记录文件的 mtime + size 指纹。下次做同类型操作时：

  ledger.py undone 补歌词 /Volumes/media-2/music     # 只列出「没做过 或 做完后又变过」的文件
  ledger.py status                                   # 各操作各完成了多少

文件只要 mtime 或 size 变了（被改名覆盖、重新下载、手工编辑标签），对应条目
自动失效、重新出现在 undone 里——所以台账永远是「做过且没再动过」的准确集合。

台账文件：~/.cache/music-library-organizer/ledger.json（环境变量 MUSIC_LIB_LEDGER 可覆盖）。
所有 skill 脚本共享同一份；操作名是自由字符串，约定用：rename / tags / lyrics /
cover / native-title / classify / dedupe。

在脚本里当模块用：
    from library_ledger import record, is_done, undone_paths
    record(op="lyrics", path=path, detail="netease")   # 成功后记账
    ...                                                # 下次先 is_done / undone_paths 再决定做谁

CLI 用法：
  ledger.py status [--op 名称 ...]
  ledger.py undone <操作名> <库根目录> [--limit N]     # 列出待处理文件（stdout，每行一个路径）
  ledger.py mark <操作名> <文件路径>... [--detail 备注] # 手工记账（或配合 --from-json）
  ledger.py mark <操作名> --from-json <jsonl>          # 每行 {"p": 路径, "detail": 可选}
  ledger.py forget <操作名> <文件路径>...               # 撤销某条记录（如重跑前清掉）
  ledger.py prune                                       # 清理已不存在的文件的条目
"""
import argparse
import json
import os
import sys
from datetime import datetime

DEFAULT_LEDGER = os.environ.get("MUSIC_LIB_LEDGER") or os.path.expanduser(
    "~/.cache/music-library-organizer/ledger.json")

AUDIO_EXT = (".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".wav",
             ".aiff", ".aif", ".wma")

_SKIP_DIRS = (".cover", "@eaDir", "$RECYCLE.BIN", ".Trashes", "lost+found")


# ---------------------------------------------------------------- 底层 API

def load(path=DEFAULT_LEDGER):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("ops", {})
    data.setdefault("meta", {})
    return data


def save(ledger, path=DEFAULT_LEDGER):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False)
    os.replace(tmp, path)   # 原子替换，中断不会留下半截 JSON


def fingerprint(path):
    st = os.stat(path)
    return {"mtime": st.st_mtime, "size": st.st_size}


def entry_valid(entry, path):
    """条目存在且文件指纹未变才算「已完成」。文件被动过 → 视为未做。

    keyed 条目（按歌曲身份记账，下载类）改用 exists 判定：文件还在就算完成。
    """
    if not entry:
        return False
    target = (entry.get("file") or path) if isinstance(entry, dict) else path
    if not os.path.exists(target):
        return False
    if isinstance(entry, dict) and entry.get("keyed"):
        return True
    try:
        fp = fingerprint(target)
    except OSError:
        return False
    return (abs(entry.get("mtime", -1) - fp["mtime"]) < 0.001
            and entry.get("size", -1) == fp["size"])


def record(ledger, op, path, detail="", at=None, path_key=None, fp=None):
    """记账。path_key 用于按「歌曲身份」等逻辑键记账（下载类操作）。

    下载类操作：path=落盘文件（用于指纹校验），path_key=norm("歌手 - 歌名")，
    entry 里同时存 "file" 字段记录实际位置。
    """
    path = os.path.abspath(path)
    if fp is None:
        try:
            fp = fingerprint(path)
        except OSError:
            fp = {}
    ledger["ops"].setdefault(op, {})[path_key or path] = {
        "mtime": fp.get("mtime"), "size": fp.get("size"),
        "at": at or datetime.now().isoformat(timespec="seconds"),
        "detail": detail, "file": path,
        "keyed": bool(path_key),   # True=按歌曲身份记账，完成判定用 exists 模式
    }


def is_done(ledger, op, path):
    return entry_valid(ledger.get("ops", {}).get(op, {}).get(path), path)


def key_done(ledger, op, key, mode="fingerprint"):
    """按键（而非路径）查台账。entry 存有 file 字段时按该文件校验。

    mode="fingerprint"（默认）：文件还在且 mtime+size 未变 → 已完成。适合
    「标签/歌词写入类」操作——文件被再次编辑意味着要重做。
    mode="exists"：文件还在就算完成。适合「下载类」操作——之后补歌词/封面
    会改 mtime 和 size，但歌已经在了，不该重新下载；文件被删 → 自动失效。
    """
    entry = ledger.get("ops", {}).get(op, {}).get(key)
    if not entry:
        return False
    fpath = entry.get("file") or key
    if not os.path.exists(fpath):
        return False
    return True if mode == "exists" else entry_valid(entry, fpath)


def undone_paths(ledger, op, root):
    """遍历库，返回 (待处理列表, 已完成数)。只碰 mtime，不打开音频，秒级完成。"""
    done_ops = ledger.get("ops", {}).get(op, {})
    todo, done = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.lower().endswith(AUDIO_EXT):
                continue
            p = os.path.join(dirpath, name)
            if entry_valid(done_ops.get(p), p):
                done += 1
            else:
                todo.append(p)
    return todo, done


def prune(ledger):
    """清掉文件已不存在的条目，防止台账无限膨胀。

    按键记账的条目（key 非路径）按其 file 字段判断。
    """
    removed = 0
    for op, entries in list(ledger["ops"].items()):
        for p, entry in list(entries.items()):
            target = entry.get("file") or p if isinstance(entry, dict) else p
            if not os.path.exists(target):
                del entries[p]
                removed += 1
    return removed


# ---------------------------------------------------------------- CLI

def _cli_status(a):
    ledger = load(a.ledger)
    ops = ledger["ops"]
    names = a.op or sorted(ops)
    if not ops:
        print("台账为空。任何批量操作完成后用 mark / record 记账即可。")
        return
    total_w = max(len(n) for n in names) + 2
    for name in names:
        entries = ops.get(name, {})
        valid = sum(1 for p, e in entries.items() if entry_valid(e, p))
        stale = len(entries) - valid
        last = max((e.get("at", "") for e in entries.values()), default="-")[:19]
        print(f"{name:<{total_w}}已完成 {valid:>6} | 过期 {stale:>5} | 最近 {last}")
    if a.op and not all(n in ops for n in a.op):
        miss = [n for n in a.op if n not in ops]
        print(f"（台账中还没有这些操作：{', '.join(miss)}）")


def _cli_undone(a):
    ledger = load(a.ledger)
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        sys.exit(f"目录不存在：{root}")
    todo, done = undone_paths(ledger, a.op, root)
    print(f"# 操作「{a.op}」：待处理 {len(todo)} / 已完成 {done}", file=sys.stderr)
    out = todo[:a.limit] if a.limit else todo
    for p in out:
        print(p)
    if a.limit and len(todo) > a.limit:
        print(f"# （仅显示前 {a.limit} 条，共 {len(todo)}）", file=sys.stderr)


def _cli_mark(a):
    ledger = load(a.ledger)
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                p = r.get("p") or r.get("path")
                if p:
                    record(ledger, a.op, p, detail=r.get("detail", ""))
    else:
        if not a.paths:
            sys.exit("请给出文件路径，或用 --from-json 批量记账")
        for p in a.paths:
            record(ledger, a.op, p, detail=a.detail)
    save(ledger, a.ledger)
    n = len(ledger["ops"].get(a.op, {}))
    print(f"已记账：操作「{a.op}」累计 {n} 条 → {a.ledger}")


def _cli_forget(a):
    ledger = load(a.ledger)
    entries = ledger["ops"].get(a.op, {})
    removed = 0
    for p in a.paths:
        p = os.path.abspath(p)
        if p in entries:
            del entries[p]
            removed += 1
    if not entries:
        ledger["ops"].pop(a.op, None)
    save(ledger, a.ledger)
    print(f"已撤销 {removed} 条（操作「{a.op}」）")


def _cli_prune(a):
    ledger = load(a.ledger)
    removed = prune(ledger)
    save(ledger, a.ledger)
    print(f"已清理 {removed} 条失效条目")


def main():
    ap = argparse.ArgumentParser(description="音乐库操作台账（完成状态记录）")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER, help="台账 JSON 路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="各操作的完成情况")
    p.add_argument("--op", nargs="*", help="只看这些操作")

    p = sub.add_parser("undone", help="列出某操作还没做（或做完后又变了）的文件")
    p.add_argument("op")
    p.add_argument("root")
    p.add_argument("--limit", type=int, default=0)

    p = sub.add_parser("mark", help="记账：某操作对某些文件已完成")
    p.add_argument("op")
    p.add_argument("paths", nargs="*")
    p.add_argument("--detail", default="")
    p.add_argument("--from-json", default="", help="JSONL 文件，每行 {\"p\": 路径, \"detail\": 可选}")

    p = sub.add_parser("forget", help="撤销记录")
    p.add_argument("op")
    p.add_argument("paths", nargs="+")

    p = sub.add_parser("prune", help="清理文件已不存在的条目")

    a = ap.parse_args()
    a.ledger = os.path.abspath(os.path.expanduser(a.ledger))
    {"status": _cli_status, "undone": _cli_undone, "mark": _cli_mark,
     "forget": _cli_forget, "prune": _cli_prune}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
