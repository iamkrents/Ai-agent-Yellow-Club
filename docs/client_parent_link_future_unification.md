# Client Parent-Child Link System — Architecture and Future Unification Plan

**Version:** v7.0.93.1  
**Date:** 2026-07-15  
**Status:** Active — two separate systems coexist

---

## Current State: Two Parallel Systems

Yellow Club has two independent parent-child link systems that serve different modules and must not be merged until both are ready for migration.

### System A — Food Module (existing, do not modify)

| Concern | Detail |
|---------|--------|
| Tables | `parent_child_links`, `camp_children` |
| Code format | `YC-XXXX` (4 chars, plaintext stored in DB) |
| Reusable codes | Yes — same code can be re-entered |
| Purpose | City programme food ordering only |
| Endpoints | `/api/food/my-children`, `/api/food/link-child` |
| Admin UI | Food admin panel (existing) |
| MoyKlass sync | `camp_children` synced from MoyKlass student roster |

### System B — Client Module (new in v7.0.93.1)

| Concern | Detail |
|---------|--------|
| Tables | `client_child_link_codes`, `client_parent_child_links` |
| Code format | `CL-XXXXXXXX` (8 chars from 32-char alphabet) |
| One-time use | Yes — each code is consumed on first use |
| Code storage | SHA-256 hash only; plaintext returned once to admin, never stored |
| Purpose | Payments section; future: attendance, progress reports, scheduling |
| Endpoints | `/api/client/children`, `/api/client/children/link` |
| Admin endpoints | `/api/client/admin/link-codes`, `/api/client/admin/link-status`, `/api/client/admin/unlink` |
| Join key | `mk_user_id` TEXT — MoyKlass user ID string |

---

## Why Two Systems?

1. **Different security models** — Food codes are convenience tokens (short, reusable, plaintext). Client codes handle financial data (hashed, one-time, 8-char).
2. **Different data sources** — Food links sync from `camp_children` (city programme roster). Client links are manually created by admins per-student.
3. **Separate access control** — A parent with a food link does not automatically get payment visibility (and vice versa).
4. **Migration risk** — Moving active food parents into the client system would require a coordinated migration with zero downtime on food ordering. This is deferred.

---

## Unified View for Parents

`GET /api/client/children` returns both systems in a single response with a `source` field:

```json
{
  "ok": true,
  "children": [
    {
      "source": "client",
      "mk_user_id": "12345",
      "display_name": "Иван Иванов",
      "linked_at": "2026-07-15T10:00:00",
      "available_modules": ["payments"]
    },
    {
      "source": "food",
      "mk_student_id": "67890",
      "display_name": "Мария Иванова",
      "group_name": "Группа A",
      "confirmed_at": "2026-06-01",
      "available_modules": ["food"]
    }
  ],
  "client_count": 1,
  "food_count": 1
}
```

The miniapp "Мои дети" tab renders both sections distinctly: a CL- form for payments, a YC- form for food.

---

## Future Unification Checklist

When the time comes to merge the two systems, the following must be completed:

### Prerequisites
- [ ] Food module migration plan approved by team
- [ ] All active `parent_child_links` rows identified and mapped to `mk_user_id`
- [ ] Communication plan for parents whose YC- codes will be retired
- [ ] Database backup before migration

### Migration steps
1. For each confirmed row in `parent_child_links`, find the corresponding `camp_children.mk_student_id` → `mk_user_id`
2. INSERT matching rows into `client_parent_child_links` with `status='active'`, `linked_by_code_id=NULL`, `unlinked_by=NULL`
3. Update food endpoints to call `client_parent_child_links` instead of `parent_child_links` for auth
4. Keep `parent_child_links` as read-only archive; stop writing new rows to it
5. Update food admin UI to show client link status alongside food status
6. Remove YC- code generation once all parents are migrated

### Post-migration
- `GET /api/client/children` no longer needs to read from `parent_child_links`; food children will appear via `client_parent_child_links` with `available_modules: ["payments", "food"]`
- `GET /api/food/my-children` can be reimplemented as an alias to the unified endpoint
- `parent_child_links` and `camp_children` tables remain for historical audit; no data deleted

---

## Key Invariants (must hold forever)

1. `list_client_visible_payment_intents` JOINs ONLY `client_parent_child_links` — never `parent_child_links`
2. `get_parents_for_child` queries ONLY `client_parent_child_links` — never `parent_child_links`
3. Food endpoints (`/api/food/*`) remain unchanged and continue using `parent_child_links`
4. Client codes are never stored in plaintext; only SHA-256 hash is persisted
5. `BEPAID_AUTO_POST_TO_MOYKLASS=false` — must not change
