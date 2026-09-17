"""코스피/코스닥 상위 종목 유니버스 선정.

키움 REST API 거래대금상위요청(ka10032, /api/dostk/rkinfo)으로 후보를 뽑는다.
키움 순위정보 TR에는 시가총액 전용 랭킹이 없어, 통합시장 거래대금 상위를
"상위 시가총액/주요 종목" 유니버스의 대용치로 쓰되, ETF/ETN/리츠 등은 개별
기업이 아니므로 종목정보 리스트(ka10099)로 만든 코스피+코스닥 "일반 종목"
화이트리스트로 걸러낸다.

주의: ka10099의 요청 파라미터 mrkt_tp='0'(코스피)으로 조회해도 응답 자체가
코스피 "거래소" 전체(ETF/ETN/리츠 등 포함, 실측 2484건 중 순수 개별종목은 917건)를
그대로 돌려준다 — 요청 파라미터는 필터가 아니다. 대신 각 행의 marketCode 필드로
'0'(거래소, 개별종목)만 걸러야 한다. mrkt_tp='10'(코스닥)은 응답이 전부
marketCode='10'이라 코스닥엔 이 문제가 없다(ETF는 코스피에만 상장됨, 실측 확인).

인증 및 공통 요청 처리는 collector/kiwoom_client.py(KiwoomQuoteClient)와 공유한다.

스펙 출처: https://github.com/Kiwoom-Securities/Kiwoom-REST-API
  examples/국내주식/순위정보/get_domestic_trading_value_top.py
  examples/국내주식/종목정보/list_domestic_stocks.py
"""

from __future__ import annotations

import logging
from typing import List, Set

from collector.kiwoom_client import KiwoomQuoteClient

logger = logging.getLogger("collector.universe")

RANKING_PATH = "/api/dostk/rkinfo"
RANKING_API_ID = "ka10032"
RESPONSE_KEY = "trde_prica_upper"

# mrkt_tp: 000=전체(코스피+코스닥 통합), 001=코스피, 101=코스닥
MARKET_ALL = "000"

# ka10099 요청 파라미터 (0=코스피, 10=코스닥)
QUERY_MARKET_TYPES = ("0", "10")
# ka10099 응답의 marketCode 필드 중 "개별 종목"으로 인정할 값 (ETF=8/ETN=60,70,90/리츠=6/
# 인프라투자금융=2/뮤추얼펀드=4 등은 제외)
ORDINARY_STOCK_MARKET_CODES = {"0", "10"}


def _strip_exchange_suffix(stk_cd: str) -> str:
    """'005930_AL' -> '005930' (거래소 접미사 제거)."""
    return stk_cd.split("_", 1)[0]


async def get_ordinary_stock_codes(client: KiwoomQuoteClient) -> Set[str]:
    """코스피+코스닥 일반 종목(ETF/ETN/리츠/ELW 등 제외) 코드 화이트리스트를 만든다."""
    codes: Set[str] = set()
    for mrkt_tp in QUERY_MARKET_TYPES:
        rows = await client.fetch_stock_list(mrkt_tp)
        codes.update(
            row["code"]
            for row in rows
            if row.get("code") and row.get("marketCode") in ORDINARY_STOCK_MARKET_CODES
        )
    logger.info("ordinary stock whitelist: %d codes", len(codes))
    return codes


async def get_universe(
    client: KiwoomQuoteClient,
    size: int = 30,
    mrkt_tp: str = MARKET_ALL,
    mang_stk_incls: str = "0",  # 0: 관리종목 미포함
    stex_tp: str = "3",  # 1: KRX, 2: NXT, 3: 통합
) -> List[str]:
    """코스피 + 코스닥 거래대금 상위 종목 중, ETF 등을 제외한 개별 기업 `size`개를 반환한다."""
    whitelist = await get_ordinary_stock_codes(client)

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

    tickers: List[str] = []
    excluded = 0
    for row in rows:
        stk_cd = row.get("stk_cd")
        if not stk_cd:
            continue
        if _strip_exchange_suffix(stk_cd) not in whitelist:
            excluded += 1
            continue
        tickers.append(stk_cd)
        if len(tickers) >= size:
            break

    if len(tickers) < size:
        logger.warning(
            "요청한 %d개를 못 채움: %d개만 확보 (거래대금상위 후보 %d개 중 %d개가 ETF 등으로 제외됨)",
            size,
            len(tickers),
            len(rows),
            excluded,
        )

    logger.info("universe selected: %d tickers (ETF 등 %d개 제외)", len(tickers), excluded)
    return tickers
