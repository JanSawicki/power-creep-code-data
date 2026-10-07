> Original pre-review protocol, retained for provenance. Completed single-human decisions now appear in `human_review/completed_annotations.csv`. The originally planned two-annotator procedure was not performed; see `human_review/README.md` for the actual procedure.

# Validation scope and annotation rubric

The manuscript sample is frozen as **exploratory retained detector outputs**, not human-adjudicated positives. No independent human annotation, agreement statistic, recall estimate, or human confirmation proportion is claimed. The 152-row annotation queue is prepared for future knowledgeable annotators. Every decision is explicitly NOT_ANNOTATED; these are not completed decisions. Existing errors are documented separately in the evidence notes. No altered aggregate sample is introduced.

Two annotators, if available, should each receive an unchanged copy of annotation_queue.csv and record independent decisions before discussing cases. Preserve both originals; reconcile into a third file. Do not calculate agreement after reconciliation. AI assistance cannot supply independent human validation.

## Decision procedure

1. Identify the exact baseline and candidate printing and observed versions. Record ambiguity in set, class, tribe, rarity, acquisition, or missing fields.
2. Check noninferior Mana, Attack, Health, Weapon durability, Location durability and applicable Hero Armor. A property omitted by the detector still matters to annotation.
3. List each baseline beneficial effect. Check that the candidate preserves its targeting, trigger, condition, timing, optionality, randomness, magnitude, and tribal dependencies.
4. Check all added text for drawbacks. Literal substring preservation is insufficient. Consider compulsory Battlecries, self-damage, targeting differences, deckbuilding restrictions, and interactions with shared keywords.
5. Identify a meaningful strict improvement. Equivalent wording, localization, markup, and keyword compression alone do not qualify. A repair is classified separately as an event even if printed states differ.
6. Record semantic decision: ACCEPT_UNDER_CRITERIA, REJECT_TRADEOFF, REJECT_EQUIVALENT, UNCERTAIN, or PRINTING_AMBIGUITY. Explain the rule/effect establishing the decision and any context limitation. Acceptance is under the specified conservative criteria, never universal game-state dominance.
7. Independently record chronology: LATER_RELEASE_VERIFIED, VERSION_REVISION_VERIFIED, OBSERVED_SNAPSHOTS_ONLY, or CHRONOLOGY_UNCERTAIN. Cite release/patch evidence. Semantic acceptance does not establish historical replacement.
8. Separately record economic eligibility and whether the baseline may be surrendered. Do not code exclusions as zero costs.

## Adjudication log schema

pair_id, annotator_1_decision, annotator_2_decision, original_disagreement, reconciled_decision, rationale, sources, chronology_status, economic_status, adjudicator, date.

There are no completed independent decisions or reconciliations in this package. The known Evasive Wyrm equivalence, Tyrantus repair, and Demon Seed comparison/event distinction are evidence checks, not a representative validation sample.
