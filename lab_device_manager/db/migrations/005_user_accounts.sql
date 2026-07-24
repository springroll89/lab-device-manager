CREATE TABLE IF NOT EXISTS user_account (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL COLLATE NOCASE UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('super_admin', 'supervisor', 'operator')),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
  must_change_password INTEGER NOT NULL DEFAULT 1
    CHECK(must_change_password IN (0, 1)),
  created_by INTEGER REFERENCES user_account(id),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  last_login_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_user_account_role_active
  ON user_account(role, is_active, username);

CREATE TABLE IF NOT EXISTS account_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id INTEGER REFERENCES user_account(id),
  target_user_id INTEGER REFERENCES user_account(id),
  action TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_account_audit_target_time
  ON account_audit(target_user_id, created_at_ms DESC);
