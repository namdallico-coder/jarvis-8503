"""8503 AI 판단 레이어 진입점 (신호 리뷰 방식).

AI가 처음부터 매매 판단을 만들어내지 않는다. signals/가 먼저 계산해둔 이동평균
크로스 신호(golden_cross/dead_cross)를 가져와서, 그 신호를 "따를지(follow)
보류할지(hold)"만 Claude에게 검토시킨다. 최근 decision_interval_sec(기본 15분)
안에 신호가 없던 종목은 AI 호출 자체를 하지 않는다 (비용 절감).

follow로 판단되면 신호 방향(golden_cross->buy, dead_cross->sell)을 최종 action으로,
hold면 action=hold로 decisions 테이블에 저장한다. 페이퍼 모드 — 실제 매매는 없다.

AI 호출을 아끼는 규칙 세 가지:
  - 장외(session=extended: NXT/프리마켓 등) 신호는 호출하지 않는다 — 정규장 신호만 검토한다.
    장외 신호는 signals 테이블에 태그된 채로 남아 있고, 스킵 건수만 로그로 남긴다.
  - 미보유 종목의 dead_cross는 호출하지 않는다 — 팔 포지션이 없어서 sell이 나와도
    risk_gate의 position_exists에서 반드시 거부되기 때문이다 (스킵 건수는 로그로만 남김).
  - 이미 decisions에 리뷰 기록이 있는 신호는 다시 호출하지 않는다 — 서비스를 재시작하면
    직전 15분 창이 겹쳐서 같은 신호를 또 리뷰하고, 그 중복 buy가 risk_gate를 두 번
    통과하면 페이퍼 원장이 왜곡되기 때문이다.

매매 상한, 손실 제한 등 안전장치는 이 컴포넌트에 없다 — risk_gate가 맡는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from config.settings import decision_settings
from decision.claude_client import ClaudeClient
from decision.context_builder import build_context
from db.repository import (
    get_connection,
    init_db,
    get_latest_universe,
    get_latest_signals_since,
    get_reviewed_signal_ids,
    save_decision,
)
from risk_gate.portfolio import is_held

logger = logging.getLogger("decision")

VALID_AI_ACTIONS = {"follow", "hold"}

SIGNAL_TO_FOLLOW_ACTION = {
    "golden_cross": "buy",
    "dead_cross": "sell",
}

SYSTEM_PROMPT = """너는 한국 주식시장 개별 종목의 이동평균 크로스 신호를 검토하는 애널리스트다.
코드가 이미 계산한 크로스 신호(signal: golden_cross 또는 dead_cross, 발생 시점의 단기/장기
이동평균과 가격 포함) 1건이 주어진다. 이 신호를 최근 1시간 시세 흐름(price_history_1h),
오늘자 신규 공시(disclosures_today), 현재 보유 상태(holding)와 함께 검토해서, 신호 방향을
따를지(follow) 보류할지(hold)를 판단해라.

새로운 매매 아이디어를 스스로 만들어내지 마라 — 이미 뜬 신호를 그대로 따를지, 아니면
반박하는 근거(예: 공시 악재, 신호 직후 추세 반전 조짐)가 있어 보류할지만 판단해라.

holding은 모의 포트폴리오의 실제 보유 상태다. held=false면 미보유이고, held=true면 평단가
(entry_price)와 지금까지의 추가매수 횟수(averaging_count)가 함께 온다. 신호를 따르면(follow)
미보유 종목의 golden_cross는 신규 매수, 보유 종목의 golden_cross는 추가매수(물타기),
보유 종목의 dead_cross는 보유 포지션 매도(청산)가 된다. 미보유 종목의 dead_cross는 팔 포지션이
없어서 애초에 검토 대상으로 오지 않는다. 보유 종목이면 평단가 대비 현재 가격(손익)도 함께 고려해라.

