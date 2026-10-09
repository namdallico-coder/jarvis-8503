"""주문 API id/주문 함수가 closing_bet 코드에 없는지 검증한다 (페이퍼 전용 보장)."""

import ast
import re
import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
REPO = PKG.parent

# 패턴은 자기 자신에 걸리지 않도록 조각으로 조립한다.
FORBIDDEN = [
    r"k" + r"t1000[0-9]",            # 키움 주식 매수/매도/정정/취소 주문 TR
    r"k" + r"t5000[0-9]",            # 신용 주문 TR
    r"/api/dostk/" + r"(crd)?ordr",  # 주문 엔드포인트
    r"\b(place|send|submit|cancel|modify)_" + r"order",
    r"\bordr_",                      # 주문 파라미터 접두 (ordr_qty 등)
]
ALLOWED_API = {"ka10032", "ka10001", "ka10099"}
ALLOWED_IMPORT_PREFIXES = ("closing_bet", "collector.kiwoom_client")
# 표준 라이브러리는 허용하되, 네트워크/프로세스 모듈은 이 패키지에서 직접 쓰지 못하게 막는다
# (키움 접근은 collector.kiwoom_client 한 곳을 통해서만, 조회 TR 3개로 제한).
STDLIB_DENY = {"socket", "ssl", "urllib", "http", "subprocess", "ftplib", "smtplib", "telnetlib", "xmlrpc", "multiprocessing"}


def package_sources():
    return [p for p in PKG.rglob("*.py") if "tests" not in p.parts]


class NoOrderTests(unittest.TestCase):
    def test_no_order_api_ids_or_functions(self):
        for path in package_sources():
            text = path.read_text(encoding="utf-8")
            for pat in FORBIDDEN:
                self.assertIsNone(re.search(pat, text, re.I), f"{path.name}: 주문 관련 패턴 발견: {pat}")

    def test_only_allowed_query_api_ids(self):
        found = set()
        for path in package_sources():
            found |= set(re.findall(r"\bk[a-z]\d{5}\b", path.read_text(encoding="utf-8")))
        self.assertTrue(found, "API id를 하나도 찾지 못함")
        self.assertLessEqual(found, ALLOWED_API, f"허용되지 않은 API id: {found - ALLOWED_API}")

    def test_imports_are_restricted(self):
        for path in package_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    top = m.split(".")[0]
                    ok = m.startswith(ALLOWED_IMPORT_PREFIXES) or (top in sys.stdlib_module_names and top not in STDLIB_DENY)
                    self.assertTrue(ok, f"{path.name}: 허용 목록 밖 import {m}")

    def test_reused_kiwoom_client_has_no_order_methods(self):
        text = (REPO / "collector" / "kiwoom_client.py").read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            self.assertIsNone(re.search(pat, text, re.I), f"재사용하는 kiwoom_client에 주문 패턴: {pat}")


if __name__ == "__main__":
    unittest.main()
