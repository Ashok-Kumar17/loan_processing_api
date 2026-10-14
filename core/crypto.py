"""Field-level encryption for sensitive identifiers (PAN).

* `encrypt` / `decrypt` use Fernet (AES-128-CBC + HMAC-SHA256, authenticated).
* `blind_index` is a keyed HMAC-SHA256 so a PAN can be looked up and kept unique
  without storing it in plaintext or with a reversible, unkeyed hash.
"""

import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        # Development fallback: derive a stable key from SECRET_KEY.
        digest = hashlib.sha256(("fernet:" + settings.SECRET_KEY).encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def _index_key() -> bytes:
    return hashlib.sha256(("blind-index:" + settings.SECRET_KEY).encode()).digest()


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def blind_index(value: str) -> str:
    return hmac.new(_index_key(), value.upper().encode(), hashlib.sha256).hexdigest()


def mask_pan(pan: str) -> str:
    """ABCDE1234F -> XXXXXX234F (only the last four characters are ever shown)."""
    return "X" * (len(pan) - 4) + pan[-4:]
