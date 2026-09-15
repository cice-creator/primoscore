"""Partner workbook reader adapted from CiceroEV; no data or runtime dependencies.

The review and commit boundary is native Primoscore and scoped to a verified studio.
"""
import hashlib
import io
import json
import posixpath
import re
import unicodedata
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
from .auth import AuthError
from .repository import encode, new_id, view
from .workspace import Workspace, DEFAULTS, STATUSES, details


def norm(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def canonical_city(value, known):
    value = ' '.join(unicodedata.normalize('NFKC', value).split()) or 'Altro'
    if len(value)>100 or not any(ch.isalpha() for ch in value) or any(unicodedata.category(ch).startswith('C') for ch in value):
        raise ValueError('Inserisci una città valida, fino a 100 caratteri.')
    return next((name for name in known if norm(name)==norm(value)), value[0].upper()+value[1:])

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
HEADERS=['Nome partner / attività','Categoria','Dettaglio categoria','Referente','Telefono','Cellulare','Email','Città / località','Provincia','Quartiere / zona','Indirizzo','Indicazioni','Sito / fonte','Priorità','Stato contatto','Primo contatto','Ultimo contatto','Esito','Prossima azione','Data richiamo','Voucher consegnati','Data consegna','Codice voucher','Lead attribuiti','Note','Verifica recapiti','Data verifica']
KEYS=['name','category','categoryDetail','referent','phone','mobile','email','city','province','district','address','maps','source','priority','status','firstContact','lastContact','outcome','nextAction','nextDate','voucherQuantity','deliveryDate','voucherCode','leadCount','notes','verification','researchDate']
ALIASES={'Agenzia immobiliare':'Agenzie immobiliari','Commercialista':'Commercialisti','CAF / Patronato':'CAF o similari','Consulente finanziario':'Consulenti finanziari'}
STATE_ALIASES={'Appuntamento':'Incontro fissato','Visitato':'Incontro svolto'}
DATES={'firstContact','lastContact','nextDate','deliveryDate','researchDate'}

def xml(z,name):
    content=z.read(name)
    # Removing NULs also detects declarations in UTF-16/32 XML.
    declarations=content.replace(b'\x00',b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:raise ValueError('Il file contiene elementi XML non supportati.')
    return ET.fromstring(content)

def read_xlsx(content):
    if len(content)>5*1024*1024:raise ValueError('Il file supera 5 MB.')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            if len(z.infolist())>1000 or sum(x.file_size for x in z.infolist())>30*1024*1024:raise ValueError('Il file è troppo grande una volta aperto.')
            if any(x.flag_bits&1 for x in z.infolist()):raise ValueError('Rimuovi la password dal file prima di importarlo.')
            if len(set(z.namelist()))!=len(z.infolist()):raise ValueError('Il file contiene parti duplicate.')
            if any('vbaproject' in name.casefold() for name in z.namelist()):raise ValueError('Salva il modello come .xlsx senza macro.')
            ss=[]
            if 'xl/sharedStrings.xml' in z.namelist():ss=[''.join(t.text or '' for t in x.iter('{'+NS['s']+'}t')) for x in xml(z,'xl/sharedStrings.xml')]
            wb=xml(z,'xl/workbook.xml');sheet=next((s for s in wb.findall('s:sheets/s:sheet',NS) if s.attrib['name'].strip().casefold()=='partner'),None)
            if sheet is None:raise ValueError('Non trovo il foglio Partner. Usa il modello scaricabile qui.')
            rid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            rel=next(r for r in xml(z,'xl/_rels/workbook.xml.rels') if r.attrib['Id']==rid)
            if rel.attrib.get('TargetMode')=='External':raise ValueError('Il foglio Partner deve essere contenuto nel file.')
            target=posixpath.normpath(posixpath.join('xl',rel.attrib['Target'])) if not rel.attrib['Target'].startswith('/') else rel.attrib['Target'].lstrip('/')
            if not target.startswith('xl/worksheets/'):raise ValueError('Percorso del foglio non valido.')
            if wb.find('s:workbookPr',NS) is not None and wb.find('s:workbookPr',NS).get('date1904') in ['1','true']:epoch=datetime(1904,1,1)
            else:epoch=datetime(1899,12,30)
            rows=xml(z,target).findall('s:sheetData/s:row',NS);columns=None;result=[]
            names={norm(h):k for h,k in zip(HEADERS,KEYS)}
            for row in rows:
                cells={};numeric=set()
                for c in row.findall('s:c',NS):
                    column=re.sub(r'\d','',c.attrib['r']);value=c.find('s:v',NS)
                    if c.find('s:f',NS)is not None:
                        if columns and columns.get(column) not in [None,'maps']:raise ValueError('Il file contiene formule nei dati. Incolla i valori prima di importare.')
                        continue
                    if c.attrib.get('t')=='s':value=ss[int(value.text)] if value is not None else ''
                    elif c.attrib.get('t')=='inlineStr':value=''.join(t.text or '' for t in c.findall('.//s:t',NS))
                    else:
                        value=value.text if value is not None else ''
                        if value and c.attrib.get('t','n')=='n':numeric.add(column)
                    cells[column]=str(value or '').strip()
                if columns is None:
                    candidate={c:names.get(norm(v)) for c,v in cells.items()}
                    if 'name' in candidate.values() and 'category' in candidate.values():
                        recognized=[v for v in candidate.values() if v]
                        if len(set(recognized))!=len(recognized):raise ValueError('Le intestazioni contengono colonne duplicate.')
                        columns=candidate
                    continue
                item={k:cells.get(c,'') for c,k in columns.items() if k}
                if not any(item.values()):continue
                for c,k in columns.items():
                    if k in DATES and c in numeric and item.get(k):
                        try:item[k]=(epoch+timedelta(days=float(item[k]))).date().isoformat()
                        except (ValueError,OverflowError):pass
                item['row']=int(row.attrib['r'])
                item['numericPhone']=any(columns.get(c) in ['phone','mobile'] for c in numeric)
                result.append(item)
                if len(result)>500:raise ValueError('Sono supportate al massimo 500 righe compilate per importazione.')
            if columns is None:raise ValueError('Intestazioni non riconosciute. Non modificare la riga dei titoli del modello.')
            return result
    except (zipfile.BadZipFile,ET.ParseError,KeyError,StopIteration,IndexError,OverflowError,RuntimeError,NotImplementedError):raise ValueError('Non riesco a leggere il file. Salvalo come Excel .xlsx usando il modello.')

def phone_key(value):
    digits=re.sub(r'\D','',str(value or ''))
    if digits.startswith('0039'):return digits[4:]
    if digits.startswith('39') and len(digits)>10:return digits[2:]
    return digits


def partner_details(fields, row):
    result={k:v for k,v in fields.items() if k in DEFAULTS}
    result['verification']=result.get('verification') or 'Da verificare'
    result['sourceNotes']='Importazione Excel · riga '+str(row)
    extra=[HEADERS[KEYS.index(k)]+': '+fields[k] for k in ('categoryDetail','province','district','maps','lastContact') if fields[k]]
    history=[HEADERS[KEYS.index(k)]+': '+fields[k] for k in ('voucherQuantity','deliveryDate','voucherCode','leadCount') if fields[k] not in ('','0')]
    notes=[fields['notes']]
    if extra:notes.append('Dettagli dal modello Excel:\n'+'\n'.join(extra))
    if history:notes.append('Storico Excel da riconciliare (nessun movimento registrato):\n'+'\n'.join(history))
    result['notes']='\n\n'.join(n for n in notes if n)
    return details(result)


def review(rows, state):
    if not isinstance(rows,list) or not 1<=len(rows)<=500:
        raise ValueError('Compila almeno un partner. Sono supportate al massimo 500 righe.')
    contacts=list(state['contacts']);known=list(state['cities']);output=[];numbers_seen=set()
    for n,raw in enumerate(rows):
        if not isinstance(raw,dict) or set(raw)-set(KEYS)-{'row','numericPhone'}:
            raise ValueError('Campi del modello non riconosciuti.')
        row=raw.get('row',n+6)
        if type(row) is not int or not 1<=row<=1048576 or row in numbers_seen:
            raise ValueError('Numero di riga non valido o ripetuto.')
        numbers_seen.add(row)
        if any(not isinstance(raw.get(k,''),str) for k in KEYS):raise ValueError('I campi devono contenere testo o valori Excel convertiti in testo.')
        p={k:unicodedata.normalize('NFC',raw.get(k,'').strip()) for k in KEYS}
        issues=[];warnings=[]
        if any(len(v)>10000 for v in p.values()):issues.append('Un campo supera la lunghezza consentita.')
        p['category']=ALIASES.get(p['category'],p['category'] or 'Altro')
        try:
            p['city']=canonical_city(p['city'],known)
            if p['city'] not in known:known.append(p['city'])
        except ValueError as exc:issues.append(str(exc))
        p['priority']=p['priority'] or 'Media'
        p['status']=STATE_ALIASES.get(p['status'],p['status'] or 'Da contattare')
        p['verification']=p['verification'] or 'Da verificare'
        if not p['name']:issues.append('Inserisci il nome del partner.')
        elif len(p['name'])>250:issues.append('Il nome supera 250 caratteri.')
        if len(p['category'])>100:issues.append('Categoria troppo lunga.')
        for k,label in (('phone','telefono'),('mobile','cellulare')):
            if p[k] and not 6<=len(re.sub(r'\D','',p[k]))<=15:issues.append('Controlla il '+label+'.')
        if raw.get('numericPhone'):warnings.append('Telefono salvato come numero in Excel: controlla eventuali zeri iniziali mancanti.')
        for k in DATES:
            if p[k]:
                try:
                    if re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}',p[k]):p[k]=datetime.strptime(p[k],'%d/%m/%Y').date().isoformat()
                    parsed=datetime.strptime(p[k],'%Y-%m-%d').date()
                    p[k]=parsed.isoformat()
                    if k!='nextDate' and parsed>datetime.now().date():issues.append('La data non può essere futura: '+HEADERS[KEYS.index(k)]+'.')
                except ValueError:issues.append('Data non valida: '+HEADERS[KEYS.index(k)]+'.')
        for k in ('voucherQuantity','leadCount'):
            if p[k] and (not re.fullmatch(r'\d{1,7}',p[k]) or int(p[k])>1000000):issues.append('Inserisci un numero intero tra 0 e 1.000.000: '+HEADERS[KEYS.index(k)]+'.')
        try:encode(partner_details(p,row))
        except (AuthError,ValueError) as exc:issues.append(str(exc))
        if p['city'] not in state['cities']:warnings.append('Nuova città: sarà aggiunta con il partner alla conferma.')
        if not any(p[k] for k in ('phone','mobile','email','source')):warnings.append('Nessun recapito: puoi completarlo in seguito.')
        if any(p[k] not in ('','0') for k in ('voucherQuantity','leadCount')) or p['voucherCode'] or p['deliveryDate']:
            warnings.append('Voucher e lead storici restano annotazioni: nessuna stampa, consegna o cliente verrà creato.')
        numbers={phone_key(p[k]) for k in ('phone','mobile') if phone_key(p[k])}
        matches=[c for c in contacts if (p['name'] and norm(p['name'])==norm(c['name']) and norm(p['city'])==norm(c['city'])) or (numbers & {phone_key(c.get(k,'')) for k in ('phone','mobile')}) or (p['email'] and p['email'].casefold()==c.get('email','').casefold())]
        duplicate=None
        if matches:
            match=matches[0]
            duplicate={'name':match['name'],'reason':'Nome e località o recapito già presenti nel file.' if 'fileRow' in match else 'Possibile corrispondenza nell’archivio, incluso il cestino. Il partner esistente non sarà modificato.'}
            duplicate.update({'row':match['fileRow']} if 'fileRow' in match else {'id':match['id']})
        kind='error' if issues else 'duplicate' if duplicate else 'ready'
        if kind=='ready':contacts.append({**p,'fileRow':row})
        output.append(dict(fields=p,row=row,numericPhone=bool(raw.get('numericPhone')),kind=kind,issues=list(dict.fromkeys(issues)),warnings=warnings,duplicate=duplicate))
    return dict(rows=output,counts={k:sum(r['kind']==k for r in output) for k in ('ready','duplicate','error')},newCities=list(dict.fromkeys(r['fields']['city'] for r in output if r['kind']=='ready' and r['fields']['city'] not in state['cities'])))


class PartnerImport(Workspace):
    def _state(self,c,tenant):
        cities=[dict(r) for r in c.execute('SELECT * FROM cities WHERE tenant_id=? ORDER BY id',(tenant,))]
        partners=[view(r) for r in c.execute('SELECT * FROM partners WHERE tenant_id=? ORDER BY id',(tenant,))]
        names={r['id']:r['name'] for r in cities}
        revision=hashlib.sha256(json.dumps([cities,partners],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return dict(cities=list(names.values()),contacts=[{**p['details'],'id':p['id'],'name':p['name'],'city':names.get(p['city_id'],'')} for p in partners],revision=revision)

    def authorize(self,token,tenant=None,reason=''):
        with self.db.transaction() as c:
            actor,tenant,reason=self._scope(c,token,tenant,reason)
            self._audit(c,actor,tenant,reason,'download_partner_template')

    def preview(self,token,rows,tenant=None,reason=''):
        with self.db.transaction() as c:
            actor,tenant,reason=self._scope(c,token,tenant,reason)
            state=self._state(c,tenant)
            try:report=review(rows,state)
            except ValueError as exc:raise AuthError(str(exc)) from exc
            # Temporary reviews are owned by both the studio and the account.
            c.execute('DELETE FROM partner_imports WHERE tenant_id=? AND created_at<? AND result_json IS NULL',(tenant,int(self.auth.clock())-86400))
            ident=new_id()
            c.execute('INSERT INTO partner_imports(tenant_id,id,actor_id,rows_json,revision,created_at) VALUES(?,?,?,?,?,?)',(tenant,ident,actor['id'],json.dumps(rows,ensure_ascii=False),state['revision'],int(self.auth.clock())))
            self._audit(c,actor,tenant,reason,'preview_partner_import',ident)
            return dict(ok=True,importId=ident,**report)

    def commit(self,token,ident,selection,tenant=None,reason=''):
        if not isinstance(ident,str) or len(ident)>128:raise AuthError('Identificativo importazione non valido.')
        if not isinstance(selection,list) or not selection or len(selection)>500 or any(type(i) is not int or i<0 for i in selection) or len(set(selection))!=len(selection):raise AuthError('Seleziona almeno un nuovo partner valido.')
        selection=sorted(selection)
        with self.db.transaction() as c:
            actor,tenant,reason=self._scope(c,token,tenant,reason)
            batch=c.execute('SELECT * FROM partner_imports WHERE tenant_id=? AND id=? AND actor_id=?',(tenant,ident,actor['id'])).fetchone()
            if not batch:raise AuthError('Controllo del file non disponibile. Carica nuovamente il file.',404)
            if batch['result_json']:
                receipt=json.loads(batch['result_json'])
                if receipt['selection']!=selection:raise AuthError('File già confermato con una selezione diversa.',409)
                return receipt
            if batch['created_at']<int(self.auth.clock())-86400:raise AuthError('Controllo scaduto. Ricontrolla il file.',409)
            state=self._state(c,tenant)
            if state['revision']!=batch['revision']:raise AuthError('Archivio aggiornato nel frattempo. Premi Ricontrolla prima di confermare.',409)
            try:report=review(json.loads(batch['rows_json']),state)
            except ValueError as exc:raise AuthError(str(exc)) from exc
            if any(i>=len(report['rows']) or report['rows'][i]['kind']!='ready' for i in selection):raise AuthError('La selezione contiene errori o possibili duplicati.',409)
            cities={norm(r['name']):r['id'] for r in c.execute('SELECT id,name FROM cities WHERE tenant_id=?',(tenant,))}
            ids=[];new_cities=[]
            for i in selection:
                row=report['rows'][i];p=row['fields'];key=norm(p['city'])
                if key not in cities:
                    cities[key]=self._apply(c,tenant,'city.create',{'name':p['city']})['id'];new_cities.append(p['city'])
                partner=self._apply(c,tenant,'partner.create',dict(name=p['name'],category=p['category'],city_id=cities[key],details=partner_details(p,row['row'])))
                ids.append(partner['id'])
            receipt=dict(ok=True,inserted=len(ids),partnerIds=ids,newCities=new_cities,selection=selection)
            c.execute('UPDATE partner_imports SET result_json=? WHERE tenant_id=? AND id=?',(encode(receipt),tenant,ident))
            self._audit(c,actor,tenant,reason,'commit_partner_import',ident)
            return receipt
