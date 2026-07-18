# Yellow Club Agent вЂ” Current State

> РџРѕСЃР»РµРґРЅРµРµ РѕР±РЅРѕРІР»РµРЅРёРµ: 2026-07-18 (v7.0.94.6)
> Р¦РµР»СЊ С„Р°Р№Р»Р°: РїРѕР·РІРѕР»РёС‚СЊ РІРѕР·РѕР±РЅРѕРІРёС‚СЊ СЂР°Р±РѕС‚Сѓ РёР· Р»СЋР±РѕРіРѕ РЅРѕРІРѕРіРѕ С‡Р°С‚Р° Р±РµР· РїРѕС‚РµСЂРё РєРѕРЅС‚РµРєСЃС‚Р°.
> **Р­С‚РѕС‚ С„Р°Р№Р» вЂ” С‚РѕР»СЊРєРѕ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ. Production-РєРѕРґ РЅРµ РјРµРЅСЏС‚СЊ С‡РµСЂРµР· СЌС‚РѕС‚ С„Р°Р№Р».**

---

## 1. Project

**Р РµРїРѕР·РёС‚РѕСЂРёР№:** `https://github.com/iamkrents/Ai-agent-Yellow-Club` (РІРµС‚РєР° `main`)

**РЎРµСЂРІРµСЂРЅС‹Р№ РїСѓС‚СЊ:** `/home/ycagent/yellow_club_agent/`  
**РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅР° СЃРµСЂРІРµСЂРµ:** `ycagent`

**РЎРµСЂРІРёСЃС‹ (systemd):**
- `yellow-bot.service` вЂ” Telegram-Р±РѕС‚ (`bot.py`), Р·Р°РїСѓСЃРєР°РµС‚СЃСЏ РєР°Рє daemon, РѕС‚РІРµС‡Р°РµС‚ РІ Р»РёС‡РєРµ Рё РіСЂСѓРїРїР°С…
- `yellow-miniapp.service` вЂ” Mini App СЃРµСЂРІРµСЂ (`web_app_server.py`), СЃР»СѓС€Р°РµС‚ РЅР° `127.0.0.1:8088` (Р·Р° nginx reverse proxy)
- nginx вЂ” TLS-С‚РµСЂРјРёРЅР°С†РёСЏ Рё РїСЂРѕРєСЃРёСЂРѕРІР°РЅРёРµ РЅР° `localhost:8088`

**РћСЃРЅРѕРІРЅРѕР№ workflow (РµРґРёРЅСЃС‚РІРµРЅРЅС‹Р№ Р±РµР·РѕРїР°СЃРЅС‹Р№):**
```
Claude Code (Р»РѕРєР°Р»СЊРЅРѕ) в†’ СЂРµРґР°РєС‚РёСЂРѕРІР°РЅРёРµ РєРѕРґР° в†’ git commit в†’ git push
    в†’ SSH РЅР° СЃРµСЂРІРµСЂ в†’ git pull в†’ py_compile РїСЂРѕРІРµСЂРєР° в†’ restart СЃРµСЂРІРёСЃРѕРІ
```

---

## 2. Current version

| РџР°СЂР°РјРµС‚СЂ | Р—РЅР°С‡РµРЅРёРµ |
|---|---|
| РџРѕСЃР»РµРґРЅСЏСЏ Р·Р°РґРµРїР»РѕРµРЅРЅР°СЏ РІРµСЂСЃРёСЏ | **v7.0.81** (commit `db0f1e9`) вЂ” РќР• СЂР°Р·РІС‘СЂРЅСѓС‚, production-РґР°С‚Р° РЅРµРёР·РІРµСЃС‚РЅР° |
| РџРѕСЃР»РµРґРЅРёР№ РєРѕРјРјРёС‚ РІ `main` | **v7.0.94.6** вЂ” Automated MoyKlass invoice detection and payment preparation pipeline |
| Frontend cache-bust | **`v=7.0.94.2`** (app.js Рё styles.css) |
| `console.log` РІ app.js | `MiniApp version: v7.0.94.6` |

> Р’СЃРµ РІРµСЂСЃРёРё РЅР°С‡РёРЅР°СЏ СЃ v7.0.82 Р·Р°РїСѓС€РµРЅС‹, РЅРѕ **РќР• РґРµРїР»РѕРёР»РёСЃСЊ** РЅР° production-СЃРµСЂРІРµСЂ. Р”РµРїР»РѕР№ вЂ” С‚РѕР»СЊРєРѕ РїРѕ РєРѕРјР°РЅРґРµ РІР»Р°РґРµР»СЊС†Р°.

### v7.0.92.5.3 вЂ” Verify unmatched acquiring payments with bePaid

**Р—Р°РґР°С‡Р°:** Р‘РµР·РѕРїР°СЃРЅРѕРµ РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРёРµ СѓСЃРїРµС€РЅРѕР№ acquiring-РѕРїР»Р°С‚С‹ С‡РµСЂРµР· РѕС„РёС†РёР°Р»СЊРЅС‹Р№ bePaid checkout status query. Р РµС€Р°РµС‚ production transaction 156 (webhook_verified=0) Р±РµР· СЂСѓС‡РЅРѕРіРѕ SQL.

**РќРѕРІС‹Р№ trust path вЂ” provider_verified:**
- `provider_verified=1` СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ `mark_bepaid_transaction_provider_verified()` РїРѕСЃР»Рµ СѓСЃРїРµС€РЅРѕРіРѕ GET checkout status query.
- `webhook_verified` РќР• РјРµРЅСЏРµС‚СЃСЏ вЂ” СЌС‚Рѕ СЃС‚СЂРѕРіРѕ РєСЂРёРїС‚РѕРіСЂР°С„РёС‡РµСЃРєРёР№ С„Р»Р°Рі RSA-РїРѕРґРїРёСЃРё webhook.
- `list_unmatched` С‚РµРїРµСЂСЊ РїРѕРєР°Р·С‹РІР°РµС‚ С‚СЂР°РЅР·Р°РєС†РёРё СЃ `webhook_verified=1 OR provider_verified=1`.
- `bepaid_reconcile_stored_transaction` СЂР°Р·СЂРµС€Р°РµС‚ reconcile РїСЂРё `webhook_verified=1 OR provider_verified=1`.

**РќРѕРІС‹Р№ endpoint:** `POST /api/payments/intents/{id}/verify-acquiring`
- Owner/admin only.
- `checkout_token` Р±РµСЂС‘С‚СЃСЏ РўРћР›Р¬РљРћ РёР· DB (РёР· acquiring option), РЅРёРєРѕРіРґР° РёР· frontend.
- РЎС‚Р°С‚СѓСЃ-Р·Р°РїСЂРѕСЃ РґРµР»Р°РµС‚СЃСЏ СЃ ACQ credentials (РЅРёРєРѕРіРґР° ERIP).
- Р’Р°Р»РёРґРёСЂСѓСЋС‚СЃСЏ: `shop.id`, `finished`, `test=false`, `status`, `order.amount`, `order.currency`, `order.tracking_id`, `gateway_response.payment.uid`, `payment.status`, `payment.amount`, `payment.currency`.
- Р›СЋР±РѕРµ РЅРµСЃРѕРІРїР°РґРµРЅРёРµ в†’ Р±Р»РѕРє, Р±РµР· РёР·РјРµРЅРµРЅРёСЏ СЃС‚Р°С‚СѓСЃРѕРІ.
- Timeout/5xx в†’ `retry=True`, Р±РµР· РёР·РјРµРЅРµРЅРёСЏ СЃС‚Р°С‚СѓСЃРѕРІ.
- РџСЂРё СѓСЃРїРµС…Рµ: acquiring option в†’ paid, parent intent в†’ paid (`paid_channel=acquiring`), ERIP sibling в†’ superseded, transaction в†’ linked. MoyKlass РЅРµ РІС‹Р·С‹РІР°РµС‚СЃСЏ.
- РџРѕРІС‚РѕСЂРЅС‹Р№ РІС‹Р·РѕРІ в†’ idempotent=True.

**UI:** РљРЅРѕРїРєР° В«РџРѕРґС‚РІРµСЂРґРёС‚СЊ РѕРїР»Р°С‚Сѓ С‡РµСЂРµР· bePaidВ» РЅР° РєР°СЂС‚РѕС‡РєРµ intent (owner/admin, РєРѕРіРґР° РµСЃС‚СЊ acquiring option СЃ checkout_token Рё СЃС‚Р°С‚СѓСЃ РЅРµ terminal).

**РСЃРїСЂР°РІР»РµРЅРёСЏ:**
- **`bepaid_client.py`**: `get_checkout_status(payment_token)`, `_get_checkout(url)`, `_parse_checkout_status_response(resp)`.
- **`storage.py`**: РЅРѕРІС‹Рµ РєРѕР»РѕРЅРєРё `provider_verified`, `provider_verified_at`, `provider_verification_method` РІ `bepaid_transactions`; РјРµС‚РѕРґ `mark_bepaid_transaction_provider_verified()`; `list_unmatched_bepaid_transactions` вЂ” `OR provider_verified=1`.
- **`web_app_server.py`**: РјРµС‚РѕРґ `bepaid_verify_acquiring_payment()`; reconcile block РѕР±РЅРѕРІР»С‘РЅ РЅР° `not webhook_verified AND not provider_verified`; route `verify-acquiring`; РїРѕР»Рµ `provider_verified` РІ unmatched list response.
- **`miniapp/app.js`**: РІРµСЂСЃРёСЏ v7.0.92.5.3; `verifyAcquiringPayment()`; РєРЅРѕРїРєР° В«РџРѕРґС‚РІРµСЂРґРёС‚СЊ РѕРїР»Р°С‚Сѓ С‡РµСЂРµР· bePaidВ».
- **`miniapp/index.html`**: cache-bust в†’ `v=7.0.92.5.3`.
- **`tests/test_provider_verify.py`**: РЅРѕРІС‹Р№ С„Р°Р№Р», 24 С‚РµСЃС‚Р°.

**Production transaction 156:** Р’С‹Р·РѕРІ `POST /api/payments/intents/ycpi_202607_14/verify-acquiring` Р±РµР·РѕРїР°СЃРЅРѕ РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚ РѕРїР»Р°С‚Сѓ Р±РµР· СЂСѓС‡РЅРѕРіРѕ SQL Рё Р±РµР· РїРѕРІС‚РѕСЂРЅРѕР№ РѕРїР»Р°С‚С‹.

**РС‚РѕРіРѕ С‚РµСЃС‚РѕРІ: 593/593 OK (+24 РЅРѕРІС‹С…).**

### v7.0.92.5.2 вЂ” Security: restore webhook_verified as cryptographic property

**РџСЂРѕР±Р»РµРјР° (РєСЂРёС‚РёС‡РµСЃРєР°СЏ, РґРѕ production-РґРµРїР»РѕСЏ):**
Р’ v7.0.92.5.1 Р±С‹Р»Рё СЃРґРµР»Р°РЅС‹ РЅРµР±РµР·РѕРїР°СЃРЅС‹Рµ РёР·РјРµРЅРµРЅРёСЏ: `webhook_verified=1` СѓСЃС‚Р°РЅР°РІР»РёРІР°Р»СЃСЏ РЅРµРјРµРґР»РµРЅРЅРѕ РїРѕСЃР»Рµ upsert С‡РµСЂРµР· `bepaid_transaction_set_verified()`, РЅРµ Р·Р°РІРёСЃСЏ РѕС‚ РєСЂРёРїС‚РѕРіСЂР°С„РёРё; С„РёР»СЊС‚СЂ `AND webhook_verified=1` РІ `list_unmatched` Р±С‹Р» СѓРґР°Р»С‘РЅ; hard block РІ reconcile РЅР° `webhook_verified=0` Р±С‹Р» Р·Р°РјРµРЅС‘РЅ warning-РѕРј. Р­С‚Рѕ РїРѕР·РІРѕР»СЏР»Рѕ РЅРµРІРµСЂРёС„РёС†РёСЂРѕРІР°РЅРЅС‹Рј С‚СЂР°РЅР·Р°РєС†РёСЏРј РїРѕРїР°СЃС‚СЊ РІ reconcile flow.

