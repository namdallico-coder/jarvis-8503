"""Kiwoom REST API 클라이언트.

인증(oauth2/token) 및 공통 요청(api-id/authorization 헤더)을 담당하며,
collector/universe.py(순위정보 조회)와 fetch_quotes(시세 조회)가 이 클라이언트를 공유한다.

스펙 출처: https://github.com/Kiwoom-Securities/Kiwoom-REST-API
  - kiwoom/core/auth.py   (토큰 발급: POST /oauth2/token)
  - kiwoom/core/client.py (공통 요청 헤더: api-id, authorization)

TODO:
  - 시세 조회 TR(api-id) 확정 후 fetch_quotes 구현
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

KST = timezone(timedelta(hours=9))
TOKEN_PATH = "/oauth2/token"


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
        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_access_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._token is not None
            and self._token_expires_at is not None
            and datetime.now(timezone.utc) < self._token_expires_at - timedelta(minutes=10)
        ):
            return self._token
        return await self._issue_token()

    async def _issue_token(self) -> str:
        if not self.app_key or not self.secret_key:
            raise QuoteClientError("KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 가 설정되지 않았습니다.")

        response = await self._client.post(
            TOKEN_PATH,
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "secretkey": self.secret_key,
            },
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )
        data = response.json()
        if response.status_code >= 400 or data.get("return_code") not in (None, 0):
            raise QuoteClientError(
                f"토큰 발급 실패: {data.get('return_msg')} (status={response.status_code}, "
                f"return_code={data.get('return_code')})"
            )

        token = data.get("token")
        expires_dt = data.get("expires_dt")
        if not token or not expires_dt:
            raise QuoteClientError(f"토큰 응답에 필요한 값이 없습니다: {data}")

        self._token = token
        self._token_expires_at = datetime.strptime(expires_dt, "%Y%m%d%H%M%S").replace(
            tzinfo=KST
        ).astimezone(timezone.utc)
        return self._token

    async def request(
        self,
        path: str,
        api_id: str,
        body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """공통 API 요청. 응답의 return_code(!=0)는 HTTP 200이어도 실패로 취급한다."""
        token = await self._get_access_token()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
            "authorization": f"Bearer {token}",
        }
        if extra_headers:
            headers.update(extra_headers)

        response = await self._client.post(path, json=body or {}, headers=headers)

        if response.status_code == 401:
            token = await self._get_access_token(force_refresh=True)
            headers["authorization"] = f"Bearer {token}"
            response = await self._client.post(path, json=body or {}, headers=headers)

        data = response.json()
        if response.status_code >= 400 or data.get("return_code") not in (None, 0):
            raise QuoteClientError(
                f"API 요청 실패 [{api_id}]: {data.get('return_msg')} "
                f"(status={response.status_code}, return_code={data.get('return_code')})"
            )
        return data

    async def fetch_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """종목코드 목록에 대한 현재가 시세를 조회한다."""
        raise NotImplementedError("시세 조회 API 연동은 아직 구현되지 않았습니다.")
