"""Voucher and Sviluppo commands: authorization and writes share one transaction.

Only verified sessions reach this boundary. No seeds or CiceroEV runtime imports.
"""
import csv
from datetime import date
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
from .auth import AuthError
from .database import now
from .repository import encode, new_id, view

CATEGORIES = ['Agenzie immobiliari','Commercialisti','CAF o similari','Consulenti finanziari']
STATUSES = ['Da contattare','Contattato','Incontro fissato','Incontro svolto','Da richiamare','Partner attivo','Non interessato','Non idoneo']
DEFAULTS = dict(referent='',phone='',mobile='',email='',address='',priority='Media',status='Da contattare',firstContact='',outcome='',nextAction='',nextDate='',notes='',leads=0,qualifiedLeads=0,verification='Da verificare',researchDate='',source='',sourceNotes='')
TABLES = ('cities','partners','voucher_lots','campaigns','deliveries','activities','print_runs')


def string(value, label, limit=10000, required=False):
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):
        raise AuthError('Campo non valido: '+label)
    return unicodedata.normalize('NFC',value.strip())


def integer(value, minimum=0, maximum=1000000):
    if type(value) is not int or not minimum<=value<=maximum:
        raise AuthError('Quantità non valida.')
    return value


def day(value, *, future=True, required=False):
    value=string(value,'data',10,required)
    if not value:return ''
    try:
        parsed=date.fromisoformat(value)
        if parsed.isoformat()!=value or (not future and parsed>date.today()):raise ValueError()
    except ValueError:raise AuthError('Inserisci una data valida'+(' non futura.' if not future else '.'))
    return value


