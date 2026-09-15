CREATE TABLE partner_imports (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 actor_id TEXT NOT NULL REFERENCES accounts(id),
 rows_json TEXT NOT NULL CHECK(json_valid(rows_json)), revision TEXT NOT NULL,
 created_at INTEGER NOT NULL, result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 PRIMARY KEY(tenant_id,id)
);
CREATE INDEX partner_import_expiry ON partner_imports(tenant_id,created_at);
