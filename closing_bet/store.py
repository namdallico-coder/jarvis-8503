"""closing_bet.db (기존 collector.db와 분리된 별도 SQLite 파일) 스키마와 저장소 함수."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from closing_bet.config import KST, ClosingBetSettings
from closing_bet.rule import rank_universe

logger = logging.getLogger("closing_bet")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cb_ordinary (code TEXT PRIMARY KEY, name TEXT, market TEXT, updated_at TEXT);
-- exact=1: 그날 전 종목 거래대금을 알고 있음(시드). exact=0: 거래대금 상위 N위만 관측, cutoff_amount는 N번째 값(미관측 상한)
CREATE TABLE IF NOT EXISTS cb_day_meta (dt TEXT PRIMARY KEY, exact INTEGER, cutoff_amount INTEGER, n_rows INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS cb_amount_history (
    dt TEXT, code TEXT, amount INTEGER, close INTEGER, volume INTEGER, PRIMARY KEY (dt, code));
CREATE TABLE IF NOT EXISTS cb_universe (asof_dt TEXT, code TEXT, rank INTEGER, avg20 REAL, PRIMARY KEY (asof_dt, code));
CREATE TABLE IF NOT EXISTS cb_snapshots (dt TEXT, phase TEXT, taken_at TEXT, signature TEXT, n_rows INTEGER, PRIMARY KEY (dt, phase));
CREATE TABLE IF NOT EXISTS cb_job_runs (dt TEXT, job TEXT, status TEXT, note TEXT, ran_at TEXT, PRIMARY KEY (dt, job));
CREATE TABLE IF NOT EXISTS cb_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, dt TEXT, kind TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS cb_calibration (
    dt TEXT, code TEXT, amount_1520 INTEGER, amount_final INTEGER, rho REAL, PRIMARY KEY (dt, code));
CREATE TABLE IF NOT EXISTS cb_judgments (
    dt TEXT, code TEXT, name TEXT, universe_asof TEXT, universe_rank INTEGER, avg20 REAL,
    in_ranking INTEGER, amount_1520 INTEGER, est_amount REAL, est_ratio REAL, rho_used REAL,
    cur_prc INTEGER, flu_rt REAL, cond_ratio INTEGER, cond_up INTEGER, cond_not_limit INTEGER,
    decision TEXT, reject_reason TEXT, judged_at TEXT,
    amount_final INTEGER, final_ratio REAL, close_final INTEGER, flu_rt_final REAL,
    final_cond_ratio INTEGER, final_cond_up INTEGER, final_cond_not_limit INTEGER, final_decision TEXT,
    agree_ratio INTEGER, agree_up INTEGER, agree_decision INTEGER, finalized_at TEXT,
    PRIMARY KEY (dt, code));
CREATE TABLE IF NOT EXISTS cb_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, dt_entry TEXT, code TEXT, name TEXT,
    entry_price INTEGER, amount_1520 INTEGER, est_ratio REAL, flu_rt_1520 REAL, rho_used REAL, judged_at TEXT,
    close_price INTEGER, entry_vs_close_pct REAL, final_decision TEXT, agree_decision INTEGER,
    status TEXT, exit_dt TEXT, exit_price INTEGER, exited_at TEXT, exit_note TEXT,
    gross_pct REAL, net_cost_pct REAL, net_slip_each_pct REAL, net_slip_total_pct REAL,
    UNIQUE (dt_entry, code));
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def log_event(conn: sqlite3.Connection, dt: str, kind: str, message: str, level: int = logging.INFO) -> None:
    logger.log(level, "[%s] %s: %s", dt, kind, message)
    conn.execute("INSERT INTO cb_events (ts, dt, kind, message) VALUES (?,?,?,?)", (now_iso(), dt, kind, message))
    conn.commit()


def job_status(conn: sqlite3.Connection, dt: str, job: str) -> Optional[str]:
    r = conn.execute("SELECT status FROM cb_job_runs WHERE dt=? AND job=?", (dt, job)).fetchone()
    return r[0] if r else None


def set_job(conn: sqlite3.Connection, dt: str, job: str, status: str, note: str = "") -> None:
    conn.execute("INSERT OR REPLACE INTO cb_job_runs VALUES (?,?,?,?,?)", (dt, job, status, note, now_iso()))
    conn.commit()


def history_dates(conn: sqlite3.Connection, upto: Optional[str] = None) -> List[str]:
    if upto:
        rows = conn.execute("SELECT dt FROM cb_day_meta WHERE dt<=? ORDER BY dt", (upto,))
    else:
        rows = conn.execute("SELECT dt FROM cb_day_meta ORDER BY dt")
    return [r[0] for r in rows]


def save_snapshot(conn: sqlite3.Connection, dt: str, phase: str, signature: str, n_rows: int) -> None:
    conn.execute("INSERT OR REPLACE INTO cb_snapshots VALUES (?,?,?,?,?)", (dt, phase, now_iso(), signature, n_rows))
    conn.commit()


def previous_snapshots(conn: sqlite3.Connection, dt: str, limit: int = 6) -> List[sqlite3.Row]:
    """오늘(dt) 이전 일자의 스냅샷 (최신순). 휴장일 감지에 쓴다."""
    return conn.execute(
        "SELECT dt, phase, signature FROM cb_snapshots WHERE dt<? ORDER BY dt DESC, phase DESC LIMIT ?", (dt, limit)
    ).fetchall()


def compute_universe(conn: sqlite3.Connection, cfg: ClosingBetSettings, asof: str) -> List[Dict[str, Any]]:
    """asof(포함)까지 직전 lookback 거래일의 평균 거래대금 상위 N. asof 다음 거래일(D)의 유니버스가 된다 —
    D 당일 정보는 쓰지 않는다. 상위 N위 밖이라 관측되지 않은 날은(approx 일자) 그날 cutoff 값으로 대체한다."""
    dates = history_dates(conn, asof)[-cfg.lookback_days:]
    if len(dates) < cfg.lookback_days:
        raise ValueError(f"거래대금 이력 부족: {len(dates)}일 < {cfg.lookback_days}일 (asof={asof})")
    meta = {r["dt"]: (r["exact"], r["cutoff_amount"] or 0) for r in conn.execute(
        f"SELECT dt, exact, cutoff_amount FROM cb_day_meta WHERE dt IN ({','.join('?' * len(dates))})", dates)}
    obs: Dict[str, Dict[str, int]] = {}
    for r in conn.execute(
        f"SELECT dt, code, amount FROM cb_amount_history WHERE dt IN ({','.join('?' * len(dates))})", dates
    ):
        obs.setdefault(r["code"], {})[r["dt"]] = r["amount"]
    series: Dict[str, List[Optional[float]]] = {}
    for code, m in obs.items():
        observed = [m.get(d) for d in dates]
        if sum(1 for v in observed if v and v > 0) < cfg.min_valid_days:
            continue
        vals: List[Optional[float]] = []
        for d, v in zip(dates, observed):
            if v is not None and v > 0:
                vals.append(float(v))
            else:
                exact, cutoff = meta[d]
                vals.append(None if exact or cutoff <= 0 else float(cutoff))
        series[code] = vals
    ranked = rank_universe(series, cfg.universe_size, cfg.min_valid_days)
    conn.execute("DELETE FROM cb_universe WHERE asof_dt=?", (asof,))
    conn.executemany(
        "INSERT INTO cb_universe VALUES (?,?,?,?)",
        [(asof, code, i + 1, avg) for i, (code, avg) in enumerate(ranked)],
    )
    conn.commit()
    return [{"code": c, "rank": i + 1, "avg20": a} for i, (c, a) in enumerate(ranked)]


def latest_universe_before(conn: sqlite3.Connection, dt: str) -> Tuple[Optional[str], List[sqlite3.Row]]:
    r = conn.execute("SELECT MAX(asof_dt) FROM cb_universe WHERE asof_dt<?", (dt,)).fetchone()
    if not r or not r[0]:
        return None, []
    rows = conn.execute("SELECT code, rank, avg20 FROM cb_universe WHERE asof_dt=? ORDER BY rank", (r[0],)).fetchall()
    return r[0], rows


def rho_summary(conn: sqlite3.Connection) -> Dict[str, Any]:
    """실측 rho(15:20 누적 / 최종) 요약. 교체 판단은 측정값만 근거로 한다."""
    rows = conn.execute(
        "SELECT dt, rho FROM cb_calibration WHERE rho IS NOT NULL AND amount_final > 0 ORDER BY dt").fetchall()
    days = sorted({r[0] for r in rows})
    vals = sorted(r[1] for r in rows)
    if not vals:
        return {"days": 0, "n": 0}
    q = lambda p: vals[min(len(vals) - 1, int(len(vals) * p))]
    return {"days": len(days), "n": len(vals), "median": q(0.5), "p25": q(0.25), "p75": q(0.75), "min": vals[0], "max": vals[-1]}
