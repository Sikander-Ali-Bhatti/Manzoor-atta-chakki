import os, re, sqlite3, secrets, uuid
from datetime import datetime
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, abort, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB, UP = os.path.join(BASE, 'data.db'), os.path.join(BASE, 'uploads')
os.makedirs(UP, exist_ok=True)
STATUSES = ['New', 'Confirmed', 'Preparing', 'Ready', 'Out for Delivery', 'Delivered', 'Cancelled']
PENDING = STATUSES[:5]
PHONE = re.compile(r'^\+?[0-9\- ]{10,15}$')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-me')
app.config.update(MAX_CONTENT_LENGTH=5 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Lax')

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT, description TEXT DEFAULT '',
  price INTEGER, unit TEXT DEFAULT 'kg', available INTEGER DEFAULT 1, image TEXT);
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, whatsapp TEXT,
  address TEXT, password_hash TEXT);  -- password_hash: reserved for optional accounts
CREATE TABLE IF NOT EXISTS payment_methods(id INTEGER PRIMARY KEY, name TEXT, details TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, token TEXT UNIQUE, customer_id INTEGER REFERENCES customers(id),
  name TEXT, phone TEXT, whatsapp TEXT, address TEXT, note TEXT, subtotal INTEGER, delivery_charge INTEGER,
  total INTEGER, payment_method TEXT, status TEXT DEFAULT 'New', created_at TEXT);
CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL, name TEXT, price INTEGER, qty REAL);
-- Future-ready (unused in V1): reports/inventory
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, title TEXT, amount INTEGER, spent_on TEXT);
CREATE TABLE IF NOT EXISTS stock_items(id INTEGER PRIMARY KEY, name TEXT, unit TEXT DEFAULT 'kg', low_alert REAL);
CREATE TABLE IF NOT EXISTS stock_moves(id INTEGER PRIMARY KEY, item_id INTEGER REFERENCES stock_items(id), qty REAL, kind TEXT, at TEXT);
"""
DEFAULTS = {'business_name': 'Manzoor Atta Chakki', 'owner': 'Manzoor Ali',
            'phone': '0307018889', 'whatsapp': '03130331750',
            'address': 'New Naka Link Society Road, Nawabshah, Pakistan',
            'hours': '8:30 AM to 7:00 PM', 'delivery_charge': '200',
            'description': 'Fresh flour ground locally, with home delivery in Nawabshah.',
            'logo': '', 'hero_photo': '', 'accounts_enabled': '0'}


def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop('db', None)
    if d:
        d.close()


def run(sql, a=()):
    c = db().execute(sql, a)
    db().commit()
    return c


def q(sql, a=(), one=False):
    c = db().execute(sql, a)
    return c.fetchone() if one else c.fetchall()


def n(sql, a=()):
    return db().execute(sql, a).fetchone()[0] or 0


def init_db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    for k, v in DEFAULTS.items():
        con.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (k, v))
    if not con.execute('SELECT 1 FROM products').fetchone():
        con.executemany('INSERT INTO products(name,description,price,available) VALUES(?,?,?,?)',
                        [('Wheat Flour', 'Fresh ground wheat flour (atta).', 150, 1),
                         ('Rice Flour', 'Fine rice flour.', 140, 1),
                         ('Besan', 'Gram flour.', 0, 0)])
        con.executemany('INSERT INTO payment_methods(name,details) VALUES(?,?)',
                        [('Cash on Delivery', 'Pay in cash when your order arrives.'),
                         ('JazzCash', 'Send payment to JazzCash number 0307018889 and keep the receipt.')])
    if not con.execute('SELECT 1 FROM admins').fetchone():
        user = os.environ.get('ADMIN_USER', 'admin')
        pw = os.environ.get('ADMIN_PASSWORD')
        if not pw:
            pw = secrets.token_urlsafe(9)
            print(f'\n*** First admin created. Username: {user}  Password: {pw}  (change via ADMIN_PASSWORD) ***\n')
        con.execute('INSERT INTO admins(username,password_hash) VALUES(?,?)', (user, generate_password_hash(pw)))
    con.commit()
    con.close()


def S():
    return {r['key']: r['value'] for r in q('SELECT * FROM settings')}


def csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(16)
    return session['_csrf']


@app.context_processor
def ctx():
    return dict(s=S(), csrf=csrf_token, STATUSES=STATUSES)


@app.template_filter('wa')
def wa(num):
    d = re.sub(r'\D', '', num or '')
    return 'https://wa.me/' + ('92' + d[1:] if d.startswith('0') else d)


@app.before_request
def csrf_check():
    if request.method == 'POST' and request.form.get('csrf', '') != session.get('_csrf', 'x'):
        abort(400)


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get('admin'):
            return redirect(url_for('login'))
        return f(*a, **k)
    return w


def save_image(f):
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.webp'):
        flash('Only PNG, JPG or WEBP images are allowed.')
        return None
    name = uuid.uuid4().hex + ext
    f.save(os.path.join(UP, name))
    return name


def drop_image(name):
    if name and os.path.exists(os.path.join(UP, name)):
        os.remove(os.path.join(UP, name))


@app.route('/uploads/<path:f>')
def uploads(f):
    return send_from_directory(UP, f)


# ---------------- Customer site ----------------
@app.route('/')
def home():
    return render_template('home.html', products=q('SELECT * FROM products ORDER BY id'))


@app.route('/products')
def products():
    return render_template('products.html', products=q('SELECT * FROM products ORDER BY id'))


@app.route('/<any(about,delivery,feedback,contact):p>')
def page(p):
    return render_template('page.html', p=p)


@app.route('/order', methods=['GET', 'POST'])
def order():
    prods = q('SELECT * FROM products WHERE available=1')
    pms = q('SELECT * FROM payment_methods WHERE active=1')
    fee = int(S()['delivery_charge'])
    f = request.form
    if request.method == 'POST':
        items, err = [], []
        for p in prods:
            try:
                x = float(f.get(f'qty_{p["id"]}') or 0)
            except ValueError:
                x = -1
            if x < 0 or x > 100 or x * 2 != int(x * 2):
                err.append(f'Invalid quantity for {p["name"]} (use 0.5 kg steps, max 100).')
            elif x > 0:
                items.append((p, x))
        if not items and not err:
            err.append('Please choose at least one product.')
        name, phone, wnum = f.get('name', '').strip(), f.get('phone', '').strip(), f.get('whatsapp', '').strip()
        addr, note = f.get('address', '').strip(), f.get('note', '').strip()[:300]
        pm = q('SELECT * FROM payment_methods WHERE id=? AND active=1', (f.get('payment', 0),), True)
        if len(name) < 2: err.append('Please enter your full name.')
        if not PHONE.match(phone): err.append('Please enter a valid phone number.')
        if wnum and not PHONE.match(wnum): err.append('WhatsApp number is not valid.')
        if len(addr) < 5: err.append('Please enter your delivery address.')
        if not pm: err.append('Please choose a payment method.')
        if err:
            for e in err: flash(e)
            return render_template('order.html', prods=prods, pms=pms, fee=fee, f=f)
        sub = sum(int(p['price'] * x) for p, x in items)
        c = q('SELECT id FROM customers WHERE phone=?', (phone,), True)
        if c:
            cid = c['id']
            run('UPDATE customers SET name=?,whatsapp=?,address=? WHERE id=?', (name, wnum, addr, cid))
        else:
            cid = run('INSERT INTO customers(name,phone,whatsapp,address) VALUES(?,?,?,?)',
                      (name, phone, wnum, addr)).lastrowid
        tok = secrets.token_urlsafe(12)
        oid = run('INSERT INTO orders(token,customer_id,name,phone,whatsapp,address,note,subtotal,delivery_charge,'
                  'total,payment_method,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (tok, cid, name, phone, wnum, addr, note, sub, fee, sub + fee, pm['name'],
                   datetime.now().strftime('%Y-%m-%d %H:%M'))).lastrowid
        for p, x in items:
            run('INSERT INTO order_items(order_id,product_id,name,price,qty) VALUES(?,?,?,?,?)',
                (oid, p['id'], p['name'], p['price'], x))
        return redirect(url_for('thanks', tok=tok))
    return render_template('order.html', prods=prods, pms=pms, fee=fee, f=f)


def load_order(tok=None, oid=None):
    o = q('SELECT * FROM orders WHERE token=?', (tok,), True) if tok else q('SELECT * FROM orders WHERE id=?', (oid,), True)
    if not o: abort(404)
    return o, q('SELECT * FROM order_items WHERE order_id=?', (o['id'],))


@app.route('/thanks/<tok>')
def thanks(tok):
    o, items = load_order(tok)
    pm = q('SELECT details FROM payment_methods WHERE name=?', (o['payment_method'],), True)
    return render_template('thanks.html', o=o, items=items, pm=pm)


@app.route('/receipt/<tok>')
def receipt(tok):
    o, items = load_order(tok)
    return render_template('receipt.html', o=o, items=items)


# ---------------- Admin ----------------
@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        a = q('SELECT * FROM admins WHERE username=?', (request.form.get('username', ''),), True)
        if a and check_password_hash(a['password_hash'], request.form.get('password', '')):
            session.clear()
            session['admin'] = a['id']
            return redirect(url_for('dashboard'))
        flash('Wrong username or password.')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route('/admin')
@admin_required
def dashboard():
    ph = ','.join('?' * len(PENDING))
    st = dict(new=n("SELECT COUNT(*) FROM orders WHERE status='New'"),
              pending=n(f'SELECT COUNT(*) FROM orders WHERE status IN ({ph})', PENDING),
              done=n("SELECT COUNT(*) FROM orders WHERE status='Delivered'"),
              cancelled=n("SELECT COUNT(*) FROM orders WHERE status='Cancelled'"),
              today=n("SELECT SUM(total) FROM orders WHERE status!='Cancelled' AND substr(created_at,1,10)=?",
                      (datetime.now().strftime('%Y-%m-%d'),)),
              total=n('SELECT COUNT(*) FROM orders'),
              avail=n('SELECT COUNT(*) FROM products WHERE available=1'))
    return render_template('admin_dash.html', st=st,
                           recent=q('SELECT * FROM orders ORDER BY id DESC LIMIT 5'))


@app.route('/admin/orders')
@admin_required
def orders():
    st = request.args.get('status', '')
    rows = q('SELECT * FROM orders WHERE status=? ORDER BY id DESC', (st,)) if st in STATUSES \
        else q('SELECT * FROM orders ORDER BY id DESC')
    return render_template('admin_orders.html', rows=rows, cur=st)


@app.route('/admin/orders/<int:oid>', methods=['GET', 'POST'])
@admin_required
def order_detail(oid):
    o, items = load_order(oid=oid)
    if request.method == 'POST':
        status = request.form.get('status', o['status'])
        try:
            fee = int(request.form.get('delivery_charge', o['delivery_charge']))
            assert fee >= 0
        except (ValueError, AssertionError):
            flash('Delivery charge must be a number, 0 or more.')
            return redirect(url_for('order_detail', oid=oid))
        if status not in STATUSES: abort(400)
        run('UPDATE orders SET status=?,delivery_charge=?,total=subtotal+? WHERE id=?', (status, fee, fee, oid))
        flash('Order updated.')
        return redirect(url_for('order_detail', oid=oid))
    return render_template('admin_order.html', o=o, items=items)


@app.route('/admin/products')
@admin_required
def admin_products():
    return render_template('admin_products.html', rows=q('SELECT * FROM products ORDER BY id'))


@app.route('/admin/products/new', methods=['GET', 'POST'])
@app.route('/admin/products/<int:pid>', methods=['GET', 'POST'])
@admin_required
def product_form(pid=None):
    p = q('SELECT * FROM products WHERE id=?', (pid,), True) if pid else None
    if pid and not p: abort(404)
    if request.method == 'POST':
        f = request.form
        try:
            price = int(f.get('price') or 0)
            assert price >= 0
        except (ValueError, AssertionError):
            flash('Price must be a whole number of rupees.')
            return render_template('admin_product.html', p=f)
        name = f.get('name', '').strip()
        if not name:
            flash('Product name is required.')
            return render_template('admin_product.html', p=f)
        img = p['image'] if p else None
        up = request.files.get('image')
        if f.get('rm_image') and img:
            drop_image(img); img = None
        if up and up.filename:
            new = save_image(up)
            if new: drop_image(img); img = new
        vals = (name, f.get('description', '').strip(), price, f.get('unit', 'kg').strip() or 'kg',
                1 if f.get('available') else 0, img)
        if p:
            run('UPDATE products SET name=?,description=?,price=?,unit=?,available=?,image=? WHERE id=?', vals + (pid,))
        else:
            run('INSERT INTO products(name,description,price,unit,available,image) VALUES(?,?,?,?,?,?)', vals)
        flash('Product saved.')
        return redirect(url_for('admin_products'))
    return render_template('admin_product.html', p=p or {'available': 1, 'unit': 'kg'})


@app.route('/admin/products/<int:pid>/delete', methods=['POST'])
@admin_required
def product_delete(pid):
    p = q('SELECT image FROM products WHERE id=?', (pid,), True)
    if p: drop_image(p['image'])
    run('DELETE FROM products WHERE id=?', (pid,))
    flash('Product deleted.')
    return redirect(url_for('admin_products'))


@app.route('/admin/customers')
@admin_required
def customers():
    rows = q('SELECT c.*, group_concat(o.id) AS oids, COUNT(o.id) AS cnt FROM customers c '
             'LEFT JOIN orders o ON o.customer_id=c.id GROUP BY c.id ORDER BY c.id DESC')
    return render_template('admin_customers.html', rows=rows)


@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        f, cur = request.form, S()
        try:
            fee = int(f.get('delivery_charge', 0)); assert fee >= 0
        except (ValueError, AssertionError):
            flash('Delivery charge must be a number, 0 or more.')
            return redirect(url_for('settings'))
        for k in ('business_name', 'owner', 'phone', 'whatsapp', 'address', 'hours', 'description'):
            run('UPDATE settings SET value=? WHERE key=?', (f.get(k, '').strip(), k))
        run('UPDATE settings SET value=? WHERE key=?', (str(fee), 'delivery_charge'))
        run('UPDATE settings SET value=? WHERE key=?', ('1' if f.get('accounts_enabled') else '0', 'accounts_enabled'))
        for k in ('logo', 'hero_photo'):
            if f.get('rm_' + k):
                drop_image(cur[k]); run("UPDATE settings SET value='' WHERE key=?", (k,))
            up = request.files.get(k)
            if up and up.filename:
                new = save_image(up)
                if new:
                    drop_image(cur[k]); run('UPDATE settings SET value=? WHERE key=?', (new, k))
        for m in q('SELECT id FROM payment_methods'):
            i = m['id']
            run('UPDATE payment_methods SET name=?,details=?,active=? WHERE id=?',
                (f.get(f'pm_name_{i}', '').strip(), f.get(f'pm_details_{i}', '').strip(),
                 1 if f.get(f'pm_active_{i}') else 0, i))
        if f.get('new_pm', '').strip():
            run('INSERT INTO payment_methods(name,details) VALUES(?,?)', (f['new_pm'].strip(), f.get('new_pm_details', '').strip()))
        flash('Settings saved.')
        return redirect(url_for('settings'))
    return render_template('admin_settings.html', pms=q('SELECT * FROM payment_methods ORDER BY id'))


init_db()
if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
