#!/usr/bin/env python3
"""Apply an explicit JSON linking plan. Defaults to dry-run; stdlib, Python 3.9+."""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from backup_vault import prune_old_backups
from scan_vault import scan_vault
from vault_paths import excluded, CREDENTIAL_RE, note_targets, resolve_target, WIKI_RE, body_text


def atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.ovm-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def insert_links(raw, lines):
    # Match headings only outside YAML/fenced examples, preserving all existing bytes.
    in_fence = False
    in_yaml = raw.startswith('---\n') or raw.startswith('---\r\n')
    offset = 0
    related_start = None
    adaptation = None
    for index, line in enumerate(raw.splitlines(keepends=True)):
        stripped = line.strip()
        if in_yaml:
            if index and stripped == '---':
                in_yaml = False
        elif stripped.startswith(('```', '~~~')):
            in_fence = not in_fence
        elif not in_fence:
            if re.match(r'^##\s+相关链接\s*$', line.rstrip('\r\n')):
                related_start = offset + len(line)
            elif re.match(r'^#{1,2}\s+', line) and related_start is not None:
                return raw[:offset] + '\n' + '\n'.join(lines) + '\n' + raw[offset:]
            if re.match(r'^##\s+相关改编\s*$', line.rstrip('\r\n')) and adaptation is None:
                adaptation = offset
        offset += len(line)
    if related_start is not None:
        return raw + '\n' + '\n'.join(lines) + '\n'
    block = '\n## 相关链接\n' + '\n'.join(lines) + '\n'
    if adaptation is not None:
        return raw[:adaptation] + block + raw[adaptation:]
    return raw + '\n' + block


