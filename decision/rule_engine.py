"""Rule-v1: 외부 AI API 없이 크로스 신호를 follow/hold로 판정하는 규칙 엔진.

기존에 Claude가 맡던 "이 신호를 따를지 보류할지" 역할을 대체한다. 입력은 signals 행 +
prices(최근 시세) + 보유 상태뿐이고, 새 지표는 쓰지 않는다.

규칙 (순서대로):
  1. 골든크로스인데 이미 보유 중  -> hold (추가매수 안 함)
  2. 데드크로스인데 미보유        -> hold (팔 포지션 없음; 보통은 main에서 이미 스킵됨)
  3. 신호 직전 가격 이력 부족      -> hold (판단 근거 없음)
  4. "추격 금지": 신호 시점 직전 lookback(15분) 동안 가격이 신호 방향으로 이미
     momentum_cap_pct(0.3%)를 넘게 움직였으면 hold. 골든은 상승분, 데드는 하락분.
     (이동평균 크로스는 후행 지표라 이미 크게 움직인 뒤의 신호는 추격이 되기 쉽다)
  5. 그 외 follow

갭(이평 간격 %)과 origin(immediate/resync)은 판정에는 안 쓰고 reason에만 남긴다 —
기존 신호 데이터에서 이들이 뒤따르는 수익률을 의미 있게 가르지 못했기 때문이다.

confidence는 확률이 아니라 "임계값에서 얼마나 떨어져 있는가"의 점수(50~100)다:
50이 임계값 바로 위/아래, 100이 임계값의 2배 이상 떨어진 경우.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config.settings import decision_settings
from db.repository import get_price_history

MODEL_NAME = "rule-v1"


def _price_before(conn: sqlite3.Connection, stk_cd: str, target: datetime) -> Optional[int]:
    """target 시각 이전 마지막 시세. tolerance보다 오래된 값이면 None (정체 데이터 방지)."""
    tol = timedelta(seconds=decision_settings.rule_price_tolerance_sec)
    rows = get_price_history(
        conn, stk_cd, (target - tol).isoformat(), until_iso=target.isoformat()
    )
    return abs(rows[-1]["cur_prc"]) if rows else None


def compute_features(conn: sqlite3.Connection, signal: Dict[str, Any]) -> Dict[str, Any]:
    t = datetime.fromisoformat(signal["detected_at"])
    lookback = timedelta(minutes=decision_settings.rule_lookback_min)
    p_before = _price_before(conn, signal["stk_cd"], t - lookback)
    price = signal["price_at_signal"]
    move = None  # 신호 직전 lookback 동안 가격 변화율(%) — 부호 그대로
    if p_before:
        move = (price / p_before - 1) * 100
    gap = abs(signal["short_ma"] - signal["long_ma"]) / price * 100
    return {"move_pct": move, "gap_pct": gap, "origin": signal.get("origin") or "-"}


def _score(distance: float, cap: float) -> int:
    return round(50 + 50 * max(0.0, min(1.0, distance / cap)))


def evaluate(conn: sqlite3.Connection, signal: Dict[str, Any], holding: Dict[str, Any]) -> Dict[str, Any]:
    """반환: {"action": "follow"|"hold", "confidence": 50~100, "reason": str, "features": {...}}"""
    cap = decision_settings.rule_momentum_cap_pct
    feat = compute_features(conn, signal)
    held = bool(holding.get("held"))
    is_golden = signal["signal_type"] == "golden_cross"
    tag = f"갭 {feat['gap_pct']:.2f}%, {feat['origin']}"

    def out(action: str, confidence: int, reason: str) -> Dict[str, Any]:
        return {"action": action, "confidence": confidence, "reason": reason, "features": feat}

    if is_golden and held:
        return out("hold", 100, f"보유중 추가매수 제외, {tag}")
    if not is_golden and not held:
        return out("hold", 100, f"미보유 매도 대상 아님, {tag}")
    if feat["move_pct"] is None:
        return out("hold", 50, f"직전 {decision_settings.rule_lookback_min}분 시세 부족, {tag}")

    run = feat["move_pct"] if is_golden else -feat["move_pct"]  # 신호 방향으로 이미 움직인 정도
    label = f"{decision_settings.rule_lookback_min}분 {'+' if feat['move_pct'] >= 0 else ''}{feat['move_pct']:.2f}%"
    if run > cap:
        return out("hold", _score(run - cap, cap), f"추격금지({label}>{cap:.2f}%), {tag}")
    return out("follow", _score(cap - run, cap), f"추격아님({label}<={cap:.2f}%), {tag}")
