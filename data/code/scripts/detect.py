#!/usr/bin/env python3
"""Unified power creep detection.

Iterates all card pairs where both cards share the same type and class and
their mana costs are within ±MANA_WINDOW. This covers both longitudinal
(same card name, different build) and inter-card (different card names) pairs.

Each candidate pair is passed to detect_power_creep(), which evaluates both
directions with the directional LLM judge.

Output: data/powercreep_results.jsonl. Each positive record includes
``comparison_type``: ``same-card-buff``, ``same-card-nerf``,
``cross-card-same-patch``, or ``cross-card-cross-patch``.

Usage:
    export DB_PATH=/path/to/powercreep.duckdb
    source vllm.endpoints
    python scripts/detect.py
    python scripts/detect.py --compare "Card A" 1.0.0.1 "Card B" 2.0.0.2
    python scripts/detect.py --cards "Fireball" "Frostbolt"
    python scripts/detect.py --mana-window 3
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path

import duckdb

from compare_pairs import detect_power_creep
from vllm_endpoint import resolve_llm_url

REPO        = Path(__file__).parent.parent
OUTPUT      = REPO / "data" / "powercreep_results.jsonl"
MANA_WINDOW = 2

SAME_CARD_BUFF = "same-card-buff"
SAME_CARD_NERF = "same-card-nerf"
CROSS_CARD_SAME_PATCH = "cross-card-same-patch"
CROSS_CARD_CROSS_PATCH = "cross-card-cross-patch"

SELECT_COLS = "name, patch_id, type, mana, attack, health, card_set, class, rarity, text, tribe"

# SQL form of ``stats_comparable``.  It rejects trade-offs before they become
# worker tasks: one card must be non-inferior on every printed stat.  Mana is
# better when lower; attack and health/durability are better when higher.
# Spells and locations have mana as their only comparable printed stat.
STAT_DOMINANCE_SQL = """
(
    (
        b.mana IS NOT NULL AND a.mana IS NOT NULL
        AND b.mana <= a.mana
        AND (
            a.type NOT IN ('MINION', 'WEAPON', 'HERO')
            OR (
                b.attack IS NOT NULL AND a.attack IS NOT NULL
                AND b.health IS NOT NULL AND a.health IS NOT NULL
                AND b.attack >= a.attack AND b.health >= a.health
            )
        )
    )
    OR
    (
        b.mana IS NOT NULL AND a.mana IS NOT NULL
        AND a.mana <= b.mana
        AND (
            a.type NOT IN ('MINION', 'WEAPON', 'HERO')
            OR (
                b.attack IS NOT NULL AND a.attack IS NOT NULL
                AND b.health IS NOT NULL AND a.health IS NOT NULL
                AND a.attack >= b.attack AND a.health >= b.health
            )
        )
    )
)
"""

# A pair is worth sending to the LLM only when at least one direction can
# retain the baseline card's tribe.  A card with no tribe imposes no
# requirement; an ALL-tribe card retains every named tribe.  Two different
# named tribes otherwise represent a synergy trade-off rather than power creep.
TRIBE_DIRECTION_COMPATIBLE_SQL = """
(
    a.tribe IS NULL OR b.tribe IS NULL
    OR a.tribe = b.tribe
    OR a.tribe = 'ALL' OR b.tribe = 'ALL'
)
"""


def class_compatible_sql(allow_neutral_cross_class: bool) -> str:
    """Return the candidate-pair class predicate for the requested study scope."""
    if allow_neutral_cross_class:
        # This permits Neutral ↔ class comparisons while continuing to reject
        # comparisons between two different named classes.  It is opt-in
        # because it broadens the original same-class detection design.
        return """
        (
            COALESCE(b.class, 'NEUTRAL') = COALESCE(a.class, 'NEUTRAL')
            OR COALESCE(a.class, 'NEUTRAL') = 'NEUTRAL'
            OR COALESCE(b.class, 'NEUTRAL') = 'NEUTRAL'
        )
        """
    return "b.class = COALESCE(a.class, 'NEUTRAL')"


def card_desc(card: dict) -> str:
    stats = []
    if card.get("mana") is not None:
        stats.append(f"mana={card['mana']}")
    if card.get("attack") is not None:
        stats.append(f"atk={card['attack']}")
    if card.get("health") is not None:
        label = "dur" if card["type"] == "WEAPON" else "hp"
        stats.append(f"{label}={card['health']}")
    stats.append(f"class={card.get('class') or 'NEUTRAL'}")
    if card.get("card_set"):
        stats.append(f"set={card['card_set']}")
    if card.get("patch_id"):
        stats.append(f"build={card['patch_id']}")
    if card.get("text"):
        stats.append(f"text={card['text']!r}")
    return f"\"{card['name']}\" [{', '.join(stats)}]"


def comparison_type(card_a: dict, card_b: dict) -> str:
    """Classify a pair for downstream analysis.

    Card A must be the judged worse baseline and Card B the better candidate.
    Same-card improvements are buffs only when the candidate is later; when a
    later card is the worse baseline, the pair is a longitudinal nerf.
    Cross-card comparisons are split according to whether both cards were
    observed in the same patch or across patches.
    """
    if (card_a.get("name") or "").casefold() == (card_b.get("name") or "").casefold():
        def version_key(value: object) -> tuple[int, ...]:
            return tuple(int(number) for number in re.findall(r"\d+", str(value or ""))) or (0,)

        return (SAME_CARD_BUFF if version_key(card_b.get("patch_id")) > version_key(card_a.get("patch_id"))
                else SAME_CARD_NERF)
    if card_a.get("patch_id") == card_b.get("patch_id"):
        return CROSS_CARD_SAME_PATCH
    return CROSS_CARD_CROSS_PATCH


def _fetch_card(name: str, patch_id: str, cur) -> dict:
    cur.execute(
        f"SELECT {SELECT_COLS} FROM cards WHERE lower(name) = lower(?) AND patch_id = ?",
        (name, patch_id),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"Card {name!r} not found in build {patch_id!r}")
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _run_compare(name_a, bid_a, name_b, bid_b, conn, llm_url, llm_model,
                 prompt_template):
    cur = conn.cursor()
    a = _fetch_card(name_a, bid_a, cur)
    b = _fetch_card(name_b, bid_b, cur)

    print(f"\nCard A: {card_desc(a)}")
    print(f"Card B: {card_desc(b)}\n")

    power_creep, justification, llm_response = detect_power_creep(
        a, b, llm_url, llm_model, prompt_template,
    )

    print(f"[{power_creep}]")
    print(f"Justification: {justification}")
    if llm_response:
        print(f"LLM response:  {json.dumps(llm_response)}")

    return power_creep, justification, llm_response


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--compare", nargs=4,
        metavar=("NAME_A", "BUILD_A", "NAME_B", "BUILD_B"),
        help="Compare two specific card versions, bypassing the full scan.",
    )
    ap.add_argument(
        "--mana-window", type=int, default=MANA_WINDOW,
        help=f"Max mana difference between candidate pairs (default: {MANA_WINDOW}).",
    )
    ap.add_argument(
        "--allow-neutral-cross-class", action="store_true",
        help=("Also compare Neutral cards with class cards (not different named classes); "
              "needed for the class-identity homogenization measure."),
    )
    ap.add_argument("--llm-url",   metavar="URL",   help="LLM endpoint URL (default: auto-detect a running vLLM job).")
    ap.add_argument("--llm-model", metavar="MODEL", help="Model name.")
    ap.add_argument(
        "--workers", type=int, default=32,
        help="Concurrent LLM requests during the full scan (default: 32).",
    )
    ap.add_argument(
        "--stop-at-cross-card-cross-patch", type=int, metavar="COUNT",
        help=("Optionally stop after writing COUNT cross-card-cross-patch positive "
              "results (default: no limit; scan all candidates)."),
    )
    args = ap.parse_args()

    if (args.stop_at_cross_card_cross_patch is not None
            and args.stop_at_cross_card_cross_patch < 1):
        ap.error("--stop-at-cross-card-cross-patch must be at least 1")


    db_path     = os.environ.get("DB_PATH", str(REPO / "data" / "powercreep.duckdb"))
    try:
        llm_url = resolve_llm_url(args.llm_url)
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    llm_model   = args.llm_model or "deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
    mana_window = args.mana_window
    prompt_template = (REPO / "prompts" / "power_creep_detection.md").read_text()
    class_sql = class_compatible_sql(args.allow_neutral_cross_class)

    print(f"LLM endpoint:  {llm_url}")
    print(f"LLM model:     {llm_model}")
    print(f"Mana window:   ±{mana_window}")

    print("Checking vLLM server... ", end="", flush=True)
    try:
        req = urllib.request.Request(
            f"{llm_url}/models",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        served = [m["id"] for m in body.get("data", [])]
        if llm_model not in served:
            print(f"WARNING: model {llm_model!r} not in served models: {served}")
        else:
            print(f"OK (model {llm_model!r} available)")
    except Exception as e:
        print(f"FAILED\n\nvLLM server at {llm_url} is not reachable: {e}")
        print("Make sure the vLLM SLURM job is running (sbatch jobs/vllm.job).")
        sys.exit(1)

    conn = duckdb.connect(db_path, read_only=True)

    if args.compare:
        name_a, bid_a, name_b, bid_b = args.compare
        _run_compare(
            name_a, bid_a, name_b, bid_b, conn, llm_url, llm_model,
            prompt_template,
        )
        conn.close()
        return

    cards_total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

    # Total candidate pairs (for ETA) — same predicate as the per-card partner
    # query below, so the count matches exactly what the scan will generate.
    total = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM cards a JOIN cards b
         ON b.type  = a.type
         AND {class_sql}
         AND ((a.rarity = 'LEGENDARY') = (b.rarity = 'LEGENDARY'))
         AND ABS(COALESCE(b.mana, 0) - COALESCE(a.mana, 0)) <= ?
         AND {STAT_DOMINANCE_SQL}
         AND {TRIBE_DIRECTION_COMPATIBLE_SQL}
         AND (b.name > a.name OR (b.name = a.name AND b.patch_id > a.patch_id))
        """,
        (mana_window,),
    ).fetchone()[0]

    print(f"{cards_total} card versions → {total} candidate pairs")
    print(f"Workers:       {args.workers}\n")

    outer_cur  = conn.cursor()
    search_cur = conn.cursor()

    outer_cur.execute(
            f"SELECT {SELECT_COLS} FROM cards ORDER BY class, type, name, mana"
        )

    outer_cols = [d[0] for d in outer_cur.description]

    def pair_stream():
        """Yield every unordered candidate pair exactly once (main-thread DB use only)."""
        while True:
            batch = outer_cur.fetchmany(500)
            if not batch:
                return
            for row in batch:
                card_a = dict(zip(outer_cols, row))
                mana_a = card_a.get("mana") or 0
                # Only fetch partners that sort strictly after card_a by its
                # (name, patch_id) primary key, so each unordered pair is
                # generated exactly once — no in-memory dedup needed.
                partner_class_sql = (
                    """
                    AND (
                        COALESCE(class, 'NEUTRAL') = ?
                        OR COALESCE(class, 'NEUTRAL') = 'NEUTRAL'
                        OR ? = 'NEUTRAL'
                    )
                    """
                    if args.allow_neutral_cross_class else
                    "AND class = ?"
                )
                partner_class_params = (
                    (card_a.get("class") or "NEUTRAL", card_a.get("class") or "NEUTRAL")
                    if args.allow_neutral_cross_class else (card_a.get("class") or "NEUTRAL",)
                )
                search_cur.execute(
                    f"""
                    SELECT {SELECT_COLS} FROM cards
                    WHERE type  = ?
                      {partner_class_sql}
                      AND ((rarity = 'LEGENDARY') = (? = 'LEGENDARY'))
                      AND ABS(COALESCE(mana, 0) - ?) <= ?
                      AND (
                          ? IS NULL OR tribe IS NULL
                          OR tribe = ?
                          OR ? = 'ALL' OR tribe = 'ALL'
                      )
                      AND (
                          (
                              mana IS NOT NULL AND ? IS NOT NULL
                              AND mana <= ?
                              AND (
                                  ? NOT IN ('MINION', 'WEAPON', 'HERO')
                                  OR (
                                      attack IS NOT NULL AND ? IS NOT NULL
                                      AND health IS NOT NULL AND ? IS NOT NULL
                                      AND attack >= ? AND health >= ?
                                  )
                              )
                          )
                          OR
                          (
                              mana IS NOT NULL AND ? IS NOT NULL
                              AND ? <= mana
                              AND (
                                  ? NOT IN ('MINION', 'WEAPON', 'HERO')
                                  OR (
                                      attack IS NOT NULL AND ? IS NOT NULL
                                      AND health IS NOT NULL AND ? IS NOT NULL
                                      AND ? >= attack AND ? >= health
                                  )
                              )
                          )
                      )
                      AND (name > ? OR (name = ? AND patch_id > ?))
                    """,
                    (
                        card_a["type"],
                        *partner_class_params,
                        card_a.get("rarity"),
                        mana_a,
                        mana_window,
                        card_a.get("tribe"),
                        card_a.get("tribe"),
                        card_a.get("tribe"),
                        card_a.get("mana"), card_a.get("mana"), card_a["type"],
                        card_a.get("attack"), card_a.get("health"),
                        card_a.get("attack"), card_a.get("health"),
                        card_a.get("mana"), card_a.get("mana"), card_a["type"],
                        card_a.get("attack"), card_a.get("health"),
                        card_a.get("attack"), card_a.get("health"),
                        card_a["name"],
                        card_a["name"],
                        card_a["patch_id"],
                    ),
                )
                search_cols = [d[0] for d in search_cur.description]
                for row_b in search_cur.fetchall():
                    yield card_a, dict(zip(search_cols, row_b))

    def judge(pair):
        """Worker: pure comparison (no DB). Runs in a thread."""
        card_a, card_b = pair
        power_creep, justification, llm_response = detect_power_creep(
            card_a, card_b, llm_url, llm_model, prompt_template,
        )
        if power_creep == "POSITIVE" and llm_response is not None:
            baseline = llm_response["comparison"]["baseline"]
            if (baseline.get("name"), baseline.get("patch_id")) == (
                    card_b.get("name"), card_b.get("patch_id")):
                card_a, card_b = card_b, card_a
        return card_a, card_b, power_creep, justification, llm_response

    found = processed = cross_card_cross_patch_found = 0
    t0 = last_report = time.time()

    print(f"{'PROG':>16}  {'%':>5}  {'FOUND':>6}  {'ETA':>7}")
    print("-" * 65)
    sys.stdout.flush()

    with OUTPUT.open("w") as out:
        def handle(fut):
            nonlocal found, processed, cross_card_cross_patch_found, last_report
            card_a, card_b, power_creep, justification, llm_response = fut.result()
            processed += 1

            if power_creep == "POSITIVE":
                result_type = comparison_type(card_a, card_b)
                record = {
                    "power_creep": power_creep,
                    "comparison_type": result_type,
                    "comparison_type_label": result_type,
                    # Keep rarity in the public result schema: it is needed for
                    # the downstream collection-cost and rarity-inflation metrics.
                    "card_a": {k: card_a.get(k) for k in ("name", "patch_id", "type", "mana", "attack", "health", "card_set", "class", "rarity", "text", "tribe")},
                    "card_b": {k: card_b.get(k) for k in ("name", "patch_id", "type", "mana", "attack", "health", "card_set", "class", "rarity", "text", "tribe")},
                    "justification": justification,
                }
                if llm_response is not None:
                    record["llm_response"] = llm_response
                out.write(json.dumps(record) + "\n")
                out.flush()
                print(f"  [POSITIVE]  {card_a['name']!r:30s} vs {card_b['name']!r:30s}  {justification}")
                sys.stdout.flush()
                found += 1
                if result_type == CROSS_CARD_CROSS_PATCH:
                    cross_card_cross_patch_found += 1
                    if (args.stop_at_cross_card_cross_patch is not None
                            and cross_card_cross_patch_found >= args.stop_at_cross_card_cross_patch):
                        print(
                            "Reached "
                            f"{cross_card_cross_patch_found} cross-card-cross-patch "
                            "positive results; stopping.",
                            flush=True,
                        )
                        return False

            now = time.time()
            if processed % 200 == 0 or processed == total or (now - last_report) >= 30:
                last_report = now
                elapsed = now - t0
                rate    = processed / elapsed if elapsed > 0 else 0
                eta     = (total - processed) / rate if rate > 0 else 0
                eta_str = f"{eta:.0f}s" if eta < 3600 else f"{eta/3600:.1f}h"
                print(
                    f"{processed:>7}/{total}  {processed/total*100:>5.1f}%"
                    f"  {found:>6}  {eta_str:>7}",
                )
                sys.stdout.flush()
                out.flush()
            return True


        max_inflight = args.workers * 4
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            inflight = set()
            stopped = False
            for pair in pair_stream():
                inflight.add(ex.submit(judge, pair))
                if len(inflight) >= max_inflight:
                    done, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                    for fut in done:
                        if not handle(fut):
                            stopped = True
                            break
                if stopped:
                    break
            if not stopped:
                for fut in as_completed(inflight):
                    if not handle(fut):
                        break

    print(f"\nDone. {found} power creep pairs → {OUTPUT}")
    outer_cur.close()
    search_cur.close()
    conn.close()


if __name__ == "__main__":
    main()
