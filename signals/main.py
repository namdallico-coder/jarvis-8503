"""8503 이동평균 크로스 신호 감지 진입점.

collector가 채운 prices 테이블에서 유니버스 종목별 단기/장기 이동평균을 계산해
크로스(골든/데드)가 발생하면 signals 테이블에 기록한다.

쿨다운(config.signal_settings.cooldown_min, 기본 15분) + 재동기화 정책을 쓴다:
같은 종목이 쿨다운 안에서 여러 번 뒤집혀도 마지막 "기록" 후 쿨다운이 지나야
다음 신호를 기록하고(origin=immediate), 쿨다운이 지난 시점에 실제 상태가
마지막 기록과 다르면(쿨다운 중 억제된 뒤집힘이 남아있던 경우, 또는 equal을 거쳐
조용히 전환된 경우) 그 시점 상태로 재동기화 신호를 1건 발행한다(origin=resync).
crossed_at 컬럼에 "실제로 전환된 시각"을 남겨서, resync의 발행 지연(crossed_at ->
detected_at)을 나중에 계산할 수 있게 한다.

정규장 여부는 걸러내지 않고 session(regular/extended) 컬럼으로 태그만 한다 —
장마감 후 신호도 노이즈가 아니라 NXT 연장거래 등 실제 체결일 수 있어서다.

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
from signals.ma_signal import (
    CooldownState,
    compute_ma_snapshot,
    decide_recording,
    detect_cross,
    session_for,
)

logger = logging.getLogger("signals")


def check_once(conn: sqlite3.Connection, state_by_stock: Dict[str, CooldownState]) -> int:
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

        state = state_by_stock.setdefault(stk_cd, CooldownState())
        raw_signal_type = detect_cross(state.current_relation, snapshot)

        now = datetime.now(timezone.utc)
        state.update_relation(snapshot, now)  # relation_since 갱신 (equal 경유 포함)

        result = decide_recording(raw_signal_type, snapshot, state, now, signal_settings.cooldown_min)
        if result:
            signal_type, origin, crossed_at = result
            state.last_recorded_at = now
            state.last_recorded_relation = snapshot.relation

            save_signal(
                conn,
                {
                    "stk_cd": stk_cd,
                    "signal_type": signal_type,
                    "session": session_for(now),
                    "origin": origin,
                    "short_window_min": signal_settings.short_window_min,
                    "long_window_min": signal_settings.long_window_min,
                    "short_ma": snapshot.short_ma,
                    "long_ma": snapshot.long_ma,
                    "price_at_signal": snapshot.price_at_signal,
                    "crossed_at": crossed_at.isoformat(),
                    "detected_at": now.isoformat(),
                },
            )
            new_signals += 1
            delay_sec = (now - crossed_at).total_seconds()
            logger.info(
                "%s: %s [%s] (단기 %.0f / 장기 %.0f, 현재가 %d, 발행지연 %.0f초)",
                stk_cd,
                signal_type,
                origin,
                snapshot.short_ma,
                snapshot.long_ma,
                snapshot.price_at_signal,
                delay_sec,
            )

    return new_signals


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    init_db(conn)

    state_by_stock: Dict[str, CooldownState] = {}
    try:
        while True:
            new_signals = check_once(conn, state_by_stock)
            logger.info("signal check done: %d new", new_signals)
            await asyncio.sleep(signal_settings.check_interval_sec)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
