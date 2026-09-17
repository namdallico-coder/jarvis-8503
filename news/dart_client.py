"""DART(전자공시) Open API 클라이언트.

인증은 발급받은 crtfc_key를 쿼리 파라미터로 붙이는 방식뿐이라(OAuth 토큰 없음)
kiwoom_client.py보다 단순하다. API 키는 무료로 즉시 발급된다:
https://opendart.fss.or.kr → 인증키 신청/관리 → 개인/이메일/비밀번호 등록.

스펙 출처:
  - 공시검색(list.json): https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001
  - 고유번호(corpCode.xml, ZIP+XML): 응답 구조는 josw123/dart-fss 참고
    (https://github.com/josw123/dart-fss/blob/master/dart_fss/api/filings/corp_code.py)

주의: list.json의 corp_code는 DART 전용 8자리 코드로, 키움 종목코드(6자리, 거래소
접미사 _AL/_NX 포함 가능)와 다르다. fetch_corp_code_map()으로 stock_code(6자리) ->
corp_code 매핑을 받아 사용해야 한다.
"""

from __future__ import annotations

import io
import os
import zipfile
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import httpx

BASE_URL = "https://opendart.fss.or.kr/api"
DISCLOSURE_LIST_PATH = "/list.json"
CORP_CODE_PATH = "/corpCode.xml"

STATUS_OK = "000"
STATUS_NO_DATA = "013"  # 조회된 데이터가 없습니다 (정상 케이스, 에러 아님)


class DartClientError(Exception):
    pass


class DartClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 10.0) -> None:
        self.api_key = api_key or os.getenv("DART_API_KEY")
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=httpx.Timeout(timeout))

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_disclosures(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_count: int = 100,
    ) -> List[Dict[str, Any]]:
        """지정 기간(bgn_de~end_de, YYYYMMDD) 내 해당 기업의 공시 목록 전체를 조회한다."""
        if not self.api_key:
            raise DartClientError("DART_API_KEY 가 설정되지 않았습니다.")

        results: List[Dict[str, Any]] = []
        page_no = 1
        while True:
            response = await self._client.get(
                DISCLOSURE_LIST_PATH,
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "page_no": page_no,
                    "page_count": page_count,
                },
            )
            data = response.json()
            status = data.get("status")
            if status == STATUS_NO_DATA:
                break
            if status != STATUS_OK:
                raise DartClientError(
                    f"공시검색 실패 [corp_code={corp_code}]: {data.get('message')} (status={status})"
                )

            results.extend(data.get("list", []))
            total_page = int(data.get("total_page", 1) or 1)
            if page_no >= total_page:
                break
            page_no += 1

        return results

    async def fetch_corp_code_map(self) -> Dict[str, Dict[str, str]]:
        """전체 상장사의 stock_code(6자리) -> {corp_code, corp_name, modify_date} 매핑을 받는다.

        비상장/펀드 등 stock_code가 없는 항목은 제외한다.
        """
        if not self.api_key:
            raise DartClientError("DART_API_KEY 가 설정되지 않았습니다.")

        response = await self._client.get(CORP_CODE_PATH, params={"crtfc_key": self.api_key})
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
        except zipfile.BadZipFile as exc:
            raise DartClientError(
                f"고유번호 다운로드 실패(zip이 아닌 응답): {response.text[:200]}"
            ) from exc

        root = ElementTree.fromstring(xml_bytes)
        mapping: Dict[str, Dict[str, str]] = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            if not stock_code:
                continue
            mapping[stock_code] = {
                "corp_code": (item.findtext("corp_code") or "").strip(),
                "corp_name": (item.findtext("corp_name") or "").strip(),
                "modify_date": (item.findtext("modify_date") or "").strip(),
            }
        return mapping
