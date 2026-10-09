import unittest
from types import SimpleNamespace

from closing_bet.kiwoom_api import KiwoomAPI
from collector.kiwoom_client import QuoteClientError


class AuthRetryTests(unittest.IsolatedAsyncioTestCase):
    def _api(self, fail_times, code=8005):
        state = {"calls": 0, "refreshed": 0}

        async def request(path, api_id, body):
            state["calls"] += 1
            if state["calls"] <= fail_times:
                raise QuoteClientError("x", return_code=code)
            return {"open_pric": "+100", "cur_prc": "-100", "trde_qty": "5"}

        async def token(force_refresh=False):
            state["refreshed"] += int(force_refresh)

        api = KiwoomAPI(client=SimpleNamespace(request=request, _get_access_token=token))
        return api, state

    async def test_retries_once_on_8005(self):
        api, st = self._api(1)
        q = await api.quote("005930")
        self.assertEqual((q["open_pric"], st["calls"], st["refreshed"]), (100, 2, 1))

    async def test_gives_up_after_second_failure(self):
        api, st = self._api(2)
        with self.assertRaises(QuoteClientError):
            await api.quote("005930")
        self.assertEqual(st["calls"], 2)

    async def test_other_errors_not_retried(self):
        api, st = self._api(1, code=5)
        with self.assertRaises(QuoteClientError):
            await api.quote("005930")
        self.assertEqual((st["calls"], st["refreshed"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
