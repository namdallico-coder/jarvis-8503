"""8503 시세 수집기 진입점.

목표: 코스피/코스닥 상위 시가총액 20~30개 종목의 시세를 주기적으로 수집한다.
유니버스 선정은 pykrx(무료, 계정 불필요), 실제 가격 조회는 키움 REST API를 사용한다.
가격 조회(kiwoom_client)는 아직 스켈레톤 상태이며, DB 저장 로직은 db/ 스키마 확정 후 채운다.
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

    symbols = get_universe(settings.universe_size)
    client = KiwoomQuoteClient(use_mock=settings.use_mock)

    try:
        while True:
            await collect_once(client, symbols)
            await asyncio.sleep(settings.interval_sec)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
