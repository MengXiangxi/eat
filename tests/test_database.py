"""SQLite/API regression tests. Set isolated paths BEFORE importing either service."""
import concurrent.futures
import importlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEST_DIR = ROOT / 'sandbox' / 'tests'
TEST_DIR.mkdir(parents=True, exist_ok=True)


class ClosingConnection(sqlite3.Connection):
    """Keep real SQLite transaction behavior and deterministically close test handles."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


_CONNECT = sqlite3.connect

def connect_closed(*args, **kwargs):
    kwargs['factory'] = ClosingConnection
    return _CONNECT(*args, **kwargs)


# Existing services use SQLite's transaction context (which does not close a handle).
# Close real test connections deterministically so Windows can remove temporary DBs.
_CONNECT_PATCH = patch.object(sqlite3, 'connect', connect_closed)
_CONNECT_PATCH.start()
_IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='eat-import-', dir=TEST_DIR)
os.environ['EAT_DB_PATH'] = str(Path(_IMPORT_TEMP.name) / 'eat.db')
os.environ['EAT_IMG_PATH'] = str(Path(_IMPORT_TEMP.name) / 'img')
server_manage = importlib.import_module('server_manage')
server = importlib.import_module('server')
MODULES = (server, server_manage)


def tearDownModule():
    _CONNECT_PATCH.stop()
    _IMPORT_TEMP.cleanup()


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eat-test-', dir=TEST_DIR)
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'eat.db')
        self.img = str(Path(self.temp.name) / 'img')
        for module in MODULES:
            db_patch = patch.object(module, 'DB_FILE', self.db)
            img_patch = patch.object(module, 'IMG_DIR', self.img)
            db_patch.start()
            img_patch.start()
            self.addCleanup(db_patch.stop)
            self.addCleanup(img_patch.stop)
        server_manage.ensure_db()
        self.client = server_manage.app.test_client()
        self.public = server.app.test_client()

    def conn(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def clear(self):
        with self.conn() as conn:
            conn.execute('DELETE FROM meals')
            conn.execute('DELETE FROM vendors')

    def old_schema(self, with_vendor_id=False):
        with self.conn() as conn:
            conn.execute('DROP TABLE meals')
            conn.execute('DROP TABLE vendors')
            conn.execute('CREATE TABLE vendors (id INTEGER PRIMARY KEY, vendor TEXT UNIQUE NOT NULL, weight INTEGER NOT NULL DEFAULT 0)')
            extra = ', vendor_id INTEGER' if with_vendor_id else ''
            conn.execute("CREATE TABLE meals (id INTEGER PRIMARY KEY, date TEXT NOT NULL, order_text TEXT NOT NULL DEFAULT '', price REAL NOT NULL DEFAULT 0, rate REAL NOT NULL DEFAULT 1, image TEXT NOT NULL DEFAULT ''" + extra + ')')
            conn.execute("INSERT INTO vendors (id, vendor, weight) VALUES (1, 'Old', 80)")
            if with_vendor_id:
                conn.execute("INSERT INTO meals (date, vendor_id, order_text, price, rate, image) VALUES ('261001', 1, 'keep note', 12, 4.5, 'old.png')")
            else:
                conn.execute("INSERT INTO meals (date, order_text, price, rate, image) VALUES ('261001', ' Old ', 12, 4.5, 'old.png')")
                conn.execute("INSERT INTO meals (date, order_text, price, rate) VALUES ('261002', ' New ', 0, 5)")

    def vendor(self, name='Test Vendor', weight=80):
        result = self.client.post('/api/vendors', json={'vendor': name, 'weight': weight})
        self.assertEqual(result.status_code, 200)
        return next(v['id'] for v in result.get_json()['vendors'] if v['vendor'] == name)

    def meal(self, vendor_id, price=10, rate=4, include=True, date='261001'):
        payload = {'date': date, 'vendor_id': vendor_id, 'order': 'note', 'price': price, 'rate': rate, 'image': 'pic.png'}
        if include is not ...:
            payload['include_in_stats'] = include
        response = self.client.post('/api/meals', json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        return max(response.get_json()['meals'], key=lambda m: m['id'])

    def stats(self):
        result = self.public.get('/api/stats')
        self.assertEqual(result.status_code, 200)
        return result.get_json()

    def four_combinations(self):
        a, b = self.vendor('A'), self.vendor('B')
        return [self.meal(a, 10, 5, True), self.meal(a, 0, 3, True),
                self.meal(b, 90, 1, False, '261101'), self.meal(b, 0, 3, False, '261101')]

    def test_import_uses_environment_paths(self):
        self.assertEqual(server.IMG_DIR, self.img)
        self.assertEqual(server_manage.DB_FILE, self.db)
        self.assertTrue((Path(_IMPORT_TEMP.name) / 'eat.db').is_file())
        self.assertNotEqual(Path(server.DB_FILE).resolve(), (ROOT / 'eat.db').resolve())

    def test_fresh_schema_both_services_idempotent(self):
        for module in MODULES:
            module.ensure_db()
            module.ensure_db()
        with self.conn() as conn:
            columns = {r['name']: r for r in conn.execute('PRAGMA table_info(meals)')}
            column = columns['include_in_stats']
            self.assertEqual(column['type'], 'INTEGER')
            self.assertEqual(column['notnull'], 1)
            self.assertEqual(column['dflt_value'], '1')
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.client.get('/api/meals').get_json(), [])

    def test_existing_vendor_column_does_not_block_stats_migration(self):
        for first, second in (MODULES, MODULES[::-1]):
            with self.subTest(first=first.__name__):
                self.old_schema(with_vendor_id=True)
                first.ensure_db()
                second.ensure_db()
                first.ensure_db()
                with self.conn() as conn:
                    row = conn.execute('SELECT * FROM meals').fetchone()
                    self.assertEqual(row['include_in_stats'], 1)
                    self.assertEqual(row['vendor_id'], 1)
                    self.assertEqual(row['order_text'], 'keep note')
                    self.assertEqual((row['price'], row['rate'], row['image']), (12, 4.5, 'old.png'))
                self.assertIs(self.client.get('/api/meals').get_json()[0]['include_in_stats'], True)
                self.assertEqual(self.public.get('/api/meals').status_code, 404)

    def test_legacy_vendor_backfill_and_history_true(self):
        for first, second in (MODULES, MODULES[::-1]):
            with self.subTest(first=first.__name__):
                self.old_schema()
                first.ensure_db()
                second.ensure_db()
                first.ensure_db()
                with self.conn() as conn:
                    rows = conn.execute('SELECT m.*, v.vendor FROM meals m LEFT JOIN vendors v ON m.vendor_id=v.id ORDER BY m.id').fetchall()
                    self.assertEqual([r['vendor'] for r in rows], ['Old', 'New'])
                    self.assertEqual([r['order_text'] for r in rows], ['', ''])
                    self.assertEqual([r['include_in_stats'] for r in rows], [1, 1])
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM vendors').fetchone()[0], 2)

    def test_migration_preserves_existing_false(self):
        vendor = self.vendor()
        self.meal(vendor, include=False)
        for module in MODULES:
            module.ensure_db()
        self.assertIs(self.client.get('/api/meals').get_json()[0]['include_in_stats'], False)

    def test_old_insert_defaults_true_and_constraints(self):
        for schema in ('fresh', 'existing_vendor', 'legacy'):
            with self.subTest(schema=schema):
                if schema != 'fresh':
                    self.old_schema(schema == 'existing_vendor')
                    server.ensure_db()
                with self.conn() as conn:
                    conn.execute("INSERT INTO meals (date, order_text, price, rate, image) VALUES ('261003', 'legacy SQL', 0, 4, '')")
                    self.assertEqual(conn.execute('SELECT include_in_stats FROM meals ORDER BY id DESC LIMIT 1').fetchone()[0], 1)
                    for invalid in (None, -1, 2, 0.5, 'true'):
                        with self.subTest(value=invalid), self.assertRaises(sqlite3.IntegrityError):
                            conn.execute('INSERT INTO meals (date, include_in_stats) VALUES (?, ?)', ('261001', invalid))

    def test_atomic_migration_rolls_back_on_failure(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                self.old_schema()
                with patch.object(module, 'ensure_meal_stats_schema', side_effect=RuntimeError('failure')):
                    with self.assertRaises(RuntimeError):
                        module.ensure_db()
                with self.conn() as conn:
                    names = {r['name'] for r in conn.execute('PRAGMA table_info(meals)')}
                    self.assertNotIn('vendor_id', names)
                    self.assertNotIn('include_in_stats', names)
                    self.assertEqual(conn.execute('SELECT order_text FROM meals ORDER BY id LIMIT 1').fetchone()[0], ' Old ')
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM vendors').fetchone()[0], 1)

    def test_concurrent_migration_waits_before_schema_inspection(self):
        for with_vendor_id in (False, True):
            with self.subTest(with_vendor_id=with_vendor_id):
                self.old_schema(with_vendor_id)
                blocker = self.conn()
                blocker.execute('BEGIN IMMEDIATE')
                barrier = threading.Barrier(9)
                def migrate(index):
                    barrier.wait(timeout=5)
                    MODULES[index % 2].ensure_db()
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    jobs = [pool.submit(migrate, i) for i in range(8)]
                    barrier.wait(timeout=5)
                    time.sleep(0.15)
                    self.assertTrue(all(not j.done() for j in jobs))
                    blocker.commit()
                    blocker.close()
                    for job in jobs:
                        job.result(timeout=10)
                with self.conn() as conn:
                    names = [r['name'] for r in conn.execute('PRAGMA table_info(meals)')]
                    self.assertEqual(names.count('include_in_stats'), 1)
                    self.assertEqual(names.count('vendor_id'), 1)
                    self.assertTrue(all(r[0] == 1 for r in conn.execute('SELECT include_in_stats FROM meals')))
                    self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_vendor_crud_and_referenced_delete(self):
        vendor = self.vendor()
        self.assertEqual(self.client.put(f'/api/vendors/{vendor}', json={'vendor': 'Renamed', 'weight': 12}).status_code, 200)
        self.meal(vendor, include=False)
        self.assertEqual(self.client.delete(f'/api/vendors/{vendor}').status_code, 400)
        meal_id = self.client.get('/api/meals').get_json()[0]['id']
        self.assertEqual(self.client.delete(f'/api/meals/{meal_id}').status_code, 200)
        self.assertEqual(self.client.delete(f'/api/vendors/{vendor}').status_code, 200)

    def test_post_default_true_and_explicit_bools(self):
        vendor = self.vendor()
        for value in (..., True, False):
            with self.subTest(value=value):
                meal = self.meal(vendor, include=value)
                self.assertIs(meal['include_in_stats'], value if value is not ... else True)
                with self.conn() as conn:
                    self.assertEqual(conn.execute('SELECT include_in_stats FROM meals WHERE id=?', (meal['id'],)).fetchone()[0], int(meal['include_in_stats']))

    def test_put_preserves_omitted_value_and_accepts_bools(self):
        vendor = self.vendor()
        for original in (True, False):
            with self.subTest(original=original):
                meal = self.meal(vendor, include=original)
                response = self.client.put(f"/api/meals/{meal['id']}", json={'price': 12.5, 'rate': 3.5})
                self.assertEqual(response.status_code, 200)
                changed = next(m for m in response.get_json()['meals'] if m['id'] == meal['id'])
                self.assertIs(changed['include_in_stats'], original)
                self.assertEqual((changed['order'], changed['image'], changed['price'], changed['rate']), ('note', 'pic.png', 12.5, 3.5))
                for flag in (False, True):
                    result = self.client.put(f"/api/meals/{meal['id']}", json={'include_in_stats': flag})
                    self.assertEqual(result.status_code, 200)
                    changed = next(m for m in result.get_json()['meals'] if m['id'] == meal['id'])
                    self.assertIs(changed['include_in_stats'], flag)

    def test_post_put_reject_nonbool_without_writes(self):
        vendor = self.vendor()
        meal = self.meal(vendor, include=False)
        original = self.client.get('/api/meals').get_json()
        for value in (0, 1, 2, 0.0, 1.0, 'true', 'false', '', None, [], {}, [True]):
            for method in ('post', 'put'):
                with self.subTest(method=method, value=value):
                    payload = {'date': '261001', 'vendor_id': vendor, 'price': 42, 'rate': 5, 'include_in_stats': value}
                    url = '/api/meals' if method == 'post' else f"/api/meals/{meal['id']}"
                    response = getattr(self.client, method)(url, json=payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('include_in_stats', response.get_json()['error'])
                    self.assertEqual(self.client.get('/api/meals').get_json(), original)

    def test_get_both_services_keeps_disabled_and_json_bools(self):
        self.four_combinations()
        meals = self.client.get('/api/meals').get_json()
        self.assertEqual(len(meals), 4)
        self.assertTrue(all(type(m['include_in_stats']) is bool for m in meals))
        self.assertEqual(sum(not m['include_in_stats'] for m in meals), 2)
        self.assertEqual(self.public.get('/api/meals').status_code, 404)
        self.assertEqual(self.public.get('/img/pic.png').status_code, 404)

    def test_four_combinations_exact_statistics(self):
        self.four_combinations()
        data = self.stats()
        self.assertEqual(data['summary'], {'totalMeals': 2, 'totalSpent': 10, 'avgPrice': 10, 'minPrice': 10, 'maxPrice': 10, 'avgRating': 3, 'vendorsUsed': 2})
        self.assertEqual(data['monthly'], [{'month': '2610', 'count': 2, 'total': 10, 'avgPrice': 10}])
        self.assertEqual(data['priceDist'], [{'range': '免费', 'count': 1}, {'range': '¥0-10', 'count': 1}])
        self.assertEqual(data['ratingDist'], [{'rating': 5, 'count': 1}, {'rating': 3, 'count': 2}, {'rating': 1, 'count': 1}])
        self.assertEqual(data['topVendors'], [{'vendor': 'B', 'count': 2, 'total': 90, 'avgPrice': 90}, {'vendor': 'A', 'count': 2, 'total': 10, 'avgPrice': 10}])
        self.assertEqual(data['vendorRatings'], [{'vendor': 'A', 'count': 2, 'avgRating': 4, 'avgPrice': 10}, {'vendor': 'B', 'count': 2, 'avgRating': 2, 'avgPrice': 90}])

    def test_toggle_changes_consumption_not_rankings_or_ratings(self):
        meals = self.four_combinations()
        before = self.stats()
        for meal in meals:
            self.assertEqual(self.client.put(f"/api/meals/{meal['id']}", json={'include_in_stats': not meal['include_in_stats']}).status_code, 200)
        after = self.stats()
        for key in ('topVendors', 'vendorRatings', 'ratingDist'):
            self.assertEqual(before[key], after[key])
        for key in ('avgRating', 'vendorsUsed'):
            self.assertEqual(before['summary'][key], after['summary'][key])
        self.assertEqual(after['summary']['totalMeals'], 2)
        self.assertEqual(after['summary']['totalSpent'], 90)
        self.assertEqual(after['summary']['avgPrice'], 90)
        self.assertEqual(after['summary']['minPrice'], 90)
        self.assertEqual(after['summary']['maxPrice'], 90)
        self.assertEqual(after['monthly'], [{'month': '2611', 'count': 2, 'total': 90, 'avgPrice': 90}])
        self.assertEqual(after['priceDist'], [{'range': '免费', 'count': 1}, {'range': '¥40+', 'count': 1}])

    def test_only_free(self):
        vendor = self.vendor()
        self.meal(vendor, 0, 5, True)
        self.meal(vendor, 0, 3, False)
        data = self.stats()
        self.assertEqual(data['summary'], {'totalMeals': 1, 'totalSpent': 0, 'avgPrice': None, 'minPrice': None, 'maxPrice': 0, 'avgRating': 4, 'vendorsUsed': 1})
        self.assertEqual(data['monthly'], [{'month': '2610', 'count': 1, 'total': 0, 'avgPrice': None}])
        self.assertEqual(data['priceDist'], [{'range': '免费', 'count': 1}])
        self.assertEqual(data['topVendors'], [{'vendor': 'Test Vendor', 'count': 2, 'total': 0, 'avgPrice': None}])
        self.assertEqual(data['vendorRatings'], [{'vendor': 'Test Vendor', 'count': 2, 'avgRating': 4, 'avgPrice': None}])

    def test_all_disabled(self):
        vendor = self.vendor()
        self.meal(vendor, 20, 5, False)
        self.meal(vendor, 0, 3, False)
        data = self.stats()
        self.assertEqual(data['summary'], {'totalMeals': 0, 'totalSpent': 0, 'avgPrice': None, 'minPrice': None, 'maxPrice': None, 'avgRating': 4, 'vendorsUsed': 1})
        self.assertEqual(data['monthly'], [])
        self.assertEqual(data['priceDist'], [])
        self.assertEqual(data['topVendors'][0]['count'], 2)
        self.assertEqual(data['vendorRatings'][0]['count'], 2)
        self.assertEqual(sum(r['count'] for r in data['ratingDist']), 2)

    def test_empty(self):
        data = self.stats()
        self.assertEqual(data['summary'], {'totalMeals': 0, 'totalSpent': 0, 'avgPrice': None, 'minPrice': None, 'maxPrice': None, 'avgRating': None, 'vendorsUsed': 0})
        for key in ('monthly', 'priceDist', 'ratingDist', 'topVendors', 'vendorRatings'):
            self.assertEqual(data[key], [])

    def test_monthly_free_only_and_disabled_only_months(self):
        vendor = self.vendor()
        self.meal(vendor, 0, include=True, date='260101')
        self.meal(vendor, 20, include=False, date='260102')
        self.meal(vendor, 30, include=True, date='260201')
        self.meal(vendor, 10, include=True, date='260202')
        self.meal(vendor, 0, include=True, date='260203')
        self.meal(vendor, 40, include=False, date='260301')
        self.assertEqual(self.stats()['monthly'], [{'month': '2601', 'count': 1, 'total': 0, 'avgPrice': None}, {'month': '2602', 'count': 3, 'total': 40, 'avgPrice': 20}])

    def test_price_distribution_all_boundaries(self):
        vendor = self.vendor()
        prices = (0, 0.01, 10, 10.01, 15, 15.01, 20, 20.01, 25, 25.01, 30, 30.01, 40, 40.01)
        for price in prices:
            self.meal(vendor, price, include=True)
            self.meal(vendor, price, include=False)
        self.assertEqual(self.stats()['priceDist'], [{'range': r, 'count': c} for r, c in [('免费', 1), ('¥0-10', 2), ('¥10-15', 2), ('¥15-20', 2), ('¥20-25', 2), ('¥25-30', 2), ('¥30-40', 2), ('¥40+', 1)]])

    def test_ranking_limits_and_count_threshold_include_free_disabled(self):
        for index in range(17):
            vendor = self.vendor(f'V{index:02}')
            self.meal(vendor, 0, 4, False)
            self.meal(vendor, index + 1, 4, False)
        single = self.vendor('single-high-rating')
        self.meal(single, 0, 5, True)
        data = self.stats()
        self.assertEqual(len(data['topVendors']), 15)
        self.assertEqual([v['vendor'] for v in data['topVendors']], [f'V{i:02}' for i in range(16, 1, -1)])
        self.assertEqual(len(data['vendorRatings']), 10)
        self.assertTrue(all(v['count'] == 2 for v in data['vendorRatings']))
        self.assertNotIn('single-high-rating', [v['vendor'] for v in data['vendorRatings']])
        self.assertEqual(data['summary']['vendorsUsed'], 18)

    def test_c1_dynamic_weight_lifecycle_and_zero_protection(self):
        res = self.client.post('/api/vendors', json={'vendor': 'NewShop'})
        self.assertEqual(res.status_code, 200)
        v_id = next(v['id'] for v in res.get_json()['vendors'] if v['vendor'] == 'NewShop')
        self.assertEqual(next(v['weight'] for v in res.get_json()['vendors'] if v['id'] == v_id), 200)

        # 1st meal: 4.0 star -> (200 * 0.5 + 10) * sqrt(2/2) = 110
        m1 = self.meal(v_id, price=20, rate=4.0, date='261001')
        w1 = next(v['weight'] for v in self.client.get('/api/vendors').get_json() if v['id'] == v_id)
        self.assertEqual(w1, 110)

        # 2nd meal: 3.0 star (unchanged Q=110) -> 110 * sqrt(2/3) = 89.81 -> 90
        m2 = self.meal(v_id, price=20, rate=3.0, date='261002')
        w2 = next(v['weight'] for v in self.client.get('/api/vendors').get_json() if v['id'] == v_id)
        self.assertEqual(w2, 90)

        # Update 2nd meal to 4.0 star -> Q=120, 120 * sqrt(2/3) = 97.98 -> 98
        self.client.put(f"/api/meals/{m2['id']}", json={'rate': 4.0})
        w2_updated = next(v['weight'] for v in self.client.get('/api/vendors').get_json() if v['id'] == v_id)
        self.assertEqual(w2_updated, 98)

        # Delete both meals -> weight returns to 200
        self.client.delete(f"/api/meals/{m2['id']}")
        self.client.delete(f"/api/meals/{m1['id']}")
        w_reset = next(v['weight'] for v in self.client.get('/api/vendors').get_json() if v['id'] == v_id)
        self.assertEqual(w_reset, 200)

        # Zero-weight vendor stays 0 after adding meals
        z_id = self.vendor('ZeroShop', weight=0)
        self.meal(z_id, price=15, rate=5.0, date='261003')
        w_zero = next(v['weight'] for v in self.client.get('/api/vendors').get_json() if v['id'] == z_id)
        self.assertEqual(w_zero, 0)

    def test_kji_thursday_read_only_override(self):
        from datetime import datetime
        k_id = self.vendor('K记', weight=200)
        self.meal(k_id, price=25, rate=3.0, date='261001')  # C1 weight becomes 100
        with patch.object(server, 'datetime') as mock_dt:
            # Thursday (weekday == 3)
            mock_dt.now.return_value = datetime(2026, 11, 5, 12, 0, tzinfo=server.BJ_TZ)
            pub_vendors = self.public.get('/api/vendors').get_json()
            self.assertEqual(next(v['weight'] for v in pub_vendors if v['id'] == k_id), 1000)
            # DB weight remains 100
            mgr_vendors = self.client.get('/api/vendors').get_json()
            self.assertEqual(next(v['weight'] for v in mgr_vendors if v['id'] == k_id), 100)

            # Monday (weekday == 0)
            mock_dt.now.return_value = datetime(2026, 11, 9, 12, 0, tzinfo=server.BJ_TZ)
            pub_vendors_mon = self.public.get('/api/vendors').get_json()
            self.assertEqual(next(v['weight'] for v in pub_vendors_mon if v['id'] == k_id), 100)


if __name__ == '__main__':
    unittest.main(verbosity=2)
