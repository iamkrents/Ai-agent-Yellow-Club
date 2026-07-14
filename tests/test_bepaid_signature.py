"""v7.0.92.4 — Cryptographic unit tests for bePaid webhook signature verification.

Tests 1-14 cover the fixed _bepaid_verify_signature implementation:
- Signature is Base64-encoded RSA PKCS#1 v1.5 + SHA-256 (NOT hex)
- raw body bytes are verified (not re-serialized JSON)
- PEM and Base64-encoded DER public keys are supported
- Invalid signatures, wrong keys, malformed Base64, missing signature all return 401

Requires: cryptography>=41.0.0 (in requirements.txt)

Run offline — no network, bePaid, or Telegram needed:
    python -m unittest tests.test_bepaid_signature -v
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _generate_rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return private_key, private_key.public_key()


def _sign_body(private_key, body: bytes) -> str:
    """Sign body bytes with RSA PKCS#1 v1.5 + SHA-256. Return Base64-encoded signature."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    sig_bytes = private_key.sign(body, asym_padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig_bytes).decode("ascii")


def _pub_key_pem(public_key) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def _pub_key_der_b64(public_key) -> str:
    """Serialize public key as Base64-encoded DER."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    der = public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode("ascii")


def _make_ctx(erip_pub_pem: str = "", acq_pub_pem: str = ""):
    from storage import Storage
    from web_app_server import MiniAppContext
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    storage = Storage(Path(tmp.name))
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bepaid_erip_public_key=erip_pub_pem,
        bepaid_acq_public_key=acq_pub_pem,
        bepaid_erip_shop_id="test-erip-shop",
        bepaid_acq_shop_id="test-acq-shop",
        bepaid_webhook_path_secret="",
        bepaid_auto_post_to_moyklass=False,
    )
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Key pair fixture (generated once per test module load)
# ─────────────────────────────────────────────────────────────────────────────

try:
    _ERIP_PRIV, _ERIP_PUB = _generate_rsa_keypair()
    _ACQ_PRIV, _ACQ_PUB = _generate_rsa_keypair()
    _OTHER_PRIV, _OTHER_PUB = _generate_rsa_keypair()
    _CRYPTO_AVAILABLE = True
except Exception:
    _CRYPTO_AVAILABLE = False


def _skip_if_no_crypto(test_func):
    import functools
    @functools.wraps(test_func)
    def wrapper(self, *args, **kwargs):
        if not _CRYPTO_AVAILABLE:
            self.skipTest("cryptography package not installed")
        return test_func(self, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Tests 1–14
# ─────────────────────────────────────────────────────────────────────────────

class TestBePaidSignature(unittest.TestCase):
    """14 cryptographic tests for _bepaid_verify_signature (v7.0.92.4 fix)."""

    def setUp(self):
        if not _CRYPTO_AVAILABLE:
            self.skipTest("cryptography package not installed")
        self.body = json.dumps({"transaction": {"uid": "test-1", "status": "successful"}}).encode("utf-8")
        self.erip_pem = _pub_key_pem(_ERIP_PUB)
        self.acq_pem = _pub_key_pem(_ACQ_PUB)
        self.other_pem = _pub_key_pem(_OTHER_PUB)
        self.ctx = _make_ctx(erip_pub_pem=self.erip_pem, acq_pub_pem=self.acq_pem)

    def _verify(self, body, sig_header, pub_pem):
        return self.ctx._bepaid_verify_signature(body, sig_header, pub_pem)

    # 1 — valid ERIP signature passes (Base64)
    def test_01_valid_erip_signature_passes(self):
        sig = _sign_body(_ERIP_PRIV, self.body)
        ok, reason = self._verify(self.body, sig, self.erip_pem)
        self.assertTrue(ok, f"Expected ok=True, got reason={reason}")
        self.assertEqual(reason, "verified")

    # 2 — valid acquiring signature passes with acquiring key
    def test_02_valid_acq_signature_passes(self):
        sig = _sign_body(_ACQ_PRIV, self.body)
        ok, reason = self._verify(self.body, sig, self.acq_pem)
        self.assertTrue(ok, f"Expected ok=True, got reason={reason}")

    # 3 — wrong shop key is rejected
    def test_03_wrong_shop_key_rejected(self):
        sig = _sign_body(_ERIP_PRIV, self.body)
        ok, reason = self._verify(self.body, sig, self.acq_pem)
        self.assertFalse(ok)
        self.assertIn("verify_failed", reason)

    # 4 — other unrelated key is rejected
    def test_04_other_key_rejected(self):
        sig = _sign_body(_OTHER_PRIV, self.body)
        ok, reason = self._verify(self.body, sig, self.erip_pem)
        self.assertFalse(ok)

    # 5 — modified body fails verification
    def test_05_modified_body_fails(self):
        sig = _sign_body(_ERIP_PRIV, self.body)
        tampered = self.body + b"x"
        ok, reason = self._verify(tampered, sig, self.erip_pem)
        self.assertFalse(ok)
        self.assertIn("verify_failed", reason)

    # 6 — malformed Base64 returns specific error
    def test_06_malformed_base64_rejected(self):
        ok, reason = self._verify(self.body, "!!!not-base64!!!", self.erip_pem)
        self.assertFalse(ok)
        self.assertIn("malformed_base64", reason)

    # 7 — empty signature returns missing_signature_header
    def test_07_missing_signature_rejected(self):
        ok, reason = self._verify(self.body, "", self.erip_pem)
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_signature_header")

    # 8 — HEX string is rejected as invalid Base64 / wrong signature
    def test_08_hex_string_not_accepted_as_base64_signature(self):
        sig_bytes = _ERIP_PRIV.sign(
            self.body,
            __import__("cryptography.hazmat.primitives.asymmetric.padding", fromlist=["PKCS1v15"]).PKCS1v15(),
            __import__("cryptography.hazmat.primitives.hashes", fromlist=["SHA256"]).SHA256(),
        )
        sig_hex = sig_bytes.hex()
        ok, reason = self._verify(self.body, sig_hex, self.erip_pem)
        # Hex is not valid Base64 for binary RSA signatures (too long / wrong chars)
        self.assertFalse(ok, "HEX-encoded signature must not pass as a valid Base64 signature")

    # 9 — signature verified against raw body, not json.dumps(parsed)
    def test_09_verified_against_raw_body_not_reserialized(self):
        raw = b'{"transaction": {"uid":"tx-1","status":"successful"}}'
        sig = _sign_body(_ERIP_PRIV, raw)
        # Re-serialized JSON has different byte representation (sorted keys, spacing)
        import json as _json
        reserialized = _json.dumps(_json.loads(raw.decode())).encode()
        # Verify against raw — should pass
        ok_raw, _ = self._verify(raw, sig, self.erip_pem)
        self.assertTrue(ok_raw, "Verification of raw body must pass")
        if reserialized != raw:
            ok_reser, _ = self._verify(reserialized, sig, self.erip_pem)
            self.assertFalse(ok_reser, "Verification of re-serialized body must fail (different bytes)")

    # 10 — sha256= prefix is stripped correctly
    def test_10_sha256_prefix_stripped(self):
        sig = _sign_body(_ERIP_PRIV, self.body)
        sig_with_prefix = f"sha256={sig}"
        ok, reason = self._verify(self.body, sig_with_prefix, self.erip_pem)
        self.assertTrue(ok, f"sha256= prefix must be stripped, got reason={reason}")

    # 11 — Base64-encoded DER key is supported
    def test_11_base64_der_public_key_supported(self):
        der_b64 = _pub_key_der_b64(_ERIP_PUB)
        sig = _sign_body(_ERIP_PRIV, self.body)
        ctx = _make_ctx(erip_pub_pem=der_b64)
        ok, reason = ctx._bepaid_verify_signature(self.body, sig, der_b64)
        self.assertTrue(ok, f"Base64-encoded DER key must be supported, got reason={reason}")

    # 12 — no public key configured → always passes (skip mode)
    def test_12_no_public_key_configured_passes(self):
        ok, reason = self._verify(self.body, "anysig", "")
        self.assertTrue(ok)
        self.assertEqual(reason, "no_public_key_configured")

    # 13 — invalid signature does NOT mark intent paid (integration check)
    def test_13_invalid_signature_does_not_mark_intent_paid(self):
        from storage import Storage
        from web_app_server import MiniAppContext
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        storage = Storage(Path(tmp.name))
        pi = storage.create_payment_intent({
            "mk_user_id": 9001, "amount_minor": 22900, "amount_byn": 229.0,
            "currency": "BYN", "purpose": "current_month", "payment_method": "erip",
            "status": "bepaid_created", "created_by_tg_id": 1, "created_by_name": "T",
        })
        import sqlite3
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_tracking_id=?, bepaid_uid=? WHERE public_id=?",
                (pi["public_id"], "uid-test-13", pi["public_id"]),
            )

        ctx = object.__new__(MiniAppContext)
        ctx.storage = storage
        ctx.settings = types.SimpleNamespace(
            bepaid_erip_public_key=self.erip_pem,
            bepaid_acq_public_key="",
            bepaid_erip_shop_id="test-shop",
            bepaid_acq_shop_id="",
            bepaid_webhook_path_secret="",
            bepaid_auto_post_to_moyklass=False,
        )

        body = json.dumps({"transaction": {
            "uid": "bad-uid", "status": "successful", "amount": 22900,
            "currency": "BYN", "tracking_id": pi["public_id"],
            "order": {"id": None}, "test": False, "paid_at": "2026-07-14T10:00:00Z",
        }}).encode()

        bad_sig = base64.b64encode(b"this-is-not-a-valid-signature").decode()
        resp, code = ctx.bepaid_handle_webhook(
            shop_type="erip", raw_body=body,
            content_signature=bad_sig, path_secret="",
        )
        self.assertEqual(code, 401)
        pi_after = storage.get_payment_intent(pi["public_id"])
        self.assertNotEqual(pi_after["status"], "paid")

    # 14 — valid webhook with correct signature is accepted (status=pending, not paid)
    def test_14_valid_pending_webhook_accepted_not_paid(self):
        from storage import Storage
        from web_app_server import MiniAppContext
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        storage = Storage(Path(tmp.name))
        pi = storage.create_payment_intent({
            "mk_user_id": 9002, "amount_minor": 22900, "amount_byn": 229.0,
            "currency": "BYN", "purpose": "current_month", "payment_method": "erip",
            "status": "bepaid_created", "created_by_tg_id": 1, "created_by_name": "T",
        })
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_tracking_id=?, bepaid_uid=? WHERE public_id=?",
                (pi["public_id"], "uid-test-14", pi["public_id"]),
            )

        ctx = object.__new__(MiniAppContext)
        ctx.storage = storage
        ctx.settings = types.SimpleNamespace(
            bepaid_erip_public_key=self.erip_pem,
            bepaid_acq_public_key="",
            bepaid_erip_shop_id="test-shop",
            bepaid_acq_shop_id="",
            bepaid_webhook_path_secret="",
            bepaid_auto_post_to_moyklass=False,
        )

        body = json.dumps({"transaction": {
            "uid": "uid-test-14-wh", "status": "pending", "amount": 22900,
            "currency": "BYN", "tracking_id": pi["public_id"],
            "order": {"id": None}, "test": False, "paid_at": "2026-07-14T10:00:00Z",
        }}).encode()

        valid_sig = _sign_body(_ERIP_PRIV, body)
        resp, code = ctx.bepaid_handle_webhook(
            shop_type="erip", raw_body=body,
            content_signature=valid_sig, path_secret="",
        )
        self.assertEqual(code, 200, f"Valid signature must return 200, got {code}: {resp}")
        # pending status must NOT mark paid
        pi_after = storage.get_payment_intent(pi["public_id"])
        self.assertNotEqual(pi_after["status"], "paid")


if __name__ == "__main__":
    unittest.main()
