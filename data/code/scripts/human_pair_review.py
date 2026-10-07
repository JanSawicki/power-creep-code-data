#!/usr/bin/env python3
"""Persist and resume the author's one-pair-at-a-time semantic review."""
import argparse
import csv
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'paper' / 'human-review' / 'different-card-pairs'
SOURCE = ROOT / 'paper/submission/supplement/annotation_queue.csv'


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def initialize(folder):
    folder.mkdir(parents=True, exist_ok=True)
    queue = folder / 'pairs.csv'
    if not queue.exists():
        queue.write_bytes(SOURCE.read_bytes())
    if not (folder / 'manifest.json').exists():
        manifest = {
            'schema_version': 1,
            'source': str(SOURCE.relative_to(ROOT)),
            'queue_sha256': hashlib.sha256(queue.read_bytes()).hexdigest(),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'Single-human semantic review of the eligible different-card queue; incomplete-effect exclusions apply. Not independent validation.',
        }
        atomic_text(folder / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    for name, original in [
        ('detection-criteria.md', ROOT / 'prompts/power_creep_detection.md'),
        ('validation-rubric.md', ROOT / 'paper/submission/supplement/validation_rubric.md'),
    ]:
        if not (folder / name).exists():
            (folder / name).write_bytes(original.read_bytes())
    (folder / 'decisions.jsonl').touch(exist_ok=True)


def load(folder):
    queue = folder / 'pairs.csv'
    manifest = json.loads((folder / 'manifest.json').read_text())
    if hashlib.sha256(queue.read_bytes()).hexdigest() != manifest['queue_sha256']:
        raise ValueError('Frozen pair queue changed; refusing to attach decisions to altered data.')
    with queue.open(newline='') as file:
        rows = list(csv.DictReader(file))
    ids = [row['pair_id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate pair IDs in queue.')
    latest = {}
    for line in (folder / 'decisions.jsonl').read_text().splitlines():
        item = json.loads(line)
        if item['pair_id'] not in ids:
            raise ValueError('Decision references an unknown pair.')
        latest[item['pair_id']] = item
    return rows, latest


def plain(value):
    return html.unescape(re.sub(r'<[^>]+>', '', value or '')).replace('|', '\\|').replace('\n', ' ')


def refresh(folder, rows, latest):
    lines = ['# Human pair-review checklist', '',
             f'**Reviewed: {len(latest)}/{len(rows)}. Remaining: {len(rows)-len(latest)}.**', '',
             'Checked means a decision was saved, including rejected or uncertain decisions. It does not mean confirmed improvement.', '',
             'Source of truth: append-only `decisions.jsonl`; latest decision per pair is current. `pairs.csv` is the frozen input.', '']
    for row in rows:
        item = latest.get(row['pair_id'])
        suffix = f" — **{item['decision']}**" if item else ''
        lines.append(f"- [{'x' if item else ' '}] {row['pair_id']}: {plain(row['baseline_name'])} → {plain(row['candidate_name'])}{suffix}")
    atomic_text(folder / 'checklist.md', '\n'.join(lines) + '\n')
    summary = {
        'total': len(rows), 'reviewed': len(latest),
        'remaining': len(rows) - len(latest),
        'next_pair_id': next((r['pair_id'] for r in rows if r['pair_id'] not in latest), None),
        'counts': {d: sum(x['decision'] == d for x in latest.values()) for d in ['YES', 'NO', 'UNCERTAIN']},
    }
    atomic_text(folder / 'progress.json', json.dumps(summary, indent=2) + '\n')
    return summary


def show(row, position, total):
    print(f"Pair {row['pair_id']} ({position}/{total})\n")
    print('| Detail | Card A — baseline | Card B — candidate |')
    print('|---|---|---|')
    for label, key in [('Name', 'name'), ('Cost (Mana)', 'mana'), ('Attack', 'attack'),
                       ('Health', 'health'), ('Effect', 'text'), ('Tribe', 'tribe')]:
        def value(prefix):
            v = plain(row[f'{prefix}_{key}'])
            return v or ('None' if key in ['tribe', 'text'] else '—')
        print(f'| {label} | {value("baseline")} | {value("candidate")} |')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['init', 'next', 'status']:
        commands.add_parser(name)
    record = commands.add_parser('record')
    record.add_argument('pair_id')
    record.add_argument('decision', choices=['YES', 'NO', 'UNCERTAIN'])
    record.add_argument('--response-file', type=Path, required=True, help='Exact human response; never use AI-generated rationale.')
    record.add_argument('--reason-code', default=None)
    args = parser.parse_args()
    folder = args.directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == 'init':
            initialize(folder)
        rows, latest = load(folder)
        if args.command == 'record':
            if args.pair_id not in {r['pair_id'] for r in rows}:
                parser.error('Unknown pair ID.')
            response = args.response_file.read_text(encoding='utf-8')
            if not response.strip():
                parser.error('Human response cannot be empty.')
            item = {
                'pair_id': args.pair_id, 'decision': args.decision,
                'reason_code': args.reason_code, 'human_response': response,
                'reviewer': 'user', 'reviewer_kind': 'human',
                'recorded_at': datetime.now(timezone.utc).isoformat(),
                'queue_sha256': hashlib.sha256((folder / 'pairs.csv').read_bytes()).hexdigest(),
                'supersedes_previous': args.pair_id in latest,
            }
            with (folder / 'decisions.jsonl').open('a') as file:
                file.write(json.dumps(item, ensure_ascii=False) + '\n')
                file.flush()
                os.fsync(file.fileno())
            latest[args.pair_id] = item
        summary = refresh(folder, rows, latest)
        if args.command == 'next':
            pair = next((r for r in rows if r['pair_id'] not in latest), None)
            if pair:
                show(pair, rows.index(pair) + 1, len(rows))
            else:
                print('All pairs reviewed. Uncertain decisions remain unresolved findings.')
        else:
            print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
