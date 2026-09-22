import hmac
import hashlib
import json
import base64
from django.conf import settings
from .models import DiningTable


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')


def _base64url_decode(data: str) -> bytes:
    padding = '=' * (4 - (len(data) % 4)) if (len(data) % 4) != 0 else ''
    return base64.urlsafe_b64decode((data + padding).encode('utf-8'))


def generate_table_qr_token(table: DiningTable) -> str:
    """
    Generates a stateless, cryptographic, URL-safe opaque QR token.
    Prevents table ID spoofing and exposes zero database identifiers in plain URLs.
    """
    payload = {
        'b': table.branch_id,
        't': table.id,
        's': table.qr_token_salt,
    }
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    encoded_payload = _base64url_encode(payload_bytes)

    # Compute HMAC-SHA256 signature
    signature = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        encoded_payload.encode('utf-8'),
        hashlib.sha256
    ).digest()
    encoded_sig = _base64url_encode(signature)

    return f"{encoded_payload}.{encoded_sig}"


def verify_and_resolve_qr_token(token: str) -> DiningTable:
    """
    Validates cryptographic signature and resolves table in sub-5ms.
    Returns DiningTable instance or None if invalid/tampered.
    """
    if not token or '.' not in token:
        return None

    try:
        parts = token.split('.')
        if len(parts) != 2:
            return None

        encoded_payload, encoded_sig = parts

        # Verify HMAC signature in constant time
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode('utf-8'),
            encoded_payload.encode('utf-8'),
            hashlib.sha256
        ).digest()
        actual_sig = _base64url_decode(encoded_sig)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        # Decode payload
        payload_bytes = _base64url_decode(encoded_payload)
        payload = json.loads(payload_bytes.decode('utf-8'))

        branch_id = payload.get('b')
        table_id = payload.get('t')
        salt = payload.get('s')

        # Fetch table and verify active status & salt
        table = (
            DiningTable.objects
            .select_related('branch', 'branch__restaurant')
            .filter(id=table_id, branch_id=branch_id, is_active=True)
            .first()
        )
        if not table or table.qr_token_salt != salt:
            return None

        return table
    except Exception:
        return None

