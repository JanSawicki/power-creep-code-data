"""Regression checks for the economic meaning of the replacement scenario."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analysis import conversion_cost, unique_pairs


def pair(a_rarity="COMMON", b_rarity="COMMON", a_set="Classic", b_set="Titans"):
    return {
        "card_a": {"name": "Baseline", "rarity": a_rarity, "card_set": a_set, "type": "MINION", "class": "NEUTRAL"},
        "card_b": {"name": "Candidate", "rarity": b_rarity, "card_set": b_set, "type": "MINION", "class": "NEUTRAL"},
        "comparison_type": "cross-card-same-patch",
    }


class ConversionCostTests(unittest.TestCase):
    def test_equal_rarity_is_not_free_conversion(self):
        for rarity, expected in [("COMMON", 35), ("RARE", 80), ("EPIC", 300), ("LEGENDARY", 1200)]:
            with self.subTest(rarity=rarity):
                cost, _ = conversion_cost(pair(rarity, rarity))
                self.assertEqual(cost.additional_dust, expected)

    def test_cheaper_candidate_can_still_require_dust(self):
        cost, _ = conversion_cost(pair("RARE", "COMMON"))
        self.assertEqual(cost.additional_dust, 20)

    def test_surplus_is_preserved_without_negative_additional_cost(self):
        cost, _ = conversion_cost(pair("LEGENDARY", "COMMON"))
        self.assertEqual(cost.net_dust, -360)
        self.assertEqual(cost.additional_dust, 0)

    def test_core_and_free_baselines_cannot_fund_replacement(self):
        for rarity, card_set in [("LEGENDARY", "Core"), ("COMMON", "Basic"), ("FREE", "Legacy")]:
            with self.subTest(card_set=card_set):
                cost, _ = conversion_cost(pair(rarity, "RARE", card_set))
                self.assertEqual(cost.baseline_recovery, 0)
                self.assertEqual(cost.additional_dust, 100)

    def test_noncraftable_candidates_are_excluded_not_treated_as_free_crafts(self):
        for rarity, card_set in [("LEGENDARY", "Core"), ("COMMON", "Basic"), ("FREE", "Legacy")]:
            with self.subTest(card_set=card_set):
                cost, reason = conversion_cost(pair(b_rarity=rarity, b_set=card_set))
                self.assertIsNone(cost)
                self.assertEqual(reason, "Candidate has no ordinary crafting route")

    def test_unresolved_acquisition_or_rarity_is_not_guessed(self):
        for record in [pair(a_set="Core Hidden"), pair(b_set="Core Hidden"), pair(a_set="Event"),
                       pair(a_set=""), pair(b_set=None), pair(a_rarity="UNKNOWN")]:
            with self.subTest(record=record):
                self.assertIsNone(conversion_cost(record)[0])

    def test_same_card_history_is_not_a_purchase(self):
        record = pair()
        record["card_b"]["name"] = "Baseline"
        self.assertIsNone(conversion_cost(record)[0])

    def test_repeated_snapshots_do_not_multiply_conversion_pairs(self):
        first, second = pair(), pair()
        first["card_a"]["patch_id"] = "1.0.0.1"
        second["card_a"]["patch_id"] = "2.0.0.2"
        self.assertEqual(len(unique_pairs([first, second])), 1)


if __name__ == "__main__":
    unittest.main()
