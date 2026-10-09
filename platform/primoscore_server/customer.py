"""Own-customer intake, drafts, immutable results and studio-specific appointments."""
import hashlib
import json
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import sqlite3
from urllib.parse import urlsplit
from .auth import AuthError,email_address,digest
from .database import now
from .repository import new_id,encode,view
from .workspace import Workspace,string,integer
from .notifications import queue
from .studio_privacy import automatic_notice
from primoscore_core.intake import clean_answers,complete_result


TEST_STUDIO_SLUG='studio-865b2fdabce3'
TEST_CONTACT={'first_name':'Test','last_name':'Primoscore','mobile':'0000000000','email':'info@primoscore.it'}

class Customer:
    def __init__(self,auth,*,local=False):self.auth=auth;self.db=auth.db;self.local=local;self.workspace=Workspace(auth)

    def event(self,c,t,client,kind):c.execute('INSERT INTO customer_events VALUES(?,?,?,?,?)',(new_id(),t,client,kind,now()))

    def _voucher(self,c,code):
        code=string(code,'voucher',80,True)
        lot=c.execute("SELECT l.*,t.slug FROM voucher_lots l JOIN tenants t ON l.tenant_id=t.id WHERE l.code=? AND l.status='active' AND t.status='active'",(code,)).fetchone()
        if not lot:raise AuthError('Voucher non disponibile.',404)
        campaign=c.execute('SELECT p.* FROM campaigns a JOIN partners p ON p.tenant_id=a.tenant_id AND p.id=a.partner_id WHERE a.tenant_id=? AND a.lot_id=?',(lot['tenant_id'],lot['id'])).fetchone()
        if campaign and campaign['status']!='active':raise AuthError('Voucher non disponibile.',404)
        return lot,campaign

    def _settings(self,c,t):
        row=c.execute('SELECT * FROM customer_settings WHERE tenant_id=?',(t,)).fetchone()
        result=dict(row) if row else dict(tenant_id=t,privacy_url='',privacy_version='',revision=0)
        profile=c.execute('SELECT controller_name,controller_email,controller_dpo,controller_address,office_address,office_postcode,office_city,office_province FROM consultant_profiles WHERE tenant_id=?',(t,)).fetchone()
        result.update(dict(profile) if profile else dict(controller_name='',controller_email=''))
        result['automatic_privacy'] = not result['privacy_url'] and not result['privacy_version']
        if profile:
            result['controller_address'] = profile['controller_address'] or f"{profile['office_address']}, {profile['office_postcode']} {profile['office_city']} ({profile['office_province']})"
        if not self.local and not result['privacy_url'] and not result['privacy_version']:
            notice = automatic_notice(c, t, self.auth.origin)
            if notice:
                result.update(notice)
        return result

    def _partners(self,c,lot):
        return [dict(r) for r in c.execute("SELECT p.id,p.name,p.category FROM partners p WHERE p.tenant_id=? AND p.city_id=? AND p.status='active' AND EXISTS(SELECT 1 FROM deliveries d WHERE d.tenant_id=p.tenant_id AND d.partner_id=p.id AND d.lot_id=?) ORDER BY p.name",(lot['tenant_id'],lot['city_id'],lot['id']))]

    def context(self,code):
        with self.db.transaction() as c:
            lot,campaign=self._voucher(c,code);t=lot['tenant_id'];settings=self._settings(c,t)
            profile=c.execute('SELECT first_name,last_name,business_name,email,mobile,oam_number FROM consultant_profiles WHERE tenant_id=?',(t,)).fetchone()
            city=c.execute('SELECT name FROM cities WHERE tenant_id=? AND id=?',(t,lot['city_id'])).fetchone()
            return dict(testing=lot['slug']==TEST_STUDIO_SLUG,test_contact=TEST_CONTACT if lot['slug']==TEST_STUDIO_SLUG else None,studio=lot['slug'],consultant=dict(profile),label=campaign['name'] if campaign else city['name'],campaign=bool(campaign),partners=[] if campaign else self._partners(c,lot),privacy_url=settings['privacy_url'],privacy_version=settings['privacy_version'],controller_name=settings['controller_name'],controller_email=settings['controller_email'],available=self.local or bool(settings['privacy_url'] and settings['privacy_version'] and settings['controller_name'] and settings['controller_email']),local=self.local)

    def _client(self,c,session,guest):
        if session:
            actor,_=self.auth._resolve(c,session,full=True)
            if actor['role']!='customer':raise AuthError('Accedi con il tuo account cliente.',403)
        else:
            if not isinstance(guest,str) or len(guest)>200:raise AuthError('Riprendi la valutazione dal tuo accesso.',401)
            row=c.execute('SELECT account_id FROM customer_guest_sessions WHERE token_hash=? AND expires_at>?',(digest(guest),self.auth.timestamp())).fetchone()
            actor=self.auth._credentials(c,row['account_id']) if row else None
            if not actor or actor['role']!='customer' or actor['status']!='pending' or actor['password_hash']:raise AuthError('Accedi per riprendere la valutazione.',401)
            if not c.execute("SELECT 1 FROM tenants WHERE id=? AND status='active'",(actor['tenant_id'],)).fetchone():raise AuthError('Studio non disponibile.',403)
        client=c.execute('SELECT * FROM clients WHERE tenant_id=? AND id=?',(actor['tenant_id'],actor['client_id'])).fetchone()
        if not client:raise AuthError('Richiesta non disponibile.',404)
        return actor,view(client)

    def acquire(self,d,guest='',ip=''):
        allowed={'code','first_name','last_name','email','mobile','partner_id','partner_other','privacy_accepted','service_requested','request_id','privacy_version','simulation_mode','residence_city'}
        if not isinstance(d,dict) or set(d)-allowed:raise AuthError('Richiesta non valida.')
        self.auth.rate('intake-ip',ip,8,3600)
        mode=d.get('simulation_mode','')
        if mode not in ('','score','max'):raise AuthError('Percorso non valido.')
        residence=string(d.get('residence_city',''),'città di residenza',100)
        first=string(d.get('first_name'),'nome',80,True);last=string(d.get('last_name'),'cognome',80,True);email=email_address(d.get('email'))
        if len(first)<2:raise AuthError('Inserisci il tuo nome completo.')
        mobile=string(d.get('mobile'),'cellulare',30,True)
        if not 9<=len(''.join(ch for ch in mobile if ch.isdigit()))<=15 or any(ch not in '+0123456789 ()-.' for ch in mobile):raise AuthError('Controlla il numero di cellulare.')
        if d.get('service_requested') is not True:raise AuthError('Conferma la richiesta di valutazione.')
        key=string(d.get('request_id'),'richiesta',128,True)
        if len(key)<16:raise AuthError('Ricarica la pagina e riprova.')
        fingerprint=hashlib.sha256(encode(d).encode()).hexdigest()
        with self.db.transaction() as c:
            lot,campaign=self._voucher(c,d.get('code'));t=lot['tenant_id'];settings=self._settings(c,t)
            if lot['slug']==TEST_STUDIO_SLUG and dict(first_name=first,last_name=last,mobile=mobile,email=email)!=TEST_CONTACT:
                raise AuthError('Questo QR è riservato al collaudo: usa il contatto di prova precompilato.')
            version=settings['privacy_version'] or ('local-test-only' if self.local else '')
            has_notice=bool(settings['privacy_url'] and settings['privacy_version'] and settings['controller_name'] and settings['controller_email']) or self.local
            if not has_notice:raise AuthError('Il consulente deve completare l’informativa prima di ricevere richieste.',403)
            if has_notice and d.get('privacy_accepted') is not True:raise AuthError('Conferma la lettura dell’informativa.')
            if not has_notice and d.get('privacy_accepted') is True:raise AuthError('Non è disponibile un’informativa da confermare.')
            if d.get('privacy_version')!=version:raise AuthError('L’informativa è cambiata. Ricarica la pagina.',409)
            previous=c.execute('SELECT * FROM customer_intakes WHERE tenant_id=? AND request_id=?',(t,key)).fetchone()
            if previous:
                actor,client=self._client(c,'',guest)
                if previous['client_id']!=client['id'] or t!=client['tenant_id'] or previous['fingerprint']!=fingerprint:raise AuthError('Richiesta già acquisita. Usa il tuo accesso per riprendere.',409)
                return {'ok':True,'studio':lot['slug']}
            existing_account=c.execute("SELECT 1 FROM accounts WHERE tenant_id=? AND role='customer' AND email=?",(t,email)).fetchone()
            if existing_account and lot['slug']==TEST_STUDIO_SLUG:
                return {'ok':True,'resume_required':True,'studio':lot['slug']}
            if existing_account:raise AuthError('Non è possibile creare un nuovo accesso con questi dati. Se hai già iniziato, usa Accedi o il recupero dell’accesso.',409)
            partner=None;label='Non ricordo'
            if campaign:partner=campaign['id'];label=campaign['name']
            else:
                choice=string(d.get('partner_id'),'partner',100,True)
                if choice=='other':label=string(d.get('partner_other'),'nome professionista',160,True)
                elif choice!='unknown':
                    p=next((p for p in self._partners(c,lot) if p['id']==choice),None)
                    if not p:raise AuthError('Seleziona il professionista nell’elenco.')
                    partner=p['id'];label=p['name']
            client=new_id();account=new_id();questionnaire=new_id()
            c.execute('INSERT INTO clients(tenant_id,id,partner_id,lot_id,first_name,last_name,email,mobile,residence_city,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(t,client,partner,lot['id'],first,last,email,mobile,residence,now()))
            c.execute('INSERT INTO questionnaires(tenant_id,id,client_id,created_at,simulation_mode) VALUES(?,?,?,?,?)',(t,questionnaire,client,now(),mode))
            c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)',(account,t,client,'customer',email,'pending',now()))
            c.execute('INSERT INTO auth_credentials(account_id,password_changed_at) VALUES(?,?)',(account,self.auth.timestamp()))
            c.execute('INSERT INTO customer_intakes(tenant_id,client_id,request_id,fingerprint,partner_label,privacy_version,privacy_url,accepted_at,source,privacy_acknowledged) VALUES(?,?,?,?,?,?,?,?,?,?)',(t,client,key,fingerprint,label,version,settings['privacy_url'],now(),'campaign_qr' if campaign else 'city_qr',int(has_notice)))
            token=secrets.token_urlsafe(32)
            c.execute('INSERT INTO customer_guest_sessions VALUES(?,?,?)',(digest(token),account,self.auth.timestamp()+86400))
            self.auth._mail_token(c,self.auth._credentials(c,account),'invite')
            from .crm import CRM
            CRM(self.auth)._client(c,t,client)
            self.event(c,t,client,'intake_created')
            queue(self.auth,c,t,client,'contact:'+client,'contact')
            if not has_notice:self.event(c,t,client,'intake_without_privacy_notice')
            return {'ok':True,'guest_token':token,'studio':lot['slug']}

    def _view(self,c,client):
        t=client['tenant_id'];ident=client['id']
        q=c.execute('SELECT * FROM questionnaires WHERE tenant_id=? AND client_id=?',(t,ident)).fetchone()
        if not q:raise AuthError('Questionario non disponibile.',404)
        result=c.execute('SELECT * FROM assessments WHERE tenant_id=? AND questionnaire_id=? AND answers_revision=? ORDER BY created_at DESC LIMIT 1',(t,q['id'],q['revision'])).fetchone()
        decoded_result=view(result)['result'] if result else None
        if decoded_result and decoded_result.get('simulationMode')=='max' and 'maximumPayment' not in decoded_result:
            saved=json.loads(result['answers_json']);income=decoded_result['metrics']['totalHouseholdIncome'];debts=saved['monthlyDebts']
            decoded_result['maximumPayment']=round(max(0,min(income*.5-debts,income-debts-decoded_result['threshold'])),2)
        attribution=c.execute('SELECT partner_label FROM customer_intakes WHERE tenant_id=? AND client_id=?',(t,ident)).fetchone()
        profile=c.execute('SELECT first_name,last_name,business_name,email,mobile FROM consultant_profiles WHERE tenant_id=?',(t,)).fetchone()
        slug=c.execute('SELECT slug FROM tenants WHERE id=?',(t,)).fetchone()[0]
        appointments=[view(r) for r in c.execute('SELECT * FROM appointments WHERE tenant_id=? AND client_id=? ORDER BY starts_at DESC',(t,ident))]
        return dict(testing=slug==TEST_STUDIO_SLUG,client=client,questionnaire=view(q),result=decoded_result or None,completed_at=result['created_at'] if result else None,appointments=appointments,consultant=dict(profile) if profile else {},studio=slug,partner_label=attribution[0] if attribution else 'Contatto dello studio')

    def get(self,session='',guest=''):
        with self.db.transaction() as c:
            actor,client=self._client(c,session,guest)
            data=self._view(c,client)
            data['assessment_completed']=bool(data['result'])
            row=c.execute('SELECT d.verified_at,d.email FROM customer_report_deliveries d JOIN assessments a ON a.id=d.assessment_id AND a.tenant_id=d.tenant_id WHERE d.account_id=? AND a.questionnaire_id=? AND a.answers_revision=? ORDER BY d.rowid DESC LIMIT 1',(actor['id'],data['questionnaire']['id'],data['questionnaire']['revision'])).fetchone()
            data['report_email']=row['email'] if row else client['email']
            if (not row or row['verified_at'] is None or row['email']!=client['email']):data['result']=None
            return {**data,'temporary_access':actor['status']=='pending','local':self.local}

    def save(self,d,session='',guest=''):
        if not isinstance(d,dict) or set(d)!={'answers','step','revision','residence_city'}:raise AuthError('Campi non validi.')
        try:answers=clean_answers(d['answers'])
        except ValueError as e:raise AuthError(str(e)) from e
        step=integer(d['step'],0,5);revision=integer(d['revision']);city=string(d['residence_city'],'città di residenza',100)
        with self.db.transaction() as c:
            _,client=self._client(c,session,guest);t=client['tenant_id']
            q=c.execute('SELECT * FROM questionnaires WHERE tenant_id=? AND client_id=?',(t,client['id'])).fetchone()
            if not q or q['revision']!=revision:raise AuthError('Le risposte sono cambiate in un’altra scheda. Ricarica prima di continuare.',409)
            c.execute('UPDATE questionnaires SET answers_json=?,current_step=?,revision=revision+1 WHERE tenant_id=? AND id=?',(encode(answers),step,t,q['id']))
            c.execute('UPDATE clients SET residence_city=?,revision=revision+1 WHERE tenant_id=? AND id=?',(city,t,client['id']))
            self.event(c,t,client['id'],'answers_saved')
            return {'revision':revision+1}

    def complete(self,d,session='',guest=''):
        if not isinstance(d,dict) or set(d)-{'revision','email'} or 'revision' not in d:raise AuthError('Richiesta non valida.')
        revision=integer(d['revision'])
        email=email_address(d.get('email') or self.get(session,guest)['client']['email'])
        # Calculate outside the write transaction (the core may consult ISTAT).
        before=self.get(session,guest);q=before['questionnaire'];client=before['client']
        if q['revision']!=revision:raise AuthError('Le risposte sono cambiate. Ricarica prima di calcolare.',409)
        self.auth.rate('customer-complete',client['tenant_id']+':'+client['id'],10,900)
        if not client['last_name'] or not client['residence_city']:raise AuthError('Completa cognome e città di residenza.')
        with self.db.transaction() as c:
            stored=c.execute('SELECT result_json FROM assessments WHERE tenant_id=? AND questionnaire_id=? AND answers_revision=? ORDER BY created_at DESC LIMIT 1',(client['tenant_id'],q['id'],revision)).fetchone()
        try:
            if stored:result=json.loads(stored['result_json'])
            elif q.get('simulation_mode'):
                from .public_simulation import evaluate
                _,result=evaluate(q['simulation_mode'],q['answers'])
            else:result=complete_result(q['answers'])
        except ValueError as e:raise AuthError(str(e)) from e
        with self.db.transaction() as c:
            actor,current=self._client(c,session,guest);t=current['tenant_id']
            if current['id']!=client['id']:raise AuthError('Accesso cambiato. Ricarica.',409)
            latest=c.execute('SELECT * FROM questionnaires WHERE tenant_id=? AND id=?',(t,q['id'])).fetchone()
            if latest['revision']!=revision or current['revision']!=client['revision']:raise AuthError('Le risposte sono cambiate durante il calcolo. Riprova.',409)
            existing=c.execute('SELECT id FROM assessments WHERE tenant_id=? AND questionnaire_id=? AND answers_revision=?',(t,q['id'],revision)).fetchone()
            if existing:return self._queue_report(c,actor,current,existing['id'],email)
            assessment=new_id()
            c.execute('INSERT INTO assessments(tenant_id,id,client_id,questionnaire_id,answers_revision,engine_version,answers_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(t,assessment,client['id'],q['id'],revision,result['engineVersion'],encode(q['answers']),encode(result),now()))
            c.execute('UPDATE questionnaires SET current_step=6 WHERE tenant_id=? AND id=?',(t,q['id']))
            self.event(c,t,client['id'],'assessment_completed')
            return self._queue_report(c,actor,current,assessment,email)


    def _queue_report(self,c,actor,client,assessment,email):
        t=client['tenant_id']
        if c.execute("SELECT 1 FROM accounts WHERE tenant_id=? AND role='customer' AND email=? AND id!=?",(t,email,actor['id'])).fetchone():
            raise AuthError('Indirizzo già associato a un altro accesso. Usa un altro indirizzo.',409)
        if c.execute('SELECT slug FROM tenants WHERE id=?',(t,)).fetchone()[0]==TEST_STUDIO_SLUG and email!=TEST_CONTACT['email']:
            raise AuthError('Usa l’indirizzo precompilato dello studio di collaudo.')
        c.execute('DELETE FROM customer_report_deliveries WHERE account_id=?',(actor['id'],))
        c.execute("UPDATE service_mail SET status='cancelled' WHERE recipient_account_id=? AND event_key LIKE 'report:%' AND status IN ('queued','failed')",(actor['id'],))
        token=secrets.token_urlsafe(32)
        c.execute('INSERT INTO customer_report_deliveries VALUES(?,?,?,?,?,?,?,?,NULL)',(digest(token),actor['id'],assessment,t,client['id'],email,actor['email'],self.auth.timestamp()+7*86400))
        payload={'purpose':'report','to':email,'url':self.auth.origin+'/cliente/report#token='+token}
        c.execute('INSERT INTO service_mail(id,tenant_id,client_id,event_key,payload_encrypted,created_at,recipient_account_id) VALUES(?,?,?,?,?,?,?)',(new_id(),t,client['id'],'report:'+digest(token),self.auth.cipher.encrypt(json.dumps(payload).encode()).decode(),self.auth.timestamp(),actor['id']))
        self.event(c,t,client['id'],'report_email_requested')
        return {'report_pending':True,'email':email,'delivery_status':'queued'}

    def report(self,token,*,verify=False):
        if not isinstance(token,str) or not 20<=len(token)<=200:raise AuthError('Collegamento non valido o scaduto.',403)
        with self.db.transaction() as c:
            row=c.execute("SELECT d.* FROM customer_report_deliveries d JOIN accounts a ON a.id=d.account_id JOIN tenants t ON t.id=d.tenant_id JOIN assessments r ON r.id=d.assessment_id AND r.tenant_id=d.tenant_id JOIN questionnaires q ON q.id=r.questionnaire_id AND q.tenant_id=r.tenant_id AND q.revision=r.answers_revision WHERE d.token_hash=? AND d.expires_at>? AND a.status IN ('pending','active') AND a.email=CASE WHEN d.verified_at IS NULL THEN d.original_email ELSE d.email END AND t.status='active'",(digest(token),self.auth.timestamp())).fetchone()
            if not row or (not verify and row['verified_at'] is None):raise AuthError('Collegamento non valido o scaduto. Richiedi una nuova email dalla tua area.',403)
            if verify and row['verified_at'] is None:
                if c.execute("SELECT 1 FROM accounts WHERE tenant_id=? AND role='customer' AND email=? AND id!=?",(row['tenant_id'],row['email'],row['account_id'])).fetchone():raise AuthError('Indirizzo già associato a un altro accesso.',409)
                actor=self.auth._credentials(c,row['account_id'])
                changed=actor['email']!=row['email']
                if changed:
                    c.execute('UPDATE accounts SET email=? WHERE id=?',(row['email'],row['account_id']))
                    c.execute('UPDATE clients SET email=?,revision=revision+1 WHERE tenant_id=? AND id=?',(row['email'],row['tenant_id'],row['client_id']))
                    c.execute('UPDATE auth_tokens SET used_at=? WHERE account_id=? AND used_at IS NULL',(self.auth.timestamp(),row['account_id']))
                    c.execute("UPDATE auth_mail SET status='cancelled' WHERE account_id=? AND status IN ('queued','failed')",(row['account_id'],))
                c.execute('UPDATE auth_credentials SET email_verified=1 WHERE account_id=?',(row['account_id'],))
                c.execute('UPDATE customer_report_deliveries SET verified_at=? WHERE token_hash=?',(self.auth.timestamp(),digest(token)))
                self.event(c,row['tenant_id'],row['client_id'],'report_email_verified')
                if changed and actor['status']=='pending':self.auth._mail_token(c,self.auth._credentials(c,row['account_id']),'invite')
            client=view(c.execute('SELECT * FROM clients WHERE tenant_id=? AND id=?',(row['tenant_id'],row['client_id'])).fetchone())
            return {**self._view(c,client),'assessment_completed':True,'report_email':row['email'],'temporary_access':False,'local':self.local}

    def restart_test(self,d,session='',guest=''):
        if not isinstance(d,dict) or set(d)!={'revision'}:raise AuthError('Richiesta non valida.')
        with self.db.transaction() as c:
            _,client=self._client(c,session,guest)
            t=client['tenant_id']
            if c.execute('SELECT slug FROM tenants WHERE id=?',(t,)).fetchone()[0]!=TEST_STUDIO_SLUG:raise AuthError('Operazione riservata allo studio di collaudo.',403)
            q=c.execute('SELECT * FROM questionnaires WHERE tenant_id=? AND client_id=?',(t,client['id'])).fetchone()
            if q['revision']!=integer(d['revision']):raise AuthError('La prova è cambiata in un’altra scheda. Ricarica.',409)
            c.execute("UPDATE questionnaires SET answers_json='{}',current_step=0,revision=revision+1 WHERE tenant_id=? AND id=?",(t,q['id']))
            self.event(c,t,client['id'],'test_restarted')
            return {'ok':True}

    def slots(self,session='',guest=''):
        with self.db.transaction() as c:
            _,client=self._client(c,session,guest)
            return [dict(r) for r in c.execute("SELECT s.id,s.starts_at,s.ends_at FROM booking_slots s WHERE s.tenant_id=? AND s.status='open' AND s.starts_at>? AND NOT EXISTS(SELECT 1 FROM appointments a WHERE a.tenant_id=s.tenant_id AND a.status='confirmed' AND a.starts_at<s.ends_at AND a.ends_at>s.starts_at) ORDER BY s.starts_at LIMIT 100",(client['tenant_id'],self.auth.timestamp()))]

    def book(self,d,session='',guest=''):
        if not isinstance(d,dict) or set(d)-{'slot_id','note'}:raise AuthError('Richiesta non valida.')
        slot_id=string(d.get('slot_id'),'orario',80,True);note=string(d.get('note',''),'nota',1000)
        try:
            with self.db.transaction() as c:
                _,client=self._client(c,session,guest);t=client['tenant_id']
                existing=c.execute("SELECT * FROM appointments WHERE tenant_id=? AND client_id=? AND status='confirmed'",(t,client['id'])).fetchone()
                if existing:
                    linked=c.execute('SELECT slot_id FROM customer_bookings WHERE tenant_id=? AND appointment_id=?',(t,existing['id'])).fetchone()
                    if linked and linked[0]==slot_id:return {'appointment':dict(existing)}
                    raise AuthError('Hai già un appuntamento confermato. Contatta il consulente per modificarlo.',409)
                slot=c.execute("SELECT * FROM booking_slots WHERE tenant_id=? AND id=? AND status='open' AND starts_at>?",(t,slot_id,self.auth.timestamp())).fetchone()
                if not slot:raise AuthError('Orario non disponibile.',409)
                ident=new_id()
                c.execute('INSERT INTO appointments(tenant_id,id,client_id,starts_at,ends_at,created_at) VALUES(?,?,?,?,?,?)',(t,ident,client['id'],slot['starts_at'],slot['ends_at'],now()))
                c.execute('INSERT INTO customer_bookings VALUES(?,?,?,?)',(t,ident,slot_id,note))
                from .crm import CRM
                CRM(self.auth).booked(c,t,client['id'])
                self.event(c,t,client['id'],'appointment_booked')
                queue(self.auth,c,t,client['id'],'booking:'+ident,'booking')
                queue(self.auth,c,t,client['id'],'booking:'+ident,'booking',customer=True)
                return {'appointment':dict(c.execute('SELECT * FROM appointments WHERE tenant_id=? AND id=?',(t,ident)).fetchone())}
        except sqlite3.IntegrityError as e:raise AuthError('Orario appena occupato. Scegline un altro.',409) from e

    def logout_guest(self,guest):
        with self.db.transaction() as c:c.execute('DELETE FROM customer_guest_sessions WHERE token_hash=?',(digest(guest),))

    def advisor(self,token,tenant=None,reason='',client_id=None):
        with self.db.transaction() as c:
            actor,t,reason=self.workspace._scope(c,token,tenant,reason)
            self.workspace._audit(c,actor,t,reason,'read_customer_area',client_id)
            if client_id:
                client=self.workspace._get(c,t,'clients',client_id)
                return self._view(c,client)
            clients=[dict(r) for r in c.execute('SELECT cl.*,q.current_step FROM clients cl LEFT JOIN questionnaires q ON q.tenant_id=cl.tenant_id AND q.client_id=cl.id WHERE cl.tenant_id=? ORDER BY cl.created_at DESC',(t,))]
            slots=[dict(r) for r in c.execute('SELECT * FROM booking_slots WHERE tenant_id=? ORDER BY starts_at',(t,))]
            appointments=[dict(r) for r in c.execute('SELECT a.*,b.note FROM appointments a LEFT JOIN customer_bookings b ON b.tenant_id=a.tenant_id AND b.appointment_id=a.id WHERE a.tenant_id=? ORDER BY starts_at',(t,))]
            return dict(clients=clients,slots=slots,appointments=appointments,settings=self._settings(c,t))

    def configure(self,token,d,tenant=None,reason=''):
        if not isinstance(d,dict) or set(d)-{'action','id','revision','starts_at','local_start','status','privacy_url','privacy_version','controller_name','controller_email','controller_address','controller_dpo'}:raise AuthError('Richiesta non valida.')
        action=d.get('action')
        with self.db.transaction() as c:
            actor,t,reason=self.workspace._scope(c,token,tenant,reason)
            if action=='privacy.auto':
                settings=self._settings(c,t)
                if not settings['automatic_privacy']:raise AuthError('Questo studio usa un’informativa esterna.')
                if settings['revision']!=integer(d.get('revision')):raise AuthError('Impostazioni aggiornate da un’altra scheda.',409)
                name=string(d.get('controller_name'),'titolare',250,True)
                email=email_address(d.get('controller_email'))
                address=string(d.get('controller_address'),'sede del titolare',250,True)
                dpo=string(d.get('controller_dpo',''),'DPO',250)
                c.execute('UPDATE consultant_profiles SET controller_name=?,controller_email=?,controller_address=?,controller_dpo=?,profile_revision=profile_revision+1 WHERE tenant_id=?',(name,email,address,dpo,t))
                c.execute("INSERT INTO customer_settings VALUES(?,'','',1) ON CONFLICT(tenant_id) DO UPDATE SET revision=customer_settings.revision+1",(t,))
                automatic_notice(c,t,self.auth.origin)
            elif action=='privacy':
                url=string(d.get('privacy_url'),'informativa',1000,True);version=string(d.get('privacy_version'),'versione informativa',100,True);parsed=urlsplit(url)
                if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise AuthError('Inserisci il collegamento HTTPS all’informativa del tuo studio.')
                settings=self._settings(c,t)
                controller_name=string(d.get('controller_name',settings['controller_name']),'titolare dello studio',250,True)
                controller_email=email_address(d.get('controller_email',settings['controller_email']))
                if settings['revision']!=integer(d.get('revision')):raise AuthError('Impostazioni aggiornate da un’altra scheda.',409)
                c.execute('INSERT INTO customer_settings VALUES(?,?,?,1) ON CONFLICT(tenant_id) DO UPDATE SET privacy_url=excluded.privacy_url,privacy_version=excluded.privacy_version,revision=customer_settings.revision+1',(t,url,version))
                c.execute('UPDATE consultant_profiles SET controller_name=?,controller_email=?,profile_revision=profile_revision+1 WHERE tenant_id=?',(controller_name,controller_email,t))
            elif action=='slot.add':
                start=d.get('starts_at')
                if 'local_start' in d:
                    value=string(d['local_start'],'orario italiano',16,True)
                    try:
                        naive=datetime.strptime(value,'%Y-%m-%dT%H:%M')
                        local_time=naive.replace(tzinfo=ZoneInfo('Europe/Rome'))
                        if local_time.astimezone(timezone.utc).astimezone(ZoneInfo('Europe/Rome')).replace(tzinfo=None)!=naive or local_time.utcoffset()!=local_time.replace(fold=1).utcoffset():raise ValueError()
                        start=int(local_time.timestamp())
                    except ValueError:raise AuthError('L’orario italiano è inesistente o ambiguo per il cambio dell’ora.')
                start=integer(start,self.auth.timestamp()+60,self.auth.timestamp()+366*86400)
                # Explicit consultant availability only. No imported/default calendar.
                overlap=c.execute("SELECT 1 FROM booking_slots WHERE tenant_id=? AND status='open' AND starts_at<? AND ends_at>?",(t,start+3600,start)).fetchone()
                if overlap:raise AuthError('Un orario disponibile si sovrappone a questo.',409)
                previous=c.execute('SELECT * FROM booking_slots WHERE tenant_id=? AND starts_at=?',(t,start)).fetchone()
                if previous:c.execute("UPDATE booking_slots SET status='open',revision=revision+1 WHERE tenant_id=? AND id=?",(t,previous['id']))
                else:c.execute('INSERT INTO booking_slots(tenant_id,id,starts_at,ends_at,created_at) VALUES(?,?,?,?,?)',(t,new_id(),start,start+3600,now()))
            elif action in ('slot.close','appointment.status'):
                table='booking_slots' if action=='slot.close' else 'appointments';ident=string(d.get('id'),'identificativo',80,True);row=self.workspace._get(c,t,table,ident);self.workspace._rev(row,d)
                status='closed' if action=='slot.close' else d.get('status')
                if action!='slot.close' and status not in ('cancelled','completed'):raise AuthError('Stato non valido.')
                if table=='appointments' and row['status']!='confirmed':raise AuthError('Appuntamento già concluso.',409)
                c.execute('UPDATE '+table+' SET status=?,revision=revision+1 WHERE tenant_id=? AND id=?',(status,t,ident))
                if table=='appointments':queue(self.auth,c,t,row['client_id'],'appointment:'+ident+':'+status,'appointment_changed',customer=True)
            else:raise AuthError('Operazione non disponibile.')
            self.workspace._audit(c,actor,t,reason,'customer_'+action)
            return {'ok':True}
