"""collector 안전수정 검증: (A) 토큰 무효(HTTP 200 + return_code=3 + 8005)를 기존 401처럼 1회만 재발급 후 재시도,
(B) collect_once()가 cur_prc/trde_qty가 None인 시세만 건너뛰고(0은 정상 값) 프로세스를 죽이지 않는지.
실행: PYTHONPATH=/home/ubuntu/jarvis-field/8503 venv/bin/python -m unittest collector.tests.test_kiwoom_client_auth"""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import httpx

from collector.kiwoom_client import KiwoomQuoteClient, QuoteClientError
from collector.main import collect_once
from db.repository import init_db

KST = timezone(timedelta(hours=9))
TOKEN_INVALID = {"return_code": 3, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"}
OK_QUOTE = {"return_code": 0, "return_msg": "정상", "stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": "-263000", "trde_qty": "21259056"}


class FakeKiwoom:
    """키움 서버를 흉내 낸다: /oauth2/token 호출마다 T1, T2, ... 발급, 그 외 호출은 주어진 응답 목록을 순서대로(마지막 값 반복) 돌려준다."""

    def __init__(self, api_responses):
        self.api_responses = api_responses
        self.api_auth_headers = []
        self.token_posts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            self.token_posts += 1
            exp = (datetime.now(KST) + timedelta(hours=24)).strftime("%Y%m%d%H%M%S")
            return httpx.Response(200, json={"token": f"T{self.token_posts}", "expires_dt": exp, "return_code": 0})
        self.api_auth_headers.append(request.headers.get("authorization"))
        status, body = self.api_responses[min(len(self.api_auth_headers) - 1, len(self.api_responses) - 1)]
        return httpx.Response(status, json=body)


async def make_client(fake: FakeKiwoom) -> KiwoomQuoteClient:
    client = KiwoomQuoteClient(app_key="test-app", secret_key="test-secret", use_mock=True)
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url=client.base_url, transport=httpx.MockTransport(fake.handler))
    return client


class TokenInvalidRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_8005_reissues_token_and_retries_once(self):
        fake = FakeKiwoom([(200, TOKEN_INVALID), (200, OK_QUOTE)])
        client = await make_client(fake)
        data = await client.request("/api/dostk/stkinfo", "ka10001", {"stk_cd": "005930"})
        self.assertEqual(data["cur_prc"], "-263000")
        self.assertEqual(len(fake.api_auth_headers), 2)                       # 원 요청 + 재시도 1회
        self.assertEqual(fake.token_posts, 2)                                   # 최초 발급 + 재발급 1회
        self.assertEqual(fake.api_auth_headers, ["Bearer T1", "Bearer T2"])     # 재시도는 새 토큰으로
        await client.close()

    async def test_8005_after_retry_raises_without_infinite_loop(self):
        fake = FakeKiwoom([(200, TOKEN_INVALID)])                               # 계속 8005
        client = await make_client(fake)
        with self.assertRaises(QuoteClientError) as cm:
            await client.request("/api/dostk/stkinfo", "ka10001", {"stk_cd": "005930"})
        self.assertEqual(cm.exception.return_code, 3)
        self.assertEqual(len(fake.api_auth_headers), 2)                         # 원 요청 + 재시도 1회, 그 이상 없음
        self.assertEqual(fake.token_posts, 2)
        await client.close()

    async def test_return_code_3_without_8005_is_not_retried(self):
        fake = FakeKiwoom([(200, {"return_code": 3, "return_msg": "다른 사유의 실패"})])
        client = await make_client(fake)
        with self.assertRaises(QuoteClientError):
            await client.request("/api/dostk/stkinfo", "ka10001", {"stk_cd": "005930"})
        self.assertEqual((len(fake.api_auth_headers), fake.token_posts), (1, 1))
        await client.close()

    async def test_existing_401_behavior_retained(self):
        fake = FakeKiwoom([(401, {"return_msg": "unauthorized"}), (200, OK_QUOTE)])
        client = await make_client(fake)
        data = await client.request("/api/dostk/stkinfo", "ka10001", {"stk_cd": "005930"})
        self.assertEqual(data["stk_cd"], "005930")
        self.assertEqual((len(fake.api_auth_headers), fake.token_posts), (2, 2))
        await client.close()

    async def test_401_twice_still_only_one_retry(self):
        fake = FakeKiwoom([(401, {"return_msg": "unauthorized"})])
        client = await make_client(fake)
        with self.assertRaises(QuoteClientError):
            await client.request("/api/dostk/stkinfo", "ka10001", {"stk_cd": "005930"})
        self.assertEqual((len(fake.api_auth_headers), fake.token_posts), (2, 2))
        await client.close()

    async def test_normal_response_unchanged(self):
        fake = FakeKiwoom([(200, OK_QUOTE)])
        client = await make_client(fake)
        data = await client.request("/api/dostk/stkinfo", "ka10001", {"stk_cd": "005930"})
        self.assertEqual(data["trde_qty"], "21259056")
        self.assertEqual((len(fake.api_auth_headers), fake.token_posts), (1, 1))
        await client.close()

    async def test_fetch_quotes_still_returns_none_for_blank_fields(self):
        """fetch_quotes()의 기존 동작(빈 값 -> None)은 그대로여야 한다. 스킵은 collect_once()가 담당한다."""
        blank = dict(OK_QUOTE, cur_prc="")
        fake = FakeKiwoom([(200, blank)])
        client = await make_client(fake)
        quotes = await client.fetch_quotes(["005930"])
        self.assertEqual(quotes, [{"stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": None, "trde_qty": 21259056}])
        await client.close()


