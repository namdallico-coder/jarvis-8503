"""이동평균 크로스 신호 계산.

collector가 쌓는 prices 테이블의 시세로 단기/장기 단순이동평균(SMA)을 시간창
기준으로 계산하고, 두 이동평균의 상하관계가 뒤집히는 순간(크로스)을 감지한다.

기간 선택 근거는 config/settings.py의 SignalSettings 주석 참고.

크로스 판정은 상태 비교 방식이다: 매 체크마다 현재 상/하 관계를 직전 체크 때의
관계와 비교해서, 관계가 뒤집힌 순간만 신호로 판단한다. 과거 시계열 전체를 매번
재구성하는 대신, 프로세스가 떠 있는 동안의 마지막 관계만 (signals/main.py가)
메모리에 들고 있으면 된다 — 재시작하면 상태가 초기화되어 재시작 직후 딱 한 번의
크로스를 놓칠 수 있지만(다음 체크부터는 정상 감지), 그 정도 손실은 감수할 만하다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Literal, Optional

from config.settings import signal_settings
from db.repository import get_price_history

Relation = Literal["above", "below", "equal"]


@dataclass
class MaSnapshot:
    short_ma: float
    long_ma: float
    relation: Relation
    price_at_signal: int


def compute_ma_snapshot(conn: sqlite3.Connection, stk_cd: str) -> Optional[MaSnapshot]:
    """장기 창 데이터가 min_samples 미만이면(신규 상장/재시작 직후 등) None을 반환한다."""
    now = datetime.now(timezone.utc)
    long_since = (now - timedelta(minutes=signal_settings.long_window_min)).isoformat()
    history = get_price_history(conn, stk_cd, long_since)
    if len(history) < signal_settings.min_samples:
        return None

    short_since = (now - timedelta(minutes=signal_settings.short_window_min)).isoformat()
    short_prices = [row["cur_prc"] for row in history if row["collected_at"] >= short_since]
    if len(short_prices) < signal_settings.min_samples:
        return None

    long_prices = [row["cur_prc"] for row in history]
    short_ma = mean(short_prices)
    long_ma = mean(long_prices)

    if short_ma > long_ma:
        relation: Relation = "above"
    elif short_ma < long_ma:
        relation = "below"
    else:
        relation = "equal"

    return MaSnapshot(
        short_ma=short_ma,
        long_ma=long_ma,
        relation=relation,
        price_at_signal=history[-1]["cur_prc"],
    )


def detect_cross(prev_relation: Optional[Relation], snapshot: MaSnapshot) -> Optional[str]:
    """직전 관계 대비 골든/데드 크로스가 발생했으면 signal_type을, 아니면 None을 반환한다."""
    if prev_relation is None or prev_relation == "equal" or snapshot.relation == "equal":
        return None
    if prev_relation == snapshot.relation:
        return None
    return "golden_cross" if snapshot.relation == "above" else "dead_cross"
