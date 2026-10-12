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


class DedicatedKeyTests(unittest.TestCase):
    ENV = ("CB_KIWOOM_APP_KEY", "CB_KIWOOM_SECRET_KEY", "CB_ALLOW_SHARED_KEY", "KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY")

    def setUp(self):
        import os
        self.os = os
        self.saved = {k: os.environ.get(k) for k in self.ENV}
        for k in self.ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                self.os.environ.pop(k, None)
            else:
                self.os.environ[k] = v

    def test_missing_keys_raise_and_no_fallback_to_collector_key(self):
        from closing_bet.kiwoom_api import KiwoomConfigError
        self.os.environ["KIWOOM_APP_KEY"] = "collector-app"      # collector 키만 있는 상태
        self.os.environ["KIWOOM_SECRET_KEY"] = "collector-sec"
        with self.assertRaises(KiwoomConfigError) as cm:
            KiwoomAPI()
        self.assertIn("CB_ALLOW_SHARED_KEY", str(cm.exception))   # 공유를 원하면 명시하라고 안내
        self.assertNotIn("collector-app", str(cm.exception))      # 값은 메시지에 없다

    def test_one_missing_key_raises(self):
        from closing_bet.kiwoom_api import KiwoomConfigError
        self.os.environ["CB_KIWOOM_APP_KEY"] = "cb-app"
        with self.assertRaises(KiwoomConfigError) as cm:
            KiwoomAPI()
        self.assertIn("CB_KIWOOM_SECRET_KEY", str(cm.exception))

    def test_shared_mode_only_with_explicit_flag_value_1(self):
        from closing_bet.kiwoom_api import KiwoomConfigError
        self.os.environ.update({"KIWOOM_APP_KEY": "collector-app", "KIWOOM_SECRET_KEY": "collector-sec"})
        for bad in ("", "0", "true", "yes", "2"):
            self.os.environ["CB_ALLOW_SHARED_KEY"] = bad
            with self.assertRaises(KiwoomConfigError, msg=repr(bad)):
                KiwoomAPI()
        self.os.environ["CB_ALLOW_SHARED_KEY"] = "1"
        api = KiwoomAPI()
        self.assertTrue(api.shared_key)
        self.assertEqual((api.client.app_key, api.client.secret_key), ("collector-app", "collector-sec"))

    def test_shared_flag_without_collector_keys_raises(self):
        from closing_bet.kiwoom_api import KiwoomConfigError
        self.os.environ["CB_ALLOW_SHARED_KEY"] = "1"
        with self.assertRaises(KiwoomConfigError):
            KiwoomAPI()

    def test_dedicated_keys_win_over_shared_flag(self):
        self.os.environ.update({"KIWOOM_APP_KEY": "collector-app", "KIWOOM_SECRET_KEY": "collector-sec",
                                "CB_KIWOOM_APP_KEY": "cb-app", "CB_KIWOOM_SECRET_KEY": "cb-sec", "CB_ALLOW_SHARED_KEY": "1"})
        api = KiwoomAPI()
        self.assertFalse(api.shared_key)
        self.assertEqual(api.client.app_key, "cb-app")

    def test_shared_mode_logs_warning_and_event(self):
        import logging, tempfile
        from pathlib import Path
        from closing_bet import store
        from closing_bet.main import init_api
        self.os.environ.update({"KIWOOM_APP_KEY": "collector-app", "KIWOOM_SECRET_KEY": "collector-sec", "CB_ALLOW_SHARED_KEY": "1"})
        with tempfile.TemporaryDirectory() as d:
            conn = store.connect(Path(d) / "t.db")
            with self.assertLogs("closing_bet", level="WARNING") as cm:
                api = init_api(conn)
            self.assertTrue(api.shared_key)
            self.assertTrue(any("공유 키 모드: collector와 토큰 충돌 가능" in m and m.startswith("WARNING") for m in cm.output), cm.output)
            self.assertFalse(any(m.startswith("CRITICAL") for m in cm.output))
            kinds = [r[0] for r in conn.execute("SELECT kind FROM cb_events")]
            self.assertEqual(kinds, ["shared_key_mode"])
            conn.close()

    def test_dedicated_mode_records_no_shared_event(self):
        import tempfile
        from pathlib import Path
        from closing_bet import store
        from closing_bet.main import init_api
        self.os.environ.update({"KIWOOM_APP_KEY": "collector-app", "KIWOOM_SECRET_KEY": "collector-sec",
                                "CB_KIWOOM_APP_KEY": "cb-app", "CB_KIWOOM_SECRET_KEY": "cb-sec"})
        with tempfile.TemporaryDirectory() as d:
            conn = store.connect(Path(d) / "t.db")
            init_api(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cb_events").fetchone()[0], 0)
            conn.close()

    def test_same_key_as_collector_is_rejected(self):
        from closing_bet.kiwoom_api import KiwoomConfigError
        self.os.environ.update({"KIWOOM_APP_KEY": "same", "CB_KIWOOM_APP_KEY": "same", "CB_KIWOOM_SECRET_KEY": "s"})
        with self.assertRaises(KiwoomConfigError):
            KiwoomAPI()

    def test_dedicated_keys_are_passed_to_client(self):
        self.os.environ.update({"KIWOOM_APP_KEY": "collector-app", "KIWOOM_SECRET_KEY": "collector-sec",
                                "CB_KIWOOM_APP_KEY": "cb-app", "CB_KIWOOM_SECRET_KEY": "cb-sec"})
        api = KiwoomAPI()
        self.assertEqual((api.client.app_key, api.client.secret_key), ("cb-app", "cb-sec"))

    def test_sources_never_read_collector_key_names(self):
        """환경변수로 collector 키 이름(KIWOOM_*)을 읽는 곳은 키 해석 함수가 있는 kiwoom_api.py뿐이어야 한다."""
        import re
        from pathlib import Path
        pkg = Path(__file__).resolve().parent.parent
        pat = re.compile(r"(?:environ\.get\(|getenv\(|environ\[)\s*[\"'](?<!CB_)KIWOOM_(?:APP|SECRET)_KEY[\"']")
        reads = []
        for p in pkg.rglob("*.py"):
            if "tests" in p.parts:
                continue
            reads += [(p.name, m) for m in pat.findall(p.read_text(encoding="utf-8"))]
        self.assertTrue(reads and all(name == "kiwoom_api.py" for name, _ in reads), reads)  # 키 해석은 kiwoom_api.py 한 곳에서만
