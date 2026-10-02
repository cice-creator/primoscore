"""Master-only pages and read endpoints."""
from urllib.parse import unquote
from flask import jsonify,request,render_template,redirect
from .auth import AuthError
from .master import Master

def register_master(app,auth,session_token,local):
    master=Master(auth)
    @app.get('/master/')
    def master_page():
        try:
            if auth.identity(session_token(),full=True)['role']!='master':raise AuthError('Area Master richiesta.',403)
        except AuthError:return redirect('/accesso/master')
        return render_template('master.html',local=local)
    @app.get('/api/master/studios')
    def master_studios():return jsonify(master.studios(session_token()))
    @app.get('/api/master/studios/<tenant>')
    @app.get('/api/master/studios/<tenant>/lots/<lot>/leads')
    def master_detail(tenant,lot=None):
        return jsonify(master.detail(session_token(),tenant,unquote(request.headers.get('X-Master-Reason','')),lot))
