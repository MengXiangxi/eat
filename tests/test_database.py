import csv
import os
import shutil
import tempfile
import unittest

import server_manage


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        # Use isolated CSV files so tests run against a clean slate.
        self.original_vendor_csv = server_manage.CSV_VENDOR_FILE
        self.original_meal_csv = server_manage.CSV_MEAL_FILE

        self.temp_dir = tempfile.mkdtemp()
        server_manage.CSV_VENDOR_FILE = os.path.join(self.temp_dir, "vendors.csv")
        server_manage.CSV_MEAL_FILE = os.path.join(self.temp_dir, "meals.csv")

        server_manage.ensure_db()
        self.client = server_manage.app.test_client()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        server_manage.CSV_VENDOR_FILE = self.original_vendor_csv
        server_manage.CSV_MEAL_FILE = self.original_meal_csv
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ensure_db_creates_empty_csv(self):
        self.assertTrue(os.path.exists(server_manage.CSV_VENDOR_FILE))
        self.assertTrue(os.path.exists(server_manage.CSV_MEAL_FILE))

        with open(server_manage.CSV_VENDOR_FILE, newline='', encoding='utf-8') as f:
            reader = list(csv.reader(f))
        with open(server_manage.CSV_MEAL_FILE, newline='', encoding='utf-8') as f:
            meal_reader = list(csv.reader(f))

        self.assertEqual(reader, [["vendor", "weight"]])
        self.assertEqual(meal_reader, [["date", "order", "price", "rate", "image"]])

    def test_add_and_read_vendor(self):
        resp = self.client.post("/api/vendors", json={"vendor": "Test Vendor", "weight": 80})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertTrue(data["success"])
        self.assertEqual(len(data["vendors"]), 1)
        vendor = data["vendors"][0]
        self.assertEqual(vendor["vendor"], "Test Vendor")
        self.assertEqual(vendor["weight"], 80)

        with open(server_manage.CSV_VENDOR_FILE, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["vendor"], "Test Vendor")
        self.assertEqual(int(rows[0]["weight"]), 80)

    def test_add_and_update_meal(self):
        create_resp = self.client.post(
            "/api/meals",
            json={
                "date": "2024-01-02",
                "order": "Noodles",
                "price": 10.5,
                "rate": 4,
                "image": "pic.png",
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        created = create_resp.get_json()
        self.assertTrue(created["success"])
        self.assertEqual(len(created["meals"]), 1)
        meal = created["meals"][0]
        self.assertEqual(meal["order"], "Noodles")
        self.assertAlmostEqual(meal["price"], 10.5)
        self.assertEqual(meal["rate"], 4)

        update_resp = self.client.put("/api/meals/0", json={"price": 12.0, "rate": 5})
        self.assertEqual(update_resp.status_code, 200)
        updated = update_resp.get_json()

        self.assertTrue(updated["success"])
        updated_meal = updated["meals"][0]
        self.assertAlmostEqual(updated_meal["price"], 12.0)
        self.assertEqual(updated_meal["rate"], 5)

        with open(server_manage.CSV_MEAL_FILE, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(float(rows[0]["price"]), 12.0)
        self.assertEqual(int(rows[0]["rate"]), 5)


if __name__ == "__main__":
    unittest.main()
