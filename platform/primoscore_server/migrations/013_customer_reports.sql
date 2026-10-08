CREATE TABLE customer_report_deliveries (
 token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 assessment_id TEXT NOT NULL, tenant_id TEXT NOT NULL, client_id TEXT NOT NULL,
 email TEXT NOT NULL, original_email TEXT NOT NULL, expires_at INTEGER NOT NULL, verified_at INTEGER,
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE INDEX customer_reports_assessment ON customer_report_deliveries(tenant_id,assessment_id);
