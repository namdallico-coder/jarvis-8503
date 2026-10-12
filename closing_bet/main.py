"""종가배팅 가상매매 서비스 진입점. 일정: 09:00:20 시초가 청산, 15:20:05 판정, 15:45 마감 처리.

페이퍼 전용: 주문을 내는 코드는 없다. 실행: python -m closing_bet.main  (서비스 등록은 별도 확인 후)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time

from closing_bet import store
from closing_bet.config import KST, settings
from closing_bet.jobs import DONE, run_exit, run_finalize, run_judge
from closing_bet.kiwoom_api import EX_CONFIG, SHARED_WARNING, KiwoomAPI, KiwoomConfigError

logger = logging.getLogger("closing_bet")
EXIT_SLOW_RETRY_SEC = 600  # 개장 직후 창(09:10) 이후에는 10분 간격으로만 재시도


def _t(parts) -> time:
    return time(*parts)


_api: KiwoomAPI | None = None


async def _with_api(fn, conn, now):
    """프로세스 수명 동안 키움 클라이언트(=토큰) 하나를 재사용한다. 작업마다 토큰을 새로 발급하면
    같은 앱키를 쓰는 collector의 토큰을 무효화할 수 있어서다."""
    global _api
    if _api is None:
        _api = KiwoomAPI()
    return await fn(conn, settings, _api, now)


def init_api(conn) -> KiwoomAPI:
    """키움 클라이언트를 만든다. 키 설정이 없으면 KiwoomConfigError. 공유 키 모드면 WARNING 로그와 cb_events 기록을 남긴다."""
    api = KiwoomAPI()
    if api.shared_key:
        store.log_event(conn, datetime.now(KST).strftime("%Y-%m-%d"), "shared_key_mode", SHARED_WARNING, logging.WARNING)
    return api


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    conn = store.connect(settings.db_path)
    global _api
    try:  # 키 설정이 없으면 시작하지 않는다 (키움 호출은 첫 작업 시각까지 없으므로 여기서 미리 검증)
        _api = init_api(conn)
    except KiwoomConfigError as e:
        logger.critical("closing_bet 시작 불가: %s", e)
        raise SystemExit(EX_CONFIG)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    try:  # 시작 시 토큰을 한 번 발급받아 키가 유효한지 확인한다 (실패해도 서비스는 유지, 작업 시각에 재시도)
        expires = await _api.verify_token()
        store.log_event(conn, today, "token_ok", f"토큰 발급 확인 (만료 {expires} KST)")
    except Exception as e:
        store.log_event(conn, today, "token_failed", f"시작 시 토큰 발급 실패: {type(e).__name__}: {str(e)[:160]}", logging.ERROR)
    last_exit_try = 0.0
    logger.info("closing_bet 시작 (페이퍼 전용), db=%s, rho=%s, threshold=%s", settings.db_path, settings.rho, settings.ratio_threshold)
    while True:
        now = datetime.now(KST)
        dt = now.strftime("%Y-%m-%d")
        t = now.time()
        try:
            if now.weekday() >= 5:
                if not store.job_status(conn, dt, "weekend"):
                    store.set_job(conn, dt, "weekend", "closed", "주말")
                    store.log_event(conn, dt, "weekend_skip", "주말 — 모든 작업 스킵")
            else:
                if _t(settings.exit_at) <= t < _t(settings.exit_deadline) and store.job_status(conn, dt, "exit") not in DONE:
                    gap = settings.exit_retry_sec if t < time(9, 10) else EXIT_SLOW_RETRY_SEC
                    if asyncio.get_event_loop().time() - last_exit_try >= gap:
                        last_exit_try = asyncio.get_event_loop().time()
                        await _with_api(run_exit, conn, now)
                elif t >= _t(settings.exit_deadline) and store.job_status(conn, dt, "exit") is None:
                    n_open = conn.execute("SELECT COUNT(*) FROM cb_trades WHERE status='open' AND dt_entry<?", (dt,)).fetchone()[0]
                    if n_open:
                        store.set_job(conn, dt, "exit", "failed", f"미청산 {n_open}건 (장중 시초가 확인 실패)")
                        store.log_event(conn, dt, "exit_failed", f"시초가 청산 실패: 미청산 {n_open}건", logging.ERROR)
                    else:
                        store.set_job(conn, dt, "exit", "no_positions", "")

                if _t(settings.judge_at) <= t < _t(settings.judge_deadline) and store.job_status(conn, dt, "judge") is None:
                    await _with_api(run_judge, conn, now)
                elif t >= _t(settings.judge_deadline) and store.job_status(conn, dt, "judge") is None:
                    store.set_job(conn, dt, "judge", "missed", "15:20~15:25 창에 서비스가 실행 중이 아니었음")
                    store.log_event(conn, dt, "judge_missed", "15:20 판정 시각을 놓침(서비스 중단)", logging.WARNING)

                if t >= _t(settings.final_at) and store.job_status(conn, dt, "finalize") is None:
                    await _with_api(run_finalize, conn, now)
        except Exception:
            logger.exception("작업 실행 중 오류 (5초 뒤 재시도 루프 계속)")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run())
