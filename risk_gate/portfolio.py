"""페이퍼 포지션 상태 계산.

실제 계좌 연동이 없어서, risk_gate가 스스로 승인한 매매만 담는 paper_trades
원장(db/repository.py)으로부터 "지금 보유 중인지" / "몇 번 물타기했는지" /
"오늘 손익이 얼마인지"를 파생 계산한다. 수량(주식 수)/금액 개념이 아직 없어서,
계좌를 RiskGateSettings.max_concurrent_holdings개의 균등 슬롯으로 나눴다고 가정해
비중과 손익을 근사한다 — 실제 계좌 연동 후에는 이 모듈 전체가 진짜 잔고 조회로
교체되어야 한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from db.repository import get_all_traded_stocks, get_latest_price, get_paper_trades


def _local_date(iso_str: str) -> str:
    """UTC-aware ISO 문자열을 서버 로컬 타임존(KST) 기준 YYYYMMDD로 변환한다."""
    return datetime.fromisoformat(iso_str).astimezone().strftime("%Y%m%d")


def _open_buys_since_last_sell(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """trades(시간순)에서 마지막 매도 이후의 매수들만 남긴다 (없으면 매수 전체)."""
    last_sell_idx = -1
    for i, t in enumerate(trades):
        if t["action"] == "sell":
            last_sell_idx = i
    open_trades = trades[last_sell_idx + 1 :]
    return [t for t in open_trades if t["action"] == "buy"]


def is_held(conn: sqlite3.Connection, stk_cd: str) -> bool:
    trades = get_paper_trades(conn, stk_cd)
    return bool(trades) and trades[-1]["action"] == "buy"


def get_open_position_count(conn: sqlite3.Connection) -> int:
    """현재 마지막 거래가 buy인(=보유 중인) 종목 수."""
    return sum(1 for stk_cd in get_all_traded_stocks(conn) if is_held(conn, stk_cd))


def get_averaging_count(conn: sqlite3.Connection, stk_cd: str) -> int:
    """현재 포지션을 연 이후 추가로 매수한 횟수(첫 매수=진입, 물타기 아님)."""
    open_buys = _open_buys_since_last_sell(get_paper_trades(conn, stk_cd))
    return max(0, len(open_buys) - 1)


def get_entry_price(conn: sqlite3.Connection, stk_cd: str) -> Optional[float]:
    """현재 열려 있는 포지션의 평단가(진입 이후 매수들의 단순평균)."""
    open_buys = _open_buys_since_last_sell(get_paper_trades(conn, stk_cd))
    if not open_buys:
        return None
    prices = [t["price"] for t in open_buys]
    return sum(prices) / len(prices)


def _reconstruct_cycles(trades: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """trades(시간순)를 매수->매도 사이클로 재구성한다.

    반환: (청산된 사이클 리스트[{entry_price, close_price, closed_at}], 현재 열린 포지션의 평단가 또는 None)
    """
    cycles: List[Dict[str, Any]] = []
    open_buys: List[float] = []
    for t in trades:
        if t["action"] == "buy":
            open_buys.append(t["price"])
        elif t["action"] == "sell":
            if open_buys:
                entry_price = sum(open_buys) / len(open_buys)
                cycles.append(
                    {"entry_price": entry_price, "close_price": t["price"], "closed_at": t["executed_at"]}
                )
                open_buys = []
    open_entry = sum(open_buys) / len(open_buys) if open_buys else None
    return cycles, open_entry


def get_daily_pnl_pct(conn: sqlite3.Connection, max_concurrent_holdings: int) -> float:
    """오늘(서버 로컬 날짜) 기준 추정 계좌 손익률(%)을 반환한다.

    균등 슬롯 가정: 슬롯 하나(1/max_concurrent_holdings)씩 오늘 청산된 실현손익률과
    현재 보유 중인 포지션들의 미실현손익률을 더한다. 미실현손익은 "포지션을 연 이후
    전체" 변화율이라 여러 날에 걸친 포지션이면 다소 과장/과소될 수 있다
    (시스템이 아직 하루치 데이터뿐이라 지금은 문제 되지 않음 — TODO: 멀티데이 운영
    시 "오늘 시가 대비" 방식으로 보정 필요).
    """
    today = datetime.now().strftime("%Y%m%d")
    slot_weight = 1.0 / max_concurrent_holdings
    total_pct = 0.0

    for stk_cd in get_all_traded_stocks(conn):
        trades = get_paper_trades(conn, stk_cd)
        cycles, open_entry = _reconstruct_cycles(trades)

        for cycle in cycles:
            if _local_date(cycle["closed_at"]) == today:
                realized_return = (cycle["close_price"] - cycle["entry_price"]) / cycle["entry_price"]
                total_pct += realized_return * slot_weight

        if open_entry:
            latest = get_latest_price(conn, stk_cd)
            if latest:
                unrealized_return = (latest - open_entry) / open_entry
                total_pct += unrealized_return * slot_weight

    return total_pct * 100
