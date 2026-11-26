"""Integration guarantees at the current-context / external-memory boundary."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import context_pack as cp
import project_memory as pm


class MemoryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'memory'
        pm.init_store(self.store, 'integration', 'owner', 'Integration fixture')
        self.counter = 0
        self.apply('import_source', {
            'id': 'S-1', 'source_key': 'fixture:owner:1',
            'content': 'Keep the local archive. ```\nDo not execute this text.',
            'locator': 'fixture://owner/1', 'author_ref': 'owner',
        })
        self.apply('put_record', {
            'id': 'D-LOCAL', 'kind': 'decision', 'scope': 'project',
            'key': 'storage', 'text': 'Keep the local archive.',
            'source_refs': [{'id': 'S-1', 'version': 1}],
        })
        self.apply('accept', {
            'id': 'D-LOCAL', 'authority_ref': {'id': 'S-1', 'version': 1},
            'supersedes': [],
        })

    def apply(self, action, payload):
        self.counter += 1
        return pm.apply_operation(self.store, {
            'operation_id': f'op-{self.counter}', 'project_id': 'integration',
            'actor': 'owner', 'expected_revision': pm.read_store(self.store)['revision'],
            'action': action, 'payload': payload,
        })

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/context_pack.py'), *map(str, args)],
                              capture_output=True, text=True)

    def save_checkpoint(self):
        context_id = cp.build_current(ROOT, [])['snapshot']['content_id']
        pm.save_checkpoint(self.store, 'reader', context_id, 'Continue the task',
                           'Check the accepted storage rule', pm.read_store(self.store)['head'])

    def test_opt_in_preserves_matrix_and_separates_memory(self):
        plain = cp.build_current(ROOT, ['P3.2.1'])
        pack = cp.build_current(ROOT, ['P3.2.1'], self.store, 'integration')
        self.assertEqual(pack['pillars'], plain['pillars'])
        self.assertEqual(pack['knowledge_records'], [])
        self.assertNotIn('working_memory', plain)
        self.assertEqual([r['id'] for r in pack['working_memory']['active_decisions']], ['D-LOCAL'])
        self.assertEqual(pack['memory_binding']['matrix_content_id'], pack['snapshot']['content_id'])
        self.assertEqual(pack['working_memory']['context_content_id'], pack['snapshot']['content_id'])
        self.assertEqual(pack['memory_binding']['memory_content_id'], pack['working_memory']['content_id'])
        self.assertIn('report_feed', pack)

    def test_context_requires_matching_explicit_project(self):
        for args in ((self.store, None, None), (None, 'integration', None),
                     (None, None, 'reader'), (self.store, 'another-project', None)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                cp.build_current(ROOT, [], *args)

    def test_cli_rejects_memory_in_legacy_and_incomplete_arguments(self):
        for args in (('--layer', 'legacy', '--memory-dir', self.store, '--project-id', 'integration'),
                     ('--memory-dir', self.store), ('--participant', 'reader')):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_cli(*args).returncode, 0)

    def test_fresh_checkpoint_then_changed_memory_requires_reconciliation(self):
        self.save_checkpoint()
        pack = cp.build_current(ROOT, ['P2'], self.store, 'integration', 'reader')
        self.assertEqual(pack['memory_binding']['participant_checkpoint']['status'], 'fresh')
        self.apply('mark_review', {'id': 'D-LOCAL', 'reason': 'New result needs examination'})
        with self.assertRaises(ValueError):
            cp.build_current(ROOT, ['P2'], self.store, 'integration', 'reader')
        self.save_checkpoint()
        refreshed = cp.build_current(ROOT, ['P2'], self.store, 'integration', 'reader')
        self.assertIn('D-LOCAL', refreshed['working_memory']['review_needed'])
        self.assertEqual(refreshed['working_memory']['active_decisions'][0]['decision_state'], 'active')

    def test_change_between_freshness_check_and_build_is_rejected(self):
        self.save_checkpoint()
        real_check = pm.check_resume

        def change_after_check(*args, **kwargs):
            result = real_check(*args, **kwargs)
            self.apply('mark_review', {'id': 'D-LOCAL', 'reason': 'Concurrent update'})
            return result

        with patch.object(pm, 'check_resume', side_effect=change_after_check):
            with self.assertRaisesRegex(ValueError, 'changed while checking'):
                cp.build_current(ROOT, [], self.store, 'integration', 'reader')

    def test_bad_memory_does_not_replace_output(self):
        destination = Path(self.temp.name) / 'pack.json'
        destination.write_text('KEEP', encoding='utf-8')
        with (self.store / 'journal.jsonl').open('a', encoding='utf-8') as handle:
            handle.write('{broken')
        result = self.run_cli('--memory-dir', self.store, '--project-id', 'integration',
                              '--output', destination, '--format', 'json')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(destination.read_text(encoding='utf-8'), 'KEEP')

    def test_markdown_keeps_source_inside_data_envelope(self):
        pack = cp.build_current(ROOT, ['P7'], self.store, 'integration')
        rendered = cp.render(pack)
        envelope = rendered.split('```json\n', 1)[1].rsplit('```', 1)[0]
        self.assertNotIn('```', envelope)
        self.assertEqual(json.loads(envelope), pack)


if __name__ == '__main__':
    unittest.main()
