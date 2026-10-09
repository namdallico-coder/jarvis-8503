import unittest

from closing_bet.config import settings as cfg
from closing_bet.rule import judge_1520, judge_final, rank_universe, returns_pct


class RuleTests(unittest.TestCase):
    AVG = 1000.0

    def test_buy_when_all_conditions_pass(self):
        j = judge_1520(cfg, self.AVG, int(3.2 * self.AVG * cfg.rho), 2.0, True)
        self.assertEqual(j["decision"], "buy")
        self.assertAlmostEqual(j["est_ratio"], 3.2, places=3)

    def test_threshold_boundary_is_inclusive(self):
        j = judge_1520(cfg, self.AVG, 3.0 * self.AVG * cfg.rho, 1.0, True)
        self.assertEqual(j["decision"], "buy")

    def test_reject_reasons(self):
        j = judge_1520(cfg, self.AVG, int(2.0 * self.AVG * cfg.rho), 1.0, True)
        self.assertTrue(j["reject_reason"].startswith("ratio_below"))
        j = judge_1520(cfg, self.AVG, int(4 * self.AVG * cfg.rho), -0.5, True)
        self.assertIn("not_up", j["reject_reason"])
        j = judge_1520(cfg, self.AVG, int(4 * self.AVG * cfg.rho), 29.5, True)
        self.assertIn("limit_up", j["reject_reason"])
        j = judge_1520(cfg, self.AVG, int(4 * self.AVG * cfg.rho), 0.0, True)  # 보합은 상승 아님
        self.assertIn("not_up", j["reject_reason"])
        j = judge_1520(cfg, self.AVG, None, None, False)
        self.assertIn("not_in_ranking_page", j["reject_reason"])
        j = judge_1520(cfg, None, 100, 1.0, True)
        self.assertEqual(j["reject_reason"], "no_avg20")

    def test_limit_up_29_is_excluded(self):
        self.assertEqual(judge_1520(cfg, self.AVG, int(5 * self.AVG), 29.0, True)["decision"], "reject")
        self.assertEqual(judge_1520(cfg, self.AVG, int(5 * self.AVG), 28.9, True)["decision"], "buy")

    def test_final_judge_has_no_rho(self):
        f = judge_final(cfg, self.AVG, 3000, 1.0, True)
        self.assertEqual(f["decision"], "buy")
        self.assertAlmostEqual(f["ratio"], 3.0)

    def test_returns(self):
        r = returns_pct(cfg, 100, 101)
        self.assertAlmostEqual(r["gross_pct"], 1.0)
        self.assertAlmostEqual(r["net_cost_pct"], 0.67)
        self.assertAlmostEqual(r["net_slip_each_pct"], 0.47)   # 0.33 + 0.2
        self.assertAlmostEqual(r["net_slip_total_pct"], 0.57)  # 0.33 + 0.1

    def test_rank_universe(self):
        s = {"A": [10.0] * 20, "B": [20.0] * 20, "C": [30.0] * 10 + [None] * 10, "D": [20.0] * 20}
        self.assertEqual([c for c, _ in rank_universe(s, 3, 15)], ["B", "D", "A"])  # C는 유효일 부족, 동률은 코드순


if __name__ == "__main__":
    unittest.main()
