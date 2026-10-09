"""Disposable, loopback-only guided preview. Never accepts an existing database.

Run directly; this module is never imported by the application or production factory.
Every launch creates new synthetic studios in a new temporary directory. SMTP is unused.
"""
import argparse
from datetime import date,timedelta
import json
from pathlib import Path
import secrets
import sys
import tempfile
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from flask import jsonify,request,Response
import pyotp
from primoscore_server.database import Database,now
from primoscore_server.auth import Auth,AuthError
from primoscore_server.repository import new_id
from primoscore_server.workspace import Workspace
from primoscore_server.customer import Customer
from primoscore_server.web import create_app


def create_preview(port=4174):
    temp=tempfile.TemporaryDirectory(prefix='primoscore-demo-')
    db=Database(Path(temp.name)/'demo.sqlite3');db.initialize()
    origin=f'http://127.0.0.1:{port}';key=Fernet.generate_key();auth=Auth(db,key,origin)
    app=create_app(db,key,origin,local=True,auth=auth,cookie_prefix='ps_demo_')
    app.extensions['preview_tempdir']=temp
    def studio(slug,first,last,active=True):
        ident=new_id()
        with db.transaction() as c:
            c.execute('INSERT INTO tenants VALUES(?,?,?,?)',(ident,slug,'active' if active else 'draft',now()))
            c.execute('INSERT INTO consultant_profiles(tenant_id,first_name,last_name,business_name,email,mobile,landline,office_address,office_postcode,office_city,office_province,oam_number,ivass_number) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,first,last,'Studio '+last+' · Demo',slug+'@example.invalid','0000000000','0000000000','Via delle Opportunità 12 · Demo','00000','Porto Nuovo · Demo','XX','M00000','Non applicabile · Demo'))
        return ident
    password=secrets.token_urlsafe(30)
    def account(role,email,tenant=None,client=None,active=True):
        ident=new_id()
        with db.transaction() as c:
            c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)',(ident,tenant,client,role,email,'active' if active else 'pending',now()))
            secret=auth.cipher.encrypt(pyotp.random_base32().encode()).decode() if role!='customer' else None
            c.execute('INSERT INTO auth_credentials(account_id,password_hash,email_verified,totp_encrypted,password_changed_at) VALUES(?,?,1,?,?)',(ident,auth.hasher.hash(password),secret,auth.timestamp()))
        return ident
    master=account('master','master-demo@example.invalid')
    tenant=studio('studio-demo','Elena','Bianchi')
    advisor=account('consultant','elena-demo@example.invalid',tenant)
    other=studio('studio-in-attesa-demo','Luca','Serra',False)
    account('consultant','luca-demo@example.invalid',other,active=False)
    def session(account_id):
        with db.transaction() as c:return auth._session(c,auth._credentials(c,account_id))['session_token']
    advisor_token=session(advisor);workspace=Workspace(auth);customer=Customer(auth,local=True)
    def command(action,**d):return workspace.command(advisor_token,dict(action=action,data=d,request_id=str(uuid4())))
    today=date.today()
    cities=[command('city.create',name=name+' · Demo') for name in ('Porto Nuovo','Borgo Serena','Colle Verde')]
    partner_specs=[('Abitare Insieme','Agenzie immobiliari',0,'Partner attivo','Alta',-1),('Orizzonte Casa','Agenzie immobiliari',0,'Incontro fissato','Alta',1),('Studio Conti','Commercialisti',1,'Da richiamare','Media',2),('Punto Famiglia','CAF o similari',1,'Contattato','Media',3),('Progetto Futuro','Consulenti finanziari',2,'Da contattare','Bassa',5)]
    partners=[]
    for i,(name,category,city,status,priority,days) in enumerate(partner_specs):
        partners.append(command('partner.create',name=name+' · Demo',city_id=cities[city]['id'],category=category,details=dict(referent='Referente dimostrativo',email=f'partner-{i}@example.invalid',phone='0000000000',address='Via delle Relazioni · Demo',status=status,priority=priority,nextAction='Confronto sul progetto voucher',nextDate=(today+timedelta(days=days)).isoformat(),leads=7+i,qualifiedLeads=3,verification='Verificato' if i%2==0 else 'Da verificare',notes='Dati inventati per esplorare Primoscore.')))
    for i,city in enumerate(cities):command('print.add',lot_id=city['lot_id'],quantity=[300,200,150][i],printed_on=today.isoformat(),notes='Stampa dimostrativa')
    for i,p in enumerate(partners):
        if i<4:command('delivery.create',partner_id=p['id'],quantity=[80,50,60,30][i],delivered_on=today.isoformat(),recipient='Referente demo')
        command('activity.create',partner_id=p['id'],revision=0,kind='Incontro' if i==0 else 'Telefonata',occurred_on=(today-timedelta(days=i)).isoformat(),outcome='Presentazione del progetto e prossimi passi concordati.',notes='Attività dimostrativa.')
    campaign=command('campaign.create',name='Il primo passo · Campagna Demo',kind='website')
    second=command('campaign.create',name='Open day casa · Demo',kind='flyer')
    command('print.add',lot_id=second['lot_id'],quantity=100,printed_on=today.isoformat(),notes='Cartoncini dimostrativi')
    command('delivery.create',partner_id=second['partner_id'],quantity=40,delivered_on=today.isoformat())
    s=workspace.snapshot(advisor_token);code=next(l['code'] for l in s['voucher_lots'] if l['id']==campaign['lot_id'])
    base=json.loads((Path(__file__).resolve().parents[1]/'tests/score-reference.json').read_text())['cases'][0]['answers']
    client_ids={}
    for role,first,last in [('customer','Marta','Rossi'),('partial','Paolo','Conti'),('draft','Giulia','Ferrari')]:
        email=role+'-demo@example.invalid'
        acquired=customer.acquire(dict(code=code,first_name=first,last_name=last+' · Demo',mobile='0000000000',email=email,privacy_accepted=True,service_requested=True,privacy_version='local-test-only',request_id=str(uuid4())),ip='preview-'+role)
        guest=acquired['guest_token']
        values=dict(base)
        if role=='partial':
            values.pop('propertyPrice');values.pop('loanAmount');values['propertyFound']='no';values['iseeUnknown']='yes';values.pop('iseeBand')
        if role=='draft':values={k:v for k,v in base.items() if k in ('purpose','propertyPrice','loanAmount','loanTerm','applicantAge','monthlyIncome','jobType','annualPayments')};values['propertyFound']='yes';values['timeframe']='months'
        customer.save(dict(answers=values,step=2 if role=='draft' else 5,revision=0,residence_city='Borgo Serena · Demo'),guest=guest)
        if role!='draft':
            customer.complete({'revision':1},guest=guest)
            with db.transaction() as c:
                row=c.execute("SELECT payload_encrypted FROM service_mail WHERE event_key LIKE 'report:%' ORDER BY rowid DESC LIMIT 1").fetchone()
            from urllib.parse import parse_qs,urlsplit
            delivery=json.loads(auth.cipher.decrypt(row[0].encode()))
            customer.report(parse_qs(urlsplit(delivery['url']).fragment)['token'][0],verify=True)
        with db.transaction() as c:
            actor=c.execute('SELECT id FROM accounts WHERE email=?',(email,)).fetchone()[0]
            c.execute("UPDATE accounts SET status='active' WHERE id=?",(actor,))
            c.execute('UPDATE auth_credentials SET password_hash=?,email_verified=1 WHERE account_id=?',(auth.hasher.hash(password),actor))
        client_ids[role]=actor
    from datetime import datetime
    from zoneinfo import ZoneInfo
    slots=[]
    for n in (1,2,3):
        day=today+timedelta(days=n)
        for h in (10,15):
            start=int(datetime(day.year,day.month,day.day,h,tzinfo=ZoneInfo('Europe/Rome')).timestamp())
            customer.configure(advisor_token,dict(action='slot.add',starts_at=start))
    draft_token=session(client_ids['draft'])
    slots=customer.slots(session=draft_token)
    customer.book({'slot_id':slots[0]['id'],'note':'Vorrei capire come organizzare l’acquisto della prima casa.'},session=draft_token)
    roles={'crm':(advisor,'/consulente/clienti'),'consultant':(advisor,'/consulente/'),'master':(master,'/master/'),'customer':(client_ids['customer'],'/cliente/'),'result':(client_ids['customer'],'/cliente/risultato'),'partial':(client_ids['partial'],'/cliente/risultato'),'draft':(client_ids['draft'],'/cliente/questionario'),'booking':(client_ids['customer'],'/cliente/appuntamento'),'voucher':(None,'/v/'+code),'registration':(None,'/accesso/registrazione'),'login':(None,'/accesso/consulente')}
    app.extensions['preview_roles']=roles

    @app.post('/api/demo/switch')
    def switch():
        d=request.get_json(silent=True)
        if not isinstance(d,dict) or set(d)!={'view'} or not isinstance(d['view'],str) or d['view'] not in roles:raise AuthError('Anteprima non disponibile.')
        actor,path=roles[d['view']]
        auth.logout(request.cookies.get('ps_demo_session',''));customer.logout_guest(request.cookies.get('ps_demo_guest',''))
        response=jsonify(path=path)
        for suffix in ('session','guest','step'):response.delete_cookie('ps_demo_'+suffix,path='/',httponly=True,samesite='Strict')
        if actor:response.set_cookie('ps_demo_session',session(actor),max_age=28800,httponly=True,samesite='Strict',path='/')
        return response

    @app.get('/__preview/')
    def index():
        cards=[('crm','CRM · STUDIO','Dal lead all’appuntamento.','Registra contatti e follow-up, genera bozze e fissa incontri nell’archivio di prova.'),('consultant','01 · CONSULENTE','Le relazioni diventano opportunità.','Esplora panoramica, partner, voucher, campagne, giacenze, clienti e agenda.'),('master','02 · MASTER','Uno sguardo su tutti gli studi.','Consulta i profili, prova l’attivazione di un consulente e apri uno studio con accesso motivato.'),('voucher','03 · DAL VOUCHER','Il primo passo del cliente.','Apri il QR, lascia recapiti inventati e inizia una nuova valutazione.'),('draft','04 · QUESTIONARIO','Un passo alla volta.','Riprendi un questionario già iniziato e percorri le sei schermate.'),('customer','05 · AREA CLIENTE','Il progetto, sempre a portata di mano.','Scopri lo spazio personale con risultato, recapiti del consulente e appuntamenti.'),('result','06 · RISULTATO','Più chiarezza, per decidere.','Guarda la valutazione completa con punteggio, rata indicativa e aspetti da approfondire.'),('partial','07 · RISULTATO PARZIALE','Anche quando la casa è da trovare.','Esplora il caso senza immobile, con i dati disponibili e i prossimi passi.'),('booking','08 · APPUNTAMENTI','Troviamo un momento per parlarne.','Scegli un orario e prova una prenotazione nell’agenda interna dello studio.'),('registration','09 · REGISTRAZIONE','Il tuo studio entra in Primoscore.','Esplora la registrazione completa del consulente e i suoi dati professionali.'),('login','10 · ACCESSI','Un accesso personale e protetto.','Guarda le schermate di accesso, recupero password e conferma email.')]
        content=''.join(f'<article class="demo-card"><p class="eyebrow">{tag}</p><h2>{title}</h2><p>{description}</p><button data-demo-view="{key}">Esplora →</button></article>' for key,tag,title,description in cards)
        return Response('<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Esplora Primoscore · Anteprima completa</title><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/static/workspace.css"><link rel="stylesheet" href="/static/preview.css"></head><body><header class="top"><a href="/__preview/"><img src="/assets/logo.svg" width="180" height="40" alt="Primoscore"></a><span>Il sito, da ogni punto di vista.</span><a href="/">Homepage pubblica ↗</a></header><main class="demo-main"><p class="eyebrow">ANTEPRIMA NAVIGABILE</p><h1>Benvenuto in Primoscore.<br><span>Esploriamolo, insieme.</span></h1><p class="demo-lead">Entra nei diversi spazi senza dover configurare un account. Prova i pulsanti, apri le schede e segui il percorso dal voucher alla valutazione.</p><div class="customer-message demo-note">Tutti i nomi e i dati sono inventati. Le modifiche restano nella dimostrazione; non vengono inviati messaggi né sincronizzati calendari.</div><div class="demo-grid">'+content+'</div><p class="muted">Ogni cambio di area seleziona un profilo dimostrativo nella sessione del browser. Usa la barra in basso per tornare qui o cambiare ruolo. Per provare insieme più ruoli indipendenti, usa finestre del browser separate.</p></main></body></html>',mimetype='text/html')

    @app.after_request
    def toolbar(response):
        if response.mimetype=='text/html' and response.status_code==200:
            bar='<link rel="stylesheet" href="/static/preview.css"><aside class="demo-toolbar" aria-label="Navigazione anteprima"><a href="/__preview/" class="demo-hub">◈ Tutta l’anteprima</a><span>Dati fittizi · Nessun invio</span><button data-demo-view="crm">CRM</button><button data-demo-view="consultant">Consulente</button><button data-demo-view="master">Master</button><button data-demo-view="customer">Cliente</button><a href="/">Homepage</a><output id="demo-feedback" aria-live="polite"></output></aside><script src="/static/preview.js" defer></script>'
            response.set_data(response.get_data(as_text=True).replace('</body>',bar+'</body>'))
        return response
    return app

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=4174);args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Porta non valida')
    application=create_preview(args.port)
    print(f'Anteprima dimostrativa: http://127.0.0.1:{args.port}/__preview/',flush=True)
    application.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)
