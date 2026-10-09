"""키움 REST 조회 어댑터. 조회 TR 3개만 쓴다 (주문 TR은 이 패키지에 없다).

  ka10032 거래대금상위 (/api/dostk/rkinfo)  — 15:20 판정 1콜, 15:45 마감 3콜
  ka10001 주식기본정보 (/api/dostk/stkinfo) — 다음날 시초가(open_pric) 조회
  ka10099 종목정보 리스트                    — 개별종목 화이트리스트 (주 1회)
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, TypeVar

from collector.kiwoom_client import KiwoomQuoteClient, QuoteClientError

logger = logging.getLogger("closing_bet")
T = TypeVar("T")
AUTH_INVALID = 8005  # "Token이 유효하지 않습니다" — 다른 프로세스가 같은 앱키로 토큰을 새로 발급하면 이전 토큰이 무효화될 수 있다

ALLOWED_API_IDS = {"ka10032", "ka10001", "ka10099"}

RANKING_PATH = "/api/dostk/rkinfo"
QUOTE_PATH = "/api/dostk/stkinfo"
# mrkt_tp 000=코스피+코스닥, stex_tp 1=KRX (일봉 거래대금과 값이 일치함을 확인), 관리종목 포함
RANKING_BODY = {"mrkt_tp": "000", "mang_stk_incls": "1", "stex_tp": "1"}


def _int(v: Any) -> int:
    return abs(int(str(v).replace(",", ""))) if v not in (None, "") else 0


def _float(v: Any) -> float:
    return float(str(v).replace(",", "").replace("+", "")) if v not in (None, "") else 0.0


class KiwoomAPI:
    """jobs.py가 기대하는 3개 메서드(ranking/quote/ordinary)를 구현한다. 테스트에서는 같은 메서드를 가진 가짜로 대체한다."""

    def __init__(self, client: KiwoomQuoteClient | None = None) -> None:
        self.client = client or KiwoomQuoteClient()

    async def close(self) -> None:
        await self.client.close()

    async def _auth_retry(self, fn: Callable[[], Awaitable[T]]) -> T:
        """토큰이 무효(8005)면 토큰을 다시 발급받아 한 번만 재시도한다 (collector의 _post는 401만 재시도한다)."""
        try:
            return await fn()
        except QuoteClientError as e:
            if e.return_code != AUTH_INVALID:
                raise
            logger.warning("키움 토큰 무효(8005) — 토큰 재발급 후 1회 재시도")
            await self.client._get_access_token(force_refresh=True)
            return await fn()

    async def ranking(self, pages: int) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        cont = nk = None
        for _ in range(pages):
            extra: Dict[str, str] = {}
            if cont:
                extra["cont-yn"] = cont
            if nk:
                extra["next-key"] = nk

            async def call():
                resp = await self.client._post(RANKING_PATH, "ka10032", RANKING_BODY, extra or None)
                data = resp.json()
                if resp.status_code >= 400 or data.get("return_code") not in (None, 0):
                    raise QuoteClientError(
                        f"ka10032 실패: {data.get('return_msg')}",
                        return_code=data.get("return_code"), status_code=resp.status_code)
                return data, resp.headers

            data, headers = await self._auth_retry(
                lambda: self.client._call_with_retry(call, label="closing_bet:ka10032"))
            for r in data.get("trde_prica_upper", []):
                rows.append({
                    "code": r["stk_cd"].split("_", 1)[0], "name": r.get("stk_nm"), "rank": _int(r.get("now_rank")),
                    "cur_prc": _int(r.get("cur_prc")), "flu_rt": _float(r.get("flu_rt")),
                    "qty": _int(r.get("now_trde_qty")), "amount": _int(r.get("trde_prica")),
                })
            cont, nk = headers.get("cont-yn"), headers.get("next-key")
            if cont != "Y":
                break
        return rows

    async def quote(self, code: str) -> Dict[str, Any]:
        d = await self._auth_retry(lambda: self.client.request(QUOTE_PATH, "ka10001", {"stk_cd": code}))
        return {"code": code, "open_pric": _int(d.get("open_pric")), "cur_prc": _int(d.get("cur_prc")),
                "qty": _int(d.get("trde_qty"))}

    async def ordinary(self) -> List[Dict[str, str]]:
        out = []
        for mrkt, mname in (("0", "KOSPI"), ("10", "KOSDAQ")):
            for r in await self._auth_retry(lambda m=mrkt: self.client.fetch_stock_list(m)):
                if r.get("marketCode") != mrkt or not r.get("code") or "스팩" in (r.get("name") or ""):
                    continue
                out.append({"code": r["code"], "name": r["name"], "market": mname})
        return out
