# Completed historical human review and provisional recomputation

The author restored qualitative ordinary-use improvement on October 7 and retained P127 as an improvement with targeting and conversion objections disclosed. The proposed strict reassessment was superseded before any new batch decisions were recorded. Historical decisions and all corrections remain preserved; this is not independent validation. See reassessment-criteria.md and author-interpretation.md.

7 comparisons with missing Spellstone upgrade conditions are excluded from every analysis dataset, for both detector and human-review results. The eligible queue contains 145 pairs: 117 accepted and 28 rejected. Stable historical pair IDs are retained with gaps. Exact surviving responses and corrections are preserved; their historical queue hash is recorded in the manifest. Exclusion is not a new human decision.

AI draft judgments were visible from P041 onward: 110 eligible decisions were assisted and 35 were not shown drafts. Reasons were not routinely collected. Retrospective author corrections are ['P020', 'P127']; their new rationales are labelled separately from the original annotations, which remain in the append-only decision history. No independent annotation, recall estimate, or chronology verification is claimed. The separate acquisition_audit directory supports ordinary availability for the included economic sample; it does not reconstruct historical holdings. P129's reverse decision remains separate.

Of 117 accepted pairs, 89 enter the conditional resource scenario and 28 are excluded for acquisition status. These acquisition exclusions are distinct from incomplete-effect exclusions and do not imply zero cost. The detector comparison also excludes the same seven relationships.

Reproduce without an LLM:

```sh
python recompute_review.py --output-dir /tmp/human-review-reproduced --no-plots
```

Python standard library suffices with `--no-plots`; plotting additionally requires matplotlib. The script checks queue hashes, surviving decision coverage, pair partitioning, and cost formulas. It does not rerun historical detection or restore missing historical provenance. Complete source evidence for the exclusion is in `source_effect_check.json`.
