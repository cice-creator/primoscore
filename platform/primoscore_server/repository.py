"""Tenant-scoped storage services for a future authenticated server.

actor_id must come from verified server authentication, NEVER an HTTP parameter.
This phase has no HTTP adapter, credential issuer or public provisioning API.
"""

from dataclasses import dataclass
import json
import sqlite3
import uuid

from .database import now


class AccessDenied(Exception):
    pass


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class Resource:
    columns: tuple
    customer_key: str = ''
    immutable: bool = False
    create: bool = True


RESOURCES = {
    'cities': Resource(('name',)),
    'partners': Resource(('city_id', 'name', 'category', 'status', 'details_json')),
    'voucher_lots': Resource(('city_id', 'printed', 'status')),
    'campaigns': Resource(('lot_id', 'partner_id', 'name', 'kind')),
    'deliveries': Resource(('lot_id', 'partner_id', 'quantity', 'delivered_on', 'recipient', 'notes')),
    'clients': Resource(('partner_id', 'lot_id', 'first_name', 'last_name', 'email', 'mobile', 'residence_city', 'stage'), 'id'),
    'questionnaires': Resource(('client_id', 'answers_json', 'current_step'), 'client_id'),
    'assessments': Resource((), 'client_id', immutable=True, create=False),
    'activities': Resource(('partner_id', 'kind', 'occurred_on', 'details_json'), immutable=True),
    'appointments': Resource(('client_id', 'starts_at', 'ends_at', 'status'), 'client_id'),
    'outbox': Resource(('client_id', 'kind', 'idempotency_key', 'payload_json', 'status')),
}


def new_id():
    return str(uuid.uuid4())


def encode(value):
    if not isinstance(value, dict):
        raise ValueError('È richiesto un oggetto.')
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(encoded.encode()) > 100000:
        raise ValueError('Dati troppo grandi.')
    return encoded


def view(row):
    data = dict(row)
    for key, value in list(data.items()):
        if key.endswith('_json'):
            data[key[:-5]] = json.loads(value)
            del data[key]
    return data


