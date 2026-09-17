"""Kiwoom REST API 시세 조회 클라이언트 (스켈레톤).

app/adapters/kiwoom_kr.py 의 인증 방식(app-key/secret + api-id 헤더)을 따른다.

TODO:
  - 토큰 발급/캐싱 로직 연동
  - 시세 조회 TR(api-id) 확정 후 fetch_quotes 구현
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx


class QuoteClientError(Exception):
    pass


class KiwoomQuoteClient:
    REAL_BASE_URL = "https://api.kiwoom.com"
    MOCK_BASE_URL = "https://mockapi.kiwoom.com"

    def __init__(
        self,
        app_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        use_mock: Optional[bool] = None,
        timeout: float = 10.0,
    ) -> None:
        self.app_key = app_key or os.getenv("KIWOOM_APP_KEY")
        self.secret_key = secret_key or os.getenv("KIWOOM_SECRET_KEY")
        self.use_mock = (
            use_mock
            if use_mock is not None
            else os.getenv("KIWOOM_USE_MOCK", "true").lower() in {"1", "true", "yes"}
        )
        self.base_url = self.MOCK_BASE_URL if self.use_mock else self.REAL_BASE_URL
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(timeout))

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """종목코드 목록에 대한 현재가 시세를 조회한다."""
        raise NotImplementedError("시세 조회 API 연동은 아직 구현되지 않았습니다.")
