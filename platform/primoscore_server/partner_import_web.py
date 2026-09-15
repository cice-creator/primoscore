"""Scoped template download, file review and explicit selected-row confirmation."""
from pathlib import Path
from urllib.parse import unquote
from flask import jsonify,request,send_file
from .auth import AuthError
from .partner_import import PartnerImport,read_xlsx,HEADERS,KEYS


def register_partner_import(app,auth,session_token):
    service=PartnerImport(auth)
    def scope():return dict(tenant=request.headers.get('X-Primoscore-Tenant') or None,reason=unquote(request.headers.get('X-Master-Reason','')))

    @app.get('/api/workspace/import/template')
    def partner_template():
        service.authorize(session_token(),**scope())
        return send_file(Path(__file__).parent/'assets/Primoscore-Modello-Partner.xlsx',as_attachment=True,download_name='Primoscore-Modello-Partner.xlsx')

    @app.post('/api/workspace/import/preview')
    def partner_preview():
        # Authenticate and resolve studio scope before parsing an uploaded archive.
        with auth.db.transaction() as c:actor,_,_=service._scope(c,session_token(),**scope())
        auth.rate('partner-import',actor['id'],30,3600)
        if request.is_json:
            data=request.get_json(silent=True)
            if not isinstance(data,dict) or set(data)!={'rows'}:raise AuthError('Richiesta non valida.')
            rows=data['rows']
        else:
            f=request.files.get('file')
            if not f or not f.filename.lower().endswith('.xlsx'):raise AuthError('Scegli il modello compilato in formato Excel .xlsx.')
            try:rows=read_xlsx(f.read(5*1024*1024+1))
            except (ValueError,TypeError) as exc:raise AuthError(str(exc)) from exc
        return jsonify(**service.preview(session_token(),rows,**scope()),columns=[dict(key=k,label=h) for h,k in zip(HEADERS,KEYS)])

    @app.post('/api/workspace/import/commit')
    def partner_commit():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)!={'importId','selection'}:raise AuthError('Richiesta non valida.')
        return jsonify(service.commit(session_token(),data['importId'],data['selection'],**scope()))
