CREATE TABLE tenants (
 id TEXT PRIMARY KEY NOT NULL,
 slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','suspended')),
 created_at TEXT NOT NULL
);

CREATE TABLE consultant_profiles (
 tenant_id TEXT PRIMARY KEY NOT NULL REFERENCES tenants(id),
 first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
 business_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
 mobile TEXT NOT NULL DEFAULT '', landline TEXT NOT NULL DEFAULT '',
 office_address TEXT NOT NULL DEFAULT '', office_postcode TEXT NOT NULL DEFAULT '',
 office_city TEXT NOT NULL DEFAULT '', office_province TEXT NOT NULL DEFAULT '',
 oam_number TEXT NOT NULL DEFAULT '', ivass_number TEXT NOT NULL DEFAULT '',
 tax_code TEXT NOT NULL DEFAULT '', vat_number TEXT NOT NULL DEFAULT ''
);

CREATE TABLE cities (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,name COLLATE NOCASE)
);

CREATE TABLE partners (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 city_id TEXT, name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 250),
 category TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive','archived')),
 details_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(details_json) AND json_type(details_json)='object'),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,city_id) REFERENCES cities(tenant_id,id)
);

CREATE TABLE voucher_lots (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 city_id TEXT, code TEXT NOT NULL UNIQUE,
 printed INTEGER NOT NULL DEFAULT 0 CHECK(typeof(printed)='integer' AND printed>=0),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,city_id) REFERENCES cities(tenant_id,id)
);

CREATE TABLE campaigns (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 lot_id TEXT NOT NULL, partner_id TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 250),
 kind TEXT NOT NULL CHECK(kind IN ('company','website','flyer','other')),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,lot_id), UNIQUE(tenant_id,partner_id),
 FOREIGN KEY(tenant_id,lot_id) REFERENCES voucher_lots(tenant_id,id),
 FOREIGN KEY(tenant_id,partner_id) REFERENCES partners(tenant_id,id)
);

CREATE TABLE deliveries (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 lot_id TEXT NOT NULL, partner_id TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
 delivered_on TEXT NOT NULL, recipient TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,lot_id) REFERENCES voucher_lots(tenant_id,id),
 FOREIGN KEY(tenant_id,partner_id) REFERENCES partners(tenant_id,id)
);
CREATE INDEX deliveries_lot ON deliveries(tenant_id,lot_id);

CREATE TRIGGER delivery_stock_insert BEFORE INSERT ON deliveries
WHEN NEW.quantity + COALESCE((SELECT SUM(quantity) FROM deliveries WHERE tenant_id=NEW.tenant_id AND lot_id=NEW.lot_id),0)
 > (SELECT printed FROM voucher_lots WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id)
BEGIN SELECT RAISE(ABORT,'insufficient_stock'); END;
CREATE TRIGGER delivery_stock_update BEFORE UPDATE ON deliveries
WHEN NEW.quantity + COALESCE((SELECT SUM(quantity) FROM deliveries WHERE tenant_id=NEW.tenant_id AND lot_id=NEW.lot_id AND id<>OLD.id),0)
 > (SELECT printed FROM voucher_lots WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id)
BEGIN SELECT RAISE(ABORT,'insufficient_stock'); END;
CREATE TRIGGER lot_stock_update BEFORE UPDATE OF printed ON voucher_lots
WHEN NEW.printed < COALESCE((SELECT SUM(quantity) FROM deliveries WHERE tenant_id=NEW.tenant_id AND lot_id=NEW.id),0)
BEGIN SELECT RAISE(ABORT,'insufficient_stock'); END;
CREATE TRIGGER delivery_identity BEFORE UPDATE ON deliveries
WHEN NEW.tenant_id<>OLD.tenant_id OR NEW.id<>OLD.id OR NEW.lot_id<>OLD.lot_id OR NEW.partner_id<>OLD.partner_id
BEGIN SELECT RAISE(ABORT,'immutable_delivery_identity'); END;

CREATE TABLE clients (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 partner_id TEXT, lot_id TEXT,
 first_name TEXT NOT NULL, last_name TEXT NOT NULL DEFAULT '',
 email TEXT NOT NULL DEFAULT '', mobile TEXT NOT NULL DEFAULT '', residence_city TEXT NOT NULL DEFAULT '',
 stage TEXT NOT NULL DEFAULT 'new' CHECK(stage IN ('new','contacted','appointment','qualified','practice','bank','won','archived')),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,partner_id) REFERENCES partners(tenant_id,id),
 FOREIGN KEY(tenant_id,lot_id) REFERENCES voucher_lots(tenant_id,id)
);

