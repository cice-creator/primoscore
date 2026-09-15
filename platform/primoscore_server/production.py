"""Production entry point behind exactly one trusted reverse proxy.

Render terminates public HTTPS; self-hosting must expose only its reverse proxy.
No demo routes, account seeds, migration or email sending on import.
"""
from werkzeug.middleware.proxy_fix import ProxyFix
from .web import from_environment


def create_app():
    app=from_environment()
    app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=0,x_port=0,x_prefix=0)
    return app
