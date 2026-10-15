"""8503 시세 수집기 진입점.

목표: 코스피/코스닥 상위 20~30개 종목의 시세를 주기적으로 수집한다.
유니버스 선정(거래대금 상위)과 실제 가격 조회 모두 키움 REST API를 사용하며,
두 조회는 동일한 KiwoomQuoteClient(인증 공유)를 통해 이루어진다.
수집한 시세는 db/prices 테이블(db/schema.sql)에 적재한다.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

from config.settings import settings
from collector.universe import get_universe
from collector.kiwoom_client import KiwoomQuoteClient
from db.repository import get_connection, init_db, save_price

logger = logging.getLogger("collector")


async def collect_once(client: KiwoomQuoteClient, symbols: list[str], conn: sqlite3.Connection) -> None:
    quotes = await client.fetch_quotes(symbols)
    collected_at = datetime.now(timezone.utc).isoformat()
    # 키움 점검 시간대에는 return_code가 정상인데 현재가/거래량이 빈 값(None)으로 오는 응답이 있다.
    # prices.cur_prc/trde_qty는 NOT NULL이라 그대로 저장하면 프로세스가 죽는다 -> None인 시세만 건너뛴다(0은 정상 값).
    skipped = [q["stk_cd"] for q in quotes if q["cur_prc"] is None or q["trde_qty"] is None]
    if skipped:
        logger.warning("빈 시세 %d건 스킵(키움 점검 시간대 가능): %s", len(skipped), ", ".join(skipped))
    saved = 0
    for quote in quotes:
        if quote["cur_prc"] is None or quote["trde_qty"] is None:
            continue
        save_price(conn, quote, collected_at)
        saved += 1
    logger.info("collected %d quotes", saved)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    init_db(conn)

    client = KiwoomQuoteClient(use_mock=settings.use_mock)
    try:
        symbols = await get_universe(client, settings.universe_size)
        while True:
            await collect_once(client, symbols, conn)
            await asyncio.sleep(settings.interval_sec)
    finally:
        await client.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
