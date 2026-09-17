"""SQLite 저장소: collector가 수집한 시세를 prices 테이블에 적재한다.

최소 스키마(db/schema.sql): 종목코드 / 종목명 / 현재가 / 거래량 / 수집시각.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

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
