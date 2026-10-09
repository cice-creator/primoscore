from test_customer import CustomerTest
from test_auth import AuthFixture
from primoscore_server.crm import CRM
from primoscore_server.customer import Customer
from primoscore_server.workspace import Workspace
from uuid import uuid4
from primoscore_server.auth import AuthError
from primoscore_server.operations import Operations

class CRMTest(AuthFixture):
    payload=CustomerTest.payload
    intake=CustomerTest.intake
    def setUp(self):
        super().setUp()
        self.advisor,self.login,_,self.master=self.active_consultant();self.session=self.login['session_token'];self.ws=Workspace(self.auth);self.customer=Customer(self.auth,local=True)
        self.campaign=self.ws.command(self.session,dict(action='campaign.create',data={'name':'Voucher sintetico','kind':'website'},request_id=str(uuid4())))
        self.code=self.ws.snapshot(self.session)['voucher_lots'][0]['code']
        self.crm=CRM(self.auth)
        self.crm.rules(self.session,dict(enabled=False,first_hours=2,retry_days=1,proposal_days=2,revision=0))
        self.guest=self.intake();self.client=self.customer.get(guest=self.guest)['client'];self.crm=CRM(self.auth);self.ident=self.client['id']
    def state(self):return self.crm.snapshot(self.session,ident=self.ident)
    def command(self,**d):return self.crm.command(self.session,self.ident,{'revision':self.state()['lead']['crm_revision'],**d})
    def test_contact_followup_and_draft_are_persistent_without_sending(self):
        self.command(action='contact',kind='phone',outcome='no_answer',note='Chiamata senza risposta',next_action='Richiamare',due_at=self.time+86400)
        state=self.state();self.assertEqual(state['lead']['crm_stage'],'no_answer');self.assertEqual(len(state['events']),1)
        with self.db.transaction() as c:before=c.execute('SELECT count(*) FROM service_mail').fetchone()[0]
        self.command(action='draft',channel='email',purpose='no_answer');draft=self.state()['drafts'][0]
        self.assertIn('Nome',draft['body']);self.assertEqual(draft['status'],'draft')
        with self.assertRaises(AuthError):self.command(action='draft',channel='email',purpose='no_answer')
        self.command(action='save_draft',id=draft['id'],draft_revision=draft['revision'],body='Testo modificato',subject='Ricontatto',sent=True)
        self.assertEqual(self.state()['drafts'][0]['status'],'sent_manual')
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT count(*) FROM service_mail').fetchone()[0],before)
    def test_appointment_is_real_and_conflicts_stop(self):
        self.command(action='appointment',starts_at=self.time+86400)
        state=self.state();self.assertEqual(state['lead']['crm_stage'],'booked');self.assertIsNone(state['lead']['due_at']);self.assertEqual(len(state['appointments']),1)
        self.assertEqual(len(self.customer.advisor(self.session)['appointments']),1)
        with self.assertRaises(AuthError):self.command(action='appointment',starts_at=self.time+86400)
        self.command(action='draft',channel='whatsapp',purpose='confirmation')
        self.assertIn('appuntamento',self.state()['drafts'][0]['body'])
        self.command(action='appointment_status',id=state['appointments'][0]['id'],status='cancelled')
        self.assertEqual(self.state()['lead']['crm_stage'],'contact');self.assertIsNotNone(self.state()['lead']['due_at'])
    def test_stop_contact_scope_revision_and_erasure(self):
        with self.assertRaises(AuthError):self.crm.snapshot(self.session,tenant='other')
        with self.assertRaises(AuthError):self.crm.command(self.session,self.ident,{'action':'contact','revision':99})
        with self.assertRaises(AuthError):self.crm.snapshot(self.master['session_token'],tenant=self.advisor['tenant_id'])
        self.command(action='update',stage='do_not_contact',priority='normal',owner='',next_action='',due_at=None)
        with self.assertRaises(AuthError):self.command(action='draft',channel='whatsapp',purpose='first')
        self.command(action='contact',kind='note',outcome='note',note='Nota interna')
        Operations(self.auth).erase(self.session,{'client_id':self.ident,'revision':self.client['revision'],'confirmation':'ANONIMIZZA'})
        self.assertEqual(self.crm.snapshot(self.session)['leads'],[])
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT count(*) FROM crm_events').fetchone()[0],0)

    def test_all_generated_messages_use_studio_signature(self):
        with self.db.transaction() as c:
            c.execute("UPDATE consultant_profiles SET business_name='Studio Alba' WHERE tenant_id=?",(self.advisor['tenant_id'],))
            profile=dict(c.execute('SELECT * FROM consultant_profiles WHERE tenant_id=?',(self.advisor['tenant_id'],)).fetchone())
        self.command(action='appointment',starts_at=self.time+86400)
        for channel in ('email','whatsapp'):
            for purpose in ('first','no_answer','followup','proposal','confirmation','reminder','reschedule'):
                self.command(action='draft',channel=channel,purpose=purpose)
        for draft in self.state()['drafts']:
            self.assertNotIn('Primoscore',draft['body']+draft['subject'])
            for value in ('Studio Alba',profile['first_name'],profile['last_name'],profile['email'],profile['mobile'],profile['office_address']):
                self.assertIn(value,draft['body'])
            if draft['channel']=='email':self.assertIn('Studio Alba',draft['subject'])
    def test_signature_without_business_name_uses_consultant(self):
        self.command(action='draft',channel='email',purpose='first')
        draft=self.state()['drafts'][0]
        self.assertNotIn('Primoscore',draft['body']+draft['subject'])
        with self.db.transaction() as c:
            profile=c.execute('SELECT * FROM consultant_profiles WHERE tenant_id=?',(self.advisor['tenant_id'],)).fetchone()
        self.assertIn(profile['first_name']+' '+profile['last_name'],draft['subject'])

    def enable_rules(self,enabled=True):
        r=self.crm.rules(self.session)
        return self.crm.rules(self.session,dict(enabled=enabled,first_hours=2,retry_days=1,proposal_days=2,revision=r['revision']))
    def new_automatic_lead(self):
        self.enable_rules()
        self.guest=self.intake('auto@example.invalid')
        self.ident=self.customer.get(guest=self.guest)['client']['id']
    def test_automatic_intake_is_idempotent_and_never_sends_messages(self):
        self.new_automatic_lead()
        state=self.state()
        self.assertEqual(state['lead']['next_action'],'Primo contatto')
        self.assertTrue(state['lead']['owner'])
        self.assertGreater(state['lead']['due_at'],self.time)
        self.assertEqual(len(state['drafts']),1)
        self.assertEqual(state['drafts'][0]['purpose'],'first')
        with self.db.transaction() as c:before=c.execute('SELECT count(*) FROM service_mail').fetchone()[0]
        for _ in range(3):self.state()
        self.assertEqual(len(self.state()['drafts']),1)
        self.command(action='contact',kind='phone',outcome='no_answer',note='',automatic=True)
        state=self.state()
        self.assertEqual(state['lead']['next_action'],'Secondo tentativo di contatto')
        self.assertGreater(state['lead']['due_at'],self.time)
        self.assertIn('no_answer',[d['purpose'] for d in state['drafts']])
        draft=next(d for d in state['drafts'] if d['purpose']=='no_answer')
        self.command(action='save_draft',id=draft['id'],draft_revision=draft['revision'],body='Bozza personalizzata',subject='',sent=False)
        self.command(action='contact',kind='phone',outcome='no_answer',note='',automatic=True)
        self.assertEqual(next(d for d in self.state()['drafts'] if d['purpose']=='no_answer')['body'],'Bozza personalizzata')
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT count(*) FROM service_mail').fetchone()[0],before)
    def test_proposal_followup_booking_and_stop(self):
        self.new_automatic_lead()
        self.command(action='contact',kind='phone',outcome='conversation',note='',automatic=True)
        d=next(d for d in self.state()['drafts'] if d['purpose']=='proposal')
        self.command(action='save_draft',id=d['id'],draft_revision=d['revision'],body=d['body'],subject='',sent=True)
        self.assertEqual(self.state()['lead']['crm_stage'],'proposed')
        self.assertEqual(self.state()['lead']['next_action'],'Verificare conferma appuntamento')
        self.command(action='appointment',starts_at=self.time+86400)
        self.assertIsNone(self.state()['lead']['due_at'])
        self.assertEqual(self.state()['lead']['crm_stage'],'booked')
        self.assertIn('confirmation',[d['purpose'] for d in self.state()['drafts']])
        self.command(action='contact',kind='phone',outcome='do_not_contact',note='')
        self.assertIsNone(self.state()['lead']['due_at'])
        self.assertEqual(self.state()['lead']['crm_stage'],'do_not_contact')
        self.command(action='appointment_status',id=self.state()['appointments'][0]['id'],status='cancelled')
        self.assertEqual(self.state()['lead']['crm_stage'],'do_not_contact')
        self.assertIsNone(self.state()['lead']['due_at'])
        self.assertNotIn('reschedule',[d['purpose'] for d in self.state()['drafts']])
    def test_customer_booking_stops_crm_followup_immediately(self):
        self.new_automatic_lead()
        self.customer.configure(self.session,dict(action='slot.add',starts_at=self.time+86400))
        slot=self.customer.slots(guest=self.guest)[0]
        self.customer.book(dict(slot_id=slot['id']),guest=self.guest)
        with self.db.transaction() as c:
            row=c.execute('SELECT * FROM crm_leads WHERE tenant_id=? AND client_id=?',(self.advisor['tenant_id'],self.ident)).fetchone()
        self.assertEqual(row['stage'],'booked');self.assertIsNone(row['due_at'])
    def test_rules_revision_scope_pause_and_working_hours(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        r=self.enable_rules()
        with self.assertRaises(AuthError):self.crm.rules(self.session,dict(enabled=True,first_hours=2,retry_days=1,proposal_days=2,revision=r['revision']-1))
        with self.assertRaises(AuthError):self.crm.rules(self.session,tenant='other')
        self.enable_rules(False)
        guest=self.intake('paused@example.invalid');ident=self.customer.get(guest=guest)['client']['id']
        self.assertEqual(self.crm.snapshot(self.session,ident=ident)['drafts'],[])
        self.time=int(datetime(2026,10,23,17,0,tzinfo=ZoneInfo('Europe/Rome')).timestamp())
        due=datetime.fromtimestamp(self.crm._working_due(hours=2),ZoneInfo('Europe/Rome'))
        self.assertEqual(due.isoformat(),'2026-10-26T10:00:00+01:00')
