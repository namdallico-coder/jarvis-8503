"""Kiwoom REST API 클라이언트.

인증(oauth2/token) 및 공통 요청(api-id/authorization 헤더)을 담당하며,
collector/universe.py(순위정보 조회)와 fetch_quotes(시세 조회)가 이 클라이언트를 공유한다.

스펙 출처: https://github.com/Kiwoom-Securities/Kiwoom-REST-API
  - kiwoom/core/auth.py   (토큰 발급: POST /oauth2/token)
  - kiwoom/core/client.py (공통 요청 헤더: api-id, authorization)
  - examples/국내주식/종목정보/get_domestic_stock_info.py (ka10001: 현재가/거래량 포함)

종목코드는 거래소별 접미사를 그대로 사용한다 (KRX: 039490, NXT: 039490_NX, SOR/통합: 039490_AL).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

KST = timezone(timedelta(hours=9))
TOKEN_PATH = "/oauth2/token"
QUOTE_PATH = "/api/dostk/stkinfo"
QUOTE_API_ID = "ka10001"  # 주식기본정보요청
QUOTE_REQUEST_DELAY_SECONDS = 0.2  # 종목별 순차 조회 간 요청 간격


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
        """종목코드 목록에 대한 현재가/거래량을 순차 조회한다 (ka10001, 1건씩만 지원)."""
        quotes: List[Dict[str, Any]] = []
        for i, symbol in enumerate(symbols):
            data = await self.request(
                path=QUOTE_PATH,
                api_id=QUOTE_API_ID,
                body={"stk_cd": symbol},
            )
            quotes.append(
                {
                    "stk_cd": data.get("stk_cd") or symbol,
                    "stk_nm": data.get("stk_nm"),
                    "cur_prc": _parse_signed_int(data.get("cur_prc")),
                    "trde_qty": _parse_signed_int(data.get("trde_qty")),
                }
            )
            if i + 1 < len(symbols):
                await asyncio.sleep(QUOTE_REQUEST_DELAY_SECONDS)
        return quotes


def _parse_signed_int(value: Optional[str]) -> Optional[int]:
    """키움 현재가/거래량 필드는 실제 부호가 아니라 등락 방향 표시용 +/- 접두사가 붙어
    내려온다 (예: 하락 종목의 현재가도 '-106280'으로 내려옴). 절대값으로 정규화한다.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return abs(int(text))
