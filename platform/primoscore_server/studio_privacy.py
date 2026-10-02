"""Versioned studio notice snapshots; custom external notices retain priority."""
import hashlib
import json
from .database import now
MODEL_VERSION = '1.0-2026-10-02'
def automatic_notice(c, tenant, origin):
    row = c.execute('SELECT p.*,t.slug FROM consultant_profiles p JOIN tenants t ON t.id=p.tenant_id WHERE p.tenant_id=?', (tenant,)).fetchone()
    if not row:
        return None
    fields = ('controller_name','controller_email','controller_dpo','office_address','office_postcode','office_city','office_province')
    profile = {key: row[key] for key in fields}
    if not all(profile[key].strip() for key in fields if key != 'controller_dpo'):
        return None
    profile['controller_address'] = row['controller_address'].strip() or f"{row['office_address']}, {row['office_postcode']} {row['office_city']} ({row['office_province']})"
    payload = json.dumps(profile, sort_keys=True, ensure_ascii=False)
    version = MODEL_VERSION + '-' + hashlib.sha256(payload.encode()).hexdigest()[:16]
    c.execute('INSERT OR IGNORE INTO studio_privacy_documents VALUES(?,?,?,?)', (tenant, version, payload, now()))
    return {'privacy_url': origin.rstrip('/') + '/privacy/studio/' + row['slug'] + '/' + version, 'privacy_version': version}
