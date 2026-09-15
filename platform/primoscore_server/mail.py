"""Explicit transactional email worker. Never runs on import or preview startup."""

from email.message import EmailMessage
import json
import os
import smtplib
import ssl
from urllib.parse import urlsplit, parse_qs

from .auth import AuthError, email_address


class SMTPMailer:
    def __init__(self):
        self.host = os.environ['PRIMOSCORE_SMTP_HOST']
        self.port = int(os.environ.get('PRIMOSCORE_SMTP_PORT','587'))
        self.user = os.environ['PRIMOSCORE_SMTP_USER']
        self.password = os.environ['PRIMOSCORE_SMTP_PASSWORD']
        self.sender = email_address(os.environ['PRIMOSCORE_MAIL_FROM'])
        if self.port not in (465,587):
            raise ValueError('Usa SMTP con TLS sulla porta 465 o 587.')

    def send(self, payload):
        labels = {'verify':'Conferma la tua email','reset':'Reimposta la password','invite':'Crea il tuo accesso cliente'}
        message = EmailMessage()
        message['From'], message['To'] = self.sender,email_address(payload['to'])
        message['Subject'] = labels[payload['purpose']]+' · Primoscore'
        message.set_content(labels[payload['purpose']]+'.\n\n'+payload['url']+'\n\nIl collegamento è personale e può essere utilizzato una sola volta. Se non hai richiesto questa operazione, ignora questa email.\n\nPrimoscore')
        context = ssl.create_default_context()
        transport = smtplib.SMTP_SSL if self.port==465 else smtplib.SMTP
        kwargs = {'context':context} if self.port==465 else {}
        with transport(self.host,self.port,timeout=15,**kwargs) as client:
            if self.port!=465:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(self.user,self.password)
            client.send_message(message)


def send_pending(auth, mailer, limit=20):
    """One bounded batch; partial SMTP failure can require retry of the same link."""
    with auth.db.transaction() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM auth_mail WHERE status='queued' AND attempts<3 ORDER BY created_at LIMIT ?",(limit,))]
    sent = failed = cancelled = 0
    for ident in ids:
        with auth.db.transaction() as c:
            row = c.execute("SELECT * FROM auth_mail WHERE id=? AND status='queued'",(ident,)).fetchone()
            if not row:
                continue
            payload = json.loads(auth.cipher.decrypt(row['payload_encrypted'].encode()))
            token = parse_qs(urlsplit(payload['url']).fragment).get('token',[''])[0]
            try:
                auth._token(c,token,payload['purpose'])
            except AuthError:
                c.execute("UPDATE auth_mail SET status='cancelled' WHERE id=?",(ident,))
                cancelled+=1
                continue
            c.execute("UPDATE auth_mail SET status='sending',attempts=attempts+1 WHERE id=?",(ident,))
        try:
            mailer.send(payload)
        except (OSError,smtplib.SMTPException):
            with auth.db.transaction() as c:
                c.execute("UPDATE auth_mail SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END WHERE id=?",(ident,))
            failed+=1
        else:
            with auth.db.transaction() as c:
                c.execute("UPDATE auth_mail SET status='sent' WHERE id=?",(ident,))
            sent+=1
    return {'sent':sent,'failed':failed,'cancelled':cancelled}