이 판단은 페이퍼 트레이딩(모의) 기록용이며 실제 주문은 실행되지 않는다.
reason은 판단 근거를 한국어로 2~3문장 이내로 간결하게 작성해라."""


def _validate(result: Dict[str, Any]) -> bool:
    if result.get("action") not in VALID_AI_ACTIONS:
        return False
    confidence = result.get("confidence")
    if not isinstance(confidence, int) or not (0 <= confidence <= 100):
        return False
    if not isinstance(result.get("reason"), str) or not result["reason"].strip():
        return False
    return True


def _final_action(ai_action: str, signal_type: str) -> str:
    """follow면 신호 방향(buy/sell)으로, hold면 그대로 hold로 확정한다."""
    if ai_action == "hold":
        return "hold"
    return SIGNAL_TO_FOLLOW_ACTION[signal_type]


async def review_one(
    client: ClaudeClient,
    conn: sqlite3.Connection,
    stk_cd: str,
    stk_nm: str,
    signal: Dict[str, Any],
    as_of: datetime | None = None,
) -> None:
    """as_of: 옛 신호를 재검토할 때 "최근 1시간"을 그 시점 기준으로 계산하려고 쓴다
    (실시간 운영에서는 생략 — build_context()와 동일한 용도)."""
    context = build_context(conn, stk_cd, stk_nm, signal, as_of=as_of)
    user_content = json.dumps(context, ensure_ascii=False, indent=2)

    try:
        result = await client.review_signal(SYSTEM_PROMPT, user_content)
    except Exception:
        logger.exception("신호 리뷰 실패: %s(%s) signal_id=%s", stk_nm, stk_cd, signal["id"])
        return

    if not _validate(result):
        logger.warning(
            "리뷰 결과 형식이 이상해서 저장하지 않음: %s(%s) -> %s", stk_nm, stk_cd, result
        )
        return

    final_action = _final_action(result["action"], signal["signal_type"])
    decided_at = datetime.now(timezone.utc).isoformat()
    save_decision(
        conn,
        {
            "stk_cd": stk_cd,
            "signal_id": signal["id"],
            "signal_type": signal["signal_type"],
            "decided_at": decided_at,
            "action": final_action,
            "confidence": result["confidence"],
            "reason": result["reason"],
            "context_snapshot": json.dumps(context, ensure_ascii=False),
            "model": client.model,
        },
    )
    logger.info(
        "[%s] %s: %s -> AI %s(confidence=%s) -> 최종 %s : %s",
        stk_cd,
        stk_nm,
        signal["signal_type"],
        result["action"],
        result["confidence"],
        final_action,
        result["reason"],
    )


def is_actionable(conn: sqlite3.Connection, signal: Dict[str, Any], as_of: datetime | None = None) -> bool:
    """AI에게 물어볼 가치가 있는 신호인지. 미보유 종목의 dead_cross는 follow(=sell)가 나와도
    risk_gate의 position_exists에서 반드시 거부되므로 물어볼 필요가 없다."""
    if signal["signal_type"] == "dead_cross" and not is_held(conn, signal["stk_cd"], as_of):
        return False
    return True


async def collect_once(client: ClaudeClient, conn: sqlite3.Connection) -> tuple[int, int, int]:
    """반환: (AI로 리뷰한 건수, 미보유 dead_cross라 스킵한 건수, 장외 신호라 스킵한 건수)."""
    since = (
        datetime.now(timezone.utc) - timedelta(seconds=decision_settings.decision_interval_sec)
    ).isoformat()
    latest = get_latest_signals_since(conn, since, session="regular")
    extended_skipped = [
        s for s in get_latest_signals_since(conn, since, session="extended")
    ]
    if extended_skipped:
        logger.info(
            "장외(extended) 신호 %d건은 정규장 신호만 검토하는 규칙에 따라 AI 호출 스킵: %s",
            len(extended_skipped),
            ", ".join(s["stk_cd"] for s in extended_skipped),
        )
    if not latest:
        logger.info("최근 %d초 안에 정규장 신호 없음 — AI 호출 없이 넘어감", decision_settings.decision_interval_sec)
        return 0, 0, len(extended_skipped)

    reviewed_ids = get_reviewed_signal_ids(conn, [s["id"] for s in latest])
    already = [s for s in latest if s["id"] in reviewed_ids]
    if already:
        logger.info("이미 리뷰한 신호 %d건은 중복이라 건너뜀", len(already))

    targets, skipped = [], []
    for signal in latest:
        if signal["id"] in reviewed_ids:
            continue
        (targets if is_actionable(conn, signal) else skipped).append(signal)

    if skipped:
        logger.info(
            "미보유 종목 dead_cross %d건은 매도할 포지션이 없어 AI 호출 스킵: %s",
            len(skipped),
            ", ".join(s["stk_cd"] for s in skipped),
        )

    name_by_code = {s["stk_cd"]: s["stk_nm"] for s in get_latest_universe(conn)}

    for i, signal in enumerate(targets):
        stk_cd = signal["stk_cd"]
        stk_nm = name_by_code.get(stk_cd, stk_cd)
        await review_one(client, conn, stk_cd, stk_nm, signal)
        if i + 1 < len(targets):
            await asyncio.sleep(decision_settings.request_delay_sec)

    return len(targets), len(skipped), len(extended_skipped)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    init_db(conn)

    client = ClaudeClient()
    try:
        while True:
            reviewed, skipped, ext_skipped = await collect_once(client, conn)
            logger.info(
                "decision cycle done: %d signals reviewed, %d skipped(미보유 dead_cross), %d skipped(장외)",
                reviewed,
                skipped,
                ext_skipped,
            )
            await asyncio.sleep(decision_settings.decision_interval_sec)
    finally:
        await client.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
