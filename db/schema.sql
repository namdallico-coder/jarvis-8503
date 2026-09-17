CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stk_cd TEXT NOT NULL,
    stk_nm TEXT,
    cur_prc INTEGER NOT NULL,
    trde_qty INTEGER NOT NULL,
    collected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prices_stk_cd_collected_at
    ON prices (stk_cd, collected_at);

-- DART 전자공시 (news/dart_client.py). rcept_no(접수번호)가 DART의 고유 식별자라
-- PRIMARY KEY로 써서 중복 저장 없이 "신규 공시만" 자연스럽게 걸러진다.
CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no TEXT PRIMARY KEY,
    corp_code TEXT NOT NULL,
    stock_code TEXT,
    corp_name TEXT,
    report_nm TEXT,
    rcept_dt TEXT,
    flr_nm TEXT,
    rm TEXT,
    collected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_disclosures_stock_code_rcept_dt
    ON disclosures (stock_code, rcept_dt);

-- AI 판단 로그 (decision/main.py). 페이퍼 모드 — 실제 매매 없이 판단만 기록한다.
-- context_snapshot에 판단 시점 입력 데이터 전체를 JSON으로 같이 저장해서
-- "왜 이렇게 판단했는지" 나중에 재구성할 수 있게 한다.
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stk_cd TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    reason TEXT NOT NULL,
    context_snapshot TEXT NOT NULL,
    model TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_stk_cd_decided_at
    ON decisions (stk_cd, decided_at);