def details(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise AuthError('Dettagli del partner non validi.')
    result={**DEFAULTS,**value}
    for key in result:
        result[key]=integer(result[key]) if key in ('leads','qualifiedLeads') else string(result[key],key)
    if result['priority'] not in ('Alta','Media','Bassa') or result['status'] not in STATUSES:raise AuthError('Priorità o stato non validi.')
    if result['qualifiedLeads']>result['leads']:raise AuthError('I contatti qualificati non possono superare i contatti totali.')
    for key in ('firstContact','researchDate'):result[key]=day(result[key],future=False)
    result['nextDate']=day(result['nextDate'])
    if result['nextDate'] and not result['nextAction']:raise AuthError('Indica la prossima azione per la scadenza.')
    if result['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',result['email']):raise AuthError('Email del partner non valida.')
    return result


class Workspace:
    def __init__(self, auth):self.auth=auth;self.db=auth.db

    def _scope(self,c,token,tenant=None,reason=''):
        actor,_=self.auth._resolve(c,token,full=True)
        if actor['role']=='customer':raise AuthError('Area riservata ai consulenti.',403)
        if actor['role']=='master':
            reason=string(reason,'motivo accesso Master',500,True)
            if not tenant or not c.execute('SELECT 1 FROM tenants WHERE id=?',(tenant,)).fetchone():raise AuthError('Studio non disponibile.',404)
        else:
            if tenant and tenant!=actor['tenant_id']:raise AuthError('Accesso non autorizzato.',403)
            tenant=actor['tenant_id'];reason=''
        return actor,tenant,reason

    def _audit(self,c,actor,tenant,reason,action,ident=None):
        c.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)',(new_id(),tenant,actor['id'],action,'workspace',ident,reason,now()))

    @staticmethod
    def _get(c,tenant,table,ident):
        row=c.execute('SELECT * FROM '+table+' WHERE tenant_id=? AND id=?',(tenant,ident)).fetchone()
        if not row:raise AuthError('Registrazione non disponibile.',404)
        return view(row)

    @staticmethod
    def _insert(c,tenant,table,values):
        values={'tenant_id':tenant,'id':new_id(),**values,'created_at':now()}
        c.execute('INSERT INTO '+table+'('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',list(values.values()))
        return values['id']

    @staticmethod
    def _rev(record,data):
        if type(data.get('revision')) is not int or data['revision']!=record['revision']:raise AuthError('Questi dati sono cambiati. Ricarica la pagina prima di riprovare.',409)

    @staticmethod
    def _unique_name(c,tenant,table,name,exclude=None):
        for row in c.execute('SELECT id,name FROM '+table+' WHERE tenant_id=?',(tenant,)):
            if row['id']!=exclude and unicodedata.normalize('NFC',row['name']).casefold()==name.casefold():
                raise AuthError('Questo nome è già presente nello studio.',409)

    def _lot(self,c,tenant,city=None):
        return self._insert(c,tenant,'voucher_lots',dict(city_id=city,code='PS-'+new_id().replace('-','').upper(),printed=0))

    def snapshot(self,token,tenant=None,reason=''):
        with self.db.transaction() as c:
            actor,tenant,reason=self._scope(c,token,tenant,reason)
            result={table:[view(row) for row in c.execute('SELECT * FROM '+table+' WHERE tenant_id=? ORDER BY created_at,id',(tenant,))] for table in TABLES}
            profile=c.execute('SELECT business_name,first_name,last_name FROM consultant_profiles WHERE tenant_id=?',(tenant,)).fetchone()
            result.update(tenant_id=tenant,studio=(profile['business_name'] or profile['first_name']+' '+profile['last_name']) if profile else 'Studio',role=actor['role'],categories=CATEGORIES,statuses=STATUSES)
            for lot in result['voucher_lots']:
                lot['delivered']=sum(d['quantity'] for d in result['deliveries'] if d['lot_id']==lot['id'])
                lot['available']=lot['printed']-lot['delivered']
            counts={row['partner_id']:row['n'] for row in c.execute('SELECT partner_id,COUNT(*) n FROM clients WHERE tenant_id=? GROUP BY partner_id',(tenant,))}
            for partner in result['partners']:partner['registered_clients']=counts.get(partner['id'],0)
            if actor['role']=='master':self._audit(c,actor,tenant,reason,'read_workspace')
            return result

    def command(self,token,payload,tenant=None,reason=''):
        if not isinstance(payload,dict) or set(payload)!={'action','data','request_id'} or not isinstance(payload['data'],dict):raise AuthError('Richiesta non valida.')
        request_id=string(payload['request_id'],'identificativo richiesta',128,True)
        if len(request_id)<16:raise AuthError('Identificativo richiesta non valido.')
        action=string(payload['action'],'azione',40,True);data=payload['data']
        fingerprint=hashlib.sha256(encode({'action':action,'data':data}).encode()).hexdigest()
        try:
            with self.db.transaction() as c:
                actor,tenant,reason=self._scope(c,token,tenant,reason)
                old=c.execute('SELECT * FROM workspace_receipts WHERE tenant_id=? AND request_id=?',(tenant,request_id)).fetchone()
                if old:
                    if old['fingerprint']!=fingerprint or old['actor_id']!=actor['id']:raise AuthError('Identificativo richiesta già utilizzato.',409)
                    return json.loads(old['result_json'])
                result=self._apply(c,tenant,action,data)
                self._audit(c,actor,tenant,reason,action,result.get('id'))
                c.execute('INSERT INTO workspace_receipts VALUES(?,?,?,?,?,?)',(tenant,request_id,actor['id'],fingerprint,encode(result),now()))
                return result
        except sqlite3.IntegrityError as error:
            raise AuthError('Operazione annullata: disponibilità insufficiente, nome già presente o collegamenti non validi.',409) from error

    def _apply(self,c,t,action,d):
        allowed={
            'city.create':{'name'},'campaign.create':{'name','kind'},
            'partner.create':{'name','city_id','category','details'},'partner.update':{'id','revision','name','city_id','category','details'},
            'partner.status':{'id','revision','status'},'lot.status':{'id','revision','status'},
            'print.add':{'lot_id','quantity','printed_on','notes'},
            'delivery.create':{'partner_id','quantity','delivered_on','recipient','notes'},
            'delivery.update':{'id','revision','quantity','delivered_on','recipient','notes'},'delivery.delete':{'id','revision'},
            'activity.create':{'partner_id','revision','kind','occurred_on','outcome','notes','nextAction','nextDate','status'}}
        if action not in allowed or set(d)-allowed[action]:raise AuthError('Operazione o campi non consentiti.')
        for key,value in d.items():
            if key in ('revision','quantity'):integer(value)
            elif key=='details':
                if not isinstance(value,dict):raise AuthError('Dettagli non validi.')
            elif key=='city_id' and value is None:pass
            else:string(value,key)
        if action=='city.create':
            name=string(d.get('name'),'città',120,True)
            self._unique_name(c,t,'cities',name)
            ident=self._insert(c,t,'cities',{'name':name})
            return {'id':ident,'lot_id':self._lot(c,t,ident)}
        if action=='campaign.create':
            name=string(d.get('name'),'nome campagna',250,True);kind=d.get('kind')
            if kind not in ('company','website','flyer','other'):raise AuthError('Tipo di campagna non valido.')
            self._unique_name(c,t,'campaigns',name)
            partner=self._insert(c,t,'partners',dict(name=name,category={'company':'Azienda','website':'Sito web','flyer':'Volantino','other':'Altro'}[kind],details_json=encode(DEFAULTS)))
            lot=self._lot(c,t)
            ident=self._insert(c,t,'campaigns',dict(name=name,kind=kind,partner_id=partner,lot_id=lot))
            return {'id':ident,'partner_id':partner,'lot_id':lot}
        if action in ('partner.create','partner.update'):
            old=self._get(c,t,'partners',d.get('id')) if action.endswith('update') else None
            if old:self._rev(old,d)
            if old and old['status']=='archived':raise AuthError('Ripristina il partner prima di modificarlo.')
            city=d.get('city_id') or None
            campaign=c.execute('SELECT id FROM campaigns WHERE tenant_id=? AND partner_id=?',(t,old['id'])).fetchone() if old else None
            if campaign:
                if city:raise AuthError('Una campagna dedicata non può essere associata a una città.')
            else:self._get(c,t,'cities',city)
            values=dict(name=string(d.get('name'),'nome partner',250,True),city_id=city,category=string(d.get('category'),'categoria',100,True),details_json=encode(details(d.get('details',{}))))
            if campaign:self._unique_name(c,t,'campaigns',values['name'],campaign['id'])
            if old:
                c.execute('UPDATE partners SET name=?,city_id=?,category=?,details_json=?,revision=revision+1 WHERE tenant_id=? AND id=?',(*values.values(),t,old['id']))
                if campaign:c.execute('UPDATE campaigns SET name=?,revision=revision+1 WHERE tenant_id=? AND id=?',(values['name'],t,campaign['id']))
                return {'id':old['id']}
            return {'id':self._insert(c,t,'partners',values)}
        if action in ('partner.status','lot.status'):
            table='partners' if action.startswith('partner') else 'voucher_lots';old=self._get(c,t,table,d.get('id'));self._rev(old,d);status=d.get('status')
            if table=='partners':
                if status=='restore' and old['status']=='archived':status=old['archived_from'] or 'active'
                elif old['status']=='archived':raise AuthError('Usa il ripristino del partner.')
                if status not in ('active','inactive','archived'):raise AuthError('Stato non valido.')
                c.execute('UPDATE partners SET status=?,archived_from=?,revision=revision+1 WHERE tenant_id=? AND id=?',(status,old['status'] if status=='archived' else None,t,old['id']))
            else:
                if status not in ('active','inactive'):raise AuthError('Stato non valido.')
                c.execute('UPDATE voucher_lots SET status=?,revision=revision+1 WHERE tenant_id=? AND id=?',(status,t,old['id']))
            return {'id':old['id']}
        if action=='print.add':
            lot=self._get(c,t,'voucher_lots',d.get('lot_id'))
            if lot['status']!='active':raise AuthError('Riattiva il voucher prima di registrare una stampa.')
            quantity=integer(d.get('quantity'),1,100000)
            ident=self._insert(c,t,'print_runs',dict(lot_id=lot['id'],quantity=quantity,printed_on=day(d.get('printed_on'),future=False,required=True),notes=string(d.get('notes',''),'note')))
            c.execute('UPDATE voucher_lots SET printed=printed+?,revision=revision+1 WHERE tenant_id=? AND id=?',(quantity,t,lot['id']))
            return {'id':ident}
        if action.startswith('delivery.'):
            old=None
            if action!='delivery.create':old=self._get(c,t,'deliveries',d.get('id'));self._rev(old,d)
            if action=='delivery.delete':
                c.execute('DELETE FROM deliveries WHERE tenant_id=? AND id=?',(t,old['id']));return {'id':old['id']}
            partner=self._get(c,t,'partners',old['partner_id'] if old else d.get('partner_id'))
            if partner['status']!='active':raise AuthError('Il partner deve essere attivo per registrare una consegna.')
            if old:lot=self._get(c,t,'voucher_lots',old['lot_id'])
            else:
                campaign=c.execute('SELECT lot_id FROM campaigns WHERE tenant_id=? AND partner_id=?',(t,partner['id'])).fetchone()
                row=c.execute('SELECT id FROM voucher_lots WHERE tenant_id=? AND city_id=?',(t,partner['city_id'])).fetchone() if not campaign else None
                if not campaign and not row:raise AuthError('Città senza voucher disponibile.')
                lot=self._get(c,t,'voucher_lots',campaign['lot_id'] if campaign else row['id'])
            if lot['status']!='active':raise AuthError('Voucher sospeso.')
            values=dict(quantity=integer(d.get('quantity'),1,100000),delivered_on=day(d.get('delivered_on'),future=False,required=True),recipient=string(d.get('recipient',''),'destinatario',250),notes=string(d.get('notes',''),'note'))
            if old:
                c.execute('UPDATE deliveries SET quantity=?,delivered_on=?,recipient=?,notes=?,revision=revision+1 WHERE tenant_id=? AND id=?',(*values.values(),t,old['id']));return {'id':old['id']}
            return {'id':self._insert(c,t,'deliveries',dict(lot_id=lot['id'],partner_id=partner['id'],**values))}
        if action=='activity.create':
            partner=self._get(c,t,'partners',d.get('partner_id'));self._rev(partner,d)
            if partner['status']=='archived':raise AuthError('Ripristina il partner prima di aggiungere attività.')
            changed={**partner['details']}
            for key in ('nextAction','nextDate','status','outcome'):
                if key in d:changed[key]=d[key]
            changed=details(changed)
            happened=day(d.get('occurred_on'),future=False,required=True)
            ident=self._insert(c,t,'activities',dict(partner_id=partner['id'],kind=string(d.get('kind'),'tipo di attività',100,True),occurred_on=happened,details_json=encode(dict(outcome=changed['outcome'],notes=string(d.get('notes',''),'note'),nextAction=changed['nextAction'],nextDate=changed['nextDate'],city_id=partner['city_id']))))
            if not changed['firstContact']:changed['firstContact']=happened
            c.execute('UPDATE partners SET details_json=?,revision=revision+1 WHERE tenant_id=? AND id=?',(encode(changed),t,partner['id']))
            return {'id':ident}
        raise AuthError('Operazione non disponibile.')

    def voucher(self,token,ident,tenant=None,reason=''):
        with self.db.transaction() as c:
            actor,t,reason=self._scope(c,token,tenant,reason)
            lot=self._get(c,t,'voucher_lots',ident)
            row=c.execute('SELECT name FROM cities WHERE tenant_id=? AND id=?',(t,lot['city_id'])).fetchone() if lot['city_id'] else c.execute('SELECT name FROM campaigns WHERE tenant_id=? AND lot_id=?',(t,ident)).fetchone()
            self._audit(c,actor,t,reason,'export_voucher',ident)
            profile=c.execute('SELECT first_name,last_name,mobile,landline,email FROM consultant_profiles WHERE tenant_id=?',(t,)).fetchone()
            return {**lot,'label':row['name'] if row else 'Voucher','url':self.auth.origin+'/v/'+lot['code'],'consultant':dict(profile) if profile else {}}

    def public_voucher(self,code):
        with self.db.transaction() as c:
            row=c.execute("SELECT l.* FROM voucher_lots l JOIN tenants t ON t.id=l.tenant_id WHERE l.code=? AND l.status='active' AND t.status='active'",(code,)).fetchone()
            if not row:raise AuthError('Voucher non disponibile.',404)
            campaign=c.execute('SELECT p.status FROM campaigns a JOIN partners p ON p.tenant_id=a.tenant_id AND p.id=a.partner_id WHERE a.tenant_id=? AND a.lot_id=?',(row['tenant_id'],row['id'])).fetchone()
            if campaign and campaign['status']!='active':raise AuthError('Voucher non disponibile.',404)
            profile=c.execute('SELECT business_name,first_name,last_name,email,mobile,oam_number FROM consultant_profiles WHERE tenant_id=?',(row['tenant_id'],)).fetchone()
            return dict(profile) if profile else {}

    def export(self,token,fmt,tenant=None,reason='',filters=None):
        data=self.snapshot(token,tenant,reason)
        # Export receives its own fresh authorization; revocation between reads fails closed.
        with self.db.transaction() as c:
            actor,t,reason=self._scope(c,token,tenant,reason);self._audit(c,actor,t,reason,'export_'+fmt)
        if fmt=='json':return json.dumps(data,ensure_ascii=False,indent=2)
        if fmt!='csv':raise AuthError('Formato non disponibile.',404)
        filters=filters or {};rows=filter_partners(data,filters)
        out=io.StringIO(newline='');writer=csv.writer(out,delimiter=';');writer.writerow(['Nome','Città','Categoria',*DEFAULTS,'Stato voucher','Clienti registrati'])
        cities={r['id']:r['name'] for r in data['cities']}
        for p in rows:
            cells=[p['name'],cities.get(p['city_id'],'Campagna dedicata'),p['category'],*[p['details'].get(k,DEFAULTS[k]) for k in DEFAULTS],p['status'],p['registered_clients']]
            writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@','\t','\r','\n')) else v for v in cells])
        return '\ufeff'+out.getvalue()


def filter_partners(data,filters):
    rows=[]
    delivered={d['partner_id'] for d in data['deliveries']}
    for p in data['partners']:
        if (p['status']=='archived')!=(filters.get('trash')=='1'):continue
        if filters.get('q','').casefold() not in ' '.join(str(v) for v in (p['name'],p['details'].get('referent',''),p['details'].get('email',''))).casefold():continue
        if any(filters.get(k) and filters[k]!=(p['city_id'] if k=='city_id' else p['category'] if k=='category' else p['details'].get(k)) for k in ('city_id','category','status','priority')):continue
        if filters.get('no_voucher')=='1' and p['id'] in delivered:continue
        if filters.get('unverified')=='1' and p['details'].get('verification')=='Verificato':continue
        rows.append(p)
    return rows
