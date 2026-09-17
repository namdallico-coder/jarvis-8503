"""8503 뉴스/공시 수집기 진입점.

collector가 채워둔 db/collector.db(prices 테이블)에서 universe.py가 선정한
종목 목록을 읽어와, 그 종목들의 DART 신규 공시를 주기적으로 수집한다.
collector와 별개 프로세스지만 같은 collector.db를 공유한다.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict

from config.settings import news_settings
from news.dart_client import DartClient
from db.repository import get_connection, init_db, get_universe_stock_codes, save_disclosure

logger = logging.getLogger("news")


def _strip_exchange_suffix(stk_cd: str) -> str:
    """키움 종목코드의 거래소 접미사(_AL/_NX)를 뗀 DART용 6자리 코드로 변환한다."""
    return stk_cd.split("_", 1)[0]


async def load_corp_code_map(client: DartClient) -> Dict[str, str]:
    mapping = await client.fetch_corp_code_map()
    corp_code_map = {stock_code: info["corp_code"] for stock_code, info in mapping.items()}
    logger.info("corp_code map loaded: %d listed companies", len(corp_code_map))
    return corp_code_map


async def collect_once(client: DartClient, conn: sqlite3.Connection, corp_code_map: Dict[str, str]) -> int:
    stock_codes = sorted({_strip_exchange_suffix(cd) for cd in get_universe_stock_codes(conn)})
    if not stock_codes:
        logger.warning("universe가 비어있습니다 (collector를 먼저 실행해야 합니다)")
        return 0

    today = datetime.now().strftime("%Y%m%d")  # 서버 로컬 타임존(KST) 기준

    new_count = 0
    for i, stock_code in enumerate(stock_codes):
        corp_code = corp_code_map.get(stock_code)
        if not corp_code:
            logger.warning("corp_code 매핑 없음(비상장 전환/코드 변경 가능): %s", stock_code)
            continue

        disclosures = await client.fetch_disclosures(corp_code, bgn_de=today, end_de=today)
        collected_at = datetime.now(timezone.utc).isoformat()
        for disclosure in disclosures:
            if save_disclosure(conn, disclosure, collected_at):
                new_count += 1
                logger.info(
                    "신규 공시: [%s] %s (%s)",
                    disclosure.get("corp_name"),
                    disclosure.get("report_nm"),
                    disclosure.get("rcept_dt"),
                )

        if i + 1 < len(stock_codes):
            await asyncio.sleep(news_settings.request_delay_sec)

    return new_count


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    init_db(conn)

    client = DartClient()
    try:
        corp_code_map = await load_corp_code_map(client)
        last_refresh = asyncio.get_event_loop().time()

        while True:
            new_count = await collect_once(client, conn, corp_code_map)
            logger.info("disclosure check done: %d new", new_count)

            await asyncio.sleep(news_settings.disclosure_check_interval_sec)

            if asyncio.get_event_loop().time() - last_refresh > news_settings.corp_code_refresh_interval_sec:
                corp_code_map = await load_corp_code_map(client)
                last_refresh = asyncio.get_event_loop().time()
    finally:
        await client.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
