"""Fire N parallel drawdowns at a running server and check that the credit line
is never over-drawn, then replay one Idempotency-Key from N threads at once.

Needs a server with demo data:
    python manage.py seed_demo
    python manage.py runserver           (or: docker compose up)
    python scripts/race_demo.py --requests 50

Uses only the standard library. With SQLite the server serialises writes anyway;
run it against PostgreSQL (docker compose) to see row locks doing the work.
"""

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier


def call(base, method, path, token=None, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, dict(res.headers), json.loads(res.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), json.loads(err.read() or b"null")


def fire(n, fn):
    barrier = Barrier(n)

    def run(i):
        barrier.wait()
        start = time.perf_counter()
        result = fn(i)
        return result, (time.perf_counter() - start) * 1000

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user", default="borrower_demo")
    ap.add_argument("--password", default="Borrower@12345")
    ap.add_argument("--requests", type=int, default=50)
    args = ap.parse_args()

    _, _, tokens = call(args.base, "POST", "/api/auth/token/", body={"username": args.user, "password": args.password})
    token = tokens["access"]
    _, _, lines = call(args.base, "GET", "/api/credit-lines/", token)
    line = next(item for item in lines["results"] if item["status"] == "ACTIVE")
    path = f"/api/credit-lines/{line['id']}"

    available = Decimal(line["available_limit"])
    amount = (available / 4).quantize(Decimal("0.01"))  # exactly 4 can fit
    print(f"Line #{line['id']}: limit Rs {line['sanctioned_limit']}, available Rs {available}")
    print(f"\n1) {args.requests} parallel drawdowns of Rs {amount}, each with its own Idempotency-Key")

    results = fire(
        args.requests,
        lambda i: call(
            args.base,
            "POST",
            f"{path}/drawdowns/",
            token,
            {"amount": str(amount)},
            {"Idempotency-Key": str(uuid.uuid4())},
        )[0],
    )
    codes = [code for code, _ in results]
    latencies = sorted(ms for _, ms in results)
    _, _, after = call(args.base, "GET", f"{path}/", token)
    print(f"   accepted (201): {codes.count(201)}   refused (409 limit_exceeded): {codes.count(409)}")
    print(
        f"   outstanding now Rs {after['outstanding_principal']} <= limit Rs {after['sanctioned_limit']}: "
        f"{Decimal(after['outstanding_principal']) <= Decimal(after['sanctioned_limit'])}"
    )
    print(
        f"   latency ms  p50 {statistics.median(latencies):.0f}  p95 {latencies[int(0.95 * (len(latencies) - 1))]:.0f}"
    )

    # Free some room, then hammer one key.
    call(
        args.base, "POST", f"{path}/repayments/", token, {"amount": str(amount)}, {"Idempotency-Key": str(uuid.uuid4())}
    )
    key = str(uuid.uuid4())
    print(f"\n2) {args.requests} parallel retries of ONE drawdown (same Idempotency-Key)")
    results = fire(
        args.requests,
        lambda i: call(args.base, "POST", f"{path}/drawdowns/", token, {"amount": "1000"}, {"Idempotency-Key": key}),
    )
    ids = {body["id"] for (code, headers, body), _ in results if code == 201}
    replayed = sum(1 for (code, headers, body), _ in results if headers.get("Idempotent-Replayed") == "true")
    print(f"   distinct ledger entries created: {len(ids)}   responses replayed: {replayed}/{args.requests}")


if __name__ == "__main__":
    main()
