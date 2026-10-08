# Manzoor Atta Chakki — Version 1 (Flask + SQLite)
## Run
    pip install -r requirements.txt
    export SECRET_KEY="long-random-string" ADMIN_USER=admin ADMIN_PASSWORD="strong-password"   # Windows: set ...
    python app.py        # open http://127.0.0.1:5000  (admin: /admin/login)
If ADMIN_PASSWORD is not set, a random one is printed in the terminal on first start.
## Structure
app.py (routes, DB, auth) · templates/ (pages) · static/style.css · uploads/ (photos) · data.db (created automatically)
Reserved for later: customers.password_hash + accounts_enabled setting, expenses/stock_items/stock_moves tables.
## Test checklist
1. Home shows Wheat Flour Rs.150, Rice Flour Rs.140, Besan "Currently Unavailable".
2. /order: pick quantities, fill details, choose payment, place order -> thank-you page + receipt.
3. /admin/login -> dashboard counts update; open the order, change status/delivery charge.
4. Products: edit a price, toggle availability, upload a photo -> see it on the site.
5. Settings: change phone/hours/delivery charge, upload logo -> site and new orders reflect it.
Production: use gunicorn behind HTTPS and set SECRET_KEY.
