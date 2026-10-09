"""순수 규칙 함수 (DB/네트워크 없음). 규칙 3: 당일 거래대금 >= 직전 20일 평균의 3배 & 상승 마감, 상한가 제외."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from closing_bet.config import ClosingBetSettings


def rank_universe(
    series: Dict[str, List[Optional[float]]], n: int = 30, min_days: int = 15
) -> List[Tuple[str, float]]:
    """code -> 직전 lookback일 거래대금 리스트(결측 None). 유효일(>0)이 min_days 미만이면 제외하고
    유효일 평균 상위 n개 (평균 내림차순, 동률은 코드 오름차순). research/universe.py의 top_universe와
    같은 결과를 내도록 만들었다 (tests/test_universe.py가 비교)."""
    scored = []
    for code, vals in series.items():
        valid = [v for v in vals if v is not None and v > 0]
        if len(valid) < min_days:
            continue
        scored.append((-sum(valid) / len(valid), code))
    scored.sort()
    return [(code, -neg) for neg, code in scored[:n]]


def _flags(cfg: ClosingBetSettings, ratio: Optional[float], flu_rt: Optional[float]) -> Dict[str, Any]:
    return {
        "cond_ratio": ratio is not None and ratio >= cfg.ratio_threshold,
        "cond_up": flu_rt is not None and flu_rt > 0,
        "cond_not_limit": flu_rt is not None and flu_rt < cfg.limit_up_pct,
    }


def _reasons(cfg: ClosingBetSettings, ratio, flu_rt, f, label: str) -> List[str]:
    out = []
    if not f["cond_ratio"]:
        out.append(f"ratio_below({label} {ratio:.2f}<{cfg.ratio_threshold})" if ratio is not None else "ratio_unknown")
    if not f["cond_up"]:
        out.append(f"not_up(flu_rt {flu_rt:+.2f}%)" if flu_rt is not None else "no_price")
    if not f["cond_not_limit"] and flu_rt is not None:
        out.append(f"limit_up(flu_rt {flu_rt:+.2f}%>={cfg.limit_up_pct})")
    return out


def judge_1520(
    cfg: ClosingBetSettings, avg20: Optional[float], amount_1520: Optional[int], flu_rt: Optional[float], in_ranking: bool
) -> Dict[str, Any]:
    """15:20 시점 판정. 추정 배수 = (누적 거래대금 / rho) / avg20."""
    if avg20 is None or avg20 <= 0:
        return {"decision": "reject", "reject_reason": "no_avg20", "est_amount": None, "est_ratio": None,
                "cond_ratio": False, "cond_up": False, "cond_not_limit": False}
    if not in_ranking:
        # 거래대금 순위 상단(조회 범위) 밖 = 규칙 3 해당 이벤트는 과거 전부 일 순위 17위 이내였으므로 미충족 처리
        return {"decision": "reject", "reject_reason": "not_in_ranking_page(15:20 거래대금 순위 조회범위 밖 → 배수 미달 처리)",
                "est_amount": None, "est_ratio": None, "cond_ratio": False, "cond_up": False, "cond_not_limit": False}
    est_amount = amount_1520 / cfg.rho
    ratio = est_amount / avg20
    f = _flags(cfg, ratio, flu_rt)
    ok = all(f.values())
    return {"decision": "buy" if ok else "reject",
            "reject_reason": None if ok else ",".join(_reasons(cfg, ratio, flu_rt, f, "est")),
            "est_amount": est_amount, "est_ratio": ratio, **f}


def judge_final(
    cfg: ClosingBetSettings, avg20: Optional[float], amount_final: Optional[int], flu_rt: Optional[float], in_ranking: bool
) -> Dict[str, Any]:
    """종가 확정 후 재판정 (rho 환산 없음)."""
    if avg20 is None or avg20 <= 0 or not in_ranking:
        return {"decision": "reject", "ratio": None, "cond_ratio": False, "cond_up": False, "cond_not_limit": False}
    ratio = amount_final / avg20
    f = _flags(cfg, ratio, flu_rt)
    return {"decision": "buy" if all(f.values()) else "reject", "ratio": ratio, **f}


def returns_pct(cfg: ClosingBetSettings, entry: float, exit_: float) -> Dict[str, float]:
    gross = (exit_ / entry - 1) * 100
    base = gross - cfg.cost_round_trip_pct
    return {
        "gross_pct": gross,
        "net_cost_pct": base,
        "net_slip_each_pct": base - 2 * cfg.slip_each_side_pct,
        "net_slip_total_pct": base - cfg.slip_total_pct,
    }
