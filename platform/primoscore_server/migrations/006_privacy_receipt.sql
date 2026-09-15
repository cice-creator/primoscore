-- Separate notice acknowledgement from the service request timestamp.
ALTER TABLE customer_intakes ADD COLUMN privacy_acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(privacy_acknowledged IN (0,1));
UPDATE customer_intakes SET privacy_acknowledged=1 WHERE privacy_version<>'';
