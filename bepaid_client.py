"""
bePaid merchant API client for Yellow Club AI Agent.

Security: Shop ID and Secret Key are passed via constructor from Settings;
they are never logged. HTTP Basic auth credentials are not persisted or printed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)

BEPAID_ERIP_ENDPOINT = "https://api.bepaid.by/beyag/transactions/payments"

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


@dataclass
class BePaidResult:
    ok: bool
    http_status: int
    data: dict = field(default_factory=dict)
    error: str = ""
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
    Client for bePaid merchant API.
    Credentials are kept only in memory and never logged.
    """

    def __init__(self, shop_id: str, secret_key: str, timeout: int = 30) -> None:
        self._shop_id = shop_id
        self._secret_key = secret_key
        self._timeout = int(timeout or 30)

    def create_erip_payment(self, payload: dict) -> BePaidResult:
        """POST a new ERIP payment invoice to bePaid."""
        return self._post(BEPAID_ERIP_ENDPOINT, payload)

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
        except requests.RequestException as exc:
            log.warning("bePaid network error url=%s type=%s", url, type(exc).__name__)
            return BePaidResult(ok=False, http_status=0, error=f"network_error:{type(exc).__name__}")

    @staticmethod
    def _parse_response(resp: requests.Response) -> BePaidResult:
        try:
            raw = resp.json()
        except Exception:
            raw = {}

        if resp.status_code in (200, 201):
            outer = raw if isinstance(raw, dict) else {}
            tx = outer.get("transaction") or {}
            if not isinstance(tx, dict):
                tx = {}
            pm = tx.get("payment_method") or {}
            if not isinstance(pm, dict):
                pm = {}
            data: dict[str, Any] = {
                "transaction_uid": tx.get("uid"),
                "status": tx.get("status"),
                "payment_method_type": pm.get("type"),
                "erip_account_number": pm.get("account_number"),
                "qr_code_raw": pm.get("qr_code_raw") or pm.get("qr"),
                "pay_url": tx.get("pay_url") or tx.get("payment_url"),
                "order_id": tx.get("order_id"),
                "tracking_id": tx.get("tracking_id"),
                "amount_minor": tx.get("amount"),
                "currency": tx.get("currency"),
                "description": tx.get("description"),
            }
            return BePaidResult(ok=True, http_status=resp.status_code, data=data)

        errors: Any = raw.get("errors") or raw.get("error") if isinstance(raw, dict) else None
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
        notification_url: str = "",
        customer_first_name: str = "",
        customer_last_name: str = "",
    ) -> dict:
        """Build the request body for bePaid /beyag/transactions/payments."""
        request: dict[str, Any] = {
            "amount": int(amount_minor),
            "currency": str(currency or "BYN"),
            "description": str(description),
            "tracking_id": str(tracking_id),
            "ip": "127.0.0.1",
            "payment_method": {
                "type": "erip",
                "account_number": str(account_number),
            },
        }
        if order_id:
            request["order_id"] = str(order_id)
        if notification_url:
            request["notification_url"] = str(notification_url)
        customer: dict[str, str] = {}
        if customer_first_name:
            customer["first_name"] = customer_first_name
        if customer_last_name:
            customer["last_name"] = customer_last_name
        if customer:
            request["customer"] = customer
        return {"request": request}

    @staticmethod
    def erip_account_number(mk_user_id: int, period_month: str) -> str:
        """
        Build ERIP account_number: {mk_user_id}{YYMM}.
        E.g. mk_user_id=8875658, period_month="2026-07" → "88756582607"
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
        return f"{mk_user_id}{yymm}"

    @staticmethod
    def erip_order_id(pi_row_id: int) -> str:
        """Build 12-digit numeric order_id from the payment_intent table row ID."""
        return f"{int(pi_row_id):012d}"


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