**РџСЂР°РІРёР»СЊРЅР°СЏ Р°СЂС…РёС‚РµРєС‚СѓСЂР° (РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅР° РІ v7.0.92.5.2):**
1. `webhook_verified=1` вЂ” РўРћР›Р¬РљРћ РєСЂРёРїС‚РѕРіСЂР°С„РёС‡РµСЃРєРёР№ СЂРµР·СѓР»СЊС‚Р°С‚ РїСЂРѕРІРµСЂРєРё Content-Signature, СѓСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ Р”Рћ matching.
2. `list_unmatched` РўР Р•Р‘РЈР•Рў `webhook_verified=1` вЂ” Р±РµР· СЌС‚РѕРіРѕ С‚СЂР°РЅР·Р°РєС†РёСЏ РЅРµ РІРёРґРЅР° РІ reconcile.
3. Reconcile Р–РЃРЎРўРљРћ Р‘Р›РћРљРР РЈР•РўРЎРЇ РїСЂРё `webhook_verified!=1`, РІРѕР·РІСЂР°С‰Р°РµС‚ `{"ok": false, "reason": "webhook_not_verified"}`.
4. `bepaid_transaction_link_intent` вЂ” РќР• С‚СЂРѕРіР°РµС‚ `webhook_verified` (СѓР±СЂР°РЅРѕ РёР· UPDATE).

**РСЃРїСЂР°РІР»РµРЅРёСЏ:**
- **`storage.py`**: РїРµСЂРµРёРјРµРЅРѕРІР°РЅ `bepaid_transaction_set_verified` в†’ `mark_bepaid_transaction_signature_verified(tx_id, *, verified_at, verification_method)`. `bepaid_transaction_link_intent` вЂ” СѓРґР°Р»С‘РЅ РїР°СЂР°РјРµС‚СЂ `verified` Рё РїРѕР»Рµ `webhook_verified` РёР· UPDATE (matching РЅРµ РґРѕР»Р¶РµРЅ С‚СЂРѕРіР°С‚СЊ РєСЂРёРїС‚РѕРіСЂР°С„РёС‡РµСЃРєРёР№ С„Р»Р°Рі). `list_unmatched_bepaid_transactions` вЂ” РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅ `AND webhook_verified=1`, РґРѕР±Р°РІР»РµРЅ `AND transaction_uid IS NOT NULL`.
- **`web_app_server.py`**: `bepaid_handle_webhook` вЂ” `mark_bepaid_transaction_signature_verified()` РІС‹Р·С‹РІР°РµС‚СЃСЏ СЃСЂР°Р·Сѓ РїРѕСЃР»Рµ upsert, РµСЃР»Рё `sig_verified=True`, РџР•Р Р•Р” matching. `bepaid_reconcile_stored_transaction` вЂ” РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅ hard block: РїСЂРё `webhook_verified=0` Р»РѕРіРёСЂСѓРµС‚ `stored_transaction_reconcile_blocked`, РІРѕР·РІСЂР°С‰Р°РµС‚ `{"ok": False, "error": "reconcile_blocked", "reason": "webhook_not_verified"}`.
- **`miniapp/app.js`**: РІРµСЂСЃРёСЏ v7.0.92.5.2; reconcile РїРѕРєР°Р·С‹РІР°РµС‚ security-СЃРѕРѕР±С‰РµРЅРёРµ РїСЂРё `webhook_not_verified`.
- **`miniapp/index.html`**: cache-bust в†’ `v=7.0.92.5.2`.
- **`tests/test_option_matching.py`**: test_23 РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅ вЂ” `len=0` РґР»СЏ `webhook_verified=0`.
- **`tests/test_unmatched_transactions.py`**: РїРѕР»РЅРѕСЃС‚СЊСЋ РїРµСЂРµРїРёСЃР°РЅ РїРѕРґ strict security model (32 С‚РµСЃС‚Р°).
- **`tests/test_security_webhook.py`**: РЅРѕРІС‹Р№ С„Р°Р№Р», 19 security С‚РµСЃС‚РѕРІ.

**Production transaction 156 (webhook_verified=0 РІ DB):**
- Content-Signature РЅРµ С…СЂР°РЅРёС‚СЃСЏ РІ DB в†’ РєСЂРёРїС‚РѕРіСЂР°С„РёС‡РµСЃРєР°СЏ РїРµСЂРµ-РІРµСЂРёС„РёРєР°С†РёСЏ РЅРµРІРѕР·РјРѕР¶РЅР°.
- Р‘РµР·РѕРїР°СЃРЅРѕРµ РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРёРµ: Р·Р°РїСЂРѕСЃРёС‚СЊ Сѓ bePaid РїРѕРІС‚РѕСЂРЅС‹Р№ webhook replay РР›Р admin SQL:
  `UPDATE bepaid_transactions SET webhook_verified=1 WHERE id=156 AND transaction_uid='06006e9d-ed00-47a6-8863-07d754744424' AND status='successful' AND test=0;`
- РџРѕСЃР»Рµ РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРёСЏ вЂ” transaction 156 РїРѕСЏРІРёС‚СЃСЏ РІ В«РќРµСЃРѕРїРѕСЃС‚Р°РІР»РµРЅРЅС‹Рµ С‚СЂР°РЅР·Р°РєС†РёРёВ».

**РС‚РѕРіРѕ С‚РµСЃС‚РѕРІ: 569/569 OK (+51 РЅРѕРІС‹С… РѕС‚ v7.0.92.5.1 + security tests).**

### v7.0.92.5.1 вЂ” Fix: unmatched transaction list invisible + reconcile blocked

**РџСЂРѕР±Р»РµРјР° (production):**
- `GET /api/payments/bepaid/unmatched` РІРѕР·РІСЂР°С‰Р°Р» HTTP 200 СЃ РїСѓСЃС‚С‹Рј СЃРїРёСЃРєРѕРј вЂ” transaction 156 РЅРµ РѕС‚РѕР±СЂР°Р¶Р°Р»Р°СЃСЊ.
- РљРЅРѕРїРєР° В«РџРѕРІС‚РѕСЂРЅРѕ СЃРѕРїРѕСЃС‚Р°РІРёС‚СЊ РѕРїР»Р°С‚СѓВ» РЅРµ РїРѕСЏРІР»СЏР»Р°СЃСЊ. Reconcile РЅРµ Р·Р°РїСѓСЃРєР°Р»СЃСЏ.

**РўРѕС‡РЅС‹Рµ РїСЂРёС‡РёРЅС‹:**
1. **`webhook_verified` РЅРµ Р·Р°РїРёСЃС‹РІР°Р»СЃСЏ РїСЂРё no_match**: `bepaid_transaction_link_intent` (РµРґРёРЅСЃС‚РІРµРЅРЅРѕРµ РјРµСЃС‚Рѕ Р·Р°РїРёСЃРё `webhook_verified=1`) РІС‹Р·С‹РІР°Р»СЃСЏ С‚РѕР»СЊРєРѕ РїСЂРё СѓСЃРїРµС€РЅРѕРј СЃРѕРІРїР°РґРµРЅРёРё. РџСЂРё `no_match` С‚СЂР°РЅР·Р°РєС†РёСЏ СЃРѕС…СЂР°РЅСЏР»Р°СЃСЊ СЃ `webhook_verified=0`.
2. **Р¤РёР»СЊС‚СЂ `AND webhook_verified=1`** РІ `list_unmatched_bepaid_transactions` РёСЃРєР»СЋС‡Р°Р» РІСЃРµ no_match С‚СЂР°РЅР·Р°РєС†РёРё вЂ” РІ С‚РѕРј С‡РёСЃР»Рµ production transaction 156.
3. **Reconcile endpoint Р±Р»РѕРєРёСЂРѕРІР°Р»** РЅР° `webhook_verified=0` Р¶С‘СЃС‚РєРёРј return error.
4. **РљР»СЋС‡ API `"transactions"`** РІРјРµСЃС‚Рѕ СЃС‚Р°РЅРґР°СЂС‚РЅРѕРіРѕ `"items"` (frontend С‡РёС‚Р°Р» `data.transactions`, API РІРѕР·РІСЂР°С‰Р°Р» `"transactions"` вЂ” СЃРѕРІРїР°РґР°Р»Рё РјРµР¶РґСѓ СЃРѕР±РѕР№, РЅРѕ РЅР°СЂСѓС€Р°Р»Рё СЃС‚Р°РЅРґР°СЂС‚ API); frontend РЅРµ С‡РёС‚Р°Р» `data.items`, РЅРµ РїСЂРѕРІРµСЂСЏР» `data.ok`, РЅРµ РёСЃРїРѕР»СЊР·РѕРІР°Р» `Array.isArray`.

**РСЃРїСЂР°РІР»РµРЅРёСЏ:**
- **`storage.py`**: `bepaid_transaction_set_verified(tx_id)` вЂ” РЅРѕРІС‹Р№ РјРµС‚РѕРґ, Р·Р°РїРёСЃС‹РІР°РµС‚ `webhook_verified=1`. `list_unmatched_bepaid_transactions` вЂ” СѓРґР°Р»С‘РЅ С„РёР»СЊС‚СЂ `AND webhook_verified=1`; С‚РµРїРµСЂСЊ `status='successful' AND test=0 AND intent_public_id IS NULL/empty` вЂ” РґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РґР»СЏ eligibility. РљРѕРјРјРµРЅС‚Р°СЂРёР№ Рѕ РїСЂРёС‡РёРЅРµ РёР·РјРµРЅРµРЅРёСЏ.
- **`web_app_server.py`**: `bepaid_handle_webhook` вЂ” РЅРµРјРµРґР»РµРЅРЅРѕ РІС‹Р·С‹РІР°РµС‚ `bepaid_transaction_set_verified(tx_id)` СЃСЂР°Р·Сѓ РїРѕСЃР»Рµ upsert РїСЂРё `sig_verified=True`, РґРѕ Р»СЋР±РѕРіРѕ match/no_match РїСѓС‚Рё. `bepaid_reconcile_stored_transaction` вЂ” СѓР±СЂР°РЅ hard block РЅР° `webhook_verified=0`, Р·Р°РјРµРЅС‘РЅ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµРј РІ Р»РѕРі (`log.warning`); security gate РѕСЃС‚Р°С‘С‚СЃСЏ С‡РµСЂРµР· `status='successful'` + `test=0`. `bepaid_list_unmatched_transactions` вЂ” РєР»СЋС‡ `"transactions"` в†’ `"items"`; РґРѕР±Р°РІР»РµРЅС‹ РїРѕР»СЏ `signature_verified`, `channel`, `match_status`.
- **`miniapp/app.js`**: `loadUnmatchedTransactions` С‡РёС‚Р°РµС‚ `data.items`, РїСЂРѕРІРµСЂСЏРµС‚ `data.ok`, РёСЃРїРѕР»СЊР·СѓРµС‚ `Array.isArray(data.items)`, С‡РёС‚Р°РµС‚ `tx.signature_verified` РІРјРµСЃС‚Рѕ `tx.webhook_verified`; РІРµСЂСЃРёСЏ v7.0.92.5.1.
- **`miniapp/index.html`**: cache-bust в†’ `v=7.0.92.5.1`.
- **`tests/test_unmatched_transactions.py`**: 31 РЅРѕРІС‹Р№ С‚РµСЃС‚.

**РС‚РѕРіРѕ С‚РµСЃС‚РѕРІ: 549/549 OK (+31 РЅРѕРІС‹С…).**

**Р”Р»СЏ production transaction 156 РїРѕСЃР»Рµ deploy:** РѕС‚РєСЂС‹С‚СЊ СЂР°Р·РґРµР» РїР»Р°С‚С‘Р¶РЅС‹С… С‡РµСЂРЅРѕРІРёРєРѕРІ в†’ transaction 156 РїРѕСЏРІРёС‚СЃСЏ РІ СЃРµРєС†РёРё В«РќРµСЃРѕРїРѕСЃС‚Р°РІР»РµРЅРЅС‹Рµ С‚СЂР°РЅР·Р°РєС†РёРё bePaidВ» в†’ РЅР°Р¶Р°С‚СЊ В«РџРѕРІС‚РѕСЂРЅРѕ СЃРѕРїРѕСЃС‚Р°РІРёС‚СЊ РѕРїР»Р°С‚СѓВ» в†’ `ycpi_202607_14` РїРµСЂРµР№РґС‘С‚ РІ status=paid, paid_channel=acquiring, ERIP-РѕРїС†РёСЏв†’superseded.

### v7.0.92.5 вЂ” Fix: bePaid webhookв†’option matching + reconciliation

