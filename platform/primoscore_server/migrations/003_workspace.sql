CREATE UNIQUE INDEX lot_city_unique ON voucher_lots(tenant_id,city_id) WHERE city_id IS NOT NULL;
CREATE UNIQUE INDEX campaign_name_unique ON campaigns(tenant_id,name COLLATE NOCASE);
ALTER TABLE partners ADD COLUMN archived_from TEXT CHECK(archived_from IN ('active','inactive'));
CREATE TABLE print_runs (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL, lot_id TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
 printed_on TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,lot_id) REFERENCES voucher_lots(tenant_id,id)
);
CREATE TRIGGER print_no_update BEFORE UPDATE ON print_runs BEGIN SELECT RAISE(ABORT,'immutable_print'); END;
CREATE TRIGGER print_no_delete BEFORE DELETE ON print_runs BEGIN SELECT RAISE(ABORT,'immutable_print'); END;
CREATE TABLE workspace_receipts (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), request_id TEXT NOT NULL,
 actor_id TEXT NOT NULL REFERENCES accounts(id), fingerprint TEXT NOT NULL,
 result_json TEXT NOT NULL CHECK(json_valid(result_json)), created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,request_id)
);
