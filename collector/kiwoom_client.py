"""Kiwoom REST API 클라이언트.

인증(oauth2/token) 및 공통 요청(api-id/authorization 헤더)을 담당하며,
collector/universe.py(순위정보 조회)와 fetch_quotes(시세 조회)가 이 클라이언트를 공유한다.

스펙 출처: https://github.com/Kiwoom-Securities/Kiwoom-REST-API
  - kiwoom/core/auth.py   (토큰 발급: POST /oauth2/token)
  - kiwoom/core/client.py (공통 요청 헤더: api-id, authorization)
  - examples/국내주식/종목정보/get_domestic_stock_info.py (ka10001: 현재가/거래량 포함)
  - examples/국내주식/종목정보/list_domestic_stocks.py (ka10099: 시장구분별 종목 리스트)

종목코드는 거래소별 접미사를 그대로 사용한다 (KRX: 039490, NXT: 039490_NX, SOR/통합: 039490_AL).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

import httpx

from config.settings import settings
from collector.notifications import notify_persistent_failure

KST = timezone(timedelta(hours=9))
TOKEN_PATH = "/oauth2/token"
QUOTE_PATH = "/api/dostk/stkinfo"
QUOTE_API_ID = "ka10001"  # 주식기본정보요청
STOCK_LIST_API_ID = "ka10099"  # 종목정보 리스트
QUOTE_REQUEST_DELAY_SECONDS = 0.2  # 종목별 순차 조회 간 요청 간격

logger = logging.getLogger("collector.kiwoom_client")

T = TypeVar("T")


class QuoteClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        return_code: Optional[int] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.status_code = status_code


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

    async def _call_with_retry(self, call: Callable[[], Awaitable[T]], *, label: str) -> T:
        """call()이 재시도 가능한 QuoteClientError를 던지면 backoff 후 재시도한다.

        재시도 불가능한 에러(인증 실패, 파라미터 오류 등)는 즉시 그대로 올린다.
        transient_retry_timeout_sec을 넘도록 계속 실패하면 ERROR 로그 + 알림 훅을
        호출하고 마지막 예외를 그대로 던진다 (systemd Restart=always가 최후 안전망).
        """
        start = time.monotonic()
        attempt = 0
        while True:
            try:
                return await call()
            except QuoteClientError as exc:
                if exc.return_code not in settings.transient_retryable_return_codes:
                    raise

                elapsed = time.monotonic() - start
                if elapsed >= settings.transient_retry_timeout_sec:
                    message = (
                        f"{label}: 키움 API 일시 오류(return_code={exc.return_code})가 "
                        f"{elapsed:.0f}초 넘게 계속돼 재시도를 포기함: {exc}"
                    )
                    logger.error(message)
                    await notify_persistent_failure(message)
                    raise

                attempt += 1
                logger.warning(
                    "%s: 키움 API 일시 오류(return_code=%s), %d초 뒤 재시도"
                    " (%d번째 시도, 경과 %.0f초): %s",
                    label,
                    exc.return_code,
                    settings.transient_retry_interval_sec,
                    attempt,
                    elapsed,
                    exc,
                )
                await asyncio.sleep(settings.transient_retry_interval_sec)

    async def _get_access_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._token is not None
            and self._token_expires_at is not None
            and datetime.now(timezone.utc) < self._token_expires_at - timedelta(minutes=10)
        ):
            return self._token
        return await self._call_with_retry(self._issue_token_once, label="토큰 발급")

    async def _issue_token_once(self) -> str:
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
                f"return_code={data.get('return_code')})",
                return_code=data.get("return_code"),
                status_code=response.status_code,
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

    async def _post(
        self,
        path: str,
        api_id: str,
        body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """인증 헤더를 붙여 POST 하고, 401이면 토큰을 한 번 재발급해 재시도한다."""
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

        return response

    async def request(
        self,
        path: str,
        api_id: str,
        body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """공통 API 요청. 일시 오류(예: return_code=7)는 내부적으로 재시도한다."""
        return await self._call_with_retry(
            lambda: self._request_once(path, api_id, body, extra_headers),
            label=f"request[{api_id}]",
        )

    async def _request_once(
        self,
        path: str,
        api_id: str,
        body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """공통 API 요청 1회 시도. 응답의 return_code(!=0)는 HTTP 200이어도 실패로 취급한다."""
        response = await self._post(path, api_id, body, extra_headers)
        data = response.json()
        if response.status_code >= 400 or data.get("return_code") not in (None, 0):
            raise QuoteClientError(
                f"API 요청 실패 [{api_id}]: {data.get('return_msg')} "
                f"(status={response.status_code}, return_code={data.get('return_code')})",
                return_code=data.get("return_code"),
                status_code=response.status_code,
            )
        return data

    async def fetch_stock_list(self, mrkt_tp: str) -> List[Dict[str, Any]]:
        """시장구분별 전체 종목 리스트를 조회한다 (ka10099). 일시 오류는 내부적으로 재시도한다.

        mrkt_tp: 0=코스피, 10=코스닥, 8=ETF, 3=ELW, 6=리츠 등 (ETF/ETN/리츠/뮤추얼펀드는
        0/10과 분리된 별도 구분값이라, 0/10만 조회하면 일반 상장기업 종목만 걸러진다).
        """
        return await self._call_with_retry(
            lambda: self._fetch_stock_list_once(mrkt_tp),
            label=f"fetch_stock_list[mrkt_tp={mrkt_tp}]",
        )

    async def _fetch_stock_list_once(self, mrkt_tp: str) -> List[Dict[str, Any]]:
        """페이지네이션 전체를 처음부터 끝까지 1회 시도. 중간에 재시도 가능한 오류가 나면
        전체를 처음부터 다시 받는다 (실측상 ka10099는 한 페이지로 끝나서 비용이 작다).
        """
        rows: List[Dict[str, Any]] = []
        cont_yn: Optional[str] = None
        next_key: Optional[str] = None
        while True:
            extra_headers = {}
            if cont_yn:
                extra_headers["cont-yn"] = cont_yn
            if next_key:
                extra_headers["next-key"] = next_key

            response = await self._post(
                QUOTE_PATH,
                STOCK_LIST_API_ID,
                {"mrkt_tp": mrkt_tp},
                extra_headers or None,
            )
            data = response.json()
            if response.status_code >= 400 or data.get("return_code") not in (None, 0):
                raise QuoteClientError(
                    f"종목정보 리스트 조회 실패 [mrkt_tp={mrkt_tp}]: {data.get('return_msg')} "
                    f"(status={response.status_code}, return_code={data.get('return_code')})",
                    return_code=data.get("return_code"),
                    status_code=response.status_code,
                )
            rows.extend(data.get("list", []))

            cont_yn = response.headers.get("cont-yn")
            next_key = response.headers.get("next-key")
            if cont_yn != "Y":
                break

        return rows

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
