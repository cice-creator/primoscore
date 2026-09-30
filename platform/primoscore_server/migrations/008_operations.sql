ALTER TABLE consultant_profiles ADD COLUMN profile_revision INTEGER NOT NULL DEFAULT 0;
CREATE TABLE email_changes (
 account_id TEXT PRIMARY KEY REFERENCES accounts(id), token_hash TEXT NOT NULL UNIQUE,
 new_email TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE service_mail (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), client_id TEXT,
 event_key TEXT NOT NULL UNIQUE, payload_encrypted TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE INDEX service_mail_status ON service_mail(status,created_at);
CREATE TABLE privacy_erasures (
 tenant_id TEXT NOT NULL, client_id TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES accounts(id),
 created_at TEXT NOT NULL, PRIMARY KEY(tenant_id,client_id),
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE TRIGGER erasure_no_update BEFORE UPDATE ON privacy_erasures BEGIN SELECT RAISE(ABORT,'immutable_erasure'); END;
CREATE TRIGGER erasure_no_delete BEFORE DELETE ON privacy_erasures BEGIN SELECT RAISE(ABORT,'immutable_erasure'); END;
DROP TRIGGER assessment_no_update;
CREATE TRIGGER assessment_no_update BEFORE UPDATE ON assessments
 WHEN NOT EXISTS(SELECT 1 FROM privacy_erasures e WHERE e.tenant_id=OLD.tenant_id AND e.client_id=OLD.client_id)
 BEGIN SELECT RAISE(ABORT,'immutable_assessment'); END;
DROP TRIGGER intake_no_update;
CREATE TRIGGER intake_no_update BEFORE UPDATE ON customer_intakes
 WHEN NOT EXISTS(SELECT 1 FROM privacy_erasures e WHERE e.tenant_id=OLD.tenant_id AND e.client_id=OLD.client_id)
 BEGIN SELECT RAISE(ABORT,'immutable_intake'); END;
