#!/usr/bin/env python3
"""Rejudge every detection solely as an A → B effect comparison.

This intentionally ignores patch order, card names, sets, and detector
comparison type. A later nerf can therefore be retained when its saved
orientation correctly has an older, stronger B; conversely, an apparent buff
is rejected if B is not a clear upgrade of A.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from compare_pairs import JudgeParseError, llm_judge
from vllm_endpoint import resolve_llm_url


REPO = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO / "data" / "powercreep_results.jsonl"
DEFAULT_OUTPUT = REPO / "data" / "powercreep_results_reviewed.jsonl"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-R1-Distill-Llama-70B"

AUDIT_PROMPT = """You are strictly auditing one proposed power-creep relation.

Ignore the card names, patches, sets, and any comparison category. Assess
only whether B is a clear upgrade of A as printed. Return YES only when B
preserves every beneficial part of A and improves it without a compensating
drawback. Lower Mana and higher Attack or Health are improvements. An added
conditional bonus is fine only if B already preserves A's unconditional or
less-restricted effect. A different target, trigger, condition, randomization,
loss of a keyword, drawback, or trade-off is NOT an upgrade. Do not treat a
word merely mentioned in a negative or conditional clause as preservation.
When uncertain, return NO.

A: `{effect_A}`
B: `{effect_B}`

Return exactly this JSON object:
{{"is_effect_better":"YES or NO","justification":"brief reason"}}"""


def judge(record: dict, llm_url: str, model: str) -> bool:
    positive, _reason, _response = llm_judge(
        record["card_a"], record["card_b"], llm_url, model, AUDIT_PROMPT
    )
    return positive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--llm-url", help="vLLM base URL; defaults to the active endpoint")
    parser.add_argument("--llm-model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    try:
        llm_url = resolve_llm_url(args.llm_url)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    records = [json.loads(line) for line in args.input.open(encoding="utf-8") if line.strip()]
    decisions: list[bool | None] = [None] * len(records)
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(judge, record, llm_url, args.llm_model): index
            for index, record in enumerate(records)
        }
        for completed in as_completed(futures):
            index = futures[completed]
            try:
                decisions[index] = completed.result()
            except (JudgeParseError, OSError, ValueError, KeyError) as exc:
                failures.append(f"line {index + 1}: {exc}")

    kept = [json.dumps(record, ensure_ascii=False) + "\n"
            for record, accepted in zip(records, decisions) if accepted]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.output.parent, delete=False
    ) as temporary:
        temporary.writelines(kept)
        temporary_path = Path(temporary.name)
    temporary_path.replace(args.output)
    print(
        f"Rejudged {len(records)} effect comparisons; retained {len(kept)} "
        f"({len(failures)} judge failures treated as NO) → {args.output}"
    )


if __name__ == "__main__":
    main()
