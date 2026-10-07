#!/usr/bin/env python3
"""Create collection-economy charts from detected power-creep comparisons.

The measures, scope, and cautions are defined in ``docs/metrics.md``.  The
script deliberately treats Card A as the baseline and Card B as the better
candidate; it does not try to infer player ownership, adoption, or spending.

By default it analyses the reviewed positive detections and creates only
paired final artefacts in ``analysis/``: ``NN_metric_name.pdf`` and the
matching ``NN_metric_name.md`` containing the values used to draw that chart.

Examples:
    python scripts/analysis.py
    python scripts/analysis.py --input data/powercreep_results.jsonl
    python scripts/analysis.py --output-dir /tmp/power-creep-analysis --top-n 20
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter


REPO = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO / "data" / "powercreep_results_reviewed.jsonl"
DEFAULT_DATABASE = REPO / "data" / "powercreep.duckdb"
DEFAULT_TIMELINE = REPO / "data" / "hearthstone_patches_and_expansions_by_year.md"
DEFAULT_OUTPUT = REPO / "analysis"

CATEGORIES = (
    "same-card-buff",
    "same-card-nerf",
    "cross-card-same-patch",
    "cross-card-cross-patch",
)
CATEGORY_LABELS = {
    "same-card-buff": "Same-card buff",
    "same-card-nerf": "Same-card nerf (control)",
    "cross-card-same-patch": "Different cards, same patch",
    "cross-card-cross-patch": "Different cards, different patches",
}
# Okabe–Ito palette: readable for common colour-vision deficiencies and in
# greyscale.  The neutral grey also keeps the control category visually quiet.
CATEGORY_COLORS = {
    "same-card-buff": "#0072B2",
    "same-card-nerf": "#999999",
    "cross-card-same-patch": "#E69F00",
    "cross-card-cross-patch": "#009E73",
}
COHORT_COLORS = ("#0072B2", "#D55E00")
RARITY_ORDER = {"FREE": 0, "COMMON": 1, "RARE": 2, "EPIC": 3, "LEGENDARY": 4}
DUST_COST = {"FREE": 0, "COMMON": 40, "RARE": 100, "EPIC": 400, "LEGENDARY": 1600}
DISENCHANT_DUST = {"FREE": 0, "COMMON": 5, "RARE": 20, "EPIC": 100, "LEGENDARY": 400}
# Ordinary, non-golden values; no temporary balance-change refund is assumed.
# Sources and acquisition assumptions are recorded in docs/metrics.md.
NO_DUST_SETS = {"basic", "core"}
UNRESOLVED_ACQUISITION_SETS = {
    "corehidden", "event", "promo", "vanilla", "invalid", "testtemporary",
    "missions", "tavernbrawl", "tavernsoftime", "battlegrounds", "mercenaries",
}
FOUNDATIONAL_SETS = {"basic", "classic", "core"}


@dataclass(frozen=True)
class TimelineEntry:
    name: str
    year: int


def normalise(value: object) -> str:
    """Return a punctuation-insensitive key for set-name matching."""
    value = str(value or "").casefold().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", value)


# Dataset labels that are known not to match their expansion's public title.
# Keys and values use normalise() so the matching is case/punctuation agnostic.
TIMELINE_ALIASES = {
    normalise("The League of Explorers"): normalise("League of Explorers"),
    normalise("Kobolds and Catacombs"): normalise("Kobolds & Catacombs"),
    normalise("Saviours of Uldum"): normalise("Saviors of Uldum"),
    normalise("Great Dark Beyond"): normalise("The Great Dark Beyond"),
    normalise("Wild West"): normalise("Showdown in the Badlands"),
    normalise("The Shrouded City"): normalise("The Lost City of Un'Goro"),
}


def read_positive_records(path: Path) -> list[dict[str, Any]]:
    """Read well-formed POSITIVE detections and give actionable errors."""
    records: list[dict[str, Any]] = []
    invalid: list[str] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if record.get("power_creep") != "POSITIVE":
                    continue
                if record.get("comparison_type") not in CATEGORIES:
                    raise ValueError("unrecognised comparison_type")
                if not isinstance(record.get("card_a"), dict) or not isinstance(record.get("card_b"), dict):
                    raise ValueError("missing card_a or card_b object")
            except (json.JSONDecodeError, ValueError) as exc:
                invalid.append(f"line {line_number}: {exc}")
                continue
            records.append(record)
    if invalid:
        details = "; ".join(invalid[:5])
        more = "" if len(invalid) <= 5 else f" (and {len(invalid) - 5} more)"
        raise ValueError(f"Invalid input records in {path}: {details}{more}")
    if not records:
        raise ValueError(f"No POSITIVE detection records found in {path}")
    return records


def card_identity(card: dict[str, Any]) -> tuple[str, str, str, str]:
    """Identity used by the metrics: card name, set, class, and type."""
    return tuple(normalise(card.get(key)) for key in ("name", "card_set", "class", "type"))


def unique_pairs(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one collection-relevant baseline→candidate pair per identity pair."""
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = (card_identity(record["card_a"]), card_identity(record["card_b"]))
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def is_different_card(record: dict[str, Any]) -> bool:
    return normalise(record["card_a"].get("name")) != normalise(record["card_b"].get("name"))


