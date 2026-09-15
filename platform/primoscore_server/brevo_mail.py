"""Transactional delivery using the existing server-side Brevo credential."""
import html
import json
import os
from urllib.request import Request, urlopen
from .auth import email_address


class BrevoMailer:
    def __init__(self):
        self.key = os.environ['BREVO_API_KEY']
        self.sender = email_address(os.environ['PRIMOSCORE_MAIL_FROM'])

    def send(self, payload):
        labels = {'verify': 'Conferma la tua email', 'reset': 'Reimposta la password',
                  'invite': 'Crea il tuo accesso cliente'}
        title = labels[payload['purpose']]
        body = title + '.\n\n' + payload['url'] + '\n\nIl collegamento è personale e può essere utilizzato una sola volta. Se non hai richiesto questa operazione, ignora questa email.\n\nPrimoscore'
        data = {'sender': {'email': self.sender, 'name': 'Primoscore'},
                'to': [{'email': email_address(payload['to'])}],
                'subject': title + ' · Primoscore', 'textContent': body,
                'htmlContent': '<p>' + html.escape(body).replace('\n', '<br>') + '</p>'}
        req = Request('https://api.brevo.com/v3/smtp/email', data=json.dumps(data).encode(),
                      headers={'api-key': self.key, 'Content-Type': 'application/json'}, method='POST')
        with urlopen(req, timeout=15) as response:
            if response.status != 201:
                raise OSError('Invio email non confermato.')
