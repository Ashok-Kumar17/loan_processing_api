"""Idempotent execution of money-moving requests.

Usage inside a view:
    return run_idempotent(request, scope="drawdown", handler=lambda: (201, body))

Guarantees:
* Same key + same payload  -> the stored response is replayed (header Idempotent-Replayed: true).
* Same key + different payload -> 422, nothing executes.
* Concurrent requests with one key -> exactly one executes; the rest wait on the
  unique index and then replay its response.
* A business-rule failure (e.g. limit exceeded) is stored too, so a retry gets the
  same answer; an unexpected 5xx rolls everything back so the client can retry.
"""

import hashlib
import json

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from .exceptions import BusinessRuleError
from .models import IdempotencyKey

HEADER = "Idempotency-Key"


def _fingerprint(request, scope: str) -> str:
    payload = json.dumps(request.data, sort_keys=True, default=str)
    return hashlib.sha256(f"{scope}|{request.path}|{payload}".encode()).hexdigest()


def run_idempotent(request, scope: str, handler) -> Response:
    key = request.headers.get(HEADER, "").strip()
    if not key or len(key) > 128:
        return Response(
            {
                "detail": f"An {HEADER} header (1-128 chars) is required for this operation.",
                "code": "idempotency_key_required",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    fingerprint = _fingerprint(request, scope)

    with transaction.atomic():
        record, created = IdempotencyKey.objects.get_or_create(
            user=request.user, key=key, defaults={"scope": scope, "fingerprint": fingerprint}
        )
        if not created:
            if record.fingerprint != fingerprint:
                return Response(
                    {
                        "detail": f"{HEADER} was already used with a different request.",
                        "code": "idempotency_key_reused",
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            response = Response(record.response_body, status=record.response_code)
            response["Idempotent-Replayed"] = "true"
            return response

        try:
            with transaction.atomic():  # savepoint: a rule violation undoes partial writes
                code, body = handler()
        except BusinessRuleError as exc:
            code, body = exc.http_status, exc.as_body()

        record.response_code = code
        record.response_body = body
        record.save(update_fields=["response_code", "response_body"])
    return Response(body, status=code)
