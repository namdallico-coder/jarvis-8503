"""거래일 판별: (1) 주말/공휴일 파일(holidays.txt) 정적 판별 + (2) 키움 응답 정체 감지.

휴장일(평일 공휴일 포함)에는 키움 순위 응답이 직전 거래일 마감값 그대로 돌아온다. 그래서 순위 상위 10개
(코드:거래대금:누적거래량) 시그니처가 이전 일자의 스냅샷과 완전히 같으면 휴장으로 본다.
판정 근거는 매번 cb_events와 로그에 남긴다 (휴장일 직후 첫 장에서 감지가 실제로 동작했는지 확인용).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from closing_bet import store
from closing_bet.config import ClosingBetSettings


def load_holidays(path: Path) -> Set[str]:
    if not Path(path).exists():
        return set()
    out = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.add(s)
    return out


def static_closed(cfg: ClosingBetSettings, d: date) -> Tuple[bool, str]:
    if d.weekday() >= 5:
        return True, f"주말({'월화수목금토일'[d.weekday()]})"
    if d.isoformat() in load_holidays(cfg.holidays_file):
        return True, "holidays.txt 등록 공휴일"
    return False, ""


def signature(rows: List[Dict[str, Any]], n: int = 10) -> str:
    return "|".join(f"{r['code']}:{r['amount']}:{r['qty']}" for r in rows[:n])


def detect_closed(conn, dt: str, rows: List[Dict[str, Any]], phase: str) -> Tuple[bool, str]:
    """순위 응답 정체 여부. 반환: (휴장 여부, 근거 문구)."""
    if not rows:
        return True, "순위 응답이 비어 있음"
    sig = signature(rows)
    prev = store.previous_snapshots(conn, dt)
    for p in prev:
        if p["signature"] == sig:
            return True, f"{phase} 스냅샷(top10)이 {p['dt']} {p['phase']} 스냅샷과 완전히 동일 → 장이 열리지 않음"
    if prev:
        return False, f"{phase} 스냅샷이 직전 {prev[0]['dt']} {prev[0]['phase']} 스냅샷과 다름 → 정상 거래일"
    return False, f"{phase} 비교할 이전 스냅샷 없음(첫 실행) → 거래일로 간주"


def log_post_holiday(conn, dt: str) -> None:
    """직전 거래 기록일과 오늘 사이에 이력이 없는 평일이 있으면 '휴장 직후 첫 장'으로 로그를 남긴다."""
    hist = [d for d in store.history_dates(conn) if d < dt]
    if not hist:
        return
    last = datetime.strptime(hist[-1], "%Y-%m-%d").date()
    today = datetime.strptime(dt, "%Y-%m-%d").date()
    gap = []
    d = last + timedelta(days=1)
    while d < today:
        if d.weekday() < 5 and d.isoformat() not in set(hist):
            gap.append(d.isoformat())
        d += timedelta(days=1)
    if gap:
        store.log_event(
            conn, dt, "post_holiday_first_session",
            f"휴장일 이후 첫 거래일 확인: 직전 거래일 {last}, 그 사이 거래 기록 없는 평일 {gap} → 오늘 스냅샷이 달라 거래일로 판정함",
            logging.WARNING)
