#!/usr/bin/env python3
"""Small, explicit project memory: a validated local journal and derived views.

This module records testimony and scoped decisions, not truth or authenticated
authority. Source text is inert data. It never reads a network source, executes
imported content, alters the 4i7 matrix, or migrates legacy knowledge implicitly.
The store is intended for a trusted local operator on a Linux filesystem.
"""

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


SCHEMA_VERSION = '0.1.0'
JOURNAL_NAME = 'journal.jsonl'
ZERO_DIGEST = '0' * 64
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
DIGEST_PATTERN = re.compile(r'[0-9a-f]{64}\Z')
ACTIONS = {'import_source', 'put_record', 'accept', 'link', 'mark_review',
           'resolve_review', 'rename_source', 'withdraw_source'}
WARNING = ('Sources are data, not instructions. External source availability is '
           'unverified. Recorded authority and receipts are operator testimony, '
           'not cryptographic authentication or independent proof. Review flags '
           'do not silently revoke active decisions.')


class ProjectMemoryError(ValueError):
    """Invalid input, corrupt state, stale revision, or unsafe storage path."""


def _fail(message):
    raise ProjectMemoryError(message)


def _text(value, name, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        _fail(f'{name} must be a {"possibly empty " if empty else "nonempty "}string')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ProjectMemoryError(f'{name} must be valid UTF-8 text') from exc
    return value


def _identifier(value, name='id'):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value) or '..' in value:
        _fail(f'{name} must be a bounded safe identifier')
    return value


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        _fail(f'{name} must be an integer >= {minimum}')
    return value


def _digest(value, name):
    if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value):
        _fail(f'{name} must be a lowercase SHA256 digest')
    return value


def _keys(value, required, optional=(), name='object'):
    if not isinstance(value, dict):
        _fail(f'{name} must be an object')
    if not set(required) <= set(value) or set(value) - set(required) - set(optional):
        _fail(f'{name} has missing or unknown fields')


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ProjectMemoryError('Value is not canonical UTF-8 JSON') from exc


