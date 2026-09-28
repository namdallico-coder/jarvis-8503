"""signals/ma_signal.py의 크로스 판정 로직을 과거 prices 데이터에 그대로 적용해 본다.

signals/main.py(실시간 루프)와 똑같은 compute_ma_snapshot() / detect_cross()를
재사용한다 — 과거 시점(as_of)을 signals/main.py의 체크 주기(check_interval_sec)
간격으로 훑으면서 "그 시점에 실시간으로 떠 있었다면 봤을 상태"를 재생하는 방식이라,
백테스트 따로 실시간 따로 로직이 어긋날 일이 없다.

사용법: PYTHONPATH=<project root> python signals/backtest.py
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config.settings import signal_settings
from db.repository import get_connection, get_latest_universe
from signals.ma_signal import (
    CooldownState,
    IMPLIED_RELATION,
    compute_ma_snapshot,
    decide_recording,
    detect_cross,
    session_for,
)


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _stock_time_range(conn, stk_cd: str, since: Optional[datetime], until: Optional[datetime]):
    if since is not None and until is not None:
        row = conn.execute(
            "SELECT MIN(collected_at), MAX(collected_at) FROM prices "
            "WHERE stk_cd = ? AND collected_at >= ? AND collected_at <= ?",
            (stk_cd, since.isoformat(), until.isoformat()),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT MIN(collected_at), MAX(collected_at) FROM prices WHERE stk_cd = ?",
            (stk_cd,),
        ).fetchone()
    if not row or row[0] is None:
        return None, None
    return _parse(row[0]), _parse(row[1])


def simulate_stock_detailed(
    conn,
    stk_cd: str,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    regular_hours_only: bool = False,
    min_gap_min: Optional[float] = None,
    min_gap_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """개선안들을 옵션으로 켤 수 있다 (기본값은 전부 off = 필터 없는 기본 동작):

    regular_hours_only: 정규장(09:00~15:30 KST) 밖에서 감지된 신호는 기록하지 않는다.
      (판정에 쓰는 이평 계산 자체는 그대로 — "신호로 기록할지"만 거른다.)
    min_gap_min: signals/ma_signal.py의 decide_recording()을 그대로 써서 쿨다운+재동기화
      정책을 적용한다 — signals/main.py(실서비스)와 완전히 같은 로직. 이걸 켜면
      min_gap_pct는 무시된다(실서비스에 쓰는 건 쿨다운뿐이라 조합을 지원 안 함).
    min_gap_pct: 단기/장기 이평 차이가 현재가 대비 이 %(예: 0.05) 이상일 때만 기록한다
      (재동기화 없는 단순 크기 필터 — 백테스트 비교 전용, 실서비스엔 안 씀).

    반환: {"crosses": [...], "final_relation": 마지막 실제 관계,
           "implied_relation": 마지막으로 "기록된" 신호가 주장하는 관계,
           "consistent": 위 둘이 일치하는지}
    """
    t_min, t_max = _stock_time_range(conn, stk_cd, since, until)
    if t_min is None:
        return {"crosses": [], "final_relation": None, "implied_relation": None, "consistent": True}
    if since is not None and t_min < since:
        t_min = since
    if until is not None and t_max > until:
        t_max = until

    step = timedelta(seconds=signal_settings.check_interval_sec)
    crosses: List[Dict[str, Any]] = []
    state = CooldownState()

    t = t_min
    while t <= t_max:
        snapshot = compute_ma_snapshot(conn, stk_cd, as_of=t)
        if snapshot is not None:
            raw_signal_type = detect_cross(state.current_relation, snapshot)
            state.update_relation(snapshot, t)  # relation_since 갱신 (equal 경유 포함)

            if min_gap_min is not None:
                result = decide_recording(raw_signal_type, snapshot, state, t, min_gap_min)
                signal_type, origin, crossed_at = result if result else (None, None, None)
            else:
                signal_type, origin, crossed_at = raw_signal_type, "immediate", t

            if signal_type:
                record = True
                if regular_hours_only and session_for(t) != "regular":
                    record = False
                if record and min_gap_min is None and min_gap_pct is not None:
                    gap_pct = abs(snapshot.short_ma - snapshot.long_ma) / snapshot.price_at_signal * 100
                    if gap_pct < min_gap_pct:
                        record = False
                if record:
                    crosses.append(
                        {
                            "as_of": t.isoformat(),
                            "signal_type": signal_type,
                            "origin": origin,
                            "crossed_at": crossed_at.isoformat(),
                            "delay_min": (t - crossed_at).total_seconds() / 60,
                            "short_ma": snapshot.short_ma,
                            "long_ma": snapshot.long_ma,
                            "price": snapshot.price_at_signal,
                        }
                    )
                    if min_gap_min is not None:
                        state.last_recorded_at = t
                        state.last_recorded_relation = snapshot.relation
        t += step

    final_relation = state.current_relation
    implied_relation = IMPLIED_RELATION.get(crosses[-1]["signal_type"]) if crosses else None
    consistent = implied_relation is None or final_relation is None or implied_relation == final_relation

    return {
        "crosses": crosses,
        "final_relation": final_relation,
        "implied_relation": implied_relation,
        "consistent": consistent,
    }


def simulate_stock(conn, stk_cd: str, **kwargs) -> List[Dict[str, Any]]:
    """simulate_stock_detailed()의 crosses만 필요할 때 쓰는 얇은 래퍼."""
    return simulate_stock_detailed(conn, stk_cd, **kwargs)["crosses"]


def main() -> None:
    conn = get_connection()
    universe = get_latest_universe(conn)
    print(f"유니버스 {len(universe)}종목, 설정: 단기{signal_settings.short_window_min}분 / "
          f"장기{signal_settings.long_window_min}분 / 체크주기{signal_settings.check_interval_sec}초\n")

    counts: Dict[str, int] = {}
    by_type: Dict[str, int] = defaultdict(int)
    details: Dict[str, List[Dict[str, Any]]] = {}

    for stock in universe:
        stk_cd = stock["stk_cd"]
        crosses = simulate_stock(conn, stk_cd)
        counts[stk_cd] = len(crosses)
        details[stk_cd] = crosses
        for c in crosses:
            by_type[c["signal_type"]] += 1

    conn.close()

    print(f"{'종목코드':12}{'종목명':16}{'크로스 수':>8}")
    for stock in sorted(universe, key=lambda s: counts[s["stk_cd"]], reverse=True):
        stk_cd = stock["stk_cd"]
        print(f"{stk_cd:12}{stock['stk_nm']:16}{counts[stk_cd]:>8}")

    values = list(counts.values())
    print("\n=== 요약 ===")
    print(f"총 크로스: {sum(values)}건 (golden_cross {by_type['golden_cross']} / dead_cross {by_type['dead_cross']})")
    print(f"종목당 평균: {statistics.mean(values):.1f}건, 중앙값: {statistics.median(values):.1f}건, "
          f"최소~최대: {min(values)}~{max(values)}건")
    zero_count = sum(1 for v in values if v == 0)
    print(f"크로스 0건 종목: {zero_count}/{len(values)}개")

    print("\n=== 크로스가 가장 많았던 종목 상세 (상위 3개) ===")
    top3 = sorted(universe, key=lambda s: counts[s["stk_cd"]], reverse=True)[:3]
    for stock in top3:
        stk_cd = stock["stk_cd"]
        print(f"\n{stk_cd} {stock['stk_nm']} ({counts[stk_cd]}건):")
        for c in details[stk_cd][:10]:
            print(f"  {c['as_of']}  {c['signal_type']:12}  단기{c['short_ma']:.0f} / 장기{c['long_ma']:.0f}  가격{c['price']}")


if __name__ == "__main__":
    main()
