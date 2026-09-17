"""8503 시세 수집기 진입점.

목표: 코스피/코스닥 상위 20~30개 종목의 시세를 주기적으로 수집한다.
유니버스 선정(거래대금 상위)과 실제 가격 조회 모두 키움 REST API를 사용하며,
두 조회는 동일한 KiwoomQuoteClient(인증 공유)를 통해 이루어진다.
가격 조회(fetch_quotes)는 아직 스켈레톤 상태이며, DB 저장 로직은 db/ 스키마 확정 후 채운다.
"""

from __future__ import annotations

import asyncio
import logging

from config.settings import settings
from collector.universe import get_universe
from collector.kiwoom_client import KiwoomQuoteClient

logger = logging.getLogger("collector")


async def collect_once(client: KiwoomQuoteClient, symbols: list[str]) -> None:
    quotes = await client.fetch_quotes(symbols)
    logger.info("collected %d quotes", len(quotes))
    # TODO: db/ 에 저장하는 로직 연동


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    client = KiwoomQuoteClient(use_mock=settings.use_mock)
    try:
        symbols = await get_universe(client, settings.universe_size)
        while True:
            await collect_once(client, symbols)
            await asyncio.sleep(settings.interval_sec)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
