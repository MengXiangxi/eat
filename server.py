# -*- coding: utf-8 -*-
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import os
import sqlite3
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'eat.db')
IMG_DIR = os.path.join(BASE_DIR, 'img')


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_meal_vendor_schema(conn):
    columns = {
        row['name']
        for row in conn.execute('PRAGMA table_info(meals)').fetchall()
    }
    if 'vendor_id' in columns:
        return

    conn.execute('ALTER TABLE meals ADD COLUMN vendor_id INTEGER')

    meal_vendor_names = [
        row['order_text'].strip()
        for row in conn.execute(
            """
            SELECT DISTINCT order_text
            FROM meals
            WHERE TRIM(order_text) != ''
            """
        ).fetchall()
    ]

    existing_vendor_names = {
        row['vendor']
        for row in conn.execute('SELECT vendor FROM vendors').fetchall()
    }

    for vendor_name in meal_vendor_names:
        if vendor_name not in existing_vendor_names:
            conn.execute(
                'INSERT INTO vendors (vendor, weight) VALUES (?, ?)',
                (vendor_name, 0),
            )
            existing_vendor_names.add(vendor_name)

    conn.execute(
        """
        UPDATE meals
        SET vendor_id = (
            SELECT id FROM vendors WHERE vendor = TRIM(meals.order_text)
        )
        WHERE vendor_id IS NULL AND TRIM(order_text) != ''
        """
    )
    conn.execute(
        """
        UPDATE meals
        SET order_text = ''
        WHERE vendor_id IS NOT NULL AND TRIM(order_text) != ''
        """
    )
    conn.commit()


def ensure_db():
    os.makedirs(IMG_DIR, exist_ok=True)
    with get_conn() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor TEXT NOT NULL UNIQUE,
                weight INTEGER NOT NULL DEFAULT 0 CHECK(weight >= 0)
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                order_text TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL DEFAULT 0 CHECK(price >= 0),
                rate REAL NOT NULL DEFAULT 1 CHECK(rate >= 0.5 AND rate <= 5),
                image TEXT NOT NULL DEFAULT ''
            )
            '''
        )
        ensure_meal_vendor_schema(conn)
        conn.commit()


def serialize_vendor(row):
    return {
        'id': row['id'],
        'vendor': row['vendor'],
        'weight': row['weight'],
    }


def serialize_meal(row):
    return {
        'id': row['id'],
        'date': row['date'],
        'vendor_id': row['vendor_id'],
        'vendor_name': row['vendor_name'],
        'order': row['order_text'] or '',
        'price': row['price'],
        'rate': row['rate'],
        'image': row['image'],
    }


def read_vendors():
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, vendor, weight FROM vendors ORDER BY id ASC'
        ).fetchall()
    return [serialize_vendor(row) for row in rows]


def read_meals():
    with get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT
                m.id,
                m.date,
                m.vendor_id,
                v.vendor AS vendor_name,
                m.order_text,
                m.price,
                m.rate,
                m.image
            FROM meals AS m
            LEFT JOIN vendors AS v ON v.id = m.vendor_id
            ORDER BY m.date DESC, m.id DESC
            '''
        ).fetchall()
    return [serialize_meal(row) for row in rows]


@app.route('/')
def index():
    return send_from_directory('.', 'eat.html')


@app.route('/common.css')
def common_css():
    return send_from_directory('.', 'common.css')


@app.route('/stats')
def stats():
    return send_from_directory('.', 'stats.html')


