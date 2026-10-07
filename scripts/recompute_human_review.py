#!/usr/bin/env python3
"""Recompute manuscript summaries from frozen inputs and saved human decisions.

Author-approved incomplete-effect exclusions apply to every analysis subset.
Can also run from supplement/human_review/recompute_review.py.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics
import shutil

HERE = Path(__file__).resolve()
PACKAGED = HERE.parent.name == 'human_review'
ROOT = HERE.parents[1]
RANK = {'FREE': 0, 'COMMON': 1, 'RARE': 2, 'EPIC': 3, 'LEGENDARY': 4}
CRAFT = {'FREE': 0, 'COMMON': 40, 'RARE': 100, 'EPIC': 400, 'LEGENDARY': 1600}
RETURN = {'FREE': 0, 'COMMON': 5, 'RARE': 20, 'EPIC': 100, 'LEGENDARY': 400}


def csvread(path):
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def csvwrite(path, rows):
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def rarity(rows):
    count = Counter('increased' if RANK[r['candidate_rarity']] > RANK[r['baseline_rarity']]
                    else 'decreased' if RANK[r['candidate_rarity']] < RANK[r['baseline_rarity']]
                    else 'unchanged' for r in rows)
    return {k: count[k] for k in ['increased', 'unchanged', 'decreased']}


def summary(pairs, included, excluded):
    matched = {}
    for key in ['nominal_price_difference', 'candidate_craft', 'additional_dust', 'net_dust', 'surplus_dust']:
        values = [int(r[key]) for r in included]
        matched[key] = {'n': len(values), 'median': statistics.median(values),
                        'mean': statistics.mean(values), 'min': min(values), 'max': max(values),
                        'distribution': dict(sorted(Counter(values).items()))}
    return {'different_card_pairs': len(pairs),
            'different_pair_categories': dict(Counter(r['category'] for r in pairs)),
            'rarity_transitions': rarity(pairs), 'conversion_included': len(included),
            'conversion_categories': dict(Counter(r['category'] for r in included)),
            'cost_exclusions': dict(Counter(r['status'] for r in excluded)),
            'included_rarity_transitions': rarity(included),
            'baseline_treatments': dict(Counter(r['baseline_treatment'] for r in included)),
            'candidate_rarities': dict(Counter(r['candidate_rarity'] for r in included)),
            'matched_sample': matched}


def plots(out, accepted, included):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 10, 'pdf.fonttype': 42, 'ps.fonttype': 42})
    categories = ['cross-card-same-patch', 'cross-card-cross-patch']
    labels = ['Same retained patch', 'Different retained patches']
    colors = ['#537995', '#9cb6c5']
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bottom = [0, 0, 0]
    for category, label, color in zip(categories, labels, colors):
        count = rarity([r for r in accepted if r['category'] == category])
        values = list(count.values())
        bars = ax.bar(['Increase', 'Unchanged', 'Decrease'], values, bottom=bottom, label=label, color=color)
        for b, value, base in zip(bars, values, bottom):
            if value:
                ax.text(b.get_x() + b.get_width()/2, base + value/2, str(value), ha='center', va='center')
        bottom = [a+b for a, b in zip(bottom, values)]
    ax.set_ylabel('Human-accepted directed different-card pairs')
    ax.set_title(f'Candidate rarity in the human-accepted subset (n = {len(accepted)})')
    ax.legend(frameon=False)
    ax.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    for ext in ['pdf', 'png']:
        fig.savefig(out / f'human_rarity.{ext}', dpi=220)
    plt.close(fig)
    values = sorted({int(r['additional_dust']) for r in included})
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bottom = [0]*len(values)
    for category, label, color in zip(categories, labels, colors):
        count = Counter(int(r['additional_dust']) for r in included if r['category'] == category)
        heights = [count[x] for x in values]
        bars = ax.bar(range(len(values)), heights, bottom=bottom, label=label, color=color)
        for b, value, base in zip(bars, heights, bottom):
            if value:
                ax.text(b.get_x()+b.get_width()/2, base+value/2, str(value), ha='center', va='center', fontsize=9)
        bottom = [a+b for a, b in zip(bottom, heights)]
    ax.set_xticks(range(len(values)), [str(v) for v in values])
    ax.set_xlabel('Additional dust after eligible baseline recovery')
    ax.set_ylabel('Human-accepted directed different-card pairs')
    ax.set_title(f'Conditional acquisition scenarios (n = {len(included)})')
    ax.legend(frameon=False)
    ax.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    for ext in ['pdf', 'png']:
        fig.savefig(out / f'human_cost.{ext}', dpi=220)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--supplement-dir', type=Path, default=HERE.parent.parent if PACKAGED else ROOT/'paper/submission/supplement')
    parser.add_argument('--review-dir', type=Path, default=HERE.parent if PACKAGED else ROOT/'paper/human-review/different-card-pairs')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    sup, review = args.supplement_dir.resolve(), args.review_dir.resolve()
    out = (args.output_dir or sup/'human_review').resolve()
    out.mkdir(parents=True, exist_ok=True)
    queue = csvread(review/'pairs.csv')
    assert queue == csvread(sup/'annotation_queue.csv'), 'Review queue differs from the frozen paper sample.'
    manifest = json.loads((review/'manifest.json').read_text())
    assert hashlib.sha256((review/'pairs.csv').read_bytes()).hexdigest() == manifest['queue_sha256']
    decisions = {}
    for line in (review/'decisions.jsonl').read_text().splitlines():
        row = json.loads(line)
        assert row['queue_sha256'] in {manifest['queue_sha256'], manifest.get('historical_queue_sha256')}
        decisions[row['pair_id']] = row
    assert set(decisions) == {r['pair_id'] for r in queue}
    assert all(r['decision'] in {'YES', 'NO', 'UNCERTAIN'} for r in decisions.values())
    annotation = []
    for row in queue:
        decision = decisions[row['pair_id']]
        # The actual session displayed six fields, without chronology or acquisition evidence.
        annotation.append({**row, 'semantic_decision': decision['decision'],
                           'chronology_decision': 'NOT_VERIFIED', 'annotator': 'user (single human reviewer)',
                           'rationale': decision.get('rationale') or '', 'human_response': decision['human_response'],
                           'recorded_at': decision['recorded_at'], 'reason_code': decision.get('reason_code') or '',
                           'ai_draft_visible': 'YES' if int(row['pair_id'][1:]) >= 41 else 'NO',
                           'rationale_status': decision.get('rationale_status') or 'NOT_SEPARATELY_RECORDED',
                           'acquisition_review_status': 'NOT_REVIEWED'})
    accepted = [r for r in queue if decisions[r['pair_id']]['decision'] == 'YES']
    rejected = [r for r in annotation if r['semantic_decision'] != 'YES']
    ids = {r['pair_id'] for r in accepted}
    original_inc = csvread(sup/'conversion_pairs.csv')
    original_exc = csvread(sup/'cost_exclusions.csv')
    assert {r['pair_id'] for r in original_inc}.isdisjoint(r['pair_id'] for r in original_exc)
    assert {r['pair_id'] for r in original_inc+original_exc} == {r['pair_id'] for r in queue}
    for r in original_inc:
        craft, recovery = int(r['candidate_craft']), int(r['baseline_recovery'])
        assert craft == CRAFT[r['candidate_rarity']]
        assert recovery == (0 if r['baseline_treatment']=='No disenchanting proceeds' else RETURN[r['baseline_rarity']])
        assert int(r['nominal_price_difference']) == craft-CRAFT[r['baseline_rarity']]
        assert int(r['net_dust']) == craft-recovery
        assert int(r['additional_dust']) == max(0, craft-recovery)
        assert int(r['surplus_dust']) == max(0, recovery-craft)
    included = [r for r in original_inc if r['pair_id'] in ids]
    excluded = [r for r in original_exc if r['pair_id'] in ids]
    original = summary(queue, original_inc, original_exc)
    revised = summary(accepted, included, excluded)
    frozen_summary = json.loads((sup/'results_summary.json').read_text())
    for key, value in original['matched_sample'].items():
        expected = frozen_summary['matched_sample'][key]
        assert value['n'] == expected['n'] and value['median'] == expected['median']
        assert abs(value['mean']-expected['mean']) < 1e-9
        assert {str(k):v for k,v in value['distribution'].items()} == expected['distribution']
    comparison = {'scope': 'Single-human, partly AI-assisted semantic review of frozen positive pairs; not independent validation, recall, or acquisition verification.',
                  'reviewed': len(decisions), 'accepted': len(accepted), 'rejected': len(rejected),
                  'uncertain': sum(r['decision']=='UNCERTAIN' for r in decisions.values()),
                  'accepted_fraction': len(accepted)/len(queue),
                  'ai_drafts_first_pair': 'P041', 'ai_drafts_last_pair': 'P152',
                  'ai_draft_visible_count': sum(int(r['pair_id'][1:]) >= 41 for r in queue),
                  'retrospective_author_corrections': [r['pair_id'] for r in decisions.values()
                                                     if r.get('decision_source') == 'author correction in revision discussion'],
                  'reverse_only_pair': 'P129', 'original': original, 'human_accepted': revised}
    source_check = sup/'human_review/source_effect_check.json'
    if source_check.exists() and source_check.resolve() != (out/'source_effect_check.json').resolve():
        shutil.copyfile(source_check, out/'source_effect_check.json')
    dump(out/'results_summary.json', comparison)
    csvwrite(out/'completed_annotations.csv', annotation)
    csvwrite(out/'accepted_pairs.csv', [r for r in annotation if r['semantic_decision']=='YES'])
    csvwrite(out/'rejected_pairs.csv', rejected)
    csvwrite(out/'conversion_pairs.csv', included)
    csvwrite(out/'cost_exclusions.csv', excluded)
    stats = []
    for label, data in [('original_detector', original), ('human_accepted', revised)]:
        for key, value in data['matched_sample'].items():
            stats.append({'sample': label, 'quantity': key, **{k:value[k] for k in ['n','median','mean','min','max']}})
    csvwrite(out/'matched_sample_comparison.csv', stats)
    for name in ['pairs.csv', 'decisions.jsonl', 'reverse-decisions.jsonl', 'checklist.md', 'manifest.json', 'detection-criteria.md', 'validation-rubric.md']:
        source = review/name
        if source.exists() and source.resolve() != (out/name).resolve():
            shutil.copyfile(source, out/name)
    if HERE.resolve() != (out/'recompute_review.py').resolve():
        shutil.copyfile(HERE, out/'recompute_review.py')
    (out/'README.md').write_text(f"""# Completed human review and recomputation

