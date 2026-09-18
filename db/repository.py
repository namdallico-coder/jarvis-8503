"""SQLite 저장소.

collector가 수집한 시세는 prices 테이블에, news가 수집한 DART 공시는
disclosures 테이블에, decision이 만든 AI 판단은 decisions 테이블에,
signals가 감지한 이동평균 크로스는 signals 테이블에 적재한다 (db/schema.sql).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

DB_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DB_DIR / "schema.sql"
DEFAULT_DB_PATH = DB_DIR / "collector.db"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def save_price(conn: sqlite3.Connection, quote: Dict[str, Any], collected_at: str) -> None:
    """fetch_quotes()가 반환한 quote 1건을 prices 테이블에 적재한다."""
    conn.execute(
        """
        INSERT INTO prices (stk_cd, stk_nm, cur_prc, trde_qty, collected_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            quote["stk_cd"],
            quote.get("stk_nm"),
            quote["cur_prc"],
            quote["trde_qty"],
            collected_at,
        ),
    )
    conn.commit()


def _latest_collected_at(conn: sqlite3.Connection) -> Any:
    row = conn.execute("SELECT MAX(collected_at) FROM prices").fetchone()
    return row[0] if row else None


def get_universe_stock_codes(conn: sqlite3.Connection) -> List[str]:
    """가장 최근 수집 사이클(prices.collected_at 최댓값) 기준 종목코드 목록을 반환한다.

    news/main.py, decision/main.py가 collector와 별도 프로세스로 돌면서도 동일한
    유니버스(universe.py가 선정한 종목)를 따라가도록, collector를 다시 호출하지 않고
    이미 적재된 prices에서 읽는다.
    """
    latest = _latest_collected_at(conn)
    if latest is None:
        return []
    cursor = conn.execute("SELECT DISTINCT stk_cd FROM prices WHERE collected_at = ?", (latest,))
    return [r[0] for r in cursor.fetchall()]


def get_latest_universe(conn: sqlite3.Connection) -> List[Dict[str, str]]:
    """get_universe_stock_codes()와 같은 기준으로, 종목명까지 같이 반환한다."""
    latest = _latest_collected_at(conn)
    if latest is None:
        return []
    cursor = conn.execute(
        "SELECT DISTINCT stk_cd, stk_nm FROM prices WHERE collected_at = ? ORDER BY stk_cd",
        (latest,),
    )
    return [{"stk_cd": r[0], "stk_nm": r[1]} for r in cursor.fetchall()]


def get_price_history(conn: sqlite3.Connection, stk_cd: str, since_iso: str) -> List[Dict[str, Any]]:
    """since_iso 이후 해당 종목의 시세 이력을 시간순으로 반환한다."""
    cursor = conn.execute(
        """
        SELECT collected_at, cur_prc, trde_qty FROM prices
        WHERE stk_cd = ? AND collected_at >= ?
        ORDER BY collected_at ASC
        """,
        (stk_cd, since_iso),
    )
    return [
        {"collected_at": r[0], "cur_prc": r[1], "trde_qty": r[2]}
        for r in cursor.fetchall()
    ]


def get_todays_disclosures(conn: sqlite3.Connection, stock_code: str, date_str: str) -> List[Dict[str, Any]]:
    """해당 종목의 date_str(YYYYMMDD) 접수 공시 목록을 반환한다."""
    cursor = conn.execute(
        """
        SELECT report_nm, rcept_dt, flr_nm, rm FROM disclosures
        WHERE stock_code = ? AND rcept_dt = ?
        ORDER BY collected_at ASC
        """,
        (stock_code, date_str),
    )
    return [
        {"report_nm": r[0], "rcept_dt": r[1], "flr_nm": r[2], "rm": r[3]}
        for r in cursor.fetchall()
    ]


def save_disclosure(conn: sqlite3.Connection, disclosure: Dict[str, Any], collected_at: str) -> bool:
    """DART list.json의 공시 1건을 저장한다. 이미 있는 rcept_no면 무시하고 False를 반환한다."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO disclosures
            (rcept_no, corp_code, stock_code, corp_name, report_nm, rcept_dt, flr_nm, rm, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            disclosure["rcept_no"],
            disclosure.get("corp_code"),
            disclosure.get("stock_code"),
            disclosure.get("corp_name"),
            disclosure.get("report_nm"),
            disclosure.get("rcept_dt"),
            disclosure.get("flr_nm"),
            disclosure.get("rm"),
            collected_at,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def save_decision(conn: sqlite3.Connection, decision: Dict[str, Any]) -> None:
    """decision/main.py의 판단 결과 1건을 근거 스냅샷과 함께 저장한다 (페이퍼 모드, 매매 없음)."""
    conn.execute(
        """
        INSERT INTO decisions
            (stk_cd, decided_at, action, confidence, reason, context_snapshot, model)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision["stk_cd"],
            decision["decided_at"],
            decision["action"],
            decision["confidence"],
            decision["reason"],
            decision["context_snapshot"],
            decision.get("model"),
        ),
    )
    conn.commit()


def save_signal(conn: sqlite3.Connection, signal: Dict[str, Any]) -> None:
    """signals/ma_signal.py가 감지한 크로스 1건을 저장한다."""
    conn.execute(
        """
        INSERT INTO signals
            (stk_cd, signal_type, short_window_min, long_window_min, short_ma, long_ma,
             price_at_signal, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal["stk_cd"],
            signal["signal_type"],
            signal["short_window_min"],
            signal["long_window_min"],
            signal["short_ma"],
            signal["long_ma"],
            signal["price_at_signal"],
            signal["detected_at"],
        ),
    )
    conn.commit()
