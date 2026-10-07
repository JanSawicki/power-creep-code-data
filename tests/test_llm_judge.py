#!/usr/bin/env python3
"""Run directional LLM effect comparisons and report results.

Reads one effect-only fixture per JSON file from a directory
(``tests/llm_judge_pairs/``):
    {"effect_a": "...", "effect_b": "...",
     "expected": {"is_effect_better": "YES|NO"}}
It calls ``llm_judge`` directly with only those two effect strings. Card
names, stats, classes, tribes, sets, patches, and bidirectional aggregation
are intentionally excluded from this test. No DB access is required.
Exit code is non-zero if any case fails.

The vLLM endpoint is auto-detected from a running SLURM job (see
scripts/vllm_endpoint.py); pass --llm-url to override.

Usage:
    python tests/test_llm_judge.py
    python tests/test_llm_judge.py --pairs tests/llm_judge_pairs
    python tests/test_llm_judge.py --llm-url http://localhost:18000/v1
    python tests/test_llm_judge.py --workers 32

Every run overwrites ``logs/test_llm_judge.log`` with the complete report.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from detection_prompt import PROMPT_TEMPLATE
from compare_pairs import llm_judge
from vllm_endpoint import resolve_llm_url

REPO = Path(__file__).parent.parent
DEFAULT_PAIRS = Path(__file__).parent / "llm_judge_pairs"
DEFAULT_LOG = REPO / "logs" / "test_llm_judge.log"
class Tee:
    """Write test output to the terminal and a report file."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            stream.write(value)
        return len(value)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return any(stream.isatty() for stream in self.streams)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS,
                    help=f"Directory of per-pair JSON files (default: {DEFAULT_PAIRS})")
    ap.add_argument("--llm-url", metavar="URL",
                    help="LLM endpoint URL (default: auto-detect a running vLLM job)")
    ap.add_argument("--llm-model", metavar="MODEL",
                    help="Model name (default: deepseek-ai/DeepSeek-R1-Distill-Llama-70B)")
    ap.add_argument("--prompt", type=Path, default=None,
                    help="Optional external prompt template file (default: built-in detector prompt)")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG,
                    help=f"Report file, overwritten on every run (default: {DEFAULT_LOG})")
    ap.add_argument("--workers", type=int, default=32,
                    help="Concurrent fixture evaluations (default: 32; use 1 for sequential output)")
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers must be at least 1")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    original_stdout = sys.stdout
    with args.log.open("w") as log_file:
        sys.stdout = Tee(original_stdout, log_file)
        try:
            _run(args)
        finally:
            sys.stdout = original_stdout


