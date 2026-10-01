"""Claude API 클라이언트 (Anthropic Messages API).

인증은 x-api-key 헤더 하나뿐이라(OAuth 없음) kiwoom_client.py보다 단순하다.
API 키 발급: https://console.anthropic.com → API Keys → Create Key
(DART/키움과 달리 유료 API — 결제수단 등록 필요)

JSON 강제 방법: "JSON만 출력해줘" 프롬프트 대신 tool_choice로 스키마를 강제하는
tool-use 방식을 쓴다. 모델이 자연어 문장 사이에 JSON을 끼워넣거나 마크다운
코드펜스를 섞어서 파싱이 깨지는 실패 모드를 원천 차단하고, 성공 시 이미
구조화된 dict(tool의 input)를 그대로 받는다.

역할: AI가 처음부터 매매 판단을 만들어내지 않는다. signals/가 먼저 계산한
크로스 신호를 decision/main.py가 넘겨주면, 그 신호를 "따를지(follow) 보류할지
(hold)"만 검토한다 — 최종 buy/sell/hold는 follow 여부와 신호 방향을 조합해
decision/main.py가 결정한다.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

BASE_URL = "https://api.anthropic.com"
MESSAGES_PATH = "/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"

# $/1M 토큰 (input, output). 2026-09 기준. 모델 바뀌면 같이 갱신 필요.
PRICING_PER_MTOK = {
    "claude-sonnet-5": (2.00, 10.00),
}


def estimate_cost_usd(model: str, usage: Dict[str, Any]) -> Optional[float]:
    """usage(response.usage)와 모델명으로 호출 1건의 비용을 추정한다. 가격표에 없는
    모델이면 None (추측해서 잘못된 비용을 보여주는 것보다 모른다고 하는 게 낫다)."""
    pricing = PRICING_PER_MTOK.get(model)
    if pricing is None:
        return None
    input_rate, output_rate = pricing
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000

SIGNAL_REVIEW_TOOL = {
    "name": "signal_review",
    "description": "이동평균 크로스 신호 1건을 검토해 그 신호를 따를지(follow) 보류할지(hold)를 반환한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["follow", "hold"],
                "description": "follow: 신호(골든/데드크로스) 방향을 따른다. hold: 신호를 무시하고 보류한다.",
            },
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": {"type": "string", "description": "판단 근거 (한국어, 2~3문장)"},
        },
        "required": ["action", "confidence", "reason"],
    },
}


class ClaudeClientError(Exception):
    pass


class ClaudeClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        # os.getenv(..., default)는 변수가 "설정 안 됨"일 때만 default를 쓴다.
        # .env에 CLAUDE_DECISION_MODEL= (빈 문자열)로 존재하는 경우도 default로 처리하려고
        # or 체인으로 한 번 더 감싼다.
        self.model = model or os.getenv("CLAUDE_DECISION_MODEL") or DEFAULT_MODEL
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=httpx.Timeout(timeout))
        self.last_usage: Optional[Dict[str, Any]] = None  # 마지막 호출의 response.usage

    async def close(self) -> None:
        await self._client.aclose()

    async def review_signal(self, system_prompt: str, user_content: str) -> Dict[str, Any]:
        """user_content(신호+컨텍스트 JSON 문자열)를 근거로 follow/hold 판단 dict를 받는다."""
        if not self.api_key:
            raise ClaudeClientError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")

        response = await self._client.post(
            MESSAGES_PATH,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
                "tools": [SIGNAL_REVIEW_TOOL],
                "tool_choice": {"type": "tool", "name": "signal_review"},
            },
        )
        data = response.json()
        if response.status_code >= 400:
            error = data.get("error", {})
            raise ClaudeClientError(
                f"Claude API 요청 실패: {error.get('message')} "
                f"(status={response.status_code}, type={error.get('type')})"
            )

        self.last_usage = data.get("usage")

        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "signal_review":
                return block["input"]

        raise ClaudeClientError(f"tool_use 응답을 찾지 못했습니다: {data}")
