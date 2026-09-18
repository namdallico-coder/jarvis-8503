"""안전장치 하드룰.

decision이 follow로 판단해 buy/sell이 확정된 건에 대해, 실제 주문이 가능한지
최종 체크한다. 매도는 리스크를 줄이는 방향이라 하드룰로 막지 않는다(포지션이
없는데 팔려는 경우만 예외적으로 막는다 — 페이퍼 모드에서 말이 안 되는 주문이라).

하드룰 (매수에만 적용):
  1. 1회 매매 최대 비중 (계좌 총액 대비 %)
  2. 동시 보유 종목 수 제한 (신규 진입일 때만 — 이미 보유 중이면 물타기 룰로 대체)
  3. 일일 최대 손실 상한 도달 시 신규 매수 차단
  4. 동일 종목 물타기 최대 횟수 제한 (이미 보유 중일 때만)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List

from config.settings import RiskGateSettings
from risk_gate import portfolio


@dataclass
class RuleCheck:
    rule: str
    passed: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule, "passed": self.passed, "reason": self.reason}


def check_position_exists(conn: sqlite3.Connection, stk_cd: str) -> RuleCheck:
    """매도 전용: 페이퍼 포지션이 없는데 팔 수는 없다."""
    held = portfolio.is_held(conn, stk_cd)
    return RuleCheck(
        rule="position_exists",
        passed=held,
        reason=(f"{stk_cd} 보유 중, 매도 가능" if held else f"{stk_cd} 페이퍼 포지션 없음 — 매도 불가"),
    )


def check_position_size(settings: RiskGateSettings) -> RuleCheck:
    """균등비중 가정(1/max_concurrent_holdings)이 1회 매매 상한을 넘지 않는지 확인."""
    implied_pct = 100.0 / settings.max_concurrent_holdings
    passed = implied_pct <= settings.max_position_pct
    return RuleCheck(
        rule="position_size",
        passed=passed,
        reason=(
            f"균등비중 가정 시 1회 매매 비중 {implied_pct:.1f}% "
            f"(상한 {settings.max_position_pct:.1f}%)"
        ),
    )


def check_concurrent_holdings(conn: sqlite3.Connection, settings: RiskGateSettings) -> RuleCheck:
    """신규 진입(아직 안 보유 중인 종목)일 때만 의미 있는 룰."""
    count = portfolio.get_open_position_count(conn)
    passed = count < settings.max_concurrent_holdings
    return RuleCheck(
        rule="concurrent_holdings",
        passed=passed,
        reason=f"현재 동시보유 {count}/{settings.max_concurrent_holdings}종목",
    )


def check_averaging_limit(conn: sqlite3.Connection, stk_cd: str, settings: RiskGateSettings) -> RuleCheck:
    count = portfolio.get_averaging_count(conn, stk_cd)
    passed = count < settings.max_averaging_count
    return RuleCheck(
        rule="averaging_limit",
        passed=passed,
        reason=f"{stk_cd} 물타기 {count}/{settings.max_averaging_count}회",
    )


def check_daily_loss_limit(conn: sqlite3.Connection, settings: RiskGateSettings) -> RuleCheck:
    pnl_pct = portfolio.get_daily_pnl_pct(conn, settings.max_concurrent_holdings)
    passed = pnl_pct > settings.daily_loss_limit_pct
    return RuleCheck(
        rule="daily_loss_limit",
        passed=passed,
        reason=f"금일 추정 손익 {pnl_pct:+.2f}% (상한 {settings.daily_loss_limit_pct:.1f}%)",
    )


def evaluate(conn: sqlite3.Connection, decision: Dict[str, Any], settings: RiskGateSettings) -> tuple[bool, List[RuleCheck]]:
    """decision(action=buy 또는 sell 확정 건)을 하드룰로 검사해 (승인여부, 체크목록)을 반환한다."""
    stk_cd = decision["stk_cd"]
    action = decision["action"]

    checks: List[RuleCheck] = []

    if action == "sell":
        checks.append(check_position_exists(conn, stk_cd))
    elif action == "buy":
        checks.append(check_position_size(settings))
        checks.append(check_daily_loss_limit(conn, settings))
        if portfolio.is_held(conn, stk_cd):
            checks.append(check_averaging_limit(conn, stk_cd, settings))
        else:
            checks.append(check_concurrent_holdings(conn, settings))
    else:
        raise ValueError(f"risk_gate는 buy/sell만 검사한다: action={action!r}")

    approved = all(c.passed for c in checks)
    return approved, checks
