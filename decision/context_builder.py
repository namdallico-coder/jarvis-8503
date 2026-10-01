"""종목별 AI 판단 입력 컨텍스트 구성.

signals 테이블에서 넘어온 크로스 신호 1건을 중심으로, prices/disclosures에서
최근 시세 흐름과 오늘자 신규 공시를 모아 Claude에게 넘길 구조화 데이터를 만든다.

보유 상태는 아직 계좌 연동 컴포넌트가 없어 전부 미보유로 고정한다.
TODO: 계좌 연동 후 실제 보유수량/평단가/수익률로 교체.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from config.settings import decision_settings
from db.repository import get_price_history, get_todays_disclosures

PRICE_SAMPLE_POINTS = 12  # 1시간치를 대략 5분 간격 수준으로 압축해서 프롬프트에 싣는다


def _strip_exchange_suffix(stk_cd: str) -> str:
    """키움 종목코드의 거래소 접미사(_AL/_NX)를 뗀 DART용 6자리 코드로 변환한다."""
    return stk_cd.split("_", 1)[0]


def _downsample(rows: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """rows가 n개보다 많으면 처음/끝을 포함해 균등 간격으로 n개만 남긴다."""
    if len(rows) <= n:
        return rows
    step = (len(rows) - 1) / (n - 1)
    indices = sorted({round(i * step) for i in range(n)})
    return [rows[i] for i in indices]


def build_context(
    conn: sqlite3.Connection,
    stk_cd: str,
    stk_nm: str,
    signal: Dict[str, Any],
    as_of: datetime | None = None,
) -> Dict[str, Any]:
    """signal(get_latest_signals_since()가 준 신호 1건)을 리뷰하는 데 필요한 컨텍스트를 만든다.

    as_of를 넘기면 "최근 1시간"을 실제 현재 시각이 아니라 그 시점 기준으로 계산한다
    (옛 신호를 다시 리뷰할 때, 실행 시점의 장마감 후 정체 데이터가 안 섞이게 하려고 씀 —
    실시간 운영에서는 항상 생략하고 실제 지금을 쓴다).
    """
    now = as_of or datetime.now(timezone.utc)
    since = (now - timedelta(hours=decision_settings.price_lookback_hours)).isoformat()
    price_rows = get_price_history(conn, stk_cd, since, until_iso=now.isoformat())
    price_history = _downsample(price_rows, PRICE_SAMPLE_POINTS)

    today = now.astimezone().strftime("%Y%m%d")  # 서버 로컬 타임존(KST) 기준
    disclosures_today = get_todays_disclosures(conn, _strip_exchange_suffix(stk_cd), today)

    return {
        "stk_cd": stk_cd,
        "stk_nm": stk_nm,
        "signal": {
            "signal_type": signal["signal_type"],
            "short_window_min": signal["short_window_min"],
            "long_window_min": signal["long_window_min"],
            "short_ma": signal["short_ma"],
            "long_ma": signal["long_ma"],
            "price_at_signal": signal["price_at_signal"],
            "detected_at": signal["detected_at"],
        },
        "price_history_1h": price_history,
        "disclosures_today": disclosures_today,
        "holding": {"held": False},
    }
