CREATE TABLE crm_rules (
 tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 first_hours INTEGER NOT NULL DEFAULT 2 CHECK(first_hours IN (1,2,4,8)),
 retry_days INTEGER NOT NULL DEFAULT 1 CHECK(retry_days BETWEEN 1 AND 3),
 proposal_days INTEGER NOT NULL DEFAULT 2 CHECK(proposal_days IN (1,2,3,5)),
 revision INTEGER NOT NULL DEFAULT 0
);