def _run(args):
    """Execute the fixture suite after output has been configured."""
    print(f"Results log:   {args.log.resolve()}")

    try:
        llm_url = resolve_llm_url(args.llm_url)
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    llm_model = args.llm_model or "deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
    prompt_template = args.prompt.read_text() if args.prompt else PROMPT_TEMPLATE

    pair_files = sorted(args.pairs.glob("*.json"))
    if not pair_files:
        print(f"\nNo pair files found in {args.pairs}/")
        sys.exit(1)
    pairs = [(f.stem, json.loads(f.read_text())) for f in pair_files]
    print(f"LLM endpoint:  {llm_url}")
    print(f"LLM model:     {llm_model}")
    print(f"Pairs dir:     {args.pairs}")
    print(f"Total pairs:   {len(pairs)}")
    print(f"Workers:       {args.workers}")

    worker_args = [
        (
            i, len(pairs), fixture_id, pair, llm_url, llm_model,
            prompt_template,
        )
        for i, (fixture_id, pair) in enumerate(pairs)
    ]
    # Worker reports stay buffered so the final log remains in fixture order,
    # but report completed work in 5% increments while requests are in flight.
    progress_interval = max(1, len(worker_args) // 20)
    evaluated = [None] * len(worker_args)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_evaluate_pair, worker_arg): index
            for index, worker_arg in enumerate(worker_args)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            evaluated[futures[future]] = future.result()
            if completed % progress_interval == 0 or completed == len(worker_args):
                print(f"Progress: {completed}/{len(worker_args)} fixtures complete", flush=True)

    # Workers never write to stdout. Emit their buffered reports in fixture
    # order, keeping logs deterministic and readable even with many requests.
    results = []
    for report, result in evaluated:
        print(report, end="")
        results.append(result)
    sys.stdout.flush()

    print(f"\n{'='*70}")
    print("SUMMARY")
    print("=" * 70)
    for i, r in enumerate(results):
        label = f"Fixture {r['fixture_id']}"
        got = r["is_effect_better"]
        if not r["has_expected"]:
            mark = "  ? "
        elif r["passed"]:
            mark = "PASS"
        else:
            mark = "FAIL"
        line = f"  {i+1:>2}. {mark}  {label:<58s} {got}"
        if r["has_expected"] and not r["passed"]:
            line += f"   (expected is_effect_better={r['expected_is_effect_better']})"
        print(line)
        llm = r.get("llm_response")
        if llm:
            print(f"      is_effect_better: {llm.get('is_effect_better', '?')}")
            print(f"      justification: {llm.get('justification', '')}")

    checked = [r for r in results if r["has_expected"]]
    n_pass = sum(1 for r in checked if r["passed"])
    n_fail = len(checked) - n_pass
    n_unchecked = len(results) - len(checked)

    # Treat YES (effect B is better) as the positive class.  An ERROR is
    # always incorrect for accuracy; it also contributes a false negative
    # when the expected result was YES.  This preserves useful metrics even
    # when an endpoint returns a malformed response for a fixture.
    true_positive = sum(
        r["expected_is_effect_better"] == "YES" and r["is_effect_better"] == "YES"
        for r in checked
    )
    false_positive = sum(
        r["expected_is_effect_better"] == "NO" and r["is_effect_better"] == "YES"
        for r in checked
    )
    false_negative = sum(
        r["expected_is_effect_better"] == "YES" and r["is_effect_better"] != "YES"
        for r in checked
    )
    true_negative = sum(
        r["expected_is_effect_better"] == "NO" and r["is_effect_better"] == "NO"
        for r in checked
    )
    accuracy = n_pass / len(checked) if checked else 0.0
    precision_denominator = true_positive + false_positive
    precision = true_positive / precision_denominator if precision_denominator else 0.0
    recall_denominator = true_positive + false_negative
    recall = true_positive / recall_denominator if recall_denominator else 0.0

    print(f"\n  PASS: {n_pass}  |  FAIL: {n_fail}"
          + (f"  |  no-expected: {n_unchecked}" if n_unchecked else ""))
    print(f"  Accuracy:  {accuracy:.2%}")
    print(f"  Precision: {precision:.2%}  (YES is positive)")
    print(f"  Recall:    {recall:.2%}  (YES is positive)")
    print("  Confusion matrix: "
          f"TP={true_positive}  FP={false_positive}  "
          f"FN={false_negative}  TN={true_negative}")

    sys.exit(1 if n_fail else 0)


def _evaluate_pair(args):
    """Evaluate one fixture without writing to shared stdout.

    Returning a rendered report lets the main thread retain deterministic log
    order while the network-bound LLM requests execute concurrently.
    """
    (
        index, total, fixture_id, pair, llm_url, llm_model, prompt_template,
    ) = args
    effect_a = pair["effect_a"]
    effect_b = pair["effect_b"]
    expected = pair.get("expected") or {}
    report = io.StringIO()

    print(f"\n{'=' * 70}", file=report)
    print(f"Fixture {index + 1}/{total}: {fixture_id}", file=report)
    print("=" * 70, file=report)
    print(f"\nEffect A: {effect_a or 'NONE'}", file=report)
    print(f"Effect B: {effect_b or 'NONE'}", file=report)
    print(file=report)

    try:
        is_effect_better, justification, llm_response = llm_judge(
            {"text": effect_a}, {"text": effect_b},
            llm_url, llm_model, prompt_template,
        )
        verdict = "YES" if is_effect_better else "NO"
        print(f"[is_effect_better={verdict}]", file=report)
        print(f"Justification: {justification}", file=report)
        print("Detector LLM response (raw):", file=report)
        print(llm_response.get("detector_llm_response", "<not captured>"), file=report)
        print("LLM response:", file=report)
        print(json.dumps(llm_response, indent=2, sort_keys=True), file=report)
    except Exception as e:
        print(f"  ERROR: {e}", file=report)
        verdict, justification, llm_response = "ERROR", str(e), None

    expected_verdict = expected.get("is_effect_better")
    passed = verdict == expected_verdict
    if expected:
        status = "PASS" if passed else "FAIL"
        print(f"Expected is_effect_better: {expected_verdict}   → {status}", file=report)

    result = {
        "fixture_id": fixture_id,
        "is_effect_better": verdict,
        "expected_is_effect_better": expected_verdict,
        "has_expected": bool(expected), "passed": passed,
        "justification": justification, "llm_response": llm_response,
    }
    return report.getvalue(), result


if __name__ == "__main__":
    main()
