"""코스피/코스닥 상위 시가총액 종목 유니버스 선정.

pykrx로 KOSPI + KOSDAQ 시가총액 데이터를 조회해 상위 `size`개 종목코드를 뽑는다.
실제 가격 조회(키움 API)는 collector/kiwoom_client.py 가 담당한다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List

import pandas as pd
from pykrx import stock

logger = logging.getLogger("collector.universe")

_MARKETS = ("KOSPI", "KOSDAQ")
_MAX_LOOKBACK_DAYS = 10


def _latest_market_cap(markets: tuple[str, ...] = _MARKETS) -> pd.DataFrame:
    """최근 영업일 기준 시가총액 데이터를 조회한다.

    휴장일(주말/공휴일)에는 데이터가 비어 있으므로, 데이터가 나올 때까지
    최대 _MAX_LOOKBACK_DAYS일 전까지 거슬러 올라간다.
    """
    day = date.today()
    for _ in range(_MAX_LOOKBACK_DAYS):
        ymd = day.strftime("%Y%m%d")
        frames = []
        for market in markets:
            df = stock.get_market_cap_by_ticker(ymd, market=market)
            if not df.empty:
                df = df.copy()
                df["시장"] = market
                frames.append(df)
        if frames:
            return pd.concat(frames)
        day -= timedelta(days=1)
    raise RuntimeError(
        f"최근 {_MAX_LOOKBACK_DAYS}일 내 시가총액 데이터를 조회하지 못했습니다."
    )


def get_universe(size: int = 30) -> List[str]:
    """코스피 + 코스닥 시가총액 상위 `size`개 종목코드를 반환한다."""
    market_cap = _latest_market_cap()
    top = market_cap.sort_values("시가총액", ascending=False).head(size)
    tickers = top.index.tolist()
    logger.info("universe selected: %d tickers", len(tickers))
    return tickers
