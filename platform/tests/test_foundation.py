from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from primoscore_server import AccessDenied, Conflict, Database, NotFound, Repository
from primoscore_server.database import now
from primoscore_server.repository import RESOURCES


class EmptyDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='primoscore-empty-')
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'empty.sqlite3')

    def test_initialization_is_empty_private_and_repeatable(self):
        self.db.initialize()
        self.db.initialize()
        with self.db.transaction() as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                with self.subTest(table=table):
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], len(list((Path(__file__).parents[1] / 'primoscore_server/migrations').glob('*.sql'))) if table == 'schema_migrations' else 0)
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.db.path.stat().st_mode & 0o777, 0o600)

    def test_missing_database_is_not_implicitly_created(self):
        with self.assertRaises(sqlite3.OperationalError):
            with self.db.transaction():
                pass
        self.assertFalse(self.db.path.exists())

    def test_public_directory_is_rejected(self):
        with self.assertRaises(ValueError):
            Database(Path(self.temp.name) / 'dist' / 'data.sqlite3')

    def test_existing_unrelated_archive_is_preserved(self):
        with sqlite3.connect(self.db.path) as c:
            c.execute('CREATE TABLE unrelated(value TEXT)')
            c.execute("INSERT INTO unrelated VALUES('synthetic sentinel')")
        with self.assertRaises(ValueError):
            self.db.initialize()
        with sqlite3.connect(self.db.path) as c:
            self.assertEqual(c.execute('SELECT * FROM unrelated').fetchone()[0], 'synthetic sentinel')
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='tenants'").fetchone())

    def test_failed_migration_rolls_back_every_table(self):
        directory = Path(self.temp.name) / 'migrations'
        directory.mkdir()
        (directory / '001_bad.sql').write_text('CREATE TABLE temporary_table(id TEXT);\nINVALID SQL;\n')
        with patch('primoscore_server.database.MIGRATIONS', directory):
            with self.assertRaises(sqlite3.OperationalError):
                self.db.initialize()
        with sqlite3.connect(self.db.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)
        self.db.initialize()

    def test_changed_or_unknown_migration_is_rejected(self):
        self.db.initialize()
        with self.db.transaction() as c:
            c.execute("UPDATE schema_migrations SET sha256='changed'")
        with self.assertRaises(ValueError):
            self.db.initialize()


class ScopedStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='primoscore-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'test.sqlite3')
        self.db.initialize()
        # Trusted test provisioning only: no production accounts, passwords or emails.
        with self.db.transaction() as c:
            for tenant in ('alpha', 'beta'):
                c.execute('INSERT INTO tenants VALUES(?,?,?,?)', (tenant, tenant, 'active', now()))
                c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)', ('advisor-' + tenant, tenant, None, 'consultant', tenant + '@example.invalid', 'active', now()))
            c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)', ('master', None, None, 'master', 'master@example.invalid', 'active', now()))
        self.a = Repository(self.db, 'advisor-alpha')
        self.b = Repository(self.db, 'advisor-beta')
        self.records = {}
        for tenant, repository in [('alpha', self.a), ('beta', self.b)]:
            city = repository.create('cities', {'name': 'Città sintetica'})
            partner = repository.create('partners', {'city_id': city['id'], 'name': 'Partner sintetico'})
            lot = repository.create('voucher_lots', {'city_id': city['id'], 'printed': 20})
            campaign = repository.create('campaigns', {'lot_id': lot['id'], 'partner_id': partner['id'], 'name': 'Campagna sintetica', 'kind': 'website'})
            client = repository.create('clients', {'partner_id': partner['id'], 'lot_id': lot['id'], 'first_name': 'Cliente sintetico', 'email': 'same@example.invalid'})
            questionnaire = repository.create('questionnaires', {'client_id': client['id']})
            assessment = repository.save_assessment(questionnaire['id'], {'engineVersion': 'synthetic-1', 'totalScore': 70}, expected_revision=0)
            activity = repository.create('activities', {'partner_id': partner['id'], 'kind': 'visit', 'occurred_on': '2026-09-13'})
            appointment = repository.create('appointments', {'client_id': client['id'], 'starts_at': 10000, 'ends_at': 13600})
            outbox = repository.create('outbox', {'client_id': client['id'], 'kind': 'reminder', 'idempotency_key': 'same-key'})
            delivery = repository.create('deliveries', {'lot_id': lot['id'], 'partner_id': partner['id'], 'quantity': 5, 'delivered_on': '2026-09-13'})
            self.records[tenant] = dict(cities=city, partners=partner, voucher_lots=lot, campaigns=campaign, clients=client, questionnaires=questionnaire, assessments=assessment, activities=activity, appointments=appointment, outbox=outbox, deliveries=delivery)
            with self.db.transaction() as c:
                c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)', ('customer-' + tenant, tenant, client['id'], 'customer', 'same@example.invalid', 'active', now()))
        self.customer = Repository(self.db, 'customer-alpha')

    def test_all_lists_and_direct_reads_are_tenant_scoped(self):
        for resource in RESOURCES:
            with self.subTest(resource=resource):
                self.assertEqual([r['id'] for r in self.a.list(resource)], [self.records['alpha'][resource]['id']])
                self.assertEqual(self.a.get(resource, self.records['alpha'][resource]['id'])['tenant_id'], 'alpha')
                with self.assertRaises(NotFound):
                    self.a.get(resource, self.records['beta'][resource]['id'])

    def test_unknown_actor_and_tenant_override_are_denied(self):
        for repository in (Repository(self.db, 'unknown'), Repository(self.db, 'advisor-alpha', tenant_id='beta'), Repository(self.db, 'customer-alpha', tenant_id='beta')):
            with self.assertRaises(AccessDenied):
                repository.list('clients')

    def test_browser_role_tenant_and_id_fields_cannot_be_injected(self):
        for key in ('role', 'tenant_id', 'id', 'created_at', 'revision'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.a.create('cities', {'name': 'Invalid injection', key: 'beta'})
        with self.assertRaises(ValueError):
            self.a.list('accounts; DROP TABLE clients')

    def test_foreign_links_cannot_cross_tenants(self):
        other = self.records['beta']
        invalid = {
            'partners': {'name': 'Foreign', 'city_id': other['cities']['id']},
            'voucher_lots': {'city_id': other['cities']['id'], 'printed': 1},
            'campaigns': {'lot_id': other['voucher_lots']['id'], 'partner_id': other['partners']['id'], 'name': 'Foreign', 'kind': 'other'},
            'deliveries': {'lot_id': other['voucher_lots']['id'], 'partner_id': other['partners']['id'], 'quantity': 1, 'delivered_on': '2026-09-13'},
            'clients': {'first_name': 'Foreign', 'partner_id': other['partners']['id']},
            'questionnaires': {'client_id': other['clients']['id']},
            'activities': {'partner_id': other['partners']['id'], 'kind': 'visit', 'occurred_on': '2026-09-13'},
            'appointments': {'client_id': other['clients']['id'], 'starts_at': 20000, 'ends_at': 23600},
            'outbox': {'client_id': other['clients']['id'], 'kind': 'test', 'idempotency_key': 'foreign'},
        }
        for resource, values in invalid.items():
            with self.subTest(resource=resource), self.assertRaises(Conflict):
                self.a.create(resource, values)

    def test_database_rejects_cross_tenant_customer_binding(self):
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as c:
            c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)', ('bad', 'alpha', self.records['beta']['clients']['id'], 'customer', 'bad@example.invalid', 'active', now()))

    def test_assessment_cannot_use_another_clients_questionnaire(self):
        peer = self.a.create('clients', {'first_name': 'Synthetic peer'})
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as c:
            c.execute('INSERT INTO assessments(tenant_id,id,client_id,questionnaire_id,answers_revision,engine_version,answers_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      ('alpha', 'bad-result', peer['id'], self.records['alpha']['questionnaires']['id'], 0, 'synthetic-bad', '{}', '{}', now()))

    def test_account_role_shape_and_email_binding_are_enforced(self):
        for role, tenant, client in [('partner', 'alpha', None), ('master', 'alpha', None), ('consultant', None, None), ('customer', 'alpha', None)]:
            with self.subTest(role=role), self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as c:
                c.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)', ('invalid-account', tenant, client, role, 'invalid@example.invalid', 'active', now()))
        # Two identical customer emails in different tenants already exist in setUp;
        # they resolve to independent records, never a shared identity.
        self.assertNotEqual(self.customer.list('clients')[0]['id'], Repository(self.db, 'customer-beta').list('clients')[0]['id'])

    def test_customer_sees_only_own_records_even_within_same_tenant(self):
        peer = self.a.create('clients', {'first_name': 'Another synthetic client'})
        peer_questionnaire = self.a.create('questionnaires', {'client_id': peer['id']})
        for resource in ('clients', 'questionnaires', 'assessments', 'appointments'):
            with self.subTest(resource=resource):
                self.assertEqual(len(self.customer.list(resource)), 1)
                with self.assertRaises(NotFound):
                    self.customer.get(resource, self.records['beta'][resource]['id'])
        with self.assertRaises(NotFound):
            self.customer.get('clients', peer['id'])
        with self.assertRaises(NotFound):
            self.customer.save_answers(peer_questionnaire['id'], {}, 1, expected_revision=0)

    def test_customer_cannot_read_commercial_or_internal_records(self):
        for resource in ('cities', 'partners', 'campaigns', 'deliveries', 'voucher_lots', 'activities', 'outbox'):
            with self.subTest(resource=resource), self.assertRaises(AccessDenied):
                self.customer.list(resource)
        with self.assertRaises(AccessDenied):
            self.customer.audit_log()

    def test_customer_cannot_create_or_promote_a_result(self):
        with self.assertRaises(AccessDenied):
            self.customer.create('clients', {'first_name': 'Forbidden'})
        with self.assertRaises(AccessDenied):
            self.customer.update('clients', self.records['alpha']['clients']['id'], {'stage': 'won'}, expected_revision=0)
        with self.assertRaises(AccessDenied):
            self.customer.save_assessment(self.records['alpha']['questionnaires']['id'], {'engineVersion': 'forged', 'totalScore': 100}, expected_revision=0)

    def test_draft_revision_and_score_snapshots(self):
        questionnaire = self.records['alpha']['questionnaires']
        updated = self.customer.save_answers(questionnaire['id'], {'monthlyIncome': 3000}, 1, expected_revision=0)
        self.assertEqual(updated['revision'], 1)
        with self.assertRaises(Conflict):
            self.customer.save_answers(questionnaire['id'], {}, 2, expected_revision=0)
        with self.assertRaises(Conflict):
            self.a.save_assessment(questionnaire['id'], {'engineVersion': 'synthetic-1'}, expected_revision=0)
        result = {'engineVersion': 'synthetic-1', 'totalScore': 71}
        saved = self.a.save_assessment(questionnaire['id'], result, expected_revision=1)
        self.assertEqual(saved['answers'], {'monthlyIncome': 3000})
        self.assertEqual(self.a.save_assessment(questionnaire['id'], result, expected_revision=1)['id'], saved['id'])
        with self.assertRaises(Conflict):
            self.a.save_assessment(questionnaire['id'], {**result, 'totalScore': 99}, expected_revision=1)
        self.assertEqual(self.a.get('assessments', self.records['alpha']['assessments']['id'])['result']['totalScore'], 70)

    def test_master_requires_explicit_scope_and_records_reads_and_writes(self):
        for repository in (Repository(self.db, 'master'), Repository(self.db, 'master', tenant_id='alpha')):
            with self.assertRaises(AccessDenied):
                repository.list('clients')
        master = Repository(self.db, 'master', tenant_id='alpha', reason='Verifica sintetica autorizzata')
        self.assertEqual(len(master.list_tenants()), 2)
        self.assertEqual(len(master.list('clients')), 1)
        master.update('cities', self.records['alpha']['cities']['id'], {'name': 'Città aggiornata'}, expected_revision=0)
        events = [r for r in master.audit_log() if r['actor_id'] == 'master']
        self.assertTrue({'read_list', 'update'} <= {r['action'] for r in events})
        self.assertTrue(all(r['tenant_id'] == 'alpha' and r['reason'] for r in events))
        with self.assertRaises(AccessDenied):
            self.a.list_tenants()

    def test_live_suspension_revokes_existing_repository_context(self):
        self.a.list('cities')
        with self.db.transaction() as c:
            c.execute("UPDATE accounts SET status='suspended' WHERE id='advisor-alpha'")
        with self.assertRaises(AccessDenied):
            self.a.list('cities')
        with self.db.transaction() as c:
            c.execute("UPDATE tenants SET status='suspended' WHERE id='beta'")
        with self.assertRaises(AccessDenied):
            self.b.list('cities')
        with self.assertRaises(AccessDenied):
            Repository(self.db, 'customer-beta').list('clients')

    def test_cross_tenant_updates_and_deletions_do_not_mutate(self):
        with self.assertRaises(NotFound):
            self.a.update('cities', self.records['beta']['cities']['id'], {'name': 'Forbidden'}, expected_revision=0)
        with self.assertRaises(NotFound):
            self.a.remove_delivery(self.records['beta']['deliveries']['id'], expected_revision=0)
        self.assertEqual(self.b.get('cities', self.records['beta']['cities']['id'])['name'], 'Città sintetica')

    def test_stock_updates_deletion_and_atomic_failure(self):
        records = self.records['alpha']
        with self.db.transaction() as c:
            audit_before = c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
        with self.assertRaises(Conflict):
            self.a.create('deliveries', {'lot_id': records['voucher_lots']['id'], 'partner_id': records['partners']['id'], 'quantity': 16, 'delivered_on': '2026-09-13'})
        self.assertEqual(len(self.a.list('deliveries')), 1)
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0], audit_before)
        with self.assertRaises(Conflict):
            self.a.update('voucher_lots', records['voucher_lots']['id'], {'printed': 4}, expected_revision=0)
        with self.assertRaises(Conflict):
            self.a.update('deliveries', records['deliveries']['id'], {'quantity': 21}, expected_revision=0)
        self.a.update('deliveries', records['deliveries']['id'], {'quantity': 20}, expected_revision=0)
        with self.assertRaises(Conflict):
            self.a.remove_delivery(records['deliveries']['id'], expected_revision=0)
        self.a.remove_delivery(records['deliveries']['id'], expected_revision=1)
        self.a.update('voucher_lots', records['voucher_lots']['id'], {'printed': 0}, expected_revision=0)

    def test_same_slot_other_tenant_allowed_overlaps_same_tenant_rejected(self):
        self.assertEqual(self.records['alpha']['appointments']['starts_at'], self.records['beta']['appointments']['starts_at'])
        client = self.records['alpha']['clients']['id']
        with self.assertRaises(Conflict):
            self.a.create('appointments', {'client_id': client, 'starts_at': 11000, 'ends_at': 14600})
        next_slot = self.a.create('appointments', {'client_id': client, 'starts_at': 13600, 'ends_at': 17200})
        with self.assertRaises(Conflict):
            self.a.update('appointments', next_slot['id'], {'starts_at': 12000}, expected_revision=0)

    def test_history_is_immutable_even_at_database_level(self):
        for table in ('activities', 'assessments', 'audit_events'):
            with self.subTest(table=table):
                for operation in ('UPDATE ' + table + " SET id='changed'", 'DELETE FROM ' + table):
                    with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as c:
                        c.execute(operation)

    def test_no_invalid_numeric_or_nonfinite_json(self):
        for number in (True, 1.5, '2'):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.a.create('voucher_lots', {'printed': number})
        with self.assertRaises(ValueError):
            self.customer.save_answers(self.records['alpha']['questionnaires']['id'], {'income': float('nan')}, 1, expected_revision=0)

    def test_concurrent_deliveries_cannot_overdraw_stock(self):
        barrier = Barrier(2)
        records = self.records['alpha']
        def deliver(_):
            barrier.wait(timeout=5)
            try:
                self.a.create('deliveries', {'lot_id': records['voucher_lots']['id'], 'partner_id': records['partners']['id'], 'quantity': 10, 'delivered_on': '2026-09-13'})
                return 'saved'
            except Conflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(deliver, range(2)))
        self.assertCountEqual(outcomes, ['saved', 'conflict'])
        self.assertEqual(sum(d['quantity'] for d in self.a.list('deliveries')), 15)

    def test_concurrent_edits_require_reload(self):
        barrier = Barrier(2)
        city = self.records['alpha']['cities']['id']
        def update(index):
            barrier.wait(timeout=5)
            try:
                self.a.update('cities', city, {'name': 'Synthetic ' + str(index)}, expected_revision=0)
                return 'saved'
            except Conflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(update, range(2)))
        self.assertCountEqual(outcomes, ['saved', 'conflict'])
        self.assertEqual(self.a.get('cities', city)['revision'], 1)


if __name__ == '__main__':
    unittest.main()