**РџСЂРѕР±Р»РµРјР° (production, 2026-07-14):**
- Р’РµР±С…СѓРє СЌРєРІР°Р№СЂРёРЅРіР° РґР»СЏ `ycpi_202607_14` (transaction id=156, tracking_id=`ycpi_202607_14_acq`) РІСЃРµРіРґР° РІРѕР·РІСЂР°С‰Р°Р» `no_match`. РџСЂРёС‡РёРЅР°: `match_bepaid_transaction_to_intent` РёСЃРєР°Р» С‚РѕР»СЊРєРѕ РІ С‚Р°Р±Р»РёС†Рµ `payment_intents.bepaid_tracking_id`, РЅРѕ Р·РЅР°С‡РµРЅРёРµ `ycpi_202607_14_acq` С…СЂР°РЅРёС‚СЃСЏ РІ `payment_intent_options.bepaid_tracking_id`. Intent РѕСЃС‚Р°РІР°Р»СЃСЏ РІ `awaiting_payment`, РѕРїС†РёСЏ РЅРµ РїРѕРјРµС‡Р°Р»Р°СЃСЊ РѕРїР»Р°С‡РµРЅРЅРѕР№, РєРЅРѕРїРєР° РњРѕР№РљР»Р°СЃСЃ РЅРµ РїРѕРєР°Р·С‹РІР°Р»Р°СЃСЊ.
- `payment_intent_mark_paid_via_option` РґРѕРїСѓСЃРєР°Р» С‚РѕР»СЊРєРѕ СЃС‚Р°С‚СѓСЃ `bepaid_created`, С…РѕС‚СЏ intent РЅР°С…РѕРґРёР»СЃСЏ РІ `awaiting_payment`.

**РСЃРїСЂР°РІР»РµРЅРёСЏ:**
- **`storage.py`**: РЅРѕРІС‹Р№ РјРµС‚РѕРґ `match_bepaid_transaction_to_payment_target(transaction, channel)` вЂ” СЃРЅР°С‡Р°Р»Р° РёС‰РµС‚ СЃРѕРІРїР°РґРµРЅРёРµ РІ `payment_intent_options` (channel-scoped), Р·Р°С‚РµРј fallback РЅР° legacy `match_bepaid_transaction_to_intent`. Acquiring-РІРµР±С…СѓРє РЅРёРєРѕРіРґР° РЅРµ СЃРѕРІРїР°РґР°РµС‚ СЃ ERIP-РѕРїС†РёРµР№ Рё РЅР°РѕР±РѕСЂРѕС‚. Р”РѕР±Р°РІР»РµРЅС‹ `get_bepaid_transaction_by_id(tx_id)` Рё `list_unmatched_bepaid_transactions(limit)`. `payment_intent_mark_paid_via_option` С‚РµРїРµСЂСЊ РїСЂРёРЅРёРјР°РµС‚ `bepaid_created`, `awaiting_payment`, `partial_ready` РєР°Рє РёСЃС…РѕРґРЅС‹Рµ СЃС‚Р°С‚СѓСЃС‹ (Рё РІ UPDATE WHERE IN).
- **`web_app_server.py`**: `bepaid_handle_webhook` РёСЃРїРѕР»СЊР·СѓРµС‚ `match_bepaid_transaction_to_payment_target` РІРјРµСЃС‚Рѕ legacy; РґР»СЏ `target_type=payment_option` РІС‹Р·С‹РІР°РµС‚ `payment_intent_mark_paid_via_option`, РґР»СЏ `legacy_intent` вЂ” `payment_intent_mark_paid`. РќРѕРІС‹Рµ РјРµС‚РѕРґС‹: `bepaid_list_unmatched_transactions` (`GET /api/payments/bepaid/unmatched`) Рё `bepaid_reconcile_stored_transaction` (`POST /api/payments/bepaid/transactions/{id}/reconcile`) вЂ” РїРѕРІС‚РѕСЂРЅР°СЏ РѕР±СЂР°Р±РѕС‚РєР° СЃРѕС…СЂР°РЅС‘РЅРЅРѕР№ С‚СЂР°РЅР·Р°РєС†РёРё Р±РµР· РІРЅРµС€РЅРёС… API-РІС‹Р·РѕРІРѕРІ.
- **`miniapp/app.js`**: РІРµСЂСЃРёСЏ v7.0.92.5; `bePaidPaidBlock` РїРѕРєР°Р·С‹РІР°РµС‚ `paid_channel` ("РћРїР»Р°С‡РµРЅРѕ Р±Р°РЅРєРѕРІСЃРєРѕР№ РєР°СЂС‚РѕР№ (СЌРєРІР°Р№СЂРёРЅРі)" / "РћРїР»Р°С‡РµРЅРѕ С‡РµСЂРµР· Р•Р РРџ"); `loadUnmatchedTransactions()` / `reconcileTransaction(txId)` / СЃРµРєС†РёСЏ В«РќРµСЃРѕРїРѕСЃС‚Р°РІР»РµРЅРЅС‹Рµ С‚СЂР°РЅР·Р°РєС†РёРё bePaidВ» РІ UI.
- **`miniapp/index.html`**: cache-bust в†’ `v=7.0.92.5`; div `unmatchedTxSection`.
- **`tests/test_option_matching.py`**: 31 РЅРѕРІС‹Р№ С‚РµСЃС‚ (option matching, channel scoping, mark_paid from awaiting_payment/partial_ready/bepaid_created, sibling superseding, idempotency, conflict detection, unmatched list, legacy fallback, failed intent protection).

**РС‚РѕРіРѕ С‚РµСЃС‚РѕРІ: 518/518 OK (+31 РЅРѕРІС‹С…).**

**Production safety:** РЅРµС‚ РІРЅРµС€РЅРёС… API-РІС‹Р·РѕРІРѕРІ РІ reconcile; BEPAID_AUTO_POST_TO_MOYKLASS=false РЅРµ С‚СЂРѕРЅСѓС‚; production intents 13, 14 РЅРµ С‚СЂРѕРЅСѓС‚С‹ РєРѕРґРѕРј.

### v7.0.92.4 вЂ” Fix: bePaid webhook signature (Base64) + unified prepare-options

**РџСЂРѕР±Р»РµРјР°:**
- Р’СЃРµ РІС…РѕРґСЏС‰РёРµ ERIP-РІРµР±С…СѓРєРё РѕС‚РєР»РѕРЅСЏР»РёСЃСЊ СЃ HTTP 401: `verify_failed: non-hexadecimal number found in fromhex()` вЂ” РєРѕРґ РѕР¶РёРґР°Р» hex, Р° bePaid РїСЂРёСЃС‹Р»Р°РµС‚ RSA-РїРѕРґРїРёСЃСЊ РІ Base64.
- Р’ UI РЅРµ Р±С‹Р»Рѕ РµРґРёРЅРѕР№ РєРЅРѕРїРєРё РґР»СЏ СЃРѕР·РґР°РЅРёСЏ РѕР±РѕРёС… СЃРїРѕСЃРѕР±РѕРІ РѕРїР»Р°С‚С‹ (ERIP + СЌРєРІР°Р№СЂРёРЅРі) Р·Р° РѕРґРёРЅ РєР»РёРє.
- РЎС‚Р°С‚РёСЃС‚РёРєР° Payment Intents РїРѕРєР°Р·С‹РІР°Р»Р° РґСѓР±Р»РёСЂСѓСЋС‰РёРµСЃСЏ С‡РёРїС‹ В«РћР¶РёРґР°РµС‚ РѕРїР»Р°С‚С‹В» (РёР· `bepaid_created` Рё `awaiting_payment`).

