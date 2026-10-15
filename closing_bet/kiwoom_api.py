"""키움 REST 조회 어댑터. 조회 TR 3개만 쓴다 (주문 TR은 이 패키지에 없다).

  ka10032 거래대금상위 (/api/dostk/rkinfo)  — 15:20 판정 1콜, 15:45 마감 3콜
  ka10001 주식기본정보 (/api/dostk/stkinfo) — 다음날 시초가(open_pric) 조회
  ka10099 종목정보 리스트                    — 개별종목 화이트리스트 (주 1회)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, TypeVar

from closing_bet.config import KST
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


ENV_APP_KEY = "CB_KIWOOM_APP_KEY"
ENV_SECRET_KEY = "CB_KIWOOM_SECRET_KEY"
ENV_ALLOW_SHARED = "CB_ALLOW_SHARED_KEY"
SHARED_WARNING = "공유 키 모드: collector와 토큰 충돌 가능"
EX_CONFIG = 78  # sysexits EX_CONFIG: 설정 오류 — systemd RestartPreventExitStatus로 재시작 루프를 막는다


class KiwoomConfigError(RuntimeError):
    """사용할 앱키를 정할 수 없을 때 (전용 키 미설정 + 공유 키 모드 미허용, 전용 키가 collector와 같음 등)."""


def resolve_keys() -> tuple[str, str, bool]:
    """(app_key, secret_key, shared) — 값은 어떤 메시지에도 싣지 않는다.

    1) CB_KIWOOM_APP_KEY/CB_KIWOOM_SECRET_KEY(전용 키)가 있으면 그것을 쓴다. 둘 다 있어야 하고 collector 키와 달라야 한다.
    2) 전용 키가 없으면 CB_ALLOW_SHARED_KEY=1 로 명시적으로 허용했을 때만 collector의 KIWOOM_APP_KEY/SECRET_KEY를 쓴다
       (허용 IP당 앱키가 하나뿐이라 분리가 불가능한 경우). 같은 앱키를 두 프로세스가 쓰면 한쪽이 토큰을 새로 발급할 때
       다른 쪽 토큰이 무효화될 수 있다(8005).
    3) 그 외에는 시작을 거부한다 — 조용한 폴백 금지.
    """
    app = (os.environ.get(ENV_APP_KEY) or "").strip()
    sec = (os.environ.get(ENV_SECRET_KEY) or "").strip()
    c_app = (os.environ.get("KIWOOM_APP_KEY") or "").strip()
    c_sec = (os.environ.get("KIWOOM_SECRET_KEY") or "").strip()
    if app or sec:
        missing = [n for n, v in ((ENV_APP_KEY, app), (ENV_SECRET_KEY, sec)) if not v]
        if missing:
            raise KiwoomConfigError(f"closing_bet 전용 키움 앱키 환경변수가 불완전합니다: 없는 항목 {', '.join(missing)}.")
        if app == c_app:
            raise KiwoomConfigError(
                f"{ENV_APP_KEY}가 collector의 KIWOOM_APP_KEY와 같은 값입니다. 별도로 발급한 앱키를 넣거나, "
                f"공유가 의도라면 {ENV_APP_KEY}/{ENV_SECRET_KEY}를 지우고 {ENV_ALLOW_SHARED}=1 을 설정하세요.")
        return app, sec, False
    if (os.environ.get(ENV_ALLOW_SHARED) or "").strip() == "1":
        if not c_app or not c_sec:
            raise KiwoomConfigError(f"{ENV_ALLOW_SHARED}=1 이지만 KIWOOM_APP_KEY/KIWOOM_SECRET_KEY가 없습니다.")
        return c_app, c_sec, True
    raise KiwoomConfigError(
        f"closing_bet 키움 앱키 설정이 없습니다: {ENV_APP_KEY}/{ENV_SECRET_KEY}를 설정하거나, collector와 같은 키를 쓰는 것을 "
        f"감수한다면 {ENV_ALLOW_SHARED}=1 을 명시하세요 (조용히 collector 키로 폴백하지 않습니다 — 같은 키를 쓰면 토큰이 서로 무효화될 수 있습니다)."
    )


class _LoggedClient(KiwoomQuoteClient):
    """토큰 발급(oauth2/token)에 성공할 때마다 INFO 로그를 남기는 closing-bet 전용 클라이언트.
    출력하는 것은 이벤트 결과와 만료 시각뿐이며 토큰/앱키/시크릿 값은 절대 출력하지 않는다.
    이전 만료 시각과 같으면 키움이 기존 토큰을 그대로 돌려준 것이다(새 토큰이면 만료 시각이 바뀐다)."""

    async def _issue_token_once(self) -> str:
        previous = self._token_expires_at
        token = await super()._issue_token_once()
        fmt = lambda d: d.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S") if d else "없음"
        logger.info("토큰 발급 성공 — 만료 %s KST (이전 만료 %s)", fmt(self._token_expires_at), fmt(previous))
        return token


class KiwoomAPI:
    """jobs.py가 기대하는 3개 메서드(ranking/quote/ordinary)를 구현한다. 테스트에서는 같은 메서드를 가진 가짜로 대체한다."""

    def __init__(self, client: KiwoomQuoteClient | None = None) -> None:
        self.shared_key = False
        if client is None:
            app, sec, self.shared_key = resolve_keys()
            client = _LoggedClient(app_key=app, secret_key=sec)
        self.client = client

    async def close(self) -> None:
        await self.client.close()

    async def verify_token(self) -> str:
        """토큰이 없으면 발급받고 만료 시각(KST 문자열)을 돌려준다. 토큰 값은 노출하지 않는다."""
        await self.client._get_access_token()
        exp = self.client._token_expires_at
        return exp.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S") if exp else "알 수 없음"

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
