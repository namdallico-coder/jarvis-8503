"""SQLite 저장소.

collector가 수집한 시세는 prices 테이블에, news가 수집한 DART 공시는
disclosures 테이블에 적재한다 (db/schema.sql).
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


def get_universe_stock_codes(conn: sqlite3.Connection) -> List[str]:
    """가장 최근 수집 사이클(prices.collected_at 최댓값) 기준 종목코드 목록을 반환한다.

    news/main.py가 collector와 별도 프로세스로 돌면서도 동일한 유니버스(universe.py가
    선정한 종목)를 따라가도록, collector.py를 다시 호출하지 않고 이미 적재된 prices에서 읽는다.
    """
    row = conn.execute("SELECT MAX(collected_at) FROM prices").fetchone()
    latest = row[0] if row else None
    if latest is None:
        return []
    cursor = conn.execute("SELECT DISTINCT stk_cd FROM prices WHERE collected_at = ?", (latest,))
    return [r[0] for r in cursor.fetchall()]


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
