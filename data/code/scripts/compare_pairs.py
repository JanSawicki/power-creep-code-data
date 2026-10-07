#!/usr/bin/env python3
"""Directional card-effect comparison.

Each pair is evaluated in both orientations. A pair is ``POSITIVE`` when
either candidate is judged to preserve the baseline effect while improving it;
otherwise it is ``NEGATIVE``. Each direction makes one LLM judge request.
"""

import json
import re
import urllib.request

# The JSON response is intentionally brief.
JUDGE_MAX_TOKENS = 1024
JUDGE_MAX_TOKENS_RETRY = 1536


class JudgeParseError(Exception):
    """The evaluator returned invalid JSON after both token-budget attempts."""


def _post_chat_completion(llm_url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{llm_url}/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def _run_judge(llm_url: str, model: str, prompt: str, max_tokens: int) -> str:
    response = _post_chat_completion(llm_url, {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a Hearthstone card evaluator. Return the required JSON object only; do not call tools."},
            {"role": "user", "content": prompt},
        ], "temperature": 0.0, "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    })
    return (response["choices"][0]["message"].get("content") or "").strip()


def _parse_judge_response(raw: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    if not raw.startswith("{") and "{" in raw:
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    parsed = json.loads(raw.strip())
    if not isinstance(parsed, dict):
        raise ValueError("response is not an object")
    answer = str(parsed.get("is_effect_better", "")).strip().upper()
    justification = parsed.get("justification")
    if answer not in ("YES", "NO"):
        raise ValueError("is_effect_better must be YES or NO")
    if not isinstance(justification, str) or not justification.strip():
        raise ValueError("justification must be a non-empty string")
    parsed["is_effect_better"] = answer
    parsed["justification"] = justification.strip()
    return parsed


def llm_judge(baseline: dict, candidate: dict, llm_url: str,
              model: str, prompt_template: str) -> tuple[bool, str, dict]:
    """Ask whether candidate is better than baseline."""
    baseline_effect = (baseline.get("text") or "").strip() or "NONE"
    candidate_effect = (candidate.get("text") or "").strip() or "NONE"

    def judge_card(card: dict, effect: str) -> str:
        stats = [f"Mana {card.get('mana', '?')}"]
        if card.get("type") in ("MINION", "WEAPON", "HERO"):
            stats.extend((f"Attack {card.get('attack', '?')}",
                          f"Health {card.get('health', '?')}"))
        return f"{' ; '.join(stats)}. Effect: {effect}"

    baseline_text = judge_card(baseline, baseline_effect)
    candidate_text = judge_card(candidate, candidate_effect)
    prompt = prompt_template.replace("{effect_A}", baseline_text)
    prompt = prompt.replace("{effect_B}", candidate_text)
    last_error = None
    for budget in (JUDGE_MAX_TOKENS, JUDGE_MAX_TOKENS_RETRY):
        try:
            raw_detector_response = _run_judge(llm_url, model, prompt, budget)
            response = _parse_judge_response(raw_detector_response)
            response["detector_llm_response"] = raw_detector_response
            baseline_comparison = {
                key: baseline.get(key)
                for key in ("name", "patch_id", "mana", "attack", "health", "text")
                if baseline.get(key) is not None
            }
            candidate_comparison = {
                key: candidate.get(key)
                for key in ("name", "patch_id", "mana", "attack", "health", "text")
                if candidate.get(key) is not None
            }
            response["comparison"] = {
                "baseline": baseline_comparison,
                "candidate": candidate_comparison,
            }
            return response["is_effect_better"] == "YES", response["justification"], response
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    raise JudgeParseError(f"invalid judge response after retry: {last_error}")


def compare_direction(baseline: dict, candidate: dict, llm_url: str,
                      model: str, prompt_template: str) -> tuple[bool, str, dict | None]:
    """Evaluate one baseline → candidate direction."""
    return llm_judge(baseline, candidate, llm_url, model, prompt_template)


def candidate_stats_noninferior(baseline: dict, candidate: dict) -> bool:
    """Return whether ``candidate`` is not worse on printed, comparable stats.

    Effects are judged by the LLM, but a directional ``YES`` only supports
    power creep when it points toward the card with non-worse stats. Mana is
    better when lower; attack and health/durability are better when higher.
    Missing values are left to the scan's SQL pre-filter, which only yields
    pairs with the relevant printed stats present.
    """
    baseline_mana = baseline.get("mana")
    candidate_mana = candidate.get("mana")
    if (baseline_mana is not None and candidate_mana is not None
            and candidate_mana > baseline_mana):
        return False

    if baseline.get("type") in ("MINION", "WEAPON", "HERO"):
        for stat in ("attack", "health"):
            baseline_stat = baseline.get(stat)
            candidate_stat = candidate.get(stat)
            if (baseline_stat is not None and candidate_stat is not None
                    and candidate_stat < baseline_stat):
                return False
    return True


def candidate_tribe_noninferior(baseline: dict, candidate: dict) -> bool:
    """Return whether a candidate preserves the baseline card's tribe synergy.

    A card without a tribe has no tribe requirement.  A candidate with the
    ALL tribe preserves every named tribe, but a named tribe cannot replace a
    different named tribe (or ALL) without introducing a trade-off.
    """
    baseline_tribe = baseline.get("tribe")
    candidate_tribe = candidate.get("tribe")
    return (
        baseline_tribe is None
        or candidate_tribe == baseline_tribe
        or candidate_tribe == "ALL"
    )


def candidate_has_effect_when_baseline_does(baseline: dict, candidate: dict) -> bool:
    """Reject an empty candidate effect in place of meaningful baseline text."""
    baseline_text = (baseline.get("text") or "").strip()
    candidate_text = (candidate.get("text") or "").strip()
    return not baseline_text or bool(candidate_text)


def _normalized_effect(card: dict) -> str:
    """Normalize enough markup to test whether an effect was preserved."""
    text = (card.get("text") or "").casefold()
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _strictly_better_printed_stats(baseline: dict, candidate: dict) -> bool:
    if not candidate_stats_noninferior(baseline, candidate):
        return False
    if (baseline.get("mana") is not None and candidate.get("mana") is not None
            and candidate["mana"] < baseline["mana"]):
        return True
    if baseline.get("type") in ("MINION", "WEAPON", "HERO"):
        return any(
            baseline.get(stat) is not None and candidate.get(stat) is not None
            and candidate[stat] > baseline[stat]
            for stat in ("attack", "health")
        )
    return False


def _strict_stat_and_effect_superset(baseline: dict, candidate: dict) -> bool:
    """Recognize upgrades that require no semantic judgment.

    If all baseline effect text is literally preserved, the candidate adds
    text, and at least one printed stat improves with none worsening, no LLM
    interpretation is needed.  This protects clear cases such as Spellbreaker
    → Royal Librarian from a judge that treats Tradeable as merely situational.
    """
    baseline_effect = _normalized_effect(baseline)
    candidate_effect = _normalized_effect(candidate)
    return (
        bool(baseline_effect)
        and baseline_effect in candidate_effect
        and baseline_effect != candidate_effect
        and _strictly_better_printed_stats(baseline, candidate)
        and candidate_tribe_noninferior(baseline, candidate)
    )


def _comparison_snapshot(card: dict) -> dict:
    return {
        key: card.get(key)
        for key in ("name", "patch_id", "mana", "attack", "health", "text")
        if card.get(key) is not None
    }


def detect_power_creep(a: dict, b: dict, llm_url: str, model: str,
                       prompt_template: str) -> tuple[str, str, dict | None]:
    """Evaluate both directions and return only POSITIVE or NEGATIVE.

    A directional LLM ``YES`` is accepted only if its candidate also has
    non-worse printed stats than its baseline in that same direction.
    """
    evaluations = []
    positive_response = None
    positive_reason = None
    try:
        for baseline, candidate in ((a, b), (b, a)):
            if _strict_stat_and_effect_superset(baseline, candidate):
                positive_reason = (
                    "Candidate preserves the complete baseline effect, adds "
                    "an effect, and improves printed stats without a drawback."
                )
                positive_response = {
                    "is_effect_better": "YES",
                    "justification": positive_reason,
                    "detector": "deterministic_strict_stat_and_effect_superset",
                    "comparison": {
                        "baseline": _comparison_snapshot(baseline),
                        "candidate": _comparison_snapshot(candidate),
                    },
                }
                break
            positive, reason, response = compare_direction(
                baseline, candidate, llm_url, model, prompt_template,
            )
            if positive and not candidate_has_effect_when_baseline_does(baseline, candidate):
                evaluations.append(
                    f"LLM YES rejected because the candidate has no effect text: {reason}"
                )
                continue
            if positive and not candidate_stats_noninferior(baseline, candidate):
                evaluations.append(
                    f"LLM YES rejected because the candidate has worse printed stats: {reason}"
                )
                continue
            if positive and not candidate_tribe_noninferior(baseline, candidate):
                evaluations.append(
                    f"LLM YES rejected because the candidate loses tribe synergy: {reason}"
                )
                continue
            evaluations.append(reason)
            if positive and positive_response is None:
                positive_response = response
                positive_reason = reason
    except JudgeParseError as exc:
        return "NEGATIVE", str(exc), None
    if positive_response is not None:
        return "POSITIVE", positive_reason, positive_response
    return "NEGATIVE", "; ".join(evaluations), None
