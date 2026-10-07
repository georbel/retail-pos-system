# POS System

A Django point-of-sale system with one role-aware login, an Admin Back Office, cashier checkout, branch-level inventory, purchasing, activity history, reports, and saved cashier Z Readings. Local development uses SQLite (`db.sqlite3`); production deployments should use SQLite on a persistent writable disk.

## Run locally (Windows)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. The first migration creates a Main Branch and Cash, Card, and Cheque payment methods.

Create the initial administrator with Django's password-hashed account creation command:

```powershell
python manage.py createsuperuser
```

The custom user manager assigns the Admin role automatically to superusers. Sign in at the single login page and use **Maintenance → User** to create Cashier accounts. Select the cashier's branch, choose Cashier, and set a password. The same login routes Admins to Back Office and Cashiers to Cashiering. Use Django's `createsuperuser` command only for the initial administrator; later user accounts should be managed in the application.

## Main workflows

- Add departments, units, suppliers, and products under Maintenance / File.
- Create a Delivery / Purchase to receive stock and record supplier payables.
- Admins can record bad orders and stock adjustments; each stock change creates a movement record.
- Cashiers search or scan product codes, build a cart, process a payment, and print the persisted receipt.
- Cashiers review today's totals and close their shift once with a permanent Z Reading. Completed shifts cannot accept more sales that business day.
- Back Office reports show inventory and movement, sales, payables, activity history, and prior Z Readings. Admins can void a sale before the cashier's day has been closed; stock is restored and the void is recorded.

## Deploying to Vercel

Vercel detects this Django project from `manage.py` and the WSGI entry point in `config/wsgi.py`. `vercel.json` includes the SQLite database and Django templates in the function bundle; `STATIC_ROOT` lets Vercel collect and serve static assets. Set these environment variables in **Project Settings → Environment Variables** for every deployment environment you use:

- `SECRET_KEY`: a long, private Django signing key. Generate one locally with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`; never commit it.
- `VERCEL`: Vercel normally supplies this automatically. It selects the writable SQLite location under `/tmp`.
- `ALLOWED_HOSTS`: optional comma-separated custom hostnames if the project uses a custom domain. The default permits Vercel domains.

Deploy the project root to Vercel and keep the detected Django framework/build settings. The bundled SQLite database is copied to `/tmp/pos.sqlite3` when a new instance starts; set `SQLITE_PATH` only if you need a different writable path. The packaged database must be migrated before deploying (`python manage.py migrate` locally).

**SQLite on Vercel is not durable or shared.** `/tmp` is temporary and unique to each serverless instance. Writes can disappear on cold starts/redeploys, and separate instances can each have different users, sessions, inventory, and sales. This configuration lets the app and SQLite-backed login run without the read-only filesystem error, but Vercel plus SQLite cannot safely provide a production POS with reliable persistent records. If that data must persist, use a host with a persistent writable disk for SQLite or choose a shared persistent database.

Local development uses `db.sqlite3` and the development settings when `VERCEL` is unset. For other production hosts, set a private `SECRET_KEY`, `DEBUG=False`, configure `ALLOWED_HOSTS`, and use HTTPS.
