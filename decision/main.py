"""8503 AI 판단 레이어 진입점.

페이퍼 모드: 실제 매매는 하지 않고 판단 로그만 decisions 테이블에 쌓는다.
15분마다 유니버스(collector가 채운 prices 테이블 기준) 30종목을 순회하며
Claude에게 최근 1시간 시세 흐름 + 오늘자 신규 공시 + 보유 상태(현재는 전부
미보유로 고정)를 근거로 action/confidence/reason 판단을 요청한다.

매매 상한, 손실 제한 등 안전장치는 이 컴포넌트에 없다 — 다음 컴포넌트에서 만든다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict

from config.settings import decision_settings
from decision.claude_client import ClaudeClient
from decision.context_builder import build_context
from db.repository import get_connection, init_db, get_latest_universe, save_decision

logger = logging.getLogger("decision")

VALID_ACTIONS = {"buy", "sell", "hold"}

SYSTEM_PROMPT = """너는 한국 주식시장 개별 종목에 대한 매매 판단을 보조하는 애널리스트다.
사용자가 JSON으로 제공하는 데이터(최근 1시간 시세 흐름 price_history_1h, 오늘자 신규 공시
disclosures_today, 현재 보유 상태 holding)를 근거로 action(buy/sell/hold), confidence(0~100),
reason을 판단해라.

이 판단은 페이퍼 트레이딩(모의) 기록용이며 실제 주문은 실행되지 않는다.
reason은 판단 근거를 한국어로 2~3문장 이내로 간결하게 작성해라."""


def _validate(result: Dict[str, Any]) -> bool:
    if result.get("action") not in VALID_ACTIONS:
        return False
    confidence = result.get("confidence")
    if not isinstance(confidence, int) or not (0 <= confidence <= 100):
        return False
    if not isinstance(result.get("reason"), str) or not result["reason"].strip():
        return False
    return True


async def decide_one(client: ClaudeClient, conn: sqlite3.Connection, stk_cd: str, stk_nm: str) -> None:
    context = build_context(conn, stk_cd, stk_nm)
    user_content = json.dumps(context, ensure_ascii=False, indent=2)

    try:
        result = await client.decide(SYSTEM_PROMPT, user_content)
    except Exception:
        logger.exception("판단 실패: %s(%s)", stk_nm, stk_cd)
        return

    if not _validate(result):
        logger.warning("판단 결과 형식이 이상해서 저장하지 않음: %s(%s) -> %s", stk_nm, stk_cd, result)
        return

    decided_at = datetime.now(timezone.utc).isoformat()
    save_decision(
        conn,
        {
            "stk_cd": stk_cd,
            "decided_at": decided_at,
            "action": result["action"],
            "confidence": result["confidence"],
            "reason": result["reason"],
            "context_snapshot": json.dumps(context, ensure_ascii=False),
            "model": client.model,
        },
    )
    logger.info(
        "[%s] %s -> %s (confidence=%s): %s",
        stk_cd,
        stk_nm,
        result["action"],
        result["confidence"],
        result["reason"],
    )


async def collect_once(client: ClaudeClient, conn: sqlite3.Connection) -> int:
    universe = get_latest_universe(conn)
    if not universe:
        logger.warning("universe가 비어있습니다 (collector를 먼저 실행해야 합니다)")
        return 0

    for i, stock in enumerate(universe):
        await decide_one(client, conn, stock["stk_cd"], stock["stk_nm"])
        if i + 1 < len(universe):
            await asyncio.sleep(decision_settings.request_delay_sec)

    return len(universe)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    init_db(conn)

    client = ClaudeClient()
    try:
        while True:
            count = await collect_once(client, conn)
            logger.info("decision cycle done: %d stocks", count)
            await asyncio.sleep(decision_settings.decision_interval_sec)
    finally:
        await client.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