CREATE TABLE questionnaires (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 client_id TEXT NOT NULL,
 answers_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(answers_json) AND json_type(answers_json)='object'),
 current_step INTEGER NOT NULL DEFAULT 0 CHECK(typeof(current_step)='integer' AND current_step BETWEEN 0 AND 6),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,client_id), UNIQUE(tenant_id,id,client_id),
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);

CREATE TABLE assessments (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 client_id TEXT NOT NULL, questionnaire_id TEXT NOT NULL,
 answers_revision INTEGER NOT NULL CHECK(typeof(answers_revision)='integer' AND answers_revision>=0),
 engine_version TEXT NOT NULL CHECK(length(engine_version)>0),
 answers_json TEXT NOT NULL CHECK(json_valid(answers_json) AND json_type(answers_json)='object'),
 result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object'),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,questionnaire_id,answers_revision,engine_version),
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id),
 FOREIGN KEY(tenant_id,questionnaire_id,client_id) REFERENCES questionnaires(tenant_id,id,client_id)
);
CREATE TRIGGER assessment_no_update BEFORE UPDATE ON assessments BEGIN SELECT RAISE(ABORT,'immutable_assessment'); END;
CREATE TRIGGER assessment_no_delete BEFORE DELETE ON assessments BEGIN SELECT RAISE(ABORT,'immutable_assessment'); END;

CREATE TABLE activities (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 partner_id TEXT NOT NULL, kind TEXT NOT NULL, occurred_on TEXT NOT NULL,
 details_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(details_json) AND json_type(details_json)='object'),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,partner_id) REFERENCES partners(tenant_id,id)
);
CREATE TRIGGER activity_no_update BEFORE UPDATE ON activities BEGIN SELECT RAISE(ABORT,'immutable_activity'); END;
CREATE TRIGGER activity_no_delete BEFORE DELETE ON activities BEGIN SELECT RAISE(ABORT,'immutable_activity'); END;

CREATE TABLE appointments (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 client_id TEXT NOT NULL, starts_at INTEGER NOT NULL CHECK(typeof(starts_at)='integer'),
 ends_at INTEGER NOT NULL CHECK(typeof(ends_at)='integer' AND ends_at>starts_at),
 status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','cancelled','completed')),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE INDEX appointments_time ON appointments(tenant_id,starts_at,ends_at) WHERE status='confirmed';
CREATE TRIGGER appointment_overlap_insert BEFORE INSERT ON appointments
WHEN NEW.status='confirmed' AND EXISTS(SELECT 1 FROM appointments WHERE tenant_id=NEW.tenant_id AND status='confirmed' AND starts_at<NEW.ends_at AND ends_at>NEW.starts_at)
BEGIN SELECT RAISE(ABORT,'appointment_overlap'); END;
CREATE TRIGGER appointment_overlap_update BEFORE UPDATE ON appointments
WHEN NEW.status='confirmed' AND EXISTS(SELECT 1 FROM appointments WHERE tenant_id=NEW.tenant_id AND id<>OLD.id AND status='confirmed' AND starts_at<NEW.ends_at AND ends_at>NEW.starts_at)
BEGIN SELECT RAISE(ABORT,'appointment_overlap'); END;

CREATE TABLE outbox (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 client_id TEXT NOT NULL, kind TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,
 payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','cancelled','failed')),
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);

CREATE TABLE accounts (
 id TEXT PRIMARY KEY NOT NULL, tenant_id TEXT REFERENCES tenants(id), client_id TEXT,
 role TEXT NOT NULL CHECK(role IN ('master','consultant','customer')),
 email TEXT NOT NULL CHECK(email=lower(trim(email)) AND length(email)>3),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','suspended')),
 created_at TEXT NOT NULL,
 CHECK((role='master' AND tenant_id IS NULL AND client_id IS NULL) OR
       (role='consultant' AND tenant_id IS NOT NULL AND client_id IS NULL) OR
       (role='customer' AND tenant_id IS NOT NULL AND client_id IS NOT NULL)),
 UNIQUE(tenant_id,role,email), UNIQUE(tenant_id,client_id),
 FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE UNIQUE INDEX master_email ON accounts(email) WHERE role='master';
CREATE UNIQUE INDEX consultant_email ON accounts(email) WHERE role='consultant';

CREATE TABLE audit_events (
 id TEXT PRIMARY KEY NOT NULL, tenant_id TEXT REFERENCES tenants(id),
 actor_id TEXT NOT NULL REFERENCES accounts(id),
 action TEXT NOT NULL, resource TEXT NOT NULL, record_id TEXT, reason TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE INDEX audit_tenant ON audit_events(tenant_id,created_at);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable_audit'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable_audit'); END;
