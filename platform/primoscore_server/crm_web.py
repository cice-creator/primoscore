from urllib.parse import unquote
from flask import request,jsonify,render_template,redirect
from .crm import CRM
from .auth import AuthError

def register_crm(app,auth,session_token,local):
    service=CRM(auth)
    def scope():return dict(tenant=request.headers.get('X-Primoscore-Tenant') or None,reason=unquote(request.headers.get('X-Master-Reason','')))
    @app.get('/consulente/clienti')
    @app.get('/consulente/crm')
    def crm_page():
        try:
            if auth.identity(session_token(),full=True)['role'] not in ('consultant','master'):raise AuthError('Area studio richiesta.',403)
        except AuthError:return redirect('/accesso/consulente')
        if request.path=='/consulente/crm':return redirect('/consulente/clienti'+('?' + request.query_string.decode() if request.query_string else ''))
        return render_template('crm.html',local=local)
    @app.get('/api/workspace/crm')
    @app.get('/api/workspace/crm/<ident>')
    def crm_read(ident=None):return jsonify(service.snapshot(session_token(),ident=ident,**scope()))
    @app.post('/api/workspace/crm/<ident>')
    def crm_command(ident):return jsonify(service.command(session_token(),ident,request.get_json(silent=True),**scope()))

    @app.route('/api/workspace/crm-rules',methods=['GET','POST'])
    def crm_rules():
        data=request.get_json(silent=True) if request.method=='POST' else None
        if request.method=='POST' and not isinstance(data,dict):raise AuthError('Dati non validi.')
        return jsonify(service.rules(session_token(),data,**scope()))
