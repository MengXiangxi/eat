# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import os
import csv

app = Flask(__name__)
CORS(app)

# Absolute paths so both public (5000) and manage (5001) services share the same storage
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_VENDOR_FILE = os.path.join(BASE_DIR, 'db.csv')
CSV_MEAL_FILE = os.path.join(BASE_DIR, 'db_meal.csv')
IMG_DIR = os.path.join(BASE_DIR, 'img')


def ensure_db():
    """Create CSV files and folders if missing."""
    os.makedirs(BASE_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)

    if not os.path.exists(CSV_VENDOR_FILE):
        with open(CSV_VENDOR_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['vendor', 'weight'])

    if not os.path.exists(CSV_MEAL_FILE):
        with open(CSV_MEAL_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['date', 'order', 'price', 'rate', 'image'])


def read_vendors():
    if not os.path.exists(CSV_VENDOR_FILE):
        return []

    vendors = []
    with open(CSV_VENDOR_FILE, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            vendor_name = (row.get('vendor') or '').strip()
            if not vendor_name:
                continue
            try:
                weight = int(row.get('weight', 0))
            except (TypeError, ValueError):
                weight = 0
            vendors.append({'id': idx, 'vendor': vendor_name, 'weight': weight})
    return vendors


def save_vendors(vendors):
    with open(CSV_VENDOR_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['vendor', 'weight'])
        writer.writeheader()
        for vendor in vendors:
            writer.writerow({'vendor': vendor['vendor'], 'weight': int(vendor.get('weight', 0))})


def read_meals():
    if not os.path.exists(CSV_MEAL_FILE):
        return []

    meals = []
    with open(CSV_MEAL_FILE, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            try:
                price = float(row.get('price', 0) or 0)
            except (TypeError, ValueError):
                price = 0.0
            try:
                rate = float(row.get('rate', 1) or 1)
                rate = max(0.5, min(round(rate * 2) / 2, 5))
            except (TypeError, ValueError):
                rate = 1.0

            meals.append({
                'id': idx,
                'date': (row.get('date') or '').strip(),
                'order': (row.get('order') or row.get('order_text') or '').strip(),
                'price': price,
                'rate': rate,
                'image': (row.get('image') or '').strip()
            })

    # Sort by date desc then original order desc (newest first)
    meals = sorted(meals, key=lambda m: (m['date'], m['id']), reverse=True)
    # Reassign sequential ids after sorting to keep API indexes stable
    for idx, meal in enumerate(meals):
        meal['id'] = idx
    return meals


def save_meals(meals):
    with open(CSV_MEAL_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'order', 'price', 'rate', 'image'])
        writer.writeheader()
        for meal in meals:
            writer.writerow({
                'date': meal.get('date', '').strip(),
                'order': meal.get('order', '').strip(),
                'price': float(meal.get('price', 0) or 0),
                'rate': round(float(meal.get('rate', 1) or 1) * 2) / 2,
                'image': (meal.get('image') or '').strip()
            })


def get_vendor_by_index(index):
    vendors = read_vendors()
    if index < 0 or index >= len(vendors):
        return None, vendors
    return vendors[index], vendors


def get_meal_by_index(index):
    meals = read_meals()
    if index < 0 or index >= len(meals):
        return None, meals
    return meals[index], meals


@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('.', 'eat.html')


@app.route('/api/vendors', methods=['GET'])
def get_vendors():
    return jsonify(read_vendors())


@app.route('/api/vendors', methods=['POST'])
def add_vendor():
    data = request.json
    vendor_name = (data.get('vendor') or '').strip()
    weight = data.get('weight', 100)

    if not vendor_name:
        return jsonify({'error': '商家名称不能为空'}), 400

    try:
        weight_value = int(weight)
    except (TypeError, ValueError):
        return jsonify({'error': '权重必须是数字'}), 400

    if weight_value < 0:
        return jsonify({'error': '权重必须大于等于0'}), 400

    vendors = read_vendors()
    if any(v['vendor'] == vendor_name for v in vendors):
        return jsonify({'error': '该商家已存在'}), 400

    vendors.append({'vendor': vendor_name, 'weight': weight_value})
    save_vendors(vendors)

    return jsonify({'success': True, 'vendors': read_vendors()})


@app.route('/api/vendors/<int:index>', methods=['PUT'])
def update_vendor(index):
    data = request.json
    new_name = (data.get('vendor') or '').strip()
    new_weight = data.get('weight')

    vendor, vendors = get_vendor_by_index(index)
    if vendor is None:
        return jsonify({'error': '无效的索引'}), 400

    if new_name:
        if any(v['vendor'] == new_name and v is not vendor for v in vendors):
            return jsonify({'error': '该商家名称已存在'}), 400

    if new_weight is not None:
        try:
            new_weight_value = int(new_weight)
        except (TypeError, ValueError):
            return jsonify({'error': '权重必须是数字'}), 400
        if new_weight_value < 0:
            return jsonify({'error': '权重必须大于等于0'}), 400
    else:
        new_weight_value = vendor['weight']

    vendor['vendor'] = new_name or vendor['vendor']
    vendor['weight'] = new_weight_value
    save_vendors(vendors)

    return jsonify({'success': True, 'vendors': read_vendors()})


@app.route('/api/vendors/<int:index>', methods=['DELETE'])
def delete_vendor(index):
    vendor, vendors = get_vendor_by_index(index)
    if vendor is None:
        return jsonify({'error': '无效的索引'}), 400

    vendors.pop(index)
    save_vendors(vendors)

    return jsonify({'success': True, 'vendors': read_vendors()})


@app.route('/api/meals', methods=['GET'])
def get_meals():
    return jsonify(read_meals())


@app.route('/api/meals', methods=['POST'])
def add_meal():
    data = request.json
    date = (data.get('date') or '').strip()
    order = (data.get('order') or '').strip()
    price = data.get('price')
    rate = data.get('rate')
    image = '' if data.get('image') is None else str(data.get('image')).strip()

    if not date:
        return jsonify({'error': '日期不能为空'}), 400

    if not order:
        return jsonify({'error': '点餐内容不能为空'}), 400

    if price is None or price < 0:
        return jsonify({'error': '价格必须大于等于0'}), 400

    try:
        rate_value = float(rate)
    except (TypeError, ValueError):
        return jsonify({'error': '评价必须是数字'}), 400

    if rate_value < 0.5 or rate_value > 5 or abs(rate_value * 2 - round(rate_value * 2)) > 1e-6:
        return jsonify({'error': '评价必须在0.5-5之间，且以0.5为步长'}), 400

    rate_value = round(rate_value * 2) / 2

    meals = read_meals()
    meals.append({
        'date': date,
        'order': order,
        'price': float(price),
        'rate': rate_value,
        'image': image
    })
    save_meals(meals)

    return jsonify({'success': True, 'meals': read_meals()})


@app.route('/api/meals/<int:index>', methods=['PUT'])
def update_meal(index):
    data = request.json
    date = (data.get('date') or '').strip()
    order = (data.get('order') or '').strip()
    price = data.get('price')
    rate = data.get('rate')
    image = data.get('image')

    meal, meals = get_meal_by_index(index)
    if meal is None:
        return jsonify({'error': '无效的索引'}), 400

    if date:
        meal['date'] = date

    if order:
        meal['order'] = order

    if price is not None:
        if price < 0:
            return jsonify({'error': '价格必须大于等于0'}), 400
        meal['price'] = float(price)

    if rate is not None:
        try:
            rate_value = float(rate)
        except (TypeError, ValueError):
            return jsonify({'error': '评价必须是数字'}), 400

        if rate_value < 0.5 or rate_value > 5 or abs(rate_value * 2 - round(rate_value * 2)) > 1e-6:
            return jsonify({'error': '评价必须在0.5-5之间，且以0.5为步长'}), 400
        meal['rate'] = round(rate_value * 2) / 2

    if image is not None:
        meal['image'] = str(image).strip()

    save_meals(meals)
    return jsonify({'success': True, 'meals': read_meals()})


@app.route('/api/meals/<int:index>', methods=['DELETE'])
def delete_meal(index):
    meal, meals = get_meal_by_index(index)
    if meal is None:
        return jsonify({'error': '无效的索引'}), 400

    meals.pop(index)
    save_meals(meals)

    return jsonify({'success': True, 'meals': read_meals()})


@app.route('/img/<path:filename>')
def serve_image(filename):
    """Serve stored images"""
    return send_from_directory(IMG_DIR, filename)


# Ensure CSV files exist even when imported by external WSGI runners
ensure_db()

if __name__ == '__main__':
    print("Server running at http://localhost:5000")
    print("Open http://localhost:5000 in your browser")
    print("\nTo access via Cloudflare Tunnel:")
    print("1. Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/")
    print("2. Run: cloudflared tunnel --url http://localhost:5000")
    app.run(host='0.0.0.0', debug=True, port=5000)
