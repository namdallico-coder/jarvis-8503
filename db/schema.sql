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

-- 이동평균 크로스 신호 (signals/ma_signal.py). 크로스가 실제로 발생한 순간만 기록한다
-- (매 체크마다 쌓지 않음 — signals/main.py가 직전 상/하 관계와 비교해서 뒤집힌 경우만 저장).
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stk_cd TEXT NOT NULL,
    signal_type TEXT NOT NULL,  -- golden_cross | dead_cross
    short_window_min INTEGER NOT NULL,
    long_window_min INTEGER NOT NULL,
    short_ma REAL NOT NULL,
    long_ma REAL NOT NULL,
    price_at_signal INTEGER NOT NULL,
    detected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_stk_cd_detected_at
    ON signals (stk_cd, detected_at);

-- AI 판단 로그 (decision/main.py). 페이퍼 모드 — 실제 매매 없이 판단만 기록한다.
-- signals 테이블에 신호가 뜬 종목만 여기 들어온다 (신호 없으면 AI 호출 자체를 안 함).
-- signal_id/signal_type으로 "이 판단이 어떤 신호에 대한 리뷰였는지" 바로 추적 가능하고,
-- context_snapshot에는 판단 시점 입력 데이터 전체(신호+최근 시세+오늘자 공시+보유상태)를
-- JSON으로 같이 저장해서 "왜 이렇게 판단했는지" 나중에 재구성할 수 있게 한다.
-- action은 AI가 신호를 "따르기로" 했을 때의 최종 매매 방향(buy/sell)이거나,
-- "보류"했을 때는 hold다 (golden_cross+따름=buy, dead_cross+따름=sell, 보류=hold).
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stk_cd TEXT NOT NULL,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    signal_type TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    action TEXT NOT NULL,  -- buy | sell | hold
    confidence INTEGER NOT NULL,
    reason TEXT NOT NULL,
    context_snapshot TEXT NOT NULL,
    model TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_stk_cd_decided_at
    ON decisions (stk_cd, decided_at);
CREATE INDEX IF NOT EXISTS idx_decisions_signal_id
    ON decisions (signal_id);
