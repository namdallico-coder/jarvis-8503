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
