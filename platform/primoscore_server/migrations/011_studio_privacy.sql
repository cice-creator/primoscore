ALTER TABLE consultant_profiles ADD COLUMN controller_dpo TEXT NOT NULL DEFAULT '';
ALTER TABLE consultant_profiles ADD COLUMN controller_address TEXT NOT NULL DEFAULT '';
CREATE TABLE studio_privacy_documents (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), version TEXT NOT NULL,
 profile_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id, version)
);
