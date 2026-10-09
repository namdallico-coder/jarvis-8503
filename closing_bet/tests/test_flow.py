"""가짜 키움 응답으로 며칠치 전체 흐름을 재현한다: 15:20 판정 → 15:45 마감 재판정 → 다음날 시초가 청산,
그리고 평일 휴장(응답 정체) 감지와 휴장 직후 첫 장 로그, 주말 스킵."""

import asyncio
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from closing_bet import store
from closing_bet.config import KST, settings as cfg
from closing_bet.jobs import run_exit, run_finalize, run_judge
from closing_bet.seed import seed


def at(day: str, hms: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hms}").replace(tzinfo=KST)


class FakeAPI:
    def __init__(self):
        self.rows = []
        self.quotes = {}

    async def ranking(self, pages):
        return self.rows[: pages * 100]

    async def quote(self, code):
        return self.quotes[code]

    async def ordinary(self):
        return [{"code": "000000", "name": "X", "market": "KOSPI"}]


def mk_rows(spec, filler_base=10_000):
    """spec: code -> (amount, cur_prc, flu_rt, qty). 거래대금 내림차순 순위 부여 + ETF 필러 행."""
    rows = [{"code": c, "name": f"N{c}", "cur_prc": p, "flu_rt": f, "qty": q, "amount": a} for c, (a, p, f, q) in spec.items()]
    rows += [{"code": f"E{i:05d}", "name": "ETF", "cur_prc": 1000, "flu_rt": 0.1, "qty": 1000 + i, "amount": filler_base - i} for i in range(5)]
    rows.sort(key=lambda r: -r["amount"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self.tmp.name) / "t.db")
        seed(self.conn, cfg, n_days=30, universes=1)
        self.asof, uni = store.latest_universe_before(self.conn, "2026-10-12")
        self.uni = [dict(u) for u in uni]
        self.api = FakeAPI()

    async def asyncTearDown(self):
        self.conn.close(); self.tmp.cleanup()

    def spec_day(self, mult_final):
        """유니버스 앞 5종목: X(매수), Y(배수 미달), Z(하락), W(상한가), V(순위권 밖)."""
        u = self.uni
        X, Y, Z, W, V = u[0], u[1], u[2], u[3], u[4]
        a = lambda r, m: int(m * r["avg20"])
        return X, {
            X["code"]: (a(X, mult_final["X"]), 50000, 2.0, 1_000_000),
            Y["code"]: (a(Y, mult_final["Y"]), 20000, 1.0, 500_000),
            Z["code"]: (a(Z, mult_final["Z"]), 30000, -1.0, 400_000),
            W["code"]: (a(W, mult_final["W"]), 40000, 29.8, 300_000),
        }

    async def test_full_cycle_and_exit(self):
        X, base = self.spec_day({"X": 3.2, "Y": 2.0, "Z": 4.0, "W": 4.0})
        rho_true = 0.97
        # --- 15:20 판정 ---
        self.api.rows = mk_rows({c: (int(v[0] * rho_true), v[1], v[2], v[3]) for c, v in base.items()})  # 15:20 누적 = 최종 x rho_true
        self.assertEqual(await run_judge(self.conn, cfg, self.api, at("2026-10-12", "15:20:05")), "ok")
        j = {r["code"]: r for r in self.conn.execute("SELECT * FROM cb_judgments WHERE dt='2026-10-12'")}
        self.assertEqual(len(j), 30)
        self.assertEqual(j[X["code"]]["decision"], "buy")
        reasons = {c: r["reject_reason"] for c, r in j.items() if r["decision"] == "reject"}
        self.assertTrue(any(v.startswith("ratio_below") for v in reasons.values()))
        self.assertTrue(any("not_up" in v for v in reasons.values()))
        self.assertTrue(any("limit_up" in v for v in reasons.values()))
        self.assertTrue(any("not_in_ranking_page" in v for v in reasons.values()))
        trades = self.conn.execute("SELECT * FROM cb_trades").fetchall()
        self.assertEqual([t["code"] for t in trades], [X["code"]])
        self.assertEqual(trades[0]["entry_price"], 50000)
        # --- 15:45 마감 ---
        final_rows = {c: (v[0], v[1], v[2], v[3] * 2) for c, v in base.items()}  # 최종 거래대금 = spec 값
        self.api.rows = mk_rows(final_rows)
        self.assertEqual(await run_finalize(self.conn, cfg, self.api, at("2026-10-12", "15:45:00")), "ok")
        x = self.conn.execute("SELECT * FROM cb_judgments WHERE dt='2026-10-12' AND code=?", (X["code"],)).fetchone()
        self.assertEqual(x["agree_decision"], 1)
        self.assertIsNotNone(x["final_ratio"])
        t = self.conn.execute("SELECT * FROM cb_trades").fetchone()
        self.assertEqual(t["final_decision"], "buy")
        self.assertEqual(t["close_price"], 50000)
        rs = store.rho_summary(self.conn)
        self.assertIsNotNone(self.conn.execute("SELECT rho FROM cb_calibration WHERE dt='2026-10-12' AND rho IS NOT NULL").fetchone())
        self.assertEqual(store.latest_universe_before(self.conn, "2026-10-13")[0], "2026-10-12")
        rho_x = self.conn.execute("SELECT rho FROM cb_calibration WHERE dt='2026-10-12' AND code=?", (X["code"],)).fetchone()[0]
        self.assertAlmostEqual(rho_x, rho_true, delta=0.001)  # 실측 rho가 입력한 0.97로 측정되는지 (필러 ETF 행은 1.0)
        # --- 다음날 시초가 청산 ---
        prev_vol = self.conn.execute("SELECT volume FROM cb_amount_history WHERE code='005930' AND dt='2026-10-08'").fetchone()[0]
        self.api.quotes = {"005930": {"qty": 1234, "open_pric": 1, "cur_prc": 1},
                           X["code"]: {"qty": 500, "open_pric": 50500, "cur_prc": 50600}}
        self.assertNotEqual(prev_vol, 1234)
        self.assertEqual(await run_exit(self.conn, cfg, self.api, at("2026-10-13", "09:00:20")), "ok")
        t = self.conn.execute("SELECT * FROM cb_trades").fetchone()
        self.assertEqual(t["status"], "closed")
        self.assertEqual(t["exit_price"], 50500)
        self.assertAlmostEqual(t["gross_pct"], 1.0)
        self.assertAlmostEqual(t["net_cost_pct"], 0.67)
        self.assertAlmostEqual(t["net_slip_each_pct"], 0.47)
        self.assertAlmostEqual(t["net_slip_total_pct"], 0.57)

    async def test_holiday_detection_and_post_holiday_log(self):
        X, base = self.spec_day({"X": 3.2, "Y": 2.0, "Z": 4.0, "W": 4.0})
        # 10/12 정상 거래일 (판정+마감) -> 포지션 1건
        self.api.rows = mk_rows(base)
        await run_judge(self.conn, cfg, self.api, at("2026-10-12", "15:20:05"))
        close_rows = mk_rows({c: (int(v[0] * 1.02), v[1], v[2], v[3] * 2) for c, v in base.items()})
        self.api.rows = close_rows
        await run_finalize(self.conn, cfg, self.api, at("2026-10-12", "15:45:00"))
        # 10/13 (평일) 휴장 가정: 키움이 10/12 마감 응답을 그대로 돌려줌
        self.api.rows = close_rows
        self.assertEqual(await run_judge(self.conn, cfg, self.api, at("2026-10-13", "15:20:05")), "closed")
        self.assertEqual(await run_finalize(self.conn, cfg, self.api, at("2026-10-13", "15:45:00")), "closed")
        vol_prev = self.conn.execute("SELECT volume FROM cb_amount_history WHERE code='005930' AND dt<'2026-10-13' ORDER BY dt DESC LIMIT 1").fetchone()[0]
        self.api.quotes = {"005930": {"qty": vol_prev, "open_pric": 1, "cur_prc": 1}}
        self.assertEqual(await run_exit(self.conn, cfg, self.api, at("2026-10-13", "09:00:20")), "closed")
        ev = [r["kind"] for r in self.conn.execute("SELECT kind FROM cb_events WHERE dt='2026-10-13'")]
        self.assertIn("holiday_detected", ev)
        # 10/14: 휴장 다음 첫 장 -> 스냅샷이 달라야 하고, '휴장 직후 첫 거래일' 로그가 남아야 한다
        new_rows = mk_rows({c: (int(v[0] * 1.1), v[1] + 100, v[2], v[3]) for c, v in base.items()})
        self.api.rows = new_rows
        with self.assertLogs("closing_bet", level="WARNING") as cm:
            self.assertEqual(await run_judge(self.conn, cfg, self.api, at("2026-10-14", "15:20:05")), "ok")
        self.assertTrue(any("휴장일 이후 첫 거래일" in m for m in cm.output), cm.output)
        kinds = [r["kind"] for r in self.conn.execute("SELECT kind FROM cb_events WHERE dt='2026-10-14'")]
        self.assertIn("post_holiday_first_session", kinds)

    async def test_weekend_and_holidays_file_skip(self):
        self.assertEqual(await run_judge(self.conn, cfg, self.api, at("2026-10-10", "15:20:05")), "closed")  # 토요일
        ev = self.conn.execute("SELECT message FROM cb_events WHERE dt='2026-10-10'").fetchone()[0]
        self.assertIn("주말", ev)

    async def test_judge_idempotent(self):
        X, base = self.spec_day({"X": 3.2, "Y": 2.0, "Z": 4.0, "W": 4.0})
        self.api.rows = mk_rows(base)
        await run_judge(self.conn, cfg, self.api, at("2026-10-12", "15:20:05"))
        await run_judge(self.conn, cfg, self.api, at("2026-10-12", "15:20:30"))  # 재실행해도 중복 기록 없음
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cb_trades").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cb_judgments").fetchone()[0], 30)


if __name__ == "__main__":
    unittest.main()
