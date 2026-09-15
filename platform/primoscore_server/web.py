"""Same-origin authentication application. Includes scoped operational module APIs."""

import base64
import hmac
import io
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, render_template, send_from_directory
import qrcode
from qrcode.image.svg import SvgPathImage

from .auth import Auth, AuthError
from .database import Database


def create_app(database, encryption_key, origin, *, local=False, auth=None, cookie_prefix=None):
    parsed = urlsplit(origin)
    if local and parsed.hostname not in ('localhost','127.0.0.1'):
        raise ValueError('L’anteprima locale può ascoltare soltanto sul computer locale.')
    if not local and parsed.scheme != 'https':
        raise ValueError('HTTPS richiesto.')
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=16384, TRUSTED_HOSTS=[parsed.netloc], TESTING=False)
    auth = auth or Auth(database,encryption_key,origin)
    app.extensions['primoscore_auth'] = auth
    public = Path(__file__).resolve().parents[2] / 'dist'
    if cookie_prefix is not None and (not local or cookie_prefix != 'ps_demo_'):
        raise ValueError('Il prefisso dimostrativo è riservato all’anteprima locale.')
    prefix = cookie_prefix or ('ps_' if local else '__Host-ps_')
    app.config['PRIMOSCORE_COOKIE_PREFIX'] = prefix
    session_cookie, step_cookie, csrf_cookie = [prefix+x for x in ('session','step','csrf')]

    def set_cookie(response, name, value, max_age):
        response.set_cookie(name,value,max_age=max_age,secure=not local,httponly=True,samesite='Strict',path='/')

    @app.before_request
    def protect_request():
        if request.path=='/api/workspace/import/preview' and request.method=='POST':
            request.max_content_length=8*1024*1024
        if request.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('Origin') != auth.origin or request.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise AuthError('Origine della richiesta non valida.',403)
            supplied, expected = request.headers.get('X-CSRF-Token',''), request.cookies.get(csrf_cookie,'')
            if not supplied or not expected or not hmac.compare_digest(supplied,expected):
                raise AuthError('Sessione della pagina scaduta. Ricarica e riprova.',403)
            upload=request.path=='/api/workspace/import/preview' and request.method=='POST' and request.mimetype=='multipart/form-data'
            if not request.is_json and not upload:
                raise AuthError('Formato della richiesta non valido.',415)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if not local:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.errorhandler(AuthError)
    def auth_error(error):
        return jsonify(error=str(error)),error.status

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error='La richiesta è troppo grande. Il file Excel può contenere fino a 500 partner e pesare al massimo 5 MB.'),413

    def body(allowed):
        value = request.get_json(silent=True)
        if not isinstance(value,dict) or set(value)-set(allowed) or any(not isinstance(v,str) for v in value.values()):
            raise AuthError('Campi della richiesta non validi.')
        return value

    def session_token():
        return request.cookies.get(session_cookie,'')

    def clear_guest(response):
        app.extensions['primoscore_customer'].logout_guest(request.cookies.get(prefix+'guest',''))
        response.delete_cookie(prefix+'guest',path='/',secure=not local,httponly=True,samesite='Strict')

    def finish(result):
        result = dict(result)
        session = result.pop('session_token',None)
        step = result.pop('step_token',None)
        csrf_token = secrets.token_urlsafe(32)
        response = jsonify(**result,csrf=csrf_token)
        set_cookie(response,csrf_cookie,csrf_token,28800)
        if session or step:clear_guest(response)
        if session:
            set_cookie(response,session_cookie,session,28800)
            response.delete_cookie(step_cookie,path='/',secure=not local,httponly=True,samesite='Strict')
        if step:
            set_cookie(response,step_cookie,step,600)
            response.delete_cookie(session_cookie,path='/',secure=not local,httponly=True,samesite='Strict')
        return response

    @app.get('/healthz')
    def health():
        return jsonify(ok=True)

    @app.get('/')
    def home():
        return send_from_directory(public,'index.html')

    @app.get('/assets/<path:filename>')
    def assets(filename):
        return send_from_directory(public/'assets',filename)

    @app.get('/<filename>')
    def home_files(filename):
        if filename=='script.js':
            return send_from_directory(Path(__file__).parent/'static','home-access.js')
        if filename not in ('styles.css','animation.js'):
            return '',404
        return send_from_directory(public,filename)

    @app.get('/accesso/')
    @app.get('/accesso/<mode>')
    def access(mode='consulente'):
        if mode not in ('consulente','cliente','master','registrazione','conferma','nuova-password','invito','recupero','verifica','profilo'):
            return '',404
        return render_template('access.html',mode=mode,local=local)

    @app.get('/api/auth/csrf')
    def csrf_endpoint():
        token = secrets.token_urlsafe(32)
        response = jsonify(csrf=token)
        set_cookie(response,csrf_cookie,token,28800)
        return response

    @app.post('/api/auth/register')
    def register():
        data = body(('first_name','last_name','email','mobile','landline','office_address','office_postcode','office_city','office_province','oam_number','business_name','ivass_number','tax_code','vat_number','ivass_registered','password'))
        auth.register(data,request.remote_addr)
        return jsonify(ok=True,message='Se l’indirizzo può essere registrato, riceverai il collegamento di conferma. Se hai già un account, usa l’accesso o il recupero password.')

    @app.post('/api/auth/login')
    def login():
        data = body(('email','password','role','slug'))
        result = auth.login(data.get('email',''),data.get('password',''),data.get('role',''),data.get('slug',''),request.remote_addr)
        auth.logout(session_token())
        return finish(result)

    @app.get('/api/auth/enrollment')
    def enrollment():
        setup = auth.enrollment(request.cookies.get(step_cookie,''))
        out = io.BytesIO()
        qrcode.make(setup['uri'],image_factory=SvgPathImage).save(out)
        return jsonify(secret=setup['secret'],qr='data:image/svg+xml;base64,'+base64.b64encode(out.getvalue()).decode())

    @app.post('/api/auth/factor')
    def factor():
        data = body(('code',))
        return finish(auth.factor(request.cookies.get(step_cookie,''),data.get('code',''),request.remote_addr))

    @app.post('/api/auth/verify')
    def verify():
        data = body(('token',))
        auth.rate('token-ip',request.remote_addr,20)
        auth.verify_email(data.get('token',''))
        return jsonify(ok=True)

    @app.post('/api/auth/request-email')
    def request_email():
        data = body(('email','role','slug','purpose'))
        auth.request_email(data.get('email',''),data.get('role',''),data.get('slug',''),data.get('purpose','reset'),request.remote_addr)
        return jsonify(ok=True,message='Se i dati corrispondono a un account disponibile, riceverai un collegamento via email.')

    @app.post('/api/auth/reset-password')
    def reset_password():
        data = body(('token','password','purpose'))
        if data.get('purpose') not in ('reset','invite'):
            raise AuthError()
        auth.rate('reset-ip',request.remote_addr,10)
        auth.reset_password(data.get('token',''),data.get('password',''),invite=data.get('purpose')=='invite')
        return jsonify(ok=True)

    @app.get('/api/auth/session')
    def session():
        return jsonify(user=auth.identity(session_token()))

    @app.post('/api/auth/logout')
    def logout():
        body(())
        auth.logout(session_token())
        response = jsonify(ok=True)
        clear_guest(response)
        for cookie in (session_cookie,step_cookie,csrf_cookie):
            response.delete_cookie(cookie,path='/',secure=not local,httponly=True,samesite='Strict')
        return response

    @app.get('/api/master/consultants')
    def consultants():
        return jsonify(consultants=auth.master_consultants(session_token()))

    @app.post('/api/master/activate')
    def activate():
        data = body(('tenant_id',))
        auth.activate(session_token(),data.get('tenant_id',''))
        return jsonify(ok=True)

    @app.post('/api/consultant/invite')
    def invite():
        data = body(('client_id',))
        user = auth.identity(session_token(),full=True)
        auth.rate('invite-account',user['id'],10,3600)
        auth.invite_customer(session_token(),data.get('client_id',''))
        return jsonify(ok=True)

    from .workspace_web import register_workspace
    register_workspace(app,auth,session_token,local)
    from .partner_import_web import register_partner_import
    register_partner_import(app,auth,session_token)
    from .customer_web import register_customer
    register_customer(app,auth,session_token,local)
    return app


def from_environment():
    required = ('PRIMOSCORE_DATABASE','PRIMOSCORE_AUTH_KEY','PRIMOSCORE_ORIGIN','PRIMOSCORE_MAIL_FROM')
    transport = os.environ.get('PRIMOSCORE_MAIL_TRANSPORT', 'smtp')
    if transport == 'brevo':
        required += ('BREVO_API_KEY',)
    elif transport == 'smtp':
        required += ('PRIMOSCORE_SMTP_HOST','PRIMOSCORE_SMTP_USER','PRIMOSCORE_SMTP_PASSWORD')
    else:
        raise RuntimeError('Trasporto email non riconosciuto.')
    if any(not os.environ.get(key) for key in required):
        raise RuntimeError('Configura database, chiave, origine HTTPS e mittente prima di avviare il servizio pubblicato.')
    return create_app(Database(os.environ['PRIMOSCORE_DATABASE']),os.environ['PRIMOSCORE_AUTH_KEY'],os.environ['PRIMOSCORE_ORIGIN'])