**РСЃРїСЂР°РІР»РµРЅРёСЏ:**
- **`web_app_server.py`**: `_bepaid_verify_signature` вЂ” `bytes.fromhex()` в†’ `base64.b64decode(validate=True)`; РїРѕРґРґРµСЂР¶РєР° PEM Рё Base64-DER РїСѓР±Р»РёС‡РЅС‹С… РєР»СЋС‡РµР№; РѕРїС†РёРѕРЅР°Р»СЊРЅС‹Р№ prefix `sha256=`/`sha1=` СЃРЅРёРјР°РµС‚СЃСЏ; РґРёР°РіРЅРѕСЃС‚РёРєР° РІРєР»СЋС‡Р°РµС‚ `error_class=` (РЅРµ Р·РЅР°С‡РµРЅРёРµ РєР»СЋС‡Р°). `_bypass_method_check: bool = False` РІ `payment_intent_create_bepaid`. РќРѕРІС‹Р№ РјРµС‚РѕРґ `payment_intent_prepare_options(auth, public_id)` вЂ” СЃРѕР·РґР°С‘С‚ ERIP Рё acquiring Р·Р° РѕРґРёРЅ РІС‹Р·РѕРІ, РїСЂРѕРІРµСЂСЏРµС‚ invoice РІ РњРѕР№РљР»Р°СЃСЃ, РёРґРµРјРїРѕС‚РµРЅС‚РµРЅ. РњР°СЂС€СЂСѓС‚ `POST /api/payments/intents/{id}/prepare-options`. `payment_intents_list` вЂ” РґРѕР±Р°РІР»РµРЅРѕ РїРѕР»Рµ `payment_options` Рє РєР°Р¶РґРѕРјСѓ intent (channel, status, account_number, uid, payment_url, has_checkout).
- **`miniapp/app.js`**: РєРЅРѕРїРєР° В«РџРѕРґРіРѕС‚РѕРІРёС‚СЊ СЃРїРѕСЃРѕР±С‹ РѕРїР»Р°С‚С‹В» (Р±С‹Р»Рѕ В«РџРѕРґРіРѕС‚РѕРІРёС‚СЊ С‡РµСЂРЅРѕРІРёРє bePaidВ»); `openMkInvoiceCreate` Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РІС‹Р·С‹РІР°РµС‚ `prepare-options` РїРѕСЃР»Рµ СЃРѕР·РґР°РЅРёСЏ intent; `acqReadyBadge` РІ РєР°СЂС‚РѕС‡РєРµ; РґСѓР±Р»РёСЂСѓСЋС‰РёР№СЃСЏ С‡РёРї В«РћР¶РёРґР°РµС‚ РѕРїР»Р°С‚С‹В» СѓР±СЂР°РЅ (СЃСѓРјРјР° `bepaid_created + awaiting_payment`).
- **`miniapp/index.html`**: cache-bust в†’ `v=7.0.92.4`.
- **`tests/test_bepaid_signature.py`**: 14 С‚РµСЃС‚РѕРІ (RSA PKCS#1 v1.5 + SHA-256, Base64 РґРµРєРѕРґРёРЅРі, PEM/DER РєР»СЋС‡Рё, РѕС‚РєР»РѕРЅРµРЅРёРµ hex-РїРѕРґРїРёСЃРё, РѕС‚РєР»РѕРЅРµРЅРёРµ РёР·РјРµРЅС‘РЅРЅРѕРіРѕ С‚РµР»Р°).
- **`tests/test_payment_options_flow.py`**: 10 С‚РµСЃС‚РѕРІ (prepare-options flow, idempotency, ERIP/ACQ РЅРµР·Р°РІРёСЃРёРјС‹Рµ СЃР±РѕРё, СЃС‚Р°С‚СѓСЃРЅС‹Рµ РїРµСЂРµС…РѕРґС‹, СЃС‚Р°С‚РёРєР° UI).

**РС‚РѕРіРѕ С‚РµСЃС‚РѕРІ: 487/487 OK (14 РЅРѕРІС‹С… signature + 10 РЅРѕРІС‹С… flow; 16 skipped РїРѕ РёРЅС‹Рј РїСЂРёС‡РёРЅР°Рј).**

### v7.0.92.3 вЂ” Feature: real bePaid acquiring (hosted checkout)

**РќРѕРІРѕРµ:**
- `bepaid_client.py`: СѓРґР°Р»РµРЅР° `BEPAID_ACQ_ENDPOINT_UNCONFIRMED`; РґРѕР±Р°РІР»РµРЅР° `BEPAID_CHECKOUT_ENDPOINT = "https://checkout.bepaid.by/ctp/api/checkouts"`; СЂРµР°Р»РёР·РѕРІР°РЅ `create_acquiring_checkout(*, amount_minor, currency, description, tracking_id, notification_url, return_url, customer=None, test=False)` СЃ РІР°Р»РёРґР°С†РёРµР№; `_post_checkout` СЃ Р·Р°РіРѕР»РѕРІРєРѕРј `X-API-Version: 2`; `_parse_checkout_response` РґР»СЏ `{"checkout": {...}}`; `build_checkout_payload` static method
- `storage.py`: `checkout_token TEXT` РІ `payment_intent_options` (РІ СЃС…РµРјРµ + _ensure_column); `update_option_checkout(option_id, *, checkout_token, payment_url)`; `payment_intent_update_status(public_id, new_status)`
- `web_app_server.py`: `payment_intent_create_acquiring_option(auth, public_id)` вЂ” idempotent, СЃРѕР·РґР°С‘С‚ option + checkout, РѕР±РЅРѕРІР»СЏРµС‚ СЃС‚Р°С‚СѓСЃ PI; GET `/payment-return` вЂ” СЃС‚Р°С‚РёС‡РµСЃРєР°СЏ СЃС‚СЂР°РЅРёС†Р° "РџР»Р°С‚С‘Р¶ РѕР±СЂР°Р±Р°С‚С‹РІР°РµС‚СЃСЏ"; РјР°СЂС€СЂСѓС‚ `POST /api/payments/intents/{id}/create-acquiring`
- `miniapp/app.js`: РІРµСЂСЃРёСЏ v7.0.92.3; СЃС‚Р°С‚СѓСЃС‹ `partial_ready` / `awaiting_payment`; РєРЅРѕРїРєР° "РћС‚РєСЂС‹С‚СЊ СЃС‚СЂР°РЅРёС†Сѓ РѕРїР»Р°С‚С‹ РєР°СЂС‚РѕР№" РґР»СЏ acquiring-РјРµС‚РѕРґР°; `openAcquiringCheckout()` вЂ” idempotent, РѕС‚РєСЂС‹РІР°РµС‚ URL С‡РµСЂРµР· Telegram.WebApp.openLink
- `miniapp/index.html`: cache-bust v7.0.92.3
- `tests/test_bepaid_acquiring.py`: 37 С‚РµСЃС‚РѕРІ (РІСЃРµ pass)

**РЎС‚Р°С‚СѓСЃРЅС‹Рµ РїРµСЂРµС…РѕРґС‹:**
- `draft`/`ready` + acquiring checkout в†’ `partial_ready`
- `bepaid_created` (ERIP СѓР¶Рµ СЃРѕР·РґР°РЅ) + acquiring checkout в†’ `awaiting_payment`
- Idempotency: repeat click РІРѕР·РІСЂР°С‰Р°РµС‚ existing `payment_url` Р±РµР· РЅРѕРІРѕРіРѕ API-РІС‹Р·РѕРІР°

**РњРёРіСЂР°С†РёСЏ:** С‚РѕР»СЊРєРѕ additive (`checkout_token TEXT` column via _ensure_column). РЎСѓС‰РµСЃС‚РІСѓСЋС‰РёРµ DB РЅРµ Р·Р°С‚СЂРѕРЅСѓС‚С‹.

**Cache-bust:** `app.js?v=7.0.92.3`, `styles.css?v=7.0.92.3`.

### v7.0.92.2 вЂ” Feature: dual-channel payments (ERIP + acquiring)

**РќРѕРІРѕРµ:**
- `config.py`: РґРѕР±Р°РІР»РµРЅ `moyklass_acquiring_payment_type_id` (РёР· `MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID`, default 0)
- `storage.py`: РЅРѕРІР°СЏ С‚Р°Р±Р»РёС†Р° `payment_intent_options` (РѕРґРёРЅ СЂСЏРґ РЅР° РєР°РЅР°Р»: erip/acquiring); РєРѕР»РѕРЅРєРё `paid_channel` Рё `paid_option_id` РІ `payment_intents`; 8 РЅРѕРІС‹С… РјРµС‚РѕРґРѕРІ (`create_payment_intent_option`, `get_options_for_intent`, `get_option_by_channel`, `get_option_by_provider_ref`, `mark_option_paid`, `mark_option_failed`, `mark_option_expired`, `supersede_sibling_options`, `payment_intent_mark_paid_via_option`)
- `bepaid_client.py`: (stub Р·Р°РјРµРЅС‘РЅ РІ v7.0.92.3)
- `web_app_server.py`: `_ACQ_KEYWORDS` + `_is_acquiring_candidate`; `moyklass_payment_types()` РІРѕР·РІСЂР°С‰Р°РµС‚ РѕР±Рµ РїСЂРёРІСЏР·РєРё (erip + acquiring); `payment_intent_post_to_moyklass` РІС‹Р±РёСЂР°РµС‚ `payment_type_id` РїРѕ `paid_channel` (acquiring в†’ `MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID`, erip/None в†’ `MOYKLASS_ERIP_PAYMENT_TYPE_ID`)
- `miniapp/app.js`: `_renderPaymentTypeBlock` (helper); `renderMkPaymentTypes` РїРѕРєР°Р·С‹РІР°РµС‚ РѕР±Р° РєР°РЅР°Р»Р°; РІРµСЂСЃРёСЏ в†’ v7.0.92.2
- `miniapp/styles.css`: СЃС‚РёР»Рё РґР»СЏ `.mk-pt-dual-channels`, `.mk-pt-channel-block`, `.mk-pt-badge-acq`
- `tests/test_dual_channel.py`: 50 С‚РµСЃС‚РѕРІ (РІСЃРµ pass)
- ~~**BLOCKER**~~: resolved in v7.0.92.3 вЂ” acquiring endpoint confirmed and implemented

**РњРёРіСЂР°С†РёРё (additive):** `paid_channel TEXT`, `paid_option_id INTEGER` РІ `payment_intents`; CREATE TABLE IF NOT EXISTS `payment_intent_options`. Legacy intents РЅРµ Р·Р°С‚СЂРѕРЅСѓС‚С‹.

**Cache-bust:** `app.js?v=7.0.92.2`, `styles.css?v=7.0.92.2`.

### v7.0.92.1.2 вЂ” Hotfix: ReferenceError escHtml РІ renderMkPaymentTypes

**Р‘Р°Рі:** `renderMkPaymentTypes()` РёСЃРїРѕР»СЊР·РѕРІР°Р»Р° РЅРµСЃСѓС‰РµСЃС‚РІСѓСЋС‰РёР№ alias `escHtml()` вЂ” 6 РІС‹Р·РѕРІРѕРІ. РџРѕСЃР»Рµ С‚РѕРіРѕ РєР°Рє v7.0.92.1.1 РёСЃРїСЂР°РІРёР» Р·Р°РіСЂСѓР·РєСѓ (`apiGet` СЂР°Р±РѕС‚Р°РµС‚, HTTP 200 РїСЂРёС…РѕРґРёС‚), СЂРµРЅРґРµСЂРёРЅРі РїР°РґР°Р» СЃ `ReferenceError: Can't find variable: escHtml`. UI РѕСЃС‚Р°РІР°Р»СЃСЏ РїСѓСЃС‚С‹Рј.

**РСЃРїСЂР°РІР»РµРЅРёРµ:** 6 РІС‹Р·РѕРІРѕРІ `escHtml(...)` в†’ `escapeHtml(...)` (РїСЂРѕРµРєС‚РЅС‹Р№ helper, СЃС‚СЂРѕРєР° 240 app.js). Р”СЂСѓРіРёРµ undefined aliases РѕС‚СЃСѓС‚СЃС‚РІСѓСЋС‚. Cache-bust: `app.js?v=7.0.92.1.2`.

### v7.0.92.1.1 вЂ” Hotfix: ReferenceError РІ loadMkPaymentTypes

**Р‘Р°Рі:** `loadMkPaymentTypes()` РІС‹Р·С‹РІР°Р»Р° РЅРµСЃСѓС‰РµСЃС‚РІСѓСЋС‰СѓСЋ `apiFetch()` в†’ `ReferenceError` СЃРёРЅС…СЂРѕРЅРЅРѕ, РґРѕ РѕС‚РїСЂР°РІРєРё Р·Р°РїСЂРѕСЃР°. Р¤СѓРЅРєС†РёСЏ РЅРµ Р±С‹Р»Р° `async`, РїРѕСЌС‚РѕРјСѓ `.catch()` РЅРµ РїРµСЂРµС…РІР°С‚С‹РІР°Р» СЃРёРЅС…СЂРѕРЅРЅСѓСЋ РѕС€РёР±РєСѓ. Р РµР·СѓР»СЊС‚Р°С‚: UI РЅР°РІСЃРµРіРґР° В«Р—Р°РіСЂСѓР·РєР°вЂ¦В», backend РЅРµ РІРёРґРµР» РЅРё РѕРґРЅРѕРіРѕ Р·Р°РїСЂРѕСЃР°.

**РСЃРїСЂР°РІР»РµРЅРёРµ:** `apiFetch` в†’ `apiGet` (СЃСѓС‰РµСЃС‚РІСѓРµС‚ РЅР° СЃС‚СЂРѕРєРµ ~568, РІРѕР·РІСЂР°С‰Р°РµС‚ СЂР°Р·РѕР±СЂР°РЅРЅС‹Р№ JSON). Р¤СѓРЅРєС†РёСЏ РїРµСЂРµРїРёСЃР°РЅР° РєР°Рє `async function` СЃ `try/catch/finally`. РЈР±СЂР°РЅ Р»РёС€РЅРёР№ `.then(r => r.json())`. РљРЅРѕРїРєР° В«РћР±РЅРѕРІРёС‚СЊВ» Р±Р»РѕРєРёСЂСѓРµС‚СЃСЏ РІРѕ РІСЂРµРјСЏ Р·Р°РїСЂРѕСЃР° (Р·Р°С‰РёС‚Р° РѕС‚ РґРІРѕР№РЅРѕРіРѕ РєР»РёРєР°), `finally` РІСЃРµРіРґР° СЂР°Р·Р±Р»РѕРєРёСЂСѓРµС‚.

**Cache-bust:** `app.js?v=7.0.92.1.1`. CSS РЅРµ РёР·РјРµРЅСЏР»Р°СЃСЊ вЂ” `styles.css?v=7.0.92.1`.

### v7.0.92.1 вЂ” Feature: РѕРїСЂРµРґРµР»РµРЅРёРµ С‚РёРїР° РѕРїР»Р°С‚С‹ Р•Р РРџ РІ РњРѕР№РљР»Р°СЃСЃ

**РџСЂРёС‡РёРЅР°:** Owner/admin РґРѕР»Р¶РЅС‹ СѓРјРµС‚СЊ РїСЂРѕРІРµСЂРёС‚СЊ, С‡С‚Рѕ `MOYKLASS_ERIP_PAYMENT_TYPE_ID` СѓРєР°Р·Р°РЅ РїСЂР°РІРёР»СЊРЅРѕ, Рё РЅР°Р№С‚Рё РїСЂР°РІРёР»СЊРЅС‹Р№ ID Р±РµР· РёР·РјРµРЅРµРЅРёСЏ `.env` РІСЂСѓС‡РЅСѓСЋ.

**РќРѕРІС‹Р№ endpoint:** GET `/api/payments/moyklass/payment-types` (owner/admin С‚РѕР»СЊРєРѕ). Р’РѕР·РІСЂР°С‰Р°РµС‚: РїРѕР»РЅС‹Р№ СЃРїРёСЃРѕРє С‚РёРїРѕРІ РѕРїР»Р°С‚С‹ (РЅРѕСЂРјР°Р»РёР·РѕРІР°РЅРЅС‹Р№), ERIP-РєР°РЅРґРёРґР°С‚С‹ (РїРѕ РєР»СЋС‡РµРІС‹Рј СЃР»РѕРІР°Рј), СЃС‚Р°С‚СѓСЃ РЅР°СЃС‚СЂРѕРµРЅРЅРѕРіРѕ ID, `env_hint` РїСЂРё РµРґРёРЅСЃС‚РІРµРЅРЅРѕРј РєР°РЅРґРёРґР°С‚Рµ.

**ERIP-РєР°РЅРґРёРґР°С‚С‹:** РџРѕРёСЃРє РїРѕ РєР»СЋС‡РµРІС‹Рј СЃР»РѕРІР°Рј `Р•Р РРџ/ERIP/BEPAID/Р‘Р•Р—РќРђР›РР§РќР«Р™/РћРќР›РђР™Рќ-РћРџР›РђРўРђ`. РќРёРєРѕРіРґР° РЅРµ СЃРѕС…СЂР°РЅСЏСЋС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё вЂ” admin РєРѕРїРёСЂСѓРµС‚ `env_hint` РІСЂСѓС‡РЅСѓСЋ.

**Readiness:** `payment_intent_moyklass_readiness` С‚РµРїРµСЂСЊ РґРµР»Р°РµС‚ live-РїСЂРѕРІРµСЂРєСѓ `paymentTypeId` С‡РµСЂРµР· `get_payment_type_by_id` РїРµСЂРµРґ РїСЂРѕРІРµСЂРєРѕР№ СЃС‡С‘С‚Р°.

**UI:** Р‘Р»РѕРє В«РўРёРї РѕРїР»Р°С‚С‹ РњРѕР№РљР»Р°СЃСЃВ» РІ СЂР°Р·РґРµР»Рµ payment intents. Cache-bust: `v=7.0.92.1`.

**`BEPAID_AUTO_POST_TO_MOYKLASS` РѕСЃС‚Р°Р»СЃСЏ `false` вЂ” РЅРµ С‚СЂРѕРіР°С‚СЊ.**

### v7.0.92 вЂ” Feature: СЂСѓС‡РЅРѕРµ РІРЅРµСЃРµРЅРёРµ bePaid РѕРїР»Р°С‚С‹ РІ РњРѕР№РљР»Р°СЃСЃ

**РќРѕРІС‹Р№ flow:** РџРѕСЃР»Рµ bePaid webhook (`paid`) owner/admin РІРёРґРёС‚ РєРЅРѕРїРєСѓ В«Р’РЅРµСЃС‚Рё РІ РњРѕР№РљР»Р°СЃСЃВ» РІ РєР°СЂС‚РѕС‡РєРµ intent. РќР°Р¶Р°С‚РёРµ РѕС‚РєСЂС‹РІР°РµС‚ РјРѕРґР°Р» СЃ live pre-flight РїСЂРѕРІРµСЂРєРѕР№ (GET readiness), РїРѕРєР°Р·С‹РІР°РµС‚ preview, Р¶РґС‘С‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ, Р·Р°С‚РµРј POST РІ РњРѕР№РљР»Р°СЃСЃ.

**Р—Р°С‰РёС‚С‹:** atomic claim (С‚РѕР»СЊРєРѕ РѕРґРёРЅ POST РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ), snapshot fingerprint (Р±Р»РѕРєРёСЂСѓРµС‚ РµСЃР»Рё СЃС‡С‘С‚ РёР·РјРµРЅРёР»СЃСЏ РјРµР¶РґСѓ preview Рё confirm), idempotency (РІС‚РѕСЂРѕР№ POST РІРѕР·РІСЂР°С‰Р°РµС‚ СЂРµР·СѓР»СЊС‚Р°С‚ РїРµСЂРІРѕРіРѕ), ambiguous state (timeout/5xx РїРѕСЃР»Рµ РѕС‚РїСЂР°РІРєРё в†’ Р±Р»РѕРєРёСЂСѓРµС‚ Р°РІС‚Рѕ-retry), reconciliation.

**РќРѕРІС‹Рµ env vars:** `MOYKLASS_ERIP_PAYMENT_TYPE_ID` (РѕР±СЏР·Р°С‚РµР»РµРЅ РґР»СЏ posting).

**`BEPAID_AUTO_POST_TO_MOYKLASS` РѕСЃС‚Р°Р»СЃСЏ `false` вЂ” РЅРµ С‚СЂРѕРіР°С‚СЊ.**

### v7.0.86 вЂ” Fix: month input overflow, toolbar grid, childrenReportMonth init

**Р‘Р°Рі 1 (РїРѕР»Рµ РїРµСЂРёРѕРґР° РІС‹С…РѕРґРёС‚ РІРїСЂР°РІРѕ):** РџСЂРёС‡РёРЅР° вЂ” `input[type="month"]` РёРјРµРµС‚ РЅР°С‚РёРІРЅСѓСЋ min-content С€РёСЂРёРЅСѓ (~220px РЅР° iOS), РєРѕС‚РѕСЂР°СЏ РІ flex-РєРѕРЅС‚РµР№РЅРµСЂРµ Р±РµР· `min-width: 0` РїРµСЂРµРїРѕР»РЅСЏРµС‚ СЂРѕРґРёС‚РµР»СЏ. РСЃРїСЂР°РІР»РµРЅРёРµ: РіР»РѕР±Р°Р»СЊРЅРѕРµ `input[type="month"] { min-width: 0; min-inline-size: 0; max-width: 100%; }` + С‚Рµ Р¶Рµ СЃРІРѕР№СЃС‚РІР° РІ `.pi-modal-body input` Рё `.reports-controls input`. Pseudo-element `::webkit-date-and-time-value { display: flex }` СѓР±СЂР°РЅ (РЅРµСЃС‚Р°РЅРґР°СЂС‚РЅС‹Р№).

**Р‘Р°Рі 2 (С„РёР»СЊС‚СЂС‹ РїСЂС‹РіР°СЋС‚):** `.pi-toolbar` Р±С‹Р» `flex; flex-wrap: wrap` вЂ” СЌР»РµРјРµРЅС‚С‹ РјРµРЅСЏР»Рё СЂР°Р·РјРµСЂС‹ РїСЂРё РёР·РјРµРЅРµРЅРёРё С‚РµРєСЃС‚Р° Рё РґР°РЅРЅС‹С…. РџРµСЂРµРїРёСЃР°РЅ РЅР° CSS Grid: `pi-toolbar-filters` (2 РєРѕР»РѕРЅРєРё `minmax(0,1fr)`) + `pi-toolbar-actions` (2 РєРѕР»РѕРЅРєРё `auto / 1fr`). РџРµСЂРµРјРµРЅРЅР°СЏ `--pi-control-h: 40px` РґР»СЏ РµРґРёРЅРѕР№ РІС‹СЃРѕС‚С‹. `.pi-toolbar button { min-width: 90px; white-space: nowrap; }` вЂ” РєРЅРѕРїРєРё РЅРµ РїСЂС‹РіР°СЋС‚.

**Р‘Р°Рі 3 (childrenReportMonth РїСѓСЃС‚Рѕ):** `renderChildrenReport()` РЅРµ РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°Р»Р° `#childrenReportMonth`. Р”РѕР±Р°РІР»РµРЅ `ensureMonthInputValue($("childrenReportMonth"), state.childrenReportMonth)` РІ РЅР°С‡Р°Р»Рѕ С„СѓРЅРєС†РёРё. РџРѕР»Рµ РїРѕРєР°Р·С‹РІР°РµС‚ С‚РµРєСѓС‰РёР№ РјРµСЃСЏС† РїСЂРё РїРµСЂРІРѕРј РѕС‚РєСЂС‹С‚РёРё СЂР°Р·РґРµР»Р°.

**Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅРѕ:** `ensureMonthInputValue` вЂ” РµРґРёРЅС‹Р№ helper; `piMonthFilter` РёРЅРёС†РёР°Р»РёР·РёСЂСѓРµС‚СЃСЏ РїСЂРё РѕС‚РєСЂС‹С‚РёРё Р°РєРєРѕСЂРґРµРѕРЅР°; `piPeriodMonth` РёСЃРїРѕР»СЊР·СѓРµС‚ `currentMonthValue()` (timezone-safe).

### v7.0.85 вЂ” Fix: РјРѕРґР°Р»СЊРЅС‹Рµ РѕРєРЅР° РІ РїСЂР°РІРёР»СЊРЅРѕРј РјРµСЃС‚Рµ viewport РЅР° iPhone

**РџСЂРёС‡РёРЅР° Р±Р°РіР°:** С‚СЂРё РјРѕРґР°Р»РєРё (`piCreateModal`, `piCancelModal`, `piBePaidModal`) Р±С‹Р»Рё РІР»РѕР¶РµРЅС‹ РІ section РѕС‚С‡С‘С‚РѕРІ, РєРѕС‚РѕСЂР°СЏ СЃРѕР·РґР°С‘С‚ РЅРѕРІС‹Р№ stacking context (CSS-Р°РЅРёРјР°С†РёРё `ycFadeIn` Рё `overflow`). `position: fixed` РЅР° iOS Telegram WebApp РїРѕР·РёС†РёРѕРЅРёСЂРѕРІР°Р»РѕСЃСЊ РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅРѕ Р°РЅРёРјРёСЂРѕРІР°РЅРЅРѕРіРѕ/scroll-РєРѕРЅС‚РµР№РЅРµСЂР°, Р° РЅРµ СЂРµР°Р»СЊРЅРѕРіРѕ viewport в†’ С„РѕСЂРјР° РїРѕСЏРІР»СЏР»Р°СЃСЊ РЅРёР¶Рµ СЌРєСЂР°РЅР°, Р±РѕРєРѕРІС‹Рµ РїРѕР»РѕСЃС‹ РЅР° overlay.

**РСЃРїСЂР°РІР»РµРЅРёРµ:**
- DOM portal: `<div id="piModalRoot">` вЂ” РїСЂСЏРјРѕР№ РїРѕС‚РѕРјРѕРє `<body>` (РїРµСЂРµРґ `</body>`) вЂ” РІСЃРµ 3 РјРѕРґР°Р»РєРё РїРµСЂРµРЅРµСЃРµРЅС‹ С‚СѓРґР°
- `.pi-modal` вЂ” СЃР°Рј СЏРІР»СЏРµС‚СЃСЏ С‚С‘РјРЅС‹Рј backdrop (`position: fixed; inset: 0; width: 100dvw; height: 100dvh; z-index: 10000; background: rgba(8,14,27,.62)`)
- `.pi-modal-sheet` (РїРµСЂРµРёРјРµРЅРѕРІР°РЅ СЃ `.pi-modal-box`) вЂ” Р±РµР»Р°СЏ РїР°РЅРµР»СЊ РІРЅСѓС‚СЂРё; header `flex:0 0 auto`, body `flex:1 1 auto; min-height:0; overflow-y:auto`, footer `flex:0 0 auto`
- `.pi-modal-overlay` СѓРґР°Р»С‘РЅ РёР· HTML (backdrop = СЃР°Рј `.pi-modal`)
- iOS scroll lock: `piLockPageScroll()` / `piUnlockPageScroll()` вЂ” `body.style.position="fixed"; body.style.top="-${scrollY}px"` СЃ РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРёРµРј РїРѕР·РёС†РёРё
- `piOpenModalCount` вЂ” СЃС‡С‘С‚С‡РёРє Р·Р°С‰РёС‰Р°РµС‚ РѕС‚ РїСЂРµР¶РґРµРІСЂРµРјРµРЅРЅРѕРіРѕ СЂР°Р·Р»РѕРєР° РїСЂРё РІР»РѕР¶РµРЅРЅС‹С… РІС‹Р·РѕРІР°С…
- `piModalOpen` / `piModalClose` вЂ” РїРµСЂРµР·Р°РїРёСЃР°РЅС‹; `piModalOpen` РїРµСЂРµРјРµС‰Р°РµС‚ СЌР»РµРјРµРЅС‚ РІ `#piModalRoot` РїСЂРё РЅРµРѕР±С…РѕРґРёРјРѕСЃС‚Рё
- Backdrop click: `.addEventListener("click", e => { if(e.target === el) close() })` вЂ” РЅРµ СЂРµР°РіРёСЂСѓРµС‚ РЅР° РєР»РёРє РїРѕ sheet
- `@media (prefers-reduced-motion: reduce)` вЂ” Р°РЅРёРјР°С†РёРё 1ms
- `env(safe-area-inset-bottom)` РІ footer вЂ” home indicator РЅР° iPhone
- z-index: toast 9999, modal 10000
- Cache-bust: `v=7.0.85`
- Р‘РёР·РЅРµСЃ-Р»РѕРіРёРєР°, backend, bePaid, food module, reports вЂ” РЅРµ РёР·РјРµРЅРµРЅС‹

### v7.0.84 вЂ” Р¤РёР»СЊС‚СЂР°С†РёСЏ РјРµРЅСЋ РїРёС‚Р°РЅРёСЏ РїРѕ СЂРµР±С‘РЅРєСѓ, СЃРјРµРЅРµ, С„РёР»РёР°Р»Сѓ

**РџСЂРёС‡РёРЅР° Р±Р°РіР°:** `food_active_menus()` РІРѕР·РІСЂР°С‰Р°Р» Р’РЎР• published РјРµРЅСЋ Р±РµР· С„РёР»СЊС‚СЂР°С†РёРё РїРѕ РґР°С‚Рµ Рё Р»РѕРєР°С†РёРё. Р РѕРґРёС‚РµР»СЊ Р¤РѕРјРµРЅРєРѕ Р’Р»Р°РґРёСЃР»Р°РІ (СЃРјРµРЅР° 13.07вЂ“17.07, YC1) РІРёРґРµР» РјРµРЅСЋ РѕС‚ 01.07.

**РСЃРїСЂР°РІР»РµРЅРёРµ:**
- `_get_child_week_period(child)` вЂ” РЅРѕРІР°СЏ С„СѓРЅРєС†РёСЏ РІ `storage.py`, РїР°СЂСЃРёС‚ `(DD.MM-DD.MM)` РёР· `group_name`
- `food_active_menus()` вЂ” РјРµРЅСЋ С„РёР»СЊС‚СЂСѓСЋС‚СЃСЏ РїРѕ `child_week_start..child_week_end` Рё `location_code`
- `_check_order_preconditions()` вЂ” РЅРѕРІР°СЏ РїСЂРѕРІРµСЂРєР° `menu_not_for_child` РЅР° Р±СЌРєРµРЅРґРµ
- Frontend: `eligibleChildIds`, РєРѕРЅС‚РµРєСЃС‚ СЃРјРµРЅС‹/С„РёР»РёР°Р»Р° СЂРµР±С‘РЅРєР°, РїСЂР°РІРёР»СЊРЅС‹Р№ "no menus" С‚РµРєСЃС‚
- РЎС‚Р°СЂС‹Рµ Р·Р°РєР°Р·С‹ РќР• СѓРґР°Р»СЏСЋС‚СЃСЏ

### РР·РІРµСЃС‚РЅС‹Р№ production-РёРЅС†РёРґРµРЅС‚ (v7.0.82.1 hotfix)
- **РЎРёРјРїС‚РѕРј:** bePaid HTTP 422 `order_id: ["should not begin with 0"]` РїСЂРё СЃРѕР·РґР°РЅРёРё ERIP-СЃС‡С‘С‚Р°
- **РџСЂРёС‡РёРЅР°:** `erip_order_id(pi_row_id)` РІРѕР·РІСЂР°С‰Р°Р» `f"{pi_row_id:012d}"` в†’ `"000000000008"` РґР»СЏ РјР°Р»С‹С… id
- **РСЃРїСЂР°РІР»РµРЅРёРµ:** РЅРѕРІС‹Р№ С„РѕСЂРјР°С‚ `f"1{pi_row_id:011d}"` в†’ `"100000000008"` (12 С†РёС„СЂ, РїРµСЂРІР°СЏ = 1)
- **РЎС‡С‘С‚ РїСЂРё 422 РќР• СЃРѕР·РґР°РІР°Р»СЃСЏ** вЂ” atomic claim СЃРЅРёРјР°Р»СЃСЏ, СЃС‚Р°С‚СѓСЃ С‡РµСЂРЅРѕРІРёРєР° РІРѕР·РІСЂР°С‰Р°Р»СЃСЏ РІ `draft`/`ready`
- **РџРѕСЃР»Рµ РґРµРїР»РѕСЏ:** РїРѕРІС‚РѕСЂРЅРѕРµ РЅР°Р¶Р°С‚РёРµ В«Р’С‹СЃС‚Р°РІРёС‚СЊ СЃС‡С‘С‚ bePaidВ» РЅР° С‚РѕРј Р¶Рµ С‡РµСЂРЅРѕРІРёРєРµ СЂР°Р±РѕС‚Р°РµС‚ С€С‚Р°С‚РЅРѕ
- `account_number` (`{mk_user_id}{YYMM}{pi_row_id}`) РЅРµ РёР·РјРµРЅСЏР»СЃСЏ вЂ” С„РѕСЂРјСѓР»Р° РєРѕСЂСЂРµРєС‚РЅР°

---

## 3. Important rules

### Р§С‚Рѕ РќР•Р›Р¬Р—РЇ РґРµР»Р°С‚СЊ Claude Code:
- РќР• Р·Р°РїСѓСЃРєР°С‚СЊ `bot.py`
- РќР• Р·Р°РїСѓСЃРєР°С‚СЊ `web_app_server.py`
- РќР• Р·Р°РїСѓСЃРєР°С‚СЊ Mini App
- РќР• РёР·РјРµРЅСЏС‚СЊ `.env`
- РќР• С‡РёС‚Р°С‚СЊ Рё РќР• РІС‹РІРѕРґРёС‚СЊ Р·РЅР°С‡РµРЅРёСЏ С‚РѕРєРµРЅРѕРІ Рё СЃРµРєСЂРµС‚РЅС‹С… РєР»СЋС‡РµР№
- РќР• РёР·РјРµРЅСЏС‚СЊ РёР»Рё РєРѕРїРёСЂРѕРІР°С‚СЊ `storage/messages.db`
- РќР• Р·Р°РїСѓСЃРєР°С‚СЊ reset-СЃРєСЂРёРїС‚С‹
- РќР• РѕС‡РёС‰Р°С‚СЊ РёР»Рё РїРµСЂРµСЃРѕР·РґР°РІР°С‚СЊ Р±Р°Р·Сѓ
- РќР• РїРѕРґРєР»СЋС‡Р°С‚СЊСЃСЏ Рє production-СЃРµСЂРІРµСЂСѓ
- РќР• РґРµР»Р°С‚СЊ deploy
- РќР• РґРµР»Р°С‚СЊ force push РІ `main`
- РќР• СЃРѕР·РґР°РІР°С‚СЊ bePaid payment request Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё (С‚РѕР»СЊРєРѕ СЂСѓС‡РЅРѕР№ trigger РёР· UI)
- РќР• СЃРѕР·РґР°РІР°С‚СЊ payment РІ РњРѕР№РљР»Р°СЃСЃ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё (`BEPAID_AUTO_POST_TO_MOYKLASS=false` вЂ” РЅРµ РјРµРЅСЏС‚СЊ)
- РќРµ Р»РѕРіРёСЂРѕРІР°С‚СЊ Secret Key РёР»Рё Authorization header РІ Р»РѕРіР°С…
- РќРµ РїРѕРєР°Р·С‹РІР°С‚СЊ Secret Key РІ UI
- РќРµ Р»РѕРіРёСЂРѕРІР°С‚СЊ РїРѕР»РЅС‹Р№ Shop ID (С‚РѕР»СЊРєРѕ last4 Рё length)

### Р§С‚Рѕ РјРѕР¶РЅРѕ:
- Р РµРґР°РєС‚РёСЂРѕРІР°С‚СЊ РєРѕРґ, С‚РµСЃС‚С‹, РґРѕРєСѓРјРµРЅС‚Р°С†РёСЋ
- Р—Р°РїСѓСЃРєР°С‚СЊ `python -m py_compile` Рё `python -m unittest`
- Р”РµР»Р°С‚СЊ `git commit` Рё `git push` РІ `main`

### РћР±СЏР·Р°С‚РµР»СЊРЅРѕ РїРµСЂРµРґ РґРµРїР»РѕРµРј:
```bash
cp storage/messages.db backups/messages_$(date +%Y%m%d_%H%M%S).db
```

### User-facing wording (РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ СЃРѕР±Р»СЋРґР°С‚СЊ):
- В«РіРѕСЂРѕРґСЃРєР°СЏ РїСЂРѕРіСЂР°РјРјР°В» вЂ” РќР• В«Р»Р°РіРµСЂСЊВ» (РІ UI РґР»СЏ СЂРѕРґРёС‚РµР»РµР№)
- В«СЃРјРµРЅР°В» вЂ” РќР• В«Р»Р°РіРµСЂСЊВ» (СЃРј. РёСЃРїСЂР°РІР»РµРЅРёСЏ РІ v6.9.4)

### РљРѕРјРјРёС‚ РЅРµ РґРѕР»Р¶РµРЅ РІРєР»СЋС‡Р°С‚СЊ:
- `.claude/settings.local.json`
- `.env`
- `storage/messages.db`
- `backups/`
- `logs/`
- `.venv/`

---

## 4. Food Module state

**РЎС‚Р°С‚СѓСЃ:** Backend РіРѕС‚РѕРІ, UI РІРєР»СЋС‡С‘РЅ Р·Р° С„Р»Р°РіРѕРј `FOOD_MODULE_ENABLED`.

### Р§С‚Рѕ СЂР°Р±РѕС‚Р°РµС‚:
- 6 SQLite-С‚Р°Р±Р»РёС†: `camp_children`, `parent_child_links`, `food_menus`, `food_items`, `food_orders`, `food_order_items`
- Р”РёР°РіРЅРѕСЃС‚РёРєР° РґРµС‚РµР№ Р»Р°РіРµСЂСЏ: `POST /api/food/debug/sync-camp-children`
- Admin CRUD РјРµРЅСЋ, РїСѓР±Р»РёРєР°С†РёСЏ, Р±Р»СЋРґР° РїРѕ РєР°С‚РµРіРѕСЂРёСЏРј
- Р РѕРґРёС‚РµР»СЊСЃРєРёР№ РёРЅС‚РµСЂС„РµР№СЃ: РїСЂРёРІСЏР·РєР° СЂРµР±С‘РЅРєР° РїРѕ РєРѕРґСѓ `YC-XXXX`, РїСЂРѕСЃРјРѕС‚СЂ РјРµРЅСЋ, РІС‹Р±РѕСЂ РїРёС‚Р°РЅРёСЏ
- РќР°РїРѕРјРёРЅР°РЅРёСЏ СЂРѕРґРёС‚РµР»СЏРј: `POST /api/food/menus/{id}/remind-missing` (cooldown 2 С‡)
- РЈРІРµРґРѕРјР»РµРЅРёСЏ Рѕ РїСѓР±Р»РёРєР°С†РёРё РјРµРЅСЋ (РѕРґРёРЅ СЂР°Р· РЅР° РјРµРЅСЋ per parent)
- РђРІС‚Рѕ-РЅР°РїРѕРјРёРЅР°РЅРёСЏ: `FOOD_AUTO_REMINDERS_ENABLED` (default `false`)
- OCR РјРµРЅСЋ РїРѕ С„РѕС‚Рѕ: `FOOD_MENU_OCR_ENABLED` (default `false`, С‚СЂРµР±СѓРµС‚ Tesseract+rus)
- РЎРІРѕРґРєР° Р·Р°РєР°Р·РѕРІ СЃ СЂР°Р·Р±РёРІРєРѕР№ РїРѕ Р»РѕРєР°С†РёСЏРј (YC1/YC2/YC3) Рё РєРѕРїРёСЂСѓРµРјС‹Р№ С‚РµРєСЃС‚
- РћС‚С‡С‘С‚ РїРѕ СЃС‚РѕРёРјРѕСЃС‚Рё РїРёС‚Р°РЅРёСЏ Р·Р° СЃРјРµРЅСѓ: `GET /api/food/reports/shift`

### Р РѕР»Рё:
- `admin` / `owner` / `operations` / `methodist` вЂ” РїРѕР»РЅС‹Р№ РґРѕСЃС‚СѓРї Рє РїРёС‚Р°РЅРёСЋ
- `parent` вЂ” РІРёРґРёС‚ РјРµРЅСЋ Рё РґРµР»Р°РµС‚ Р·Р°РєР°Р· С‡РµСЂРµР· СЂРѕР»СЊ, РІС‹РґР°РЅРЅСѓСЋ РїСЂРё FOOD_MODULE_ENABLED
- `intern` / `teacher` вЂ” РїРёС‚Р°РЅРёРµ РЅРµ РІРёРґСЏС‚

### Р§С‚Рѕ РІР°Р¶РЅРѕ РЅРµ СЃР»РѕРјР°С‚СЊ:
- Р”РµРґСѓРїР»РёРєР°С†РёСЏ РґРµС‚РµР№ РїРѕ `mk_student_id` (upsert)
- РђРІС‚Рѕ-РѕРїСЂРµРґРµР»РµРЅРёРµ Р°РєС‚РёРІРЅРѕР№ РЅРµРґРµР»Рё: `CAMP_ACTIVE_WEEK_MODE=auto`
- Р¤РёР»СЊС‚СЂ Р·Р°РЅСЏС‚РёР№: `CAMP_LESSON_NAME_FILTER=Yellow Summer Week` вЂ” РќР• РґРѕР±Р°РІР»СЏС‚СЊ YC1/YC2 (Р»РѕР¶РЅС‹Рµ СЃСЂР°Р±Р°С‚С‹РІР°РЅРёСЏ)
- `1 Р±Р»СЋРґРѕ РЅР° РєР°С‚РµРіРѕСЂРёСЋ` вЂ” Р»РѕРіРёРєР° РґРµРґСѓРїР»РёРєР°С†РёРё РІ submit_order
- РЎРІРѕРґРєР° РіСЂСѓРїРїРёСЂСѓРµС‚СЃСЏ РїРѕ `group_name`/`mk_class_name` РґР»СЏ РѕРїСЂРµРґРµР»РµРЅРёСЏ Р»РѕРєР°С†РёРё

---

## 5. Reports state

### Monthly children report
- `GET /api/reports/children/monthly` вЂ” СЃРїРёСЃРѕРє РґРµС‚РµР№ РїРѕ Р·Р°РЅСЏС‚РёСЏРј Р·Р° РјРµСЃСЏС†
- РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РґР»СЏ СѓС‡С‘С‚Р° РїРѕСЃРµС‰Р°РµРјРѕСЃС‚Рё

### Revenue report
- `GET /api/reports/revenue` вЂ” РµР¶РµРјРµСЃСЏС‡РЅС‹Р№ РѕС‚С‡С‘С‚ СЃ РѕР±РѕСЂРѕС‚РѕРј
- Р’РєР»СЋС‡Р°РµС‚ bePaid-С‚СЂР°РЅР·Р°РєС†РёРё, РњРѕР№РљР»Р°СЃСЃ-РїР»Р°С‚РµР¶Рё, СЃРІРѕРґРєСѓ РїРѕ С„РёР»РёР°Р»Р°Рј
- Р¤СѓРЅРєС†РёСЏ: workoff РІРєР»СЋС‡Р°РµС‚СЃСЏ РІ СЂР°СЃС‡С‘С‚ (СЃ v7.0.66)

### bePaid reconciliation (СЃРІРµСЂРєР°)
- `GET /api/bepaid/transactions` вЂ” РёСЃС‚РѕСЂРёСЏ РёРјРїРѕСЂС‚РёСЂРѕРІР°РЅРЅС‹С… С‚СЂР°РЅР·Р°РєС†РёР№
- РРјРїРѕСЂС‚ С‡РµСЂРµР· Reports API v2 (day-by-day): `POST /api/bepaid/import`
- РЎС‚Р°С‚СѓСЃС‹ СЃРІРµСЂРєРё: `already_in_moyklass`, `found_in_subscription`, `possible_subscription_match`, `historical_subscription_match`, `user_found_no_payment_or_subscription`, `possible_payment_match`, `needs_review`, `ignored_not_successful`, `ignored_test`
- `BEPAID_AUTO_POST_TO_MOYKLASS=false` вЂ” Р°РІС‚РѕРїРѕСЃС‚РёРЅРі **РІС‹РєР»СЋС‡РµРЅ**, СЂСѓС‡РЅРѕР№ СЂРµР¶РёРј
- РўР°Р±Р»РёС†Р° `bepaid_transactions`: `transaction_uid`, `order_id`, `tracking_id`, `mk_user_id`, `match_status`, `mk_payment_id`

### Payment intents
- РўР°Р±Р»РёС†Р° `payment_intents` вЂ” СЂСѓС‡РЅС‹Рµ С‡РµСЂРЅРѕРІРёРєРё СЃС‡РµС‚РѕРІ
- РЎРѕР·РґР°РЅРёРµ/РїСЂРѕСЃРјРѕС‚СЂ/РѕС‚РјРµРЅР° С‡РµСЂРµР· Mini App (Admin)
- Р”РѕСЃС‚СѓРї: `owner`, `admin`, `director`, `operations`, `client_manager`
- РЎС‚Р°С‚СѓСЃС‹: `draft`, `ready`, `bepaid_creating`, `bepaid_created`, `bepaid_requires_check`, `paid`, `posted_to_moyklass`, `cancelled`, `error`
- РњРµС‚РѕРґ РѕРїР»Р°С‚С‹: `erip` (Р•Р РРџ) вЂ” РїРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ. `acquiring` вЂ” UI РµСЃС‚СЊ, РёРЅС‚РµРіСЂР°С†РёСЏ РЅРµ СЂРµР°Р»РёР·РѕРІР°РЅР°.

---

## 6. bePaid / MoyKlass payments state

### Р§С‚Рѕ СЂРµР°Р»РёР·РѕРІР°РЅРѕ:
| Р¤СѓРЅРєС†РёСЏ | РЎС‚Р°С‚СѓСЃ |
|---|---|
| РРјРїРѕСЂС‚ РёСЃС‚РѕСЂРёРё bePaid (Reports API v2, day-by-day) | вњ… Р Р°Р±РѕС‚Р°РµС‚ |
| bePaid webhook (РїСЂРёС‘Рј, РІРµСЂРёС„РёРєР°С†РёСЏ RSA, СЃРѕС…СЂР°РЅРµРЅРёРµ) | вњ… Р Р°Р±РѕС‚Р°РµС‚ |
| РЎРІРµСЂРєР° bePaid в†” РњРѕР№РљР»Р°СЃСЃ payments (userId + СЃСѓРјРјР°) | вњ… Р Р°Р±РѕС‚Р°РµС‚ |
| РЎРІРµСЂРєР° bePaid в†” РњРѕР№РљР»Р°СЃСЃ userSubscriptions (confidence) | вњ… Р Р°Р±РѕС‚Р°РµС‚ (v7.0.76) |
| UI СЃРІРµСЂРєРё (mobile cards, СЃС‚Р°С‚СѓСЃС‹, chips) | вњ… Р Р°Р±РѕС‚Р°РµС‚ |
| `bepaid_transactions` РІ SQLite | вњ… Р Р°Р±РѕС‚Р°РµС‚ |
| РЎРѕР·РґР°РЅРёРµ ERIP-СЃС‡С‘С‚Р° bePaid РёР· payment_intent | вњ… Р Р°Р±РѕС‚Р°РµС‚ (v7.0.82) |
| РђС‚РѕРјР°СЂРЅС‹Р№ claim `bepaid_creating` (race-condition guard) | вњ… Р Р°Р±РѕС‚Р°РµС‚ (v7.0.82) |
| `bepaid_requires_check` РїСЂРё timeout/5xx/missing UID | вњ… Р Р°Р±РѕС‚Р°РµС‚ (v7.0.82) |
| Р’Р°Р»РёРґР°С†РёСЏ РѕС‚РІРµС‚Р° bePaid (UID, amount, currency, tracking_id) | вњ… Р Р°Р±РѕС‚Р°РµС‚ (v7.0.82) |
| РЎРѕС…СЂР°РЅРµРЅРёРµ `bepaid_qr_code_raw` (Base64, РёР· `transaction.erip`) | вњ… Р Р°Р±РѕС‚Р°РµС‚ (v7.0.82) |
| РџРѕР»РЅС‹Р№ СЂСѓС‡РЅРѕР№ С†РёРєР»: РњРљ invoice в†’ intent в†’ bePaid ERIP | вњ… **РџРѕРґС‚РІРµСЂР¶РґРµРЅРѕ РІ production** (v7.0.90.4) |
| РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРѕРµ СЃРѕР·РґР°РЅРёРµ payment РІ РњРѕР№РљР»Р°СЃСЃ РїРѕСЃР»Рµ webhook | вќЊ РќРµ СЂРµР°Р»РёР·РѕРІР°РЅРѕ (`BEPAID_AUTO_POST_TO_MOYKLASS=false`) |
| Billing Profile / Auto Renewal / С†РёРєР» Р°Р±РѕРЅРµРјРµРЅС‚РѕРІ | вќЊ РќРµ СЂРµР°Р»РёР·РѕРІР°РЅРѕ |

### Production verification (2026-07-13):
- РЈС‡РµРЅРёРє: РљСЂРµРЅСЊС‚СЊ РђР»РµРєСЃР°РЅРґСЂ РђР»РµРєСЃР°РЅРґСЂРѕРІРёС‡, mk_user_id=9748998
- РЎС‡С‘С‚ РњРѕР№РљР»Р°СЃСЃ: #19060579, СЃСѓРјРјР° 229 BYN, Р°Р±РѕРЅРµРјРµРЅС‚ #17998775
- Payment intent: `ycpi_202607_9` СѓСЃРїРµС€РЅРѕ СЃРѕР·РґР°РЅ
- bePaid ERIP: СЃС‡С‘С‚ 974899826079, UID `779fe891-1be7-4318-8490-9748428b2999`

### РљР»СЋС‡РµРІС‹Рµ С‚РµС…РЅРёС‡РµСЃРєРёРµ РґРµС‚Р°Р»Рё (bePaid):
- **Endpoint:** `POST https://api.bepaid.by/beyag/payments`
- **Auth:** HTTP Basic (`BEPAID_ERIP_SHOP_ID` : `BEPAID_ERIP_SECRET_KEY`)
- **account_number:** `{mk_user_id}{YYMM}{pi_row_id}`, max 30 СЃРёРјРІРѕР»РѕРІ, СѓРЅРёРєР°Р»РµРЅ РЅР° intent
- **Р’РЅРёРјР°РЅРёРµ:** РЅРѕРІС‹Р№ Р·Р°РїСЂРѕСЃ СЃ С‚РµРј Р¶Рµ `account_number` Р°РЅРЅСѓР»РёСЂСѓРµС‚ РїСЂРµРґС‹РґСѓС‰РёР№ СЃС‡С‘С‚ РІ bePaid
- **ERIP-РґР°РЅРЅС‹Рµ РІ РѕС‚РІРµС‚Рµ:** `transaction.erip.account_number`, `transaction.erip.qr_code_raw` (РќР• `transaction.payment_method`)
- **notification_url** РІСЃРµРіРґР° РѕР±СЏР·Р°С‚РµР»РµРЅ: `{BEPAID_PUBLIC_BASE_URL}/api/integrations/bepaid/webhook/erip/{BEPAID_WEBHOOK_PATH_SECRET}`

### РўРµРєСѓС‰Р°СЏ С†РµР»СЊ: РїРµСЂРµС…РѕРґ Рє Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРѕРјСѓ С†РёРєР»Сѓ
РЎРµР№С‡Р°СЃ `payment_intents` вЂ” СЂСѓС‡РЅС‹Рµ С‡РµСЂРЅРѕРІРёРєРё. Р¦РµР»СЊ вЂ” Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРёР№ С†РёРєР» (СЃРј. СЂР°Р·РґРµР» 7).

---

## 7. Current business goal

**Р“Р»Р°РІРЅР°СЏ С†РµР»СЊ:** Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРёР№ С†РёРєР» РѕРїР»Р°С‚ РїРѕ Р°Р±РѕРЅРµРјРµРЅС‚Р°Рј:

```
РљР»РёРµРЅС‚ Р·Р°РїРёСЃС‹РІР°РµС‚СЃСЏ
    в†’ РњРµРЅРµРґР¶РµСЂ (РёР»Рё Р°РіРµРЅС‚) СЃРѕР·РґР°С‘С‚ payment_intent
    в†’ РђРіРµРЅС‚ РІС‹СЃС‚Р°РІР»СЏРµС‚ СЃС‡С‘С‚ bePaid (ERIP РёР»Рё СЌРєРІР°Р№СЂРёРЅРі)
    в†’ РљР»РёРµРЅС‚ РѕРїР»Р°С‡РёРІР°РµС‚
    в†’ bePaid РїСЂРёСЃС‹Р»Р°РµС‚ webhook
    в†’ РђРіРµРЅС‚ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё СЃРѕР·РґР°С‘С‚ payment РІ РњРѕР№РљР»Р°СЃСЃ
    в†’ РђРіРµРЅС‚ РїСЂРёРІСЏР·С‹РІР°РµС‚ payment Рє userSubscription (Р°Р±РѕРЅРµРјРµРЅС‚)
    в†’ Р—Р°РЅСЏС‚РёСЏ СЃРїРёСЃС‹РІР°СЋС‚СЃСЏ СЃ Р°Р±РѕРЅРµРјРµРЅС‚Р°
    в†’ РђРіРµРЅС‚ РѕС‚СЃР»РµР¶РёРІР°РµС‚ РѕСЃС‚Р°С‚РѕРє Р·Р°РЅСЏС‚РёР№
    в†’ РџСЂРё РёСЃС‡РµСЂРїР°РЅРёРё Р°Р±РѕРЅРµРјРµРЅС‚Р° в†’ Р°РІС‚Рѕ-РїСЂРѕРґР»РµРЅРёРµ в†’ РЅРѕРІС‹Р№ СЃС‡С‘С‚
```

**РўРµРєСѓС‰РёР№ СѓСЂРѕРІРµРЅСЊ Р°РІС‚РѕРјР°С‚РёР·Р°С†РёРё:** СЂСѓС‡РЅРѕРµ РІС‹СЃС‚Р°РІР»РµРЅРёРµ СЃС‡С‘С‚Р° bePaid С‡РµСЂРµР· Mini App (РєРЅРѕРїРєР° В«Р’С‹СЃС‚Р°РІРёС‚СЊ СЃС‡С‘С‚ bePaidВ»). РџРѕСЃР»Рµ РѕРїР»Р°С‚С‹ вЂ” СЂСѓС‡РЅР°СЏ СЃРІРµСЂРєР°, Р°РІС‚РѕСЃРѕР·РґР°РЅРёСЏ payment РІ РњРљ РЅРµС‚.

---

## 8. Current problem / next direction

### РџСЂРѕР±Р»РµРјР°:
`payment_intents` СЂР°Р±РѕС‚Р°СЋС‚ РєР°Рє СЂСѓС‡РЅС‹Рµ С‡РµСЂРЅРѕРІРёРєРё. РњРµР¶РґСѓ РѕРїР»Р°С‚РѕР№ bePaid Рё СЃРѕР·РґР°РЅРёРµРј payment РІ РњРѕР№РљР»Р°СЃСЃ вЂ” СЂСѓС‡РЅРѕР№ С€Р°Рі.

### Р¦РµР»РµРІР°СЏ Р°СЂС…РёС‚РµРєС‚СѓСЂР° (Р·Р°РґРѕРєСѓРјРµРЅС‚РёСЂРѕРІР°РЅР° РІ `docs/payment_automation_research.md`):
```
Billing Profile
    в†’ Auto Renewal (С‚СЂРёРіРіРµСЂ: Р·Р° N РґРЅРµР№ РґРѕ РѕРєРѕРЅС‡Р°РЅРёСЏ Р°Р±РѕРЅРµРјРµРЅС‚Р°)
    в†’ Payment Intent (Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РёР»Рё РїРѕ РєРЅРѕРїРєРµ РјРµРЅРµРґР¶РµСЂР°)
    в†’ bePaid Request (СЃС‡С‘С‚ ERIP/СЌРєРІР°Р№СЂРёРЅРі)
    в†’ Webhook Paid (РІРµСЂРёС„РёРєР°С†РёСЏ, СЃРѕС…СЂР°РЅРµРЅРёРµ)
    в†’ MoyKlass Payment (Р°РІС‚РѕСЃРѕР·РґР°РЅРёРµ payment + РїСЂРёРІСЏР·РєР° Рє subscription)
    в†’ Subscription updated (Р·Р°РЅСЏС‚РёСЏ Р°РєС‚РёРІРЅС‹)
    в†’ РЎР»РµРґСѓСЋС‰РёР№ С†РёРєР» РїСЂРё РёСЃС‡РµСЂРїР°РЅРёРё
```

### Р§С‚Рѕ РЅСѓР¶РЅРѕ СЂРµР°Р»РёР·РѕРІР°С‚СЊ (РїРѕ РїРѕСЂСЏРґРєСѓ РІР°Р¶РЅРѕСЃС‚Рё):
1. **Webhook в†’ MoyKlass**: РїРѕСЃР»Рµ `status=successful` РІ webhook Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё СЃРѕР·РґР°РІР°С‚СЊ `POST /v1/company/payments` РІ РњРѕР№РљР»Р°СЃСЃ СЃ РґСѓР±Р»РµРј-Р·Р°С‰РёС‚РѕР№ РїРѕ `transaction_uid`.
2. **Billing Profile**: С‚Р°Р±Р»РёС†Р° СЃ userId, filialId, subscription template, period, amount вЂ” РїСЂРѕС„РёР»СЊ РґР»СЏ Р°РІС‚РѕРІС‹СЃС‚Р°РІР»РµРЅРёСЏ.
3. **Auto Renewal**: scheduled job, РїСЂРѕРІРµСЂСЏРµС‚ РёСЃС‚РµРєР°СЋС‰РёРµ Р°Р±РѕРЅРµРјРµРЅС‚С‹ Рё СЃРѕР·РґР°С‘С‚ payment_intent Р·Р°СЂР°РЅРµРµ.
4. **РџРѕР»РЅС‹Р№ С†РёРєР»**: РѕР±СЉРµРґРёРЅРµРЅРёРµ РІСЃРµРіРѕ РІС‹С€Рµ РІ РµРґРёРЅС‹Р№ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРёР№ РїРѕС‚РѕРє.

---

## 9. Recent commits

```
0d118e9  fix(payments): harden bePaid invoice idempotency (v7.0.82)
db0f1e9  Add bePaid ERIP invoice creation from payment intent (v7.0.81)
8c7364f  Fix payment intents amount formatter (v7.0.80)
3b2b5fe  Fix payment intents rendering and filters (v7.0.79)
8c0858a  Fix payment intents list and UI (v7.0.78)
3486ef8  Add payment intents foundation (v7.0.77)
fef5ce4  Document payment automation architecture (payment-automation-research)
62b7485  Add confidence to bePaid subscription matching
17d0d4c  Add confidence to bePaid subscription matching (v7.0.76)
277f790  Match bePaid payments with MoyKlass subscriptions (v7.0.75)
b7a796e  Improve bePaid MoyKlass reconciliation details (v7.0.74)
4661b59  Use bePaid reports API v2 format (v7.0.73)
eb71694  Add bePaid history import for reconciliation (v7.0.69)
856aba5  Add bePaid payment reconciliation foundation (v7.0.67)
```

---

## 10. Standard deploy commands

```bash
# 1. РџРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє СЃРµСЂРІРµСЂСѓ
ssh <user>@<server-ip>

# 2. РџРµСЂРµРєР»СЋС‡РёС‚СЊСЃСЏ РЅР° ycagent
su - ycagent

# 3. РџРµСЂРµР№С‚Рё РІ РїСЂРѕРµРєС‚
cd /home/ycagent/yellow_club_agent

# 4. РЎРґРµР»Р°С‚СЊ backup Р±Р°Р·С‹ (РћР‘РЇР—РђРўР•Р›Р¬РќРћ)
cp storage/messages.db backups/messages_$(date +%Y%m%d_%H%M%S).db

# 5. Р—Р°Р±СЂР°С‚СЊ РёР·РјРµРЅРµРЅРёСЏ
git pull origin main

# 6. РџСЂРѕРІРµСЂРёС‚СЊ cache-bust РІРµСЂСЃРёСЋ
grep -r "v=7\." miniapp/index.html

# 7. РЎРёРЅС‚Р°РєСЃРёС‡РµСЃРєР°СЏ РїСЂРѕРІРµСЂРєР° Python
python -m py_compile config.py storage.py web_app_server.py intern_track.py bepaid_client.py

# 8. РџРµСЂРµР·Р°РїСѓСЃС‚РёС‚СЊ СЃРµСЂРІРёСЃС‹
sudo systemctl restart yellow-miniapp
sudo systemctl restart yellow-bot

# 9. РџСЂРѕРІРµСЂРёС‚СЊ СЃС‚Р°С‚СѓСЃ
sudo systemctl status yellow-miniapp --no-pager
sudo systemctl status yellow-bot --no-pager

# 10. РџРѕСЃРјРѕС‚СЂРµС‚СЊ Р»РѕРіРё
sudo journalctl -u yellow-miniapp -n 50 --no-pager
sudo journalctl -u yellow-bot -n 50 --no-pager
```

---

## 11. Next recommended task

**РЎРѕР·РґР°С‚СЊ С„Р°Р№Р»:** `docs/billing_cycle_automation_plan.md`

**РЎРѕРґРµСЂР¶Р°РЅРёРµ:** РїР»Р°РЅ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРѕРіРѕ С†РёРєР»Р° РѕРїР»Р°С‚ РїРѕ Р°Р±РѕРЅРµРјРµРЅС‚Р°Рј.

**Р§С‚Рѕ РґРѕР»Р¶РµРЅ РІРєР»СЋС‡Р°С‚СЊ РїР»Р°РЅ:**
1. РЎС…РµРјР°: payment_intent в†’ bePaid в†’ webhook в†’ РњРѕР№РљР»Р°СЃСЃ payment в†’ subscription
2. РўР°Р±Р»РёС†Р° `billing_profiles`: РїРѕР»СЏ, РёРЅРґРµРєСЃС‹, СЃРІСЏР·СЊ СЃ mk_user_id Рё userSubscriptionId
3. Webhook handler: Р°Р»РіРѕСЂРёС‚Рј РїРѕРёСЃРєР° payment_intent РїРѕ tracking_id/order_id + СЃРѕР·РґР°РЅРёРµ MK payment
4. Р”СѓР±Р»РµР·Р°С‰РёС‚Р°: РїРѕ `transaction_uid` (СѓРЅРёРєР°Р»СЊРЅС‹Р№ РёРЅРґРµРєСЃ РІ payment_intents РёР»Рё bepaid_transactions)
5. Auto Renewal job: periodic check РЅР° РёСЃС‚РµРєР°СЋС‰РёРµ Р°Р±РѕРЅРµРјРµРЅС‚С‹, СЃРѕР·РґР°РЅРёРµ payment_intent
6. Error handling: С‡С‚Рѕ РґРµР»Р°С‚СЊ РµСЃР»Рё РњРѕР№РљР»Р°СЃСЃ РЅРµРґРѕСЃС‚СѓРїРµРЅ, subscription РЅРµ РЅР°Р№РґРµРЅ
7. Rollout plan: СЌС‚Р°РїС‹ РѕС‚ СЂСѓС‡РЅРѕРіРѕ Рє РїРѕР»РЅРѕРјСѓ Р°РІС‚Рѕ

**РџСЂР°РІРёР»Р° РїСЂРё РёСЃСЃР»РµРґРѕРІР°РЅРёРё Рё СЂРµР°Р»РёР·Р°С†РёРё:**
- РќРµ РІРєР»СЋС‡Р°С‚СЊ `BEPAID_AUTO_POST_TO_MOYKLASS=true`
- РќРµ СЃРѕР·РґР°РІР°С‚СЊ payment РІ РњРѕР№РљР»Р°СЃСЃ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё (РїРѕРєР° РЅРµ СЃРѕРіР»Р°СЃРѕРІР°РЅР° СЃС…РµРјР°)
- РќРµ СЃРѕР·РґР°РІР°С‚СЊ bePaid request (С‚РѕР»СЊРєРѕ РїРѕ СЂСѓС‡РЅРѕРјСѓ trigger РёР· UI)
- РќРµ РјРµРЅСЏС‚СЊ `.env`
- РќРµ С‚СЂРѕРіР°С‚СЊ `storage/messages.db`
- Р§РёС‚Р°С‚СЊ `docs/payment_automation_research.md` РєР°Рє Р±Р°Р·Сѓ вЂ” С‚Р°Рј РїРѕРґСЂРѕР±РЅС‹Р№ Р°РЅР°Р»РёР· API

---

## Appendix: Key files

| Р¤Р°Р№Р» | РќР°Р·РЅР°С‡РµРЅРёРµ |
|---|---|
| `bot.py` | Telegram-Р±РѕС‚ (Р·Р°РїСЂРµС‰РµРЅРѕ Р·Р°РїСѓСЃРєР°С‚СЊ Р»РѕРєР°Р»СЊРЅРѕ) |
| `web_app_server.py` | Mini App HTTP-СЃРµСЂРІРµСЂ, РІСЃРµ API endpoints |
| `storage.py` | SQLite: РІСЃРµ С‚Р°Р±Р»РёС†С‹, РјРёРіСЂР°С†РёРё, Р·Р°РїСЂРѕСЃС‹ |
| `config.py` | РљРѕРЅС„РёРі РёР· env-РїРµСЂРµРјРµРЅРЅС‹С… |
| `bepaid_client.py` | bePaid API client (ERIP invoice, response parsing) |
| `moyklass_client.py` | РњРѕР№РљР»Р°СЃСЃ API client |
| `intern_track.py` | Р›РѕРіРёРєР° РјР°СЂС€СЂСѓС‚Р° СЃС‚Р°Р¶С‘СЂР° |
| `food_menu_ocr.py` | OCR РјРµРЅСЋ РїРѕ С„РѕС‚Рѕ (pytesseract) |
| `miniapp/index.html` | Mini App HTML (cache-bust РІРµСЂСЃРёСЏ Р·РґРµСЃСЊ) |
| `miniapp/app.js` | Mini App JS (РІРµСЃСЊ frontend) |
| `miniapp/styles.css` | Mini App CSS |
| `storage/messages.db` | Production Р±Р°Р·Р° РґР°РЅРЅС‹С… (РќР• РєРѕРјРјРёС‚РёС‚СЊ) |
| `docs/payment_automation_research.md` | Р”РµС‚Р°Р»СЊРЅС‹Р№ Р°РЅР°Р»РёР· bePaid + РњРѕР№РљР»Р°СЃСЃ API |
| `docs/CURRENT_STATE.md` | Р­С‚РѕС‚ С„Р°Р№Р» вЂ” handoff РґРѕРєСѓРјРµРЅС‚ |
| `PROJECT_STATUS.md` | РСЃС‚РѕСЂРёСЏ РІРµСЂСЃРёР№ Рё changelog |
| `RELEASE_CHECKLIST.md` | Р§РµРєР»РёСЃС‚С‹ РґРµРїР»РѕСЏ Рё С‚РµСЃС‚РёСЂРѕРІР°РЅРёСЏ |
| `FOOD_MODULE_PLAN.md` | Р”РµС‚Р°Р»СЊРЅС‹Р№ РїР»Р°РЅ РјРѕРґСѓР»СЏ РїРёС‚Р°РЅРёСЏ |
