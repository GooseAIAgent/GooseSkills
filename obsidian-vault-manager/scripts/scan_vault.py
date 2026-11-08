#!/usr/bin/env python3
"""Read-only scanner for an Obsidian vault.

Scans all Markdown notes, extracts wikilinks/tags/frontmatter, and reports
stats, orphans, broken links, duplicate basenames, and per-file metadata.

Usage:
    python3 scan_vault.py --vault /path/to/vault [--out report.json]
    python3 scan_vault.py --vault /path/to/vault --include-sensitive

Stdlib only. Deterministic output (sorted lists).
"""

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from vault_paths import excluded, resolve_target, body_text, CREDENTIAL_RE

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Directories always skipped (system / tool / attachment dirs).
SKIP_DIR_NAMES = {
    ".obsidian", ".trash", ".agents", ".codian", ".codex",
    ".hinote", ".smart-env", ".cache", "Obsidian Attachments",
}
# Any hidden dir (starts with ".") is skipped regardless of name.
SKIP_FILES = {".DS_Store", "index.base", "Icon"}

# Basenames (without extension) matching these are treated as sensitive:
# content is not read, not summarized, not linked; only the filename appears in reports.
SENSITIVE_RE = re.compile(
    r"(密码|password|token|secret|credential|私钥|凭据|api[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)

# 支持 Obsidian 转义：`\#`、`\|` 属于目标文件名的一部分（非锚点/非别名）。
# 普通字符类排除反斜杠、#、|，使转义字符只能通过转义分支被消费。
WIKILINK_RE = re.compile(r"\[\[((?:[^\[\]|#\\]|\\#|\\\|)+?)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
TAG_RE = re.compile(r"#([^\s#\[\]|]+)")
# Markdown 超链接 [text](url) 与图片 ![alt](url)；url 可为相对路径或外部协议
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
# 外部/特殊协议（不当作仓库内链接处理）
EXTERNAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")


def unescape_target(t: str) -> str:
    """还原 Obsidian wiki-link 中的转义（\\# → #、\\| → |），用于解析与匹配。"""
    t = t.replace("\\#", "\x00").replace("\\|", "\x01")
    t = t.split("|")[0].split("#")[0]
    return t.replace("\x00", "#").replace("\x01", "|").strip()


def find_markdown_files(vault: Path, include_sensitive: bool) -> list[Path]:
    files = []
    for p in sorted(vault.rglob("*")):
        if not p.is_file() or excluded(p, vault, include_sensitive):
            continue
        rel = p.relative_to(vault)
        parts = rel.parts
        if any(part.startswith(".") or part in SKIP_DIR_NAMES for part in parts[:-1]):
            continue
        if p.name in SKIP_FILES or p.suffix.lower() not in (".md", ".markdown"):
            continue
        if not include_sensitive and SENSITIVE_RE.search(p.stem):
            continue
        files.append(p)
    return files


def extract_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    block = content[3:end]
    data = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data


def link_target_resolves(note_path: Path, vault: Path, target: str) -> bool:
    """Return True if the wikilink target resolves to an existing file.

    Handles relative paths (./x, ../x) and vault-root-relative paths,
    e.g. [[../../Obsidian Attachments/attachments/img.png]].
    Escaped hashes (\\#) are part of the filename, not anchors.
    """
    t = unescape_target(target)
    if not t:
        return False
    candidates = [note_path.parent / t, vault / t]
    return any(c.is_file() for c in candidates)


def scan_vault(vault: Path, include_sensitive: bool) -> dict:
    vault = vault.resolve()
    files = find_markdown_files(vault, include_sensitive)

    # name -> list of relative paths (basename without extension, lowercased for matching)
    name_index: dict[str, list[str]] = {}
    # name -> list of relative paths for ALL files (incl. non-md) for broken-link check
    all_name_index: dict[str, list[str]] = {}
    file_index = {"name": {}, "stem": {}}
    for p in vault.rglob("*"):
        if p.is_file() and not any(
            part.startswith(".") or part in SKIP_DIR_NAMES for part in p.relative_to(vault).parts[:-1]
        ):
            key = p.stem.lower()
            rel = p.relative_to(vault).as_posix()
            if not excluded(p, vault, include_sensitive=True):
                file_index["name"].setdefault(p.name, []).append(rel)
                file_index["stem"].setdefault(p.stem, []).append(rel)
            all_name_index.setdefault(key, []).append(rel)
            if p.suffix.lower() in (".md", ".markdown"):
                name_index.setdefault(key, []).append(rel)

    links: dict[str, list[str]] = {}          # note rel path -> list of link targets (basename as written)
    incoming: dict[str, list[str]] = {}       # target rel path -> source rel paths (resolved)
    tags: dict[str, list[str]] = {}           # note rel path -> tags
    files_meta: list[dict] = []
    broken: list[dict] = []
    duplicates: dict[str, list[str]] = {}
    ambiguous: list[dict] = []
    skipped_sensitive: list[str] = []

    for p in files:
        rel = p.relative_to(vault).as_posix()
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            files_meta.append({"path": rel, "error": str(e)})
            continue

        if not include_sensitive and CREDENTIAL_RE.search(raw):
            skipped_sensitive.append(rel)
            continue

        content = raw
        fm = extract_frontmatter(content)
        body = body_text(content)
        for regex, wiki in ((WIKILINK_RE, True), (MD_LINK_RE, False)):
            for match in regex.finditer(body):
                target = match.group(1).strip()
                if not target or (not wiki and (target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I))):
                    continue
                links.setdefault(rel, []).append(target)
                candidates = resolve_target(p, vault, target, wiki, file_index)
                if len(candidates) > 1:
                    ambiguous.append({"from": rel, "link": target, "candidates": candidates})
                elif candidates:
                    dest = candidates[0]
                    if dest != rel and dest.lower().endswith((".md", ".markdown")):
                        incoming.setdefault(dest, []).append(rel)
                elif wiki or urllib.parse.unquote(target.split("#", 1)[0]).lower().endswith((".md", ".markdown")):
                    broken.append({"from": rel, "link": target})

        note_tags: list[str] = []
        for m in TAG_RE.finditer(body):
            t = m.group(1).strip()
            if t and not t.startswith("^"):  # exclude block refs
                note_tags.append(t)
        tags[rel] = sorted(set(note_tags))

        files_meta.append(
            {
                "path": rel,
                "chars": len(raw),
                "words": len(re.findall(r"\S+", raw)),
                "modified": p.stat().st_mtime,
                "frontmatter_keys": sorted(fm.keys()),
            }
        )

    # orphans: no incoming links AND no outgoing links
    orphan_set = []
    no_incoming_set = []
    for p in files:
        rel = p.relative_to(vault).as_posix()
        if rel in skipped_sensitive:
            continue
        has_out = bool(links.get(rel))
        has_in = bool(incoming.get(rel))
        if not has_out and not has_in:
            orphan_set.append(rel)
        elif not has_in:
            no_incoming_set.append(rel)

    for name, paths in name_index.items():
        if len(paths) > 1:
            duplicates[name] = sorted(paths)

    return {
        "vault": str(vault),
        "ambiguous_links": ambiguous,
        "skipped_sensitive": sorted(set(skipped_sensitive + [p.relative_to(vault).as_posix() for p in vault.rglob("*") if p.is_file() and SENSITIVE_RE.search(p.stem)])),
        "include_sensitive": include_sensitive,
        "stats": {
            "note_count": len(files),
            "total_chars": sum(f.get("chars", 0) for f in files_meta),
            "link_count": sum(len(v) for v in links.values()),
            "incoming_count": sum(len(v) for v in incoming.values()),
            "orphan_count": len(orphan_set),
            "no_incoming_count": len(no_incoming_set),
            "broken_link_count": len(broken),
            "duplicate_name_groups": len(duplicates),
        },
        "links": {k: sorted(v) for k, v in sorted(links.items())},
        "incoming": {k: sorted(set(v)) for k, v in sorted(incoming.items())},
        "tags": tags,
        "orphans": sorted(orphan_set),
        "no_incoming_links": sorted(no_incoming_set),
        "broken_links": sorted(broken, key=lambda d: (d["from"], d["link"])),
        "duplicates": {k: sorted(v) for k, v in sorted(duplicates.items())},
        "files": sorted(files_meta, key=lambda d: d["path"]),
    }


def print_summary(report: dict) -> None:
    s = report["stats"]
    print(f"仓库: {report['vault']}")
    print(f"笔记数: {s['note_count']} | 总字数: {s['total_chars']}")
    print(f"链接总数: {s['link_count']} | 入链总数: {s['incoming_count']}")
    print(f"孤立笔记（无入链且无出链）: {s['orphan_count']}")
    print(f"无入链笔记: {s['no_incoming_count']}")
    print(f"断链: {s['broken_link_count']}")
    print(f"同名笔记组: {s['duplicate_name_groups']}")
    if report["orphans"]:
        print("\n孤立笔记:")
        for p in report["orphans"][:50]:
            print(f"  - {p}")
        if len(report["orphans"]) > 50:
            print(f"  ... 共 {len(report['orphans'])} 条")
    if report["broken_links"]:
        print("\n断链（前 50 条）:")
        for b in report["broken_links"][:50]:
            print(f"  - {b['from']} -> {b['link']}")
    if report["duplicates"]:
        print("\n同名笔记:")
        for name, paths in report["duplicates"].items():
            print(f"  - {name}: {', '.join(paths)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only Obsidian vault scanner.")
    ap.add_argument("--vault", required=True, help="Absolute path to the vault folder")
    ap.add_argument("--out", help="Write JSON report to this file")
    ap.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Also scan files whose names match sensitive patterns (credentials etc.)",
    )
    args = ap.parse_args()

    vault = Path(args.vault)
    if not vault.is_dir():
        sys.exit(f"vault not found: {vault}")

    report = scan_vault(vault, args.include_sensitive)
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 报告已写入: {args.out}")
    print_summary(report)


if __name__ == "__main__":
    main()
