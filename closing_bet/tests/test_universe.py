"""closing_bet 유니버스가 research/universe.py의 top_universe와 같은 결과를 내는지, 미래 정보를 안 쓰는지,
상위 300만 관측하는 근사 모드가 정확한 결과와 얼마나 겹치는지 검증한다."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from closing_bet import store
from closing_bet.config import settings as cfg
from closing_bet.seed import seed

RESEARCH = Path("/home/ubuntu/jarvis-field/research")


@unittest.skipUnless(cfg.research_db.exists(), "research DB 없음")
class UniverseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = store.connect(Path(cls.tmp.name) / "base.db")
        cls.hist = seed(base, cfg, n_days=60, universes=0)
        base.close()
        sys.path.insert(0, str(RESEARCH))
        import universe as ru
        cls.ru = ru
        cls.data = ru.load()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        sys.path.remove(str(RESEARCH))

    def setUp(self):  # 테스트마다 시드된 기준 DB 복사본을 쓴다 (테스트 간 오염 방지)
        path = Path(self.tmp.name) / f"{self.id().split('.')[-1]}.db"
        shutil.copy(Path(self.tmp.name) / "base.db", path)
        self.conn = store.connect(path)

    def tearDown(self):
        self.conn.close()

    def _research(self, asof):
        d = self.data.dates.index(asof.replace("-", "")) + 1  # asof 다음 거래일의 유니버스
        return self.ru.top_universe(self.data, d)

    def test_exact_mode_equals_research(self):
        for asof in self.hist[-14:-1]:
            mine = [u["code"] for u in store.compute_universe(self.conn, cfg, asof)]
            self.assertEqual(mine, self._research(asof), asof)

    def test_no_lookahead(self):
        asof = self.hist[-8]
        before = [u["code"] for u in store.compute_universe(self.conn, cfg, asof)]
        # asof 이후 데이터를 모두 지워도 같은 결과여야 한다
        later = [d for d in self.hist if d > asof]
        self.conn.execute(f"DELETE FROM cb_amount_history WHERE dt IN ({','.join('?'*len(later))})", later)
        self.conn.execute(f"DELETE FROM cb_day_meta WHERE dt IN ({','.join('?'*len(later))})", later)
        self.conn.commit()
        after = [u["code"] for u in store.compute_universe(self.conn, cfg, asof)]
        self.assertEqual(before, after)

    def test_approx_mode_overlap(self):
        exact = {a: {u["code"] for u in store.compute_universe(self.conn, cfg, a)} for a in self.hist[-6:-1]}
        # 마지막 25일을 "상위 300위만 관측" 상태로 바꾼다
        for d in self.hist[-26:]:
            rows = self.conn.execute("SELECT code, amount FROM cb_amount_history WHERE dt=? ORDER BY amount DESC", (d,)).fetchall()
            keep, drop = rows[:300], rows[300:]
            self.conn.executemany("DELETE FROM cb_amount_history WHERE dt=? AND code=?", [(d, r[0]) for r in drop])
            self.conn.execute("UPDATE cb_day_meta SET exact=0, cutoff_amount=?, n_rows=300 WHERE dt=?", (keep[-1][1], d))
        self.conn.commit()
        overlaps = []
        for a, ex in exact.items():
            ap = {u["code"] for u in store.compute_universe(self.conn, cfg, a)}
            overlaps.append(len(ex & ap))
        self.assertGreaterEqual(min(overlaps), 26, overlaps)
        print("\n근사(상위300) 겹침:", overlaps)


if __name__ == "__main__":
    unittest.main()
