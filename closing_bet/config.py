"""종가배팅 설정. 전략 값(임계값 3.0, 상한가 29%, 유니버스 30)은 고정이며 수익률을 보고 조정하지 않는다.
rho(15:20 누적 거래대금 / 마감 누적 거래대금)만 잠정값 0.95이고, 10거래일 뒤 실측 중앙값(측정값만 근거)으로 1회 교체한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ClosingBetSettings:
    db_path: Path = ROOT / "db" / "closing_bet.db"
    research_db: Path = Path("/home/ubuntu/jarvis-field/research/daily.sqlite")
    holidays_file: Path = Path(__file__).resolve().parent / "holidays.txt"

    # 전략 (고정)
    universe_size: int = 30
    lookback_days: int = 20
    min_valid_days: int = 15
    ratio_threshold: float = 3.0
    limit_up_pct: float = 29.0

    # 15:20 -> 최종 거래대금 환산 계수 (잠정, 10거래일 뒤 실측 중앙값으로 1회 교체)
    rho: float = 0.95
    rho_review_after_days: int = 10

    # 비용 (%)
    cost_round_trip_pct: float = 0.33
    slip_each_side_pct: float = 0.10   # 편도 0.1% x 2 = 왕복 0.2%
    slip_total_pct: float = 0.10       # 왕복 합계 0.1%

    # 키움 호출 페이지 수 (100행/페이지)
    rank_pages_judge: int = 1
    rank_pages_final: int = 3
    ordinary_refresh_days: int = 7

    # 일정 (KST: 시, 분, 초)
    judge_at: tuple = (15, 20, 5)
    judge_deadline: tuple = (15, 25, 0)
    final_at: tuple = (15, 45, 0)
    exit_at: tuple = (9, 0, 20)
    exit_deadline: tuple = (15, 19, 0)   # 이 시각 전이면 늦게라도 시초가(당일 open_pric)로 청산 시도
    exit_retry_sec: int = 20
    seed_days: int = 25


settings = ClosingBetSettings()
