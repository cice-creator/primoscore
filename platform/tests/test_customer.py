import io
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
from test_auth import AuthFixture,PASSWORD
from primoscore_server.auth import AuthError
from primoscore_server.customer import Customer
from primoscore_server.workspace import Workspace
from primoscore_server.web import create_app
from primoscore_core.intake import complete_result,clean_answers
from primoscore_core import score_engine
import unittest

BASE=json.loads((Path(__file__).parent/'score-reference.json').read_text())['cases'][0]['answers']

class IntakeRulesTest(unittest.TestCase):
    def test_full_v2_outputs_match_ten_pinned_completion_cases(self):
        reference=json.loads((Path(__file__).parent/'intake-reference.json').read_text())
        self.assertTrue(reference['synthetic'])
        def brand(value):
            if isinstance(value,str):return value.replace('Andrea verificherà','Il tuo consulente verificherà').replace('Andrea','il tuo consulente')
            if isinstance(value,list):return [brand(v) for v in value]
            if isinstance(value,dict):return {k:brand(v) for k,v in value.items()}
            return value
        for case in reference['cases']:
            with self.subTest(case=case['name']),patch.object(score_engine,'urlopen',side_effect=AssertionError('Unexpected network')):
                result=complete_result(case['answers']);result.pop('intakeVersion')
                expected=brand(case['expected'])
                if not expected.get('partial'):
                    expected['engineVersion']='primoscore-mutuoscore-1.7'
                    if expected['metrics']['subsistence']['status']=='below_threshold':
                        expected['totalScore']=min(expected['totalScore'],59)
                        expected['classification']=score_engine.classify(score_engine.load_constitution()['classification'],expected['totalScore'])
                if case['answers'].get('purpose') != 'first_home':expected['consap']=None
                self.assertEqual(result,expected)

    def test_v2_full_score_preserves_original_engine_numbers(self):
        source=json.loads((Path(__file__).parent/'score-reference.json').read_text())['cases'][0]['expected']
        result=complete_result(BASE)
        for key in ('totalScore','rawScore','areaScores','metrics','classification','consap','strengths'):
            self.assertEqual(result[key],source[key])
        self.assertTrue(any('da approfondire con il tuo consulente' in v for v in result['warnings']))

    def test_no_property_partial_does_not_manufacture_score_or_payment(self):
        answers={k:v for k,v in BASE.items() if k not in ('propertyPrice','loanAmount')};answers['propertyFound']='no'
        result=complete_result(answers)
        self.assertTrue(result['partial']);self.assertNotIn('totalScore',result);self.assertEqual(result['metrics'],{})
        answers['propertyPrice']=250000;answers['loanAmount']=180000
        self.assertEqual(complete_result(answers)['totalScore'],86)

    def test_isee_unknown_and_older_applicants_receive_manual_consap(self):
        for changes in ({'iseeUnknown':'yes'},{'applicantAge':45}):
            a={**BASE,**changes};a.pop('iseeBand')
            result=complete_result(a)
            self.assertEqual(result['consap']['accessStatus'],'manual_review')
            self.assertIn('non ti esclude',result['consap']['explanation'])
        with self.assertRaises(ValueError):complete_result({k:v for k,v in BASE.items() if k!='iseeBand'})

    def test_invalid_shape_fraction_boolean_and_household_rejected(self):
        for values in ({'monthlyIncome':True},{'monthlyIncome':1800.9},{'jobType':{}},{'iseeUnknown':[]},{'injected':1}):
            with self.subTest(values=values),self.assertRaises(ValueError):clean_answers(values)
        with self.assertRaises(ValueError):complete_result({**BASE,'children':2})
        with self.assertRaises(ValueError):complete_result({**BASE,'householdEarners':3})
        self.assertEqual(clean_answers({'monthlyIncome':'1800'}),{'monthlyIncome':1800})
        self.assertNotIn('supportIncome',clean_answers({**BASE,'supportIncome':1000}))