def build_plan(vault, data):
    if not isinstance(data, dict) or not isinstance(data.get('links'), list):
        raise ValueError('plan must be an object with a links array')
    original = {}
    rows = []
    seen = set()
    counts = {}
    clusters = {}

    def checked(value):
        if not isinstance(value, str) or not value or '\\' in value or Path(value).is_absolute() or re.match(r'^[A-Za-z]:', value):
            raise ValueError('plan paths must be vault-relative with / separators')
        path = vault / value
        if excluded(path, vault) or not path.is_file() or path.suffix.lower() not in ('.md', '.markdown'):
            raise ValueError('excluded, missing or non-Markdown note: ' + value)
        rel = path.resolve().relative_to(vault).as_posix()
        if rel not in original:
            original[rel] = path.read_bytes()
            if CREDENTIAL_RE.search(original[rel].decode('utf-8')):
                raise ValueError('possible credentials; skipped note: ' + rel)
        return rel

    for row in data['links']:
        if not isinstance(row, dict):
            raise ValueError('each link must be an object')
        src, dst = checked(row.get('source')), checked(row.get('target'))
        reason, cluster = row.get('reason'), row.get('cluster')
        if not isinstance(reason, str) or not reason.strip() or '\n' in reason or '\r' in reason:
            raise ValueError('each link needs a single-line reason')
        if not isinstance(cluster, str) or not cluster.strip():
            raise ValueError('each link needs a cluster identifier')
        if src == dst or (src, dst) in seen:
            continue
        existing = note_targets(vault / src, vault, original[src].decode('utf-8'))
        if dst in existing:
            continue
        # An unresolved ambiguous Wiki target could already refer to this note: require review.
        for match in WIKI_RE.finditer(body_text(original[src].decode('utf-8'))):
            candidates = resolve_target(vault / src, vault, match.group(1), True)
            if len(candidates) > 1 and dst in candidates:
                raise ValueError('resolve ambiguous existing Wiki link before adding: ' + src)
        seen.add((src, dst))
        counts[src] = counts.get(src, 0) + 1
        key = (src, cluster)
        clusters[key] = clusters.get(key, 0) + 1
        if counts[src] > 8 or clusters[key] > 3:
            raise ValueError('plan exceeds 8 links per note or 3 per note/cluster: ' + src)
        rows.append(dict(source=src, target=dst, reason=reason.strip(), cluster=cluster))
    return rows, original


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vault', required=True)
    ap.add_argument('--plan', required=True, help='JSON plan: links[{source,target,reason,cluster}]')
    ap.add_argument('--backup-root', default=str(Path.home() / 'ovm-backups'))
    ap.add_argument('--keep', type=int, default=3)
    ap.add_argument('--report', help='Operation record JSON; default: <plan-stem>.result.json')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if args.keep < 1:
        ap.error('--keep must be at least 1')
    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        ap.error('vault not found')
    plan_path = Path(args.plan).resolve()
    report_path = Path(args.report).resolve() if args.report else plan_path.with_name(plan_path.stem + '.result.json')
    if report_path == plan_path or report_path.is_relative_to(vault):
        ap.error('report must be outside vault and different from plan')
    if report_path.exists():
        report_path = report_path.with_name(report_path.stem + '-' + uuid.uuid4().hex[:8] + report_path.suffix)
    rows, original = build_plan(vault, json.loads(plan_path.read_text(encoding='utf-8')))
    touched = sorted({r['source'] for r in rows})
    print('链接计划: 改动 %d 篇 | 新增 %d 条' % (len(touched), len(rows)))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    if not args.apply:
        print('dry-run：未写入；使用 --apply 执行已授权清单。')
        return
    backup_root = Path(args.backup_root).resolve()
    if backup_root.is_relative_to(vault):
        ap.error('backup root must be outside vault')
    baseline = {(r['from'], r['link']) for r in scan_vault(vault, False)['broken_links']}
    record = dict(status='pending', links=rows, files=len(touched), backup=None, hashes={})
    report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(report_path, json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
    if not rows:
        record.update(status='success', verification='no new links needed')
        atomic_write(report_path, json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
        return
    backup = backup_root / ('links-' + uuid.uuid4().hex[:8] + '-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    record['backup'] = str(backup)
    written = {}
    try:
        for rel in touched:
            if (vault / rel).read_bytes() != original[rel]:
                raise RuntimeError('note changed since planning: ' + rel)
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(vault / rel, dst)
            if dst.read_bytes() != original[rel]:
                raise RuntimeError('backup mismatch: ' + rel)
        record['backup'] = str(backup)
        for rel in touched:
            lines = []
            for row in rows:
                if row['source'] != rel:
                    continue
                label = Path(row['target']).stem
                if sum(Path(r['target']).stem == label for r in rows if r['source'] == rel) > 1:
                    label = row['target']
                label = label.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
                target = quote(os.path.relpath(vault / row['target'], (vault / rel).parent).replace(os.sep, '/'), safe='/.-_~')
                lines.append('- [%s](%s) — %s' % (label, target, row['reason']))
            written[rel] = insert_links(original[rel].decode('utf-8'), lines).encode('utf-8')
            record['hashes'][rel] = dict(before=hashlib.sha256(original[rel]).hexdigest(), after=hashlib.sha256(written[rel]).hexdigest())
        # Persist recovery information before touching notes.
        atomic_write(report_path, json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
        for rel in touched:
            if (vault / rel).read_bytes() != original[rel]:
                raise RuntimeError('note changed before write: ' + rel)
            atomic_write(vault / rel, written[rel])
        for row in rows:
            if row['target'] not in note_targets(vault / row['source'], vault, (vault / row['source']).read_text(encoding='utf-8')):
                raise RuntimeError('new link verification failed')
        after = scan_vault(vault, False)
        if {(r['from'], r['link']) for r in after['broken_links']} - baseline:
            raise RuntimeError('new broken links detected')
        record.update(status='success', verification='targets exist; no new broken links')
    except Exception as error:
        conflicts = []
        for rel, content in written.items():
            try:
                current = (vault / rel).read_bytes()
            except OSError:
                conflicts.append(rel)
                continue
            if current == content:
                atomic_write(vault / rel, original[rel])
            elif current != original[rel]:
                conflicts.append(rel)
        record.update(status='failed', error=str(error), rollback_conflicts=conflicts)
        raise
    finally:
        atomic_write(report_path, json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
    prune_old_backups(backup_root, args.keep)
    print('已验证：%d 个文件，%d 条链接；备份：%s；记录：%s' % (len(touched), len(rows), backup, report_path))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        sys.exit(str(error))
