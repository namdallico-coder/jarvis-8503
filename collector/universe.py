"""코스피/코스닥 상위 종목 유니버스 선정.

키움 REST API 거래대금상위요청(ka10032, /api/dostk/rkinfo)으로 유니버스를 뽑는다.
키움 순위정보 TR에는 시가총액 전용 랭킹이 없어, 통합시장 거래대금 상위를
"상위 시가총액/주요 종목" 유니버스의 대용치로 사용한다.

인증 및 공통 요청 처리는 collector/kiwoom_client.py(KiwoomQuoteClient)와 공유한다.

스펙 출처: https://github.com/Kiwoom-Securities/Kiwoom-REST-API
  examples/국내주식/순위정보/get_domestic_trading_value_top.py
"""

from __future__ import annotations

import logging
from typing import List

from collector.kiwoom_client import KiwoomQuoteClient

logger = logging.getLogger("collector.universe")

RANKING_PATH = "/api/dostk/rkinfo"
RANKING_API_ID = "ka10032"
RESPONSE_KEY = "trde_prica_upper"

# mrkt_tp: 000=전체(코스피+코스닥 통합), 001=코스피, 101=코스닥
MARKET_ALL = "000"


async def get_universe(
    client: KiwoomQuoteClient,
    size: int = 30,
    mrkt_tp: str = MARKET_ALL,
    mang_stk_incls: str = "0",  # 0: 관리종목 미포함
    stex_tp: str = "3",  # 1: KRX, 2: NXT, 3: 통합
) -> List[str]:
    """코스피 + 코스닥 거래대금 상위 `size`개 종목코드를 반환한다."""
    data = await client.request(
        path=RANKING_PATH,
        api_id=RANKING_API_ID,
        body={
            "mrkt_tp": mrkt_tp,
            "mang_stk_incls": mang_stk_incls,
            "stex_tp": stex_tp,
        },
    )
    rows = data.get(RESPONSE_KEY, [])
    tickers = [row["stk_cd"] for row in rows[:size] if row.get("stk_cd")]
    logger.info("universe selected: %d tickers", len(tickers))
    return tickers
