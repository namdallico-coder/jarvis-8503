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


@dataclass(frozen=True)
class DecisionSettings:
    decision_interval_sec: int = 15 * 60  # 15분마다 유니버스 전체 판단
    price_lookback_hours: int = 1  # 시세 흐름 조회 범위
    request_delay_sec: float = 1.0  # 종목별 Claude 호출 간 요청 간격 (레이트리밋 여유)


decision_settings = DecisionSettings()


@dataclass(frozen=True)
class SignalSettings:
    # collector 실측 수집 간격이 약 36초(설정 interval_sec=30초 + 30종목 순회 소요시간)라
    # 그 실측치를 근거로 잡음:
    #  - 5분 ≈ 샘플 8~9개, 20분 ≈ 샘플 33개 — 일봉 5일/20일 이동평균의 1:4 비율을
    #    장중 스케일로 그대로 가져옴.
    #  - 이보다 짧게(예: 1분) 잡으면 30~36초 틱 노이즈에 크로스가 남발되고,
    #    더 길게 잡으면 장중 세션(6.5시간) 동안 신호가 몇 번 안 떠서
    #    15분 주기 판단 레이어와 궁합이 나빠짐.
    short_window_min: int = 5
    long_window_min: int = 20
    min_samples: int = 3  # 창 안에 이보다 적으면 아직 판단하기엔 데이터 부족으로 보고 건너뜀
    check_interval_sec: int = 60  # 1분마다 크로스 여부 확인


signal_settings = SignalSettings()
