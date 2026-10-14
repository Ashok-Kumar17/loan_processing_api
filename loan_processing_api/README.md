# LAMF Credit Line API

[![CI](https://github.com/Ashok-Kumar17/loan_processing_api/actions/workflows/ci.yml/badge.svg)](https://github.com/Ashok-Kumar17/loan_processing_api/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%20|%203.12%20|%203.13-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django%205.2-092E20?logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/Django%20REST-ff1709?logo=django&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)

A backend for **loans against mutual funds (LAMF)**: a borrower pledges mutual-fund units,
gets a credit line, and draws or repays money any time while the units keep earning returns.
Built with Django REST Framework and PostgreSQL.

It covers the whole lifecycle:

**portfolio fetch from the RTAs (CAMS, KFintech) → credit-bureau check → LTV-based limit →
lien marking → drawdowns and repayments → daily interest accrual → partner (MFD/RIA) trail commission**

> I built this after my SDE internship at a loan-against-mutual-funds fintech, to rebuild the
> core lending flows from scratch and go deeper on correctness. It contains no company code,
> data or credentials. The RTA, bureau and bank integrations are deterministic mocks behind
> the same interfaces a real integration would implement.

![Partner dashboard](docs/screenshots/partner_dashboard.png)

---

## What makes it more than CRUD

| Problem in lending | How it is handled here | Proven by |
|---|---|---|
| A retried or double-clicked request must never move money twice | **Idempotency keys** on every drawdown and repayment. Same key and same body replays the stored response; same key and a different body returns 422. | `test_money_movement.py`, `test_concurrency.py` |
| Parallel drawdowns must not overdraw the line | **`SELECT … FOR UPDATE` row locks** in the service layer, plus a **DB `CHECK` constraint** `0 ≤ outstanding ≤ sanctioned_limit` as a second guard | 20 parallel requests on PostgreSQL: exactly 3 fit, 17 are refused |
| An RTA fails halfway through lien marking | **Compensation**: liens already marked in that attempt are released, and the line stays `APPROVED` | `test_lien_failure_releases_already_marked_liens` |
| Lifecycle rules (no drawdown before lien, no close with dues) | Explicit **state machine** `APPROVED → ACTIVE → CLOSED` (or `REJECTED`/`CANCELLED`). Any other transition returns 409. | `test_credit_lines.py` |
| PAN is sensitive personal data | **Field-level encryption** (Fernet, AES-128 + HMAC), a keyed **HMAC blind index** for uniqueness, masked in every response, never exposed to partners | `test_register_stores_pan_encrypted…` |
| Interest jobs get re-run | Daily accrual is **idempotent**: one interest entry per line per day, enforced by a partial unique index | `test_accrual_is_idempotent_per_day` |
| Dashboards slow down as data grows | Partner dashboard and client list aggregate **in SQL** (conditional `Sum`, `Subquery`) with a **fixed query count** whether a partner has 2 clients or 15 | `django_assert_num_queries` tests |
| Money maths | `Decimal` everywhere, rounded half-up to the paisa. Interest is settled before principal. | `test_core.py` |

**84 tests · 94% coverage** · CI runs lint, SQLite on Python 3.11/3.12/3.13, the full suite on PostgreSQL 16 (including the race tests), and a Docker build.

---

## Architecture

```
            JWT (SimpleJWT)              Swagger UI at /api/docs   Partner page at /partner
                  │
   ┌──────────────▼───────────────┐
   │ Views (DRF)                  │  thin: validate input, check role, call a service
   │  accounts · lending ·        │
   │  partners · ops              │
   └──────────────┬───────────────┘
                  │
   ┌──────────────▼───────────────┐      ┌─────────────────────────────┐
   │ lending/services.py          │─────▶│ integrations/ (interfaces)  │
   │  all business rules,         │      │  rta.py    CAMS · KFintech  │
   │  row locks, state machine,   │      │  bureau.py credit score     │
   │  ledger, accrual             │      │  payout.py bank transfer    │
   └──────────────┬───────────────┘      └─────────────────────────────┘
                  │
   ┌──────────────▼───────────────┐
   │ PostgreSQL (or SQLite in dev)│  CHECK + partial UNIQUE constraints back up
   │                              │  every invariant the code enforces
   └──────────────────────────────┘
```

**Credit-line state machine**

```
apply ──▶ REJECTED                  (bureau score < 700 or overdue accounts)
  │
  └─────▶ APPROVED ──pledge──▶ ACTIVE ──close (dues = 0)──▶ CLOSED
             │                   ▲  │
          cancel           drawdown / repayment
             ▼
         CANCELLED
```

**Lending policy** (`settings.LENDING`, all configurable): limit ₹10,000 to ₹25 crore · 10.40% APR
simple interest on the utilised amount · LTV 50% equity / 60% hybrid / 80% debt ·
minimum bureau score 700 · partner trail 0.50% p.a. on outstanding principal.

---

## Run it locally (Windows)

Needs Python 3.11 or newer. No database install is needed, because SQLite is used by default.

```powershell
git clone https://github.com/Ashok-Kumar17/loan_processing_api.git
cd loan_processing_api
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

On Linux or macOS, use `python3 -m venv venv` and `source venv/bin/activate`. The other steps are the same.

Then open:

| URL | What |
|---|---|
| http://127.0.0.1:8000/api/docs/ | Swagger UI. Every endpoint, tryable in the browser. |
| http://127.0.0.1:8000/partner/ | Partner dashboard (`partner_demo` / `Partner@12345`) |
| http://127.0.0.1:8000/admin/ | Read-only ops view (`admin` / `Admin@12345`) |

`seed_demo` creates an MFD partner with two referral links, seven referred borrowers (five active
lines and one bureau rejection) and 45 days of interest and commission history.

### Run it with Docker (PostgreSQL + gunicorn)

```bash
docker compose up --build
docker compose exec web python manage.py seed_demo
```

Same URLs as above, now backed by PostgreSQL 16, with static files served by WhiteNoise.

---

## Try the full flow in Swagger

1. `POST /api/auth/token/` with `borrower_demo` / `Borrower@12345`, then copy `access`.
2. Click **Authorize** and paste the token.
3. `GET /api/credit-lines/` shows the active line, its limit, pledges and dues.
4. `POST /api/credit-lines/{id}/drawdowns/` with `{"amount": "5000"}` and any `Idempotency-Key`
   (for example `my-first-draw`). Send it again with the same key and the response is replayed,
   so no second payout happens.
5. `POST /api/credit-lines/{id}/repayments/` settles the interest due first, then principal.
6. `GET /api/credit-lines/{id}/statement/` lists the ledger.

To start fresh, register a new borrower (`POST /api/auth/register/`, with any PAN like `ABCDE1234F`
and optionally `"referral_code": "SHARMA01"`). Then call `POST /api/portfolio/sync/`, and apply with
`POST /api/credit-lines/` using holding ids and units from the sync response. A PAN whose digits
start with 9 (for example `ABCDE9123F`) simulates a bureau rejection.

![API docs](docs/screenshots/swagger_docs.png)

---

## Tests

```bash
pytest                       # 81 tests on SQLite; the 3 race tests skip
pytest --cov=lending --cov=partners --cov=core --cov=accounts --cov=integrations
```

The race-condition tests need real row locks, so they run on PostgreSQL:

```powershell
docker compose up -d db                                   # Postgres 16 on localhost:5433
$env:DATABASE_URL="postgres://postgres:postgres@localhost:5433/lamf"
pytest                                                    # all 84 tests, including the race tests
```
(On Linux or macOS: `export DATABASE_URL=postgres://postgres:postgres@localhost:5433/lamf`.)

To check that these tests catch a real bug, remove `select_for_update()` from `_lock_line` in
`lending/services.py`. All 20 parallel drawdowns then "succeed", but the line records only one of
them (a lost update), and the tests fail.

### Live race demo against a running server

```bash
python scripts/race_demo.py --requests 50
```

Output against the Docker stack (PostgreSQL, 3 gunicorn workers):

```
Line #1: limit Rs 273000.00, available Rs 109200.00

1) 50 parallel drawdowns of Rs 27300.00, each with its own Idempotency-Key
   accepted (201): 4   refused (409 limit_exceeded): 46
   outstanding now Rs 273000.00 <= limit Rs 273000.00: True

2) 50 parallel retries of ONE drawdown (same Idempotency-Key)
   distinct ledger entries created: 1   responses replayed: 49/50
```

---

## API overview

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/register/` · `POST /api/auth/token/` · `POST /api/auth/token/refresh/` · `GET /api/auth/me/` |
| Portfolio | `POST /api/portfolio/sync/` · `GET /api/portfolio/` |
| Credit lines | `POST /api/credit-lines/` · `GET /api/credit-lines/[{id}/]` · `POST …/{id}/pledge/` · `…/cancel/` · `…/close/` |
| Money (idempotent) | `POST …/{id}/drawdowns/` · `POST …/{id}/repayments/` · `GET …/{id}/statement/` |
| Partner | `GET/POST /api/partner/referral-links/` · `PATCH …/{id}/` · `GET /api/partner/dashboard/` · `/clients/` · `/commissions/` |
| Ops | `POST /api/ops/accrue-interest/` (or `python manage.py accrue_interest --days 30`) |

Roles: **borrower** (own data only), **partner** (own referred clients, never their PAN), **ops** (read all, run accruals).
Login and sign-up are rate-limited (20 per minute).

---

## Project structure

```
accounts/        custom User (role, encrypted PAN), registration, JWT, role permissions
lending/         Holding, CreditLine, Pledge, LedgerEntry models
  services.py    every business rule: apply, lien, drawdown, repay, close, accrue
  management/    seed_demo, accrue_interest
partners/        Partner, ReferralLink, CommissionAccrual + dashboard API
core/            idempotency, audit trail, money and crypto helpers, health check
integrations/    RTA (CAMS/KFintech), credit bureau and payout adapters (mocks)
static/, templates/   partner dashboard (vanilla JS)
scripts/         race_demo.py
tests/           84 pytest tests (3 PostgreSQL-only race tests)
```

## Limitations and next steps

- The integrations are mocks. Real CAMS/KFintech lien APIs are asynchronous with OTP consent,
  so lien marking would become a background job with webhooks.
- Interest is simple daily accrual. Monthly interest debit to principal and NAV-drop margin calls
  (LTV breach, then shortfall notice, then invocation) are the natural next features.
- Accrual runs as a management command. In production it would run on a scheduler (Celery beat or cron).

---

**Ashok Kumar Meena** · Electrical Engineering, IIT Madras
