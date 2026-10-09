"""research/daily.sqlite(읽기 전용)에서 최근 N거래일의 종목별 거래대금/종가/거래량을 closing_bet.db에 시드하고
초기 유니버스를 계산한다. 일회성 준비 작업이며 키움을 호출하지 않는다.
실행: python -m closing_bet.seed [N]"""

from __future__ import annotations

import sqlite3
import sys

from closing_bet import store
from closing_bet.config import settings


def seed(conn, cfg, n_days: int | None = None, research_db=None, universes: int = 5) -> list[str]:
    n_days = n_days or cfg.seed_days
    src = sqlite3.connect(f"file:{research_db or cfg.research_db}?mode=ro", uri=True)
    def fmt(d: str) -> str:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    dates = [r[0] for r in src.execute("SELECT dt FROM index_daily WHERE ind='001' ORDER BY dt DESC LIMIT ?", (n_days,))][::-1]
    q = ",".join("?" * len(dates))
    ts = store.now_iso()
    conn.executemany("INSERT OR REPLACE INTO cb_ordinary VALUES (?,?,?,?)",
                     [(c, n, m, ts) for c, n, m in src.execute("SELECT code, name, market FROM stocks").fetchall()])
    for d in dates:
        conn.execute("INSERT OR REPLACE INTO cb_day_meta VALUES (?,?,?,?,?)", (fmt(d), 1, 0, None, "research"))
    cur = src.execute(f"SELECT dt, code, amount, close, volume FROM daily WHERE dt IN ({q})", dates)
    while True:
        chunk = cur.fetchmany(200_000)
        if not chunk:
            break
        conn.executemany("INSERT OR REPLACE INTO cb_amount_history VALUES (?,?,?,?,?)",
                         [(fmt(r[0]), r[1], r[2], r[3], r[4]) for r in chunk])
    conn.commit()
    hist = store.history_dates(conn)
    for asof in hist[-universes:]:
        if len(store.history_dates(conn, asof)) >= cfg.lookback_days:
            store.compute_universe(conn, cfg, asof)
    return hist


if __name__ == "__main__":
    c = store.connect(settings.db_path)
    h = seed(c, settings, int(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"시드 완료: 거래일 {len(h)}개 ({h[0]}~{h[-1]}), 종목 {c.execute('select count(*) from cb_ordinary').fetchone()[0]}, "
          f"최신 유니버스 asof={c.execute('select max(asof_dt) from cb_universe').fetchone()[0]}")


async def prime_snapshot(conn, api, dt: str) -> str:
    """운영 DB에 마지막 거래일 마감 순위 스냅샷을 1회 넣어, 첫 실행일에도 휴장 감지(응답 정체 비교)가 되게 한다.
    장이 닫힌 시각에 호출해야 한다(마지막 거래일 마감값이 돌아옴). 키움 1콜."""
    from closing_bet.calendar_check import signature
    rows = await api.ranking(1)
    store.save_snapshot(conn, dt, "1545", signature(rows), len(rows))
    return signature(rows)