@dataclass(frozen=True)
class ConversionCost:
    candidate_craft: int
    baseline_recovery: int
    baseline_treatment: str

    @property
    def net_dust(self) -> int:
        return self.candidate_craft - self.baseline_recovery

    @property
    def additional_dust(self) -> int:
        return max(0, self.net_dust)


def conversion_cost(record: dict[str, Any]) -> tuple[ConversionCost | None, str]:
    """Estimate one-copy replacement under explicitly conditional ordinary rules.

    The corpus lacks per-printing craftability flags. Exclude unresolved special
    sets and non-craftable candidates; give known non-disenchantable baselines
    no recovery. Other printings are assumed normally craftable/disenchantable.
    """
    if not is_different_card(record):
        return None, "Same-card history"
    a, b = record["card_a"], record["card_b"]
    if a.get("rarity") not in DUST_COST or b.get("rarity") not in DUST_COST:
        return None, "Unknown rarity"
    a_set, b_set = normalise(a.get("card_set")), normalise(b.get("card_set"))
    if b_set in NO_DUST_SETS or b["rarity"] == "FREE":
        return None, "Candidate has no ordinary crafting route"
    if not a_set or not b_set or {a_set, b_set} & UNRESOLVED_ACQUISITION_SETS:
        return None, "Unresolved acquisition status"
    no_recovery = a_set in NO_DUST_SETS or a["rarity"] == "FREE"
    return ConversionCost(
        candidate_craft=DUST_COST[b["rarity"]],
        baseline_recovery=0 if no_recovery else DISENCHANT_DUST[a["rarity"]],
        baseline_treatment="No disenchanting proceeds" if no_recovery else "Ordinary disenchanting assumed",
    ), "Included under ordinary-rules assumptions"


