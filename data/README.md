> October 7: ordinary-use interpretation restored; P127 retained as an improvement. The temporary stricter proposals were superseded before any new batch decisions. Mind Control and Hemet Nesingwary discussion and wiki.gg illustrations added.

# Reproducibility supplement — current analysis

Seven Spellstone comparisons with missing upgrade conditions are excluded from every detector and human-review analysis dataset. The exclusion policy is `frozen_data/excluded_comparisons.json`; complete source evidence for the missing clauses is in `human_review/source_effect_check.json`. Surviving pair IDs remain stable and contain gaps. The source catalogue remains a representation of input card states, not a set of accepted comparisons.

The detector datasets contain 1,405 initial and 1,079 retained records, 938 directed identity relationships, and 145 different-card pairs. Detector resource analysis includes 102 pairs. The single-human accepted subset contains 117 accepted pairs and 89 conditional resource scenarios after the October 6 author correction of P020. The author interpretation and P020 correction are documented in `human_review/author-interpretation.md`; historical responses remain in the append-only log. Human decisions used AI drafts for 110 eligible pairs; 35 were not shown drafts. Neither acceptance nor arithmetic reproduction establishes independent semantic validation or printing-level acquisition eligibility.

Reproduce the current manuscript without an LLM:

```sh
python human_review/recompute_review.py --output-dir /tmp/power-creep-human-reproduced --no-plots
```

Reproduce detector arithmetic, the frozen catalogue, reconstructed candidate total, and all 11 reports (requires the supplied environment's duckdb/matplotlib dependencies):

```sh
python reproduce_package.py --output-dir /tmp/power-creep-detector-reproduced
```

`annotation_queue.csv` is the eligible detector queue; `human_review/completed_annotations.csv` contains the surviving completed decisions. `conversion_pairs.csv` and `cost_exclusions.csv` at each level preserve distinct resource eligibility. `results_summary.json` at the supplement root describes eligible detector outputs; `human_review/results_summary.json` supplies current manuscript results. Exclusions are not semantic rejections or zero-cost transactions. The source-gap sensitivity used in an earlier version has been replaced by exclusion from the primary datasets.

Frozen inputs, manifests, current code snapshots, and prompts document the recovered implementation. Historical detector script/weight versions, exact second-pass invocation, and complete failure totals remain unavailable. Source-linked card histories and selected printings are in `case_events.csv` and `source_case_transitions.csv`; source-to-claim boundaries are in `source_to_claim.md`. Historical source coverage differs from aggregate ingestion coverage.

Resource scenarios assume eligible normal copies, one baseline owned, candidate absent, no prior dust, and no refund window. Core/free baselines remain held with zero recovery. Overlapping pairs are not a collection budget or observed spending. No historical prevalence, developer intent, or player response is estimated.

The authoritative editable manuscript is the LaTeX source. Recompile with `latexmk`; do not regenerate it from older Markdown. No external archive upload or journal submission has been made.

## October 7 acquisition audit

Local hsdata history identifies all 220 distinct represented states but supplies no direct crafting/disenchanting flags. Matching wiki.gg regular-copy entries and general crafting/Core rules support all 89 included accepted-pair dust assignments, with no change to the main results. The 28 accepted-pair exclusions resolve into 10 noncraftable candidates and 18 reserve Core baselines with no established holding. Current evidence is in acquisition_audit/pair_audit.csv and the accompanying JSON records. Historical cost_exclusions.csv reasons remain preserved.

The detector sensitivity sample remains 102 pairs. For the rejected event-card comparisons P074 and P112, candidate crafting is verified but baseline recovery remains unresolved; these stay excluded. The audit does not reconstruct historical holdings, every acquisition route, or refunds.
