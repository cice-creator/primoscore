CREATE TABLE crm_leads (
 tenant_id TEXT NOT NULL,client_id TEXT NOT NULL,
 stage TEXT NOT NULL DEFAULT 'new' CHECK(stage IN ('new','contact','no_answer','conversation','proposed','booked','completed','later','lost','do_not_contact')),
 priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('high','normal','low')),
 next_action TEXT NOT NULL DEFAULT 'Primo contatto',due_at INTEGER,
 owner TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant_id,client_id),FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE TABLE crm_events (
 tenant_id TEXT NOT NULL,client_id TEXT NOT NULL,id TEXT NOT NULL,
 kind TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE TABLE crm_drafts (
 tenant_id TEXT NOT NULL,client_id TEXT NOT NULL,id TEXT NOT NULL,
 channel TEXT NOT NULL CHECK(channel IN ('whatsapp','email')),purpose TEXT NOT NULL,
 subject TEXT NOT NULL DEFAULT '',body TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','sent_manual')),
 revision INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,client_id) REFERENCES clients(tenant_id,id)
);
CREATE INDEX crm_due ON crm_leads(tenant_id,due_at);
