"""Transactional delivery using the existing server-side Brevo credential."""
import html
import json
import os
from urllib.request import Request, urlopen
from .notifications import LABELS
from .auth import email_address


class BrevoMailer:
    def __init__(self):
        self.key = os.environ['BREVO_API_KEY']
        self.sender = email_address(os.environ['PRIMOSCORE_MAIL_FROM'])

    def send(self, payload):
        labels = {'verify': 'Conferma la tua email', 'reset': 'Reimposta la password',
                  'invite': 'Crea il tuo accesso cliente',**LABELS}
        title = labels[payload['purpose']]
        body = title + '.\n\n' + payload['url'] + '\n\nIl collegamento è personale e può essere utilizzato una sola volta. Se non hai richiesto questa operazione, ignora questa email.\n\nPrimoscore'
        if payload['purpose'] in LABELS:
            body=title+'.\n\nApri la tua area riservata per i dettagli:\n'+payload['url']+'\n\nPrimoscore'
        if payload['purpose']=='report':
            body='Il tuo report Primoscore è pronto.\n\nLeggi il tuo report e conferma il tuo indirizzo email:\n'+payload['url']+'\n\nIl collegamento è personale e valido per 7 giorni. Non condividerlo.\n\nPrimoscore'
        data = {'sender': {'email': self.sender, 'name': 'Primoscore'},
                'to': [{'email': email_address(payload['to'])}],
                'subject': title + ' · Primoscore', 'textContent': body,
                'htmlContent': '<p>' + html.escape(body).replace('\n', '<br>') + '</p>'}
        if payload['purpose']=='report':
            data['htmlContent']='<h2>Il tuo report Primoscore è pronto</h2><p>Apri il report per confermare il tuo indirizzo email e leggere il risultato.</p><p><a href="'+html.escape(payload['url'],quote=True)+'">Leggi il tuo report</a></p><p>Il collegamento è personale e valido per 7 giorni. Non condividerlo.</p><p>Primoscore</p>'
        req = Request('https://api.brevo.com/v3/smtp/email', data=json.dumps(data).encode(),
                      headers={'api-key': self.key, 'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'Primoscore/1.0'}, method='POST')
        with urlopen(req, timeout=15) as response:
            if response.status != 201:
                raise OSError('Invio email non confermato.')