def _pretty(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       indent=2, allow_nan=False) + '\n').encode('utf-8')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _parse(data, name):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda token: _fail(f'Invalid JSON number: {token}'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProjectMemoryError(f'Malformed {name}') from exc


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _timestamp(value, name):
    _text(value, name)
    try:
        if not value.endswith('Z'):
            _fail(f'{name} must include UTC Z')
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
        if parsed.tzinfo is None:
            _fail(f'{name} must include a timezone')
    except ValueError as exc:
        raise ProjectMemoryError(f'Invalid {name}') from exc
    return value


def _safe_directory(path, create=False):
    if not isinstance(path, Path):
        _fail('Store root must be a pathlib.Path')
    path = Path(os.path.abspath(path))
    try:
        for component in reversed((path, *path.parents)):
            if component.is_symlink():
                _fail(f'Symlink directory rejected: {component}')
            if component.exists() and not component.is_dir():
                _fail(f'Not a directory: {component}')
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            _fail(f'Missing store directory: {path}')
    except OSError as exc:
        raise ProjectMemoryError(f'Cannot access directory: {path}') from exc
    return path


def _safe_file(path, allow_missing=False):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if allow_missing:
            return
        _fail(f'Missing file: {path}')
    except OSError as exc:
        raise ProjectMemoryError(f'Cannot inspect file: {path}') from exc
    if not stat.S_ISREG(mode):
        _fail(f'Only regular nonsymlink files are allowed: {path}')


def _read_bytes(path):
    _safe_file(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail(f'Not a regular file: {path}')
            return stream.read()
    except OSError as exc:
        raise ProjectMemoryError(f'Cannot read file safely: {path}') from exc


def _atomic_write(path, data):
    _safe_directory(path.parent)
    _safe_file(path, allow_missing=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.memory-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe_file(path, allow_missing=True)
        os.replace(temporary, path)
        temporary = None
        # Best-effort directory durability. Atomic replacement already succeeded;
        # a platform not supporting directory fsync must not report a failed write.
        try:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    except OSError as exc:
        raise ProjectMemoryError(f'Atomic write failed: {path}') from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


@contextmanager
def _locked(root, create=False):
    root = _safe_directory(root, create=create)
    lock_path = root / '.memory.lock'
    _safe_file(lock_path, allow_missing=True)
    descriptor = None
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail('Invalid store lock file')
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield root
    except OSError as exc:
        raise ProjectMemoryError('Cannot lock or access memory store') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_operation(operation, allow_init=False):
    _keys(operation, {'operation_id', 'project_id', 'actor', 'expected_revision', 'action', 'payload'},
          name='operation')
    for key in ('operation_id', 'project_id', 'actor'):
        _identifier(operation[key], key)
    _integer(operation['expected_revision'], 'expected_revision')
    action = operation['action']
    if not isinstance(action, str) or action not in ACTIONS | ({'init'} if allow_init else set()):
        _fail('Unknown operation action')
    if not isinstance(operation['payload'], dict):
        _fail('Operation payload must be an object')
    _canonical(operation)


def _record(state, identifier, kind=None):
    _identifier(identifier)
    record = state['records'].get(identifier)
    if record is None or (kind is not None and record['kind'] != kind):
        _fail(f'Missing record or wrong kind: {identifier}')
    return record


def _source_ref(state, reference):
    _keys(reference, {'id', 'version'}, name='source reference')
    source = _record(state, reference['id'], 'source')
    version = _integer(reference['version'], 'source version', 1)
    if version > len(source['versions']):
        _fail(f'Unknown source version: {reference}')
    return copy.deepcopy(reference)


def _source_refs(state, references):
    if not isinstance(references, list):
        _fail('source_refs must be a list')
    result = [_source_ref(state, reference) for reference in references]
    if len({(r['id'], r['version']) for r in result}) != len(result):
        _fail('Duplicate source reference')
    return result


def _editor(state, actor):
    if actor != state['editor']:
        _fail('This action requires the configured editor')


def _dependencies(state):
    result = {identifier: set() for identifier in state['records']}
    for identifier, record in state['records'].items():
        result[identifier].update(r['id'] for r in record.get('source_refs', []))
        result[identifier].update(r['id'] for r in record.get('review_source_refs', []))
        # Acceptance evidence is also a dependency, pinned to its source version.
        if record.get('authority_ref'):
            result[identifier].add(record['authority_ref']['id'])
    for relation in state['relations']:
        if relation['kind'] == 'depends_on':
            result[relation['from']].add(relation['to'])
    return result


def _check_cycles(state):
    dependencies = _dependencies(state)
    remaining = {identifier: len(bases) for identifier, bases in dependencies.items()}
    consumers = {identifier: set() for identifier in dependencies}
    for identifier, bases in dependencies.items():
        for basis in bases:
            consumers[basis].add(identifier)
    pending = [identifier for identifier, count in remaining.items() if not count]
    visited = 0
    while pending:
        basis = pending.pop()
        visited += 1
        for consumer in consumers[basis]:
            remaining[consumer] -= 1
            if remaining[consumer] == 0:
                pending.append(consumer)
    if visited != len(dependencies):
        _fail('Dependency cycle rejected')


def _mark_records(state, seeds, reason, timestamp, include_seeds=True):
    dependencies = _dependencies(state)
    reached, pending = set(seeds), list(seeds)
    while pending:
        basis = pending.pop()
        for consumer, inputs in dependencies.items():
            if basis in inputs and consumer not in reached:
                reached.add(consumer)
                pending.append(consumer)
    targets = reached if include_seeds else reached - set(seeds)
    for identifier in sorted(targets):
        record = state['records'][identifier]
        record['review_needed'] = True
        record.setdefault('review_history', []).append(
            {'action': 'mark', 'reason': reason, 'recorded_at': timestamp})
    return sorted(targets)


def _source_problems(state, references, note_newer=True):
    problems = []
    for reference in references:
        source = state['records'][reference['id']]
        version = source['versions'][reference['version'] - 1]
        if version['availability'] == 'withdrawn':
            problems.append(f'{source["id"]}:v{reference["version"]} is withdrawn')
        if note_newer and reference['version'] != source['latest_version']:
            problems.append(f'{source["id"]}:v{reference["version"]} has a newer version')
        if source['review_needed']:
            problems.append(f'{source["id"]} needs review')
    return problems


def _inherit_review(state, identifier, timestamp):
    """New dependencies must not conceal an already known reason for review."""
    record = state['records'][identifier]
    references = record.get('source_refs', []) + record.get('review_source_refs', [])
    if record.get('authority_ref'):
        references = references + [record['authority_ref']]
    problems = _source_problems(state, references)
    for basis_id in _dependencies(state)[identifier]:
        basis = state['records'][basis_id]
        if basis['review_needed']:
            problems.append(f'{basis_id} needs review')
        if basis.get('assessment') == 'refuted':
            problems.append(f'{basis_id} is assessed as refuted')
        if basis.get('decision_state') == 'superseded':
            problems.append(f'{basis_id} is superseded')
        if basis['kind'] == 'source' and basis['versions'][-1]['availability'] == 'withdrawn':
            problems.append(f'{basis_id} latest version is withdrawn')
    if problems:
        return _mark_records(state, [identifier], 'Dependency review: ' + '; '.join(sorted(set(problems))),
                             timestamp)
    return []


def _import_source(state, payload, timestamp, actor):
    _keys(payload, {'id', 'source_key', 'content', 'locator', 'author_ref'}, {'source_created_at'},
          name='import_source payload')
    identifier = _identifier(payload['id'])
    for field in ('source_key', 'locator', 'author_ref'):
        _text(payload[field], field)
    content = _text(payload['content'], 'content', empty=True)
    original_at = payload.get('source_created_at')
    if original_at is not None:
        _text(original_at, 'source_created_at')
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    existing_id = state['records'].get(identifier)
    by_key = next((r for r in state['records'].values()
                   if r['kind'] == 'source' and r['source_key'] == payload['source_key']), None)
    if existing_id is not None and (existing_id['kind'] != 'source'
                                   or existing_id['source_key'] != payload['source_key']):
        _fail('Source ID already belongs to another identity')
    source = by_key
    if source is None:
        source = {'id': identifier, 'kind': 'source', 'source_key': payload['source_key'],
                  'locator': payload['locator'], 'versions': [], 'review_needed': False,
                  'created_at': timestamp, 'created_by': actor}
        state['records'][identifier] = source
    for version in source['versions']:
        if version['digest'] == digest:
            return {'id': source['id'], 'version': version['version'], 'digest': digest, 'reused': True}
    number = len(source['versions']) + 1
    source['versions'].append({'version': number, 'digest': digest, 'content': content,
                               'locator': payload['locator'], 'author_ref': payload['author_ref'],
                               'source_created_at': original_at, 'received_at': timestamp,
                               'availability': 'available'})
    source['latest_version'] = number
    reviewed = []
    if number > 1:
        reviewed = _mark_records(state, [source['id']],
                                 f'Source {source["id"]} has a new version {number}', timestamp,
                                 include_seeds=False)
    return {'id': source['id'], 'version': number, 'digest': digest, 'reused': False,
            'review_needed': reviewed}


def _put_record(state, payload, timestamp, actor):
    kind = payload.get('kind')
    if kind not in ('claim', 'decision', 'review-anchor'):
        _fail('put_record kind must be claim, decision, or review-anchor')
    required = {'id', 'kind', 'scope', 'text', 'source_refs'}
    required |= {'claim': {'assessment'}, 'decision': {'key'},
                 'review-anchor': {'trigger', 'exit_criteria', 'reopen_if'}}[kind]
    _keys(payload, required, {'title'}, name='put_record payload')
    identifier = _identifier(payload['id'])
    if identifier in state['records']:
        _fail('Record ID already exists; create a new record and an explicit relationship')
    for field in required - {'id', 'kind', 'source_refs'}:
        _text(payload[field], field)
    if 'title' in payload:
        _text(payload['title'], 'title', empty=True)
    references = _source_refs(state, payload['source_refs'])
    record = copy.deepcopy(payload)
    record.update(source_refs=references, created_at=timestamp, created_by=actor, review_needed=False)
    if kind == 'claim':
        if payload['assessment'] not in {'unverified', 'supported', 'refuted'}:
            _fail('Unknown claim assessment')
    elif kind == 'decision':
        record['decision_state'] = 'proposed'
    else:
        record['anchor_state'] = 'open'
    state['records'][identifier] = record
    _check_cycles(state)
    return {'id': identifier, 'review_needed': _inherit_review(state, identifier, timestamp)}


def _accept(state, payload, timestamp, actor):
    _keys(payload, {'id', 'authority_ref', 'supersedes'}, name='accept payload')
    _editor(state, actor)
    decision = _record(state, payload['id'], 'decision')
    if decision['decision_state'] != 'proposed':
        _fail('Only a proposed decision can be accepted')
    authority = _source_ref(state, payload['authority_ref'])
    supersedes = payload['supersedes']
    if not isinstance(supersedes, list):
        _fail('supersedes must be a list')
    for identifier in supersedes:
        _identifier(identifier)
    if len(set(supersedes)) != len(supersedes):
        _fail('Duplicate superseded ID')
    existing = sorted(r['id'] for r in state['records'].values()
                      if r['kind'] == 'decision' and r['decision_state'] == 'active'
                      and (r['scope'], r['key']) == (decision['scope'], decision['key']))
    if sorted(supersedes) != existing:
        _fail('Name exactly the active decisions with the same scope and key in supersedes')
    for identifier in supersedes:
        previous = _record(state, identifier, 'decision')
        previous['decision_state'] = 'superseded'
        previous['superseded_by'] = decision['id']
        state['relations'].append({'from': decision['id'], 'to': identifier, 'kind': 'supersedes'})
    decision.update(decision_state='active', authority_ref=authority,
                    supersedes=copy.deepcopy(supersedes), accepted_at=timestamp, accepted_by=actor)
    _check_cycles(state)
    reviewed = _mark_records(state, supersedes,
                             f'Decision replaced by {decision["id"]}', timestamp, include_seeds=False)
    reviewed = sorted(set(reviewed) | set(_inherit_review(state, decision['id'], timestamp)))
    return {'id': decision['id'], 'supersedes': copy.deepcopy(supersedes), 'review_needed': reviewed}


def _apply(state, operation, timestamp):
    action, payload, actor = operation['action'], operation['payload'], operation['actor']
    if operation['project_id'] != state['project_id']:
        _fail('Operation belongs to a different project')
    if operation['expected_revision'] != state['revision']:
        _fail('Stale expected_revision')
    if action == 'import_source':
        return _import_source(state, payload, timestamp, actor)
    if action == 'put_record':
        return _put_record(state, payload, timestamp, actor)
    if action == 'accept':
        return _accept(state, payload, timestamp, actor)
    if action == 'link':
        _keys(payload, {'from', 'to', 'kind'}, name='link payload')
        _record(state, payload['from'])
        _record(state, payload['to'])
        if payload['kind'] not in ('references', 'depends_on', 'contradicts'):
            _fail('Unknown link kind; supersedes links are generated by accept only')
        if payload['kind'] == 'depends_on' and payload['from'] == payload['to']:
            _fail('Self dependency rejected')
        reused = payload in state['relations']
        if not reused:
            state['relations'].append(copy.deepcopy(payload))
        _check_cycles(state)
        reviewed = _inherit_review(state, payload['from'], timestamp) if payload['kind'] == 'depends_on' else []
        return {'relation': copy.deepcopy(payload), 'reused': reused, 'review_needed': reviewed}
    if action == 'mark_review':
        _keys(payload, {'id', 'reason'}, name='mark_review payload')
        _record(state, payload['id'])
        _text(payload['reason'], 'reason')
        return {'review_needed': _mark_records(state, [payload['id']], payload['reason'], timestamp)}
    if action == 'resolve_review':
        _keys(payload, {'id', 'reason', 'source_refs'}, {'anchor_state', 'assessment'},
              name='resolve_review payload')
        _editor(state, actor)
        record = _record(state, payload['id'])
        _text(payload['reason'], 'reason')
        references = _source_refs(state, payload['source_refs'])
        if not references:
            _fail('Resolving review requires explicit source evidence')
        if _source_problems(state, references, note_newer=False):
            _fail('Resolving review requires available evidence without an open source review')
        old_assessment, old_anchor_state = record.get('assessment'), record.get('anchor_state')
        if 'anchor_state' in payload:
            if record['kind'] != 'review-anchor' or payload['anchor_state'] not in ('open', 'closed'):
                _fail('anchor_state applies only to anchors and must be open or closed')
            record['anchor_state'] = payload['anchor_state']
        if 'assessment' in payload:
            if record['kind'] != 'claim' or payload['assessment'] not in ('unverified', 'supported', 'refuted'):
                _fail('assessment applies only to claims')
            record['assessment'] = payload['assessment']
        record['review_needed'] = False
        record.setdefault('review_history', []).append(
            {'action': 'resolve', 'reason': payload['reason'], 'source_refs': references,
             'recorded_at': timestamp, 'actor': actor})
        # Resolution evidence remains a dependency without replacing original evidence.
        record['review_source_refs'] = references
        _check_cycles(state)
        changed = (old_assessment != record.get('assessment') or old_anchor_state != record.get('anchor_state'))
        consumers = _mark_records(state, [record['id']],
                                  f'Assessment or anchor state changed: {record["id"]}', timestamp,
                                  include_seeds=False) if changed else []
        return {'id': record['id'], 'review_needed': False, 'dependent_review_needed': consumers}
    if action == 'rename_source':
        _keys(payload, {'id', 'locator'}, name='rename_source payload')
        _editor(state, actor)
        source = _record(state, payload['id'], 'source')
        _text(payload['locator'], 'locator')
        source.setdefault('locator_history', []).append(
            {'previous': source['locator'], 'locator': payload['locator'], 'recorded_at': timestamp})
        source['locator'] = payload['locator']
        return {'id': source['id'], 'locator': source['locator']}
    if action == 'withdraw_source':
        _keys(payload, {'id', 'version', 'reason'}, name='withdraw_source payload')
        _editor(state, actor)
        reference = _source_ref(state, {'id': payload['id'], 'version': payload['version']})
        _text(payload['reason'], 'reason')
        source = state['records'][reference['id']]
        version = source['versions'][reference['version'] - 1]
        if version['availability'] == 'withdrawn':
            _fail('Source version is already withdrawn')
        version.update(availability='withdrawn', withdrawn_at=timestamp, withdrawal_reason=payload['reason'])
        reviewed = _mark_records(state, [source['id']], payload['reason'], timestamp, include_seeds=False)
        return {'id': source['id'], 'version': reference['version'], 'review_needed': reviewed}
    _fail('Unknown operation action')


def _event(operation, revision, previous_digest):
    event = {'schema_version': SCHEMA_VERSION, 'revision': revision,
             'previous_digest': previous_digest, 'recorded_at': _now(), 'operation': operation}
    event['digest'] = hashlib.sha256(_canonical(event)).hexdigest()
    return event


def _replay(raw):
    if not raw or not raw.endswith(b'\n'):
        _fail('Journal is empty or has a truncated final line')
    state, operations = None, {}
    previous = ZERO_DIGEST
    for revision, line in enumerate(raw.split(b'\n')[:-1], 1):
        event = _parse(line, 'journal event')
        _keys(event, {'schema_version', 'revision', 'previous_digest', 'recorded_at', 'operation', 'digest'},
              name='journal event')
        if event['schema_version'] != SCHEMA_VERSION:
            _fail('Unsupported journal schema version')
        _integer(event['revision'], 'event revision', 1)
        if event['revision'] != revision or event['previous_digest'] != previous:
            _fail('Broken revision or previous-digest chain')
        _digest(event['digest'], 'event digest')
        digest = hashlib.sha256(_canonical({k: v for k, v in event.items() if k != 'digest'})).hexdigest()
        if event['digest'] != digest or line != _canonical(event):
            _fail('Journal event hash or canonical encoding mismatch')
        _timestamp(event['recorded_at'], 'event recorded_at')
        operation = event['operation']
        _validate_operation(operation, allow_init=revision == 1)
        operation_id = operation['operation_id']
        if operation_id in operations:
            _fail('Duplicate operation ID in journal')
        if revision == 1:
            if operation['action'] != 'init' or operation['expected_revision'] != 0:
                _fail('Journal must start with an initialization event')
            payload = operation['payload']
            _keys(payload, {'editor', 'title'}, name='init payload')
            _identifier(payload['editor'], 'editor')
            _text(payload['title'], 'title', empty=True)
            if operation['actor'] != payload['editor']:
                _fail('Genesis actor must match configured editor')
            state = {'schema_version': SCHEMA_VERSION, 'project_id': operation['project_id'],
                     'editor': payload['editor'], 'title': payload['title'], 'revision': 0,
                     'head': ZERO_DIGEST, 'records': {}, 'relations': []}
            result = {'project_id': state['project_id']}
        else:
            result = _apply(state, operation, event['recorded_at'])
        state['revision'], state['head'] = revision, digest
        previous = digest
        operations[operation_id] = {'envelope': _canonical(operation), 'result': copy.deepcopy(result)}
    return state, operations


def _load(root):
    root = _safe_directory(root)
    raw = _read_bytes(root / JOURNAL_NAME)
    state, operations = _replay(raw)
    return raw, state, operations


def init_store(root, project_id, editor, title=''):
    """Create a new store; refuse to overwrite any existing journal."""
    _identifier(project_id, 'project_id')
    _identifier(editor, 'editor')
    _text(title, 'title', empty=True)
    with _locked(root, create=True) as directory:
        path = directory / JOURNAL_NAME
        _safe_file(path, allow_missing=True)
        if path.exists():
            _fail('Store is already initialized')
        operation = {'operation_id': 'genesis', 'project_id': project_id, 'actor': editor,
                     'expected_revision': 0, 'action': 'init', 'payload': {'editor': editor, 'title': title}}
        raw = _canonical(_event(operation, 1, ZERO_DIGEST)) + b'\n'
        state, _ = _replay(raw)
        _atomic_write(path, raw)
        return state


def read_store(root):
    """Replay a single immutable read of the journal; never repair or skip a tail."""
    return _load(root)[1]


def apply_operation(root, operation):
    """Validate and atomically append under lock, with exact operation retries."""
    _validate_operation(operation)
    operation = copy.deepcopy(operation)
    with _locked(root) as directory:
        raw, state, operations = _load(directory)
        if operation['project_id'] != state['project_id']:
            _fail('Operation belongs to a different project')
        previous = operations.get(operation['operation_id'])
        if previous:
            if previous['envelope'] != _canonical(operation):
                _fail('Operation ID was already used with a different envelope')
            return {'revision': state['revision'], 'head': state['head'],
                    'result': copy.deepcopy(previous['result']), 'state': state, 'idempotent': True}
        if operation['expected_revision'] != state['revision']:
            _fail('Stale expected_revision')
        event = _event(operation, state['revision'] + 1, state['head'])
        updated = raw + _canonical(event) + b'\n'
        new_state, new_operations = _replay(updated)
        _atomic_write(directory / JOURNAL_NAME, updated)
        return {'revision': new_state['revision'], 'head': new_state['head'],
                'result': new_operations[operation['operation_id']]['result'],
                'state': new_state, 'idempotent': False}


def _working_set(raw, state, context_content_id):
    if context_content_id is not None:
        _text(context_content_id, 'context_content_id')
    journal_digest = hashlib.sha256(raw).hexdigest()
    module_digest = hashlib.sha256(_read_bytes(Path(__file__).absolute())).hexdigest()
    identity = {'schema_version': SCHEMA_VERSION, 'projection_version': '1',
                'journal_sha256': journal_digest, 'module_sha256': module_digest}
    records = [copy.deepcopy(state['records'][identifier]) for identifier in sorted(state['records'])]
    return {'schema_version': SCHEMA_VERSION, 'project_id': state['project_id'],
            'revision': state['revision'], 'journal_head': state['head'],
            'content_id': hashlib.sha256(_canonical(identity)).hexdigest(),
            'context_content_id': context_content_id,
            'active_decisions': [r for r in records if r.get('decision_state') == 'active'],
            'claims': [r for r in records if r['kind'] == 'claim'],
            'proposals': [r for r in records if r.get('decision_state') == 'proposed'],
            'historical_decisions': [r for r in records if r.get('decision_state') == 'superseded'],
            'open_anchors': [r for r in records if r.get('anchor_state') == 'open'],
            'closed_anchors': [r for r in records if r.get('anchor_state') == 'closed'],
            'sources': [r for r in records if r['kind'] == 'source'],
            'relations': copy.deepcopy(state['relations']),
            'review_needed': [r['id'] for r in records if r.get('review_needed')],
            'provenance': {**identity, 'journal_path': JOURNAL_NAME,
                           'module_path': 'scripts/project_memory.py',
                           'source_digest_kind': 'sha256 of exact UTF-8 content bytes',
                           'sources_are_data': True, 'external_source_availability': 'unverified'},
            'warning': WARNING}


def build_working_set(root, project_id, context_content_id=None):
    _identifier(project_id, 'project_id')
    raw, state, _ = _load(root)
    if project_id != state['project_id']:
        _fail('Requested project does not match memory store')
    return _working_set(raw, state, context_content_id)


def _searchable(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return '\n'.join(_searchable(v) for v in value.values())
    if isinstance(value, list):
        return '\n'.join(_searchable(v) for v in value)
    return ''


def search_records(root, project_id, query):
    _identifier(project_id, 'project_id')
    _text(query, 'query', empty=True)
    state = read_store(root)
    if project_id != state['project_id']:
        _fail('Requested project does not match memory store')
    return [copy.deepcopy(state['records'][identifier]) for identifier in sorted(state['records'])
            if query.casefold() in _searchable(state['records'][identifier]).casefold()]


def _view_bytes(working_set):
    data = _pretty(working_set)
    markdown = ('# Project working memory\n\n' + WARNING + '\n\n'
                + 'Generated from the validated journal. Edit through operations; rebuild this view.\n\n'
                + '```json\n' + data.decode('utf-8').replace('`', '\\u0060') + '```\n')
    return data, markdown.encode('utf-8')


def materialize(root):
    """Rebuild disposable views. The pair is checked together by validate_view."""
    with _locked(root) as directory:
        raw, state, _ = _load(directory)
        working_set = _working_set(raw, state, None)
        data, markdown = _view_bytes(working_set)
        for name in ('CURRENT.json', 'CURRENT.md'):
            _safe_file(directory / name, allow_missing=True)
        _atomic_write(directory / 'CURRENT.json', data)
        _atomic_write(directory / 'CURRENT.md', markdown)
        return {'revision': state['revision'], 'head': state['head'],
                'content_id': working_set['content_id'],
                'paths': ['CURRENT.json', 'CURRENT.md']}


def validate_view(root):
    directory = _safe_directory(root)
    raw, state, _ = _load(directory)
    working_set = _working_set(raw, state, None)
    expected = _view_bytes(working_set)
    for name, data in zip(('CURRENT.json', 'CURRENT.md'), expected):
        if _read_bytes(directory / name) != data:
            _fail(f'{name} is stale, ahead, or modified; rebuild with materialize')
    return {'valid': True, 'revision': state['revision'], 'head': state['head'],
            'content_id': working_set['content_id']}


def _receipts(value):
    if not isinstance(value, list):
        _fail('receipts must be a list')
    seen = set()
    result = []
    for receipt in value:
        _keys(receipt, {'report_id', 'status', 'consequence'}, {'result', 'verification'}, name='receipt')
        for field in ('report_id', 'consequence'):
            _text(receipt[field], field)
        if receipt['report_id'] in seen:
            _fail('Duplicate report receipt')
        seen.add(receipt['report_id'])
        if receipt['status'] not in ('read', 'applied'):
            _fail('Receipt status must be read or applied')
        if receipt['status'] == 'applied' and not {'result', 'verification'} <= set(receipt):
            _fail('Applied receipt requires result and verification')
        for field in ('result', 'verification'):
            if field in receipt:
                _text(receipt[field], field)
        result.append(copy.deepcopy(receipt))
    return result


def _checkpoint_path(root, participant_id, create=False):
    _identifier(participant_id, 'participant_id')
    directory = _safe_directory(root / 'checkpoints', create=create)
    path = directory / (participant_id + '.json')
    _safe_file(path, allow_missing=create)
    return path


def _validate_checkpoint(checkpoint, participant_id):
    _keys(checkpoint, {'schema_version', 'project_id', 'participant_id', 'context_content_id',
                       'memory_head', 'memory_revision', 'memory_content_id', 'active_decision_ids',
                       'open_anchor_ids', 'task', 'next_step', 'receipts', 'saved_at'}, name='checkpoint')
    if checkpoint['schema_version'] != SCHEMA_VERSION:
        _fail('Unsupported checkpoint schema')
    _identifier(checkpoint['project_id'], 'checkpoint project_id')
    if checkpoint['participant_id'] != participant_id:
        _fail('Checkpoint participant mismatch')
    for field in ('context_content_id', 'task', 'next_step'):
        _text(checkpoint[field], field)
    for field in ('memory_head', 'memory_content_id'):
        _digest(checkpoint[field], field)
    _integer(checkpoint['memory_revision'], 'memory_revision', 1)
    _timestamp(checkpoint['saved_at'], 'saved_at')
    for field in ('active_decision_ids', 'open_anchor_ids'):
        if not isinstance(checkpoint[field], list):
            _fail(f'{field} must be a list')
        for identifier in checkpoint[field]:
            _identifier(identifier)
        if len(set(checkpoint[field])) != len(checkpoint[field]):
            _fail(f'Duplicate {field}')
    _receipts(checkpoint['receipts'])
    return checkpoint


def save_checkpoint(root, participant_id, context_content_id, task, next_step,
                    expected_head, receipts=None):
    _identifier(participant_id, 'participant_id')
    for value, name in ((context_content_id, 'context_content_id'), (task, 'task'), (next_step, 'next_step')):
        _text(value, name)
    _digest(expected_head, 'expected_head')
    incoming = _receipts(receipts) if receipts is not None else []
    with _locked(root) as directory:
        raw, state, _ = _load(directory)
        if state['head'] != expected_head:
            _fail('Stale expected_head while saving checkpoint')
        working_set = _working_set(raw, state, context_content_id)
        path = _checkpoint_path(directory, participant_id, create=True)
        merged = {}
        if path.exists():
            previous = _validate_checkpoint(_parse(_read_bytes(path), 'checkpoint'), participant_id)
            if previous['project_id'] != state['project_id']:
                _fail('Cannot reuse another project checkpoint')
            merged = {r['report_id']: r for r in previous['receipts']}
        for receipt in incoming:
            previous = merged.get(receipt['report_id'])
            if previous and previous['status'] == 'applied' and receipt['status'] == 'read':
                _fail('Cannot downgrade an applied receipt to read')
            merged[receipt['report_id']] = receipt
        checkpoint = {'schema_version': SCHEMA_VERSION, 'project_id': state['project_id'],
                      'participant_id': participant_id, 'context_content_id': context_content_id,
                      'memory_head': state['head'], 'memory_revision': state['revision'],
                      'memory_content_id': working_set['content_id'],
                      'active_decision_ids': [r['id'] for r in working_set['active_decisions']],
                      'open_anchor_ids': [r['id'] for r in working_set['open_anchors']],
                      'task': task, 'next_step': next_step,
                      'receipts': [merged[key] for key in sorted(merged)], 'saved_at': _now()}
        _validate_checkpoint(checkpoint, participant_id)
        _atomic_write(path, _pretty(checkpoint))
        return checkpoint


def check_resume(root, participant_id, context_content_id):
    _identifier(participant_id, 'participant_id')
    _text(context_content_id, 'context_content_id')
    directory = _safe_directory(root)
    raw, state, _ = _load(directory)
    path = _checkpoint_path(directory, participant_id)
    checkpoint = _validate_checkpoint(_parse(_read_bytes(path), 'checkpoint'), participant_id)
    working_set = _working_set(raw, state, context_content_id)
    comparisons = (
        ('project_id', state['project_id'], 'project changed'),
        ('memory_head', state['head'], 'memory journal head changed'),
        ('memory_revision', state['revision'], 'memory revision changed'),
        ('memory_content_id', working_set['content_id'], 'memory content or projection code changed'),
        ('context_content_id', context_content_id, 'context content changed'),
        ('active_decision_ids', [r['id'] for r in working_set['active_decisions']], 'active decision set changed'),
        ('open_anchor_ids', [r['id'] for r in working_set['open_anchors']], 'open anchor set changed'),
    )
    reasons = [reason for key, current, reason in comparisons if checkpoint[key] != current]
    return {'status': 'stale' if reasons else 'fresh', 'reasons': reasons, 'checkpoint': checkpoint}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Explicit project memory directory')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Create a new store')
    init.add_argument('--project-id', required=True)
    init.add_argument('--editor', required=True)
    init.add_argument('--title', default='')
    apply = commands.add_parser('apply', help='Validate an operation JSON file and append it')
    apply.add_argument('--operation', type=Path, required=True)
    commands.add_parser('show', help='Replay the journal and show full state')
    search = commands.add_parser('search', help='Literal case-insensitive search, all statuses preserved')
    search.add_argument('--project-id', required=True)
    search.add_argument('--query', required=True)
    commands.add_parser('materialize', help='Rebuild CURRENT.json and CURRENT.md')
    commands.add_parser('validate', help='Validate journal and exact generated views')
    checkpoint = commands.add_parser('checkpoint', help='Save participant state without advancing the ledger')
    checkpoint.add_argument('--participant', required=True)
    checkpoint.add_argument('--context-content-id', required=True)
    checkpoint.add_argument('--task', required=True)
    checkpoint.add_argument('--next-step', required=True)
    checkpoint.add_argument('--expected-head', required=True)
    checkpoint.add_argument('--receipts', type=Path)
    resume = commands.add_parser('resume', help='Check whether a participant checkpoint is fresh')
    resume.add_argument('--participant', required=True)
    resume.add_argument('--context-content-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            result = init_store(args.root, args.project_id, args.editor, args.title)
        elif args.command == 'apply':
            result = apply_operation(args.root, _parse(_read_bytes(args.operation), 'operation'))
        elif args.command == 'show':
            result = read_store(args.root)
        elif args.command == 'search':
            result = search_records(args.root, args.project_id, args.query)
        elif args.command == 'materialize':
            result = materialize(args.root)
        elif args.command == 'validate':
            result = validate_view(args.root)
        elif args.command == 'checkpoint':
            receipts = _parse(_read_bytes(args.receipts), 'receipts') if args.receipts else None
            result = save_checkpoint(args.root, args.participant, args.context_content_id,
                                     args.task, args.next_step, args.expected_head, receipts)
        else:
            result = check_resume(args.root, args.participant, args.context_content_id)
    except (ProjectMemoryError, RecursionError) as exc:
        parser.error(str(exc))
    print(_pretty(result).decode('utf-8'), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