{len(manifest.get("excluded_pair_ids", []))} comparisons with missing Spellstone upgrade conditions are excluded from every analysis dataset, for both detector and human-review results. The eligible queue contains {len(queue)} pairs: {len(accepted)} accepted and {len(rejected)} rejected. Stable historical pair IDs are retained with gaps. Exact surviving responses and corrections are preserved; their historical queue hash is recorded in the manifest. Exclusion is not a new human decision.

AI draft judgments were visible from P041 onward: {comparison['ai_draft_visible_count']} eligible decisions were assisted and {len(queue)-comparison['ai_draft_visible_count']} were not shown drafts. Reasons were not routinely collected. Retrospective author corrections are {comparison['retrospective_author_corrections']}; their new rationales are labelled separately from the original annotations, which remain in the append-only decision history. No independent annotation, recall estimate, chronology verification, or acquisition verification is claimed. P129's reverse decision remains separate.

Of {len(accepted)} accepted pairs, {len(included)} enter the conditional resource scenario and {len(excluded)} are excluded for acquisition status. These acquisition exclusions are distinct from incomplete-effect exclusions and do not imply zero cost. The detector comparison also excludes the same seven relationships.

Reproduce without an LLM:

```sh
python recompute_review.py --output-dir /tmp/human-review-reproduced --no-plots
```

Python standard library suffices with `--no-plots`; plotting additionally requires matplotlib. The script checks queue hashes, surviving decision coverage, pair partitioning, and cost formulas. It does not rerun historical detection or restore missing historical provenance. Complete source evidence for the exclusion is in `source_effect_check.json`.
""")
    if not args.no_plots:
        plots(out, accepted, included)
    dump(out/'verification.json', {'status':'PASS', 'reviewed':len(decisions), 'accepted':len(accepted),
                                  'rejected':len(rejected), 'conversion_included':len(included),
                                  'cost_excluded':len(excluded), 'original_arithmetic_checked':True,
                                  'reverse_pair_not_inserted':True})
    print(json.dumps({'reviewed':len(decisions), 'accepted':len(accepted), 'rejected':len(rejected),
                      'included':len(included), 'excluded':len(excluded),
                      'matched_sample':revised['matched_sample']}, indent=2))


if __name__ == '__main__':
    main()
