PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS battles (
  id TEXT PRIMARY KEY,
  arena TEXT,
  topic TEXT,
  status TEXT,
  phase TEXT,
  created_at INTEGER,
  completed_at INTEGER,
  error TEXT
);

CREATE TABLE IF NOT EXISTS contestants (
  id TEXT PRIMARY KEY,
  battle_id TEXT,
  slot INTEGER,
  provider_id TEXT,
  model_id TEXT,
  position_index INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  latency_ms INTEGER,
  output_text TEXT,
  code TEXT,
  status TEXT,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS votes (
  id TEXT PRIMARY KEY,
  battle_id TEXT,
  contestant_id TEXT,
  voter_key TEXT,
  position_index INTEGER,
  created_at INTEGER,
  UNIQUE(battle_id, voter_key)
);

CREATE TABLE IF NOT EXISTS predictions (
  id TEXT PRIMARY KEY,
  battle_id TEXT,
  contestant_id TEXT,
  voter_key TEXT,
  position_index INTEGER,
  created_at INTEGER,
  UNIQUE(battle_id, voter_key)
);

CREATE TABLE IF NOT EXISTS judgements (
  id TEXT PRIMARY KEY,
  battle_id TEXT,
  contestant_id TEXT,
  round INTEGER,
  judge_model TEXT,
  position_index INTEGER,
  scores_json TEXT,
  rationale TEXT,
  created_at INTEGER
);

CREATE TABLE IF NOT EXISTS sandbox_results (
  id TEXT PRIMARY KEY,
  contestant_id TEXT,
  backend TEXT,
  isolation_level TEXT,
  language TEXT,
  status TEXT,
  passed INTEGER,
  total INTEGER,
  time_ms INTEGER,
  memory_kb INTEGER,
  compile_output TEXT,
  stdout_tail TEXT,
  degraded INTEGER
);

CREATE INDEX IF NOT EXISTS idx_contestants_battle_id ON contestants(battle_id);
CREATE INDEX IF NOT EXISTS idx_votes_battle_id ON votes(battle_id);
CREATE INDEX IF NOT EXISTS idx_predictions_battle_id ON predictions(battle_id);
CREATE INDEX IF NOT EXISTS idx_judgements_battle_id ON judgements(battle_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_results_contestant_id ON sandbox_results(contestant_id);
