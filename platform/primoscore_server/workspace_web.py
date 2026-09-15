"""Authenticated workspace HTTP adapter; the service checks each operation again."""
import io
from urllib.parse import unquote
from flask import jsonify,request,render_template,redirect,Response,send_file
import qrcode
from qrcode.image.svg import SvgPathImage
from .auth import AuthError
from .workspace import Workspace
from .voucher_pdf import card


def register_workspace(app,auth,session_token,local):
    workspace=Workspace(auth)
    def scope():return dict(tenant=request.headers.get('X-Primoscore-Tenant') or None,reason=unquote(request.headers.get('X-Master-Reason','')))

    @app.get('/consulente/')
    @app.get('/consulente/<section>')
    def consultant_workspace(section='panoramica'):
        if section not in ('panoramica','sviluppo','voucher','agenda','clienti'):return '',404
        try:
            user=auth.identity(session_token(),full=True)
            if user['role']=='customer':raise AuthError('Area riservata ai consulenti.',403)
        except AuthError:return redirect('/accesso/consulente')
        return render_template('workspace.html',section=section,local=local)

    @app.get('/api/workspace')
    def snapshot():return jsonify(workspace.snapshot(session_token(),**scope()))

    @app.post('/api/workspace/commands')
    def command():
        return jsonify(workspace.command(session_token(),request.get_json(silent=True),**scope()))

    @app.get('/api/workspace/export.<fmt>')
    def export(fmt):
        if fmt not in ('csv','json'):return '',404
        result=workspace.export(session_token(),fmt,**scope(),filters=request.args.to_dict())
        return Response(result,content_type=('text/csv' if fmt=='csv' else 'application/json')+'; charset=utf-8',headers={'Content-Disposition':'attachment; filename="primoscore-sviluppo.'+fmt+'"'})

    @app.get('/api/workspace/lots/<ident>/<kind>')
    def voucher_export(ident,kind):
        if kind not in ('qr.svg','voucher.pdf'):return '',404
        lot=workspace.voucher(session_token(),ident,**scope())
        if kind=='voucher.pdf':
            result=card(lot['label'],lot['url'],local=local)
            return send_file(io.BytesIO(result),mimetype='application/pdf',download_name='primoscore-voucher.pdf',as_attachment=True)
        output=io.BytesIO();qrcode.make(lot['url'],image_factory=SvgPathImage).save(output)
        return Response(output.getvalue(),mimetype='image/svg+xml',headers={'Content-Disposition':'attachment; filename="primoscore-qr.svg"'})

    @app.get('/v/<code>')
    def public_voucher(code):
        try:profile=workspace.public_voucher(code)
        except AuthError:return render_template('voucher.html',profile=None,local=local),404
        return render_template('customer.html',section='ingresso',code=code,local=local)