class Repository:
    def __init__(self, database, actor_id, *, tenant_id=None, reason=''):
        self.database = database
        self.actor_id = actor_id
        self.selected_tenant = tenant_id
        self.reason = reason.strip()

    def _identity(self, connection):
        actor = connection.execute('SELECT * FROM accounts WHERE id=? AND status=\'active\'', (self.actor_id,)).fetchone()
        if not actor:
            raise AccessDenied('Accesso non autorizzato.')
        return actor

    def _authorize(self, connection, resource, write=False, customer_write=False):
        if resource not in RESOURCES:
            raise ValueError('Risorsa non disponibile.')
        actor = self._identity(connection)
        if actor['role'] == 'master':
            if not self.selected_tenant or not self.reason or len(self.reason) > 500:
                raise AccessDenied('Seleziona uno spazio e indica il motivo dell’accesso Master.')
            tenant = connection.execute('SELECT id FROM tenants WHERE id=?', (self.selected_tenant,)).fetchone()
            if not tenant:
                raise NotFound('Spazio non disponibile.')
            tenant_id = tenant['id']
        else:
            tenant_id = actor['tenant_id']
            if self.selected_tenant is not None and self.selected_tenant != tenant_id:
                raise AccessDenied('Accesso non autorizzato.')
            if not connection.execute("SELECT 1 FROM tenants WHERE id=? AND status='active'", (tenant_id,)).fetchone():
                raise AccessDenied('Spazio non attivo.')
        where, parameters = 'tenant_id=?', [tenant_id]
        if actor['role'] == 'customer':
            key = RESOURCES[resource].customer_key
            if not key or (write and not customer_write):
                raise AccessDenied('Operazione non autorizzata.')
            where += ' AND ' + key + '=?'
            parameters.append(actor['client_id'])
        return actor, tenant_id, where, parameters

    def _audit(self, connection, actor, tenant_id, action, resource, record_id=None):
        connection.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)',
                           (new_id(), tenant_id, actor['id'], action, resource, record_id, self.reason if actor['role'] == 'master' else '', now()))

    def list(self, resource):
        with self.database.transaction() as connection:
            actor, tenant, where, parameters = self._authorize(connection, resource)
            rows = connection.execute('SELECT * FROM ' + resource + ' WHERE ' + where + ' ORDER BY created_at,id', parameters).fetchall()
            if actor['role'] == 'master':
                self._audit(connection, actor, tenant, 'read_list', resource)
            return [view(row) for row in rows]

    def get(self, resource, record_id):
        with self.database.transaction() as connection:
            actor, tenant, where, parameters = self._authorize(connection, resource)
            row = connection.execute('SELECT * FROM ' + resource + ' WHERE ' + where + ' AND id=?', [*parameters, record_id]).fetchone()
            if not row:
                raise NotFound('Record non disponibile.')
            if actor['role'] == 'master':
                self._audit(connection, actor, tenant, 'read', resource, record_id)
            return view(row)

    @staticmethod
    def _values(resource, values):
        if not isinstance(values, dict) or not values or set(values) - set(RESOURCES[resource].columns):
            raise ValueError('Campi non consentiti o mancanti.')
        cleaned = dict(values)
        for key, value in cleaned.items():
            if key.endswith('_json'):
                cleaned[key] = encode(value)
            elif key in ('quantity', 'printed', 'current_step', 'starts_at', 'ends_at'):
                if type(value) is not int:
                    raise ValueError('È richiesto un numero intero.')
            elif value is not None and (not isinstance(value, str) or len(value) > 10000):
                raise ValueError('Campo non valido.')
        return cleaned

    def create(self, resource, values):
        try:
            with self.database.transaction() as connection:
                actor, tenant, _, _ = self._authorize(connection, resource, write=True)
                if not RESOURCES[resource].create:
                    raise AccessDenied('Usa il servizio dedicato.')
                values = self._values(resource, values)
                ident = new_id()
                if resource == 'voucher_lots':
                    values['code'] = 'PS-' + uuid.uuid4().hex.upper()
                values = {'tenant_id': tenant, 'id': ident, **values, 'created_at': now()}
                connection.execute('INSERT INTO ' + resource + '(' + ','.join(values) + ') VALUES(' + ','.join('?' for _ in values) + ')', list(values.values()))
                self._audit(connection, actor, tenant, 'create', resource, ident)
                return view(connection.execute('SELECT * FROM ' + resource + ' WHERE tenant_id=? AND id=?', (tenant, ident)).fetchone())
        except sqlite3.IntegrityError as error:
            raise Conflict('Dati o collegamenti non validi, disponibilità esaurita o registrazione già presente.') from error

    def update(self, resource, record_id, values, *, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Revisione non valida.')
        try:
            with self.database.transaction() as connection:
                actor, tenant, where, parameters = self._authorize(connection, resource, write=True)
                if RESOURCES[resource].immutable or resource == 'questionnaires':
                    raise AccessDenied('Registrazione immutabile o servizio dedicato richiesto.')
                values = self._values(resource, values)
                if set(values) & {'client_id', 'partner_id', 'lot_id'}:
                    raise ValueError('Le associazioni storiche non possono essere riassegnate.')
                old = connection.execute('SELECT revision FROM ' + resource + ' WHERE ' + where + ' AND id=?', [*parameters, record_id]).fetchone()
                if not old:
                    raise NotFound('Record non disponibile.')
                if old['revision'] != expected_revision:
                    raise Conflict('Il record è stato aggiornato. Ricarica i dati.')
                connection.execute('UPDATE ' + resource + ' SET ' + ','.join(key + '=?' for key in values) + ',revision=revision+1 WHERE ' + where + ' AND id=?', [*values.values(), *parameters, record_id])
                self._audit(connection, actor, tenant, 'update', resource, record_id)
                return view(connection.execute('SELECT * FROM ' + resource + ' WHERE ' + where + ' AND id=?', [*parameters, record_id]).fetchone())
        except sqlite3.IntegrityError as error:
            raise Conflict('Operazione non compatibile con i dati o la disponibilità.') from error

    def remove_delivery(self, record_id, *, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Revisione non valida.')
        with self.database.transaction() as connection:
            actor, tenant, where, parameters = self._authorize(connection, 'deliveries', write=True)
            row = connection.execute('SELECT revision FROM deliveries WHERE ' + where + ' AND id=?', [*parameters, record_id]).fetchone()
            if not row:
                raise NotFound('Record non disponibile.')
            if row['revision'] != expected_revision:
                raise Conflict('Il record è stato aggiornato.')
            connection.execute('DELETE FROM deliveries WHERE ' + where + ' AND id=?', [*parameters, record_id])
            self._audit(connection, actor, tenant, 'delete', 'deliveries', record_id)

    def save_answers(self, questionnaire_id, answers, current_step, *, expected_revision):
        if type(current_step) is not int or not 0 <= current_step <= 6 or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Avanzamento o revisione non validi.')
        encoded = encode(answers)
        with self.database.transaction() as connection:
            actor, tenant, where, parameters = self._authorize(connection, 'questionnaires', write=True, customer_write=True)
            row = connection.execute('SELECT * FROM questionnaires WHERE ' + where + ' AND id=?', [*parameters, questionnaire_id]).fetchone()
            if not row:
                raise NotFound('Record non disponibile.')
            if row['revision'] != expected_revision:
                raise Conflict('Le risposte sono cambiate. Ricarica i dati.')
            connection.execute('UPDATE questionnaires SET answers_json=?,current_step=?,revision=revision+1 WHERE ' + where + ' AND id=?', [encoded, current_step, *parameters, questionnaire_id])
            self._audit(connection, actor, tenant, 'save_answers', 'questionnaires', questionnaire_id)
            return view(connection.execute('SELECT * FROM questionnaires WHERE ' + where + ' AND id=?', [*parameters, questionnaire_id]).fetchone())

    def save_assessment(self, questionnaire_id, result, *, expected_revision):
        """Called only by the trusted scoring service after validation/calculation.

        Pins the exact answer snapshot; browser-supplied results must never reach here.
        """
        encoded = encode(result)
        version = result.get('engineVersion')
        if not isinstance(version, str) or not version.strip() or len(version) > 200 or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Versione del motore o revisione mancanti.')
        with self.database.transaction() as connection:
            actor, tenant, where, parameters = self._authorize(connection, 'questionnaires', write=True)
            row = connection.execute('SELECT * FROM questionnaires WHERE ' + where + ' AND id=?', [*parameters, questionnaire_id]).fetchone()
            if not row:
                raise NotFound('Record non disponibile.')
            if row['revision'] != expected_revision:
                raise Conflict('Le risposte sono cambiate durante il calcolo.')
            existing = connection.execute('SELECT * FROM assessments WHERE tenant_id=? AND questionnaire_id=? AND answers_revision=? AND engine_version=?', (tenant, questionnaire_id, expected_revision, version)).fetchone()
            if existing:
                if existing['result_json'] != encoded:
                    raise Conflict('Esiste già un risultato diverso per questa versione e queste risposte.')
                if actor['role'] == 'master':
                    self._audit(connection, actor, tenant, 'read', 'assessments', existing['id'])
                return view(existing)
            ident = new_id()
            connection.execute('INSERT INTO assessments(tenant_id,id,client_id,questionnaire_id,answers_revision,engine_version,answers_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (tenant, ident, row['client_id'], questionnaire_id, expected_revision, version, row['answers_json'], encoded, now()))
            self._audit(connection, actor, tenant, 'create', 'assessments', ident)
            return view(connection.execute('SELECT * FROM assessments WHERE tenant_id=? AND id=?', (tenant, ident)).fetchone())

    def list_tenants(self):
        with self.database.transaction() as connection:
            actor = self._identity(connection)
            if actor['role'] != 'master' or not self.reason or len(self.reason) > 500:
                raise AccessDenied('Operazione Master con motivazione richiesta.')
            rows = connection.execute('SELECT * FROM tenants ORDER BY created_at,id').fetchall()
            self._audit(connection, actor, None, 'read_list', 'tenants')
            return [dict(row) for row in rows]

    def audit_log(self):
        with self.database.transaction() as connection:
            actor, tenant, _, _ = self._authorize(connection, 'clients')
            if actor['role'] != 'master':
                raise AccessDenied('Operazione Master richiesta.')
            self._audit(connection, actor, tenant, 'read_list', 'audit_events')
            return [dict(row) for row in connection.execute('SELECT * FROM audit_events WHERE tenant_id=? ORDER BY created_at,id', (tenant,))]
