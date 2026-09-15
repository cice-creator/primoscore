from urllib.parse import unquote
from flask import request,jsonify,render_template,redirect
from .auth import AuthError
from .customer import Customer


def register_customer(app,auth,session_token,local):
    service=Customer(auth,local=local);app.extensions['primoscore_customer']=service
    prefix=app.config['PRIMOSCORE_COOKIE_PREFIX']
    guest_cookie=prefix+'guest'
    def credentials():return dict(session=session_token(),guest=request.cookies.get(guest_cookie,''))
    def scope():return dict(tenant=request.headers.get('X-Primoscore-Tenant') or None,reason=unquote(request.headers.get('X-Master-Reason','')))

    @app.get('/cliente/')
    @app.get('/cliente/<section>')
    def customer_page(section='area'):
        if section not in ('area','questionario','risultato','appuntamento','informativa-prova'):return '',404
        return render_template('customer.html',section=section,local=local,code='')

    @app.get('/api/customer/context/<code>')
    def customer_context(code):return jsonify(service.context(code))

    @app.post('/api/customer/intake')
    def acquire():
        result=service.acquire(request.get_json(silent=True),request.cookies.get(guest_cookie,''),request.remote_addr)
        token=result.pop('guest_token',None);response=jsonify(result)
        if token:
            auth.logout(session_token())
            service.logout_guest(request.cookies.get(guest_cookie,''))
            response.delete_cookie(prefix+'session',path='/',secure=not local,httponly=True,samesite='Strict')
            response.set_cookie(guest_cookie,token,max_age=86400,httponly=True,secure=not local,samesite='Strict',path='/')
        return response

    @app.get('/api/customer')
    def customer():return jsonify(service.get(**credentials()))

    @app.post('/api/customer/save')
    def save():return jsonify(service.save(request.get_json(silent=True),**credentials()))

    @app.post('/api/customer/complete')
    def complete():return jsonify(service.complete(request.get_json(silent=True),**credentials()))

    @app.get('/api/customer/slots')
    def slots():return jsonify(slots=service.slots(**credentials()))

    @app.post('/api/customer/book')
    def book():return jsonify(service.book(request.get_json(silent=True),**credentials()))

    @app.get('/api/workspace/customers')
    def customers():return jsonify(service.advisor(session_token(),**scope()))

    @app.get('/api/workspace/customers/<ident>')
    def customer_detail(ident):return jsonify(service.advisor(session_token(),client_id=ident,**scope()))

    @app.post('/api/workspace/customer-settings')
    def customer_settings():return jsonify(service.configure(session_token(),request.get_json(silent=True),**scope()))
