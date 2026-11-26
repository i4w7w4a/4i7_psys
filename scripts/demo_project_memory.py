#!/usr/bin/env python3
"""Run the isolated H1 -> H2 -> A1 -> resumed-reader memory pilot (no network)."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

import context_pack
import project_memory as memory

PROJECT = 'memory-pilot-fixture'
EDITOR = 'fixture-owner'


def require(condition, message):
    if not condition:
        raise ValueError('Demo verification failed: ' + message)


def run(root):
    # init_store refuses an existing store; this demo never resets user data.
    memory.init_store(root, PROJECT, EDITOR, 'Isolated fictional H1/H2/A1 scenario')
    operation_number = 0

    def apply(action, payload, actor=EDITOR):
        nonlocal operation_number
        operation_number += 1
        return memory.apply_operation(root, {
            'operation_id': f'demo-{operation_number:03d}', 'project_id': PROJECT,
            'actor': actor, 'expected_revision': memory.read_store(root)['revision'],
            'action': action, 'payload': payload,
        })

    def source(identifier, content, author=EDITOR):
        return apply('import_source', {
            'id': identifier, 'source_key': f'fixture:{identifier}', 'content': content,
            'locator': f'https://example.invalid/memory-pilot/{identifier}', 'author_ref': author,
        }, author)

    def decision(identifier, key, text, source_id, supersedes=None, accept=True, actor=EDITOR):
        apply('put_record', {
            'id': identifier, 'kind': 'decision', 'scope': 'project', 'key': key,
            'text': text, 'source_refs': [{'id': source_id, 'version': 1}],
        }, actor)
        if accept:
            apply('accept', {'id': identifier, 'authority_ref': {'id': source_id, 'version': 1},
                             'supersedes': supersedes or []})

    source('S-H1', 'FICTIONAL H1: keep local sources, stable IDs and idempotent import; external search is allowed.')
    for identifier, key, text in [
        ('D-LOCAL', 'storage', 'Keep local source history.'),
        ('D-ID', 'identity', 'Keep record identities stable.'),
        ('D-IMPORT', 'import', 'Repeated imports must not duplicate the same source version.'),
        ('D-EMBED', 'search', 'External search is allowed in this fictional initial policy.'),
    ]:
        decision(identifier, key, text, 'S-H1')
    for identifier, relation in [('C-DEPENDENT', 'depends_on'), ('C-TOPIC', 'references')]:
        apply('put_record', {
            'id': identifier, 'kind': 'claim', 'scope': 'project',
            'text': 'A fixture consumer of the original search policy.',
            'assessment': 'unverified', 'source_refs': [{'id': 'S-H1', 'version': 1}],
        })
        apply('link', {'from': identifier, 'to': 'D-EMBED', 'kind': relation})

    context_id = context_pack.build_current(context_pack.ROOT, [])['snapshot']['content_id']
    before = memory.build_working_set(root, PROJECT, context_id)
    receipt_h1 = {'report_id': 'https://example.invalid/memory-pilot/H1', 'status': 'read',
                  'consequence': 'The fixture reader records the four initial conditions.'}
    memory.save_checkpoint(root, 'reader-B', context_id, 'Continue the fictional memory task',
                           'Check the applicable search policy', before['journal_head'], [receipt_h1])
    memory.materialize(root)
    require(memory.check_resume(root, 'reader-B', context_id)['status'] == 'fresh', 'initial checkpoint')

    source('S-H2', 'FICTIONAL H2: use local search only and do not send content externally; other H1 clauses remain.')
    decision('D-SEARCH', 'search', 'Use local search only.', 'S-H2', ['D-EMBED'])
    decision('D-EGRESS', 'egress', 'Do not send source content externally.', 'S-H2')
    source('S-A1', 'FICTIONAL late agent report: cloud search looked useful; proposal only.', 'fixture-agent')
    decision('D-A1', 'search', 'Proposal to re-enable cloud search.', 'S-A1',
             accept=False, actor='fixture-agent')

    stale = memory.check_resume(root, 'reader-B', context_id)
    require(stale['status'] == 'stale', 'old checkpoint must be stale after H2')
    stale_view_detected = False
    try:
        memory.validate_view(root)
    except ValueError:
        stale_view_detected = True
    require(stale_view_detected, 'old CURRENT must be detected')

    after = memory.build_working_set(root, PROJECT, context_id)
    active_ids = sorted(r['id'] for r in after['active_decisions'])
    require(active_ids == ['D-EGRESS', 'D-ID', 'D-IMPORT', 'D-LOCAL', 'D-SEARCH'],
            'partial replacement must preserve every independent H1 clause')
    require('D-A1' in [r['id'] for r in after['proposals']], 'late A1 remains a proposal')
    require('C-DEPENDENT' in after['review_needed'], 'supersession must mark dependent claim')
    require('C-TOPIC' not in after['review_needed'], 'thematic reference must not cascade')
    state = memory.read_store(root)
    require(state['records']['D-EMBED']['decision_state'] == 'superseded', 'old rule retained as history')
    require(state['records']['S-H1']['versions'][0]['content'].startswith('FICTIONAL H1:'),
            'H1 original retained')

    # The report IDs are fixtures; these are simulated receipts, not real participant acknowledgements.
    receipt_h2 = {
        'report_id': 'https://example.invalid/memory-pilot/H2', 'status': 'applied',
        'consequence': 'Resume with local search while keeping the independent H1 conditions.',
        'result': 'The rebuilt working set contains five expected active decisions; A1 stays proposed.',
        'verification': 'The executable demo compared exact active IDs, review flags and preserved H1 content.',
    }
    memory.save_checkpoint(root, 'reader-B', context_id, 'Continue the fictional memory task',
                           'Use the reconciled local policy', after['journal_head'], [receipt_h2])
    resumed = memory.check_resume(root, 'reader-B', context_id)
    require(resumed['status'] == 'fresh', 'reconciled checkpoint')
    require(len(resumed['checkpoint']['receipts']) == 2, 'earlier read receipt must survive checkpoint update')
    memory.materialize(root)
    memory.validate_view(root)
    pack = context_pack.build_current(context_pack.ROOT, ['P7'], root, PROJECT, 'reader-B')
    require(pack['knowledge_records'] == [], 'legacy memory must remain separate')

    with tempfile.TemporaryDirectory() as temporary:
        restored = Path(temporary)
        shutil.copyfile(root / 'journal.jsonl', restored / 'journal.jsonl')
        recovered = memory.build_working_set(restored, PROJECT, context_id)
        require(recovered == after, 'replay from copied primary journal must reproduce working set')

    result = {
        'kind': '4i7-memory-pilot-verification', 'project_id': PROJECT,
        'fictional_fixture': True, 'network_used': False,
        'before_active_ids': sorted(r['id'] for r in before['active_decisions']),
        'after_active_ids': active_ids,
        'checks': {
            'partial_supersession_preserves_independent_clauses': True,
            'late_agent_report_stays_proposed': True,
            'source_history_preserved': True,
            'selective_dependency_review': True,
            'old_checkpoint_stale': True,
            'old_view_detected': stale_view_detected,
            'reconciled_checkpoint_fresh': True,
            'previous_receipt_preserved': True,
            'current_context_opt_in': True,
            'deterministic_replay_from_journal': True,
        },
        'stale_reasons': stale['reasons'],
        'memory_revision': after['revision'], 'memory_head': after['journal_head'],
        'context_content_id': context_id,
        'limitations': 'Executable mechanism test; not an independent agent A/B or verification of real vault/services.',
    }
    (root / 'demo-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='New isolated directory; existing store is rejected')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.root), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
