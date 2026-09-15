CREATE TABLE customer_settings (
 tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), privacy_url TEXT NOT NULL DEFAULT '',
 privacy_version TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE customer_intakes (
 tenant_id TEXT NOT NULL, client_id TEXT NOT NULL, request_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, partner_label TEXT NOT NULL, privacy_version TEXT NOT NULL,
 privacy_url TEXT NOT NULL, accepted_at TEXT NOT NULL, source TEXT NOT NULL,
 PRIMARY KEY(tenant_id,client_id), UNIQUE(tenant_id,request_id),
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE TABLE customer_guest_sessions (
 token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 expires_at INTEGER NOT NULL
);
CREATE TABLE customer_events (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, client_id TEXT NOT NULL, kind TEXT NOT NULL,
 created_at TEXT NOT NULL, FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE TRIGGER customer_event_no_update BEFORE UPDATE ON customer_events BEGIN SELECT RAISE(ABORT,'immutable_customer_event'); END;
CREATE TRIGGER customer_event_no_delete BEFORE DELETE ON customer_events BEGIN SELECT RAISE(ABORT,'immutable_customer_event'); END;
CREATE TABLE booking_slots (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL, starts_at INTEGER NOT NULL,
 ends_at INTEGER NOT NULL CHECK(ends_at>starts_at), status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, PRIMARY KEY(tenant_id,id),
 UNIQUE(tenant_id,starts_at)
);
CREATE TABLE customer_bookings (
 tenant_id TEXT NOT NULL, appointment_id TEXT NOT NULL, slot_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(tenant_id,appointment_id), FOREIGN KEY(tenant_id,appointment_id) REFERENCES appointments(tenant_id,id),
 FOREIGN KEY(tenant_id,slot_id) REFERENCES booking_slots(tenant_id,id)
);
CREATE TRIGGER intake_no_update BEFORE UPDATE ON customer_intakes BEGIN SELECT RAISE(ABORT,'immutable_intake'); END;
CREATE TRIGGER intake_no_delete BEFORE DELETE ON customer_intakes BEGIN SELECT RAISE(ABORT,'immutable_intake'); END;