class CustomerTest(AuthFixture):
    def setUp(self):
        super().setUp();self.advisor,self.login,_,self.master=self.active_consultant();self.session=self.login['session_token'];self.ws=Workspace(self.auth);self.customer=Customer(self.auth,local=True)
        self.campaign=self.ws.command(self.session,dict(action='campaign.create',data={'name':'Voucher sintetico','kind':'website'},request_id=str(uuid4())))
        self.code=self.ws.snapshot(self.session)['voucher_lots'][0]['code']

    def payload(self,email='client@example.invalid'):
        return dict(code=self.code,first_name='Nome',last_name='Sintetico',mobile='+39 3330000000',email=email,privacy_accepted=True,service_requested=True,privacy_version='local-test-only',request_id=str(uuid4()))

    def intake(self,email='client@example.invalid'):
        result=self.customer.acquire(self.payload(email),ip=email);return result['guest_token']

    def save(self,guest,answers=None,revision=0,city='Comune sintetico'):
        return self.customer.save(dict(answers=BASE if answers is None else answers,step=5,revision=revision,residence_city=city),guest=guest)

    def configure(self,**d):return self.customer.configure(self.session,d)

    def test_intake_is_atomic_attributed_and_customer_only(self):
        guest=self.intake();view=self.customer.get(guest=guest)
        self.assertEqual(view['partner_label'],'Voucher sintetico');self.assertEqual(view['client']['partner_id'],self.campaign['partner_id']);self.assertEqual(view['questionnaire']['answers'],{})
        self.assertTrue(view['temporary_access']);self.assertNotIn('password_hash',view)
        with self.assertRaises(AuthError):self.ws.snapshot(guest)
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM customer_intakes').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_mail WHERE account_id=?',(self.account_from_connection(c,'client@example.invalid')['id'],)).fetchone()[0],1)
            self.assertNotEqual(c.execute('SELECT token_hash FROM customer_guest_sessions').fetchone()[0],guest)

    @staticmethod
    def account_from_connection(c,email):return c.execute('SELECT * FROM accounts WHERE email=?',(email,)).fetchone()

    def test_retry_requires_original_guest_not_just_request_id(self):
        d=self.payload();first=self.customer.acquire(d,ip='one');guest=first['guest_token']
        self.assertTrue(self.customer.acquire(d,guest=guest,ip='retry')['ok'])
        with self.assertRaises(AuthError):self.customer.acquire(d,ip='forged')
        with self.assertRaises(AuthError):self.customer.acquire({**d,'mobile':'3339999999'},guest=guest,ip='changed')
        with self.assertRaises(AuthError):self.intake()
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM clients').fetchone()[0],1)

    def test_city_picker_only_active_delivered_partners_and_foreign_id_rejected(self):
        def cmd(action,**d):return self.ws.command(self.session,dict(action=action,data=d,request_id=str(uuid4())))
        city=cmd('city.create',name='Città sintetica');partner=cmd('partner.create',name='Partner prova',category='CAF',city_id=city['id'])
        city_code=next(l['code'] for l in self.ws.snapshot(self.session)['voucher_lots'] if l['id']==city['lot_id'])
        self.assertEqual(self.customer.context(city_code)['partners'],[])
        from datetime import date
        cmd('print.add',lot_id=city['lot_id'],quantity=10,printed_on=date.today().isoformat());cmd('delivery.create',partner_id=partner['id'],quantity=1,delivered_on=date.today().isoformat())
        self.assertEqual(self.customer.context(city_code)['partners'][0]['id'],partner['id'])
        with self.assertRaises(AuthError):self.customer.acquire({**self.payload(), 'code':city_code,'partner_id':self.campaign['partner_id']},ip='badpartner')
        g=self.customer.acquire({**self.payload(), 'code':city_code,'partner_id':partner['id']},ip='goodpartner')['guest_token']
        self.assertEqual(self.customer.get(guest=g)['client']['partner_id'],partner['id'])
        cmd('partner.status',id=partner['id'],revision=0,status='inactive')
        self.assertEqual(self.customer.context(city_code)['partners'],[])

    def test_draft_resume_completion_history_and_stale_revisions(self):
        guest=self.intake();self.save(guest)
        self.assertEqual(self.customer.get(guest=guest)['questionnaire']['answers'],BASE)
        with self.assertRaises(AuthError):self.save(guest,revision=0)
        sent=self.customer.complete({'revision':1},guest=guest);self.assertTrue(sent['report_pending'])
        self.assertIsNone(self.customer.get(guest=guest)['result'])
        r=self.customer.advisor(self.session,client_id=self.customer.get(guest=guest)['client']['id'])['result'];self.assertEqual(r['totalScore'],86)
        self.assertTrue(self.customer.complete({'revision':1},guest=guest)['report_pending'])
        self.save(guest,{**BASE,'savings':60000},revision=1)
        self.assertIsNone(self.customer.get(guest=guest)['result'])
        self.customer.complete({'revision':2},guest=guest)
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],2)
        with self.assertRaises(sqlite3.IntegrityError),self.db.transaction() as c:c.execute('DELETE FROM assessments')

    def test_complete_detects_changed_answers_during_calculation(self):
        guest=self.intake();self.save(guest)
        original=complete_result
        def changed(answers):
            result=original(answers);self.save(guest,{**BASE,'savings':10000},revision=1);return result
        with patch('primoscore_server.customer.complete_result',side_effect=changed),self.assertRaises(AuthError):self.customer.complete({'revision':1},guest=guest)
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0],0)

    def test_email_activation_revokes_guest_then_password_resumes(self):
        guest=self.intake();self.save(guest)
        account=self.account('client@example.invalid');self.auth.reset_password(self.token(account['id']),PASSWORD,invite=True)
        with self.assertRaises(AuthError):self.customer.get(guest=guest)
        slug=self.ws.snapshot(self.session)['tenant_id']
        with self.db.transaction() as c:slug=c.execute('SELECT slug FROM tenants WHERE id=?',(slug,)).fetchone()[0]
        login=self.auth.login(account['email'],PASSWORD,'customer',slug,'customer-login')
        self.assertEqual(self.customer.get(session=login['session_token'])['questionnaire']['answers'],BASE)
        self.assertFalse(self.customer.get(session=login['session_token'])['temporary_access'])

    def test_invite_resend_is_generic_and_guest_expiry_enforced(self):
        guest=self.intake();view=self.customer.get(guest=guest);account=self.account('client@example.invalid');old=self.token(account['id'])
        self.auth.request_email(account['email'],'customer',view['studio'],'reset','resend')
        self.assertNotEqual(old,self.token(account['id']))
        with self.assertRaises(AuthError):self.auth.reset_password(old,PASSWORD,invite=True)
        self.time+=86401
        with self.assertRaises(AuthError):self.customer.get(guest=guest)

    def test_customer_identity_cannot_be_selected_from_payload(self):
        one=self.intake();two=self.intake('second-client@example.invalid')
        self.save(one,{'savings':1000})
        self.assertEqual(self.customer.get(guest=two)['questionnaire']['answers'],{})
        with self.assertRaises(AuthError):self.customer.save(dict(answers=BASE,step=1,revision=0,residence_city='X',client_id=self.customer.get(guest=one)['client']['id']),guest=two)
        with self.assertRaises(AuthError):self.customer.get(session=self.session)
        with self.db.transaction() as c:c.execute("UPDATE tenants SET status='suspended' WHERE id=?",(self.advisor['tenant_id'],))
        with self.assertRaises(AuthError):self.customer.get(guest=one)

    def test_production_generated_notice_still_requires_acknowledgment(self):
        production=Customer(self.auth)
        self.assertTrue(production.context(self.code)['available'])
        with self.assertRaises(AuthError):
            production.acquire({**self.payload(),'privacy_version':'','privacy_accepted':False},ip='no-notice')
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM customer_intakes').fetchone()[0],0)

    def test_designated_test_studio_only_accepts_synthetic_contact(self):
        from primoscore_server.customer import TEST_STUDIO_SLUG,TEST_CONTACT
        production=Customer(self.auth)
        with self.db.transaction() as c:
            c.execute('UPDATE tenants SET slug=? WHERE id=?',(TEST_STUDIO_SLUG,self.advisor['tenant_id']))
        self.configure(action='privacy',privacy_url='https://studio.example/privacy-collaudo',privacy_version='test-v1',revision=0)
        context=production.context(self.code)
        self.assertTrue(context['testing'])
        self.assertEqual(context['test_contact'],TEST_CONTACT)
        with self.assertRaises(AuthError):
            production.acquire({**self.payload(),'privacy_version':'test-v1'},ip='bad-contact')
        result=production.acquire({**self.payload(),**TEST_CONTACT,'privacy_version':'test-v1'},ip='test-contact')
        self.assertTrue(production.get(guest=result['guest_token'])['testing'])

    def test_controller_required_for_existing_studio_and_exposed_in_qr(self):
        production=Customer(self.auth)
        with self.db.transaction() as c:
            c.execute("UPDATE consultant_profiles SET controller_name='',controller_email='' WHERE tenant_id=?",(self.advisor['tenant_id'],))
        self.assertFalse(production.context(self.code)['available'])
        with self.assertRaises(AuthError):
            self.configure(action='privacy',privacy_url='https://studio.example/privacy',privacy_version='v1',revision=0)
        self.configure(action='privacy',privacy_url='https://studio.example/privacy',privacy_version='v1',revision=0,controller_name='Società dello studio',controller_email='privacy@example.invalid')
        context=production.context(self.code)
        self.assertTrue(context['available'])
        self.assertEqual(context['controller_name'],'Società dello studio')
        self.assertEqual(context['controller_email'],'privacy@example.invalid')

    def test_production_requires_explicit_privacy_version_and_logs_consent(self):
        production=Customer(self.auth)
        self.assertTrue(production.context(self.code)['available'])
        with self.assertRaises(AuthError):production.acquire(self.payload(),ip='no-privacy')
        self.configure(action='privacy',privacy_url='https://studio.example/privacy',privacy_version='v1',revision=0)
        with self.assertRaises(AuthError):production.acquire(self.payload(),ip='old-privacy')
        g=production.acquire({**self.payload(),'privacy_version':'v1'},ip='valid-privacy')['guest_token']
        with self.db.transaction() as c:
            r=c.execute('SELECT * FROM customer_intakes').fetchone();self.assertEqual(r['privacy_version'],'v1');self.assertEqual(r['privacy_url'],'https://studio.example/privacy')
        self.assertFalse(production.get(guest=g)['local'])

    def test_generated_notice_is_public_versioned_and_intake_records_it(self):
        production=Customer(self.auth)
        initial=production.context(self.code)
        url=initial['privacy_url']
        app=create_app(self.db,self.key,self.auth.origin,auth=self.auth)
        client=app.test_client()
        page=client.get(url,base_url=self.auth.origin)
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Studio sintetico',page.data)
        self.assertNotIn(b'Nota per la verifica',page.data)
        result=production.acquire({**self.payload(), 'privacy_version':initial['privacy_version']},ip='generated-notice')
        with self.db.transaction() as c:
            row=c.execute('SELECT * FROM customer_intakes').fetchone()
            self.assertEqual(row['privacy_url'],url)
            self.assertEqual(row['privacy_version'],initial['privacy_version'])
        production.configure(self.session,dict(action='privacy.auto',revision=0,controller_name='Nuovo titolare',controller_email='new@example.invalid',controller_address='Nuova sede',controller_dpo='dpo@example.invalid'))
        current=production.context(self.code)
        self.assertNotEqual(current['privacy_version'],initial['privacy_version'])
        self.assertIn(b'Studio sintetico',client.get(url,base_url=self.auth.origin).data)
        self.assertIn(b'Nuovo titolare',client.get(current['privacy_url'],base_url=self.auth.origin).data)
        with self.assertRaises(AuthError):
            production.acquire({**self.payload('another@example.invalid'),'privacy_version':initial['privacy_version']},ip='stale-notice')
        self.assertEqual(client.get('/privacy/studio/missing/version',base_url=self.auth.origin).status_code,404)

    def test_slots_are_explicit_booking_idempotent_and_competing_clients_conflict(self):
        one=self.intake();two=self.intake('two@example.invalid');self.assertEqual(self.customer.slots(guest=one),[])
        self.configure(action='slot.add',starts_at=self.time+86400)
        slot=self.customer.slots(guest=one)[0]
        def attempt(guest):
            try:return self.customer.book({'slot_id':slot['id']},guest=guest)
            except AuthError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[one,two]))
        self.assertEqual(sum(r is not None for r in results),1)
        winner=one if results[0] else two
        self.assertEqual(self.customer.book({'slot_id':slot['id']},guest=winner),next(r for r in results if r))
        self.assertEqual(self.customer.slots(guest=winner),[])
        a=next(r for r in results if r)['appointment']
        self.configure(action='appointment.status',id=a['id'],revision=0,status='cancelled')
        self.assertEqual(len(self.customer.slots(guest=winner)),1)

    def test_other_studio_same_time_allowed_master_reason_and_foreign_details_denied(self):
        other,login,_=self.enrolled('other-advisor@example.invalid');self.auth.activate(self.master['session_token'],other['tenant_id'])
        self.configure(action='slot.add',starts_at=self.time+86400)
        self.customer.configure(login['session_token'],dict(action='slot.add',starts_at=self.time+86400))
        own=self.intake();ident=self.customer.get(guest=own)['client']['id']
        with self.assertRaises(AuthError):self.customer.advisor(login['session_token'],client_id=ident)
        with self.assertRaises(AuthError):self.customer.advisor(self.session,tenant=other['tenant_id'])
        with self.assertRaises(AuthError):self.customer.advisor(self.master['session_token'],tenant=self.advisor['tenant_id'])
        result=self.customer.advisor(self.master['session_token'],tenant=self.advisor['tenant_id'],reason='Assistenza di prova',client_id=ident)
        self.assertEqual(result['client']['id'],ident)
        foreign=self.customer.advisor(login['session_token'])['slots'][0]['id']
        with self.assertRaises(AuthError):self.customer.book({'slot_id':foreign},guest=own)

    def test_http_csrf_guest_cookie_logout_and_no_client_list(self):
        app=create_app(self.db,self.key,self.auth.origin,auth=self.auth,local=False);app.testing=True;client=app.test_client();base=self.auth.origin
        self.configure(action='privacy',privacy_url='https://studio.example/privacy',privacy_version='v1',revision=0)
        csrf=client.get('/api/auth/csrf',base_url=base).json['csrf'];headers={'Origin':base,'X-CSRF-Token':csrf};payload={**self.payload(),'privacy_version':'v1'}
        self.assertEqual(client.post('/api/customer/intake',json=payload,base_url=base).status_code,403)
        result=client.post('/api/customer/intake',json=payload,headers=headers,base_url=base)
        self.assertEqual(result.status_code,200);self.assertNotIn('guest_token',result.json)
        cookie=';'.join(result.headers.getlist('Set-Cookie'));self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie);self.assertIn('SameSite=Strict',cookie)
        self.assertEqual(client.get('/api/customer',base_url=base).status_code,200)
        self.assertEqual(client.get('/api/workspace/customers',base_url=base).status_code,401)
        self.assertEqual(client.get('/api/customer?client_id=forged',base_url=base).status_code,200)
        result=client.post('/api/customer/save',json=dict(answers={'jobType':{}},revision=0,step=1,residence_city='X'),headers=headers,base_url=base)
        self.assertEqual(result.status_code,400)
        client.post('/api/auth/logout',json={},headers=headers,base_url=base)
        self.assertEqual(client.get('/api/customer',base_url=base).status_code,401)

    def test_slot_conversion_uses_italian_timezone_and_rejects_clock_changes(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        day=datetime.fromtimestamp(self.time+86400,ZoneInfo('Europe/Rome')).replace(hour=9,minute=0,second=0,microsecond=0)
        self.configure(action='slot.add',local_start=day.strftime('%Y-%m-%dT%H:%M'))
        slots=self.customer.advisor(self.session)['slots']
        self.assertEqual(slots[0]['starts_at'],int(day.timestamp()))
        for value in ('2030-03-31T02:30','2030-10-27T02:30','invalid'):
            with self.subTest(value=value),self.assertRaises(AuthError):self.configure(action='slot.add',local_start=value)

    def test_full_http_flow_saves_result_and_books_before_email_activation(self):
        self.configure(action='privacy',privacy_url='https://studio.example/privacy',privacy_version='v1',revision=0)
        self.configure(action='slot.add',starts_at=self.time+86400)
        app=create_app(self.db,self.key,self.auth.origin,auth=self.auth);app.testing=True;client=app.test_client();base=self.auth.origin
        csrf=client.get('/api/auth/csrf',base_url=base).json['csrf'];headers={'Origin':base,'X-CSRF-Token':csrf}
        r=client.post('/api/customer/intake',base_url=base,headers=headers,json={**self.payload(),'privacy_version':'v1'});self.assertEqual(r.status_code,200)
        r=client.post('/api/customer/save',base_url=base,headers=headers,json=dict(answers=BASE,step=5,revision=0,residence_city='Comune sintetico'));self.assertEqual(r.json['revision'],1)
        r=client.post('/api/customer/complete',base_url=base,headers=headers,json={'revision':1});self.assertTrue(r.json['report_pending']);self.assertNotIn('result',r.json)
        slot=client.get('/api/customer/slots',base_url=base).json['slots'][0]
        r=client.post('/api/customer/book',base_url=base,headers=headers,json={'slot_id':slot['id'],'note':'Domanda di prova'});self.assertEqual(r.status_code,200)
        own=client.get('/api/customer',base_url=base).json
        self.assertEqual(own['appointments'][0]['status'],'confirmed');self.assertIsNone(own['result']);self.assertTrue(own['assessment_completed'])
        for route in ('/cliente/','/cliente/questionario','/cliente/risultato','/cliente/appuntamento','/v/'+self.code,'/static/customer.js','/static/customer.css'):
            self.assertEqual(client.get(route,base_url=base).status_code,200)
