from fractions import Fraction
import random
import unittest

from test_output_contract import VALIDATOR, cost_result


class CostMatrixTests(unittest.TestCase):
    def test_seeded_actual_estimated_unknown_accounting(self):
        randomizer = random.Random(20260906)
        for case in range(200):
            with self.subTest(case=case):
                routes = []
                for index in range(randomizer.randrange(7)):
                    basis = randomizer.choice(["actual", "estimated", "unknown"])
                    amount = randomizer.choice([0, .01, .06, .28, 1.1, 5.01])
                    routes.append({
                        "route_id": f"route-{index}", "provider": randomizer.choice(["deepline", "scrapingdog"]),
                        "paid_calls": 1, "cost_basis": basis,
                        "cost_credits": amount if basis == "actual" else None,
                        "cost_upper_bound_credits": amount if basis != "unknown" else None,
                    })
                document = cost_result(routes)
                summary = VALIDATOR.calculate_cost_summary(document)
                for provider in ("deepline", "scrapingdog"):
                    selected = [r for r in routes if r["provider"] == provider]
                    confirmed = sum((Fraction(str(r["cost_credits"])) for r in selected if r["cost_basis"] == "actual"), Fraction())
                    unknown = any(r["cost_basis"] == "unknown" for r in selected)
                    maximum = None if unknown else sum((Fraction(str(r["cost_upper_bound_credits"])) for r in selected), Fraction())
                    self.assertEqual(summary[provider]["confirmed_credits"], float(confirmed))
                    self.assertEqual(summary[provider]["maximum_credits"], None if maximum is None else float(maximum))
                    document["budget"]["spent"][provider + "_credits"] = (
                        None if any(r["cost_basis"] != "actual" for r in selected) else float(confirmed))
                expected_status = "unknown" if any(r["cost_basis"] == "unknown" for r in routes) else (
                    "estimated_range" if any(r["cost_basis"] == "estimated" for r in routes) else "exact")
                self.assertEqual(summary["status"], expected_status)
                self.assertFalse(VALIDATOR.validate_run(document))

    def test_estimate_over_cap_is_not_hidden_by_unknown_actual(self):
        document = cost_result([{"route_id": "paid", "provider": "deepline", "paid_calls": 1,
                                 "cost_basis": "estimated", "cost_credits": None, "cost_upper_bound_credits": 5.01}])
        document["budget"]["limits"]["deepline_credits"] = 5
        self.assertTrue(any("exceeds" in error for error in VALIDATOR.validate_run(document)))
