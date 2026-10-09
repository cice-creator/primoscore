"""Workbook parsing and isolated, reviewed imports; synthetic temporary studios only."""
import io
import json
from pathlib import Path
from unittest.mock import patch
import unittest
from uuid import uuid4
from xml.sax.saxutils import escape
import zipfile
from test_auth import AuthFixture
from primoscore_server.auth import AuthError
from primoscore_server.partner_import import PartnerImport,read_xlsx,review,HEADERS,KEYS,NS
from primoscore_server.web import create_app


def fixture(rows, *, epoch=False, formula=None, extra=None, headers=HEADERS):
    """Minimal XML wire fixtures, not a user-facing spreadsheet authoring path."""
    def col(i):return (chr(64+i//26) if i>=26 else '')+chr(65+i%26)
    def cells(values,row):
        output=[]
        for i,value in enumerate(values):
            ref=col(i)+str(row)
            if formula==ref:output.append(f'<c r="{ref}"><f>1+1</f><v>2</v></c>')
            elif type(value) in (int,float):output.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:output.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        return f'<row r="{row}">'+''.join(output)+'</row>'
    content='<worksheet xmlns="'+NS['s']+'"><sheetData>'+cells(headers,5)+''.join(cells([r.get(k,'') for k in KEYS],n+6) for n,r in enumerate(rows))+'</sheetData></worksheet>'
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="'+NS['s']+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr date1904="'+str(int(epoch))+'"/><sheets><sheet name="Partner" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',content)
        for name,value in (extra or {}).items():z.writestr(name,value)
    return out.getvalue()


def row(name='Partner sintetico',city='Comune sintetico',**fields):return dict(name=name,city=city,category='Commercialista',**fields)


class ReaderTest(unittest.TestCase):
    def test_shipped_model_is_empty_and_has_expected_headers(self):
        path=Path(__file__).resolve().parents[1]/'primoscore_server/assets/Primoscore-Modello-Partner.xlsx'
        self.assertEqual(read_xlsx(path.read_bytes()),[])
        with zipfile.ZipFile(path) as z:
            sheet=z.read('xl/worksheets/sheet1.xml')
            self.assertIn(b'dataValidation',sheet)
            self.assertIn(b'pane',sheet)

    def test_values_phone_dates_and_maps(self):
        result=read_xlsx(fixture([row(phone='0801234567',firstContact=45000,maps='Ingresso laterale')]))[0]
        self.assertEqual(result['phone'],'0801234567');self.assertFalse(result['numericPhone'])
        self.assertEqual(result['firstContact'],'2023-03-15');self.assertEqual(result['maps'],'Ingresso laterale')
        result=read_xlsx(fixture([row(phone=801234567,firstContact=0,lastContact=1)],epoch=True))[0]
        self.assertTrue(result['numericPhone']);self.assertEqual(result['firstContact'],'1904-01-01');self.assertEqual(result['lastContact'],'1904-01-02')

    def test_formula_and_malformed_archives_rejected(self):
        for blob in (b'not excel',fixture([row()],formula='A6'),fixture([row()],extra={'xl/vbaProject.bin':b'macro'}),b'a'*(5*1024*1024+1),fixture([row()],extra={'oversized':b'x'*(31*1024*1024)})):
            with self.subTest(size=len(blob)),self.assertRaises(ValueError):read_xlsx(blob)
        self.assertEqual(read_xlsx(fixture([row()],formula='L6'))[0]['maps'],'')

    def test_entity_declarations_in_utf16_rejected(self):
        from primoscore_server.partner_import import xml
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('x','<!DOCTYPE x [<!ENTITY a "hi">]><x>&a;</x>'.encode('utf-16'))
        with zipfile.ZipFile(b) as z,self.assertRaises(ValueError):xml(z,'x')

    def test_row_limit_and_headers(self):
        with self.assertRaises(ValueError):read_xlsx(fixture([row()]*501))
        with self.assertRaises(ValueError):read_xlsx(fixture([row()],headers=['Wrong headers']))
        with self.assertRaises(ValueError):read_xlsx(fixture([row()],headers=HEADERS+[HEADERS[0]]))
        with self.assertRaises(ValueError):review([row(row=6),row(row=6)],{'contacts':[],'cities':[]})
        with self.assertRaises(ValueError):review([],{'contacts':[],'cities':[]})


class ImportTest(AuthFixture):
    def setUp(self):
        super().setUp();self.actor,self.login,_,self.master=self.active_consultant()
        self.session=self.login['session_token'];self.service=PartnerImport(self.auth)

    def preview(self,rows):return self.service.preview(self.session,rows)
    def commit(self,report,selection):return self.service.commit(self.session,report['importId'],selection)
    def snapshot(self):return self.service.snapshot(self.session)
    def cmd(self,action,**data):return self.service.command(self.session,dict(action=action,data=data,request_id=str(uuid4())))

    def test_review_does_not_populate_operational_archive(self):
        r=self.preview([row(phone='0801234567'),row(name='',email='invalid'),row(name='Secondo',city='COMUNE SINTETICO')])
        self.assertEqual(r['counts'],dict(ready=2,duplicate=0,error=1));self.assertEqual(r['newCities'],['Comune sintetico'])
        s=self.snapshot()
        for key in ('cities','partners','voucher_lots','deliveries','activities'):self.assertEqual(s[key],[])

    def test_full_500_row_import_and_followup_review(self):
        r=self.preview([row(name='Partner sintetico '+str(i),notes='Annotazione di prova '*30) for i in range(500)])
        self.assertEqual(r['counts']['ready'],500)
        receipt=self.commit(r,list(range(500)));self.assertEqual(receipt['inserted'],500)
        self.assertEqual(self.preview([row(name='Partner sintetico 499')])['counts']['duplicate'],1)
        self.assertEqual(len(self.snapshot()['partners']),500)

    def test_selected_rows_new_cities_history_and_idempotency(self):
        r=self.preview([row(phone='0801234567',categoryDetail='Studio',province='XX',district='Centro',maps='Ingresso a destra',lastContact='01/01/2025',voucherQuantity='12',voucherCode='STORICO',deliveryDate='2025-01-01',leadCount='4'),row(name='Escluso',city='Altra città')])
        receipt=self.commit(r,[0]);self.assertEqual(receipt['inserted'],1)
        self.assertEqual(self.commit(r,[0]),receipt)
        with self.assertRaises(AuthError):self.commit(r,[1])
        s=self.snapshot();self.assertEqual(len(s['cities']),1);self.assertEqual(len(s['partners']),1)
        self.assertEqual(s['partners'][0]['category'],'Commercialisti');self.assertEqual(s['partners'][0]['details']['phone'],'0801234567')
        notes=s['partners'][0]['details']['notes']
        for text in ('Dettaglio categoria: Studio','Provincia: XX','Quartiere / zona: Centro','Indicazioni: Ingresso a destra','Ultimo contatto: 2025-01-01','Voucher consegnati: 12','Lead attribuiti: 4'):self.assertIn(text,notes)
        self.assertEqual(s['partners'][0]['details']['leads'],0)
        self.assertEqual(s['voucher_lots'][0]['printed'],0);self.assertEqual(s['deliveries'],[])
        with self.db.transaction() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM clients').fetchone()[0],0)

    def test_duplicates_same_file_existing_and_archived(self):
        r=self.preview([row(email='first@example.invalid',phone='0801234567'),row(name='partner SINTETICO',city=' COMUNE   SINTETICO '),row(name='Altro',mobile='+39 0801234567'),row(name='Terzo',email='FIRST@example.invalid')])
        self.assertEqual(r['counts'],dict(ready=1,duplicate=3,error=0))
        receipt=self.commit(r,[0]);p=self.snapshot()['partners'][0]
        self.cmd('partner.status',id=p['id'],revision=p['revision'],status='archived')
        r=self.preview([row(name='Nuovo nome',city='Nuova città',email='FIRST@example.invalid')])
        self.assertEqual(r['rows'][0]['kind'],'duplicate')
        with self.assertRaises(AuthError):self.commit(r,[0])
        self.assertEqual(len(self.snapshot()['cities']),1)

    def test_error_correction_and_long_notes(self):
        r=self.preview([row(name='',nextDate='2025-01-01'),row(name='Lungo',notes='a'*10000,district='Centro'),row(name='Data',lastContact='2999-01-01'),row(name='Quantità',leadCount='-1')])
        self.assertEqual(r['counts']['error'],4)
        fixed=[{**r['rows'][0]['fields'],'name':'Corretto','nextAction':'Telefonare'}]
        corrected=self.preview(fixed);self.assertEqual(corrected['counts']['ready'],1)
        self.commit(corrected,[0]);self.assertEqual(len(self.snapshot()['partners']),1)

    def test_stale_review_rechecked_before_commit(self):
        r=self.preview([row()]);self.cmd('city.create',name='Modifica successiva')
        with self.assertRaises(AuthError) as e:self.commit(r,[0])
        self.assertEqual(e.exception.status,409);self.assertEqual(len(self.snapshot()['partners']),0)
        updated=self.preview([r['rows'][0]['fields']]);self.commit(updated,[0])
        self.assertEqual(len(self.snapshot()['partners']),1)

    def test_atomic_rollback_if_second_write_fails(self):
        r=self.preview([row(),row(name='Secondo',city='Altra città')]);original=self.service._apply
        def fail(c,t,action,data):
            if action=='partner.create' and data['name']=='Secondo':raise AuthError('Simulazione errore')
            return original(c,t,action,data)
        with patch.object(self.service,'_apply',side_effect=fail),self.assertRaises(AuthError):self.commit(r,[0,1])
        self.assertEqual(self.snapshot()['partners'],[]);self.assertEqual(self.snapshot()['cities'],[])
        self.assertEqual(self.commit(r,[0,1])['inserted'],2)

    def test_expired_batch_and_invalid_selection(self):
        r=self.preview([row()])
        for s in ([],[True],[0,0],[1],[-1],['0']):
            with self.subTest(s=s),self.assertRaises(AuthError):self.commit(r,s)
        with self.db.transaction() as c:c.execute('UPDATE partner_imports SET created_at=created_at-86401')
        with self.assertRaises(AuthError):self.commit(r,[0])
        self.assertEqual(self.snapshot()['partners'],[])

    def test_tenant_isolation_master_scope_and_batch_owner(self):
        r=self.preview([row()]);other,login,_=self.enrolled('other@example.invalid');self.auth.activate(self.master['session_token'],other['tenant_id'])
        other_token=login['session_token']
        with self.assertRaises(AuthError):self.service.commit(other_token,r['importId'],[0])
        with self.assertRaises(AuthError):self.service.preview(other_token,[row()],tenant=self.actor['tenant_id'])
        self.commit(r,[0])
        other_preview=self.service.preview(other_token,[row()]);self.assertEqual(other_preview['counts']['ready'],1)
        with self.assertRaises(AuthError):self.service.preview(self.master['session_token'],[row(name='Master')],tenant=self.actor['tenant_id'])
        scope=dict(tenant=self.actor['tenant_id'],reason='Assistenza richiesta')
        report=self.service.preview(self.master['session_token'],[row(name='Master')],**scope)
        with self.assertRaises(AuthError):self.commit(report,[0])
        self.service.commit(self.master['session_token'],report['importId'],[0],**scope)
        with self.db.transaction() as c:
            event=c.execute("SELECT reason FROM audit_events WHERE action='commit_partner_import' AND actor_id=(SELECT id FROM accounts WHERE role='master')").fetchone()
            self.assertEqual(event[0],scope['reason'])

    def test_customer_and_suspended_studio_cannot_import(self):
        from primoscore_server.repository import Repository
        from test_auth import PASSWORD
        client=Repository(self.db,self.actor['id']).create('clients',{'first_name':'Cliente','email':'customer@example.invalid'})
        self.auth.invite_customer(self.session,client['id']);account=self.account('customer@example.invalid')
        self.auth.reset_password(self.token(account['id']),PASSWORD,invite=True)
        with self.db.transaction() as c:slug=c.execute('SELECT slug FROM tenants WHERE id=?',(self.actor['tenant_id'],)).fetchone()[0]
        customer=self.auth.login(account['email'],PASSWORD,'customer',slug,'customer')['session_token']
        r=self.preview([row()])
        for action in (lambda:self.service.preview(customer,[row()]),lambda:self.service.authorize(customer),lambda:self.service.commit(customer,r['importId'],[0])):
            with self.assertRaises(AuthError):action()
        with self.db.transaction() as c:c.execute("UPDATE tenants SET status='suspended' WHERE id=?",(self.actor['tenant_id'],))
        with self.assertRaises(AuthError):self.commit(r,[0])

    def test_http_upload_template_csrf_sizes_and_confirmation(self):
        app=create_app(self.db,self.key,self.auth.origin,auth=self.auth);app.testing=True
        client=app.test_client();base=dict(base_url=self.auth.origin)
        self.assertEqual(client.get('/api/workspace/import/template',**base).status_code,401)
        client.set_cookie('__Host-ps_session',self.session,domain='primoscore.example')
        csrf=client.get('/api/auth/csrf',**base).json['csrf'];headers={'Origin':self.auth.origin,'X-CSRF-Token':csrf}
        template=client.get('/api/workspace/import/template',**base)
        self.assertEqual(template.status_code,200);self.assertEqual(read_xlsx(template.data),[])
        template.close()
        def upload(blob=fixture([row()]),h=headers):
            response=client.post('/api/workspace/import/preview',data={'file':(io.BytesIO(blob),'partner.xlsx')},headers=h,**base)
            response.get_data();response.close();response.request.close()
            return response
        self.assertEqual(upload(h={'Origin':self.auth.origin}).status_code,403)
        self.assertEqual(upload(h={**headers,'Origin':'https://other.example'}).status_code,403)
        self.assertEqual(upload(b'x'*(5*1024*1024+1)).status_code,400)
        self.assertEqual(upload(b'x'*(8*1024*1024+1)).status_code,413)
        self.assertEqual(upload(b'bad').status_code,400)
        report=upload();self.assertEqual(report.status_code,200);self.assertEqual(len(report.json['columns']),27)
        result=client.post('/api/workspace/import/commit',json={'importId':report.json['importId'],'selection':[0]},headers=headers,**base)
        self.assertEqual(result.status_code,200);self.assertEqual(result.json['inserted'],1)
        response=client.post('/api/workspace/commands',data={'file':(io.BytesIO(b'x'),'x')},headers=headers,**base)
        self.assertEqual(response.status_code,415)
