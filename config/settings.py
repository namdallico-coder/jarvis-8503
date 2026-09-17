from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CollectorSettings:
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ")
    universe_size: int = 30  # 코스피 + 코스닥 상위 시가총액 종목 수 (20~30)
    interval_sec: int = 10  # 시세 수집 주기(초)
    use_mock: bool = os.getenv("KIWOOM_USE_MOCK", "true").lower() in {"1", "true", "yes"}


settings = CollectorSettings()
