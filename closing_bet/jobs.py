"""15:20 판정 / 15:45 마감 재판정·유니버스 갱신 / 다음 거래일 시초가 청산. 페이퍼 전용 — 어떤 주문도 내지 않는다.

api 객체는 ranking(pages)/quote(code)/ordinary() 비동기 메서드만 있으면 된다(실제: kiwoom_api.KiwoomAPI, 테스트: 가짜).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from closing_bet import store
from closing_bet.calendar_check import detect_closed, log_post_holiday, signature, static_closed
from closing_bet.config import ClosingBetSettings
from closing_bet.rule import judge_1520, judge_final, returns_pct

logger = logging.getLogger("closing_bet")
DONE = ("ok", "closed", "no_positions", "failed", "missed")


def _dt(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


async def refresh_ordinary(conn, api, force: bool = False, max_age_days: int = 7) -> None:
    r = conn.execute("SELECT MAX(updated_at) FROM cb_ordinary").fetchone()[0]
    if r and not force:
        ts = datetime.fromisoformat(r)
        if ts.tzinfo is None:  # 예전 형식(naive)이면 현지 시각으로 본다
            ts = ts.astimezone()
        age = (datetime.now().astimezone() - ts).days
        if age < max_age_days:
            return
    rows = await api.ordinary()
    ts = store.now_iso()
    conn.execute("DELETE FROM cb_ordinary")
    conn.executemany("INSERT INTO cb_ordinary VALUES (?,?,?,?)", [(x["code"], x["name"], x["market"], ts) for x in rows])
    conn.commit()
    logger.info("개별종목 화이트리스트 갱신: %d개", len(rows))


async def run_judge(conn, cfg: ClosingBetSettings, api, now: datetime) -> str:
    dt = _dt(now)
    st = store.job_status(conn, dt, "judge")
    if st in DONE:
        return st
    closed, why = static_closed(cfg, now.date())
    if closed:
        store.set_job(conn, dt, "judge", "closed", why)
        store.log_event(conn, dt, "holiday_detected", f"15:20 판정 스킵: {why}")
        return "closed"

    rows = await api.ranking(cfg.rank_pages_judge)
    is_closed, evidence = detect_closed(conn, dt, rows, "1520")
    store.log_event(conn, dt, "trading_day_check", f"15:20 거래일 확인: {evidence}")
    if is_closed:
        store.set_job(conn, dt, "judge", "closed", evidence)
        store.log_event(conn, dt, "holiday_detected", f"휴장 감지(15:20): {evidence}", logging.WARNING)
        return "closed"
    store.save_snapshot(conn, dt, "1520", signature(rows), len(rows))
    log_post_holiday(conn, dt)

    asof, uni = store.latest_universe_before(conn, dt)
    if not uni:
        hist = [d for d in store.history_dates(conn) if d < dt]
        if not hist:
            store.set_job(conn, dt, "judge", "failed", "거래대금 이력 없음 (seed 필요)")
            return "failed"
        asof = hist[-1]
        uni = [{"code": u["code"], "rank": u["rank"], "avg20": u["avg20"]} for u in store.compute_universe(conn, cfg, asof)]
        store.log_event(conn, dt, "universe_computed", f"유니버스 즉석 계산 asof={asof}")

    by_code = {r["code"]: r for r in rows}
    names = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM cb_ordinary")}
    ts = store.now_iso()
    n_buy = 0
    for u in uni:
        code = u["code"]
        row = by_code.get(code)
        j = judge_1520(cfg, u["avg20"], row["amount"] if row else None, row["flu_rt"] if row else None, row is not None)
        name = (row or {}).get("name") or names.get(code) or code
        conn.execute(
            "INSERT OR REPLACE INTO cb_judgments (dt, code, name, universe_asof, universe_rank, avg20, in_ranking, amount_1520,"
            " est_amount, est_ratio, rho_used, cur_prc, flu_rt, cond_ratio, cond_up, cond_not_limit, decision, reject_reason, judged_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (dt, code, name, asof, u["rank"], u["avg20"], int(row is not None), row["amount"] if row else None,
             j["est_amount"], j["est_ratio"], cfg.rho, row["cur_prc"] if row else None, row["flu_rt"] if row else None,
             int(j["cond_ratio"]), int(j["cond_up"]), int(j["cond_not_limit"]), j["decision"], j["reject_reason"], ts))
        if j["decision"] == "buy":
            n_buy += 1
            conn.execute(
                "INSERT OR IGNORE INTO cb_trades (dt_entry, code, name, entry_price, amount_1520, est_ratio, flu_rt_1520, rho_used,"
                " judged_at, status) VALUES (?,?,?,?,?,?,?,?,?,'open')",
                (dt, code, name, row["cur_prc"], row["amount"], j["est_ratio"], row["flu_rt"], cfg.rho, ts))
    conn.executemany(
        "INSERT OR REPLACE INTO cb_calibration (dt, code, amount_1520) VALUES (?,?,?)",
        [(dt, r["code"], r["amount"]) for r in rows])
    conn.commit()
    store.set_job(conn, dt, "judge", "ok", f"유니버스 {len(uni)}종목(asof {asof}), 순위권 {sum(1 for u in uni if u['code'] in by_code)}, 매수 {n_buy}")
    store.log_event(conn, dt, "judge_done", f"15:20 판정 완료: 유니버스 {len(uni)}, 매수(가상) {n_buy}")
    return "ok"


async def run_finalize(conn, cfg: ClosingBetSettings, api, now: datetime) -> str:
    dt = _dt(now)
    if store.job_status(conn, dt, "finalize") in DONE:
        return store.job_status(conn, dt, "finalize")
    closed, why = static_closed(cfg, now.date())
    if closed or store.job_status(conn, dt, "judge") == "closed":
        store.set_job(conn, dt, "finalize", "closed", why or "15:20 단계에서 휴장 감지")
        return "closed"

    rows = await api.ranking(cfg.rank_pages_final)
    is_closed, evidence = detect_closed(conn, dt, rows, "1545")
    store.log_event(conn, dt, "trading_day_check", f"15:45 거래일 확인: {evidence}")
    if is_closed:
        store.set_job(conn, dt, "finalize", "closed", evidence)
        store.log_event(conn, dt, "holiday_detected", f"휴장 감지(15:45): {evidence}", logging.WARNING)
        return "closed"
    store.save_snapshot(conn, dt, "1545", signature(rows), len(rows))

    if not conn.execute("SELECT 1 FROM cb_ordinary LIMIT 1").fetchone():
        await refresh_ordinary(conn, api, force=True)
    ordinary = {r[0] for r in conn.execute("SELECT code FROM cb_ordinary")}
    by_code = {r["code"]: r for r in rows}
    cutoff = min(r["amount"] for r in rows)
    conn.execute("INSERT OR REPLACE INTO cb_day_meta VALUES (?,?,?,?,?)", (dt, 0, cutoff, len(rows), "ranking"))
    conn.executemany(
        "INSERT OR REPLACE INTO cb_amount_history VALUES (?,?,?,?,?)",
        [(dt, r["code"], r["amount"], r["cur_prc"], r["qty"]) for r in rows if r["code"] in ordinary])

    for r in rows:  # rho 실측
        conn.execute(
            "UPDATE cb_calibration SET amount_final=?, rho=CASE WHEN ?>0 THEN CAST(amount_1520 AS REAL)/? END"
            " WHERE dt=? AND code=? AND amount_1520 IS NOT NULL", (r["amount"], r["amount"], r["amount"], dt, r["code"]))

    ts = store.now_iso()
    agree = total = 0
    for j in conn.execute("SELECT * FROM cb_judgments WHERE dt=?", (dt,)).fetchall():
        row = by_code.get(j["code"])
        f = judge_final(cfg, j["avg20"], row["amount"] if row else None, row["flu_rt"] if row else None, row is not None)
        a_dec = int(f["decision"] == j["decision"])
        agree += a_dec
        total += 1
        conn.execute(
            "UPDATE cb_judgments SET amount_final=?, final_ratio=?, close_final=?, flu_rt_final=?, final_cond_ratio=?,"
            " final_cond_up=?, final_cond_not_limit=?, final_decision=?, agree_ratio=?, agree_up=?, agree_decision=?, finalized_at=?"
            " WHERE dt=? AND code=?",
            (row["amount"] if row else None, f["ratio"], row["cur_prc"] if row else None, row["flu_rt"] if row else None,
             int(f["cond_ratio"]), int(f["cond_up"]), int(f["cond_not_limit"]), f["decision"],
             int(bool(f["cond_ratio"]) == bool(j["cond_ratio"])), int(bool(f["cond_up"]) == bool(j["cond_up"])), a_dec, ts, dt, j["code"]))
        t = conn.execute("SELECT id, entry_price FROM cb_trades WHERE dt_entry=? AND code=?", (dt, j["code"])).fetchone()
        if t:
            close = row["cur_prc"] if row else None
            conn.execute(
                "UPDATE cb_trades SET close_price=?, entry_vs_close_pct=?, final_decision=?, agree_decision=? WHERE id=?",
                (close, ((close / t["entry_price"] - 1) * 100) if close else None, f["decision"], a_dec, t["id"]))
    conn.commit()

    universe = store.compute_universe(conn, cfg, dt)
    await refresh_ordinary(conn, api, max_age_days=cfg.ordinary_refresh_days)
    rs = store.rho_summary(conn)
    msg = f"15:45 마감 처리: 판정 {total}건 중 15:20-종가 판정 일치 {agree}건, 다음 유니버스 {len(universe)}종목(asof {dt})"
    if rs.get("n"):
        msg += f" | 실측 ρ: {rs['days']}일 {rs['n']}건 중앙값 {rs['median']:.3f}(p25 {rs['p25']:.3f}, p75 {rs['p75']:.3f}), 사용 중 {cfg.rho}"
        if rs["days"] >= cfg.rho_review_after_days:
            msg += f" ★ {cfg.rho_review_after_days}거래일 이상 측정됨 — ρ 교체 검토 시점(측정값 기준, 1회)"
    store.log_event(conn, dt, "finalize_done", msg)
    store.set_job(conn, dt, "finalize", "ok", msg)
    return "ok"


async def run_exit(conn, cfg: ClosingBetSettings, api, now: datetime) -> str:
    """진입일 다음 거래일 시초가로 청산. 반환: ok/closed/no_positions/wait(개장 대기 또는 일부 미체결)."""
    dt = _dt(now)
    st = store.job_status(conn, dt, "exit")
    if st in DONE:
        return st
    opens = conn.execute("SELECT * FROM cb_trades WHERE status='open' AND dt_entry<?", (dt,)).fetchall()
    if not opens:
        store.set_job(conn, dt, "exit", "no_positions", "")
        return "no_positions"
    closed, why = static_closed(cfg, now.date())
    if closed:
        store.set_job(conn, dt, "exit", "closed", why)
        store.log_event(conn, dt, "holiday_detected", f"시초가 청산 스킵: {why}")
        return "closed"

    # 개장 지표: 삼성전자 누적 거래량. 휴장일엔 직전 마감 거래량 그대로 돌아온다.
    ind = await api.quote("005930")
    prev = conn.execute(
        "SELECT volume FROM cb_amount_history WHERE code='005930' AND dt<? ORDER BY dt DESC LIMIT 1", (dt,)).fetchone()
    if prev and ind["qty"] and ind["qty"] == prev[0]:
        evidence = f"삼성전자 누적거래량 {ind['qty']:,}이 직전 마감값과 동일 → 장이 열리지 않음"
        store.set_job(conn, dt, "exit", "closed", evidence)
        store.log_event(conn, dt, "holiday_detected", f"휴장 감지(시초가 단계): {evidence}", logging.WARNING)
        return "closed"
    if not ind["qty"]:
        return "wait"
    store.log_event(conn, dt, "trading_day_check",
                    f"시초가 단계 거래일 확인: 삼성전자 누적거래량 {ind['qty']:,} (직전 마감 {prev[0] if prev else '없음'}) → 개장")

    late = bool(conn.execute(
        "SELECT 1 FROM cb_job_runs WHERE job='exit' AND status IN ('failed','missed') AND dt>?",
        (min(t["dt_entry"] for t in opens),)).fetchone())
    pending = 0
    ts = store.now_iso()
    for t in opens:
        q = await api.quote(t["code"])
        if q["open_pric"] <= 0 or q["qty"] <= 0:
            pending += 1
            continue
        ret = returns_pct(cfg, t["entry_price"], q["open_pric"])
        conn.execute(
            "UPDATE cb_trades SET status='closed', exit_dt=?, exit_price=?, exited_at=?, exit_note=?, gross_pct=?,"
            " net_cost_pct=?, net_slip_each_pct=?, net_slip_total_pct=? WHERE id=?",
            (dt, q["open_pric"], ts, "late_exit" if late else None, ret["gross_pct"], ret["net_cost_pct"],
             ret["net_slip_each_pct"], ret["net_slip_total_pct"], t["id"]))
        store.log_event(conn, dt, "exit", f"{t['code']} {t['name']} 청산 시가 {q['open_pric']:,} (매수 {t['entry_price']:,}) "
                        f"총 {ret['gross_pct']:+.2f}% / 비용후 {ret['net_cost_pct']:+.2f}%")
    conn.commit()
    if pending:
        return "wait"
    store.set_job(conn, dt, "exit", "ok", f"{len(opens)}건 청산")
    return "ok"
