"""Master summaries and explicitly audited per-studio reads."""
from .auth import AuthError
from .workspace import Workspace

class Master:
    def __init__(self, auth):
        self.auth=auth; self.db=auth.db; self.workspace=Workspace(auth)

    def _actor(self,c,token):
        actor,_=self.auth._resolve(c,token,full=True)
        if actor['role']!='master':raise AuthError('Operazione Master richiesta.',403)
        return actor

    @staticmethod
    def _metrics(c,tenant):
        row=c.execute("""SELECT count(*) leads,
          count(CASE WHEN lot_id IS NULL THEN 1 END) unattributed,
          count(CASE WHEN EXISTS(SELECT 1 FROM assessments a WHERE a.tenant_id=cl.tenant_id AND a.client_id=cl.id AND a.result_json!='{}') THEN 1 END) completed
          FROM clients cl WHERE tenant_id=? AND NOT EXISTS(SELECT 1 FROM privacy_erasures e WHERE e.tenant_id=cl.tenant_id AND e.client_id=cl.id)""",(tenant,)).fetchone()
        return {**dict(row),'qr':c.execute('SELECT count(*) FROM voucher_lots WHERE tenant_id=?',(tenant,)).fetchone()[0]}

    def studios(self,token):
        with self.db.transaction() as c:
            actor=self._actor(c,token)
            result=[]
            for row in c.execute("SELECT t.id tenant_id,t.slug,t.status tenant_status,p.business_name,p.first_name,p.last_name,p.office_city FROM tenants t JOIN consultant_profiles p ON p.tenant_id=t.id ORDER BY p.business_name,p.last_name").fetchall():
                result.append({**dict(row),**self._metrics(c,row['tenant_id'])})
            self.auth._event(c,actor['id'],'master_overview_read')
            return {'studios':result}

    def detail(self,token,tenant,reason,lot=None):
        with self.db.transaction() as c:
            self._actor(c,token)
            actor,t,reason=self.workspace._scope(c,token,tenant,reason)
            profile=c.execute('SELECT p.*,t.slug,t.status tenant_status FROM consultant_profiles p JOIN tenants t ON t.id=p.tenant_id WHERE p.tenant_id=?',(t,)).fetchone()
            if not profile:raise AuthError('Studio non disponibile.',404)
            if lot:
                self.workspace._get(c,t,'voucher_lots',lot)
                leads=[dict(r) for r in c.execute("""SELECT cl.id,cl.first_name,cl.last_name,cl.email,cl.mobile,cl.stage,cl.created_at,
                EXISTS(SELECT 1 FROM assessments a WHERE a.tenant_id=cl.tenant_id AND a.client_id=cl.id AND a.result_json!='{}') completed
                FROM clients cl WHERE cl.tenant_id=? AND cl.lot_id=? AND NOT EXISTS(SELECT 1 FROM privacy_erasures e WHERE e.tenant_id=cl.tenant_id AND e.client_id=cl.id) ORDER BY cl.created_at DESC""",(t,lot))]
                self.workspace._audit(c,actor,t,reason,'master_qr_leads_read',lot)
                return {'leads':leads}
            lots=[dict(r) for r in c.execute("""SELECT l.id,l.code,l.status,l.printed,l.created_at,COALESCE(ca.name,ci.name,'Voucher') label,
            CASE WHEN ca.id IS NULL THEN 'Voucher città' ELSE 'Campagna' END kind,
            (SELECT count(*) FROM clients cl WHERE cl.tenant_id=l.tenant_id AND cl.lot_id=l.id AND NOT EXISTS(SELECT 1 FROM privacy_erasures e WHERE e.tenant_id=cl.tenant_id AND e.client_id=cl.id)) leads
            FROM voucher_lots l LEFT JOIN campaigns ca ON ca.tenant_id=l.tenant_id AND ca.lot_id=l.id
            LEFT JOIN cities ci ON ci.tenant_id=l.tenant_id AND ci.id=l.city_id WHERE l.tenant_id=? ORDER BY l.created_at DESC""",(t,))]
            accounts=[dict(r) for r in c.execute("SELECT a.id,a.email,a.status,x.email_verified,(x.totp_encrypted IS NOT NULL) mfa_ready FROM accounts a JOIN auth_credentials x ON x.account_id=a.id WHERE a.tenant_id=? AND a.role='consultant'",(t,))]
            audit=[dict(r) for r in c.execute("SELECT e.action,e.reason,e.created_at,a.email operator FROM audit_events e JOIN accounts a ON a.id=e.actor_id WHERE e.tenant_id=? AND a.role='master' ORDER BY e.created_at DESC,e.rowid DESC LIMIT 50",(t,))]
            settings=c.execute('SELECT privacy_url,privacy_version FROM customer_settings WHERE tenant_id=?',(t,)).fetchone()
            self.workspace._audit(c,actor,t,reason,'master_studio_read')
            return {'profile':dict(profile),'metrics':self._metrics(c,t),'lots':lots,'accounts':accounts,'audit':audit,'privacy':dict(settings) if settings else {}}
