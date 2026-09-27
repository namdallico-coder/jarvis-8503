"""장애 알림 훅.

지금은 ERROR 로그만 남긴다. TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 환경변수가
설정되면(예: 8500의 텔레그램 봇 토큰을 재사용) 자동으로 텔레그램 메시지도
보낸다 — 이 파일은 그때도 코드 변경 없이 그대로 쓸 수 있게 만든 것이다.

알림 전송 자체가 실패해도(네트워크 문제 등) 절대 예외를 밖으로 던지지 않는다 —
알림 실패 때문에 collector가 또 죽으면 본말전도이기 때문이다.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("collector.notifications")

TELEGRAM_API_BASE = "https://api.telegram.org"


async def notify_persistent_failure(message: str) -> None:
    """일시 오류 재시도가 타임아웃될 만큼(예: 10분) 계속 실패했을 때 호출한다."""
    logger.error("지속 장애 알림: %s", message)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return  # 아직 텔레그램 연동 전 — 로그만 남기고 끝

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await client.post(
                f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": f"[8503 collector] {message}"},
            )
    except Exception:
        logger.exception("텔레그램 알림 전송 실패 (원래 장애와는 별개)")
