"""8503 이동평균 크로스 신호 감지 진입점.

collector가 채운 prices 테이블에서 유니버스 종목별 단기/장기 이동평균을 계산해
크로스(골든/데드)가 발생하면 signals 테이블에 기록한다.

decision/의 AI 호출부는 아직 이 신호를 읽어가지 않는다 — 이 컴포넌트는 신호
계산/저장만 담당하고, "AI가 신호를 검토하는" 다음 단계는 별도로 진행한다.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict

from config.settings import signal_settings
from db.repository import get_connection, init_db, get_latest_universe, save_signal
from signals.ma_signal import Relation, compute_ma_snapshot, detect_cross

logger = logging.getLogger("signals")


def check_once(conn: sqlite3.Connection, last_relation: Dict[str, Relation]) -> int:
    universe = get_latest_universe(conn)
    if not universe:
        logger.warning("universe가 비어있습니다 (collector를 먼저 실행해야 합니다)")
        return 0

    new_signals = 0
    for stock in universe:
        stk_cd = stock["stk_cd"]
        snapshot = compute_ma_snapshot(conn, stk_cd)
        if snapshot is None:
            continue

        signal_type = detect_cross(last_relation.get(stk_cd), snapshot)
        if signal_type:
            detected_at = datetime.now(timezone.utc).isoformat()
            save_signal(
                conn,
                {
                    "stk_cd": stk_cd,
                    "signal_type": signal_type,
                    "short_window_min": signal_settings.short_window_min,
                    "long_window_min": signal_settings.long_window_min,
                    "short_ma": snapshot.short_ma,
                    "long_ma": snapshot.long_ma,
                    "price_at_signal": snapshot.price_at_signal,
                    "detected_at": detected_at,
                },
            )
            new_signals += 1
            logger.info(
                "%s: %s (단기 %.0f / 장기 %.0f, 현재가 %d)",
                stk_cd,
                signal_type,
                snapshot.short_ma,
                snapshot.long_ma,
                snapshot.price_at_signal,
            )

        last_relation[stk_cd] = snapshot.relation

    return new_signals


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    init_db(conn)

    last_relation: Dict[str, Relation] = {}
    try:
        while True:
            new_signals = check_once(conn, last_relation)
            logger.info("signal check done: %d new", new_signals)
            await asyncio.sleep(signal_settings.check_interval_sec)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
