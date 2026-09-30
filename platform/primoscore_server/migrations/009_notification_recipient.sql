ALTER TABLE service_mail ADD COLUMN recipient_account_id TEXT REFERENCES accounts(id);
CREATE INDEX service_mail_recipient ON service_mail(recipient_account_id,status);
