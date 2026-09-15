"""Password, email, TOTP and server-side session workflows. No default accounts."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit, urlencode

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet
import pyotp

from .database import now
from .repository import new_id


class AuthError(Exception):
    def __init__(self, message='Accesso non disponibile. Controlla i dati e riprova.', status=400):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def email_address(value):
    if not isinstance(value, str):
        raise AuthError('Inserisci un indirizzo email valido.')
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', value):
        raise AuthError('Inserisci un indirizzo email valido.')
    return value


def password_value(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise AuthError('Usa una password da 12 a 128 caratteri.')
    return value


def consultant_profile(data):
    required = ('first_name','last_name','email','mobile','landline','office_address','office_postcode','office_city','office_province','oam_number')
    optional = ('business_name','ivass_number','tax_code','vat_number')
    allowed = set(required + optional) | {'password','ivass_registered'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise AuthError('Campi di registrazione non validi.')
    result = {}
    for field in required + optional:
        value = data.get(field, '')
        if not isinstance(value, str) or len(value) > 250 or (field in required and not value.strip()):
            raise AuthError('Completa i dati richiesti del consulente e della sede.')
        result[field] = value.strip()
    result['email'] = email_address(result['email'])
    for field in ('mobile','landline'):
        if not re.fullmatch(r'\+?[0-9 ()-]{6,24}', result[field]):
            raise AuthError('Controlla cellulare e telefono fisso.')
    result['oam_number'] = result['oam_number'].upper().replace(' ', '')
    result['office_province'] = result['office_province'].upper()
    if not re.fullmatch(r'M[0-9]{1,10}', result['oam_number']):
        raise AuthError('Inserisci il numero OAM con prefisso M.')
    if not re.fullmatch(r'[0-9]{5}', result['office_postcode']) or not re.fullmatch(r'[A-Z]{2}', result['office_province']):
        raise AuthError('Controlla CAP e sigla della provincia.')
    if data.get('ivass_registered') not in ('yes','no'):
        raise AuthError('Indica se hai un’iscrizione IVASS.')
    if data['ivass_registered'] == 'yes' and not result['ivass_number']:
        raise AuthError('Inserisci il numero di iscrizione IVASS.')
    if data['ivass_registered'] == 'no':
        result['ivass_number'] = ''
    return result


class Auth:
    def __init__(self, database, encryption_key, origin, *, clock=time.time, hasher=None):
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('Origine applicazione non valida.')
        if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1','localhost'):
            raise ValueError('HTTPS richiesto fuori dall’ambiente locale.')
        self.db, self.origin, self.clock = database, origin.rstrip('/'), clock
        self.key = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        self.cipher = Fernet(self.key)
        self.hasher = hasher or PasswordHasher()
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def timestamp(self):
        return int(self.clock())

    def _event(self, c, account, event):
        c.execute('INSERT INTO auth_events VALUES(?,?,?,?)', (new_id(), account, event, self.timestamp()))

    def rate(self, action, identity, limit=10, seconds=900):
        bucket = hmac.new(self.key, (action + ':' + str(identity)).encode(), hashlib.sha256).hexdigest()
        timestamp = self.timestamp()
        # Commit even failed attempts; do not roll back this counter with the operation.
        with self.db.transaction() as c:
            old = c.execute('SELECT * FROM auth_rate_limits WHERE bucket=?', (bucket,)).fetchone()
            count = old['attempts'] + 1 if old and old['window_start'] + seconds > timestamp else 1
            start = old['window_start'] if old and old['window_start'] + seconds > timestamp else timestamp
            c.execute('INSERT INTO auth_rate_limits VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,attempts=excluded.attempts', (bucket,start,count))
        if count > limit:
            raise AuthError('Troppi tentativi. Attendi e riprova.', 429)

    def _credentials(self, c, account_id):
        return c.execute('SELECT a.*,p.password_hash,p.email_verified,p.totp_encrypted,p.totp_last_step FROM accounts a JOIN auth_credentials p ON p.account_id=a.id WHERE a.id=?', (account_id,)).fetchone()

    def _usable(self, c, actor):
        if not actor or actor['status'] == 'suspended':
            return False
        if actor['tenant_id']:
            tenant = c.execute('SELECT status FROM tenants WHERE id=?', (actor['tenant_id'],)).fetchone()
            if not tenant or tenant['status'] == 'suspended':
                return False
        return True

    def _password_matches(self, stored, password):
        if not isinstance(password, str) or len(password) > 128:
            return False
        try:
            return self.hasher.verify(stored or self.dummy_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def _mail_token(self, c, actor, purpose):
        token = secrets.token_urlsafe(32)
        timestamp = self.timestamp()
        c.execute('UPDATE auth_tokens SET used_at=? WHERE account_id=? AND purpose=? AND used_at IS NULL', (timestamp,actor['id'],purpose))
        c.execute('INSERT INTO auth_tokens VALUES(?,?,?,?,NULL)', (digest(token),actor['id'],purpose,timestamp+(1800 if purpose=='reset' else 86400)))
        route = {'verify':'conferma','reset':'nuova-password','invite':'invito'}[purpose]
        # Fragment prevents the secret from entering request logs and referrer headers.
        query = {'tipo':actor['role']}
        if actor['tenant_id']:
            query['studio'] = c.execute('SELECT slug FROM tenants WHERE id=?',(actor['tenant_id'],)).fetchone()['slug']
        payload = {'to':actor['email'],'purpose':purpose,'url':self.origin+'/accesso/'+route+'?'+urlencode(query)+'#token='+token}
        c.execute('INSERT INTO auth_mail(id,account_id,payload_encrypted,created_at) VALUES(?,?,?,?)', (new_id(),actor['id'],self.cipher.encrypt(json.dumps(payload).encode()).decode(),timestamp))

    def register(self, data, ip):
        self.rate('register-ip', ip, 5, 3600)
        profile = consultant_profile(data)
        self.rate('register-email', profile['email'], 3, 3600)
        hashed = self.hasher.hash(password_value(data.get('password')))
        with self.db.transaction() as c:
            if c.execute("SELECT id FROM accounts WHERE role='consultant' AND email=?", (profile['email'],)).fetchone():
                return
            tenant, account = new_id(), new_id()
            c.execute('INSERT INTO tenants VALUES(?,?,?,?)', (tenant,'studio-'+secrets.token_hex(6),'draft',now()))
            c.execute('INSERT INTO consultant_profiles(tenant_id,'+','.join(profile)+') VALUES('+','.join('?' for _ in range(len(profile)+1))+')', [tenant,*profile.values()])
            c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)', (account,tenant,None,'consultant',profile['email'],'pending',now()))
            c.execute('INSERT INTO auth_credentials(account_id,password_hash,password_changed_at) VALUES(?,?,?)', (account,hashed,self.timestamp()))
            actor = self._credentials(c, account)
            self._mail_token(c, actor, 'verify')
            self._event(c, account, 'registered')

    def bootstrap_master(self, email, password):
        """Trusted local administration only; deliberately absent from HTTP routes."""
        email, hashed = email_address(email), self.hasher.hash(password_value(password))
        with self.db.transaction() as c:
            if c.execute("SELECT 1 FROM accounts WHERE role='master'").fetchone():
                raise AuthError('Esiste già un Master. Il comando iniziale non è più disponibile.')
            account = new_id()
            c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)', (account,None,None,'master',email,'pending',now()))
            c.execute('INSERT INTO auth_credentials(account_id,password_hash,password_changed_at) VALUES(?,?,?)', (account,hashed,self.timestamp()))
            self._mail_token(c, self._credentials(c,account), 'verify')
            self._event(c, account, 'master_provisioned')

    def _token(self, c, token, purpose):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise AuthError('Collegamento non valido o scaduto.')
        row = c.execute('SELECT * FROM auth_tokens WHERE token_hash=? AND purpose=? AND used_at IS NULL AND expires_at>?', (digest(token),purpose,self.timestamp())).fetchone()
        if not row or not self._usable(c, self._credentials(c,row['account_id'])):
            raise AuthError('Collegamento non valido o scaduto.')
        return row

    def verify_email(self, token):
        with self.db.transaction() as c:
            row = self._token(c, token, 'verify')
            c.execute('UPDATE auth_tokens SET used_at=? WHERE token_hash=?', (self.timestamp(),digest(token)))
            c.execute('UPDATE auth_credentials SET email_verified=1 WHERE account_id=?', (row['account_id'],))
            self._event(c, row['account_id'], 'email_verified')

    def _lookup(self, c, email, role, slug):
        if role not in ('master','consultant','customer'):
            return None
        if role == 'customer':
            row = c.execute("SELECT a.id FROM accounts a JOIN tenants t ON t.id=a.tenant_id WHERE a.email=? AND a.role='customer' AND t.slug=?", (email,slug)).fetchone()
        else:
            row = c.execute('SELECT id FROM accounts WHERE email=? AND role=?', (email,role)).fetchone()
        return self._credentials(c,row['id']) if row else None

    def _step(self, c, actor, kind):
        token = secrets.token_urlsafe(32)
        secret = self.cipher.encrypt(pyotp.random_base32().encode()).decode() if kind == 'enroll' else None
        c.execute('DELETE FROM auth_steps WHERE account_id=?', (actor['id'],))
        c.execute('INSERT INTO auth_steps(token_hash,account_id,kind,pending_secret,expires_at) VALUES(?,?,?,?,?)', (digest(token),actor['id'],kind,secret,self.timestamp()+600))
        return {'step_token':token,'step':kind}

    def _session(self, c, actor):
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)', (digest(token),actor['id'],self.timestamp(),self.timestamp()+28800,self.timestamp()))
        self._event(c,actor['id'],'signed_in')
        return {'session_token':token,'step':'complete'}

    def login(self, email, password, role, slug, ip):
        self.rate('login-ip',ip,40)
        email = email_address(email)
        self.rate('login-account',role+':'+slug+':'+email,10)
        with self.db.transaction() as c:
            actor = self._lookup(c,email,role,slug)
            valid = self._password_matches(actor['password_hash'] if actor else None,password)
            if not valid or not self._usable(c,actor):
                failure = AuthError('Credenziali non valide o accesso non disponibile.',401)
                if actor:
                    self._event(c,actor['id'],'login_failed')
                result = None
            elif not actor['email_verified']:
                failure, result = AuthError('Conferma l’email prima di accedere.',403), None
            else:
                failure = None
                if actor['role'] == 'customer':
                    result = self._session(c,actor)
                else:
                    result = self._step(c,actor,'otp' if actor['totp_encrypted'] else 'enroll')
        if failure:
            raise failure
        return result

    def _get_step(self, c, token, kind=None):
        if not isinstance(token,str) or len(token)>200:
            raise AuthError('Ripeti l’accesso.',401)
        step = c.execute('SELECT * FROM auth_steps WHERE token_hash=? AND expires_at>? AND attempts<6', (digest(token),self.timestamp())).fetchone()
        actor = self._credentials(c,step['account_id']) if step else None
        if not step or not self._usable(c,actor) or not actor['email_verified'] or (kind and step['kind'] != kind):
            raise AuthError('Ripeti l’accesso.',401)
        return step, actor

    def enrollment(self, token):
        with self.db.transaction() as c:
            step, actor = self._get_step(c,token,'enroll')
            secret = self.cipher.decrypt(step['pending_secret'].encode()).decode()
            return {'secret':secret,'uri':pyotp.TOTP(secret).provisioning_uri(name=actor['email'],issuer_name='Primoscore')}

    def _totp_counter(self, secret, code, last_step):
        if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code):
            return None
        counter = self.timestamp() // 30
        totp = pyotp.TOTP(secret)
        for candidate in (counter,counter-1,counter+1):
            if candidate>last_step and hmac.compare_digest(totp.at(candidate*30),code):
                return candidate
        return None

    def factor(self, token, code, ip):
        self.rate('factor-ip',ip,30)
        with self.db.transaction() as c:
            _, actor = self._get_step(c,token)
            account = actor['id']
        self.rate('factor-account',account,10)
        with self.db.transaction() as c:
            step, actor = self._get_step(c,token)
            code = code.strip() if isinstance(code,str) else ''
            secret = self.cipher.decrypt((step['pending_secret'] if step['kind']=='enroll' else actor['totp_encrypted']).encode()).decode()
            counter = self._totp_counter(secret,code,-1 if step['kind']=='enroll' else actor['totp_last_step'])
            recovery = step['kind']=='otp' and bool(c.execute('SELECT 1 FROM auth_recovery_codes WHERE account_id=? AND code_hash=?', (account,digest(code))).fetchone())
            if counter is None and not recovery:
                c.execute('UPDATE auth_steps SET attempts=attempts+1 WHERE token_hash=?', (digest(token),))
                self._event(c,account,'factor_failed')
                failure, result = AuthError('Codice non valido, già utilizzato o scaduto.'), None
            else:
                failure = None
                if recovery:
                    c.execute('DELETE FROM auth_recovery_codes WHERE account_id=? AND code_hash=?',(account,digest(code)))
                    c.execute('DELETE FROM auth_sessions WHERE account_id=?',(account,))
                    result = self._step(c,actor,'enroll')
                    self._event(c,account,'recovery_code_used')
                else:
                    c.execute('DELETE FROM auth_steps WHERE token_hash=?',(digest(token),))
                    codes = []
                    if step['kind']=='enroll':
                        c.execute('UPDATE auth_credentials SET totp_encrypted=? WHERE account_id=?',(step['pending_secret'],account))
                        c.execute('DELETE FROM auth_recovery_codes WHERE account_id=?',(account,))
                        c.execute('DELETE FROM auth_sessions WHERE account_id=?',(account,))
                        codes = [secrets.token_urlsafe(15) for _ in range(8)]
                        c.executemany('INSERT INTO auth_recovery_codes VALUES(?,?)',[(account,digest(value)) for value in codes])
                        if actor['role']=='master':
                            c.execute("UPDATE accounts SET status='active' WHERE id=?",(account,))
                        self._event(c,account,'authenticator_enrolled')
                    c.execute('UPDATE auth_credentials SET totp_last_step=? WHERE account_id=?',(counter,account))
                    result = self._session(c,actor)
                    if codes:
                        result['recovery_codes'] = codes
        if failure:
            raise failure
        return result

    def _resolve(self, c, token, *, full=False):
        if not isinstance(token,str) or len(token)>200:
            raise AuthError('Accedi per continuare.',401)
        session = c.execute('SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>? AND last_seen>?',(digest(token),self.timestamp(),self.timestamp()-1800)).fetchone()
        actor = self._credentials(c,session['account_id']) if session else None
        if not self._usable(c,actor) or not actor['email_verified'] or (actor['role']!='customer' and not actor['totp_encrypted']):
            raise AuthError('Accedi per continuare.',401)
        active = actor['status']=='active'
        if actor['tenant_id']:
            active = active and c.execute("SELECT status FROM tenants WHERE id=?",(actor['tenant_id'],)).fetchone()['status']=='active'
        if full and not active:
            raise AuthError('Il tuo account è in attesa di attivazione.',403)
        c.execute('UPDATE auth_sessions SET last_seen=? WHERE token_hash=?',(self.timestamp(),digest(token)))
        return actor, active

    def identity(self, token, *, full=False):
        with self.db.transaction() as c:
            actor, active = self._resolve(c,token,full=full)
            result = {'id':actor['id'],'role':actor['role'],'tenant_id':actor['tenant_id'],'email':actor['email'],'active':active}
            if actor['tenant_id']:
                result['slug'] = c.execute('SELECT slug FROM tenants WHERE id=?',(actor['tenant_id'],)).fetchone()['slug']
            if actor['role']=='consultant':
                result['profile'] = dict(c.execute('SELECT * FROM consultant_profiles WHERE tenant_id=?',(actor['tenant_id'],)).fetchone())
            return result

    def logout(self, token):
        with self.db.transaction() as c:
            session = c.execute('SELECT account_id FROM auth_sessions WHERE token_hash=?',(digest(token),)).fetchone()
            c.execute('DELETE FROM auth_sessions WHERE token_hash=?',(digest(token),))
            if session:
                self._event(c,session['account_id'],'signed_out')

    def request_email(self, email, role, slug, purpose, ip):
        if purpose not in ('verify','reset'):
            raise AuthError()
        self.rate('mail-ip',ip,10,3600)
        email = email_address(email)
        self.rate('mail-account',role+':'+slug+':'+email,3,3600)
        with self.db.transaction() as c:
            actor = self._lookup(c,email,role,slug)
            if self._usable(c,actor) and actor['role']=='customer' and actor['status']=='pending' and not actor['password_hash']:
                self._mail_token(c,actor,'invite')
            elif self._usable(c,actor) and (purpose!='verify' or not actor['email_verified']) and actor['password_hash']:
                self._mail_token(c,actor,purpose)

    def reset_password(self, token, password, *, invite=False):
        hashed = self.hasher.hash(password_value(password))
        with self.db.transaction() as c:
            row = self._token(c,token,'invite' if invite else 'reset')
            account = row['account_id']
            c.execute('UPDATE auth_credentials SET password_hash=?,password_changed_at=?,email_verified=1 WHERE account_id=?',(hashed,self.timestamp(),account))
            c.execute('UPDATE auth_tokens SET used_at=? WHERE account_id=? AND used_at IS NULL',(self.timestamp(),account))
            c.execute('DELETE FROM auth_sessions WHERE account_id=?',(account,))
            c.execute('DELETE FROM auth_steps WHERE account_id=?',(account,))
            if invite:
                c.execute("UPDATE accounts SET status='active' WHERE id=? AND role='customer'",(account,))
            self._event(c,account,'invitation_accepted' if invite else 'password_reset')

    def invite_customer(self, token, client_id):
        with self.db.transaction() as c:
            actor, _ = self._resolve(c,token,full=True)
            if actor['role']!='consultant':
                raise AuthError('Operazione non consentita.',403)
            client = c.execute('SELECT * FROM clients WHERE tenant_id=? AND id=?',(actor['tenant_id'],client_id)).fetchone()
            if not client:
                raise AuthError('Cliente non disponibile.',404)
            email = email_address(client['email'])
            existing = c.execute('SELECT id FROM accounts WHERE tenant_id=? AND client_id=?',(actor['tenant_id'],client_id)).fetchone()
            if existing:
                invited = self._credentials(c,existing['id'])
                if invited and invited['status']=='pending' and not invited['password_hash']:
                    self._mail_token(c,invited,'invite')
                    self._event(c,actor['id'],'customer_reinvited')
                return
            account = new_id()
            try:
                c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)',(account,actor['tenant_id'],client_id,'customer',email,'pending',now()))
            except sqlite3.IntegrityError as error:
                raise AuthError('Esiste già un accesso cliente con questa email nel tuo spazio.') from error
            c.execute('INSERT INTO auth_credentials(account_id,password_changed_at) VALUES(?,?)',(account,self.timestamp()))
            self._mail_token(c,self._credentials(c,account),'invite')
            self._event(c,actor['id'],'customer_invited')

    def master_consultants(self, token):
        with self.db.transaction() as c:
            actor, _ = self._resolve(c,token,full=True)
            if actor['role']!='master':
                raise AuthError('Operazione non consentita.',403)
            self._event(c,actor['id'],'consultants_reviewed')
            return [dict(row) for row in c.execute("SELECT p.*,a.status,a.id account_id,t.slug,t.status tenant_status,x.email_verified,(x.totp_encrypted IS NOT NULL) mfa_ready FROM consultant_profiles p JOIN tenants t ON t.id=p.tenant_id JOIN accounts a ON a.tenant_id=t.id AND a.role='consultant' JOIN auth_credentials x ON x.account_id=a.id ORDER BY a.created_at")]

    def activate(self, token, tenant_id):
        with self.db.transaction() as c:
            actor, _ = self._resolve(c,token,full=True)
            if actor['role']!='master':
                raise AuthError('Operazione non consentita.',403)
            target = c.execute("SELECT a.id FROM accounts a JOIN auth_credentials x ON x.account_id=a.id JOIN tenants t ON t.id=a.tenant_id WHERE a.tenant_id=? AND a.role='consultant' AND a.status='pending' AND t.status='draft' AND x.email_verified=1 AND x.totp_encrypted IS NOT NULL",(tenant_id,)).fetchone()
            if not target:
                raise AuthError('Prima servono email verificata e Authenticator configurato.')
            c.execute("UPDATE accounts SET status='active' WHERE id=?",(target['id'],))
            c.execute("UPDATE tenants SET status='active' WHERE id=?",(tenant_id,))
            c.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)',(new_id(),tenant_id,actor['id'],'activate','accounts',target['id'],'Attivazione del consulente dopo verifica del profilo',now()))
