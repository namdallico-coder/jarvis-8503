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
from typing import Any, Dict, List

from config.settings import signal_settings
from db.repository import get_connection, get_latest_universe
from signals.ma_signal import compute_ma_snapshot, detect_cross


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _stock_time_range(conn, stk_cd: str):
    row = conn.execute(
        "SELECT MIN(collected_at), MAX(collected_at) FROM prices WHERE stk_cd = ?",
        (stk_cd,),
    ).fetchone()
    if not row or row[0] is None:
        return None, None
    return _parse(row[0]), _parse(row[1])


def simulate_stock(conn, stk_cd: str) -> List[Dict[str, Any]]:
    t_min, t_max = _stock_time_range(conn, stk_cd)
    if t_min is None:
        return []

    step = timedelta(seconds=signal_settings.check_interval_sec)
    crosses: List[Dict[str, Any]] = []
    prev_relation = None

    t = t_min
    while t <= t_max:
        snapshot = compute_ma_snapshot(conn, stk_cd, as_of=t)
        if snapshot is not None:
            signal_type = detect_cross(prev_relation, snapshot)
            if signal_type:
                crosses.append(
                    {
                        "as_of": t.isoformat(),
                        "signal_type": signal_type,
                        "short_ma": snapshot.short_ma,
                        "long_ma": snapshot.long_ma,
                        "price": snapshot.price_at_signal,
                    }
                )
            prev_relation = snapshot.relation
        t += step

    return crosses


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
