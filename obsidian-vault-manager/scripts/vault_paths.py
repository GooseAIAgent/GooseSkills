"""Shared path resolution and conservative exclusions; Python 3.9+, stdlib only."""
import re
import urllib.parse
from pathlib import Path, PurePosixPath

SKIP_DIR_NAMES = {'.obsidian', '.trash', '.agents', '.codian', '.codex', '.hinote',
                  '.smart-env', '.cache', 'Obsidian Attachments'}
SKIP_FILES = {'.DS_Store', 'index.base', 'Icon'}
SENSITIVE_RE = re.compile(r'密码|password|token|secret|credential|私钥|凭据|api[_-]?key|access[_-]?key', re.I)
CREDENTIAL_RE = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|^\s*(?:password|passwd|api[_-]?key|access[_-]?key|client[_-]?secret|密码|私钥)\s*[:=]\s*\S+', re.I | re.M)
WIKI_RE = re.compile(r'\[\[((?:[^\[\]|#\\]|\\#|\\\|)+?)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]')
MD_RE = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)')
EXTERNAL_RE = re.compile(r'^[a-z][a-z0-9+.-]*:', re.I)


def body_text(raw):
    if raw.startswith('---\n') or raw.startswith('---\r\n'):
        raw = re.sub(r'\A---\r?\n.*?\r?\n---(?:\r?\n|$)', '', raw, count=1, flags=re.S)
    parts = re.split(r'^```.*$|^~~~.*$', raw, flags=re.M)
    return '\n'.join(parts[0::2])


def unescape_target(target):
    target = target.replace('\\#', '\x00').replace('\\|', '\x01')
    return target.split('|')[0].split('#')[0].replace('\x00', '#').replace('\x01', '|').strip()


def excluded(path, vault, include_sensitive=False):
    """Never read symlinks that escape the vault or excluded directories."""
    try:
        lexical = path.relative_to(vault)
        actual = path.resolve().relative_to(vault.resolve())
    except ValueError:
        return True
    for rel in (lexical, actual):
        if any(part.startswith('.') or part in SKIP_DIR_NAMES for part in rel.parts[:-1]):
            return True
        if rel.name.startswith('.') or rel.name in SKIP_FILES:
            return True
        if not include_sensitive and SENSITIVE_RE.search(rel.stem):
            return True
    return False


def resolve_target(note, vault, target, wiki=False, file_index=None):
    """Return existing file candidates, bounded to the vault; ambiguity stays explicit."""
    vault = vault.resolve()
    if wiki:
        value = unescape_target(target)
    else:
        if target.startswith('#') or EXTERNAL_RE.match(target):
            return []
        value = urllib.parse.unquote(target.split('#', 1)[0])
    if not value:
        return []
    value = value.replace('\\', '/')
    if PurePosixPath(value).is_absolute() or re.match(r'^[A-Za-z]:', value):
        return []
    relative = Path(value)
    variants = [relative]
    if wiki and not relative.suffix:
        variants += [Path(value + '.md'), Path(value + '.markdown')]
    # Explicit paths must never fall back to basename matching.
    roots = [note.parent, vault] if wiki else [note.parent]
    for root in roots:
        found = []
        for variant in variants:
            p = (root / variant).resolve()
            if p.is_relative_to(vault) and p.is_file():
                found.append(p.relative_to(vault).as_posix())
        if found:
            return sorted(set(found))
    if wiki and '/' not in value:
        if file_index is not None:
            return sorted(set(file_index['name' if relative.suffix else 'stem'].get(value, [])))
        found = []
        for p in vault.rglob('*'):
            if not p.is_file() or excluded(p, vault, include_sensitive=True):
                continue
            match = p.name == value if relative.suffix else p.stem == value
            if match and p.resolve().is_relative_to(vault):
                found.append(p.resolve().relative_to(vault).as_posix())
        return sorted(set(found))
    return []


def note_targets(note, vault, raw):
    result = set()
    body = body_text(raw)
    for regex, wiki in ((MD_RE, False), (WIKI_RE, True)):
        for match in regex.finditer(body):
            candidates = resolve_target(note, vault, match.group(1).strip(), wiki)
            if len(candidates) == 1:
                result.add(candidates[0])
    return result
