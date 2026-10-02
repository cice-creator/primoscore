"""Appointment-focused CRM. Draft generation is deterministic and never sends mail."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from .auth import AuthError
from .workspace import Workspace,string,integer
from .repository import new_id
from .database import now
STAGES=('new','contact','no_answer','conversation','proposed','booked','completed','later','lost','do_not_contact')
PURPOSES={'first','no_answer','followup','proposal','confirmation','reminder','reschedule'}
class CRM:
    def __init__(self,auth):self.auth=auth;self.db=auth.db;self.workspace=Workspace(auth)
    def _scope(self,c,token,tenant,reason):return self.workspace._scope(c,token,tenant,reason)
    def _client(self,c,t,ident):
        client=self.workspace._get(c,t,'clients',ident)
        if c.execute('SELECT 1 FROM privacy_erasures WHERE tenant_id=? AND client_id=?',(t,ident)).fetchone():raise AuthError('Contatto anonimizzato.',404)
        c.execute('INSERT OR IGNORE INTO crm_leads(tenant_id,client_id,due_at) VALUES(?,?,?)',(t,ident,self.auth.timestamp()))
        return client,dict(c.execute('SELECT * FROM crm_leads WHERE tenant_id=? AND client_id=?',(t,ident)).fetchone())
    def _event(self,c,t,ident,kind,body):c.execute('INSERT INTO crm_events VALUES(?,?,?,?,?,?)',(t,ident,new_id(),kind,body,now()))
    def snapshot(self,token,tenant=None,reason='',ident=None):
        with self.db.transaction() as c:
            actor,t,reason=self._scope(c,token,tenant,reason)
            for r in c.execute('SELECT id FROM clients cl WHERE tenant_id=? AND NOT EXISTS(SELECT 1 FROM privacy_erasures e WHERE e.tenant_id=cl.tenant_id AND e.client_id=cl.id)',(t,)).fetchall():self._client(c,t,r['id'])
            rows=[dict(r) for r in c.execute("""SELECT cl.*,r.stage crm_stage,r.priority,r.next_action,r.due_at,r.owner,r.revision crm_revision,
                COALESCE(l.code,'Senza QR') qr,COALESCE(ca.name,ci.name,'Contatto diretto') source,
                COALESCE(p.name,'Non indicato') partner,
                EXISTS(SELECT 1 FROM assessments a WHERE a.tenant_id=cl.tenant_id AND a.client_id=cl.id AND a.result_json!='{}') evaluated
                FROM clients cl JOIN crm_leads r ON r.tenant_id=cl.tenant_id AND r.client_id=cl.id
                LEFT JOIN voucher_lots l ON l.tenant_id=cl.tenant_id AND l.id=cl.lot_id
                LEFT JOIN campaigns ca ON ca.tenant_id=l.tenant_id AND ca.lot_id=l.id
                LEFT JOIN cities ci ON ci.tenant_id=l.tenant_id AND ci.id=l.city_id
                LEFT JOIN partners p ON p.tenant_id=cl.tenant_id AND p.id=cl.partner_id
                WHERE cl.tenant_id=? AND NOT EXISTS(SELECT 1 FROM privacy_erasures e WHERE e.tenant_id=cl.tenant_id AND e.client_id=cl.id) ORDER BY cl.created_at DESC""",(t,))]
            appointments=[dict(r) for r in c.execute('SELECT * FROM appointments WHERE tenant_id=? ORDER BY starts_at',(t,))]
            for row in rows:
                active=[a for a in appointments if a['client_id']==row['id'] and a['status']=='confirmed' and a['starts_at']>self.auth.timestamp()]
                if active and row['crm_stage'] not in ('do_not_contact','lost'):row['crm_stage']='booked'
            self.workspace._audit(c,actor,t,reason,'read_crm',ident)
            profile=dict(c.execute('SELECT business_name,first_name,last_name,email,mobile FROM consultant_profiles WHERE tenant_id=?',(t,)).fetchone())
            if not ident:return {'leads':rows,'appointments':appointments,'studio':profile,'time':self.auth.timestamp()}
            client,state=self._client(c,t,ident)
            return {'lead':next(r for r in rows if r['id']==ident),'events':[dict(r) for r in c.execute('SELECT * FROM crm_events WHERE tenant_id=? AND client_id=? ORDER BY created_at DESC',(t,ident))],'drafts':[dict(r) for r in c.execute('SELECT * FROM crm_drafts WHERE tenant_id=? AND client_id=? ORDER BY created_at DESC',(t,ident))],'appointments':[a for a in appointments if a['client_id']==ident],'studio':profile,'slots':[dict(r) for r in c.execute("SELECT * FROM booking_slots WHERE tenant_id=? AND status='open' AND starts_at>? AND NOT EXISTS(SELECT 1 FROM appointments a WHERE a.tenant_id=booking_slots.tenant_id AND a.status='confirmed' AND a.starts_at<booking_slots.ends_at AND a.ends_at>booking_slots.starts_at) ORDER BY starts_at LIMIT 20",(t,self.auth.timestamp()))]}
    def command(self,token,ident,d,tenant=None,reason=''):
        if not isinstance(d,dict):raise AuthError('Richiesta non valida.')
        action=d.get('action')
        with self.db.transaction() as c:
            actor,t,reason=self._scope(c,token,tenant,reason);client,state=self._client(c,t,ident)
            if integer(d.get('revision'))!=state['revision']:raise AuthError('Scheda aggiornata in un’altra finestra. Ricarica.',409)
            if action=='update':
                stage=d.get('stage');priority=d.get('priority')
                if stage not in STAGES or priority not in ('high','normal','low'):raise AuthError('Stato o priorità non validi.')
                if stage in ('booked','completed') and not c.execute('SELECT 1 FROM appointments WHERE tenant_id=? AND client_id=? AND status=?', (t,ident,'confirmed' if stage=='booked' else 'completed')).fetchone():raise AuthError('Prima registra l’appuntamento nella scheda.')
                task=string(d.get('next_action'),'prossima azione',250);owner=string(d.get('owner'),'referente',120)
                due=integer(d['due_at'],maximum=4102444800) if d.get('due_at') is not None else None
                if stage in ('lost','do_not_contact','booked','completed'):task='';due=None
                elif not task or due is None:raise AuthError('Indica la prossima azione e la scadenza.')
                c.execute('UPDATE crm_leads SET stage=?,priority=?,next_action=?,due_at=?,owner=? WHERE tenant_id=? AND client_id=?',(stage,priority,task,due,owner,t,ident))
                self._event(c,t,ident,'update','Percorso e prossima azione aggiornati.')
            elif action=='contact':
                kind=d.get('kind');outcome=d.get('outcome');note=string(d.get('note'),'nota',3000)
                if kind not in ('phone','whatsapp','email','note') or outcome not in ('no_answer','conversation','later','lost','do_not_contact','note'):raise AuthError('Esito non valido.')
                if state['stage']=='do_not_contact' and kind!='note':raise AuthError('Il lead è segnato come non contattabile.')
                self._event(c,t,ident,kind,outcome+' · '+note)
                if outcome!='note':
                    due=None if outcome in ('lost','do_not_contact') else integer(d.get('due_at'),maximum=4102444800)
                    task='' if due is None else string(d.get('next_action'),'prossima azione',250,True)
                    c.execute('UPDATE crm_leads SET stage=?,next_action=?,due_at=? WHERE tenant_id=? AND client_id=?',(outcome,task,due,t,ident))
            elif action=='draft':
                if state['stage'] in ('do_not_contact','lost'):raise AuthError('Riattiva esplicitamente il lead prima di preparare un contatto.')
                channel=d.get('channel');purpose=d.get('purpose')
                if channel not in ('whatsapp','email') or purpose not in PURPOSES:raise AuthError('Bozza non valida.')
                existing=c.execute("SELECT id FROM crm_drafts WHERE tenant_id=? AND client_id=? AND channel=? AND purpose=? AND status='draft'",(t,ident,channel,purpose)).fetchone()
                if existing:raise AuthError('Esiste già una bozza di questo tipo: modifica quella disponibile.',409)
                studio=c.execute('SELECT * FROM consultant_profiles WHERE tenant_id=?',(t,)).fetchone();signature=studio['first_name']+' '+studio['last_name']+' · '+(studio['business_name'] or 'Primoscore')
                future=c.execute("SELECT * FROM appointments WHERE tenant_id=? AND client_id=? AND status='confirmed' AND starts_at>? ORDER BY starts_at LIMIT 1",(t,ident,self.auth.timestamp())).fetchone()
                slots=c.execute("SELECT * FROM booking_slots s WHERE tenant_id=? AND status='open' AND starts_at>? AND NOT EXISTS(SELECT 1 FROM appointments a WHERE a.tenant_id=s.tenant_id AND a.status='confirmed' AND a.starts_at<s.ends_at AND a.ends_at>s.starts_at) ORDER BY starts_at LIMIT 2",(t,self.auth.timestamp())).fetchall()
                when=lambda ts:datetime.fromtimestamp(ts,ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y alle %H:%M')
                if purpose in ('confirmation','reminder') and not future:raise AuthError('Prima fissa un appuntamento futuro.')
                messages={'first':'Hai lasciato una richiesta tramite Primoscore. Mi farebbe piacere ascoltare il tuo progetto: quando preferisci sentirci per concordare un appuntamento?',
                'no_answer':'Ho provato a chiamarti per la tua richiesta su Primoscore. Qual è un momento comodo per sentirci?',
                'followup':'Ti ricontatto per la tua richiesta su Primoscore. Se vuoi, possiamo fissare un incontro per parlarne insieme. Quando ti sarebbe comodo?',
                'proposal':('Per il nostro incontro posso proporti '+ ' oppure '.join(when(s['starts_at']) for s in slots)+'. Quale preferisci? Gli orari sono da confermare.' if slots else 'Vorrei proporti un incontro. Quali giorni e orari preferisci?'),
                'confirmation':'Ti confermo il nostro appuntamento del '+(when(future['starts_at']) if future else '')+'. Se hai bisogno di modificarlo, rispondimi.',
                'reminder':'Ti ricordo il nostro appuntamento del '+(when(future['starts_at']) if future else '')+'. Ti aspetto! Se hai un imprevisto, avvisami.',
                'reschedule':'Non siamo riusciti a incontrarci. Vuoi concordare un nuovo appuntamento? Indicami un momento comodo per te.'}
                body='Ciao '+client['first_name']+',\n\n'+messages[purpose]+'\n\n'+signature
                c.execute('INSERT INTO crm_drafts(tenant_id,client_id,id,channel,purpose,subject,body,created_at) VALUES(?,?,?,?,?,?,?,?)',(t,ident,new_id(),channel,purpose,'Il tuo appuntamento · '+(studio['business_name'] or 'Primoscore') if channel=='email' else '',body,now()))
                self._event(c,t,ident,'draft','Bozza '+channel+' preparata. Nessun invio.')
            elif action=='save_draft':
                draft=self.workspace._get(c,t,'crm_drafts',d.get('id'))
                if draft['client_id']!=ident:raise AuthError('Bozza non disponibile.',404)
                if draft['revision']!=integer(d.get('draft_revision')) or draft['status']!='draft':raise AuthError('Bozza aggiornata o già inviata.',409)
                sent=d.get('sent') is True
                if sent and state['stage']=='do_not_contact':raise AuthError('Lead non contattabile.')
                body=string(d.get('body'),'testo',5000,True);subject=string(d.get('subject'),'oggetto',250)
                c.execute('UPDATE crm_drafts SET body=?,subject=?,status=?,revision=revision+1 WHERE tenant_id=? AND id=?',(body,subject,'sent_manual' if sent else 'draft',t,draft['id']))
                if sent:self._event(c,t,ident,'sent_manual','Invio '+draft['channel']+' dichiarato dal consulente. Il sistema non ha inviato il messaggio.')
            elif action=='appointment':
                if state['stage']=='do_not_contact':raise AuthError('Lead non contattabile.')
                starts=integer(d.get('starts_at'),maximum=4102444800)
                if starts<=self.auth.timestamp():raise AuthError('Scegli un orario futuro.')
                if c.execute("SELECT 1 FROM appointments WHERE tenant_id=? AND client_id=? AND status='confirmed' AND starts_at>?",(t,ident,self.auth.timestamp())).fetchone():raise AuthError('Il lead ha già un appuntamento futuro.',409)
                try:c.execute('INSERT INTO appointments(tenant_id,id,client_id,starts_at,ends_at,created_at) VALUES(?,?,?,?,?,?)',(t,new_id(),ident,starts,starts+3600,now()))
                except sqlite3.IntegrityError as e:raise AuthError('Orario già occupato.',409) from e
                self._event(c,t,ident,'appointment','Appuntamento fissato: '+datetime.fromtimestamp(starts,ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y %H:%M'))
                c.execute("UPDATE crm_leads SET stage='booked',next_action='',due_at=NULL WHERE tenant_id=? AND client_id=?",(t,ident))
            elif action=='appointment_status':
                appointment=self.workspace._get(c,t,'appointments',d.get('id'));status=d.get('status')
                if appointment['client_id']!=ident or status not in ('completed','cancelled'):raise AuthError('Appuntamento non valido.')
                if appointment['status']!='confirmed':raise AuthError('Appuntamento già aggiornato.',409)
                if status=='completed' and appointment['starts_at']>self.auth.timestamp():raise AuthError('L’appuntamento non si è ancora svolto.')
                c.execute('UPDATE appointments SET status=?,revision=revision+1 WHERE tenant_id=? AND id=?',(status,t,appointment['id']))
                c.execute('UPDATE crm_leads SET stage=?,next_action=?,due_at=? WHERE tenant_id=? AND client_id=?',('completed' if status=='completed' else 'contact','' if status=='completed' else 'Riprogrammare appuntamento',None if status=='completed' else self.auth.timestamp()+86400,t,ident))
                self._event(c,t,ident,'appointment', 'Appuntamento svolto' if status=='completed' else 'Appuntamento annullato; ricontatto programmato')
            else:raise AuthError('Operazione non disponibile.')
            c.execute('UPDATE crm_leads SET revision=revision+1 WHERE tenant_id=? AND client_id=?',(t,ident))
            self.workspace._audit(c,actor,t,reason,'crm_'+action,ident)
            return {'ok':True}
