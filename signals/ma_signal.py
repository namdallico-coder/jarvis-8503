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
from datetime import datetime, time as dtime, timedelta, timezone
from statistics import mean
from typing import Literal, Optional

from config.settings import signal_settings
from db.repository import get_price_history

Relation = Literal["above", "below", "equal"]
Session = Literal["regular", "extended"]

REGULAR_OPEN = dtime(9, 0)
REGULAR_CLOSE = dtime(15, 30)


def session_for(dt: datetime) -> Session:
    """서버 로컬 타임존(KST) 기준 정규장(09:00~15:30)이면 'regular', 아니면 'extended'.

    'extended'는 노이즈가 아니라 장 전 예비호가나 NXT 연장거래 등 실제 체결도 포함한다
    (signals/backtest.py 09/28 검증: 장마감 후 신호의 거래량이 실제로 계속 누적됨).
    삭제하지 않고 태그만 남겨서 나중에 필터링 여부를 고를 수 있게 한다.
    """
    local = dt.astimezone()
    return "regular" if REGULAR_OPEN <= local.time() <= REGULAR_CLOSE else "extended"


@dataclass
class MaSnapshot:
    short_ma: float
    long_ma: float
    relation: Relation
    price_at_signal: int


def compute_ma_snapshot(
    conn: sqlite3.Connection, stk_cd: str, as_of: Optional[datetime] = None
) -> Optional[MaSnapshot]:
    """장기 창 데이터가 min_samples 미만이면(신규 상장/재시작 직후 등) None을 반환한다.

    as_of를 넘기면 그 시점을 "지금"으로 보고 계산한다 (signals/backtest.py가
    과거 시점을 재생하며 신호 판정 로직을 그대로 재사용하는 데 쓴다).
    """
    now = as_of or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    long_since = (now - timedelta(minutes=signal_settings.long_window_min)).isoformat()
    history = get_price_history(conn, stk_cd, long_since, until_iso=now_iso)
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


Origin = Literal["immediate", "resync"]

# signal_type -> 그 신호가 "기록되고 나면" 맞다고 주장하는 관계.
IMPLIED_RELATION = {"golden_cross": "above", "dead_cross": "below"}


@dataclass
class CooldownState:
    """종목 1개의 쿨다운 상태. signals/main.py와 signals/backtest.py가 종목별로 하나씩 들고 있는다."""

    last_recorded_at: Optional[datetime] = None
    last_recorded_relation: Optional[Relation] = None


def decide_recording(
    raw_signal_type: Optional[str],
    snapshot: MaSnapshot,
    state: CooldownState,
    now: datetime,
    cooldown_min: float,
) -> Optional[tuple[str, Origin]]:
    """쿨다운+재동기화 정책. (signal_type, origin) 또는 기록 안 하면 None을 반환한다.

    - 쿨다운(마지막 "기록" 이후 cooldown_min분 이내)이면 무조건 기록 안 함 — 그 사이
      뒤집힌 것들은 상태 추적(last_relation)만 되고 로그에는 안 남는다.
    - 쿨다운이 지난 시점에 막 뒤집힌 거면 즉시(immediate) 기록.
    - 막 뒤집힌 건 아니지만(쿨다운 중 억제된 뒤집힘 때문에) 마지막 "기록된" 관계와
      지금 실제 관계가 다르면, 그 시점에 재동기화(resync) 신호를 1건 발행해서
      DB가 실제 상태를 계속 정확히 반영하게 한다 — "쿨다운 만료 후에도 영영
      재동기화가 안 되는" 문제(직전 대화에서 확인한 052690_AL 사례)를 막는다.

    호출한 쪽은 반환값이 있으면 state.last_recorded_at/last_recorded_relation을
    갱신해야 한다 (이 함수는 조회만 하고 상태를 바꾸지 않는다).
    """
    cooldown_active = (
        state.last_recorded_at is not None
        and (now - state.last_recorded_at).total_seconds() / 60 < cooldown_min
    )
    if cooldown_active:
        return None

    if raw_signal_type is not None:
        return raw_signal_type, "immediate"

    implied = state.last_recorded_relation
    if implied is not None and snapshot.relation != "equal" and snapshot.relation != implied:
        resync_type = "golden_cross" if snapshot.relation == "above" else "dead_cross"
        return resync_type, "resync"

    return None
