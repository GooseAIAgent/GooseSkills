#!/usr/bin/env python3
"""Backup files before batch writes in an Obsidian vault.

Copies the given files (resolved against the vault) into a timestamped backup
folder, preserving the vault-relative directory structure.

Usage:
    python3 backup_vault.py --vault /path/to/vault --files noteA.md notes/noteB.md
    python3 backup_vault.py --vault /path/to/vault --files noteA.md --backup-dir /tmp/my-backup
    python3 backup_vault.py --vault /path/to/vault --files noteA.md --keep 5

Backup location: <backup-root>/<YYYYmmdd-HHMMSS>/
  --backup-dir overrides the root; default root is <home>/ovm-backups/.

Rotation: keeps the newest BACKUP_KEEP (default 3) timestamped backup dirs
under the backup root, removes older ones. Only touches dirs whose names match
the timestamp pattern; never deletes user-created directories.

Stdlib only. Cross-platform (Windows / macOS / Linux).
"""
import argparse
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BACKUP_KEEP = 3
TIMESTAMP_RE = re.compile(r"(?:.*-)?(\d{8}-\d{6})$")


def prune_old_backups(root: Path, keep: int = BACKUP_KEEP) -> list[Path]:
    """Remove timestamped backup dirs under root beyond the newest `keep`."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    dirs = [p for p in root.iterdir() if p.is_dir() and TIMESTAMP_RE.match(p.name)]
    dirs.sort(key=lambda p: TIMESTAMP_RE.match(p.name).group(1))
    removed = dirs[:-keep] if keep > 0 else dirs
    for d in removed:
        shutil.rmtree(d)
    return removed


def main() -> None:
    ap = argparse.ArgumentParser(description="Backup vault files before batch writes.")
    ap.add_argument("--vault", required=True, help="Absolute path to the vault folder")
    ap.add_argument("--files", nargs="+", required=True, help="Files to back up (relative to vault or absolute)")
    ap.add_argument(
        "--backup-dir",
        default=None,
        help=f"Backup root override (default: <home>/ovm-backups/<timestamp>)",
    )
    ap.add_argument("--keep", type=int, default=BACKUP_KEEP, help=f"Backup versions to keep (default: {BACKUP_KEEP})")
    args = ap.parse_args()
    if args.keep < 1:
        ap.error("--keep must be at least 1")

    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        sys.exit(f"vault not found: {vault}")

    if args.backup_dir:
        backup_root = Path(args.backup_dir).resolve()
    else:
        backup_root = Path.home() / "ovm-backups"
    backup = backup_root / (uuid.uuid4().hex[:8] + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"))

    copied: list[str] = []
    skipped: list[str] = []

    for f in args.files:
        src = Path(f).resolve() if Path(f).is_absolute() else (vault / f).resolve()
        try:
            rel = src.relative_to(vault)
        except ValueError:
            skipped.append(f"{f} (outside vault, skipped)")
            continue
        if not src.is_file():
            skipped.append(f"{f} (not a file, skipped)")
            continue
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel.as_posix())

    if not copied:
        print("没有文件被备份。")
    else:
        print(f"备份位置: {backup}")
        for c in copied:
            print(f"  ✓ {c}")
        print(f"共备份 {len(copied)} 个文件")
    for s in skipped:
        print(f"  - {s}")

    if skipped:
        sys.exit("备份不完整；禁止继续写入。")

    # 备份轮转：备份根下只保留最近 args.keep 个时间戳备份目录
    if backup_root.is_dir():
        removed = prune_old_backups(backup_root, args.keep)
        if removed:
            print(f"轮转清理：删除 {len(removed)} 个旧备份，保留最近 {args.keep} 个")


if __name__ == "__main__":
    main()
