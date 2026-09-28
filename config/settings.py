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

    # 키움 API의 일시적 오류에 대한 재시도 정책.
    # 7  = "서비스를 처리하는 중에 오류가 발생했습니다" — 실측으로 거의 매일
    #      07:50~08:00 KST경(장 시작 전, 거래일/휴장일 무관 — 키움 쪽 일일 점검으로 추정)
    #      나타났다가 몇 분 안에 자연 복구되는 걸 확인해서 재시도 대상으로 등록했다.
    # 5  = "허용된 요청 개수를 초과하였습니다"(HTTP 429, 요청 유량 제한) — 재시도 로직
    #      검증 중 실제로 재현됨. fetch_quotes의 종목별 딜레이(0.2초=초당 5건)가
    #      ka10001에서 관측된 유량 한도(초당 5건)와 딱 맞닿아 있어, 지터만 있어도
    #      정상 운영 중에도 튈 수 있다 — 잠깐 기다리면 풀리는 오류라 재시도 대상.
    # 다른 일시적 코드가 더 발견되면 여기 추가.
    transient_retryable_return_codes: frozenset[int] = frozenset({7, 5})
    transient_retry_interval_sec: int = 30  # 일시 오류 시 재시도 간격
    transient_retry_timeout_sec: int = 10 * 60  # 이 시간 넘게 계속 실패하면 진짜 문제로 취급


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
    # 처음엔 일봉 5일/20일 비율을 그대로 가져와 5분/20분으로 잡았는데,
    # signals/backtest.py로 실측 데이터를 재생해보니 종목당 하루 평균 27.4건,
    # 크로스 간격 중앙값 6분(최소 1분)으로 whipsaw가 심했다. 10/40, 15/60 등으로
    # 스윕 비교한 결과 15분/60분이 종목당 평균 11.9건(총 358건, 재검증 완료)으로
    # 15분 주기 판단 레이어와 가장 궁합이 좋았음. 크로스 간격은 전체 유니버스
    # 기준 중앙값 32분/평균 43.2분 (특정 종목 하나만 보면 이보다 짧게 나올 수 있음).
    short_window_min: int = 15
    long_window_min: int = 60
    min_samples: int = 3  # 창 안에 이보다 적으면 아직 판단하기엔 데이터 부족으로 보고 건너뜀
    check_interval_sec: int = 60  # 1분마다 크로스 여부 확인

    # 쿨다운(같은 종목 연속 기록 최소 간격) + 재동기화 정책.
    # 09/28 백테스트: 쿨다운 없이 하루 250건(정규장만) -> 15분 쿨다운 191건.
    # 쿨다운만 쓰면 "쿨다운 중 억제된 뒤집힘 이후 다시 안 뒤집히면 DB의 마지막 기록이
    # 실제 상태와 영영 어긋나는" 위험이 있었음(052690_AL 실측 사례, 7% 종목에서 발생) —
    # 쿨다운 만료 시 실제 상태와 마지막 기록이 다르면 재동기화 신호를 1건 발행하는
    # 로직(signals/ma_signal.py의 decide_recording)으로 해결. signals.origin 컬럼에
    # immediate/resync로 구분 기록.
    cooldown_min: float = 15.0


signal_settings = SignalSettings()


@dataclass(frozen=True)
class RiskGateSettings:
    # 처음엔 1회 최대 5%로 잡았는데, 균등비중 가정(계좌를 max_concurrent_holdings개
    # 슬롯으로 균등분할)과 모순이었다 — 5종목 동시보유면 슬롯당 20%인데 상한이 5%라
    # 매수가 항상 거부되는 버그 아닌 버그. max_concurrent_holdings=5 기준
    # 100/5=20%로 맞춤.
    max_position_pct: float = 20.0  # 1회 매매 최대 비중 (계좌 총액 대비 %)
    max_concurrent_holdings: int = 5  # 동시 보유 종목 수 제한
    daily_loss_limit_pct: float = -3.0  # 일일 최대 손실 상한 (%, 도달 시 신규 매수 차단)
    max_averaging_count: int = 2  # 동일 종목 물타기(추가매수) 최대 횟수

    # 아직 실계좌 연동이 없어서 균등비중 가정으로 근사한다: 계좌를
    # max_concurrent_holdings개 슬롯으로 나눠 각 슬롯이 이 총액의 1/N을 담당한다고 본다.
    # TODO: 실제 계좌 연동 후 실 잔고로 교체.
    paper_account_total_value: float = 10_000_000.0  # 페이퍼 계좌 총액 가정치 (원)

    check_interval_sec: int = 60  # decisions에서 미검토 buy/sell 확인 주기


risk_gate_settings = RiskGateSettings()
