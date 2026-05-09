# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import os
import sqlite3
import uuid
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'eat.db')
IMG_DIR = os.path.join(BASE_DIR, 'img')
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


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


def get_vendor(vendor_id):
    with get_conn() as conn:
        row = conn.execute(
            'SELECT id, vendor, weight FROM vendors WHERE id = ?',
            (vendor_id,),
        ).fetchone()
    return row


def get_meal(meal_id):
    with get_conn() as conn:
        row = conn.execute(
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
            WHERE m.id = ?
            ''',
            (meal_id,),
        ).fetchone()
    return row


def parse_weight(value):
    try:
        weight = int(value)
    except (TypeError, ValueError):
        return None
    return weight if weight >= 0 else None


def parse_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price >= 0 else None


def parse_rate(value):
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate < 0.5 or rate > 5:
        return None
    if abs(rate * 2 - round(rate * 2)) > 1e-6:
        return None
    return round(rate * 2) / 2


def parse_vendor_id(value):
    if value in (None, ''):
        return None
    try:
        vendor_id = int(value)
    except (TypeError, ValueError):
        return None
    return vendor_id if vendor_id > 0 else None


def validate_meal_payload(data, current=None):
    date = (data.get('date') or '').strip() if 'date' in data else (current['date'] if current else '')
    order = str(data.get('order') or '').strip() if 'order' in data else (current['order_text'] if current else '')
    price = parse_price(data.get('price')) if 'price' in data else (current['price'] if current else None)
    rate = parse_rate(data.get('rate')) if 'rate' in data else (current['rate'] if current else None)
    image = str(data.get('image') or '').strip() if 'image' in data else (current['image'] if current else '')
    vendor_id = parse_vendor_id(data.get('vendor_id')) if 'vendor_id' in data else (current['vendor_id'] if current else None)

    if not date:
        return None, '日期不能为空'
    if price is None:
        return None, '价格必须大于等于0'
    if rate is None:
        return None, '评价必须在0.5-5之间，且以0.5为步长'
    if vendor_id is None:
        return None, '必须选择商家'
    if vendor_id is not None and get_vendor(vendor_id) is None:
        return None, '无效的商家ID'

    return {
        'date': date,
        'vendor_id': vendor_id,
        'order_text': order,
        'price': price,
        'rate': rate,
        'image': image,
    }, None


def allowed_file(filename):
    _, ext = os.path.splitext(filename)
    return ext.lower() in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    return send_from_directory('.', 'eat_manage.html')


@app.route("/common.css")
def common_css():
    return send_from_directory(".", "common.css")


@app.route('/eat_manage.html')
def eat_manage():
    return send_from_directory('.', 'eat_manage.html')


@app.route('/api/vendors', methods=['GET'])
def get_vendors():
    return jsonify(read_vendors())


@app.route('/api/vendors', methods=['POST'])
def add_vendor():
    data = request.get_json(silent=True) or {}
    vendor_name = (data.get('vendor') or '').strip()
    weight = parse_weight(data.get('weight', 100))

    if not vendor_name:
        return jsonify({'error': '商家名称不能为空'}), 400
    if weight is None:
        return jsonify({'error': '权重必须是大于等于0的整数'}), 400

    try:
        with get_conn() as conn:
            conn.execute(
                'INSERT INTO vendors (vendor, weight) VALUES (?, ?)',
                (vendor_name, weight),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': '该商家已存在'}), 400

    return jsonify({'success': True, 'vendors': read_vendors()})


@app.route('/api/vendors/<int:vendor_id>', methods=['PUT'])
def update_vendor(vendor_id):
    data = request.get_json(silent=True) or {}
    current = get_vendor(vendor_id)
    if current is None:
        return jsonify({'error': '无效的商家ID'}), 400

    new_name = (data.get('vendor') or '').strip() or current['vendor']
    new_weight = current['weight'] if data.get('weight') is None else parse_weight(data.get('weight'))

    if not new_name:
        return jsonify({'error': '商家名称不能为空'}), 400
    if new_weight is None:
        return jsonify({'error': '权重必须是大于等于0的整数'}), 400

    try:
        with get_conn() as conn:
            conn.execute(
                'UPDATE vendors SET vendor = ?, weight = ? WHERE id = ?',
                (new_name, new_weight, vendor_id),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': '该商家名称已存在'}), 400

    return jsonify({'success': True, 'vendors': read_vendors()})


@app.route('/api/vendors/<int:vendor_id>', methods=['DELETE'])
def delete_vendor(vendor_id):
    if get_vendor(vendor_id) is None:
        return jsonify({'error': '无效的商家ID'}), 400

    with get_conn() as conn:
        linked_meal = conn.execute(
            'SELECT id FROM meals WHERE vendor_id = ? LIMIT 1',
            (vendor_id,),
        ).fetchone()
        if linked_meal is not None:
            return jsonify({'error': '该商家已被点餐记录引用，不能删除'}), 400
        conn.execute('DELETE FROM vendors WHERE id = ?', (vendor_id,))
        conn.commit()

    return jsonify({'success': True, 'vendors': read_vendors()})


@app.route('/api/meals', methods=['GET'])
def get_meals():
    return jsonify(read_meals())


@app.route('/api/meals', methods=['POST'])
def add_meal():
    data = request.get_json(silent=True) or {}
    payload, error = validate_meal_payload(data)
    if error:
        return jsonify({'error': error}), 400

    with get_conn() as conn:
        conn.execute(
            'INSERT INTO meals (date, vendor_id, order_text, price, rate, image) VALUES (?, ?, ?, ?, ?, ?)',
            (
                payload['date'],
                payload['vendor_id'],
                payload['order_text'],
                payload['price'],
                payload['rate'],
                payload['image'],
            ),
        )
        conn.commit()

    return jsonify({'success': True, 'meals': read_meals()})


@app.route('/api/meals/<int:meal_id>', methods=['PUT'])
def update_meal(meal_id):
    data = request.get_json(silent=True) or {}
    current = get_meal(meal_id)
    if current is None:
        return jsonify({'error': '无效的点餐记录ID'}), 400

    payload, error = validate_meal_payload(data, current)
    if error:
        return jsonify({'error': error}), 400

    with get_conn() as conn:
        conn.execute(
            'UPDATE meals SET date = ?, vendor_id = ?, order_text = ?, price = ?, rate = ?, image = ? WHERE id = ?',
            (
                payload['date'],
                payload['vendor_id'],
                payload['order_text'],
                payload['price'],
                payload['rate'],
                payload['image'],
                meal_id,
            ),
        )
        conn.commit()

    return jsonify({'success': True, 'meals': read_meals()})


@app.route('/api/meals/<int:meal_id>', methods=['DELETE'])
def delete_meal(meal_id):
    if get_meal(meal_id) is None:
        return jsonify({'error': '无效的点餐记录ID'}), 400

    with get_conn() as conn:
        conn.execute('DELETE FROM meals WHERE id = ?', (meal_id,))
        conn.commit()

    return jsonify({'success': True, 'meals': read_meals()})


@app.route('/api/upload_image', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({'error': '未找到文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '文件名不能为空'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': '仅支持png/jpg/jpeg/gif/webp格式'}), 400

    filename = secure_filename(file.filename)
    _, ext = os.path.splitext(filename)
    unique_name = f'{uuid.uuid4().hex}{ext.lower()}'
    os.makedirs(IMG_DIR, exist_ok=True)
    file.save(os.path.join(IMG_DIR, unique_name))

    return jsonify({'success': True, 'filename': unique_name, 'url': f'/img/{unique_name}'})


@app.route('/img/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMG_DIR, filename)


ensure_db()

if __name__ == '__main__':
    print('Management server running at http://localhost:5001')
    print('Open http://localhost:5001 in your browser for management')
    app.run(host='0.0.0.0', debug=True, port=5001)
