"""Tests for evals/stats.py. Run with: python3 -m unittest discover tests"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import stats  # noqa: E402


class VectorTests(unittest.TestCase):
    """Hand-computed test vectors."""

    def test_kappa_table_20_5_10_15_is_0_4(self):
        a = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
        b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
        self.assertEqual(stats.cohen_kappa(a, b), 0.4)

    def test_wilcoxon_all_positive_1_to_5(self):
        w = stats.wilcoxon([1, 2, 3, 4, 5])
        self.assertEqual((w["w_plus"], w["w_minus"], w["p"], w["method"]), (15.0, 0.0, 0.0625, "exact"))

    def test_wilcoxon_one_negative(self):
        w = stats.wilcoxon([1, -2, 3, 4, 5])
        self.assertEqual((w["w_minus"], w["p"]), (2.0, 0.1875))

    def test_wilcoxon_drops_zeros(self):
        w = stats.wilcoxon([0, 1, 2])
        self.assertEqual((w["n"], w["w_plus"], w["p"]), (2, 3.0, 0.5))

    def test_bootstrap_of_a_constant_is_that_constant_and_is_repeatable(self):
        self.assertEqual(stats.bootstrap_ci([0.2] * 10), (0.2, 0.2))
        data = [0.1, 0.4, 0.2, 0.9, 0.3]
        self.assertEqual(stats.bootstrap_ci(data, rng_seed=7), stats.bootstrap_ci(data, rng_seed=7))
        self.assertNotEqual(stats.bootstrap_ci(data, rng_seed=7), stats.bootstrap_ci(data, rng_seed=8))

    def test_holm(self):
        self.assertEqual(stats.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])

    def test_equivalence(self):
        self.assertTrue(stats.equivalent([0.05] * 10, 0.10))
        self.assertFalse(stats.equivalent([0.15] * 10, 0.10))

    def test_n_required(self):
        self.assertEqual(stats.n_required(0.2, 0.1), 32)
        self.assertEqual(stats.n_required(0.1, 0.1), 8)


class DetailTests(unittest.TestCase):
    def test_kappa_edges(self):
        self.assertEqual(stats.cohen_kappa(["a", "b"], ["a", "b"]), 1.0)
        self.assertEqual(stats.cohen_kappa(["a", "a"], ["a", "a"]), 1.0)  # chance agreement is 1: still perfect
        self.assertEqual(stats.cohen_kappa(["a", "b"], ["b", "a"]), -1.0)
        with self.assertRaises(ValueError):
            stats.cohen_kappa(["a"], ["a", "b"])
        with self.assertRaises(ValueError):
            stats.cohen_kappa([], [])

    def test_wilcoxon_exact_small_cases(self):
        self.assertEqual(stats.wilcoxon([1, 2, 3])["p"], 0.25)  # 2 / 2**3
        self.assertEqual(stats.wilcoxon([3, -2, 1])["p"], 0.75)  # W- = 2: {}, {1}, {2} are 3 of 8 patterns, doubled
        self.assertEqual(stats.wilcoxon([])["p"], 1.0)
        self.assertEqual(stats.wilcoxon([0, 0])["n"], 0)

    def test_wilcoxon_ties_use_the_normal_approximation_with_tie_correction(self):
        # four equal positive values: ranks 2.5 each, W+ = 10, mu = 5, variance 7.5 - 1.25 = 6.25, z = (5 - 0.5) / 2.5 = 1.8
        w = stats.wilcoxon([1, 1, 1, 1])
        self.assertEqual(w["method"], "normal")
        self.assertAlmostEqual(w["p"], 0.07186, places=4)

    def test_wilcoxon_beyond_25_pairs_is_approximate(self):
        w = stats.wilcoxon(list(range(1, 27)))
        self.assertEqual((w["n"], w["method"]), (26, "normal"))
        self.assertLess(w["p"], 0.001)
        self.assertEqual(stats.wilcoxon(list(range(1, 26)))["method"], "exact")

    def test_bootstrap_interval_orders_and_empty_input(self):
        lo, hi = stats.bootstrap_ci([0.0, 1.0, 0.5, 0.25, 0.75, 0.6, 0.4], rng_seed=3)
        self.assertLessEqual(lo, 0.5)
        self.assertGreaterEqual(hi, 0.5)
        lo90, hi90 = stats.bootstrap_ci([0.0, 1.0, 0.5, 0.25, 0.75, 0.6, 0.4], alpha=0.5, rng_seed=3)
        self.assertGreaterEqual(lo90, lo)
        self.assertLessEqual(hi90, hi)
        self.assertEqual(stats.bootstrap_ci([]), (None, None))

    def test_holm_is_monotone_capped_and_in_input_order(self):
        self.assertEqual(stats.holm([0.5, 0.5, 0.5]), [1.0, 1.0, 1.0])
        self.assertEqual(stats.holm([0.04, 0.001]), [0.04, 0.002])
        self.assertEqual(stats.holm([]), [])

    def test_equivalence_with_no_data_is_false(self):
        self.assertFalse(stats.equivalent([], 0.1))

    def test_aa_split_uses_seeds_1_to_4(self):
        self.assertEqual(stats.aa_split({"f": [0.2, 0.4, 0.1, 0.3, 0.9]}), {"f": 0.1})
        self.assertEqual(stats.aa_split({"f": [0.2, 0.4, 0.1, 0.3, 0.0]}), {"f": 0.1})
        self.assertEqual(stats.aa_split({"f": [0.2, 0.4, 0.1], "g": [0.2, None, 0.1, 0.3]}), {})

    def test_seed_mean_and_paired_diffs(self):
        self.assertEqual(stats.seed_mean([0.5, None, 1.0]), 0.75)
        self.assertIsNone(stats.seed_mean([None]))
        self.assertEqual(stats.paired_diffs({"b": 1.0, "a": 0.5, "c": None}, {"a": 0.25, "b": 0.5, "d": 1.0}), [0.25, 0.5])

    def test_within_fixture_sd_is_the_pooled_root_mean_square(self):
        self.assertEqual(stats.within_fixture_sd({"a": [1, 3], "b": [2, 2]}), 1.0)
        self.assertEqual(stats.within_fixture_sd({"a": [1, None], "b": [5]}), None)
        self.assertEqual(stats.stdev([1, 3]), round(math.sqrt(2), 10))


if __name__ == "__main__":
    unittest.main()
