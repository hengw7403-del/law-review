PRAGMA foreign_keys = ON;

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','reviewer')),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  auth_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE contracts (
  id TEXT PRIMARY KEY,
  series_id TEXT NOT NULL,
  contract_version INTEGER NOT NULL CHECK (contract_version > 0),
  previous_version_id TEXT REFERENCES contracts(id),
  title TEXT NOT NULL,
  filename TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  party_stance TEXT NOT NULL CHECK (party_stance IN ('party_a','party_b')),
  amount_minor INTEGER CHECK (amount_minor IS NULL OR amount_minor >= 0),
  currency TEXT NOT NULL DEFAULT 'CNY',
  focus_points TEXT,
  raw_text TEXT,
  parse_status TEXT NOT NULL CHECK (parse_status IN ('uploaded','parsing','parsed','parse_failed')),
  parse_error TEXT,
  parse_warnings TEXT NOT NULL DEFAULT '[]',
  parse_confirmed_by TEXT REFERENCES users(id),
  parse_confirmed_at TEXT,
  owner_id TEXT NOT NULL REFERENCES users(id),
  reviewer_id TEXT REFERENCES users(id),
  locked_at TEXT,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(series_id, contract_version)
);

CREATE TABLE clauses (
  id TEXT PRIMARY KEY,
  contract_id TEXT NOT NULL REFERENCES contracts(id),
  parent_clause_id TEXT REFERENCES clauses(id),
  seq INTEGER NOT NULL,
  original_no TEXT,
  content TEXT NOT NULL,
  char_start INTEGER,
  char_end INTEGER,
  location_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(contract_id, seq)
);

CREATE TABLE knowledge_chunks (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('law','internal_rule')),
  source_file TEXT,
  source_locator TEXT NOT NULL,
  publisher TEXT,
  source_version INTEGER NOT NULL CHECK (source_version > 0),
  article_no TEXT,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  effective_date TEXT,
  expiry_date TEXT,
  applicability TEXT,
  verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
  verified_by TEXT REFERENCES users(id),
  verified_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
  superseded INTEGER NOT NULL DEFAULT 0 CHECK (superseded IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, source_version, chunk_index)
);

CREATE TABLE review_tasks (
  id TEXT PRIMARY KEY,
  contract_id TEXT NOT NULL REFERENCES contracts(id),
  review_version INTEGER NOT NULL CHECK (review_version > 0),
  status TEXT NOT NULL CHECK (status IN ('queued','running','completed','partial_failed','failed')),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','in_review','confirmed')),
  stage TEXT NOT NULL DEFAULT 'queued',
  input_snapshot TEXT NOT NULL,
  total_items INTEGER NOT NULL DEFAULT 0,
  done_items INTEGER NOT NULL DEFAULT 0,
  failed_items INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_message TEXT,
  summary TEXT,
  summary_history TEXT NOT NULL DEFAULT '[]',
  frozen_report TEXT,
  report_hash TEXT,
  confirmed_by TEXT REFERENCES users(id),
  confirmed_at TEXT,
  revision INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL REFERENCES users(id),
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(contract_id, review_version),
  CHECK (review_status <> 'confirmed' OR
    (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND
     frozen_report IS NOT NULL AND report_hash IS NOT NULL))
);

CREATE TABLE review_items (
  id TEXT PRIMARY KEY,
  review_id TEXT NOT NULL REFERENCES review_tasks(id),
  item_key TEXT NOT NULL,
  clause_id TEXT REFERENCES clauses(id),
  rule_id TEXT NOT NULL,
  execution_status TEXT NOT NULL DEFAULT 'pending' CHECK (execution_status IN ('pending','done')),
  check_status TEXT CHECK (check_status IN ('supported','insufficient','failed','no_issue_detected')),
  risk_level TEXT CHECK (risk_level IN ('high','medium','low','unknown')),
  machine_result TEXT,
  human_result TEXT,
  decision TEXT NOT NULL DEFAULT 'pending' CHECK (decision IN
    ('pending','investigating','accepted','modified','excluded','unresolved')),
  decision_note TEXT,
  decision_history TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT REFERENCES users(id),
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(review_id, item_key),
  CHECK (execution_status <> 'done' OR (check_status IS NOT NULL AND machine_result IS NOT NULL))
);

CREATE TABLE llm_logs (
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  review_id TEXT NOT NULL REFERENCES review_tasks(id),
  item_id TEXT REFERENCES review_items(id),
  purpose TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  duration_ms INTEGER,
  attempt_no INTEGER NOT NULL,
  error_code TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_contracts_owner ON contracts(owner_id);
CREATE INDEX idx_contracts_reviewer ON contracts(reviewer_id);
CREATE INDEX idx_clauses_contract ON clauses(contract_id);
CREATE INDEX idx_knowledge_active ON knowledge_chunks(enabled, verified, superseded);
CREATE INDEX idx_tasks_queue ON review_tasks(status, created_at);
CREATE UNIQUE INDEX uq_active_review ON review_tasks(contract_id)
  WHERE status IN ('queued','running');
CREATE INDEX idx_items_review ON review_items(review_id);
CREATE INDEX idx_logs_review ON llm_logs(review_id, created_at);
