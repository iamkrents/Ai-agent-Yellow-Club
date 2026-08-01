"""Static-analysis tests for v7.1.8 — actual payment method display fix.

Covers: the new shared helper _getActualPaymentMethodLabel(), its use in both
the admin renderPaymentIntentCard() and the Workspace _wsRenderPaymentCard(),
the "Платёжная страница" checkout-block rename, the "Оставить счёт" safety
fix (pure UI dismiss, no backend call), and the new payment-method preview.

Static text/regex checks only — no browser, no real fetch. Consistent with
this repo's existing frontend test convention. Run offline:
    python -m unittest tests.test_payment_method_ui -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")


def _fn_body(name: str, text: str = APP_JS) -> str:
    m = re.search(rf"function {name}\([^)]*\) \{{(.*?)\n\}}\n", text, re.S)
    return m.group(1) if m else ""


class TestSharedHelper(unittest.TestCase):
    def test_14_same_helper_used_by_both_renderers(self):
        self.assertEqual(APP_JS.count("_getActualPaymentMethodLabel(pi)"), 3,
                          "helper definition + renderPaymentIntentCard + _wsRenderPaymentCard")

    def test_helper_never_uses_payment_method_field(self):
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertNotIn("payment_method", body)
        self.assertNotIn("erip_account_number", body)
        self.assertNotIn("bepaid_uid", body)
        self.assertNotIn("checkout", body.lower())


class TestUnpaidStates(unittest.TestCase):
    def test_01_unpaid_shows_not_yet_chosen(self):
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertIn('"ещё не выбран"', body)

    def test_02_unpaid_with_erip_number_still_not_chosen(self):
        # The helper only branches on status — presence of an ERIP number is
        # irrelevant to it (proven structurally: no such field referenced).
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertNotIn("account_number", body)

    def test_03_unpaid_with_checkout_still_not_chosen(self):
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertNotIn("payment_options", body)

    def test_07_withdrawn_unpaid_shows_not_yet_chosen(self):
        # Withdrawn is a client_visibility flag, not a status — the helper's
        # isPaid check is purely status-based, so a withdrawn draft/ready
        # intent (status never became "paid") still returns "ещё не выбран".
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertNotIn("client_visibility", body)
        self.assertNotIn("withdrawn", body)


class TestPaidStates(unittest.TestCase):
    def test_04_paid_card_shows_bank_card(self):
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertIn('pi.paid_channel === "acquiring"', body)
        self.assertIn('"банковская карта"', body)

    def test_05_paid_erip_shows_erip(self):
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertIn('pi.paid_channel === "erip"', body)
        self.assertIn('"ЕРИП"', body)

    def test_06_paid_unknown_shows_not_determined(self):
        body = _fn_body("_getActualPaymentMethodLabel")
        self.assertIn('"не определён"', body)

    def test_13_legacy_paid_record_unknown(self):
        # A paid intent with paid_channel absent/None falls through both
        # branches to the final "не определён" return — no guessing.
        body = _fn_body("_getActualPaymentMethodLabel")
        lines = [l.strip() for l in body.strip().splitlines()]
        self.assertTrue(lines[-1].startswith('return "не определён"'))


class TestCheckoutRename(unittest.TestCase):
    def test_08_checkout_block_named_payment_page(self):
        self.assertIn("Платёжная страница", APP_JS)
        self.assertIn("Платёжная страница: <strong>Checkout создан</strong>", APP_JS)

    def test_09_checkout_never_rendered_as_actual_card_payment(self):
        # The old misleading "Эквайринг: Checkout создан" (implying acquiring
        # IS the actual method) must be fully gone.
        self.assertNotIn("Эквайринг: <strong>Checkout создан</strong>", APP_JS)
        self.assertNotIn("<span>Эквайринг: <strong>Checkout", APP_JS)

    def test_10_erip_number_block_not_labeled_as_actual_payment(self):
        # "Номер ЕРИП: ..." (the account number shown while unpaid) must never
        # be worded as "Оплачено..."/"Фактическая оплата..." — those two
        # phrases are reserved for the paid-channel detail block.
        m = re.search(r"Номер ЕРИП: [^`]*", APP_JS)
        self.assertIsNotNone(m)


class TestPaidDetailBlock(unittest.TestCase):
    def test_11_card_payment_detail_text(self):
        self.assertIn("Фактическая оплата: банковская карта", APP_JS)

    def test_12_erip_payment_detail_text(self):
        self.assertIn("Фактическая оплата: ЕРИП", APP_JS)

    def test_unknown_paid_detail_text_no_guessing(self):
        self.assertIn("Фактический способ оплаты не сохранён", APP_JS)
        self.assertNotIn("Оплачено в bePaid", APP_JS)

    def test_no_raw_provider_codes_shown(self):
        # paid_channel raw value ("acquiring"/"erip") must never be interpolated
        # directly into user-facing text — only through the label maps/ternaries.
        self.assertNotIn("${pi.paid_channel}", APP_JS)

    def test_no_card_sensitive_data_shown(self):
        for forbidden in ("cardNumber", "cvv", "card_number", "pan_number"):
            self.assertNotIn(forbidden.lower(), APP_JS.lower())


class TestOptOutButtonSafety(unittest.TestCase):
    """'Оставить счёт' — must be a pure UI dismiss, never a backend mutation."""

    def test_dismiss_button_has_no_backend_call(self):
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', APP_JS)
        self.assertIsNotNone(m)
        onclick = m.group(1)
        self.assertNotIn("_apiPostRaw", onclick)
        self.assertNotIn("fetch(", onclick)
        self.assertIn(".remove()", onclick)

    def test_dismiss_does_not_reference_training_endpoints(self):
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', APP_JS)
        onclick = m.group(1)
        self.assertNotIn("training-resume", onclick)
        self.assertNotIn("training-check", onclick)


class TestNoLayoutRegression(unittest.TestCase):
    def test_16_all_payments_layout_unchanged(self):
        self.assertIn('class="ws-pi-row"', APP_JS)
        self.assertIn('class="ws-pi-meta"', APP_JS)

    def test_17_attention_layout_unchanged(self):
        self.assertIn('class="ws-attention-item"', APP_JS)
        # Attention cards never showed a payment-method line — confirmed still absent.
        attention_fn = _fn_body("_wsRenderAttentionItem")
        self.assertNotIn("_getActualPaymentMethodLabel", attention_fn)


class TestPreviewRemoved(unittest.TestCase):
    """v7.1.8 release cleanup: the temporary localhost-only payment-method
    preview (dev_preview=payment-method) — and the training-pause preview
    alongside it — have been visually approved and fully removed. These
    replace the old TestPreviewPaymentMethod / TestPreviewRealCapabilityContract
    classes, which asserted the previews' presence — now we assert their
    absence, that production boot()/navigation is untouched, and that the
    actual payment-method display fix they were previewing is still present
    (covered by the other test classes in this file: TestSharedHelper,
    TestUnpaidStates, TestPaidStates, etc.).
    """

    def test_20_no_preview_markers_in_index_html(self):
        for marker in (
            "dev_preview", "LOCAL PREVIEW", "TEMPORARY", "blocked_in_preview",
            "PREVIEW_ME", "PREVIEW_INTENTS", "_wsPreviewAssert", "_wsPreviewFail",
            "__YC_DEV_PREVIEW__", "Preview Client Manager", "PI-PM-1",
        ):
            self.assertNotIn(marker, INDEX_HTML, f"leftover preview marker: {marker}")

    def test_no_preview_markers_in_app_js(self):
        for marker in (
            "dev_preview", "LOCAL PREVIEW", "blocked_in_preview",
            "PREVIEW_ME", "PREVIEW_INTENTS", "_wsPreviewAssert", "_wsPreviewFail",
        ):
            self.assertNotIn(marker, APP_JS, f"leftover preview marker: {marker}")

    def test_no_inline_script_blocks_left_in_index_html(self):
        self.assertEqual(re.findall(r"<script>", INDEX_HTML), [])

    def test_production_boot_calls_load_me(self):
        m = re.search(r"async function boot\(\) \{(.*?)\nboot\(\);", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("await loadMe()", m.group(1))

    def test_cache_bust_is_current_release(self):
        self.assertIn("app.js?v=7.1.12", INDEX_HTML)
        self.assertIn('console.log("MiniApp version: v7.1.12.3")', APP_JS)


if __name__ == "__main__":
    unittest.main()