def candidate_identity(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return card_identity(record["card_b"])


def unique_candidates(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for record in records:
        key = candidate_identity(record)
        if key not in seen:
            seen.add(key)
            output.append(record)
    return output


def parse_timeline(path: Path) -> dict[str, TimelineEntry]:
    """Parse set release years from the supplied Markdown timeline."""
    year: int | None = None
    timeline: dict[str, TimelineEntry] = {}
    heading = re.compile(r"^## .*?\((\d{4})(?:[–-]\d{4})?\)")
    row = re.compile(r"^\|\s*\*\*Patch .*?\*\*\s*\|\s*(.*?)\s*\|")
    for line in path.read_text(encoding="utf-8").splitlines():
        found_heading = heading.match(line)
        if found_heading:
            year = int(found_heading.group(1))
            continue
        found_row = row.match(line)
        if found_row and year is not None:
            name = re.sub(r"[*_`]", "", found_row.group(1)).strip()
            if name and name != "Mini-Set":
                timeline[normalise(name)] = TimelineEntry(name, year)
    if not timeline:
        raise ValueError(f"No timeline rows parsed from {path}")
    return timeline


def timeline_entry(card_set: object, timeline: dict[str, TimelineEntry]) -> TimelineEntry | None:
    key = normalise(card_set)
    return timeline.get(TIMELINE_ALIASES.get(key, key))


def short_card(card: dict[str, Any]) -> str:
    return f"{card.get('name') or 'Unknown'} ({card.get('card_set') or 'Unknown set'})"


def category_counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(record["comparison_type"] for record in records)
    return {category: counts[category] for category in CATEGORIES}


def md_table(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Render machine-readable raw values as a simple Markdown table."""
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    table = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    table.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    return "\n".join(table)


def write_markdown(path: Path, title: str, definition: str, methodology: str,
                   columns: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    path.write_text(
        f"# {title}\n\n"
        f"**Definition:** {definition}\n\n"
        f"**Method:** {methodology}\n\n"
        "The table below is the exact data plotted in the matching PDF.\n\n"
        + md_table(columns, rows) + "\n",
        encoding="utf-8",
    )


def append_markdown_table(path: Path, heading: str, columns: Sequence[str],
                          rows: Iterable[Sequence[object]]) -> None:
    """Append an auditable detail table after a chart's plotted-value table."""
    with path.open("a", encoding="utf-8") as output:
        output.write(f"\n## {heading}\n\n" + md_table(columns, rows) + "\n")


def figure(title: str, subtitle: str, *, width: float = 9.5, height: float = 5.7):
    # Do not rely on constrained_layout for the figure-level subtitle: it does
    # not reserve space for fig.text(), which made the subtitle's distance to
    # the plot vary with tick-label length.  A fixed header band keeps every
    # title/definition pair aligned and prevents overlap with the axes.
    wrapped_subtitle = textwrap.fill(subtitle, width=112)
    # Reserve a compact physical header (rather than a fixed fraction of the
    # figure).  That avoids excessive blank space on the taller ranking plots,
    # while allowing a little more room when a definition wraps to two lines.
    header_inches = 1.14 if wrapped_subtitle.count("\n") == 0 else 1.38
    fig, axis = plt.subplots(figsize=(width, height))
    fig.subplots_adjust(left=0.125, right=0.97, bottom=0.18, top=1 - header_inches / height)
    fig.text(0.125, 0.965, title, ha="left", va="top", fontsize=15, fontweight="bold")
    fig.text(0.125, 0.895, wrapped_subtitle, ha="left", va="top", fontsize=9.5, color="#404040")
    axis.grid(axis="y", color="#D9D9D9", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    return fig, axis


def finish_figure(fig, path: Path) -> None:
    fig.savefig(path, format="pdf", bbox_inches="tight", metadata={"Title": path.stem})
    plt.close(fig)


def labelled_bars(axis, bars, *, horizontal: bool = False, fmt: Callable[[float], str] = lambda x: f"{x:g}") -> None:
    for bar in bars:
        if horizontal:
            value = bar.get_width()
            axis.text(value, bar.get_y() + bar.get_height() / 2, f" {fmt(value)}", va="center", fontsize=8.5)
        else:
            value = bar.get_height()
            axis.text(bar.get_x() + bar.get_width() / 2, value, fmt(value), ha="center", va="bottom", fontsize=8.5)


def chart_category_counts(pdf: Path, markdown: Path, records: list[dict[str, Any]], *, unique: bool) -> None:
    title = "Unique replacement pairs" if unique else "Reported positive comparisons"
    definition = (
        "The count of unique baseline-to-candidate identity pairs in each comparison category."
        if unique else
        "The number of detected positive comparisons in each comparison category; repeated client snapshots are retained."
    )
    measured = unique_pairs(records) if unique else records
    counts = category_counts(measured)
    fig, axis = figure(title, definition)
    bars = axis.bar(range(len(CATEGORIES)), list(counts.values()), color=[CATEGORY_COLORS[c] for c in CATEGORIES], width=0.68)
    axis.set_xticks(range(len(CATEGORIES)), [CATEGORY_LABELS[c] for c in CATEGORIES], rotation=18, ha="right")
    axis.set_ylabel("Comparisons" if not unique else "Unique pairs")
    axis.set_ylim(0, max(counts.values(), default=0) * 1.18 + 1)
    labelled_bars(axis, bars)
    finish_figure(fig, pdf)
    write_markdown(markdown, title, definition,
                   "Raw records are counted by their detector comparison category." if not unique else
                   "Repeated snapshots are deduplicated by baseline and candidate name, set, class, and type before counting.",
                   ("comparison_category", "count"),
                   [(CATEGORY_LABELS[c], counts[c]) for c in CATEGORIES])


def chart_rarity_transitions(pdf: Path, markdown: Path, pairs: list[dict[str, Any]]) -> None:
    title = "Rarity transitions between different cards"
    definition = "The rarity change from baseline A to candidate B, with rarity ordered Free < Common < Rare < Epic < Legendary."
    rows = [record for record in pairs if is_different_card(record)
            and record["card_a"].get("rarity") in RARITY_ORDER and record["card_b"].get("rarity") in RARITY_ORDER]
    transitions = ("Increase", "Unchanged", "Decrease")
    counts: dict[str, dict[str, int]] = {transition: {category: 0 for category in CATEGORIES} for transition in transitions}
    raw: list[tuple[object, ...]] = []
    for record in rows:
        a_rarity, b_rarity = record["card_a"]["rarity"], record["card_b"]["rarity"]
        comparison = RARITY_ORDER[b_rarity] - RARITY_ORDER[a_rarity]
        transition = "Increase" if comparison > 0 else "Decrease" if comparison < 0 else "Unchanged"
        category = record["comparison_type"]
        counts[transition][category] += 1
        raw.append((short_card(record["card_a"]), short_card(record["card_b"]), a_rarity.title(), b_rarity.title(), transition, CATEGORY_LABELS[category]))
    fig, axis = figure(title, definition)
    x = list(range(len(transitions)))
    bottom = [0] * len(transitions)
    present_categories = [category for category in CATEGORIES if any(counts[transition][category] for transition in transitions)]
    for category in present_categories:
        values = [counts[transition][category] for transition in transitions]
        bars = axis.bar(x, values, bottom=bottom, color=CATEGORY_COLORS[category], label=CATEGORY_LABELS[category])
        for bar, value, previous in zip(bars, values, bottom):
            if value:
                axis.text(bar.get_x() + bar.get_width() / 2, previous + value / 2, str(value), ha="center", va="center", fontsize=8)
        bottom = [old + value for old, value in zip(bottom, values)]
    axis.set_xticks(x, transitions)
    axis.set_ylabel("Unique different-card pairs")
    axis.legend(loc="upper right", frameon=False, fontsize=8)
    total = len(rows)
    upgrade = sum(counts["Increase"].values())
    axis.text(0.01, 0.99, f"Upgrade rate: {upgrade / total:.1%}" if total else "Upgrade rate: n/a", transform=axis.transAxes, va="top", fontsize=9)
    finish_figure(fig, pdf)
    summary = [(transition, CATEGORY_LABELS[category], counts[transition][category])
               for transition in transitions for category in CATEGORIES]
    summary.append(("Upgrade rate", "All included pairs", f"{upgrade / total:.6f}" if total else "n/a"))
    write_markdown(markdown, title, definition,
                   "Unique pairs only; same-name histories and rows without a known rarity are excluded. Upgrade rate = increases / included pairs.",
                   ("transition", "comparison_category", "unique_pairs"), summary)
    append_markdown_table(markdown, "Pair-level raw data",
                          ("baseline_card", "candidate_card", "baseline_rarity", "candidate_rarity", "transition", "comparison_category"), raw)


def chart_dust_difference(pdf: Path, markdown: Path, pairs: list[dict[str, Any]]) -> None:
    title = "Nominal crafting-price difference"
    definition = "Candidate minus baseline rarity-based crafting price; this does not include disenchanting proceeds."
    rows = [record for record in pairs if is_different_card(record)
            and record["card_a"].get("rarity") in DUST_COST and record["card_b"].get("rarity") in DUST_COST]
    values = sorted({DUST_COST[r["card_b"]["rarity"]] - DUST_COST[r["card_a"]["rarity"]] for r in rows})
    counts = {value: {category: 0 for category in CATEGORIES} for value in values}
    raw: list[tuple[object, ...]] = []
    for record in rows:
        a_cost, b_cost = DUST_COST[record["card_a"]["rarity"]], DUST_COST[record["card_b"]["rarity"]]
        difference = b_cost - a_cost
        category = record["comparison_type"]
        counts[difference][category] += 1
        raw.append((short_card(record["card_a"]), short_card(record["card_b"]), a_cost, b_cost, difference, CATEGORY_LABELS[category]))
    fig, axis = figure(title, definition)
    bottom = [0] * len(values)
    present_categories = [category for category in CATEGORIES if any(counts[value][category] for value in values)]
    for category in present_categories:
        category_values = [counts[value][category] for value in values]
        bars = axis.bar(range(len(values)), category_values, bottom=bottom, color=CATEGORY_COLORS[category], label=CATEGORY_LABELS[category])
        for bar, value, previous in zip(bars, category_values, bottom):
            if value:
                axis.text(bar.get_x() + bar.get_width() / 2, previous + value / 2, str(value), ha="center", va="center", fontsize=7.5)
        bottom = [old + value for old, value in zip(bottom, category_values)]
    axis.axhline(0, color="#444444", linewidth=0.8)
    axis.set_xticks(range(len(values)), [f"{value:+,}" for value in values])
    axis.set_xlabel("Candidate crafting price − baseline crafting price (dust)")
    axis.set_ylabel("Unique different-card pairs")
    axis.legend(loc="upper right", frameon=False, fontsize=8)
    finish_figure(fig, pdf)
    summary = [(difference, CATEGORY_LABELS[category], counts[difference][category])
               for difference in values for category in CATEGORIES]
    write_markdown(markdown, title, definition,
                   "Unique different-card pairs only. Nominal prices (0, 40, 100, 400, 1600 dust) are assigned from rarity, including non-craftable printings as a rarity-tier comparator. This is not a conversion cost: the baseline's crafting price is not recovered on disenchantment. See 11_conversion_cost for ordinary disenchanting and acquisition exclusions.",
                   ("dust_difference", "comparison_category", "unique_pairs"), summary)
    append_markdown_table(markdown, "Pair-level raw data",
                          ("baseline_card", "candidate_card", "baseline_dust", "candidate_dust", "dust_difference", "comparison_category"), raw)


def chart_conversion_cost(pdf: Path, markdown: Path, pairs: list[dict[str, Any]]) -> None:
    title = "Additional dust for card replacement"
    definition = "One normal copy: max(0, candidate crafting cost minus baseline disenchanting proceeds), outside refund windows."
    included: list[tuple[dict[str, Any], ConversionCost]] = []
    excluded: list[tuple[object, ...]] = []
    for record in pairs:
        if not is_different_card(record):
            continue
        cost, reason = conversion_cost(record)
        if cost is None:
            excluded.append((short_card(record["card_a"]), short_card(record["card_b"]), reason))
        else:
            included.append((record, cost))
    counts = Counter((cost.additional_dust, record["comparison_type"]) for record, cost in included)
    values = sorted({cost.additional_dust for _, cost in included})
    fig, axis = figure(title, definition, width=10.5)
    bottom = [0] * len(values)
    for category in CATEGORIES:
        heights = [counts[value, category] for value in values]
        if not any(heights):
            continue
        bars = axis.bar(range(len(values)), heights, bottom=bottom,
                        color=CATEGORY_COLORS[category], label=CATEGORY_LABELS[category])
        for bar, height, previous in zip(bars, heights, bottom):
            if height:
                axis.text(bar.get_x() + bar.get_width() / 2, previous + height / 2,
                          str(height), ha="center", va="center", fontsize=7.5)
        bottom = [old + value for old, value in zip(bottom, heights)]
    axis.set_xticks(range(len(values)), [f"{value:,}" for value in values])
    axis.set_xlabel("Additional dust after using baseline proceeds")
    axis.set_ylabel("Unique different-card pairs")
    if included:
        axis.legend(loc="upper right", frameon=False, fontsize=8)
    else:
        axis.text(0.5, 0.5, "No eligible pairs", transform=axis.transAxes, ha="center")
    fig.text(0.125, 0.025,
             f"Conditional crafting scenario: {len(included)} included; {len(excluded)} excluded. Known Basic/Core/free baselines return 0 dust.",
             fontsize=8, color="#404040")
    finish_figure(fig, pdf)
    method = (
        "Unique different-card pairs; own one normal baseline copy, lack the candidate, and use no existing dust. "
        "Ordinary disenchanting returns Common 5, Rare 20, Epic 100, Legendary 400 dust. "
        "Basic/Core/free baselines return zero and are retained. Basic/Core/free candidates have no ordinary crafting route and are excluded; "
        "Core Hidden, Event, other unresolved special sets, missing sets, and unknown rarities are also excluded. "
        "Other printings are assumed ordinarily craftable/disenchantable: the corpus lacks per-printing acquisition flags. "
        "This is a conditional resource estimate, not observed spending or a historical availability reconstruction. "
        "Surplus proceeds are preserved in net_dust and surplus_dust; additional_dust is floored at zero. "
        "Keeping the baseline instead requires the full candidate crafting cost. No pair costs should be summed into a collection budget. "
        "The hypothetical full-refund regime is not assigned without evidence of eligibility and timing. "
        "Sources: [Blizzard crafting values](https://hearthstone.blizzard.com/en-us/news/10245930/hearthstone-crafting-in-dust-we-trust), "
        "[Core and refund rules](https://hearthstone.blizzard.com/en-gb/news/24077480). Verified 2026-10-03."
    )
    write_markdown(markdown, title, definition, method,
                   ("additional_dust", "comparison_category", "unique_pairs"),
                   [(value, CATEGORY_LABELS[category], counts[value, category])
                    for value in values for category in CATEGORIES if category.startswith("cross-card")])
    append_markdown_table(markdown, "Coverage", ("status", "unique_pairs"),
                          [("Included", len(included)), ("Excluded", len(excluded))]
                          + sorted(Counter(row[2] for row in excluded).items()))
    stats = []
    for label, subset in [("All included pairs", included)] + [
        (CATEGORY_LABELS[c], [(r, cost) for r, cost in included if r["comparison_type"] == c])
        for c in CATEGORIES if c.startswith("cross-card")
    ]:
        additional = [cost.additional_dust for _, cost in subset]
        if additional:
            stats.append((label, len(subset), sum(v > 0 for v in additional),
                          sum(v == 0 for v in additional), f"{statistics.mean(additional):.6f}",
                          f"{statistics.median(additional):g}",
                          f"{statistics.median(cost.candidate_craft for _, cost in subset):g}"))
    append_markdown_table(markdown, "Summary", (
        "cohort", "unique_pairs", "positive_additional_dust", "zero_additional_dust",
        "mean_additional_dust", "median_additional_dust", "median_cost_keeping_baseline"), stats)
    append_markdown_table(markdown, "Pair-level raw data", (
        "baseline_card", "candidate_card", "baseline_rarity", "candidate_rarity",
        "baseline_treatment", "baseline_recovery", "candidate_craft", "nominal_price_difference",
        "net_dust", "additional_dust", "surplus_dust", "comparison_category"), [
        (short_card(r["card_a"]), short_card(r["card_b"]), r["card_a"]["rarity"], r["card_b"]["rarity"],
         cost.baseline_treatment, cost.baseline_recovery, cost.candidate_craft,
         DUST_COST[r["card_b"]["rarity"]] - DUST_COST[r["card_a"]["rarity"]],
         cost.net_dust, cost.additional_dust, max(0, -cost.net_dust), CATEGORY_LABELS[r["comparison_type"]])
        for r, cost in included])
    append_markdown_table(markdown, "Excluded pairs", ("baseline_card", "candidate_card", "reason"), excluded)


def chart_foundational_displacement(pdf: Path, markdown: Path, pairs: list[dict[str, Any]]) -> None:
    title = "Foundational-to-priced-rarity comparisons"
    definition = "Share of pairs with a Basic, Classic, or Core baseline and a candidate of positive nominal crafting price."
    counts = {category: {"displaced": 0, "total": 0} for category in CATEGORIES}
    raw: list[tuple[object, ...]] = []
    for record in pairs:
        category = record["comparison_type"]
        baseline_set = normalise(record["card_a"].get("card_set"))
        candidate_rarity = record["card_b"].get("rarity")
        qualifying = baseline_set in FOUNDATIONAL_SETS and DUST_COST.get(candidate_rarity, 0) > 0
        counts[category]["total"] += 1
        counts[category]["displaced"] += int(qualifying)
        raw.append((short_card(record["card_a"]), short_card(record["card_b"]), record["card_a"].get("card_set") or "Unknown", candidate_rarity or "Unknown", "Yes" if qualifying else "No", CATEGORY_LABELS[category]))
    # Include the aggregate in the plot and raw summary, while retaining every pair below.
    aggregate = {key: sum(counts[c][key] for c in CATEGORIES) for key in ("displaced", "total")}
    plot_labels = ["All categories"] + [CATEGORY_LABELS[c] for c in CATEGORIES]
    plot_values = [aggregate["displaced"] / aggregate["total"] if aggregate["total"] else 0] + [counts[c]["displaced"] / counts[c]["total"] if counts[c]["total"] else 0 for c in CATEGORIES]
    plot_colors = ["#333333"] + [CATEGORY_COLORS[c] for c in CATEGORIES]
    fig, axis = figure(title, definition)
    bars = axis.bar(range(len(plot_labels)), plot_values, color=plot_colors)
    axis.set_xticks(range(len(plot_labels)), plot_labels, rotation=18, ha="right")
    axis.set_ylabel("Share of unique pairs")
    axis.yaxis.set_major_formatter(PercentFormatter(1))
    axis.set_ylim(0, max(plot_values, default=0) * 1.2 + 0.03)
    labelled_bars(axis, bars, fmt=lambda value: f"{value:.1%}")
    finish_figure(fig, pdf)
    summary = [("All categories", aggregate["displaced"], aggregate["total"], f"{plot_values[0]:.6f}")]
    summary.extend((CATEGORY_LABELS[c], counts[c]["displaced"], counts[c]["total"], f"{counts[c]['displaced'] / counts[c]['total'] if counts[c]['total'] else 0:.6f}") for c in CATEGORIES)
    write_markdown(markdown, title, definition,
                   "Unique baseline-to-candidate identity pairs, including same-card histories. Candidate price is assigned from rarity only; this does not establish craftability, displacement, or an acquisition requirement. The historical filename is retained for stable links. The plot uses the summary table; pair-level classification follows it for auditability.",
                   ("category", "foundational_to_craftable_pairs", "unique_pairs", "share"), summary)
    append_markdown_table(markdown, "Pair-level classification",
                          ("baseline_card", "candidate_card", "baseline_set", "candidate_rarity", "qualifies", "comparison_category"), raw)


def candidate_cohort(records: list[dict[str, Any]], timeline: dict[str, TimelineEntry]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = unique_candidates(records)
    matched, unmatched = [], []
    for record in candidates:
        (matched if timeline_entry(record["card_b"].get("card_set"), timeline) else unmatched).append(record)
    return matched, unmatched


def chart_timeline_coverage(pdf: Path, markdown: Path, records: list[dict[str, Any]], timeline: dict[str, TimelineEntry]) -> None:
    title = "Candidate timeline coverage by Hearthstone year"
    definition = "Unique candidate cards grouped by the release year of their timeline-matched set; unmatched sets are shown separately."
    matched, unmatched = candidate_cohort(records, timeline)
    by_year = Counter(timeline_entry(record["card_b"].get("card_set"), timeline).year for record in matched)
    labels = [str(year) for year in sorted(by_year)] + ["Unmatched"]
    values = [by_year[int(label)] for label in labels[:-1]] + [len(unmatched)]
    fig, axis = figure(title, definition, width=10.5)
    colors = ["#0072B2"] * (len(labels) - 1) + ["#999999"]
    bars = axis.bar(range(len(labels)), values, color=colors)
    axis.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
    axis.set_ylabel("Unique candidate cards")
    labelled_bars(axis, bars)
    finish_figure(fig, pdf)
    raw = []
    for record in matched + unmatched:
        entry = timeline_entry(record["card_b"].get("card_set"), timeline)
        raw.append((short_card(record["card_b"]), record["card_b"].get("rarity") or "Unknown", entry.name if entry else "Unmatched", entry.year if entry else "Unmatched"))
    summary = [(label, value) for label, value in zip(labels, values)]
    write_markdown(markdown, title, definition,
                   "Candidates are deduplicated by name, set, class, and type. Set labels are case- and punctuation-normalised, with explicit aliases for known dataset labels.",
                   ("release_year", "unique_candidate_cards"), summary)
    append_markdown_table(markdown, "Candidate-level raw data",
                          ("candidate_card", "rarity", "timeline_set", "release_year"), raw)


def catalogue_cards_in_sets(database: Path, sets: set[str]) -> list[dict[str, str]]:
    """Get the distinct catalogue cards in candidate sets, across snapshots."""
    if not database.exists():
        raise FileNotFoundError(f"Card database not found: {database}")
    connection = duckdb.connect(str(database), read_only=True)
    try:
        rows = connection.execute(
            "SELECT DISTINCT name, card_set, class, type, rarity FROM cards WHERE card_set IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()
    # A card can be represented in many client snapshots.  Its collection
    # identity deliberately mirrors the pair deduplication rule, rather than
    # treating a changed rarity field in a later snapshot as a new card.
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for name, card_set, card_class, card_type, rarity in rows:
        if normalise(card_set) in sets:
            card = {"name": name, "card_set": card_set, "class": card_class, "type": card_type, "rarity": rarity}
            unique.setdefault(card_identity(card), card)
    return list(unique.values())


def chart_high_rarity_dependency(pdf: Path, markdown: Path, records: list[dict[str, Any]], timeline: dict[str, TimelineEntry], database: Path) -> None:
    title = "High-rarity candidate dependency"
    definition = "The Epic-or-Legendary share among timeline-matched unique candidate cards, compared with all catalogue cards in those same candidate sets."
    matched, _unmatched = candidate_cohort(records, timeline)
    candidate_sets = {normalise(record["card_b"].get("card_set")) for record in matched}
    catalogue = catalogue_cards_in_sets(database, candidate_sets)
    candidate_cards = [record["card_b"] for record in matched]
    cohorts = (("Unique candidate cards", candidate_cards), ("Catalogue cards in candidate sets", catalogue))
    summaries = []
    for name, cards in cohorts:
        high = sum(card.get("rarity") in {"EPIC", "LEGENDARY"} for card in cards)
        total = len(cards)
        summaries.append((name, high, total, high / total if total else 0))
    fig, axis = figure(title, definition)
    bars = axis.bar(range(len(summaries)), [summary[3] for summary in summaries], color=COHORT_COLORS, width=0.58)
    axis.set_xticks(range(len(summaries)), [summary[0] for summary in summaries])
    axis.set_ylabel("Epic-or-Legendary share")
    axis.yaxis.set_major_formatter(PercentFormatter(1))
    axis.set_ylim(0, max((summary[3] for summary in summaries), default=0) * 1.2 + 0.03)
    labelled_bars(axis, bars, fmt=lambda value: f"{value:.1%}")
    finish_figure(fig, pdf)
    raw = [(name, high, total, f"{share:.6f}") for name, high, total, share in summaries]
    write_markdown(markdown, title, definition,
                   "Candidate cards are deduplicated by name, set, class, and type. The catalogue cohort uses distinct card identities from the DuckDB catalogue in exactly the matched candidate sets; this is a cohort comparator, not a causal estimate.",
                   ("cohort", "epic_or_legendary_cards", "cards", "share"), raw)
    append_markdown_table(markdown, "Candidate cards plotted",
                          ("candidate_card", "set", "rarity", "release_year"),
                          [(card.get("name"), card.get("card_set"), card.get("rarity"), timeline_entry(card.get("card_set"), timeline).year) for card in candidate_cards])


def chart_replacement_pressure(pdf: Path, markdown: Path, pairs: list[dict[str, Any]], timeline: dict[str, TimelineEntry]) -> None:
    title = "Replacement pressure by release year"
    definition = "Distinct detected baseline-to-candidate replacement pairs grouped by the candidate card set's Hearthstone release year."
    grouped: dict[int, dict[str, int]] = defaultdict(lambda: {category: 0 for category in CATEGORIES})
    raw: list[tuple[object, ...]] = []
    unmatched = 0
    for record in pairs:
        entry = timeline_entry(record["card_b"].get("card_set"), timeline)
        if entry is None:
            unmatched += 1
            continue
        category = record["comparison_type"]
        grouped[entry.year][category] += 1
        raw.append((short_card(record["card_a"]), short_card(record["card_b"]), entry.name, entry.year, CATEGORY_LABELS[category]))
    years = sorted(grouped)
    fig, axis = figure(title, definition, width=10.5)
    bottom = [0] * len(years)
    for category in CATEGORIES:
        values = [grouped[year][category] for year in years]
        bars = axis.bar(range(len(years)), values, bottom=bottom, color=CATEGORY_COLORS[category], label=CATEGORY_LABELS[category])
        for bar, value, previous in zip(bars, values, bottom):
            if value >= 3:
                axis.text(bar.get_x() + bar.get_width() / 2, previous + value / 2, str(value), ha="center", va="center", fontsize=7.5)
        bottom = [old + value for old, value in zip(bottom, values)]
    axis.set_xticks(range(len(years)), years, rotation=45, ha="right")
    axis.set_ylabel("Unique replacement pairs")
    axis.legend(loc="upper left", frameon=False, fontsize=8)
    finish_figure(fig, pdf)
    summary = [(year, CATEGORY_LABELS[category], grouped[year][category])
               for year in years for category in CATEGORIES]
    write_markdown(markdown, title, definition,
                   f"Unique pairs only. {unmatched} pair(s) with an unmatched candidate set are excluded because they have no supplied release year.",
                   ("release_year", "comparison_category", "unique_replacement_pairs"), summary)
    append_markdown_table(markdown, "Pair-level raw data",
                          ("baseline_card", "candidate_card", "timeline_set", "release_year", "comparison_category"), raw)


def chart_top_same_card(pdf: Path, markdown: Path, records: list[dict[str, Any]], *, category: str, top_n: int) -> None:
    title = "Top observed buffed cards" if category == "same-card-buff" else "Top observed nerfed cards"
    verb = "buff" if category == "same-card-buff" else "nerf"
    definition = f"For each card, the number of detected same-card comparisons classified as {verb}s; each observed client snapshot is retained."
    counts = Counter(record["card_b"].get("name") or "Unknown" for record in records if record["comparison_type"] == category)
    shown = sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:top_n]
    # Reverse for a conventional descending horizontal bar ranking from top.
    labels, values = zip(*shown) if shown else ((), ())
    fig, axis = figure(title, definition, width=10, height=max(5.7, 0.38 * len(labels) + 2.4))
    bars = axis.barh(range(len(labels)), values, color=CATEGORY_COLORS[category])
    axis.set_yticks(range(len(labels)), labels)
    axis.invert_yaxis()
    axis.set_xlabel("Detected comparisons")
    axis.grid(axis="x", color="#D9D9D9", linewidth=0.8)
    axis.grid(axis="y", visible=False)
    labelled_bars(axis, bars, horizontal=True)
    finish_figure(fig, pdf)
    write_markdown(markdown, title, definition,
                   f"All retained {verb} comparisons are counted, including multiple observations from separate client snapshots. The PDF shows the top {top_n} by count.",
                   ("card", "detected_comparisons"), shown)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help=f"Positive-detection JSONL (default: {DEFAULT_INPUT})")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help=f"DuckDB card catalogue (default: {DEFAULT_DATABASE})")
    parser.add_argument("--timeline", type=Path, default=DEFAULT_TIMELINE, help=f"Set release timeline Markdown (default: {DEFAULT_TIMELINE})")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help=f"Directory for paired PDFs and Markdown (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--top-n", type=int, default=15, help="Number of cards in each same-card ranking (default: 15)")
    args = parser.parse_args()
    if args.top_n < 1:
        parser.error("--top-n must be at least 1")
    for required in (args.input, args.database, args.timeline):
        if not required.exists():
            parser.error(f"Required input does not exist: {required}")

    records = read_positive_records(args.input)
    pairs = unique_pairs(records)
    timeline = parse_timeline(args.timeline)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    charts: list[tuple[str, Callable[[Path, Path], None]]] = [
        ("reported_positive_comparisons", lambda pdf, md: chart_category_counts(pdf, md, records, unique=False)),
        ("unique_replacement_pairs", lambda pdf, md: chart_category_counts(pdf, md, records, unique=True)),
        ("rarity_transitions", lambda pdf, md: chart_rarity_transitions(pdf, md, pairs)),
        ("dust_difference", lambda pdf, md: chart_dust_difference(pdf, md, pairs)),
        ("foundational_to_craftable_displacement", lambda pdf, md: chart_foundational_displacement(pdf, md, pairs)),
        ("candidate_timeline_coverage", lambda pdf, md: chart_timeline_coverage(pdf, md, records, timeline)),
        ("high_rarity_candidate_dependency", lambda pdf, md: chart_high_rarity_dependency(pdf, md, records, timeline, args.database)),
        ("replacement_pressure_by_release_year", lambda pdf, md: chart_replacement_pressure(pdf, md, pairs, timeline)),
        ("top_observed_buffed_cards", lambda pdf, md: chart_top_same_card(pdf, md, records, category="same-card-buff", top_n=args.top_n)),
        ("top_observed_nerfed_cards", lambda pdf, md: chart_top_same_card(pdf, md, records, category="same-card-nerf", top_n=args.top_n)),
        ("conversion_cost", lambda pdf, md: chart_conversion_cost(pdf, md, pairs)),
    ]
    for number, (name, render) in enumerate(charts, 1):
        stem = f"{number:02d}_{name}"
        render(args.output_dir / f"{stem}.pdf", args.output_dir / f"{stem}.md")
    print(f"Analysed {len(records):,} positive detections ({len(pairs):,} unique replacement pairs).")
    print(f"Wrote {len(charts)} PDF/Markdown artifact pairs to {args.output_dir}")


if __name__ == "__main__":
    main()
