"""Temporary-vault regression checks; never use a real Obsidian vault."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import linker
from scan_vault import scan_vault
from vault_paths import resolve_target


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ovm-regression-')
        self.root = Path(self.tmp.name).resolve()
        self.vault = self.root / 'vault'
        self.vault.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, name, text='plain text\n'):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return path

    def row(self, source='Source.md', target='Target.md', cluster='topic'):
        return dict(source=source, target=target, cluster=cluster, reason='explicit same project')

    def test_missing_markdown_and_encoded_target(self):
        self.note('Source.md', '[missing](Missing.md)\n[ok](a%20%23%25%28%29.md)')
        self.note('a #%().md')
        report = scan_vault(self.vault, False)
        self.assertEqual(report['broken_links'], [{'from': 'Source.md', 'link': 'Missing.md'}])
        self.assertEqual(report['incoming']['a #%().md'], ['Source.md'])
        self.assertNotIn('Missing.md', report['incoming'])

    def test_wiki_path_extension_and_ambiguity(self):
        self.note('Source.md', '[[a/Note]]\n[[Explicit.md]]\n[[Note]]')
        self.note('a/Note.md'); self.note('b/Note.md'); self.note('Explicit.md')
        report = scan_vault(self.vault, False)
        self.assertEqual(report['incoming'].get('a/Note.md'), ['Source.md'])
        self.assertNotIn('b/Note.md', report['incoming'])
        self.assertEqual(report['incoming']['Explicit.md'], ['Source.md'])
        self.assertEqual(len(report['ambiguous_links']), 1)

    def test_backslash_path_normalized(self):
        source = self.note('Source.md'); self.note('folder/Target.md')
        self.assertEqual(resolve_target(source, self.vault, 'folder\\Target.md'), ['folder/Target.md'])

    def test_exclusions_and_external_symlink(self):
        self.note('.hidden/Note.md'); self.note('password.md', 'private')
        self.note('credentials-list.md', 'password: actual-value')
        outside = self.root / 'outside.md'; outside.write_text('private')
        try:
            (self.vault / 'escape.md').symlink_to(outside)
        except OSError:
            pass
        report = scan_vault(self.vault, False)
        self.assertEqual(report['files'], [])
        for target in ['password.md', 'credentials-list.md', '../outside.md', '.hidden/Note.md']:
            self.note('Source.md')
            with self.assertRaises(ValueError):
                linker.build_plan(self.vault, {'links': [self.row(target=target)]})

    def test_existing_wiki_and_basename_collision(self):
        self.note('Source.md', '[[a/Note]]\n')
        self.note('a/Note.md'); self.note('b/Note.md')
        rows, _ = linker.build_plan(self.vault, {'links': [self.row(target='a/Note.md'), self.row(target='b/Note.md')]})
        self.assertEqual([r['target'] for r in rows], ['b/Note.md'])

    def test_limits_reject_before_write(self):
        source = self.note('Source.md')
        for i in range(9): self.note('T%d.md' % i)
        for count, separate_clusters in [(4, False), (9, True)]:
            rows = [self.row(target='T%d.md' % i, cluster=str(i) if separate_clusters else 'topic') for i in range(count)]
            with self.assertRaises(ValueError): linker.build_plan(self.vault, {'links': rows})
        self.assertEqual(source.read_text(), 'plain text\n')

    def invocation(self, apply=True):
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'links': [self.row()]}))
        args = ['linker.py', '--vault', str(self.vault), '--plan', str(plan), '--backup-root', str(self.root/'backup')]
        return args + (['--apply'] if apply else [])

    def test_apply_backup_preserve_and_repeat(self):
        source = self.note('Source.md', '---\nsummary: keep\n---\nBody  \n\n## 相关改编\nExisting\n')
        self.note('Target.md'); before = source.read_bytes()
        with patch.object(sys, 'argv', self.invocation()): linker.main()
        first = source.read_bytes()
        self.assertTrue(first.startswith(b'---\nsummary: keep\n---\nBody  \n'))
        self.assertTrue(first.endswith(b'## \xe7\x9b\xb8\xe5\x85\xb3\xe6\x94\xb9\xe7\xbc\x96\nExisting\n'))
        backup = list((self.root/'backup').rglob('Source.md'))
        self.assertEqual(len(backup), 1); self.assertEqual(backup[0].read_bytes(), before)
        with patch.object(sys, 'argv', self.invocation()): linker.main()
        self.assertEqual(source.read_bytes(), first)
        self.assertEqual(len(list((self.root/'backup').rglob('Source.md'))), 1)

    def test_validation_failure_rolls_back(self):
        source = self.note('Source.md'); self.note('Target.md'); before = source.read_bytes()
        real_scan = linker.scan_vault
        with patch.object(sys, 'argv', self.invocation()), patch.object(linker, 'scan_vault', side_effect=[real_scan(self.vault, False), RuntimeError('injected verification failure')]):
            with self.assertRaises(RuntimeError): linker.main()
        self.assertEqual(source.read_bytes(), before)
        record = json.loads((self.root/'plan.result.json').read_text())
        self.assertEqual(record['status'], 'failed'); self.assertEqual(record['rollback_conflicts'], [])

    def test_keep_zero_fails_without_backup(self):
        self.note('Source.md')
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS/'backup_vault.py'), '--vault', str(self.vault), '--files', 'Source.md', '--backup-dir', str(self.root/'backup'), '--keep', '0'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root/'backup').exists())

    def test_partial_backup_fails(self):
        self.note('Source.md')
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS/'backup_vault.py'), '--vault', str(self.vault), '--files', 'Source.md', 'Missing.md', '--backup-dir', str(self.root/'backup')], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list((self.root/'backup').rglob('Source.md'))), 1)

    def test_empty_apply_no_backup(self):
        plan = self.root/'empty.json'; plan.write_text('{"links": []}')
        with patch.object(sys, 'argv', ['linker.py', '--vault', str(self.vault), '--plan', str(plan), '--backup-root', str(self.root/'backup'), '--apply']): linker.main()
        self.assertFalse((self.root/'backup').exists())

    def test_dry_run_never_writes(self):
        source = self.note('Source.md'); self.note('Target.md'); before = source.read_bytes()
        with patch.object(sys, 'argv', self.invocation(False)): linker.main()
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.root/'backup').exists())
        self.assertFalse((self.root/'plan.result.json').exists())

    def test_rollback_preserves_later_edit(self):
        source = self.note('Source.md'); self.note('Target.md')
        original_scan = linker.scan_vault
        def verification(vault, sensitive):
            source.write_text('User edit after write')
            raise RuntimeError('injected concurrent edit')
        with patch.object(sys, 'argv', self.invocation()), patch.object(linker, 'scan_vault', side_effect=[original_scan(self.vault, False), None]) as mocked:
            mocked.side_effect = [original_scan(self.vault, False), RuntimeError('unused')]
            calls = [0]
            def scan(vault, sensitive):
                calls[0] += 1
                return original_scan(vault, sensitive) if calls[0] == 1 else verification(vault, sensitive)
            mocked.side_effect = scan
            with self.assertRaises(RuntimeError): linker.main()
        self.assertEqual(source.read_text(), 'User edit after write')
        record = json.loads((self.root/'plan.result.json').read_text())
        self.assertEqual(record['rollback_conflicts'], ['Source.md'])

    def test_backup_rotation_preserves_custom_directory(self):
        from backup_vault import prune_old_backups
        backup = self.root/'backups'; backup.mkdir()
        for folder in ['20260101-010000', 'links-20260102-010000', 'custom']:
            (backup/folder).mkdir()
        prune_old_backups(backup, 1)
        self.assertEqual(sorted(p.name for p in backup.iterdir()), ['custom', 'links-20260102-010000'])



if __name__ == '__main__':
    unittest.main()
