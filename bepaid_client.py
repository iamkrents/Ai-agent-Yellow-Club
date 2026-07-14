"""
bePaid merchant API client for Yellow Club AI Agent.

Official API docs: https://docs.bepaid.by/en/payment_methods/apms/erip/create_payment/

Security: Shop ID and Secret Key are passed via constructor from Settings;
they are never logged or included in the request body.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)

# Official bePaid ERIP endpoint.
# Source: https://docs.bepaid.by/en/payment_methods/apms/erip/create_payment/
BEPAID_ERIP_ENDPOINT = "https://api.bepaid.by/beyag/payments"

# bePaid hosted checkout (acquiring/card) endpoint.
# Source: https://docs.bepaid.by/en/payment_methods/cards/hosted_checkout/
BEPAID_CHECKOUT_ENDPOINT = "https://checkout.bepaid.by/ctp/api/checkouts"

_PURPOSE_MAP: dict[str, str] = {
    "current_month": "Текущий месяц",
    "previous_month_debt": "Долг за предыдущий месяц",
    "old_debt": "Долг",
    "advance": "Аванс",
    "city_program": "Городская программа",
    "other": "Оплата",
}

_MONTH_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

# Official bePaid constraint: account_number max 30 characters.
# Warning from docs: a new request with the same account_number expires the previous invoice.
BEPAID_ACCOUNT_NUMBER_MAX_LEN = 30


@dataclass
class BePaidResult:
    ok: bool
    http_status: int
    data: dict = field(default_factory=dict)
    error: str = ""
    # True when the invoice may or may not have been created:
    # - requests.Timeout
    # - requests.ConnectionError
    # - HTTP 5xx
    requires_check: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "http_status": self.http_status,
            "data": self.data,
            "error": self.error,
            "requires_check": self.requires_check,
        }


class BePaidClient:
    """
    Client for bePaid merchant API (ERIP payments).
    Credentials are kept only in memory and never logged.

    Reference: https://docs.bepaid.by/en/payment_methods/apms/erip/create_payment/
    """

    def __init__(self, shop_id: str, secret_key: str, timeout: int = 30) -> None:
        self._shop_id = shop_id
        self._secret_key = secret_key
        self._timeout = int(timeout or 30)

    def create_erip_payment(self, payload: dict) -> BePaidResult:
        """POST a new ERIP payment invoice to bePaid."""
        return self._post(BEPAID_ERIP_ENDPOINT, payload)

    def create_acquiring_checkout(
        self,
        *,
        amount_minor: int,
        currency: str,
        description: str,
        tracking_id: str,
        notification_url: str,
        return_url: str,
        customer: dict | None = None,
        test: bool = False,
    ) -> "BePaidResult":
        """POST a new hosted checkout to bePaid acquiring (card payments).

        Requires BEPAID_ACQ_SHOP_ID / BEPAID_ACQ_SECRET_KEY credentials.
        Returns BePaidResult with data["checkout_token"] and data["payment_url"] on success.

        Reference: https://docs.bepaid.by/en/payment_methods/cards/hosted_checkout/
        """
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
            raise ValueError(
                f"amount_minor must be a positive int, got {type(amount_minor).__name__}"
            )
        if amount_minor <= 0:
            raise ValueError(f"amount_minor must be positive, got {amount_minor}")
        if str(currency).upper() != "BYN":
            raise ValueError(f"currency must be BYN, got {currency!r}")
        if not str(notification_url).startswith("https://"):
            raise ValueError(
                f"notification_url must be HTTPS, got {notification_url!r}"
            )
        if not str(return_url).startswith("https://"):
            raise ValueError(f"return_url must be HTTPS, got {return_url!r}")

        payload = BePaidClient.build_checkout_payload(
            amount_minor=amount_minor,
            currency=currency,
            description=description,
            tracking_id=tracking_id,
            notification_url=notification_url,
            return_url=return_url,
            customer=customer,
            test=test,
        )
        shop_id = self._shop_id
        log.info(
            "bepaid create_acquiring_checkout tracking_id=%s shop_id_len=%d shop_id_last4=%s",
            tracking_id,
            len(shop_id),
            shop_id[-4:] if len(shop_id) >= 4 else "****",
        )
        return self._post_checkout(BEPAID_CHECKOUT_ENDPOINT, payload)

    def _post(self, url: str, payload: dict) -> BePaidResult:
        try:
            resp = requests.post(
                url,
                json=payload,
                auth=(self._shop_id, self._secret_key),
                timeout=self._timeout,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            log.info("bePaid POST %s → HTTP %s", url, resp.status_code)
            return self._parse_response(resp)
        except requests.Timeout:
            log.warning("bePaid timeout url=%s timeout=%ss", url, self._timeout)
            return BePaidResult(ok=False, http_status=0, error="timeout", requires_check=True)
        except requests.ConnectionError as exc:
            log.warning("bePaid connection_error url=%s type=%s", url, type(exc).__name__)
            return BePaidResult(ok=False, http_status=0, error=f"connection_error:{type(exc).__name__}", requires_check=True)
        except requests.RequestException as exc:
            log.warning("bePaid network error url=%s type=%s", url, type(exc).__name__)
            return BePaidResult(ok=False, http_status=0, error=f"network_error:{type(exc).__name__}")

    def _post_checkout(self, url: str, payload: dict) -> "BePaidResult":
        """POST to bePaid hosted checkout endpoint with X-API-Version: 2 header."""
        try:
            resp = requests.post(
                url,
                json=payload,
                auth=(self._shop_id, self._secret_key),
                timeout=self._timeout,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-API-Version": "2",
                },
            )
            log.info("bePaid POST %s → HTTP %s", url, resp.status_code)
            return self._parse_checkout_response(resp)
        except requests.Timeout:
            log.warning("bePaid timeout url=%s timeout=%ss", url, self._timeout)
            return BePaidResult(ok=False, http_status=0, error="timeout", requires_check=True)
        except requests.ConnectionError as exc:
            log.warning("bePaid connection_error url=%s type=%s", url, type(exc).__name__)
            return BePaidResult(
                ok=False,
                http_status=0,
                error=f"connection_error:{type(exc).__name__}",
                requires_check=True,
            )
        except requests.RequestException as exc:
            log.warning("bePaid network error url=%s type=%s", url, type(exc).__name__)
            return BePaidResult(
                ok=False,
                http_status=0,
                error=f"network_error:{type(exc).__name__}",
            )

    @staticmethod
    def _parse_checkout_response(resp: requests.Response) -> "BePaidResult":
        """Parse bePaid hosted checkout response: {"checkout": {"token": ..., "redirect_url": ...}}."""
        try:
            raw = resp.json()
        except Exception:
            raw = {}

        if resp.status_code >= 500:
            msg = f"HTTP {resp.status_code}"
            if isinstance(raw, dict):
                errs = raw.get("errors") or raw.get("message") or raw.get("error")
                if isinstance(errs, str):
                    msg = errs[:120]
                elif isinstance(errs, dict):
                    msg = "; ".join(f"{k}: {v}" for k, v in list(errs.items())[:2])[:120]
            return BePaidResult(
                ok=False,
                http_status=resp.status_code,
                error=f"server_error:{msg}",
                requires_check=True,
            )

        if resp.status_code in (200, 201):
            outer = raw if isinstance(raw, dict) else {}
            checkout = outer.get("checkout") or {}
            if not isinstance(checkout, dict):
                checkout = {}
            token = str(checkout.get("token") or "").strip() or None
            redirect_url = str(checkout.get("redirect_url") or "").strip() or None
            if not token or not redirect_url:
                return BePaidResult(
                    ok=False,
                    http_status=resp.status_code,
                    error="missing_checkout_fields:token_or_redirect_url",
                    requires_check=False,
                )
            return BePaidResult(
                ok=True,
                http_status=resp.status_code,
                data={
                    "checkout_token": token,
                    "payment_url": redirect_url,
                    "status": str(checkout.get("status") or "").strip() or None,
                },
            )

        errors: Any = (
            raw.get("errors") or raw.get("error") or raw.get("message")
            if isinstance(raw, dict)
            else None
        )
        if isinstance(errors, list):
            msg = "; ".join(str(e)[:120] for e in errors[:3])
        elif isinstance(errors, dict):
            msg = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])
        elif isinstance(errors, str):
            msg = errors[:200]
        else:
            msg = f"HTTP {resp.status_code}"
        return BePaidResult(ok=False, http_status=resp.status_code, error=msg)

    @staticmethod
    def _parse_response(resp: requests.Response) -> BePaidResult:
        try:
            raw = resp.json()
        except Exception:
            raw = {}

        # HTTP 5xx: invoice may or may not have been created
        if resp.status_code >= 500:
            msg = f"HTTP {resp.status_code}"
            if isinstance(raw, dict):
                errs = raw.get("errors") or raw.get("message") or raw.get("error")
                if isinstance(errs, str):
                    msg = errs[:120]
                elif isinstance(errs, dict):
                    msg = "; ".join(f"{k}: {v}" for k, v in list(errs.items())[:2])[:120]
            return BePaidResult(
                ok=False, http_status=resp.status_code,
                error=f"server_error:{msg}", requires_check=True,
            )

        if resp.status_code in (200, 201):
            outer = raw if isinstance(raw, dict) else {}
            tx = outer.get("transaction") or {}
            if not isinstance(tx, dict):
                tx = {}
            # Per official docs, ERIP data lives under transaction.erip (not payment_method).
            # Source: https://docs.bepaid.by/en/payment_methods/apms/erip/create_payment/
            erip = tx.get("erip") or {}
            if not isinstance(erip, dict):
                erip = {}
            data: dict[str, Any] = {
                "transaction_uid": tx.get("uid"),
                "status": tx.get("status"),
                "payment_method_type": tx.get("payment_method_type"),
                "erip_account_number": erip.get("account_number"),
                "qr_code_raw": erip.get("qr_code_raw"),
                "qr_code": erip.get("qr_code"),
                "pay_url": tx.get("pay_url") or tx.get("payment_url"),
                "order_id": tx.get("order_id"),
                "tracking_id": tx.get("tracking_id"),
                "amount_minor": tx.get("amount"),
                "currency": tx.get("currency"),
                "description": tx.get("description"),
            }
            return BePaidResult(ok=True, http_status=resp.status_code, data=data)

        # HTTP 4xx: definitive client-side error, invoice was NOT created
        errors: Any = raw.get("errors") or raw.get("error") or raw.get("message") if isinstance(raw, dict) else None
        if isinstance(errors, list):
            msg = "; ".join(str(e)[:120] for e in errors[:3])
        elif isinstance(errors, dict):
            msg = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])
        elif isinstance(errors, str):
            msg = errors[:200]
        else:
            msg = f"HTTP {resp.status_code}"
        return BePaidResult(ok=False, http_status=resp.status_code, error=msg)

    @staticmethod
    def build_erip_payload(
        *,
        amount_minor: int,
        currency: str = "BYN",
        description: str,
        account_number: str,
        tracking_id: str,
        order_id: str,
        notification_url: str,
        customer_first_name: str = "",
        customer_last_name: str = "",
    ) -> dict:
        """Build the request body for bePaid /beyag/payments (ERIP).

        Reference: https://docs.bepaid.by/en/payment_methods/apms/erip/create_payment/
        notification_url is required for webhook delivery and must always be provided.
        """
        request: dict[str, Any] = {
            "amount": int(amount_minor),
            "currency": str(currency or "BYN"),
            "description": str(description),
            "tracking_id": str(tracking_id),
            "ip": "127.0.0.1",
            "notification_url": str(notification_url),
            "payment_method": {
                "type": "erip",
                "account_number": str(account_number),
            },
        }
        if order_id:
            request["order_id"] = str(order_id)
        customer: dict[str, str] = {}
        if customer_first_name:
            customer["first_name"] = customer_first_name
        if customer_last_name:
            customer["last_name"] = customer_last_name
        if customer:
            request["customer"] = customer
        return {"request": request}

    @staticmethod
    def build_checkout_payload(
        *,
        amount_minor: int,
        currency: str = "BYN",
        description: str,
        tracking_id: str,
        notification_url: str,
        return_url: str,
        customer: dict | None = None,
        test: bool = False,
    ) -> dict:
        """Build request body for bePaid hosted checkout (acquiring).

        Reference: https://docs.bepaid.by/en/payment_methods/cards/hosted_checkout/
        """
        checkout: dict[str, Any] = {
            "transaction_type": "payment",
            "order": {
                "amount": int(amount_minor),
                "currency": str(currency or "BYN"),
                "description": str(description),
                "tracking_id": str(tracking_id),
            },
            "settings": {
                "notification_url": str(notification_url),
                "return_url": str(return_url),
                "language": "ru",
                "auto_return": 0,
            },
            "payment_method": {
                "types": ["credit_card"],
            },
        }
        if customer and isinstance(customer, dict):
            checkout["customer"] = customer
        if test:
            checkout["test"] = True
        return {"checkout": checkout}

    @staticmethod
    def erip_account_number(mk_user_id: int, period_month: str, pi_row_id: int) -> str:
        """
        Build a globally unique ERIP account_number: {mk_user_id}{YYMM}{pi_row_id}.

        Official constraint: max 30 characters (digits only recommended).
        Warning per docs: a new invoice with the same account_number expires the previous one.
        Including pi_row_id guarantees one unique number per payment_intent.

        E.g. mk_user_id=8875658, period_month="2026-07", pi_row_id=42 → "882756582607 42"
        = "88756582607 42" → "8875658260742"

        If the combined string exceeds 30 chars (extremely large mk_user_id + pi_row_id),
        the leftmost digits of mk_user_id are trimmed to keep the rightmost unique part.
        """
        yymm = ""
        if period_month and len(period_month) >= 7:
            try:
                parts = period_month[:7].split("-")
                year = parts[0][2:]   # "2026" → "26"
                month = parts[1]      # "07"
                yymm = year + month   # "2607"
            except (IndexError, ValueError):
                pass
        candidate = f"{mk_user_id}{yymm}{pi_row_id}"
        if len(candidate) > BEPAID_ACCOUNT_NUMBER_MAX_LEN:
            # Trim leftmost chars of mk_user_id to keep the always-unique pi_row_id suffix
            suffix = f"{yymm}{pi_row_id}"
            max_mk = BEPAID_ACCOUNT_NUMBER_MAX_LEN - len(suffix)
            mk_str = str(mk_user_id)
            if max_mk < 0:
                # pi_row_id alone overflows — extremely unlikely (>26-digit row id)
                candidate = str(pi_row_id)[-BEPAID_ACCOUNT_NUMBER_MAX_LEN:]
            else:
                candidate = mk_str[-max_mk:] + suffix if max_mk > 0 else suffix
        return candidate[:BEPAID_ACCOUNT_NUMBER_MAX_LEN]

    @staticmethod
    def erip_order_id(pi_row_id: int) -> str:
        """Build a 12-digit numeric order_id that does NOT start with zero.

        bePaid rejects order_id values that begin with "0" (HTTP 422:
        "order_id should not begin with 0"). Format: "1" + zero-padded row ID
        (11 digits), e.g. pi_row_id=8 → "100000000008".

        Constraints:
        - Exactly 12 digits.
        - First digit is always "1" (never 0).
        - pi_row_id must be positive and ≤ 99_999_999_999.

        Official constraint: max 12 digits (integer or numeric string).
        Source: https://docs.bepaid.by/en/payment_methods/apms/erip/create_payment/
        """
        row_id = int(pi_row_id)
        if row_id <= 0:
            raise ValueError(f"pi_row_id must be positive, got {row_id}")
        if row_id > 99_999_999_999:
            raise ValueError(f"pi_row_id {row_id} is too large for bePaid order_id (max 99_999_999_999)")
        return f"1{row_id:011d}"


def build_erip_description(intent: dict) -> str:
    """Build a human-readable ERIP payment description from a payment intent row."""
    parts = ["Жёлтый Клуб"]
    name = str(intent.get("student_name") or "").strip()
    if name:
        parts.append(name)
    purpose = str(intent.get("purpose") or "")
    period = str(intent.get("period_month") or "")
    purpose_label = _PURPOSE_MAP.get(purpose, "оплата")
    if period and len(period) >= 7:
        try:
            year_s, month_s = period[:7].split("-")
            month_ru = _MONTH_RU[int(month_s) - 1]
            parts.append(f"{purpose_label} {month_ru} {year_s}")
        except (ValueError, IndexError):
            parts.append(f"{purpose_label} {period}" if period else purpose_label)
    elif purpose_label:
        parts.append(purpose_label)
    return " — ".join(parts)[:255]
