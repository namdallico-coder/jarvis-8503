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