@app.route('/api/stats')
def api_stats():
    with get_conn() as conn:
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS totalMeals,
                COUNT(DISTINCT vendor_id) AS vendorsUsed,
                ROUND(AVG(price), 2) AS avgPrice,
                ROUND(MIN(price), 2) AS minPrice,
                ROUND(MAX(price), 2) AS maxPrice,
                ROUND(SUM(price), 2) AS totalSpent,
                ROUND(AVG(rate), 1) AS avgRating
            FROM meals
            WHERE price > 0
            """
        ).fetchone()

        top_vendors = conn.execute(
            """
            SELECT
                v.vendor,
                COUNT(m.id) AS count,
                ROUND(AVG(m.price), 2) AS avgPrice,
                ROUND(SUM(m.price), 2) AS total
            FROM meals m
            JOIN vendors v ON m.vendor_id = v.id
            WHERE m.price > 0
            GROUP BY m.vendor_id
            ORDER BY count DESC, total DESC
            LIMIT 15
            """
        ).fetchall()

        monthly = conn.execute(
            """
            SELECT
                SUBSTR(date, 1, 4) AS month,
                COUNT(*) AS count,
                ROUND(SUM(price), 2) AS total,
                ROUND(AVG(price), 2) AS avgPrice
            FROM meals
            WHERE price > 0
            GROUP BY month
            ORDER BY month
            """
        ).fetchall()

        rating_dist = conn.execute(
            """
            SELECT
                ROUND(rate * 2) / 2 AS rating,
                COUNT(*) AS count
            FROM meals
            WHERE price > 0
            GROUP BY rating
            ORDER BY rating DESC
            """
        ).fetchall()

        price_dist = conn.execute(
            """
            SELECT
                CASE
                    WHEN price = 0 THEN '免费'
                    WHEN price <= 10 THEN '¥0-10'
                    WHEN price <= 15 THEN '¥10-15'
                    WHEN price <= 20 THEN '¥15-20'
                    WHEN price <= 25 THEN '¥20-25'
                    WHEN price <= 30 THEN '¥25-30'
                    WHEN price <= 40 THEN '¥30-40'
                    ELSE '¥40+'
                END AS range,
                COUNT(*) AS count
            FROM meals
            GROUP BY range
            ORDER BY MIN(price)
            """
        ).fetchall()

        vendor_ratings = conn.execute(
            """
            SELECT
                v.vendor,
                COUNT(m.id) AS count,
                ROUND(AVG(m.rate), 1) AS avgRating,
                ROUND(AVG(m.price), 2) AS avgPrice
            FROM meals m
            JOIN vendors v ON m.vendor_id = v.id
            WHERE m.price > 0
            GROUP BY m.vendor_id
            HAVING count >= 2
            ORDER BY avgRating DESC, count DESC
            LIMIT 10
            """
        ).fetchall()

    return jsonify({
        'summary': {
            'totalMeals': summary['totalMeals'],
            'vendorsUsed': summary['vendorsUsed'],
            'avgPrice': summary['avgPrice'],
            'minPrice': summary['minPrice'],
            'maxPrice': summary['maxPrice'],
            'totalSpent': summary['totalSpent'],
            'avgRating': summary['avgRating'],
        },
        'topVendors': [dict(r) for r in top_vendors],
        'monthly': [dict(r) for r in monthly],
        'ratingDist': [dict(r) for r in rating_dist],
        'priceDist': [dict(r) for r in price_dist],
        'vendorRatings': [dict(r) for r in vendor_ratings],
    })


BJ_TZ = timezone(timedelta(hours=8))


def ensure_kji_weight():
    """自动调整K记权重：星期四 1000，其他 100"""
    with get_conn() as conn:
        kji = conn.execute(
            "SELECT id, weight FROM vendors WHERE vendor = 'K记'"
        ).fetchone()
        if not kji:
            return
        target = 1000 if datetime.now(BJ_TZ).weekday() == 3 else 100
        if kji['weight'] != target:
            conn.execute(
                "UPDATE vendors SET weight = ? WHERE id = ?",
                (target, kji['id']),
            )
            conn.commit()


@app.route('/api/vendors', methods=['GET'])
def get_vendors():
    ensure_kji_weight()
    return jsonify(read_vendors())


@app.route('/api/meals', methods=['GET'])
def get_meals():
    return jsonify(read_meals())


@app.route('/img/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMG_DIR, filename)


ensure_db()

if __name__ == '__main__':
    print('Server running at http://localhost:5000')
    print('Open http://localhost:5000 in your browser')
    app.run(host='0.0.0.0', debug=False, port=5000)
