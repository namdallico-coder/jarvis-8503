"""8503 안전장치(risk_gate) 진입점.

decision이 follow로 판단해 buy/sell이 확정된 건을 가져와 하드룰로 최종
주문 가능 여부를 판정하고, 판정 근거를 risk_checks 테이블에 남긴다.
승인된 건만 paper_trades 원장에 반영된다(거부된 건 상태 변화 없음).

실제 주문 API 연동은 없다 — 여기까지는 판정 로직과 로그만 만든다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict

from config.settings import risk_gate_settings
from db.repository import (
    get_connection,
    init_db,
    get_ungated_decisions,
    get_latest_price,
    save_risk_check,
    record_paper_trade,
)
from risk_gate.rules import evaluate

logger = logging.getLogger("risk_gate")


def check_one(conn: sqlite3.Connection, decision: Dict[str, Any]) -> None:
    approved, checks = evaluate(conn, decision, risk_gate_settings)
    checked_at = datetime.now(timezone.utc).isoformat()

    save_risk_check(
        conn,
        {
            "decision_id": decision["id"],
            "stk_cd": decision["stk_cd"],
            "action": decision["action"],
            "approved": approved,
            "checks": json.dumps([c.to_dict() for c in checks], ensure_ascii=False),
            "checked_at": checked_at,
        },
    )

    status = "승인" if approved else "거부"
    reasons = "; ".join(f"{c.rule}={'OK' if c.passed else 'FAIL'}({c.reason})" for c in checks)
    logger.info("[%s] %s -> %s | %s", decision["stk_cd"], decision["action"], status, reasons)

    if not approved:
        return

    price = get_latest_price(conn, decision["stk_cd"])
    if price is None:
        logger.warning("승인됐지만 현재가를 못 찾아 paper_trades에 기록 못함: %s", decision["stk_cd"])
        return

    record_paper_trade(
        conn,
        {
            "stk_cd": decision["stk_cd"],
            "action": decision["action"],
            "price": price,
            "decision_id": decision["id"],
            "executed_at": checked_at,
        },
    )


def check_once(conn: sqlite3.Connection) -> int:
    decisions = get_ungated_decisions(conn)
    for decision in decisions:
        check_one(conn, decision)
    return len(decisions)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    init_db(conn)

    try:
        while True:
            count = check_once(conn)
            logger.info("risk_gate cycle done: %d decisions checked", count)
            await asyncio.sleep(risk_gate_settings.check_interval_sec)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
