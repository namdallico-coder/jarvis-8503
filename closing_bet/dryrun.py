"""드라이런: 실제 키움 응답(읽기 전용 조회만)으로 15:20 판정 → 15:45 마감 → 시초가 청산 경로를 임시 DB에서 한 번 돌린다.

운영 closing_bet.db는 건드리지 않고 복사본(임시 파일)에만 쓴다. 장이 닫힌 시각에 돌리면 키움은 마지막 거래일(예: 10/8)
마감값을 돌려주므로, 그 값을 "15:20 값"으로 간주해 파이프라인과 파서, 휴장 감지(응답 정체)를 점검한다.
실행: python -m closing_bet.dryrun [YYYY-MM-DD(시뮬레이션할 거래일, 기본 마지막 시드 일자)]
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from closing_bet import store
from closing_bet.config import KST, settings as cfg
from closing_bet.jobs import run_exit, run_finalize, run_judge
from closing_bet.kiwoom_api import KiwoomAPI


class CountingAPI(KiwoomAPI):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def ranking(self, pages):
        self.calls.append(f"ka10032 x{pages}p")
        return await super().ranking(pages)

    async def quote(self, code):
        self.calls.append(f"ka10001 {code}")
        return await super().quote(code)

    async def ordinary(self):
        self.calls.append("ka10099")
        return await super().ordinary()


def at(day: str, hms: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hms}").replace(tzinfo=KST)


async def main(day: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    tmp = Path(tempfile.mkdtemp()) / "dryrun.db"
    shutil.copy(cfg.db_path, tmp)
    conn = store.connect(tmp)
    api = CountingAPI()
    try:
        print(f"\n=== 드라이런 (임시 DB {tmp}) 시뮬레이션 거래일 {day} ===")
        print("1) 15:20 판정", await run_judge(conn, cfg, api, at(day, "15:20:05")))
        rows = conn.execute("SELECT * FROM cb_judgments WHERE dt=? ORDER BY universe_rank", (day,)).fetchall()
        print(f"   유니버스 {len(rows)}종목, 순위권 내 {sum(r['in_ranking'] for r in rows)}, 매수 {sum(r['decision']=='buy' for r in rows)}")
        print("   순위 코드     avg20(백만원)  15:20대금   추정배수  등락률  판정  사유")
        for r in rows:
            print(f"   {r['universe_rank']:>3} {r['code']:<7} {r['avg20']:>13,.0f} {r['amount_1520'] or 0:>11,} "
                  f"{(r['est_ratio'] or 0):>7.2f} {(r['flu_rt'] if r['flu_rt'] is not None else 0):>+7.2f} {r['decision']:<6} {r['reject_reason'] or ''}")
        print("2) 15:45 마감", await run_finalize(conn, cfg, api, at(day, "15:45:00")))
        s = conn.execute("SELECT COUNT(*) n, SUM(agree_decision) a, SUM(agree_ratio) ar, SUM(agree_up) au FROM cb_judgments WHERE dt=?", (day,)).fetchone()
        print(f"   재판정 일치: 판정 {s['a']}/{s['n']}, 배수조건 {s['ar']}/{s['n']}, 상승조건 {s['au']}/{s['n']}")
        print(f"   다음 유니버스 asof={conn.execute('select max(asof_dt) from cb_universe').fetchone()[0]}, 실측ρ(같은 데이터라 1.0 기대): {store.rho_summary(conn)}")
        # 합성 포지션 하나로 시초가 청산 경로 점검(임시 DB에서만)
        if not conn.execute("SELECT 1 FROM cb_trades WHERE status='open'").fetchone():
            conn.execute("INSERT INTO cb_trades (dt_entry, code, name, entry_price, status) VALUES (?, '005930', '삼성전자(합성)', 263000, 'open')", (day,))
            conn.commit()
            print("   (실제 매수 판정이 없어 청산 경로 점검용 합성 포지션 1건을 임시 DB에만 추가)")
        nxt = "2026-10-12"
        print(f"3) {nxt}(월) 15:20 — 장이 닫혀 있어 응답이 마감값 그대로 돌아오는 상황:", await run_judge(conn, cfg, api, at(nxt, "15:20:05")))
        print(f"4) {nxt}(월) 09:00:20 시초가 청산 —", await run_exit(conn, cfg, api, at(nxt, "09:00:20")))
        print("\n--- 이벤트 로그 ---")
        for e in conn.execute("SELECT dt, kind, message FROM cb_events ORDER BY id"):
            print(f"   [{e['dt']}] {e['kind']}: {e['message']}")
        print(f"\n키움 호출 {len(api.calls)}건: {api.calls}")
    finally:
        await api.close()
        conn.close()


if __name__ == "__main__":
    last = store.connect(cfg.db_path).execute("SELECT MAX(dt) FROM cb_day_meta").fetchone()[0]
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else last))
