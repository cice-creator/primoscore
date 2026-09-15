CREATE TABLE auth_credentials (
 account_id TEXT PRIMARY KEY REFERENCES accounts(id), password_hash TEXT,
 email_verified INTEGER NOT NULL DEFAULT 0 CHECK(email_verified IN (0,1)),
 totp_encrypted TEXT, totp_last_step INTEGER NOT NULL DEFAULT -1,
 password_changed_at INTEGER NOT NULL
);
CREATE TABLE auth_tokens (
 token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset','invite')),
 expires_at INTEGER NOT NULL, used_at INTEGER
);
CREATE INDEX auth_tokens_account ON auth_tokens(account_id,purpose);
CREATE TABLE auth_steps (
 token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 kind TEXT NOT NULL CHECK(kind IN ('enroll','otp')),
 pending_secret TEXT, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX auth_steps_account ON auth_steps(account_id);
CREATE TABLE auth_sessions (
 token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL
);
CREATE INDEX auth_sessions_account ON auth_sessions(account_id);
CREATE TABLE auth_recovery_codes (
 account_id TEXT NOT NULL REFERENCES accounts(id), code_hash TEXT NOT NULL,
 PRIMARY KEY(account_id,code_hash)
);
CREATE TABLE auth_rate_limits (
 bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, attempts INTEGER NOT NULL
);
CREATE TABLE auth_mail (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 payload_encrypted TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE INDEX auth_mail_status ON auth_mail(status,created_at);
CREATE TABLE auth_events (
 id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(id),
 event TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TRIGGER auth_events_no_update BEFORE UPDATE ON auth_events BEGIN SELECT RAISE(ABORT,'immutable_auth_event'); END;
CREATE TRIGGER auth_events_no_delete BEFORE DELETE ON auth_events BEGIN SELECT RAISE(ABORT,'immutable_auth_event'); END;