class FakeQuoteClient:
    def __init__(self, quotes):
        self.quotes = quotes

    async def fetch_quotes(self, symbols):
        return self.quotes


def q(code, cur, qty):
    return {"stk_cd": code, "stk_nm": code, "cur_prc": cur, "trde_qty": qty}


class CollectOnceSkipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def saved(self):
        return {r[0]: (r[1], r[2]) for r in self.conn.execute("SELECT stk_cd, cur_prc, trde_qty FROM prices")}

    async def test_none_cur_prc_is_skipped_and_logged(self):
        client = FakeQuoteClient([q("A", 100, 5), q("B", None, 7), q("C", 300, 9)])
        with self.assertLogs("collector", level="WARNING") as cm:
            await collect_once(client, ["A", "B", "C"], self.conn)
        self.assertEqual(self.saved(), {"A": (100, 5), "C": (300, 9)})
        self.assertTrue(any("1건" in m and "B" in m for m in cm.output), cm.output)

    async def test_none_trde_qty_is_skipped(self):
        client = FakeQuoteClient([q("A", 100, None), q("B", 200, 8)])
        with self.assertLogs("collector", level="WARNING") as cm:
            await collect_once(client, ["A", "B"], self.conn)
        self.assertEqual(self.saved(), {"B": (200, 8)})
        self.assertTrue(any("A" in m for m in cm.output))

    async def test_normal_quotes_are_saved_without_warning(self):
        client = FakeQuoteClient([q("A", 100, 5), q("B", 200, 8)])
        with self.assertNoLogs("collector", level="WARNING"):
            await collect_once(client, ["A", "B"], self.conn)
        self.assertEqual(self.saved(), {"A": (100, 5), "B": (200, 8)})

    async def test_zero_is_not_confused_with_none(self):
        client = FakeQuoteClient([q("A", 0, 0), q("B", 100, 0), q("C", None, 0)])
        with self.assertLogs("collector", level="WARNING") as cm:
            await collect_once(client, ["A", "B", "C"], self.conn)
        self.assertEqual(self.saved(), {"A": (0, 0), "B": (100, 0)})   # 0은 저장, None만 스킵
        self.assertTrue(any("1건" in m and "C" in m and "A" not in m.split(":")[-1] for m in cm.output), cm.output)

    async def test_all_none_does_not_crash(self):
        client = FakeQuoteClient([q("A", None, None), q("B", None, 5)])
        with self.assertLogs("collector", level="WARNING"):
            await collect_once(client, ["A", "B"], self.conn)
        self.assertEqual(self.saved(), {})


if __name__ == "__main__":
    unittest.main()
