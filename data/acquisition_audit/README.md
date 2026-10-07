# Acquisition eligibility audit

Scope: all 145 eligible different-card relationships, including the 117 human-accepted pairs. The audit recovers exact card IDs by matching name, retained build, set, class, type, rarity, Mana, Attack, Health, tribe and cleaned effect text against the local hsdata git history. Source commits and XML hashes are preserved. COLLECTIBLE is not treated as proof of crafting or disenchanting eligibility.

The examined source states have no explicit crafting/disenchanting flags. Matching wiki.gg card pages supply regular-copy acquisition statements. A regular crafting entry must agree with the retained rarity's ordinary dust value. Recovery values use the ordinary crafting rules outside refunds; Core/free versions provide no proceeds. Golden, Signature and Diamond variants are outside scope.

Core Hidden is source set 1810, described by wiki.gg as Core reserve. The audit keeps the exact CORE_ identity and does not substitute a same-name original card. A reserve baseline is excluded from the baseline-owned scenario because usable access to that reserve version is not established; a Core/reserve candidate lacks an ordinary crafting route.

Evidence files: card_audit.json (distinct source states and card-level availability), pair_audit.json (directed pair classification), source_builds.json (local source commits and XML hashes), wiki_evidence.json (wiki URLs, revision links, page hashes and acquisition excerpts), summary.json (coverage and limitations). Unavailable pages remain unresolved.

Wiki availability is checked at retrieval. It does not establish every historical acquisition route, actual holdings, pack purchases, or temporary refund eligibility. The ordinary-cost analysis remains a hypothetical scenario rather than a reconstruction of historical transactions.

The local HTML cache is retained for inspection outside the publication ZIP. A cached page hash binds the extracted evidence to those retrieved bytes; revision links allow later source inspection.

## Completed coverage

All 220 distinct source states were recovered, and all 219 required wiki pages were retrieved. The 89 included human-accepted cost assignments match the original analysis. The 28 accepted exclusions comprise 18 reserve Core baselines and 10 noncraftable candidates. Baseline recovery for the noncraftable event card CATA_EVENT_401 remains unresolved; P074 and P112 therefore stay economically excluded. All accepted-pair classifications are supported.

The source-audited detector scenario contains the same 102 pairs as the frozen ordinary-cost analysis. P074 and P112 have verified candidate crafting entries but unresolved baseline recovery; uncraftability alone is not treated as proof of zero recovery for this Epic event card. The accepted economic sample remains 89. audited_cost_summary.json and audited_conversion_pairs.csv provide the checked arithmetic.

Source matching distinguishes renamed cards by ID: historical Hallucination UNG_856 maps to Spore Hallucination, not the new SC_757 card. Historical Core_ ID capitalization is matched to CORE_ with the difference disclosed.

Reproduce from the repository with `.venv/bin/python scripts/audit_acquisition_eligibility.py`. It requires requests, beautifulsoup4, lxml and the ingestion script dependencies. The local cache is analysis/acquisition_audit/wiki_pages; unavailable web pages remain unresolved if rerun without the cache. The supplied JSON/CSV artifacts permit arithmetic verification without contacting the wiki.
