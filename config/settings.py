from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CollectorSettings:
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ")
    universe_size: int = 30  # 코스피 + 코스닥 상위 시가총액 종목 수 (20~30)
    # fetch_quotes 실측: 종목 30개 기준 약 6.1초(종목당 0.2초 딜레이가 지배적).
    # 그 5배가량 여유를 두고 30초로 설정.
    interval_sec: int = 30  # 시세 수집 주기(초)
    use_mock: bool = os.getenv("KIWOOM_USE_MOCK", "true").lower() in {"1", "true", "yes"}


settings = CollectorSettings()


@dataclass(frozen=True)
class NewsSettings:
    disclosure_check_interval_sec: int = 300  # 5분마다 신규 공시 확인
    corp_code_refresh_interval_sec: int = 24 * 60 * 60  # 고유번호 매핑 24시간마다 갱신
    request_delay_sec: float = 0.2  # 종목별 순차 조회 간 요청 간격


news_settings = NewsSettings()
