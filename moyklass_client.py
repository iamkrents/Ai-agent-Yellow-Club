from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional


@dataclass
class MoyKlassResult:
    ok: bool
    data: Any = None
    status: int = 0
    error: str = ""
    endpoint: str = ""


class MoyKlassClient:
    """Small read-only MoyKlass API client for Yellow Club Agent.

    Auth flow:
    POST /v1/company/auth/getToken with {"apiKey": "..."}, then x-access-token header.
    This client supports read operations and guarded write diagnostics used only after explicit user confirmation.
    """

    def __init__(self, api_url: str, api_key: str, timeout: int = 25):
        self.api_url = (api_url or "https://api.moyklass.com").strip().rstrip("/")
        self.api_key = (api_key or "").strip().strip('"').strip("'")
        self.timeout = int(timeout or 25)
        self._access_token: Optional[str] = None
        self._lookup_maps: Optional[dict[str, dict[str, str]]] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.upper().startswith("PASTE_"))

    def _json_request(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> MoyKlassResult:
        if not path.startswith("/"):
            path = "/" + path
        query = ""
        if params:
            clean_params = {k: v for k, v in params.items() if v not in (None, "")}
            if clean_params:
                query = "?" + urllib.parse.urlencode(clean_params, doseq=True)
        url = self.api_url + path + query
        body = None
        req_headers = {"Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=req_headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return MoyKlassResult(True, data=None, status=getattr(resp, "status", 200), endpoint=path)
                try:
                    return MoyKlassResult(True, data=json.loads(raw), status=getattr(resp, "status", 200), endpoint=path)
                except Exception:
                    return MoyKlassResult(True, data=raw, status=getattr(resp, "status", 200), endpoint=path)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            return MoyKlassResult(False, status=e.code, error=(raw or str(e))[:2000], endpoint=path)
        except Exception as e:
            return MoyKlassResult(False, status=0, error=str(e), endpoint=path)

    def get_token(self) -> MoyKlassResult:
        if not self.is_configured:
            return MoyKlassResult(False, error="MOYKLASS_API_KEY не указан в .env", endpoint="/v1/company/auth/getToken")
        result = self._json_request(
            "POST",
            "/v1/company/auth/getToken",
            payload={"apiKey": self.api_key},
            headers={"Content-Type": "application/json"},
        )
        if not result.ok:
            return result
        token = self._extract_access_token(result.data)
        if not token:
            return MoyKlassResult(False, data=result.data, status=result.status, error="Токен не найден в ответе API", endpoint=result.endpoint)
        self._access_token = token
        return MoyKlassResult(True, data={"accessToken": token}, status=result.status, endpoint=result.endpoint)

    def _extract_access_token(self, data: Any) -> Optional[str]:
        if isinstance(data, dict):
            for key in ("accessToken", "access_token", "token", "access token", "accessTokenString"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in data.values():
                token = self._extract_access_token(value)
                if token:
                    return token
        return None

    def request(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> MoyKlassResult:
        if not self._access_token:
            token_result = self.get_token()
            if not token_result.ok:
                return token_result
        return self._json_request(
            method,
            path,
            payload=payload,
            params=params,
            headers={"x-access-token": self._access_token or ""},
        )

    def test(self) -> MoyKlassResult:
        token_result = self.get_token()
        if not token_result.ok:
            return token_result
        users_result = self.request("GET", "/v1/company/users", params={"limit": 1})
        if users_result.ok:
            return MoyKlassResult(True, data={"auth": "ok", "users_endpoint": "ok"}, status=users_result.status)
        return MoyKlassResult(True, data={"auth": "ok", "users_endpoint": f"error {users_result.status}: {users_result.error[:500]}"}, status=token_result.status)

    def get_lessons(self, raw_args: str = "") -> MoyKlassResult:
        params = self._parse_params(raw_args, default_limit="10")
        return self._enrich_lesson_result(self.request("GET", "/v1/company/lessons", params=params))

    def get_users(self, raw_args: str = "") -> MoyKlassResult:
        params = self._parse_params(raw_args, default_limit="10")
        return self.request("GET", "/v1/company/users", params=params)

    def get_teachers(self, raw_args: str = "") -> MoyKlassResult:
        """Try to read MoyKlass teachers/staff list.

        MoyKlass accounts can expose teacher data in different endpoints depending on configuration.
        This method tries the most likely endpoint first. If it fails, use teacherIds from lessons.
        """
        params = self._parse_params(raw_args, default_limit="30")
        result = self.request("GET", "/v1/company/teachers", params=params)
        if result.ok:
            return result
        # Keep the original error, but include fallback hint in data for diagnostics.
        fallback = self.request("GET", "/v1/company/managers", params=params)
        if fallback.ok:
            return fallback
        result.data = {"tried": ["/v1/company/teachers", "/v1/company/managers"]}
        return result

    def get_lessons_on_date(self, day: str | date) -> MoyKlassResult:
        d = parse_date(day) if isinstance(day, str) else day
        if not d:
            return MoyKlassResult(False, error="Неверная дата. Формат: YYYY-MM-DD")
        return self.get_lessons_between(d, d + timedelta(days=1))

    def get_lessons_between(self, date_from: str | date, date_to: str | date, limit: int = 80) -> MoyKlassResult:
        """Read lessons for a date range and never show old lessons as 'today'.

        MoyKlass API installations can ignore unsupported query parameters and return
        the first old lessons from the account. Earlier versions passed only
        dateFrom/dateTo, so if the API ignored them the bot displayed 2022 lessons
        under the button 'Занятия сегодня'.

        This method tries several safe date parameter variants and additionally
        filters the response locally by the lesson's `date` field. If the API still
        returns only old lessons, the user will see 'no lessons found' with a
        diagnostic note instead of outdated lessons.
        """
        d1 = parse_date(date_from) if isinstance(date_from, str) else date_from
        d2 = parse_date(date_to) if isinstance(date_to, str) else date_to
        if not d1 or not d2:
            return MoyKlassResult(False, error="Неверная дата. Формат: YYYY-MM-DD")
        if d2 <= d1:
            d2 = d1 + timedelta(days=1)

        limit_str = str(max(int(limit or 80), 80))
        date_variants = [
            {"dateFrom": d1.isoformat(), "dateTo": d2.isoformat(), "limit": limit_str},
            {"beginDate": d1.isoformat(), "endDate": d2.isoformat(), "limit": limit_str},
            {"startDate": d1.isoformat(), "endDate": d2.isoformat(), "limit": limit_str},
            {"dateStart": d1.isoformat(), "dateEnd": d2.isoformat(), "limit": limit_str},
            {"from": d1.isoformat(), "to": d2.isoformat(), "limit": limit_str},
            {"date_from": d1.isoformat(), "date_to": d2.isoformat(), "limit": limit_str},
            {"date": d1.isoformat(), "limit": limit_str},
        ]

        first_ok: MoyKlassResult | None = None
        diagnostics: list[dict[str, Any]] = []
        for params in date_variants:
            result = self.request("GET", "/v1/company/lessons", params=params)
            diagnostics.append({
                "params": params,
                "ok": result.ok,
                "status": result.status,
                "count": len(extract_items(result.data)) if result.ok else 0,
                "error": result.error[:300] if result.error else "",
            })
            if not result.ok:
                continue
            if first_ok is None:
                first_ok = result
            filtered = self._filter_lessons_data_by_date(result.data, d1, d2)
            if filtered:
                return MoyKlassResult(
                    True,
                    data={
                        "lessons": self._enrich_lesson_items(filtered),
                        "_range": {"from": d1.isoformat(), "to": d2.isoformat()},
                        "_usedParams": params,
                    },
                    status=result.status,
                    endpoint=result.endpoint,
                )

        # If date parameters are ignored by MoyKlass, the endpoint usually returns
        # the first old lessons from the account. In Yellow Club it returned 2022
        # while the needed lessons existed in 2026. As a fallback, scan pages and
        # filter locally by the `date` field.
        scan = self._scan_lessons_between_by_pages(d1, d2, limit=limit)
        if scan.ok:
            scan_items = extract_items(scan.data)
            if scan_items:
                return scan
            if isinstance(scan.data, dict):
                diagnostics.extend(scan.data.get("_scanDiagnostics") or [])

        # Do not return old unfiltered lessons. Showing old lessons under 'today'
        # is worse than showing an empty result with diagnostics.
        status = first_ok.status if first_ok else (scan.status if scan else 0)
        return MoyKlassResult(
            True,
            data={
                "lessons": [],
                "_range": {"from": d1.isoformat(), "to": d2.isoformat()},
                "_filterWarning": (
                    "По выбранному периоду подходящих занятий не найдено. "
                    "МойКласс не принял прямой фильтр даты, а резервное сканирование страниц "
                    "тоже не нашло занятий. Старые занятия скрыты, чтобы не показывать их как актуальные."
                ),
                "_diagnostics": diagnostics[:20],
            },
            status=status,
            endpoint="/v1/company/lessons",
        )

    def _scan_lessons_between_by_pages(self, d1: date, d2: date, limit: int = 120) -> MoyKlassResult:
        """Fallback for accounts where /lessons ignores date filters.

        The API can return old lessons when it does not understand dateFrom/dateTo.
        This method tries common pagination styles and filters lessons locally.
        It stops when pagination repeats the same page or when enough matching
        lessons are collected.
        """
        page_size = 200
        wanted_limit = max(int(limit or 120), 120)
        max_pages = 80  # up to 16k lessons per strategy; enough for diagnostics without infinite loops
        strategies = [
            ("offset", lambda page: {"limit": str(page_size), "offset": str(page * page_size)}),
            ("skip", lambda page: {"limit": str(page_size), "skip": str(page * page_size)}),
            ("page", lambda page: {"limit": str(page_size), "page": str(page + 1)}),
            ("page_pageSize", lambda page: {"page": str(page + 1), "pageSize": str(page_size)}),
            ("start", lambda page: {"limit": str(page_size), "start": str(page * page_size)}),
        ]
        all_diagnostics: list[dict[str, Any]] = []
        best_status = 0

        for strategy_name, make_params in strategies:
            seen_signatures: set[str] = set()
            matches: list[dict[str, Any]] = []
            min_seen: str = ""
            max_seen: str = ""
            repeated = False

            for page in range(max_pages):
                params = make_params(page)
                result = self.request("GET", "/v1/company/lessons", params=params)
                best_status = result.status or best_status
                if not result.ok:
                    all_diagnostics.append({
                        "scan": strategy_name,
                        "page": page + 1,
                        "params": params,
                        "ok": False,
                        "status": result.status,
                        "error": result.error[:300],
                    })
                    break

                items = [x for x in extract_items(result.data) if isinstance(x, dict)]
                if not items:
                    all_diagnostics.append({
                        "scan": strategy_name,
                        "page": page + 1,
                        "params": params,
                        "ok": True,
                        "count": 0,
                        "note": "empty_page",
                    })
                    break

                first_id = _pick(items[0], ("id", "lessonId"))
                last_id = _pick(items[-1], ("id", "lessonId"))
                first_date = _lesson_date_value(items[0])
                last_date = _lesson_date_value(items[-1])
                signature = f"{first_id}|{last_id}|{first_date}|{last_date}|{len(items)}"
                if signature in seen_signatures:
                    repeated = True
                    all_diagnostics.append({
                        "scan": strategy_name,
                        "page": page + 1,
                        "params": params,
                        "ok": True,
                        "count": len(items),
                        "first": first_date,
                        "last": last_date,
                        "note": "repeated_page_stop",
                    })
                    break
                seen_signatures.add(signature)

                dates = [parse_date(_lesson_date_value(x)) for x in items]
                dates = [x for x in dates if x]
                if dates:
                    local_min = min(dates).isoformat()
                    local_max = max(dates).isoformat()
                    min_seen = min([v for v in (min_seen, local_min) if v], default="")
                    max_seen = max([v for v in (max_seen, local_max) if v], default="")
                else:
                    local_min = ""
                    local_max = ""

                page_matches = []
                for item in items:
                    lesson_date = parse_date(_lesson_date_value(item))
                    if lesson_date and d1 <= lesson_date < d2:
                        page_matches.append(item)
                if page_matches:
                    matches.extend(page_matches)
                    if len(matches) >= wanted_limit:
                        break

                # If this pagination is sorted by date and we already passed the target range, stop early.
                if dates and min(dates) >= d2 and not page_matches:
                    all_diagnostics.append({
                        "scan": strategy_name,
                        "page": page + 1,
                        "params": params,
                        "ok": True,
                        "count": len(items),
                        "minDate": local_min,
                        "maxDate": local_max,
                        "note": "passed_target_range_stop",
                    })
                    break

                # Keep diagnostics compact: first pages, last status, and pages near hits are enough.
                if page < 3 or page % 10 == 9 or page_matches:
                    all_diagnostics.append({
                        "scan": strategy_name,
                        "page": page + 1,
                        "params": params,
                        "ok": True,
                        "count": len(items),
                        "minDate": local_min,
                        "maxDate": local_max,
                        "matches": len(page_matches),
                    })

            if matches:
                matches.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
                return MoyKlassResult(
                    True,
                    data={
                        "lessons": self._enrich_lesson_items(matches[:wanted_limit]),
                        "_range": {"from": d1.isoformat(), "to": d2.isoformat()},
                        "_usedParams": {"fallbackScan": strategy_name},
                        "_scanDiagnostics": all_diagnostics[-12:],
                    },
                    status=best_status or 200,
                    endpoint="/v1/company/lessons",
                )

            # If a strategy repeats the first page, other offset-like strategies may still work, so continue.
            all_diagnostics.append({
                "scan": strategy_name,
                "ok": True,
                "note": "no_matches",
                "minSeen": min_seen,
                "maxSeen": max_seen,
                "repeated": repeated,
            })

        return MoyKlassResult(
            True,
            data={
                "lessons": [],
                "_range": {"from": d1.isoformat(), "to": d2.isoformat()},
                "_scanDiagnostics": all_diagnostics[-20:],
            },
            status=best_status,
            endpoint="/v1/company/lessons",
        )

    def get_upcoming_lessons(self, days: int = 7, limit: int = 120) -> MoyKlassResult:
        today = date.today()
        return self.get_lessons_between(today, today + timedelta(days=max(1, int(days))), limit=limit)

    def get_teacher_lessons_paginated(
        self,
        teacher_id: str | int,
        date_from: date,
        date_to: date,
        max_pages: int = 30,
    ) -> dict[str, Any]:
        """Fetch all lessons for a specific teacher with full pagination.

        Tries three strategies in order:
        1. Direct teacher filter (teacherId param) — most efficient if API supports it
        2. Date-range with pagination through all pages — works when API respects dateFrom/dateTo
        3. Full page scan without date filter — last resort fallback

        Returns a rich diagnostic dict (not MoyKlassResult) with:
        - matched: list of matching lesson items
        - total_loaded: total lessons fetched across all pages
        - pages_loaded: number of pages fetched
        - strategy_used: which strategy succeeded
        - teacher_field_stats: counts of which field shapes were seen
        - unique_teacher_ids_sample: sample of IDs seen in teacher fields
        - sample_raw_shapes: first few lesson shapes for debugging
        - reason_if_zero: human-readable reason when matched=0
        - date_range: {from, to}
        """
        tid = str(teacher_id or "").strip()
        d1 = date_from
        d2 = date_to
        page_size = 200
        matched: list[dict] = []
        all_items: list[dict] = []
        pages_loaded = 0
        strategy_used = "none"

        def _extract_ids(item: dict) -> list[str]:
            """Extract all teacher IDs from a lesson item using every known field."""
            results: list[str] = []

            def _add_value(v: Any) -> None:
                if v is None or v == "":
                    return
                s = str(v).strip()
                if s and s not in results:
                    results.append(s)

            def _add_from_obj(obj: Any) -> None:
                if isinstance(obj, dict):
                    for k in ("id", "teacherId", "userId", "employeeId", "staffId", "managerId", "tutorId", "responsibleId"):
                        _add_value(obj.get(k))
                elif obj is not None and obj != "":
                    _add_value(str(obj))

            for field in ("teacherIds", "teacher_ids", "teachersIds", "teacher_id_list", "teacherId", "teacher_id"):
                val = item.get(field)
                if isinstance(val, list):
                    for v in val:
                        _add_from_obj(v)
                elif val is not None and val != "":
                    _add_value(str(val))

            for field in ("teachers", "teacher", "staff", "staffs", "employees"):
                val = item.get(field)
                if isinstance(val, list):
                    for v in val:
                        _add_from_obj(v)
                elif isinstance(val, dict):
                    _add_from_obj(val)
                elif val is not None and val != "":
                    _add_value(str(val))

            for field in ("employeeId", "employeeIds", "staffId", "managerId", "tutorId", "responsibleId", "userId"):
                val = item.get(field)
                if isinstance(val, list):
                    for v in val:
                        _add_value(str(v))
                elif val is not None and val != "":
                    _add_value(str(val))

            return results

        def _collect_field_stats(items: list[dict]) -> dict:
            stats: dict[str, int] = {}
            unique_ids: list[str] = []
            shapes: list[dict] = []
            for item in items:
                for field in ("teacherId", "teacherIds", "teachers", "teacher", "teacher_id", "teacher_ids",
                              "staff", "employees", "userId", "responsibleId", "employeeId", "staffId",
                              "managerId", "tutorId"):
                    if item.get(field) not in (None, "", [], {}):
                        stats[field] = stats.get(field, 0) + 1
                for iid in _extract_ids(item):
                    if iid and iid not in unique_ids:
                        unique_ids.append(iid)
                if len(shapes) < 5:
                    lid = str(_pick(item, ("id", "lessonId")) or "")
                    shape: dict[str, Any] = {
                        "lesson_id": lid,
                        "date": _lesson_date_value(item) or "",
                        "keys": [k for k in item.keys() if k not in ("students", "visits", "attendance", "records")],
                    }
                    for f in ("teacherId", "teacherIds", "teacher_id", "teachers", "teacher", "staff", "employees",
                              "employeeId", "staffId", "managerId", "userId", "responsibleId"):
                        if item.get(f) is not None:
                            v = item[f]
                            if isinstance(v, list) and len(v) > 3:
                                shape[f + "_sample"] = v[:3]
                            else:
                                shape[f] = v
                    shapes.append(shape)
            return {"fields": stats, "unique_ids_sample": unique_ids[:20], "sample_shapes": shapes}

        # Strategy 1: Direct teacher filter
        teacher_param_variants = [
            {"teacherId": tid, "dateFrom": d1.isoformat(), "dateTo": d2.isoformat(), "limit": str(page_size)},
            {"teacher_id": tid, "dateFrom": d1.isoformat(), "dateTo": d2.isoformat(), "limit": str(page_size)},
            {"teacherId": tid, "beginDate": d1.isoformat(), "endDate": d2.isoformat(), "limit": str(page_size)},
            {"userId": tid, "dateFrom": d1.isoformat(), "dateTo": d2.isoformat(), "limit": str(page_size)},
        ]
        for tv_params in teacher_param_variants:
            r = self.request("GET", "/v1/company/lessons", params=tv_params)
            if not r.ok:
                continue
            items = [x for x in extract_items(r.data) if isinstance(x, dict)]
            filtered = [x for x in items if d1 <= (parse_date(_lesson_date_value(x)) or d1) < d2]
            if filtered:
                matched = filtered
                all_items = items
                pages_loaded = 1
                strategy_used = f"teacher_filter:{list(tv_params.keys())[0]}"
                break

        # Strategy 2: Date-range with full pagination
        if not matched:
            date_param_sets = [
                {"dateFrom": d1.isoformat(), "dateTo": d2.isoformat()},
                {"beginDate": d1.isoformat(), "endDate": d2.isoformat()},
                {"startDate": d1.isoformat(), "endDate": d2.isoformat()},
                {"date_from": d1.isoformat(), "date_to": d2.isoformat()},
            ]
            pagination_styles = [
                ("offset", lambda pg: {"offset": str(pg * page_size)}),
                ("skip", lambda pg: {"skip": str(pg * page_size)}),
                ("page", lambda pg: {"page": str(pg + 1)}),
            ]
            for base_params in date_param_sets:
                for pag_name, pag_fn in pagination_styles:
                    seen_sigs: set[str] = set()
                    page_items: list[dict] = []
                    page_count = 0
                    for pg in range(max_pages):
                        params = {**base_params, "limit": str(page_size), **pag_fn(pg)}
                        r = self.request("GET", "/v1/company/lessons", params=params)
                        if not r.ok:
                            break
                        items = [x for x in extract_items(r.data) if isinstance(x, dict)]
                        if not items:
                            break
                        sig = f"{_pick(items[0], ('id','lessonId'))}|{len(items)}"
                        if sig in seen_sigs:
                            break
                        seen_sigs.add(sig)
                        page_count += 1
                        in_range = [x for x in items if d1 <= (parse_date(_lesson_date_value(x)) or d1) < d2]
                        page_items.extend(in_range)
                        all_items.extend(items)
                        if len(items) < page_size:
                            break
                    if page_items:
                        pages_loaded = page_count
                        strategy_used = f"date_range+{pag_name}:{list(base_params.keys())[0]}"
                        matched = page_items
                        break
                if matched:
                    break

        # Strategy 3: Full page scan without date filter (last resort)
        if not matched:
            for pag_name, pag_fn in [
                ("offset", lambda pg: {"offset": str(pg * page_size)}),
                ("page", lambda pg: {"page": str(pg + 1)}),
            ]:
                seen_sigs2: set[str] = set()
                page_items2: list[dict] = []
                page_count2 = 0
                for pg in range(max_pages):
                    params2 = {"limit": str(page_size), **pag_fn(pg)}
                    r2 = self.request("GET", "/v1/company/lessons", params=params2)
                    if not r2.ok:
                        break
                    items2 = [x for x in extract_items(r2.data) if isinstance(x, dict)]
                    if not items2:
                        break
                    sig2 = f"{_pick(items2[0], ('id','lessonId'))}|{len(items2)}"
                    if sig2 in seen_sigs2:
                        break
                    seen_sigs2.add(sig2)
                    page_count2 += 1
                    in_range2 = [x for x in items2 if d1 <= (parse_date(_lesson_date_value(x)) or d1) < d2]
                    page_items2.extend(in_range2)
                    all_items.extend(items2)
                    dates2 = [parse_date(_lesson_date_value(x)) for x in items2]
                    dates2 = [d for d in dates2 if d]
                    if dates2 and min(dates2) > d2:
                        break
                    if len(items2) < page_size:
                        break
                if page_items2:
                    pages_loaded = page_count2
                    strategy_used = f"full_scan+{pag_name}"
                    matched = page_items2
                    all_items = list({_pick(x, ("id", "lessonId")): x for x in all_items}.values())
                    break

        # Filter matched by teacher ID
        total_loaded = len(all_items)
        teacher_matched = [x for x in matched if tid in _extract_ids(x)]
        # Name-based fallback detection
        name_matched_ids: list[str] = []
        teacher_name_in_storage = ""
        for item in all_items:
            for field in ("teachers", "teacher", "staff"):
                val = item.get(field)
                objs = val if isinstance(val, list) else ([val] if isinstance(val, dict) else [])
                for obj in objs:
                    if isinstance(obj, dict):
                        obj_name = str(obj.get("name") or obj.get("fullName") or obj.get("fio") or "").strip()
                        obj_id = str(obj.get("id") or obj.get("userId") or obj.get("teacherId") or "").strip()
                        if obj_id and obj_id not in name_matched_ids:
                            name_matched_ids.append(obj_id)
        field_stats = _collect_field_stats(all_items[:200])

        reason_if_zero = ""
        if not teacher_matched and total_loaded > 0:
            if not matched:
                reason_if_zero = "no_lessons_in_date_range"
            elif len(matched) > 0 and not teacher_matched:
                reason_if_zero = "teacher_id_not_present_in_loaded_lessons"
                if len(all_items) >= page_size * max_pages:
                    reason_if_zero += "+possible_pagination_truncated"
            else:
                reason_if_zero = "unknown"
        elif total_loaded == 0:
            reason_if_zero = "no_lessons_loaded_from_api"

        return {
            "matched": teacher_matched,
            "all_in_range": matched,
            "total_loaded": total_loaded,
            "total_in_range": len(matched),
            "pages_loaded": max(pages_loaded, 1 if total_loaded > 0 else 0),
            "strategy_used": strategy_used,
            "date_range": {"from": d1.isoformat(), "to": d2.isoformat()},
            "teacher_id_searched": tid,
            "matched_by_id": len(teacher_matched),
            "field_stats": field_stats,
            "reason_if_zero": reason_if_zero,
        }

    def _filter_lessons_data_by_date(self, data: Any, d1: date, d2: date) -> list[dict[str, Any]]:
        items = extract_items(data)
        filtered: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            lesson_date = parse_date(_lesson_date_value(item))
            if lesson_date and d1 <= lesson_date < d2:
                filtered.append(item)
        filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
        return filtered

    def search_users(self, query: str, limit: int = 10) -> MoyKlassResult:
        query = (query or "").strip()
        if not query:
            return self.get_users(f"limit={limit}")
        # MoyKlass integrations commonly accept free-text query parameters differently,
        # so send several harmless variants; the API will ignore unsupported ones.
        params = {"q": query, "search": query, "name": query, "limit": str(limit)}
        return self.request("GET", "/v1/company/users", params=params)


    def get_classes(self, raw_args: str = "") -> MoyKlassResult:
        """Read MoyKlass groups/classes using several known endpoint variants."""
        params = self._parse_params(raw_args, default_limit="60")
        endpoints = [
            "/v1/company/classes",
            "/v1/company/groups",
            "/v1/company/courseClasses",
            "/v1/company/classGroups",
        ]
        last = MoyKlassResult(False, error="Не удалось получить группы МойКласс", endpoint=endpoints[0])
        diagnostics: list[dict[str, Any]] = []
        for endpoint in endpoints:
            result = self.request("GET", endpoint, params=params)
            diagnostics.append({
                "endpoint": endpoint,
                "ok": result.ok,
                "status": result.status,
                "count": len(extract_items(result.data)) if result.ok else 0,
                "error": result.error[:300] if result.error else "",
            })
            last = result
            if result.ok and extract_items(result.data):
                if isinstance(result.data, dict):
                    result.data.setdefault("_endpoint", endpoint)
                    result.data.setdefault("_diagnostics", diagnostics)
                return result
        if isinstance(last.data, dict):
            last.data.setdefault("_diagnostics", diagnostics)
        else:
            last.data = {"_diagnostics": diagnostics, "raw": last.data}
        return last

    def search_classes(self, query: str, limit: int = 30) -> MoyKlassResult:
        query = (query or "").strip()
        if not query:
            return self.get_classes(f"limit={limit}")
        base = self.get_classes(f"limit={max(limit, 100)}")
        if not base.ok:
            return base
        items = extract_items(base.data)
        q = query.lower().replace("ё", "е")
        filtered: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _class_name_value(item).lower().replace("ё", "е")
            item_id = _class_id_value(item)
            if q in name or q == str(item_id):
                filtered.append(item)
        return MoyKlassResult(True, data={"classes": filtered, "_query": query}, status=base.status, endpoint=base.endpoint)

    def get_class_by_id(self, class_id: int | str) -> MoyKlassResult:
        class_id = str(class_id).strip()
        if not class_id.isdigit():
            return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/classes/{id}")
        endpoints = [
            f"/v1/company/classes/{class_id}",
            f"/v1/company/groups/{class_id}",
            f"/v1/company/courseClasses/{class_id}",
            f"/v1/company/classGroups/{class_id}",
        ]
        diagnostics: list[dict[str, Any]] = []
        for endpoint in endpoints:
            result = self.request("GET", endpoint)
            diagnostics.append({"endpoint": endpoint, "ok": result.ok, "status": result.status, "error": result.error[:300] if result.error else ""})
            if result.ok:
                item = _class_item_from_any(result.data)
                if item:
                    if isinstance(result.data, dict):
                        result.data.setdefault("_endpoint", endpoint)
                        result.data.setdefault("_diagnostics", diagnostics)
                    return result
        # Fallback: get all classes and find the ID locally.
        all_result = self.get_classes("limit=1000")
        if all_result.ok:
            for item in extract_items(all_result.data):
                if isinstance(item, dict) and str(_class_id_value(item)) == class_id:
                    return MoyKlassResult(True, data=item, status=all_result.status, endpoint=all_result.endpoint)
        return MoyKlassResult(False, data={"_diagnostics": diagnostics}, status=0, error="Группа не найдена по доступным endpoint МойКласс", endpoint="/v1/company/classes/{id}")

    def get_class_lessons(self, class_id: int | str, days: int = 30, limit: int = 120) -> MoyKlassResult:
        class_id = str(class_id).strip()
        if not class_id.isdigit():
            return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/lessons")
        # Direct variants first.
        today = date.today()
        future = today + timedelta(days=max(1, int(days or 30)))
        variants = [
            {"classId": class_id, "dateFrom": today.isoformat(), "dateTo": future.isoformat(), "limit": str(limit)},
            {"groupId": class_id, "dateFrom": today.isoformat(), "dateTo": future.isoformat(), "limit": str(limit)},
            {"classId": class_id, "limit": str(limit)},
            {"groupId": class_id, "limit": str(limit)},
        ]
        diagnostics: list[dict[str, Any]] = []
        for params in variants:
            result = self._enrich_lesson_result(self.request("GET", "/v1/company/lessons", params=params))
            items = extract_items(result.data) if result.ok else []
            filtered = [x for x in items if isinstance(x, dict) and str(_pick(x, ("classId", "groupId"))) == class_id]
            diagnostics.append({"params": params, "ok": result.ok, "status": result.status, "count": len(items), "filtered": len(filtered), "error": result.error[:300] if result.error else ""})
            if filtered:
                filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
                return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(filtered[:limit]), "_diagnostics": diagnostics}, status=result.status, endpoint="/v1/company/lessons")
        # Reliable fallback: use already working upcoming scan and filter locally.
        scan = self.get_upcoming_lessons(days=days, limit=max(limit, 150))
        if scan.ok:
            filtered = [x for x in extract_items(scan.data) if isinstance(x, dict) and str(_pick(x, ("classId", "groupId"))) == class_id]
            filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
            return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(filtered[:limit]), "_diagnostics": diagnostics}, status=scan.status, endpoint="/v1/company/lessons")
        return scan

    def get_class_users(self, class_id: int | str, limit: int = 80) -> MoyKlassResult:
        """Try to read students of a concrete MoyKlass class/group.

        MoyKlass installations expose this relation differently, so this method tries
        several safe read-only endpoints and then several query-parameter variants.
        """
        class_id = str(class_id).strip()
        if not class_id.isdigit():
            return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/classes/{id}/users")
        endpoints = [
            f"/v1/company/classes/{class_id}/users",
            f"/v1/company/classes/{class_id}/students",
            f"/v1/company/groups/{class_id}/users",
            f"/v1/company/groups/{class_id}/students",
            f"/v1/company/courseClasses/{class_id}/users",
            f"/v1/company/courseClasses/{class_id}/students",
        ]
        diagnostics: list[dict[str, Any]] = []
        for endpoint in endpoints:
            result = self.request("GET", endpoint, params={"limit": str(limit)})
            count = len(extract_items(result.data)) if result.ok else 0
            diagnostics.append({"endpoint": endpoint, "ok": result.ok, "status": result.status, "count": count, "error": result.error[:300] if result.error else ""})
            if result.ok and count:
                if isinstance(result.data, dict):
                    result.data.setdefault("_endpoint", endpoint)
                    result.data.setdefault("_diagnostics", diagnostics)
                return result
        query_variants = [
            {"classId": class_id, "limit": str(limit)},
            {"groupId": class_id, "limit": str(limit)},
            {"class_id": class_id, "limit": str(limit)},
            {"group_id": class_id, "limit": str(limit)},
        ]
        for params in query_variants:
            result = self.request("GET", "/v1/company/users", params=params)
            count = len(extract_items(result.data)) if result.ok else 0
            diagnostics.append({"endpoint": "/v1/company/users", "params": params, "ok": result.ok, "status": result.status, "count": count, "error": result.error[:300] if result.error else ""})
            if result.ok and count:
                if isinstance(result.data, dict):
                    result.data.setdefault("_diagnostics", diagnostics)
                return result
        return MoyKlassResult(True, data={"users": [], "_diagnostics": diagnostics, "_note": "students_not_found_by_available_endpoints"}, status=200, endpoint="/v1/company/users")

    def get_lesson_by_id(self, lesson_id: int | str) -> MoyKlassResult:
        lesson_id = str(lesson_id).strip()
        if not lesson_id.isdigit():
            return MoyKlassResult(False, error="lesson_id должен быть числом", endpoint="/v1/company/lessons/{id}")
        return self._enrich_lesson_result(self.request("GET", f"/v1/company/lessons/{lesson_id}"))

    def update_lesson_fields(self, lesson_id: int | str, fields: dict[str, Any], method: str = "POST") -> MoyKlassResult:
        """Update a MoyKlass lesson by ID and verify the changed fields.

        Real test on Yellow Club MoyKlass showed the accepted format:
        POST /v1/company/lessons/{id} with a simple JSON body, for example
        {"comment": "..."}. PATCH/PUT returned RequestValidationError Parse error.
        """
        lesson_id = str(lesson_id).strip()
        if not lesson_id.isdigit():
            return MoyKlassResult(False, error="lesson_id должен быть числом", endpoint="/v1/company/lessons/{id}")
        clean_fields = {str(k): v for k, v in (fields or {}).items() if str(k).strip() and v is not None}
        if not clean_fields:
            return MoyKlassResult(False, error="Нет полей для обновления", endpoint=f"/v1/company/lessons/{lesson_id}")
        method = (method or "POST").strip().upper()
        if method not in {"POST", "PATCH", "PUT"}:
            return MoyKlassResult(False, error="Для обновления занятия разрешены только POST, PATCH или PUT", endpoint=f"/v1/company/lessons/{lesson_id}")

        path = f"/v1/company/lessons/{lesson_id}"
        result = self.request(method, path, payload=clean_fields)
        if not result.ok:
            return result

        check = self.get_lesson_by_id(lesson_id)
        check_item = self._extract_lesson_item(check.data) if check.ok else {}
        verified_fields: dict[str, Any] = {}
        not_verified: dict[str, Any] = {}
        for k, v in clean_fields.items():
            if check_item and str(check_item.get(k, "")) == str(v):
                verified_fields[k] = v
            else:
                not_verified[k] = v

        if not not_verified:
            return MoyKlassResult(
                True,
                data={
                    "verified": True,
                    "method": method,
                    "path": path,
                    "fields": verified_fields,
                    "lesson": check_item or result.data,
                },
                status=result.status,
                endpoint=path,
            )
        return MoyKlassResult(
            False,
            data={
                "verified": False,
                "method": method,
                "path": path,
                "sent_fields": clean_fields,
                "not_verified": not_verified,
                "api_response": result.data,
                "lesson_after_update": check_item or check.data,
            },
            status=result.status,
            error="Запрос принят API, но повторное чтение не подтвердило изменение полей",
            endpoint=path,
        )

    def _extract_lesson_item(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            if isinstance(data.get("lesson"), dict):
                return data["lesson"]
            items = extract_items(data)
            if len(items) == 1 and isinstance(items[0], dict):
                return items[0]
            # GET /lessons/{id} in MoyKlass can return the lesson object directly.
            if any(k in data for k in ("id", "date", "beginTime", "endTime", "classId")):
                return data
        return {}

    def _minimal_lesson_update_payload(self, lesson: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "date",
            "beginTime",
            "endTime",
            "filialId",
            "roomId",
            "classId",
            "status",
            "maxStudents",
            "topic",
            "description",
            "comment",
            "teacherIds",
        ):
            if key in lesson and lesson.get(key) is not None:
                payload[key] = lesson.get(key)
        payload.update(fields)
        return payload

    def probe_update_lesson_fields(self, lesson_id: int | str, fields: dict[str, Any]) -> MoyKlassResult:
        """Diagnostic safe writer for lessons.

        It tries several known REST shapes one by one and stops after the first
        verified success. Use only after explicit owner confirmation and only on
        a test/old lesson. Every attempt writes the same desired values.
        """
        lesson_id = str(lesson_id).strip()
        if not lesson_id.isdigit():
            return MoyKlassResult(False, error="lesson_id должен быть числом", endpoint="/v1/company/lessons/{id}")
        clean_fields = {str(k): v for k, v in (fields or {}).items() if str(k).strip() and v is not None}
        if not clean_fields:
            return MoyKlassResult(False, error="Нет полей для обновления", endpoint=f"/v1/company/lessons/{lesson_id}")

        current = self.get_lesson_by_id(lesson_id)
        current_item = self._extract_lesson_item(current.data) if current.ok else {}
        minimal = self._minimal_lesson_update_payload(current_item, clean_fields) if current_item else dict(clean_fields)

        # Ordered from least invasive to fuller payload.
        attempts: list[dict[str, Any]] = [
            {"name": "PATCH id simple", "method": "PATCH", "path": f"/v1/company/lessons/{lesson_id}", "payload": dict(clean_fields)},
            {"name": "PUT id simple", "method": "PUT", "path": f"/v1/company/lessons/{lesson_id}", "payload": dict(clean_fields)},
            {"name": "POST id simple", "method": "POST", "path": f"/v1/company/lessons/{lesson_id}", "payload": dict(clean_fields)},
            {"name": "PATCH id nested lesson", "method": "PATCH", "path": f"/v1/company/lessons/{lesson_id}", "payload": {"lesson": dict(clean_fields)}},
            {"name": "PUT id nested lesson", "method": "PUT", "path": f"/v1/company/lessons/{lesson_id}", "payload": {"lesson": dict(clean_fields)}},
            {"name": "PATCH id minimal full lesson", "method": "PATCH", "path": f"/v1/company/lessons/{lesson_id}", "payload": minimal},
            {"name": "PUT id minimal full lesson", "method": "PUT", "path": f"/v1/company/lessons/{lesson_id}", "payload": minimal},
            {"name": "POST collection with id", "method": "POST", "path": "/v1/company/lessons", "payload": {"id": int(lesson_id), **dict(clean_fields)}},
            {"name": "POST collection nested lesson", "method": "POST", "path": "/v1/company/lessons", "payload": {"lesson": {"id": int(lesson_id), **dict(clean_fields)}}},
            {"name": "POST update endpoint", "method": "POST", "path": f"/v1/company/lessons/{lesson_id}/update", "payload": dict(clean_fields)},
            {"name": "POST update collection", "method": "POST", "path": "/v1/company/lessons/update", "payload": {"id": int(lesson_id), **dict(clean_fields)}},
        ]

        attempt_results: list[dict[str, Any]] = []
        verified = False
        final_result: MoyKlassResult | None = None

        for attempt in attempts:
            result = self.request(attempt["method"], attempt["path"], payload=attempt["payload"])
            final_result = result
            verification_note = ""
            if result.ok:
                check = self.get_lesson_by_id(lesson_id)
                check_item = self._extract_lesson_item(check.data) if check.ok else {}
                if check_item and all(str(check_item.get(k, "")) == str(v) for k, v in clean_fields.items()):
                    verified = True
                    verification_note = "verified"
                elif check.ok:
                    verification_note = "request_ok_but_not_verified"
                else:
                    verification_note = f"request_ok_but_verification_failed:{check.status}"
            attempt_results.append({
                "name": attempt["name"],
                "method": attempt["method"],
                "path": attempt["path"],
                "payload": attempt["payload"],
                "ok": result.ok,
                "status": result.status,
                "error": result.error[:700] if result.error else "",
                "verification": verification_note,
                "response": result.data if result.ok else None,
            })
            if verified:
                return MoyKlassResult(
                    True,
                    data={"verified": True, "fields": clean_fields, "attempts": attempt_results},
                    status=result.status,
                    endpoint=attempt["path"],
                )

        status = final_result.status if final_result else 0
        error = final_result.error if final_result else "Все варианты завершились без результата"
        return MoyKlassResult(
            False,
            data={"verified": False, "fields": clean_fields, "attempts": attempt_results},
            status=status,
            error=error or "МойКласс не принял ни один формат записи",
            endpoint=f"/v1/company/lessons/{lesson_id}",
        )

    def raw_write(self, method: str, path: str, payload: dict[str, Any]) -> MoyKlassResult:
        method = (method or "").strip().upper()
        if method not in {"POST", "PATCH", "PUT", "DELETE"}:
            return MoyKlassResult(False, error="Разрешены только POST, PATCH, PUT, DELETE", endpoint=path)
        if method == "DELETE":
            # Keep DELETE technically possible for diagnostics, but never send a bodyless delete from convenience commands.
            return MoyKlassResult(False, error="DELETE отключён в агенте, чтобы случайно не удалить данные. Используйте только POST/PATCH/PUT.", endpoint=path)
        return self.request(method, path, payload=payload or {})

    def clear_lookup_cache(self) -> None:
        self._lookup_maps = None

    def _first_ok_lookup(self, paths: list[str], params: Optional[dict[str, Any]] = None) -> MoyKlassResult:
        last = MoyKlassResult(False, error="lookup endpoint was not tried")
        for path in paths:
            result = self.request("GET", path, params=params or {"limit": "1000"})
            last = result
            if result.ok and extract_items(result.data):
                return result
        return last

    def _id_name_map_from_result(self, result: MoyKlassResult, id_keys: tuple[str, ...], name_keys: tuple[str, ...]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        if not result.ok:
            return mapping
        for item in extract_items(result.data):
            if not isinstance(item, dict):
                continue
            item_id = _pick(item, id_keys)
            name = _pick(item, name_keys)
            if not name:
                first = _pick(item, ("firstName", "firstname", "name"))
                last = _pick(item, ("lastName", "lastname", "surname"))
                name = " ".join(x for x in [first, last] if x).strip()
            if item_id and name:
                mapping[str(item_id)] = str(name).strip()
        return mapping

    def _lookup_maps_cached(self) -> dict[str, dict[str, str]]:
        if self._lookup_maps is not None:
            return self._lookup_maps

        maps: dict[str, dict[str, str]] = {"classes": {}, "rooms": {}, "teachers": {}}

        classes_result = self._first_ok_lookup([
            "/v1/company/classes",
            "/v1/company/groups",
            "/v1/company/courseClasses",
        ])
        maps["classes"].update(self._id_name_map_from_result(
            classes_result,
            ("id", "classId", "groupId"),
            ("name", "title", "className", "groupName", "displayName"),
        ))

        rooms_result = self._first_ok_lookup([
            "/v1/company/rooms",
            "/v1/company/classrooms",
            "/v1/company/filialRooms",
        ])
        maps["rooms"].update(self._id_name_map_from_result(
            rooms_result,
            ("id", "roomId", "classroomId"),
            ("name", "title", "roomName", "classroomName", "displayName"),
        ))

        teachers_result = self.get_teachers("limit=300")
        maps["teachers"].update(self._id_name_map_from_result(
            teachers_result,
            ("id", "teacherId", "userId", "staffId"),
            ("fullName", "fio", "title", "name", "displayName"),
        ))

        manual = load_moyklass_manual_names()
        for key in ("classes", "rooms", "teachers"):
            maps[key].update({str(k): str(v) for k, v in manual.get(key, {}).items() if str(v).strip()})

        self._lookup_maps = maps
        return maps

    def _enrich_lesson_item(self, item: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            return item
        maps = self._lookup_maps_cached()
        class_id = _pick(item, ("classId", "groupId"))
        room_id = _pick(item, ("roomId", "classroomId"))
        teacher_ids = item.get("teacherIds")
        teacher_id = _pick(item, ("teacherId",))

        if class_id and maps["classes"].get(class_id):
            item["_prettyClassName"] = maps["classes"][class_id]
        elif class_id and not _pick(item, ("className", "groupName", "groupTitle", "classTitle")):
            fallback = _fallback_group_name_from_lesson(item)
            if fallback:
                item["_prettyClassName"] = fallback

        if room_id and maps["rooms"].get(room_id):
            item["_prettyRoomName"] = _normalize_room_name(maps["rooms"][room_id])

        resolved_teachers: list[str] = []
        if isinstance(teacher_ids, list):
            for tid in teacher_ids:
                name = maps["teachers"].get(str(tid))
                if name:
                    resolved_teachers.append(name)
        elif teacher_id and maps["teachers"].get(teacher_id):
            resolved_teachers.append(maps["teachers"][teacher_id])
        if resolved_teachers:
            item["_prettyTeacherNames"] = ", ".join(resolved_teachers)
        return item

    def _enrich_lesson_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._enrich_lesson_item(item) if isinstance(item, dict) else item for item in items]

    def _enrich_lesson_result(self, result: MoyKlassResult) -> MoyKlassResult:
        if not result.ok:
            return result
        data = result.data
        if isinstance(data, dict):
            if isinstance(data.get("lesson"), dict):
                data["lesson"] = self._enrich_lesson_item(data["lesson"])
            for key in ("lessons", "items", "data", "rows", "records"):
                if isinstance(data.get(key), list):
                    data[key] = self._enrich_lesson_items(data[key])
            if any(k in data for k in ("id", "date", "beginTime", "endTime", "classId")):
                data = self._enrich_lesson_item(data)
                result.data = data
        elif isinstance(data, list):
            result.data = self._enrich_lesson_items(data)
        return result



    def get_month_analytics(self, month: str | None = None) -> MoyKlassResult:
        """Build a monthly management report from MoyKlass.

        Uses official MoyKlass company API endpoints from openapi.json:
        - /v1/company/lessonRecords with date=YYYY-MM-DD,YYYY-MM-DD for attendance records
        - /v1/company/payments with date=YYYY-MM-DD,YYYY-MM-DD and optype=income for payments

        The API date array uses style=form, explode=false, so it must be sent as
        a comma-separated string, not as dateFrom/dateTo.
        """
        start, end, month_label = _month_bounds(month)
        if not start or not end:
            return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="analytics")

        classes_result = self.get_classes("limit=1000")
        class_items = [x for x in extract_items(classes_result.data) if isinstance(x, dict)] if classes_result.ok else []

        lessons_result = self.get_lessons_between(start, end, limit=2000)
        lesson_items = [x for x in extract_items(lessons_result.data) if isinstance(x, dict)] if lessons_result.ok else []

        # Official endpoint for attendance/records. It returns entries with visit=true/false.
        lesson_records_result = self._scan_lesson_records_for_month(start, end, limit=8000)
        lesson_record_items = [x for x in extract_items(lesson_records_result.data) if isinstance(x, dict)] if lesson_records_result.ok else []
        attended_records = [x for x in lesson_record_items if _truthy(x.get("visit")) and not _truthy(x.get("skip"))]
        missed_records = [x for x in lesson_record_items if x.get("visit") is not None and not _truthy(x.get("visit")) and not _truthy(x.get("skip"))]
        test_records = [x for x in lesson_record_items if _truthy(x.get("test"))]
        paid_lesson_records = [x for x in lesson_record_items if _truthy(x.get("paid"))]

        # Official endpoint for payments. Use only incoming payments for revenue.
        payments_result = self._scan_payments_for_month(start, end, limit=8000)
        payment_items_all = [x for x in extract_items(payments_result.data) if isinstance(x, dict)] if payments_result.ok else []
        payment_items = [x for x in payment_items_all if (str(x.get("optype") or "income").lower() == "income")]

        users_result = self._scan_users_for_month(start, end, limit=3000)
        user_items = [x for x in extract_items(users_result.data) if isinstance(x, dict)] if users_result.ok else []

        unique_attended_students = _unique_ids_from_records(attended_records, ("userId", "studentId", "clientId", "customerId", "idUser"))
        unique_record_students = _unique_ids_from_records(lesson_record_items, ("userId", "studentId", "clientId", "customerId", "idUser"))
        unique_users = _unique_ids_from_records(user_items, ("id", "userId", "studentId", "clientId"))

        payment_sum = _sum_payment_amounts(payment_items)
        paid_count = len(payment_items)

        lesson_statuses: dict[str, int] = {}
        for item in lesson_items:
            status = str(_pick(item, ("status", "statusId", "state")) or "не указан")
            lesson_statuses[status] = lesson_statuses.get(status, 0) + 1

        lesson_control = _build_lesson_control_analytics(lesson_items)

        data = {
            "month": month_label,
            "dateFrom": start.isoformat(),
            "dateTo": end.isoformat(),
            "groups": {
                "ok": classes_result.ok,
                "count": len(class_items),
                "status": classes_result.status,
                "endpoint": classes_result.endpoint,
                "error": classes_result.error[:500] if classes_result.error else "",
            },
            "lessons": {
                "ok": lessons_result.ok,
                "count": len(lesson_items),
                "statuses": lesson_statuses,
                "status": lessons_result.status,
                "endpoint": lessons_result.endpoint,
                "error": lessons_result.error[:500] if lessons_result.error else "",
            },
            "visits": {
                "ok": lesson_records_result.ok,
                "available": lesson_records_result.ok,
                "count": len(attended_records),
                "recordsTotal": len(lesson_record_items),
                "missed": len(missed_records),
                "test": len(test_records),
                "paidRecords": len(paid_lesson_records),
                "uniqueStudents": len(unique_attended_students),
                "uniqueStudentsByRecords": len(unique_record_students),
                "status": lesson_records_result.status,
                "endpoint": lesson_records_result.endpoint,
                "error": lesson_records_result.error[:500] if lesson_records_result.error else "",
                "diagnostics": (lesson_records_result.data or {}).get("_diagnostics", []) if isinstance(lesson_records_result.data, dict) else [],
            },
            "clients": {
                "source": "lessonRecords" if unique_record_students else ("users" if unique_users else "unavailable"),
                "uniqueByVisits": len(unique_attended_students),
                "uniqueByRecords": len(unique_record_students),
                "usersInMonth": len(unique_users),
            },
            "payments": {
                "ok": payments_result.ok,
                "available": payments_result.ok,
                "count": paid_count,
                "sum": payment_sum,
                "status": payments_result.status,
                "endpoint": payments_result.endpoint,
                "error": payments_result.error[:500] if payments_result.error else "",
                "diagnostics": (payments_result.data or {}).get("_diagnostics", []) if isinstance(payments_result.data, dict) else [],
            },
            "users": {
                "ok": users_result.ok,
                "count": len(user_items),
                "status": users_result.status,
                "endpoint": users_result.endpoint,
                "error": users_result.error[:500] if users_result.error else "",
            },
            "lessonControl": lesson_control,
        }
        return MoyKlassResult(True, data=data, status=200, endpoint="analytics")

    def probe_analytics_sources(self, month: str | None = None) -> MoyKlassResult:
        """Diagnostic read-only probe for attendance/payment endpoints.

        This does not change MoyKlass data. It helps identify which endpoints are
        available in a concrete MoyKlass account for visits/attendance and payments.
        """
        start, end, month_label = _month_bounds(month)
        if not start or not end:
            return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="analytics_probe")

        lessons_result = self.get_lessons_between(start, end, limit=30)
        lesson_items = [x for x in extract_items(lessons_result.data) if isinstance(x, dict)] if lessons_result.ok else []
        sample_lesson_id = ""
        sample_class_id = ""
        if lesson_items:
            sample_lesson_id = str(_pick(lesson_items[0], ("id", "lessonId")) or "")
            sample_class_id = str(_pick(lesson_items[0], ("classId", "groupId")) or "")

        date_range_value = self._api_date_range_value(start, end)
        common_date_params = {"dateFrom": start.isoformat(), "dateTo": end.isoformat(), "limit": "3"}
        alt_date_params = {"from": start.isoformat(), "to": end.isoformat(), "limit": "3"}
        official_date_params = {"date": date_range_value, "limit": "3"}

        attendance_tests: list[tuple[str, str, dict[str, Any]]] = []
        attendance_base = [
            "/v1/company/visits",
            "/v1/company/lessonVisits",
            "/v1/company/lesson-visits",
            "/v1/company/attendances",
            "/v1/company/attendance",
            "/v1/company/userLessons",
            "/v1/company/lessonUsers",
            "/v1/company/lessonStudents",
            "/v1/company/lesson-students",
        ]
        attendance_tests.append(("official lessonRecords date", "/v1/company/lessonRecords", {**official_date_params, "includeLessons": "true"}))
        attendance_tests.append(("official lessonRecords visited", "/v1/company/lessonRecords", {**official_date_params, "visit": "true", "includeLessons": "true"}))
        for endpoint in attendance_base:
            attendance_tests.append(("month dateFrom/dateTo", endpoint, dict(common_date_params)))
            attendance_tests.append(("month from/to", endpoint, dict(alt_date_params)))
            if sample_lesson_id:
                attendance_tests.append(("lessonId param", endpoint, {"lessonId": sample_lesson_id, "limit": "3"}))
                attendance_tests.append(("idLesson param", endpoint, {"idLesson": sample_lesson_id, "limit": "3"}))
        if sample_lesson_id:
            for endpoint in [
                f"/v1/company/lessons/{sample_lesson_id}/users",
                f"/v1/company/lessons/{sample_lesson_id}/students",
                f"/v1/company/lessons/{sample_lesson_id}/visits",
                f"/v1/company/lessons/{sample_lesson_id}/attendance",
                f"/v1/company/lessons/{sample_lesson_id}/attendances",
            ]:
                attendance_tests.append(("lesson nested", endpoint, {"limit": "3"}))

        payment_tests: list[tuple[str, str, dict[str, Any]]] = []
        payment_base = [
            "/v1/company/payments",
            "/v1/company/userPayments",
            "/v1/company/transactions",
            "/v1/company/finance/payments",
            "/v1/company/finance/transactions",
            "/v1/company/invoices",
            "/v1/company/bills",
            "/v1/company/orders",
            "/v1/company/sales",
        ]
        payment_tests.append(("official payments date income", "/v1/company/payments", {**official_date_params, "optype": "income"}))
        payment_tests.append(("official payments date all", "/v1/company/payments", dict(official_date_params)))
        for endpoint in payment_base:
            payment_tests.append(("month dateFrom/dateTo", endpoint, dict(common_date_params)))
            payment_tests.append(("month from/to", endpoint, dict(alt_date_params)))

        attendance_results = self._probe_endpoint_tests(attendance_tests, max_tests=60)
        payment_results = self._probe_endpoint_tests(payment_tests, max_tests=40)

        return MoyKlassResult(
            True,
            data={
                "month": month_label,
                "dateFrom": start.isoformat(),
                "dateTo": end.isoformat(),
                "sampleLessonId": sample_lesson_id,
                "sampleClassId": sample_class_id,
                "lessonsInMonth": len(lesson_items),
                "attendance": attendance_results,
                "payments": payment_results,
            },
            status=200,
            endpoint="analytics_probe",
        )

    def _probe_endpoint_tests(self, tests: list[tuple[str, str, dict[str, Any]]], max_tests: int = 50) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for label, endpoint, params in tests[:max_tests]:
            result = self.request("GET", endpoint, params=params)
            items = extract_items(result.data) if result.ok else []
            sample_keys: list[str] = []
            sample: Any = None
            if items and isinstance(items[0], dict):
                sample_keys = list(items[0].keys())[:18]
                sample = {k: items[0].get(k) for k in sample_keys[:10]}
            results.append({
                "label": label,
                "endpoint": endpoint,
                "params": params,
                "ok": result.ok,
                "status": result.status,
                "count": len(items),
                "sampleKeys": sample_keys,
                "sample": sample,
                "error": result.error[:350] if result.error else "",
            })
        return results

    def _scan_users_for_month(self, start: date, end: date, limit: int = 3000) -> MoyKlassResult:
        # Try direct created-date filters first; if ignored, the local filter will remove unrelated users.
        return self._scan_month_endpoint_variants(
            endpoints=["/v1/company/users"],
            start=start,
            end=end,
            limit=limit,
            date_keys=("createdAt", "created", "date", "registrationDate", "updatedAt"),
        )

    def _api_date_range_value(self, start: date, end: date) -> str:
        # MoyKlass date range params are arrays with explode=false; urlencode(list) is wrong here.
        # Send them as a comma-separated value: date=YYYY-MM-DD,YYYY-MM-DD.
        end_inclusive = end - timedelta(days=1)
        if end_inclusive < start:
            end_inclusive = start
        return f"{start.isoformat()},{end_inclusive.isoformat()}"

    def _scan_lesson_records_for_month(self, start: date, end: date, limit: int = 8000) -> MoyKlassResult:
        """Read official MoyKlass attendance records for the month.

        Endpoint from openapi.json: GET /v1/company/lessonRecords.
        Important params: date, visit, paid, test, includeLessons, includeBills.
        """
        page_size = 500
        max_pages = max(1, (int(limit or 8000) // page_size) + 2)
        endpoint = "/v1/company/lessonRecords"
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        diagnostics: list[dict[str, Any]] = []
        date_value = self._api_date_range_value(start, end)

        # First request without visit filter: it returns both attended and missed records.
        base_params = {
            "date": date_value,
            "includeLessons": "true",
            "includeBills": "true",
            "includeUserSubscriptions": "true",
        }
        for page in range(max_pages):
            params = {**base_params, "limit": str(page_size), "offset": str(page * page_size)}
            result = self.request("GET", endpoint, params=params)
            items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
            if page == 0:
                diagnostics.append({
                    "endpoint": endpoint,
                    "params": base_params,
                    "ok": result.ok,
                    "status": result.status,
                    "count": len(items),
                    "error": result.error[:250] if result.error else "",
                })
            if not result.ok:
                return MoyKlassResult(False, data={"items": collected, "_diagnostics": diagnostics}, status=result.status, error=result.error, endpoint=endpoint)
            if not items:
                break
            for item in items:
                rec_date = _record_lesson_date(item)
                if rec_date and start <= rec_date < end:
                    rec_id = str(_pick(item, ("id", "recordId", "lessonRecordId")) or json.dumps(item, sort_keys=True, ensure_ascii=False)[:240])
                    if rec_id not in seen:
                        collected.append(item)
                        seen.add(rec_id)
            if len(items) < page_size:
                break

        return MoyKlassResult(True, data={"lessonRecords": collected, "_endpoint": endpoint, "_diagnostics": diagnostics}, status=200, endpoint=endpoint)

    def _scan_payments_for_month(self, start: date, end: date, limit: int = 8000) -> MoyKlassResult:
        """Read official MoyKlass incoming payments for the month.

        Endpoint from openapi.json: GET /v1/company/payments.
        Important params: date=YYYY-MM-DD,YYYY-MM-DD and optype=income.
        """
        page_size = 500
        max_pages = max(1, (int(limit or 8000) // page_size) + 2)
        endpoint = "/v1/company/payments"
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        diagnostics: list[dict[str, Any]] = []
        date_value = self._api_date_range_value(start, end)
        base_params = {"date": date_value, "optype": "income", "appendInvoices": "true"}

        for page in range(max_pages):
            params = {**base_params, "limit": str(page_size), "offset": str(page * page_size)}
            result = self.request("GET", endpoint, params=params)
            items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
            if page == 0:
                diagnostics.append({
                    "endpoint": endpoint,
                    "params": base_params,
                    "ok": result.ok,
                    "status": result.status,
                    "count": len(items),
                    "error": result.error[:250] if result.error else "",
                })
            if not result.ok:
                return MoyKlassResult(False, data={"items": collected, "_diagnostics": diagnostics}, status=result.status, error=result.error, endpoint=endpoint)
            if not items:
                break
            for item in items:
                rec_date = _record_date(item, ("date", "paymentDate", "createdAt", "created", "paidAt"))
                if rec_date and start <= rec_date < end:
                    rec_id = str(_pick(item, ("id", "paymentId", "transactionId")) or json.dumps(item, sort_keys=True, ensure_ascii=False)[:240])
                    if rec_id not in seen:
                        collected.append(item)
                        seen.add(rec_id)
            if len(items) < page_size:
                break

        return MoyKlassResult(True, data={"payments": collected, "_endpoint": endpoint, "_diagnostics": diagnostics}, status=200, endpoint=endpoint)

    def _scan_month_endpoint_variants(self, endpoints: list[str], start: date, end: date, limit: int = 3000, date_keys: tuple[str, ...] = ("date", "createdAt")) -> MoyKlassResult:
        """Try endpoint/date/pagination variants and filter locally by date-like fields."""
        limit = max(50, min(int(limit or 3000), 5000))
        page_size = 200
        max_pages = max(1, (limit // page_size) + 2)
        diagnostics: list[dict[str, Any]] = []
        best_status = 0
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        date_param_variants = [
            {"dateFrom": start.isoformat(), "dateTo": end.isoformat()},
            {"beginDate": start.isoformat(), "endDate": end.isoformat()},
            {"startDate": start.isoformat(), "endDate": end.isoformat()},
            {"from": start.isoformat(), "to": end.isoformat()},
            {"date_from": start.isoformat(), "date_to": end.isoformat()},
            {"createdAtFrom": start.isoformat(), "createdAtTo": end.isoformat()},
            {"createdFrom": start.isoformat(), "createdTo": end.isoformat()},
        ]
        page_variants = [
            lambda page: {"limit": str(page_size), "offset": str(page * page_size)},
            lambda page: {"limit": str(page_size), "skip": str(page * page_size)},
            lambda page: {"limit": str(page_size), "page": str(page + 1)},
            lambda page: {"page": str(page + 1), "pageSize": str(page_size)},
        ]

        for endpoint in endpoints:
            endpoint_had_ok = False
            for date_params in date_param_variants:
                for make_page_params in page_variants:
                    seen_pages: set[str] = set()
                    for page in range(max_pages):
                        params = {**date_params, **make_page_params(page)}
                        result = self.request("GET", endpoint, params=params)
                        best_status = result.status or best_status
                        count = len(extract_items(result.data)) if result.ok else 0
                        if page == 0:
                            diagnostics.append({
                                "endpoint": endpoint,
                                "params": date_params,
                                "ok": result.ok,
                                "status": result.status,
                                "count": count,
                                "error": result.error[:250] if result.error else "",
                            })
                        if not result.ok:
                            break
                        endpoint_had_ok = True
                        items = [x for x in extract_items(result.data) if isinstance(x, dict)]
                        if not items:
                            break
                        sig = _records_page_signature(items)
                        if sig in seen_pages:
                            break
                        seen_pages.add(sig)
                        for item in items:
                            rec_date = _record_date(item, date_keys)
                            if rec_date and start <= rec_date < end:
                                rec_id = str(_pick(item, ("id", "visitId", "paymentId", "transactionId", "userId", "studentId")) or json.dumps(item, sort_keys=True, ensure_ascii=False)[:200])
                                if rec_id not in seen_ids:
                                    collected.append(item)
                                    seen_ids.add(rec_id)
                                    if len(collected) >= limit:
                                        return MoyKlassResult(True, data={"items": collected, "_endpoint": endpoint, "_diagnostics": diagnostics[-20:]}, status=result.status, endpoint=endpoint)
                        if count < page_size:
                            break
                if collected:
                    return MoyKlassResult(True, data={"items": collected, "_endpoint": endpoint, "_diagnostics": diagnostics[-20:]}, status=best_status or 200, endpoint=endpoint)
            if endpoint_had_ok and collected:
                return MoyKlassResult(True, data={"items": collected, "_endpoint": endpoint, "_diagnostics": diagnostics[-20:]}, status=best_status or 200, endpoint=endpoint)

        # Even if endpoints were unavailable, return ok=True with empty list so report can show partial data.
        return MoyKlassResult(True, data={"items": [], "_diagnostics": diagnostics[-25:]}, status=best_status, endpoint=",".join(endpoints[:3]))
    def get_today_params(self) -> dict[str, str]:
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        return {"dateFrom": today, "dateTo": tomorrow}

    def _parse_params(self, raw_args: str, default_limit: str = "10") -> dict[str, str]:
        raw_args = (raw_args or "").strip()
        if not raw_args:
            return {"limit": default_limit}
        # Supports: /mk_lessons limit=5 dateFrom=2026-06-13 dateTo=2026-06-14
        params: dict[str, str] = {}
        for part in raw_args.split():
            if "=" in part:
                key, value = part.split("=", 1)
                params[key.strip()] = value.strip()
        if params:
            params.setdefault("limit", default_limit)
            return params
        # For /mk_users Иван, try common search parameter names.
        return {"q": raw_args, "search": raw_args, "limit": default_limit}



def _month_bounds(value: str | None) -> tuple[Optional[date], Optional[date], str]:
    raw = str(value or "").strip()
    today = date.today()
    if not raw:
        year, month = today.year, today.month
    else:
        m = re.search(r"(20\d{2})[-.](\d{1,2})", raw)
        if not m:
            d = parse_date(raw)
            if not d:
                return None, None, raw
            year, month = d.year, d.month
        else:
            year, month = int(m.group(1)), int(m.group(2))
    if month < 1 or month > 12:
        return None, None, raw
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end, f"{year:04d}-{month:02d}"


def _record_date(item: dict[str, Any], keys: tuple[str, ...]) -> Optional[date]:
    for key in keys:
        value = item.get(key)
        if value in (None, ""):
            continue
        d = parse_date(str(value)[:10])
        if d:
            return d
    # Fallback: recursively inspect nested dictionaries for common date fields.
    for value in item.values():
        if isinstance(value, dict):
            d = _record_date(value, keys)
            if d:
                return d
    return None


def _record_lesson_date(item: dict[str, Any]) -> Optional[date]:
    """Return the date of a lesson record by its nested lesson date or direct date."""
    if not isinstance(item, dict):
        return None
    lesson = item.get("lesson")
    if isinstance(lesson, dict):
        d = _record_date(lesson, ("date", "lessonDate", "startDate", "createdAt"))
        if d:
            return d
    return _record_date(item, ("date", "lessonDate", "createdAt", "created"))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "да", "истина"}


def _records_page_signature(items: list[dict[str, Any]]) -> str:
    if not items:
        return "empty"
    def rec_sig(item: dict[str, Any]) -> str:
        return str(_pick(item, ("id", "visitId", "paymentId", "transactionId", "userId", "studentId")) or json.dumps(item, sort_keys=True, ensure_ascii=False)[:160])
    return f"{rec_sig(items[0])}|{rec_sig(items[-1])}|{len(items)}"


def _unique_ids_from_records(items: list[dict[str, Any]], keys: tuple[str, ...]) -> set[str]:
    result: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = _pick(item, keys)
        if value not in (None, ""):
            result.add(str(value))
            continue
        for nested in item.values():
            if isinstance(nested, dict):
                value = _pick(nested, keys)
                if value not in (None, ""):
                    result.add(str(value))
    return result


def _analytics_source_available(result: MoyKlassResult) -> bool:
    """Return False when all tested endpoints are clearly unavailable.

    Some MoyKlass endpoints return ok=True with empty data, which means the
    endpoint exists but no records were found for the period. If diagnostics are
    only 404/NotFound, the source should be displayed as unavailable rather than
    as an exact zero.
    """
    if not result.ok:
        return False
    items = extract_items(result.data)
    if items:
        return True
    data = result.data if isinstance(result.data, dict) else {}
    diagnostics = data.get("_diagnostics") or []
    if not diagnostics:
        return bool(result.status and result.status != 404)
    ok_diags = [d for d in diagnostics if d.get("ok")]
    if ok_diags:
        return True
    statuses = {int(d.get("status") or 0) for d in diagnostics}
    if statuses and statuses.issubset({404}):
        return False
    return bool(result.status and result.status != 404)


def _sum_payment_amounts(items: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in items:
        value = _pick(item, ("summa", "amount", "sum", "value", "paid", "price", "total", "money"))
        if isinstance(value, dict):
            value = _pick(value, ("amount", "sum", "value"))
        if value in (None, ""):
            continue
        text = str(value).replace(" ", "").replace(",", ".")
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if m:
            try:
                total += float(m.group(0))
            except Exception:
                pass
    return round(total, 2)



def _counter_add(counter: dict[str, int], key: str, fallback: str = "не указано") -> None:
    label = str(key or "").strip() or fallback
    counter[label] = counter.get(label, 0) + 1


def _top_counter_items(counter: dict[str, int], limit: int = 8) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:limit]
    ]


def _build_lesson_control_analytics(lessons: list[dict[str, Any]]) -> dict[str, Any]:
    """Build management analytics using only lesson fields that are already available.

    This does not rely on visits/payments endpoints. It is useful for operational
    CRM control: filling quality, distribution by teachers, classrooms and groups.
    """
    by_room: dict[str, int] = {}
    by_teacher: dict[str, int] = {}
    by_group: dict[str, int] = {}
    by_status_label: dict[str, int] = {}
    missing_topic: list[dict[str, Any]] = []
    missing_comment: list[dict[str, Any]] = []
    no_teacher: list[dict[str, Any]] = []
    no_room: list[dict[str, Any]] = []
    lessons_without_group: list[dict[str, Any]] = []

    for item in lessons:
        if not isinstance(item, dict):
            continue
        topic = _pick(item, ("topic", "name", "title"))
        comment = _pick(item, ("comment", "description"))
        room = _lesson_room_value(item)
        teacher = _lesson_teacher_value(item)
        group = _lesson_group_value(item)
        status_label = _lesson_status_value(item) or "не указан"

        _counter_add(by_room, room, "кабинет не указан")
        _counter_add(by_teacher, teacher, "преподаватель не указан")
        _counter_add(by_group, group, "группа не указана")
        _counter_add(by_status_label, status_label, "статус не указан")

        if not topic:
            missing_topic.append(item)
        if not comment:
            missing_comment.append(item)
        if not teacher:
            no_teacher.append(item)
        if not room:
            no_room.append(item)
        if not group:
            lessons_without_group.append(item)

    total = len([x for x in lessons if isinstance(x, dict)])
    return {
        "totalLessons": total,
        "missingTopic": len(missing_topic),
        "missingComment": len(missing_comment),
        "noTeacher": len(no_teacher),
        "noRoom": len(no_room),
        "noGroup": len(lessons_without_group),
        "byRoom": _top_counter_items(by_room, 8),
        "byTeacher": _top_counter_items(by_teacher, 10),
        "byGroup": _top_counter_items(by_group, 10),
        "byStatusLabel": _top_counter_items(by_status_label, 8),
        "examplesMissingTopic": _lesson_examples_for_report(missing_topic, 5),
        "examplesMissingComment": _lesson_examples_for_report(missing_comment, 5),
        "examplesNoTeacher": _lesson_examples_for_report(no_teacher, 5),
        "examplesNoRoom": _lesson_examples_for_report(no_room, 5),
    }


def _lesson_examples_for_report(lessons: list[dict[str, Any]], limit: int = 5) -> list[str]:
    examples: list[str] = []
    for item in lessons[:limit]:
        group = _lesson_group_value(item) or "группа не указана"
        date_value = _format_date_human(_lesson_date_value(item)) or _lesson_date_value(item) or "дата не указана"
        time_value = re.sub(r"\s*-\s*", "-", _lesson_time_value(item) or "") or "время не указано"
        examples.append(f"{group} | {date_value} | {time_value}")
    return examples


def _format_top_items(items: list[dict[str, Any]], empty: str = "нет данных", limit: int = 6) -> list[str]:
    if not items:
        return [f"- {empty}"]
    lines = []
    for item in items[:limit]:
        lines.append(f"- {item.get('name')}: {item.get('count')}")
    return lines


def _format_examples(items: list[str], empty: str = "нет") -> list[str]:
    if not items:
        return [f"- {empty}"]
    return [f"- {x}" for x in items]

def format_month_analytics_result(result: MoyKlassResult) -> str:
    if not result.ok:
        return f"Не удалось сформировать отчёт МойКласс.\nОшибка: {result.error or result.status}"
    data = result.data or {}
    month = data.get("month") or "месяц"
    groups = data.get("groups") or {}
    lessons = data.get("lessons") or {}
    visits = data.get("visits") or {}
    clients = data.get("clients") or {}
    payments = data.get("payments") or {}
    control = data.get("lessonControl") or {}

    visits_available = bool(visits.get("available", visits.get("ok")))
    payments_available = bool(payments.get("available", payments.get("ok")))

    visits_text = str(visits.get("count", 0)) if visits_available else "недоступно через API"
    unique_visits_text = _unique_visits_text(clients, visits) if visits_available else "недоступно"
    active_clients_text = _active_client_count_text(clients, visits) if visits_available else "недоступно"
    payments_text = str(payments.get("count", 0)) if payments_available else "недоступно через API"
    payments_sum_text = _money_text(payments.get("sum", 0)) if payments_available else "недоступно через API"

    missing_topic = int(control.get("missingTopic") or 0)
    missing_comment = int(control.get("missingComment") or 0)
    no_teacher = int(control.get("noTeacher") or 0)
    no_room = int(control.get("noRoom") or 0)

    lines = [
        f"📊 Управленческий отчёт МойКласс за {month}",
        "━━━━━━━━━━━━━━━━",
        "",
        "📌 Общие показатели",
        f"👥 Групп всего в базе: {groups.get('count', 0) if groups.get('ok') else 'не удалось получить'}",
        f"📚 Занятий в месяце: {lessons.get('count', 0) if lessons.get('ok') else 'не удалось получить'}",
        f"🆕 Новых учеников в базе за месяц: {clients.get('usersInMonth', 0)}",
        "",
        "✅ Посещения и клиенты",
        f"Посещений в месяце: {visits_text}",
        f"Записей на занятия в месяце: {visits.get('recordsTotal', 'недоступно') if visits_available else 'недоступно'}",
        f"Пропусков в месяце: {visits.get('missed', 'недоступно') if visits_available else 'недоступно'}",
        f"Уникальных учеников по посещениям: {unique_visits_text}",
        f"Активных клиентов / учеников за месяц: {active_clients_text}",
        "",
        "💳 Оплаты",
        f"Оплат за месяц: {payments_text}",
        f"Сумма оплат: {payments_sum_text}",
        "",
        "🧭 Контроль заполнения МК",
        f"🏷 Занятий без темы: {missing_topic}",
        f"💬 Занятий без комментария: {missing_comment}",
        f"👨‍🏫 Занятий без преподавателя: {no_teacher}",
        f"🏫 Занятий без кабинета: {no_room}",
    ]

    statuses_human = control.get("byStatusLabel") or []
    if statuses_human:
        lines += ["", "📌 Статусы занятий:"]
        lines += _format_top_items(statuses_human, limit=8)
    else:
        statuses = lessons.get("statuses") or {}
        if statuses:
            status_bits = ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items()))
            lines += ["", f"📌 Статусы занятий: {status_bits}"]

    lines += ["", "🏫 Занятия по кабинетам:"]
    lines += _format_top_items(control.get("byRoom") or [], limit=6)

    lines += ["", "👨‍🏫 Занятия по преподавателям:"]
    lines += _format_top_items(control.get("byTeacher") or [], limit=8)

    lines += ["", "👥 Топ групп по количеству занятий:"]
    lines += _format_top_items(control.get("byGroup") or [], limit=8)

    if missing_topic or missing_comment or no_teacher or no_room:
        lines += ["", "⚠️ Что проверить в МойКласс"]
        if missing_topic:
            lines.append("\nЗанятия без темы:")
            lines += _format_examples(control.get("examplesMissingTopic") or [])
        if missing_comment:
            lines.append("\nЗанятия без комментария:")
            lines += _format_examples(control.get("examplesMissingComment") or [])
        if no_teacher:
            lines.append("\nЗанятия без преподавателя:")
            lines += _format_examples(control.get("examplesNoTeacher") or [])
        if no_room:
            lines.append("\nЗанятия без кабинета:")
            lines += _format_examples(control.get("examplesNoRoom") or [])

    notes = []
    if not visits_available:
        notes.append("посещения: текущий API-ключ не отдаёт endpoint посещений; нужен рабочий endpoint или права API")
    elif visits.get("count", 0) == 0:
        notes.append("посещения: endpoint доступен, но записей за месяц не найдено")
    if not payments_available:
        notes.append("оплаты: текущий API-ключ не отдаёт endpoint оплат; нужен рабочий endpoint или права API")
    elif payments.get("count", 0) == 0:
        notes.append("оплаты: endpoint доступен, но оплат за месяц не найдено")
    if clients.get("source") == "lessonRecords":
        notes.append("активные клиенты посчитаны по уникальным ученикам в записях на занятия за месяц")
    elif clients.get("source") == "users":
        notes.append("новые ученики посчитаны по дате создания пользователей, это не равно активным клиентам месяца")
    elif clients.get("source") == "unavailable":
        notes.append("точный месячный счётчик активных клиентов недоступен без endpoint посещений/статусов")
    notes.append("этот отчёт использует только данные, которые МойКласс реально отдал через API")
    if notes:
        lines += ["", "ℹ️ Примечания:"]
        lines += [f"- {x}" for x in notes]

    lines += ["", "Команды:", "/mk_report 2026-06 - отчёт за месяц", "/mk_report_debug 2026-06 - диагностика текущего отчёта", "/mk_report_probe 2026-06 - поиск endpoint посещений и оплат"]
    return "\n".join(lines)


def _client_count_text(clients: dict[str, Any]) -> str:
    if clients.get("source") in {"visits", "lessonRecords"}:
        return str(clients.get("uniqueByRecords", clients.get("uniqueByVisits", 0)))
    return "не удалось точно определить"


def _unique_visits_text(clients: dict[str, Any], visits: dict[str, Any]) -> str:
    if visits.get("count", 0) > 0 or clients.get("source") in {"visits", "lessonRecords"}:
        return str(clients.get("uniqueByVisits", 0))
    return "недоступно"


def _active_client_count_text(clients: dict[str, Any], visits: dict[str, Any]) -> str:
    if clients.get("source") == "lessonRecords":
        return str(clients.get("uniqueByRecords", 0))
    if visits.get("count", 0) > 0 or clients.get("source") == "visits":
        return str(clients.get("uniqueByVisits", 0))
    return "недоступно"


def _money_text(value: Any) -> str:
    try:
        number = float(value or 0)
    except Exception:
        number = 0.0
    if number == 0:
        return "0"
    if number.is_integer():
        return str(int(number))
    return str(round(number, 2))


def format_month_analytics_debug(result: MoyKlassResult) -> str:
    if not result.ok:
        return f"Диагностика отчёта МК недоступна.\nОшибка: {result.error or result.status}"
    data = result.data or {}
    parts = [f"🔎 Диагностика отчёта МойКласс за {data.get('month', '')}"]
    for key in ("groups", "lessons", "visits", "payments", "users"):
        block = data.get(key) or {}
        parts.append("")
        parts.append(f"{key}:")
        parts.append(f"ok: {block.get('ok')}")
        parts.append(f"count: {block.get('count', block.get('uniqueStudents', ''))}")
        parts.append(f"endpoint: {block.get('endpoint', '')}")
        parts.append(f"status: {block.get('status', '')}")
        if block.get("error"):
            parts.append(f"error: {block.get('error')}")
        diags = block.get("diagnostics") or []
        if diags:
            parts.append("diagnostics:")
            for d in diags[:6]:
                parts.append(json.dumps(d, ensure_ascii=False)[:700])
    return "\n".join(parts)


def format_month_analytics_probe(result: MoyKlassResult) -> str:
    if not result.ok:
        return f"Диагностика endpoint МойКласс недоступна.\nОшибка: {result.error or result.status}"
    data = result.data or {}
    lines = [
        f"🔎 Поиск источников аналитики МойКласс за {data.get('month', '')}",
        "━━━━━━━━━━━━━━━━",
        f"Период: {data.get('dateFrom')} - {data.get('dateTo')}",
        f"Занятий найдено: {data.get('lessonsInMonth', 0)}",
        f"Тестовое занятие: {data.get('sampleLessonId') or 'не найдено'}",
        "",
        "✅ Что нужно искать:",
        "- endpoint, где status=200 и count больше 0;",
        "- sampleKeys покажет реальные поля, по ним можно правильно считать посещения/оплаты.",
        "",
        "Посещения / ученики:",
    ]
    lines += _format_probe_section(data.get("attendance") or [])
    lines += ["", "Оплаты:"]
    lines += _format_probe_section(data.get("payments") or [])
    lines += ["", "После этого пришлите ответ целиком или скрин. По рабочему endpoint можно будет сделать точный отчёт."]
    return "\n".join(lines)


def _format_probe_section(items: list[dict[str, Any]]) -> list[str]:
    good = [x for x in items if x.get("ok") and int(x.get("count") or 0) > 0]
    ok_empty = [x for x in items if x.get("ok") and int(x.get("count") or 0) == 0]
    errors = [x for x in items if not x.get("ok")]
    lines: list[str] = []
    if good:
        for x in good[:8]:
            lines.append(f"+ {x.get('endpoint')} | {x.get('label')} | count={x.get('count')}")
            if x.get("sampleKeys"):
                lines.append("  поля: " + ", ".join(x.get("sampleKeys")[:14]))
        return lines
    lines.append("Рабочий endpoint с данными пока не найден.")
    if ok_empty:
        lines.append("Пустые, но доступные endpoint:")
        for x in ok_empty[:8]:
            lines.append(f"- {x.get('endpoint')} | {x.get('label')} | status={x.get('status')} | count=0")
    if errors:
        lines.append("Ошибки/недоступные endpoint:")
        for x in errors[:8]:
            err = str(x.get("error") or "")[:90].replace("\n", " ")
            lines.append(f"- {x.get('endpoint')} | status={x.get('status')} | {err}")
    return lines


def _extract_id_value(v: Any) -> str:
    """Safely convert a scalar to a non-empty string ID."""
    if v is None or v == "" or isinstance(v, (list, dict)):
        return ""
    return str(v).strip()


def _extract_ids_from_teacher_value(value: Any) -> list[str]:
    """Extract IDs from any MoyKlass teacher-field value (scalar, list, dict, list-of-dicts)."""
    results: list[str] = []
    _ID_KEYS = ("id", "teacherId", "userId", "employeeId", "staffId", "managerId", "tutorId", "responsibleId")
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                for k in _ID_KEYS:
                    v = _extract_id_value(item.get(k))
                    if v and v not in results:
                        results.append(v)
            else:
                v = _extract_id_value(item)
                if v and v not in results:
                    results.append(v)
    elif isinstance(value, dict):
        for k in _ID_KEYS:
            v = _extract_id_value(value.get(k))
            if v and v not in results:
                results.append(v)
    else:
        v = _extract_id_value(value)
        if v:
            results.append(v)
    return results


def lesson_teacher_ids(item: dict[str, Any]) -> list[str]:
    """Extract all teacher IDs from a MoyKlass lesson object.

    MoyKlass installations use different field names. We check every known
    variant so that accounts that store teachers differently still work.
    All IDs are normalised to non-empty strings.
    """
    if not isinstance(item, dict):
        return []
    results: list[str] = []

    # Priority 1: flat ID fields (most common)
    for field in ("teacherIds", "teacher_ids", "teachersIds", "teacher_id_list",
                  "teacherId", "teacher_id"):
        val = item.get(field)
        if val is None:
            continue
        ids = _extract_ids_from_teacher_value(val)
        for i in ids:
            if i not in results:
                results.append(i)

    # Priority 2: structured teacher objects
    for field in ("teachers", "teacher", "staff", "staffs", "employees"):
        val = item.get(field)
        if val is None or val in ("", [], {}):
            continue
        ids = _extract_ids_from_teacher_value(val)
        for i in ids:
            if i not in results:
                results.append(i)

    # Priority 3: other scalar staff-like fields (last resort)
    if not results:
        for field in ("employeeId", "employeeIds", "staffId", "managerId",
                      "tutorId", "responsibleId"):
            val = item.get(field)
            if val is None:
                continue
            ids = _extract_ids_from_teacher_value(val)
            for i in ids:
                if i not in results:
                    results.append(i)

    return results


def lesson_has_teacher(item: dict[str, Any], teacher_id: str | int) -> bool:
    tid = str(teacher_id or "").strip()
    if not tid or not isinstance(item, dict):
        return False
    return tid in lesson_teacher_ids(item)


def filter_lessons_by_teacher(result: MoyKlassResult, teacher_id: str | int) -> MoyKlassResult:
    if not result.ok:
        return result
    tid = str(teacher_id or "").strip()
    if not tid:
        return MoyKlassResult(False, status=result.status, error="У сотрудника не указан teacherId МойКласс", endpoint=result.endpoint)
    items = extract_items(result.data)
    filtered = [item for item in items if isinstance(item, dict) and lesson_has_teacher(item, tid)]
    data: dict[str, Any] = {"lessons": filtered, "teacherId": tid}
    if isinstance(result.data, dict):
        for key in ("_range", "_usedParams", "_filterWarning", "_diagnostics"):
            if key in result.data:
                data[key] = result.data[key]
    return MoyKlassResult(True, data=data, status=result.status, endpoint=result.endpoint)


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def extract_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "users", "lessons", "lessonRecords", "payments", "invoices", "subscriptions", "joins", "classes", "result", "rows", "records"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = extract_items(value)
                if nested:
                    return nested
        values = list(data.values())
        if values and all(isinstance(v, dict) for v in values):
            return values
    return []


def preview_json(data: Any, max_chars: int = 2500) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        text = str(data)
    if len(text) > max_chars:
        return text[:max_chars] + "\n..."
    return text


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = d.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _compact_phone(value: str, full: bool = False) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if full:
        return value
    digits = re.sub(r"\D+", "", value)
    if len(digits) <= 4:
        return "****"
    return digits[:3] + "****" + digits[-4:]


def _lesson_date_value(item: dict[str, Any]) -> str:
    return _pick(item, ("date", "dateTime", "lessonDate", "startDate", "createdAt"))


def _lesson_time_value(item: dict[str, Any]) -> str:
    begin = _pick(item, ("beginTime", "startTime", "time"))
    end = _pick(item, ("endTime", "finishTime"))
    if begin and end:
        return f"{begin}-{end}"
    return begin or end


def _lesson_title_value(item: dict[str, Any]) -> str:
    return _pick(item, ("topic", "name", "title", "subjectName", "courseName", "groupName", "description"))


def format_teachers_result(result: MoyKlassResult, limit: int = 30) -> str:
    if not result.ok:
        return (
            f"МойКласс не вернул список преподавателей.\n"
            f"endpoint: {result.endpoint}\nstatus: {result.status}\n{result.error}\n\n"
            "Можно привязать преподавателя вручную по teacherIds из занятия. "
            "Открой занятие через /mk_lesson ID и возьми число из строки teacherIds."
        )
    items = extract_items(result.data)
    if not items:
        return "Ответ получен, но список преподавателей не найден. Используйте teacherIds из /mk_lesson ID.\n\n" + preview_json(result.data, 1500)
    lines = [f"Преподаватели из МойКласс: найдено {len(items)}"]
    for item in items[:limit]:
        if isinstance(item, dict):
            teacher_id = _pick(item, ("id", "teacherId", "userId"))
            first = _pick(item, ("firstName", "firstname", "name"))
            last = _pick(item, ("lastName", "lastname", "surname"))
            full_name = _pick(item, ("fullName", "fio", "title")) or " ".join(x for x in [first, last] if x).strip()
            username = _pick(item, ("username", "login", "email"))
            parts = [f"ID: {teacher_id}" if teacher_id else "", full_name, username]
            lines.append("- " + " | ".join(x for x in parts if x))
        else:
            lines.append("- " + str(item))
    lines.append("\nПривязать: /mk_link_teacher TELEGRAM_ID MK_TEACHER_ID")
    return "\n".join(lines)




def _class_id_value(item: dict[str, Any]) -> str:
    return _pick(item, ("id", "classId", "groupId", "courseClassId", "class_id", "group_id"))


def _class_name_value(item: dict[str, Any]) -> str:
    name = _pick(item, ("name", "title", "className", "groupName", "displayName", "courseName"))
    if name:
        return name
    item_id = _class_id_value(item)
    return f"Группа ID {item_id}" if item_id else "Группа без названия"


def _class_item_from_any(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        if isinstance(data.get("class"), dict):
            return data["class"]
        if isinstance(data.get("group"), dict):
            return data["group"]
        items = extract_items(data)
        if len(items) == 1 and isinstance(items[0], dict):
            return items[0]
        if any(k in data for k in ("id", "classId", "groupId", "name", "title", "className", "groupName")):
            return data
    return {}


def class_open_button_label(item: dict[str, Any], idx: int = 1) -> str:
    name = _class_name_value(item)
    item_id = _class_id_value(item)
    # Telegram button text limit is 64 bytes; keep it short.
    base = f"👥 {idx}. {name}"
    if len(base) > 58:
        base = base[:55].rstrip() + "..."
    return base if item_id else base


def format_classes_result(result: MoyKlassResult, limit: int = 30, title: str = "Группы МойКласс") -> str:
    if not result.ok:
        return f"МойКласс вернул ошибку.\nendpoint: {result.endpoint}\nstatus: {result.status}\n{result.error}"
    items = [x for x in extract_items(result.data) if isinstance(x, dict)]
    if not items:
        extra = ""
        if isinstance(result.data, dict) and result.data.get("_diagnostics"):
            extra = "\n\nДиагностика:\n" + preview_json(result.data.get("_diagnostics"), 1200)
        return f"{title}\n\nГруппы не найдены.{extra}"
    lines = [f"👥 {title}", f"Найдено групп: {len(items)}", ""]
    for idx, item in enumerate(items[:limit], start=1):
        item_id = _class_id_value(item)
        name = _class_name_value(item)
        status = _pick(item, ("status", "state", "active"))
        max_students = _pick(item, ("maxStudents", "maxUsers", "capacity"))
        parts = [f"{idx}. {name}"]
        if status:
            parts.append(f"статус: {status}")
        if max_students:
            parts.append(f"макс.: {max_students}")
        if item_id:
            parts.append(f"ID: {item_id}")
        lines.append(" | ".join(parts))
    if len(items) > limit:
        lines.append(f"\nПоказано {limit} из {len(items)}. Уточните поиск командой /mk_groups название")
    return "\n".join(lines).strip()


def format_class_preview(result: MoyKlassResult, title: str = "Карточка группы") -> str:
    if not result.ok:
        return f"{title}: не удалось получить данные. status={result.status} {result.error}".strip()
    item = _class_item_from_any(result.data)
    if not item:
        return f"{title}:\n" + preview_json(result.data, 1200)
    item_id = _class_id_value(item)
    name = _class_name_value(item)
    status = _pick(item, ("status", "state", "active"))
    course = _pick(item, ("courseName", "course", "subjectName", "programName"))
    max_students = _pick(item, ("maxStudents", "maxUsers", "capacity"))
    filial = _pick(item, ("filialId", "filialName", "branchName"))
    teacher = _pick(item, ("teacherName", "teacher", "teacherIds", "teachers"))
    lines = [
        f"📘 Группа: {_format_empty(name)}",
        "━━━━━━━━━━━━━━━━",
        "",
    ]
    if course:
        lines.append(f"📚 Курс: {course}")
    if teacher:
        lines.append(f"👨‍🏫 Преподаватель: {teacher}")
    if max_students:
        lines.append(f"👥 Максимум учеников: {max_students}")
    if filial:
        lines.append(f"🏫 Филиал: {filial}")
    if status:
        lines.append(f"📌 Статус: {status}")
    lines.extend([
        "",
        "✅ Группа выбрана.",
        "Можно открыть учеников группы или ближайшие занятия.",
    ])
    if item_id:
        lines.append(f"\nТехнически: classId {item_id}")
    return "\n".join(lines).strip()


def format_class_users_result(result: MoyKlassResult, limit: int = 40, include_contacts: bool = False) -> str:
    if not result.ok:
        return f"МойКласс вернул ошибку.\nendpoint: {result.endpoint}\nstatus: {result.status}\n{result.error}"
    items = extract_items(result.data)
    if not items:
        extra = ""
        if isinstance(result.data, dict) and result.data.get("_diagnostics"):
            extra = "\n\nПока API не отдал учеников группы через доступные endpoint. Диагностика:\n" + preview_json(result.data.get("_diagnostics"), 1600)
        return "👥 Ученики группы\n\nУченики не найдены." + extra
    lines = [f"👥 Ученики группы: найдено {len(items)}", ""]
    for idx, item in enumerate(items[:limit], start=1):
        if isinstance(item, dict):
            first = _pick(item, ("firstName", "firstname", "name"))
            last = _pick(item, ("lastName", "lastname", "surname"))
            full_name = _pick(item, ("fullName", "fio", "title")) or " ".join(x for x in [first, last] if x).strip()
            user_id = _pick(item, ("id", "userId"))
            phone = _pick(item, ("phone", "phoneNumber", "tel"))
            parts = [f"{idx}. {full_name or 'Без имени'}"]
            if user_id:
                parts.append(f"ID: {user_id}")
            if phone:
                parts.append(_compact_phone(phone, full=include_contacts))
            lines.append(" | ".join(parts))
        else:
            lines.append(f"{idx}. {item}")
    if len(items) > limit:
        lines.append(f"\nПоказано {limit} из {len(items)}.")
    if not include_contacts:
        lines.append("\nКонтакты скрыты. Полный вывод доступен только владельцу.")
    return "\n".join(lines).strip()

def format_users_result(result: MoyKlassResult, limit: int = 10, include_contacts: bool = False) -> str:
    if not result.ok:
        return f"МойКласс вернул ошибку.\nendpoint: {result.endpoint}\nstatus: {result.status}\n{result.error}"
    items = extract_items(result.data)
    if not items:
        return "Ответ получен, но список учеников не найден в стандартном формате.\n\nФрагмент ответа:\n" + preview_json(result.data)
    lines = [f"Ученики из МойКласс: найдено {len(items)}"]
    for item in items[:limit]:
        if isinstance(item, dict):
            first = _pick(item, ("firstName", "firstname", "name"))
            last = _pick(item, ("lastName", "lastname", "surname"))
            full_name = _pick(item, ("fullName", "fio")) or " ".join(x for x in [first, last] if x).strip()
            phone = _pick(item, ("phone", "phoneNumber", "tel"))
            email = _pick(item, ("email",))
            user_id = _pick(item, ("id", "userId"))
            contact_parts = []
            if phone:
                contact_parts.append(_compact_phone(phone, full=include_contacts))
            if include_contacts and email:
                contact_parts.append(email)
            parts = [f"ID: {user_id}" if user_id else "", full_name]
            if contact_parts:
                parts.append(" | ".join(contact_parts))
            extra = " | ".join(x for x in parts if x)
            lines.append("- " + (extra or preview_json(item, 300)))
        else:
            lines.append("- " + str(item))
    if not include_contacts:
        lines.append("\nКонтакты скрыты. Для полного вывода владельцу: /mk_users_full запрос")
    return "\n".join(lines)



def _format_date_human(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    raw = value[:10]
    d = parse_date(raw)
    if not d:
        return value
    weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return f"{d.strftime('%d.%m.%Y')} ({weekdays[d.weekday()]})"


def _format_empty(value: Any, fallback: str = "не указано") -> str:
    text = str(value or "").strip()
    return text if text else fallback


MOYKLASS_NAMES_PATH = Path("data") / "moyklass_names.json"


def _default_moyklass_names() -> dict[str, dict[str, str]]:
    return {"classes": {}, "rooms": {}, "teachers": {}}


def load_moyklass_manual_names() -> dict[str, dict[str, str]]:
    try:
        if not MOYKLASS_NAMES_PATH.exists():
            return _default_moyklass_names()
        data = json.loads(MOYKLASS_NAMES_PATH.read_text(encoding="utf-8"))
        result = _default_moyklass_names()
        if isinstance(data, dict):
            for key in result:
                value = data.get(key)
                if isinstance(value, dict):
                    result[key] = {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}
        return result
    except Exception:
        return _default_moyklass_names()


def save_moyklass_manual_name(kind: str, object_id: str | int, name: str) -> bool:
    kind_map = {
        "class": "classes",
        "classes": "classes",
        "group": "classes",
        "groups": "classes",
        "room": "rooms",
        "rooms": "rooms",
        "teacher": "teachers",
        "teachers": "teachers",
    }
    target = kind_map.get(str(kind or "").strip().lower())
    obj_id = str(object_id or "").strip()
    display_name = str(name or "").strip()
    if not target or not obj_id or not display_name:
        return False
    data = load_moyklass_manual_names()
    data.setdefault(target, {})[obj_id] = display_name
    MOYKLASS_NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOYKLASS_NAMES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def delete_moyklass_manual_name(kind: str, object_id: str | int) -> bool:
    kind_map = {"class": "classes", "group": "classes", "room": "rooms", "teacher": "teachers"}
    target = kind_map.get(str(kind or "").strip().lower())
    obj_id = str(object_id or "").strip()
    if not target or not obj_id:
        return False
    data = load_moyklass_manual_names()
    existed = obj_id in data.get(target, {})
    data.setdefault(target, {}).pop(obj_id, None)
    MOYKLASS_NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOYKLASS_NAMES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return existed


def format_moyklass_manual_names() -> str:
    data = load_moyklass_manual_names()
    labels = {"classes": "Группы", "rooms": "Кабинеты", "teachers": "Преподаватели"}
    lines = ["Сохранённые названия МойКласс"]
    has_any = False
    for key in ("classes", "rooms", "teachers"):
        lines.append("")
        lines.append(labels[key] + ":")
        if not data.get(key):
            lines.append("- не заполнено")
            continue
        has_any = True
        for obj_id, name in sorted(data[key].items(), key=lambda x: x[0]):
            lines.append(f"- {obj_id}: {name}")
    if not has_any:
        lines.append("\nМожно добавить вручную:")
        lines.append("/mk_name_class 709199 Вторник 17:00")
        lines.append("/mk_name_room 42756 Учебный класс YC1")
        lines.append("/mk_name_teacher 220305 Иван Иванов")
    return "\n".join(lines)


def _weekday_short_from_date(raw: str) -> str:
    d = parse_date(raw)
    if not d:
        return ""
    return ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][d.weekday()]


def _fallback_group_name_from_lesson(item: dict[str, Any]) -> str:
    weekday = _weekday_short_from_date(_lesson_date_value(item))
    time_value = _lesson_time_value(item)
    begin = time_value.split("-")[0].strip() if time_value else ""
    if weekday and begin:
        return f"Группа {weekday} {begin}"
    if begin:
        return f"Группа {begin}"
    return ""


def _normalize_room_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    low = text.lower()
    if "онлайн" in low or text.upper() == "YC0":
        return "Онлайн"
    m = re.search(r"\bYC\s*([123])\b", text, flags=re.IGNORECASE)
    if m:
        return f"Учебный класс YC{m.group(1)}"
    if text.upper() in {"YC1", "YC2", "YC3"}:
        return f"Учебный класс {text.upper()}"
    return text


def _lesson_group_value(item: dict[str, Any]) -> str:
    pretty = _pick(item, ("_prettyClassName",))
    if pretty:
        return pretty
    name = _pick(item, ("className", "groupName", "groupTitle", "classTitle", "courseName", "subjectName"))
    if name:
        return name
    fallback = _fallback_group_name_from_lesson(item)
    if fallback:
        return fallback
    class_id = _pick(item, ("classId", "groupId"))
    if class_id:
        return f"Группа ID {class_id}"
    return ""


def _lesson_room_value(item: dict[str, Any]) -> str:
    pretty = _pick(item, ("_prettyRoomName",))
    if pretty:
        return pretty
    name = _pick(item, ("roomName", "classroomName", "roomTitle", "classroomTitle"))
    if name:
        return _normalize_room_name(name)
    room_id = _pick(item, ("roomId", "classroomId"))
    if room_id and room_id in {"0", "None", "none"}:
        return "Онлайн"
    if room_id:
        return f"Кабинет ID {room_id}"
    return ""


def _lesson_teacher_value(item: dict[str, Any]) -> str:
    pretty = _pick(item, ("_prettyTeacherNames",))
    if pretty:
        return pretty
    teacher = _pick(item, ("teacherName", "teacher", "staffName", "teacherFullName"))
    teacher_ids = item.get("teacherIds")
    if teacher:
        return teacher
    if isinstance(teacher_ids, list) and teacher_ids:
        return "Преподаватель ID " + ", ".join(map(str, teacher_ids[:5]))
    teacher_id = _pick(item, ("teacherId",))
    if teacher_id:
        return f"Преподаватель ID {teacher_id}"
    return ""


def _lesson_status_value(item: dict[str, Any]) -> str:
    status = _pick(item, ("status",))
    mapping = {
        "0": "не проведено / запланировано",
        "1": "проведено",
        "2": "проведено",
        "3": "отменено",
    }
    if status in mapping:
        return mapping[status]
    return status


def _lesson_item_from_any(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        if isinstance(data.get("lesson"), dict):
            return data["lesson"]
        items = extract_items(data)
        if len(items) == 1 and isinstance(items[0], dict):
            return items[0]
        if any(k in data for k in ("id", "date", "beginTime", "endTime", "classId")):
            return data
    return {}


def extract_lesson_topic_from_result(result: MoyKlassResult) -> str:
    if not result.ok:
        return ""
    item = _lesson_item_from_any(result.data)
    if not item:
        return ""
    return _pick(item, ("topic", "name", "title", "subjectName", "courseName", "groupName", "description"))


def extract_lesson_id_from_result(result: MoyKlassResult) -> str:
    if not result.ok:
        return ""
    item = _lesson_item_from_any(result.data)
    if not item:
        return ""
    return _pick(item, ("id", "lessonId"))


def format_lesson_card_from_item(
    item: dict[str, Any],
    title: str = "Карточка занятия",
    *,
    include_open_hint: bool = False,
    include_technical: bool = False,
) -> str:
    lesson_id = _pick(item, ("id", "lessonId"))
    class_id = _pick(item, ("classId", "groupId"))
    room_id = _pick(item, ("roomId", "classroomId"))
    teacher_ids = item.get("teacherIds")
    teacher_id = _pick(item, ("teacherId",))

    date_value = _format_date_human(_lesson_date_value(item))
    time_value = re.sub(r"\s*-\s*", "-", _lesson_time_value(item))
    topic = _pick(item, ("topic", "name", "title"))
    comment = _pick(item, ("comment", "description"))
    group = _lesson_group_value(item)
    room = _lesson_room_value(item)
    teacher = _lesson_teacher_value(item)
    status = _lesson_status_value(item)

    raw_title = str(title or "").strip()
    auto_title = False
    if (
        not raw_title
        or "#" in raw_title
        or raw_title.lower().startswith(("карточка занятия", "моё занятие", "мое занятие", "выбранное занятие", "текущее занятие"))
    ):
        auto_title = True
        raw_title = "Занятие: " + _format_empty(group, "без названия")

    lines = [
        "📘 " + raw_title,
        "━━━━━━━━━━━━━━━━",
    ]
    if not auto_title:
        lines.append(f"👥 Группа: {_format_empty(group)}")
    lines.extend([
        f"📅 Дата: {_format_empty(date_value)}",
        f"⏰ Время: {_format_empty(time_value)}",
        f"🏫 Кабинет: {_format_empty(room)}",
        f"👨‍🏫 Преподаватель: {_format_empty(teacher)}",
        "",
        f"🏷 Тема: {_format_empty(topic)}",
        f"💬 Комментарий: {_format_empty(comment)}",
        f"📌 Статус: {_format_empty(status)}",
    ])

    if include_technical:
        technical = []
        if lesson_id:
            technical.append(f"занятие {lesson_id}")
        if class_id:
            technical.append(f"classId {class_id}")
        if room_id:
            technical.append(f"roomId {room_id}")
        if isinstance(teacher_ids, list) and teacher_ids:
            technical.append("teacherId " + ", ".join(map(str, teacher_ids[:5])))
        elif teacher_id:
            technical.append(f"teacherId {teacher_id}")
        if technical:
            lines.extend(["", "⚙️ Технически: " + " | ".join(technical)])

    if include_open_hint and lesson_id:
        lines.extend(["", f"Открыть карточку: /mk_lesson {lesson_id}"])
    return "\n".join(lines)


def lesson_open_button_label(item: dict[str, Any], index: int) -> str:
    """Human-readable reply-keyboard button for opening a lesson from a list."""
    if not isinstance(item, dict):
        return f"📘 Открыть занятие {index}"
    group = _lesson_group_value(item)
    time_value = re.sub(r"\s*-\s*", "-", _lesson_time_value(item))
    date_value = _format_date_human(_lesson_date_value(item))
    short_date = date_value.split()[0] if date_value else ""

    label = f"📘 {index}. "
    if group:
        label += group
    elif time_value:
        label += time_value
    elif short_date:
        label += short_date
    else:
        label += "Занятие"

    if time_value and time_value not in label:
        label += f" | {time_value}"
    if short_date and short_date not in label:
        label += f" | {short_date}"
    return label[:90]

def format_lesson_technical(result: MoyKlassResult, title: str = "Техническая информация занятия") -> str:
    if not result.ok:
        return f"{title}: не удалось получить данные. status={result.status} {result.error}".strip()
    item = _lesson_item_from_any(result.data)
    if not item:
        return f"{title}:\n" + preview_json(result.data, 1800)

    lesson_id = _pick(item, ("id", "lessonId"))
    class_id = _pick(item, ("classId", "groupId"))
    room_id = _pick(item, ("roomId", "classroomId"))
    teacher_ids = item.get("teacherIds")
    teacher_id = _pick(item, ("teacherId",))
    filial_id = _pick(item, ("filialId", "branchId"))
    status = _pick(item, ("status",))
    max_students = _pick(item, ("maxStudents",))

    if isinstance(teacher_ids, list):
        teacher_line = ", ".join(map(str, teacher_ids))
    else:
        teacher_line = teacher_id

    return "\n".join([
        "⚙️ " + str(title or "Техническая информация занятия"),
        "━━━━━━━━━━━━━━━━",
        f"ID занятия: {_format_empty(lesson_id)}",
        f"classId: {_format_empty(class_id)}",
        f"roomId: {_format_empty(room_id)}",
        f"filialId: {_format_empty(filial_id)}",
        f"status: {_format_empty(status)}",
        f"maxStudents: {_format_empty(max_students)}",
        f"teacherIds: {_format_empty(teacher_line)}",
        "",
        "Сырые данные:",
        preview_json(item, 2200),
    ])

def format_lessons_result(
    result: MoyKlassResult,
    limit: int = 20,
    title: str = "Занятия из МойКласс",
    *,
    include_open_hint: bool = True,
) -> str:
    if not result.ok:
        return f"МойКласс вернул ошибку.\nendpoint: {result.endpoint}\nstatus: {result.status}\n{result.error}"
    items = extract_items(result.data)
    if not items:
        extra = ""
        if isinstance(result.data, dict):
            warning = str(result.data.get("_filterWarning") or "").strip()
            if warning:
                extra = "\n\n" + warning
        return f"{title}\n\nЗанятий не найдено.{extra}"
    lines = [
        f"📚 {title}",
        f"Найдено занятий: {len(items)}",
        "",
    ]
    for idx, item in enumerate(items[:limit], start=1):
        if isinstance(item, dict):
            lines.append(format_lesson_card_from_item(item, title=f"Занятие {idx}", include_open_hint=include_open_hint))
        else:
            lines.append("━━━━━━━━━━━━━━\n" + str(item))
        lines.append("")
    if len(items) > limit:
        lines.append(f"Показано {limit} из {len(items)}. Уточните период или используйте поиск по дате.")
    return "\n".join(lines).strip()


def format_lesson_preview(result: MoyKlassResult, title: str = "Карточка занятия", *, include_technical: bool = False) -> str:
    if not result.ok:
        return f"{title}: не удалось получить данные. status={result.status} {result.error}".strip()
    item = _lesson_item_from_any(result.data)
    if not item:
        return f"{title}:\n" + preview_json(result.data, 1200)

    lines = [format_lesson_card_from_item(item, title=title, include_open_hint=False, include_technical=include_technical)]
    lesson_id = _pick(item, ("id", "lessonId"))
    lines.extend([
        "",
        "✅ Занятие выбрано.",
        "Выберите действие ниже.",
    ])
    if not lesson_id:
        lines.append("Если кнопки не сработают, откройте занятие по ID вручную.")
    return "\n".join(lines)


# --- Yellow Club v3.9.6: actionable management report helpers ---
def _mk_lesson_issue_predicate(item: dict[str, Any], issue: str) -> bool:
    issue = (issue or "").strip().lower()
    if issue in {"missing_topic", "topic", "no_topic"}:
        return not bool(_pick(item, ("topic", "name", "title")).strip())
    if issue in {"missing_comment", "comment", "no_comment"}:
        return not bool(_pick(item, ("comment", "description")).strip())
    if issue in {"no_teacher", "missing_teacher", "teacher"}:
        ids = item.get("teacherIds")
        return not bool(ids or _pick(item, ("teacherId", "teacherName", "teacher")))
    if issue in {"no_room", "missing_room", "room"}:
        return not bool(_pick(item, ("roomId", "classroomId", "roomName", "classroomName")))
    return False


def _mk_issue_label(issue: str) -> str:
    issue = (issue or "").strip().lower()
    labels = {
        "missing_topic": "занятия без темы",
        "topic": "занятия без темы",
        "no_topic": "занятия без темы",
        "missing_comment": "занятия без комментария",
        "comment": "занятия без комментария",
        "no_comment": "занятия без комментария",
        "no_teacher": "занятия без преподавателя",
        "missing_teacher": "занятия без преподавателя",
        "teacher": "занятия без преподавателя",
        "no_room": "занятия без кабинета",
        "missing_room": "занятия без кабинета",
        "room": "занятия без кабинета",
    }
    return labels.get(issue, "занятия для проверки")


def _mk_breakdown_label(kind: str) -> str:
    kind = (kind or "").strip().lower()
    return {
        "teacher": "по преподавателям",
        "teachers": "по преподавателям",
        "group": "по группам",
        "groups": "по группам",
        "room": "по кабинетам",
        "rooms": "по кабинетам",
        "status": "по статусам",
        "statuses": "по статусам",
    }.get(kind, "сводка")


def _mk_breakdown_key(kind: str) -> str:
    kind = (kind or "").strip().lower()
    if kind in {"teacher", "teachers"}:
        return "byTeacher"
    if kind in {"group", "groups"}:
        return "byGroup"
    if kind in {"room", "rooms"}:
        return "byRoom"
    if kind in {"status", "statuses"}:
        return "byStatusLabel"
    return "byGroup"


def _mk_get_month_issue_lessons(self: MoyKlassClient, month: str | None = None, issue: str = "missing_topic", limit: int = 2000) -> MoyKlassResult:
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="analytics_issue")
    lessons_result = self.get_lessons_between(start, end, limit=limit)
    if not lessons_result.ok:
        return lessons_result
    items = [x for x in extract_items(lessons_result.data) if isinstance(x, dict)]
    filtered = [x for x in items if _mk_lesson_issue_predicate(x, issue)]
    filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x), _lesson_group_value(x)))
    return MoyKlassResult(
        True,
        data={
            "month": month_label,
            "dateFrom": start.isoformat(),
            "dateTo": end.isoformat(),
            "issue": issue,
            "issueLabel": _mk_issue_label(issue),
            "totalLessons": len(items),
            "count": len(filtered),
            "lessons": filtered,
        },
        status=200,
        endpoint="analytics_issue",
    )


def _mk_get_month_lesson_breakdown(self: MoyKlassClient, month: str | None = None, kind: str = "teacher", limit: int = 2000) -> MoyKlassResult:
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="analytics_breakdown")
    lessons_result = self.get_lessons_between(start, end, limit=limit)
    if not lessons_result.ok:
        return lessons_result
    items = [x for x in extract_items(lessons_result.data) if isinstance(x, dict)]
    control = _build_lesson_control_analytics(items)
    key = _mk_breakdown_key(kind)
    return MoyKlassResult(
        True,
        data={
            "month": month_label,
            "dateFrom": start.isoformat(),
            "dateTo": end.isoformat(),
            "kind": kind,
            "label": _mk_breakdown_label(kind),
            "totalLessons": len(items),
            "items": control.get(key) or [],
        },
        status=200,
        endpoint="analytics_breakdown",
    )


MoyKlassClient.get_month_issue_lessons = _mk_get_month_issue_lessons
MoyKlassClient.get_month_lesson_breakdown = _mk_get_month_lesson_breakdown


def format_month_issue_lessons_result(result: MoyKlassResult, limit: int = 20) -> str:
    if not result.ok:
        return f"Не удалось получить список задач МойКласс.\nОшибка: {result.error or result.status}"
    data = result.data or {}
    month = data.get("month") or "месяц"
    label = data.get("issueLabel") or "занятия для проверки"
    count = int(data.get("count") or 0)
    total = int(data.get("totalLessons") or 0)
    items = [x for x in extract_items(data) if isinstance(x, dict)]
    lines = [
        f"⚠️ МойКласс: {label} за {month}",
        "━━━━━━━━━━━━━━━━",
        f"Всего занятий в месяце: {total}",
        f"Найдено для проверки: {count}",
        "",
    ]
    if not items:
        lines.append("Таких занятий не найдено.")
        return "\n".join(lines).strip()
    lines.append("Откройте занятие кнопкой ниже и исправьте тему/комментарий прямо из карточки.")
    lines.append("")
    for idx, item in enumerate(items[:limit], start=1):
        group = _lesson_group_value(item) or "группа не указана"
        date_value = _format_date_human(_lesson_date_value(item)) or "дата не указана"
        time_value = re.sub(r"\s*-\s*", "-", _lesson_time_value(item) or "время не указано")
        topic = _pick(item, ("topic", "name", "title")) or "не указано"
        comment = _pick(item, ("comment", "description")) or "не указано"
        lines.append(f"{idx}. {group} | {date_value} | {time_value}")
        if "тем" in label:
            lines.append(f"   Тема: {topic}")
        if "коммент" in label:
            lines.append(f"   Комментарий: {comment}")
    if len(items) > limit:
        lines.append(f"\nПоказано {limit} из {len(items)}. Используйте команду с месяцем или открывайте список частями.")
    return "\n".join(lines).strip()


def format_month_lesson_breakdown_result(result: MoyKlassResult, limit: int = 20) -> str:
    if not result.ok:
        return f"Не удалось получить сводку МойКласс.\nОшибка: {result.error or result.status}"
    data = result.data or {}
    month = data.get("month") or "месяц"
    label = data.get("label") or "сводка"
    total = int(data.get("totalLessons") or 0)
    items = data.get("items") or []
    lines = [
        f"📊 МойКласс: {label} за {month}",
        "━━━━━━━━━━━━━━━━",
        f"Всего занятий в месяце: {total}",
        "",
    ]
    if not items:
        lines.append("Данных для сводки нет.")
        return "\n".join(lines).strip()
    for idx, item in enumerate(items[:limit], start=1):
        if isinstance(item, dict):
            lines.append(f"{idx}. {item.get('name')}: {item.get('count')}")
        else:
            lines.append(f"{idx}. {item}")
    if len(items) > limit:
        lines.append(f"\nПоказано {limit} из {len(items)}.")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# v3.9.8 official MoyKlass API sync helpers
# These monkey-patches keep backward compatibility with older modules while
# forcing the core CRM operations to use the official OpenAPI paths/params:
# classes, joins, lessonRecords, payments, lessons(date=array style).
# ---------------------------------------------------------------------------

def _v398_date_range_value(start: date, end: date) -> str:
    end_inclusive = end - timedelta(days=1)
    if end_inclusive < start:
        end_inclusive = start
    return f"{start.isoformat()},{end_inclusive.isoformat()}"


def _v398_get_lessons_between(self: MoyKlassClient, date_from: str | date, date_to: str | date, limit: int = 120) -> MoyKlassResult:
    d1 = parse_date(date_from) if isinstance(date_from, str) else date_from
    d2 = parse_date(date_to) if isinstance(date_to, str) else date_to
    if not d1 or not d2:
        return MoyKlassResult(False, error="Неверная дата. Формат: YYYY-MM-DD", endpoint="/v1/company/lessons")
    if d2 <= d1:
        d2 = d1 + timedelta(days=1)
    limit_int = max(int(limit or 120), 80)
    endpoint = "/v1/company/lessons"
    date_value = _v398_date_range_value(d1, d2)
    diagnostics: list[dict[str, Any]] = []

    # Official OpenAPI shape: date is an array with explode=false, therefore comma-separated.
    official_params = {"date": date_value, "limit": str(limit_int), "offset": "0"}
    official = self.request("GET", endpoint, params=official_params)
    official_items = [x for x in extract_items(official.data) if isinstance(x, dict)] if official.ok else []
    filtered = []
    for item in official_items:
        ld = parse_date(_lesson_date_value(item))
        if ld and d1 <= ld < d2:
            filtered.append(item)
    diagnostics.append({
        "strategy": "official date",
        "params": official_params,
        "ok": official.ok,
        "status": official.status,
        "count": len(official_items),
        "filtered": len(filtered),
        "error": official.error[:250] if official.error else "",
    })
    if filtered or (official.ok and not official_items):
        filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
        return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(filtered), "_range": {"from": d1.isoformat(), "to": d2.isoformat()}, "_usedParams": official_params, "_diagnostics": diagnostics}, status=official.status, endpoint=endpoint)

    # Fallback for large accounts: official date + pagination.
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_size = 500
    max_pages = max(1, (limit_int // page_size) + 4)
    for page in range(max_pages):
        params = {"date": date_value, "limit": str(page_size), "offset": str(page * page_size)}
        result = self.request("GET", endpoint, params=params)
        items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
        if page == 0:
            diagnostics.append({"strategy": "official date pages", "params": {"date": date_value, "limit": page_size}, "ok": result.ok, "status": result.status, "count": len(items), "error": result.error[:250] if result.error else ""})
        if not result.ok:
            break
        if not items:
            break
        for item in items:
            ld = parse_date(_lesson_date_value(item))
            if ld and d1 <= ld < d2:
                lesson_id = str(_pick(item, ("id", "lessonId")) or json.dumps(item, ensure_ascii=False, sort_keys=True)[:160])
                if lesson_id not in seen:
                    collected.append(item)
                    seen.add(lesson_id)
        if len(items) < page_size or len(collected) >= limit_int:
            break
    if collected:
        collected.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
        return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(collected[:limit_int]), "_range": {"from": d1.isoformat(), "to": d2.isoformat()}, "_usedParams": {"date": date_value, "pagination": "offset"}, "_diagnostics": diagnostics}, status=200, endpoint=endpoint)

    # Last fallback: keep the older page scan because some accounts returned old data when date was ignored.
    scan = self._scan_lessons_between_by_pages(d1, d2, limit=limit_int)
    if scan.ok and extract_items(scan.data):
        if isinstance(scan.data, dict):
            scan.data.setdefault("_diagnostics", diagnostics + (scan.data.get("_scanDiagnostics") or []))
        return scan
    return MoyKlassResult(True, data={"lessons": [], "_range": {"from": d1.isoformat(), "to": d2.isoformat()}, "_diagnostics": diagnostics, "_filterWarning": "По выбранному периоду занятия не найдены. Старые занятия скрыты."}, status=official.status, endpoint=endpoint)


def _v398_get_classes(self: MoyKlassClient, raw_args: str = "") -> MoyKlassResult:
    params = self._parse_params(raw_args, default_limit="80")
    params.setdefault("includeStats", "true")
    params.setdefault("includeJoinsStats", "true")
    return self.request("GET", "/v1/company/classes", params=params)


def _v398_get_class_by_id(self: MoyKlassClient, class_id: int | str) -> MoyKlassResult:
    class_id = str(class_id).strip()
    if not class_id.isdigit():
        return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/classes/{id}")
    return self.request("GET", f"/v1/company/classes/{class_id}", params={"includeStats": "true", "includeJoinsStats": "true", "includeDescription": "true"})


def _v398_get_class_lessons(self: MoyKlassClient, class_id: int | str, days: int = 30, limit: int = 120) -> MoyKlassResult:
    class_id = str(class_id).strip()
    if not class_id.isdigit():
        return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/lessons")
    today = date.today()
    future = today + timedelta(days=max(1, int(days or 30)))
    date_value = _v398_date_range_value(today, future)
    result = self.request("GET", "/v1/company/lessons", params={"classId": class_id, "date": date_value, "limit": str(max(limit, 120))})
    items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
    filtered = [x for x in items if str(_pick(x, ("classId", "groupId"))) == class_id]
    if result.ok:
        filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
        return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(filtered[:limit]), "_usedParams": {"classId": class_id, "date": date_value}}, status=result.status, endpoint="/v1/company/lessons")
    return result


def _v398_get_joins(self: MoyKlassClient, raw_args: str = "") -> MoyKlassResult:
    params = self._parse_params(raw_args, default_limit="80")
    return self.request("GET", "/v1/company/joins", params=params)


def _v398_get_class_users(self: MoyKlassClient, class_id: int | str, limit: int = 80) -> MoyKlassResult:
    class_id = str(class_id).strip()
    if not class_id.isdigit():
        return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/joins")
    joins_result = self.request("GET", "/v1/company/joins", params={"classId": class_id, "limit": str(max(limit, 80))})
    joins = [x for x in extract_items(joins_result.data) if isinstance(x, dict)] if joins_result.ok else []
    user_ids: list[str] = []
    for join in joins:
        uid = _pick(join, ("userId", "studentId", "clientId", "idUser"))
        if uid and str(uid) not in user_ids:
            user_ids.append(str(uid))
    users: list[dict[str, Any]] = []
    diagnostics = [{"endpoint": "/v1/company/joins", "ok": joins_result.ok, "status": joins_result.status, "count": len(joins), "error": joins_result.error[:250] if joins_result.error else ""}]
    # Read user cards one by one: official /users supports userIds, but single GET is simpler and stable for small groups.
    for uid in user_ids[:limit]:
        ures = self.request("GET", f"/v1/company/users/{uid}", params={"includeJoins": "true"})
        diagnostics.append({"endpoint": f"/v1/company/users/{uid}", "ok": ures.ok, "status": ures.status})
        if ures.ok and isinstance(ures.data, dict):
            users.append(ures.data)
    return MoyKlassResult(True, data={"users": users, "joins": joins, "classId": class_id, "_endpoint": "/v1/company/joins", "_diagnostics": diagnostics[:12]}, status=joins_result.status or 200, endpoint="/v1/company/joins")


def _v398_get_month_lesson_records(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="/v1/company/lessonRecords")
    result = self._scan_lesson_records_for_month(start, end, limit=12000)
    if isinstance(result.data, dict):
        result.data.setdefault("month", month_label)
        result.data.setdefault("dateFrom", start.isoformat())
        result.data.setdefault("dateTo", end.isoformat())
    return result


def _v398_get_month_payments(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="/v1/company/payments")
    result = self._scan_payments_for_month(start, end, limit=12000)
    if isinstance(result.data, dict):
        result.data.setdefault("month", month_label)
        result.data.setdefault("dateFrom", start.isoformat())
        result.data.setdefault("dateTo", end.isoformat())
    return result


def _v398_get_lesson_records(self: MoyKlassClient, lesson_id: int | str, limit: int = 120) -> MoyKlassResult:
    lesson_id = str(lesson_id).strip()
    if not lesson_id.isdigit():
        return MoyKlassResult(False, error="lesson_id должен быть числом", endpoint="/v1/company/lessonRecords")
    result = self.request("GET", "/v1/company/lessonRecords", params={"lessonId": lesson_id, "includeLessons": "true", "includeBills": "true", "includeUserSubscriptions": "true", "limit": str(limit)})
    if result.ok and isinstance(result.data, dict):
        result.data.setdefault("lessonId", lesson_id)
    return result


def _v398_get_class_records_month(self: MoyKlassClient, class_id: int | str, month: str | None = None, limit: int = 3000) -> MoyKlassResult:
    class_id = str(class_id).strip()
    if not class_id.isdigit():
        return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/lessonRecords")
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="/v1/company/lessonRecords")
    date_value = _v398_date_range_value(start, end)
    result = self.request("GET", "/v1/company/lessonRecords", params={"classId": class_id, "date": date_value, "includeLessons": "true", "includeBills": "true", "includeUserSubscriptions": "true", "limit": str(limit)})
    if result.ok and isinstance(result.data, dict):
        result.data.setdefault("classId", class_id)
        result.data.setdefault("month", month_label)
    return result


def _v398_format_record_user(record: dict[str, Any]) -> str:
    for key in ("user", "student", "client"):
        value = record.get(key)
        if isinstance(value, dict):
            name = _pick(value, ("name", "fullName", "fio", "title"))
            if name:
                return name
    return str(_pick(record, ("userName", "studentName", "clientName", "name", "userId", "studentId", "clientId")) or "ученик")


def _v398_format_record_lesson(record: dict[str, Any]) -> str:
    lesson = record.get("lesson") if isinstance(record, dict) else None
    if isinstance(lesson, dict):
        d = _lesson_date_value(lesson)
        t1 = _lesson_time_value(lesson)
        t2 = str(_pick(lesson, ("endTime", "timeEnd", "end")) or "")
        group = _lesson_group_value(lesson)
        return " | ".join(x for x in [group, d, f"{t1}-{t2}" if t1 and t2 else t1] if x)
    return str(_pick(record, ("lessonId", "idLesson")) or "")


def format_lesson_records_result(result: MoyKlassResult, limit: int = 30) -> str:
    if not result.ok:
        return f"🧾 Записи на занятия недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    items = [x for x in extract_items(result.data) if isinstance(x, dict)]
    attended = [x for x in items if _truthy(x.get("visit")) and not _truthy(x.get("skip"))]
    missed = [x for x in items if x.get("visit") is not None and not _truthy(x.get("visit")) and not _truthy(x.get("skip"))]
    trial = [x for x in items if _truthy(x.get("test"))]
    paid = [x for x in items if _truthy(x.get("paid"))]
    unique_students = _unique_ids_from_records(items, ("userId", "studentId", "clientId", "customerId", "idUser"))
    unique_attended = _unique_ids_from_records(attended, ("userId", "studentId", "clientId", "customerId", "idUser"))
    lines = [
        f"🧾 Посещения / записи на занятия {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Всего записей: {len(items)}",
        f"Посещений visit=true: {len(attended)}",
        f"Пропусков visit=false: {len(missed)}",
        f"Пробных записей: {len(trial)}",
        f"Платных записей: {len(paid)}",
        f"Уникальных учеников по записям: {len(unique_students)}",
        f"Уникальных учеников по посещениям: {len(unique_attended)}",
    ]
    if items:
        lines += ["", "Примеры записей:"]
        for rec in items[:limit]:
            status = "посетил" if _truthy(rec.get("visit")) else ("пропуск" if rec.get("visit") is not None else "статус не указан")
            flags = []
            if _truthy(rec.get("test")):
                flags.append("пробное")
            if _truthy(rec.get("paid")):
                flags.append("платное")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"- {_v398_format_record_user(rec)} - {status}{suffix} | {_v398_format_record_lesson(rec)}")
    return "\n".join(lines)


def format_payments_result(result: MoyKlassResult, limit: int = 30) -> str:
    if not result.ok:
        return f"💳 Оплаты недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    items_all = [x for x in extract_items(result.data) if isinstance(x, dict)]
    items = [x for x in items_all if str(x.get("optype") or "income").lower() == "income"]
    total = _sum_payment_amounts(items)
    unique_students = _unique_ids_from_records(items, ("userId", "studentId", "clientId", "customerId", "idUser"))
    by_type: dict[str, int] = {}
    for item in items:
        typ = str(_pick(item, ("paymentTypeId", "type", "paymentType", "method")) or "не указан")
        by_type[typ] = by_type.get(typ, 0) + 1
    lines = [
        f"💳 Оплаты МойКласс {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Входящих оплат: {len(items)}",
        f"Сумма оплат: {_money_text(total)}",
        f"Уникальных учеников с оплатами: {len(unique_students)}",
    ]
    if by_type:
        lines += ["", "По типам оплаты:"]
        for key, count in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)[:10]:
            lines.append(f"- {key}: {count}")
    if items:
        lines += ["", "Последние оплаты:"]
        for p in items[:limit]:
            amount = _pick(p, ("summa", "amount", "sum", "value", "paid", "price", "total", "money")) or "0"
            d = str(_pick(p, ("date", "paymentDate", "createdAt", "paidAt")) or "")[:10]
            uid = _pick(p, ("userId", "studentId", "clientId")) or "-"
            lines.append(f"- {d} | ученик {uid} | {_money_text(amount)}")
    return "\n".join(lines)


def format_active_clients_result(result: MoyKlassResult, limit: int = 40) -> str:
    if not result.ok:
        return f"🧑‍🎓 Активные клиенты недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    items = [x for x in extract_items(result.data) if isinstance(x, dict)]
    by_user: dict[str, dict[str, Any]] = {}
    for rec in items:
        uid = str(_pick(rec, ("userId", "studentId", "clientId", "customerId", "idUser")) or "").strip()
        if not uid:
            continue
        entry = by_user.setdefault(uid, {"records": 0, "visits": 0, "missed": 0, "name": _v398_format_record_user(rec)})
        entry["records"] += 1
        if _truthy(rec.get("visit")):
            entry["visits"] += 1
        elif rec.get("visit") is not None:
            entry["missed"] += 1
    rows = sorted(by_user.items(), key=lambda kv: (kv[1].get("visits", 0), kv[1].get("records", 0)), reverse=True)
    lines = [
        f"🧑‍🎓 Активные клиенты {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Активных учеников по записям: {len(by_user)}",
        "",
        "Топ по посещениям / записям:",
    ]
    for uid, info in rows[:limit]:
        lines.append(f"- {info.get('name') or uid}: посещений {info.get('visits', 0)}, записей {info.get('records', 0)}, пропусков {info.get('missed', 0)}")
    if not rows:
        lines.append("Нет данных по записям за период.")
    return "\n".join(lines)


def format_moyklass_work_tasks_result(result: MoyKlassResult, limit: int = 20) -> str:
    if not result.ok:
        return f"📋 Задачи МойКласс недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data or {}
    control = data.get("lessonControl") or {}
    visits = data.get("visits") or {}
    payments = data.get("payments") or {}
    missing_topic = int(control.get("missingTopic") or 0)
    missing_comment = int(control.get("missingComment") or 0)
    no_teacher = int(control.get("noTeacher") or 0)
    no_room = int(control.get("noRoom") or 0)
    missed = int(visits.get("missed") or 0)
    payments_count = int(payments.get("count") or 0) if payments.get("available") else 0
    lines = [
        f"📋 Задачи по МойКласс за {data.get('month', '')}",
        "━━━━━━━━━━━━━━━━",
        "Что стоит проверить:",
        f"1. Занятия без темы: {missing_topic}",
        f"2. Занятия без комментария: {missing_comment}",
        f"3. Занятия без преподавателя: {no_teacher}",
        f"4. Занятия без кабинета: {no_room}",
        f"5. Пропуски по записям: {missed}",
        f"6. Оплат за месяц в МК: {payments_count}",
        "",
        "Команды для работы:",
        "/mk_missing_topics YYYY-MM - открыть занятия без темы",
        "/mk_missing_comments YYYY-MM - открыть занятия без комментария",
        "/mk_attendance YYYY-MM - посещения и пропуски",
        "/mk_payments YYYY-MM - оплаты",
        "/mk_active_clients YYYY-MM - активные ученики",
    ]
    if control.get("examplesMissingTopic"):
        lines += ["", "Первые занятия без темы:"]
        lines += _format_examples(control.get("examplesMissingTopic")[:limit])
    return "\n".join(lines)


# Apply official-method overrides.
MoyKlassClient.get_lessons_between = _v398_get_lessons_between
MoyKlassClient.get_classes = _v398_get_classes
MoyKlassClient.get_class_by_id = _v398_get_class_by_id
MoyKlassClient.get_class_lessons = _v398_get_class_lessons
MoyKlassClient.get_class_users = _v398_get_class_users
MoyKlassClient.get_joins = _v398_get_joins
MoyKlassClient.get_month_lesson_records = _v398_get_month_lesson_records
MoyKlassClient.get_month_payments = _v398_get_month_payments
MoyKlassClient.get_lesson_records = _v398_get_lesson_records
MoyKlassClient.get_class_records_month = _v398_get_class_records_month

# ---------------------------------------------------------------------------
# v3.9.8.1 date-array compatibility fix
# ---------------------------------------------------------------------------
# MoyKlass OpenAPI marks date filters as arrays of date strings. In practice the
# API validator can reject a comma-joined string as one invalid date. These
# overrides send date ranges as repeated query params first:
# date=YYYY-MM-DD&date=YYYY-MM-DD, then keep bracket/comma fallbacks for accounts
# that parse arrays differently.

def _v3981_date_values(start: date, end: date) -> list[str]:
    end_inclusive = end - timedelta(days=1)
    if end_inclusive < start:
        end_inclusive = start
    return [start.isoformat(), end_inclusive.isoformat()]


def _v3981_date_range_value(start: date, end: date) -> list[str]:
    return _v3981_date_values(start, end)


def _v3981_date_param_variants(start: date, end: date) -> list[dict[str, Any]]:
    start_s, end_s = _v3981_date_values(start, end)
    return [
        {"date": [start_s, end_s]},               # date=2026-06-01&date=2026-06-30
        {"date[]": [start_s, end_s]},             # date[]=2026-06-01&date[]=2026-06-30
        {"date[0]": start_s, "date[1]": end_s},  # date[0]=...&date[1]=...
        {"date": f"{start_s},{end_s}"},           # old comma fallback
    ]


def _v3981_date_params_debug(params: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            clean[key] = list(value)
        else:
            clean[key] = value
    return clean


def _v3981_is_date_validation_error(result: MoyKlassResult) -> bool:
    err = (result.error or "").lower()
    return result.status == 400 and "date" in err and "format" in err


def _v3981_scan_lesson_records_for_month(self: MoyKlassClient, start: date, end: date, limit: int = 8000) -> MoyKlassResult:
    page_size = 500
    max_pages = max(1, (int(limit or 8000) // page_size) + 2)
    endpoint = "/v1/company/lessonRecords"
    diagnostics: list[dict[str, Any]] = []
    last_error = ""
    last_status = 0

    for date_params in _v3981_date_param_variants(start, end):
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        base_params = {
            **date_params,
            "includeLessons": "true",
            "includeBills": "true",
            "includeUserSubscriptions": "true",
        }
        variant_ok = False
        for page in range(max_pages):
            params = {**base_params, "limit": str(page_size), "offset": str(page * page_size)}
            result = self.request("GET", endpoint, params=params)
            items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
            if page == 0:
                diagnostics.append({
                    "endpoint": endpoint,
                    "params": _v3981_date_params_debug(base_params),
                    "ok": result.ok,
                    "status": result.status,
                    "count": len(items),
                    "error": result.error[:250] if result.error else "",
                })
            if not result.ok:
                last_error = result.error
                last_status = result.status
                # Try the next date-array encoding if this one was rejected.
                if _v3981_is_date_validation_error(result):
                    break
                # Non-date errors are still worth trying with another variant, but keep diagnostics.
                break
            variant_ok = True
            if not items:
                break
            for item in items:
                rec_date = _record_lesson_date(item)
                if rec_date and start <= rec_date < end:
                    rec_id = str(_pick(item, ("id", "recordId", "lessonRecordId")) or json.dumps(item, sort_keys=True, ensure_ascii=False)[:240])
                    if rec_id not in seen:
                        collected.append(item)
                        seen.add(rec_id)
            if len(items) < page_size:
                break

        if variant_ok:
            return MoyKlassResult(
                True,
                data={"lessonRecords": collected, "_endpoint": endpoint, "_usedDateParams": _v3981_date_params_debug(date_params), "_diagnostics": diagnostics},
                status=200,
                endpoint=endpoint,
            )

    return MoyKlassResult(False, data={"lessonRecords": [], "_diagnostics": diagnostics}, status=last_status, error=last_error, endpoint=endpoint)


def _v3981_scan_payments_for_month(self: MoyKlassClient, start: date, end: date, limit: int = 8000) -> MoyKlassResult:
    page_size = 500
    max_pages = max(1, (int(limit or 8000) // page_size) + 2)
    endpoint = "/v1/company/payments"
    diagnostics: list[dict[str, Any]] = []
    last_error = ""
    last_status = 0

    for date_params in _v3981_date_param_variants(start, end):
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        base_params = {**date_params, "optype": "income", "appendInvoices": "true"}
        variant_ok = False
        for page in range(max_pages):
            params = {**base_params, "limit": str(page_size), "offset": str(page * page_size)}
            result = self.request("GET", endpoint, params=params)
            items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
            if page == 0:
                diagnostics.append({
                    "endpoint": endpoint,
                    "params": _v3981_date_params_debug(base_params),
                    "ok": result.ok,
                    "status": result.status,
                    "count": len(items),
                    "error": result.error[:250] if result.error else "",
                })
            if not result.ok:
                last_error = result.error
                last_status = result.status
                if _v3981_is_date_validation_error(result):
                    break
                break
            variant_ok = True
            if not items:
                break
            for item in items:
                rec_date = _record_date(item, ("date", "paymentDate", "createdAt", "created", "paidAt"))
                if rec_date and start <= rec_date < end:
                    rec_id = str(_pick(item, ("id", "paymentId", "transactionId")) or json.dumps(item, sort_keys=True, ensure_ascii=False)[:240])
                    if rec_id not in seen:
                        collected.append(item)
                        seen.add(rec_id)
            if len(items) < page_size:
                break

        if variant_ok:
            return MoyKlassResult(
                True,
                data={"payments": collected, "_endpoint": endpoint, "_usedDateParams": _v3981_date_params_debug(date_params), "_diagnostics": diagnostics},
                status=200,
                endpoint=endpoint,
            )

    return MoyKlassResult(False, data={"payments": [], "_diagnostics": diagnostics}, status=last_status, error=last_error, endpoint=endpoint)


def _v3981_get_lessons_between(self: MoyKlassClient, date_from: str | date, date_to: str | date, limit: int = 120) -> MoyKlassResult:
    d1 = parse_date(date_from) if isinstance(date_from, str) else date_from
    d2 = parse_date(date_to) if isinstance(date_to, str) else date_to
    if not d1 or not d2:
        return MoyKlassResult(False, error="Неверная дата. Формат: YYYY-MM-DD", endpoint="/v1/company/lessons")
    if d2 <= d1:
        d2 = d1 + timedelta(days=1)
    limit_int = max(int(limit or 120), 80)
    endpoint = "/v1/company/lessons"
    diagnostics: list[dict[str, Any]] = []

    for date_params in _v3981_date_param_variants(d1, d2):
        official_params = {**date_params, "limit": str(limit_int), "offset": "0"}
        official = self.request("GET", endpoint, params=official_params)
        official_items = [x for x in extract_items(official.data) if isinstance(x, dict)] if official.ok else []
        filtered = []
        for item in official_items:
            ld = parse_date(_lesson_date_value(item))
            if ld and d1 <= ld < d2:
                filtered.append(item)
        diagnostics.append({
            "strategy": "official date array",
            "params": _v3981_date_params_debug(official_params),
            "ok": official.ok,
            "status": official.status,
            "count": len(official_items),
            "filtered": len(filtered),
            "error": official.error[:250] if official.error else "",
        })
        if official.ok:
            if filtered or not official_items:
                filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
                return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(filtered), "_range": {"from": d1.isoformat(), "to": d2.isoformat()}, "_usedParams": _v3981_date_params_debug(official_params), "_diagnostics": diagnostics}, status=official.status, endpoint=endpoint)
            # If API returned unfiltered data, fall through to page scan/fallback.
            break
        if not _v3981_is_date_validation_error(official):
            break

    # Page scan with the first repeated-param variant.
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_size = 500
    max_pages = max(1, (limit_int // page_size) + 4)
    base_date_params = _v3981_date_param_variants(d1, d2)[0]
    for page in range(max_pages):
        params = {**base_date_params, "limit": str(page_size), "offset": str(page * page_size)}
        result = self.request("GET", endpoint, params=params)
        items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
        if page == 0:
            diagnostics.append({"strategy": "official date array pages", "params": _v3981_date_params_debug({**base_date_params, "limit": page_size}), "ok": result.ok, "status": result.status, "count": len(items), "error": result.error[:250] if result.error else ""})
        if not result.ok or not items:
            break
        for item in items:
            ld = parse_date(_lesson_date_value(item))
            if ld and d1 <= ld < d2:
                lesson_id = str(_pick(item, ("id", "lessonId")) or json.dumps(item, ensure_ascii=False, sort_keys=True)[:160])
                if lesson_id not in seen:
                    collected.append(item)
                    seen.add(lesson_id)
        if len(items) < page_size or len(collected) >= limit_int:
            break
    if collected:
        collected.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
        return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(collected[:limit_int]), "_range": {"from": d1.isoformat(), "to": d2.isoformat()}, "_usedParams": _v3981_date_params_debug(base_date_params), "_diagnostics": diagnostics}, status=200, endpoint=endpoint)

    scan = self._scan_lessons_between_by_pages(d1, d2, limit=limit_int)
    if scan.ok and extract_items(scan.data):
        if isinstance(scan.data, dict):
            scan.data.setdefault("_diagnostics", diagnostics + (scan.data.get("_scanDiagnostics") or []))
        return scan
    return MoyKlassResult(True, data={"lessons": [], "_range": {"from": d1.isoformat(), "to": d2.isoformat()}, "_diagnostics": diagnostics, "_filterWarning": "По выбранному периоду занятия не найдены. Старые занятия скрыты."}, status=200, endpoint=endpoint)


def _v3981_get_class_lessons(self: MoyKlassClient, class_id: int | str, days: int = 30, limit: int = 120) -> MoyKlassResult:
    class_id = str(class_id).strip()
    if not class_id.isdigit():
        return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/lessons")
    today = date.today()
    future = today + timedelta(days=max(1, int(days or 30)))
    diagnostics: list[dict[str, Any]] = []
    endpoint = "/v1/company/lessons"
    for date_params in _v3981_date_param_variants(today, future):
        params = {**date_params, "classId": class_id, "limit": str(max(limit, 120))}
        result = self.request("GET", endpoint, params=params)
        items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
        diagnostics.append({"params": _v3981_date_params_debug(params), "ok": result.ok, "status": result.status, "count": len(items), "error": result.error[:200] if result.error else ""})
        if result.ok:
            filtered = [x for x in items if str(_pick(x, ("classId", "groupId"))) == class_id]
            filtered.sort(key=lambda x: (_lesson_date_value(x), _lesson_time_value(x)))
            return MoyKlassResult(True, data={"lessons": self._enrich_lesson_items(filtered[:limit]), "_usedParams": _v3981_date_params_debug(params), "_diagnostics": diagnostics}, status=result.status, endpoint=endpoint)
        if not _v3981_is_date_validation_error(result):
            break
    return MoyKlassResult(False, data={"lessons": [], "_diagnostics": diagnostics}, status=diagnostics[-1].get("status", 0) if diagnostics else 0, error=diagnostics[-1].get("error", "") if diagnostics else "", endpoint=endpoint)


def _v3981_get_class_records_month(self: MoyKlassClient, class_id: int | str, month: str | None = None, limit: int = 3000) -> MoyKlassResult:
    class_id = str(class_id).strip()
    if not class_id.isdigit():
        return MoyKlassResult(False, error="class_id должен быть числом", endpoint="/v1/company/lessonRecords")
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="/v1/company/lessonRecords")
    diagnostics: list[dict[str, Any]] = []
    endpoint = "/v1/company/lessonRecords"
    for date_params in _v3981_date_param_variants(start, end):
        params = {**date_params, "classId": class_id, "includeLessons": "true", "includeBills": "true", "includeUserSubscriptions": "true", "limit": str(limit)}
        result = self.request("GET", endpoint, params=params)
        items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
        diagnostics.append({"params": _v3981_date_params_debug(params), "ok": result.ok, "status": result.status, "count": len(items), "error": result.error[:200] if result.error else ""})
        if result.ok:
            if isinstance(result.data, dict):
                result.data.setdefault("classId", class_id)
                result.data.setdefault("month", month_label)
                result.data.setdefault("_diagnostics", diagnostics)
            return result
        if not _v3981_is_date_validation_error(result):
            break
    return MoyKlassResult(False, data={"lessonRecords": [], "_diagnostics": diagnostics}, status=diagnostics[-1].get("status", 0) if diagnostics else 0, error=diagnostics[-1].get("error", "") if diagnostics else "", endpoint=endpoint)


# Apply v3.9.8.1 compatibility overrides after v3.9.8 official sync overrides.
MoyKlassClient._api_date_range_value = lambda self, start, end: _v3981_date_range_value(start, end)
MoyKlassClient._api_date_range_param_variants = lambda self, start, end: _v3981_date_param_variants(start, end)
MoyKlassClient._scan_lesson_records_for_month = _v3981_scan_lesson_records_for_month
MoyKlassClient._scan_payments_for_month = _v3981_scan_payments_for_month
MoyKlassClient.get_lessons_between = _v3981_get_lessons_between
MoyKlassClient.get_class_lessons = _v3981_get_class_lessons
MoyKlassClient.get_class_records_month = _v3981_get_class_records_month

# ---------------------------------------------------------------------------
# v3.9.9 operational MoyKlass tasks: absences, unpaid active clients,
# low attendance, teacher month report and daily manager tasks.
# ---------------------------------------------------------------------------


def _v399_user_id(record: dict[str, Any]) -> str:
    return str(_pick(record, ("userId", "studentId", "clientId", "customerId", "idUser")) or "").strip()


def _v399_payment_user_id(payment: dict[str, Any]) -> str:
    return str(_pick(payment, ("userId", "studentId", "clientId", "customerId", "idUser")) or "").strip()


def _v399_lesson_id_from_record(record: dict[str, Any]) -> str:
    lesson = record.get("lesson") if isinstance(record, dict) else None
    if isinstance(lesson, dict):
        value = _pick(lesson, ("id", "lessonId"))
        if value:
            return value
    return _pick(record, ("lessonId", "idLesson"))


def _v399_record_key(record: dict[str, Any]) -> str:
    return str(_pick(record, ("id", "recordId", "lessonRecordId")) or json.dumps(record, sort_keys=True, ensure_ascii=False)[:220])


def _v399_payment_key(payment: dict[str, Any]) -> str:
    return str(_pick(payment, ("id", "paymentId", "transactionId")) or json.dumps(payment, sort_keys=True, ensure_ascii=False)[:220])


def _v399_records_for_month_result(self: MoyKlassClient, month: str | None = None, limit: int = 12000) -> MoyKlassResult:
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="/v1/company/lessonRecords")
    result = self._scan_lesson_records_for_month(start, end, limit=limit)
    if result.ok and isinstance(result.data, dict):
        result.data.setdefault("month", month_label)
        result.data.setdefault("dateFrom", start.isoformat())
        result.data.setdefault("dateTo", end.isoformat())
    return result


def _v399_payments_for_month_result(self: MoyKlassClient, month: str | None = None, limit: int = 12000) -> MoyKlassResult:
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="/v1/company/payments")
    result = self._scan_payments_for_month(start, end, limit=limit)
    if result.ok and isinstance(result.data, dict):
        result.data.setdefault("month", month_label)
        result.data.setdefault("dateFrom", start.isoformat())
        result.data.setdefault("dateTo", end.isoformat())
    return result


def _v399_build_user_stats(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    seen_by_user: dict[str, set[str]] = {}
    for rec in records:
        if not isinstance(rec, dict) or _truthy(rec.get("skip")):
            continue
        uid = _v399_user_id(rec)
        if not uid:
            continue
        rec_key = _v399_record_key(rec)
        seen = seen_by_user.setdefault(uid, set())
        if rec_key in seen:
            continue
        seen.add(rec_key)
        row = stats.setdefault(uid, {
            "userId": uid,
            "name": _v398_format_record_user(rec),
            "records": 0,
            "visits": 0,
            "missed": 0,
            "test": 0,
            "paidRecords": 0,
            "examplesMissed": [],
            "examplesVisited": [],
            "groups": {},
        })
        if not row.get("name") or str(row.get("name")) == uid:
            row["name"] = _v398_format_record_user(rec)
        row["records"] += 1
        if _truthy(rec.get("test")):
            row["test"] += 1
        if _truthy(rec.get("paid")):
            row["paidRecords"] += 1
        lesson_text = _v398_format_record_lesson(rec)
        group_name = ""
        lesson = rec.get("lesson")
        if isinstance(lesson, dict):
            group_name = _lesson_group_value(lesson)
        if group_name:
            row["groups"][group_name] = row["groups"].get(group_name, 0) + 1
        if _truthy(rec.get("visit")):
            row["visits"] += 1
            if len(row["examplesVisited"]) < 3 and lesson_text:
                row["examplesVisited"].append(lesson_text)
        elif rec.get("visit") is not None:
            row["missed"] += 1
            if len(row["examplesMissed"]) < 5 and lesson_text:
                row["examplesMissed"].append(lesson_text)
    for row in stats.values():
        records = int(row.get("records") or 0)
        visits = int(row.get("visits") or 0)
        row["attendanceRate"] = round((visits / records) * 100, 1) if records else 0.0
        groups = row.get("groups") or {}
        row["topGroups"] = [name for name, _count in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return stats


def _v399_payment_user_stats(payments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    seen_by_user: dict[str, set[str]] = {}
    for p in payments:
        if not isinstance(p, dict):
            continue
        if str(p.get("optype") or "income").lower() != "income":
            continue
        uid = _v399_payment_user_id(p)
        if not uid:
            continue
        pay_key = _v399_payment_key(p)
        seen = seen_by_user.setdefault(uid, set())
        if pay_key in seen:
            continue
        seen.add(pay_key)
        row = stats.setdefault(uid, {"userId": uid, "payments": 0, "sum": 0.0, "last": []})
        row["payments"] += 1
        row["sum"] = round(float(row.get("sum") or 0) + float(_sum_payment_amounts([p])), 2)
        if len(row["last"]) < 3:
            d = str(_pick(p, ("date", "paymentDate", "createdAt", "paidAt")) or "")[:10]
            amount = _pick(p, ("summa", "amount", "sum", "value", "paid", "price", "total", "money")) or "0"
            row["last"].append(f"{d} - {_money_text(amount)}")
    return stats


def _v399_month_records_and_payments(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    records_result = _v399_records_for_month_result(self, month)
    payments_result = _v399_payments_for_month_result(self, month)
    month_label = ""
    if isinstance(records_result.data, dict):
        month_label = str(records_result.data.get("month") or "")
    if not month_label and isinstance(payments_result.data, dict):
        month_label = str(payments_result.data.get("month") or "")
    return MoyKlassResult(
        bool(records_result.ok or payments_result.ok),
        data={
            "month": month_label,
            "recordsResult": records_result,
            "paymentsResult": payments_result,
            "lessonRecords": extract_items(records_result.data) if records_result.ok else [],
            "payments": extract_items(payments_result.data) if payments_result.ok else [],
        },
        status=200 if (records_result.ok or payments_result.ok) else (records_result.status or payments_result.status),
        error="" if (records_result.ok or payments_result.ok) else (records_result.error or payments_result.error),
        endpoint="operational-analytics",
    )


def _v399_get_absences(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    return _v399_records_for_month_result(self, month)


def _v399_get_low_attendance(self: MoyKlassClient, month: str | None = None, threshold: float = 60.0) -> MoyKlassResult:
    result = _v399_records_for_month_result(self, month)
    if result.ok and isinstance(result.data, dict):
        result.data["threshold"] = float(threshold)
    return result


def _v399_get_unpaid_active_clients(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    return _v399_month_records_and_payments(self, month)


def _v399_get_teacher_month_report(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    start, end, month_label = _month_bounds(month)
    if not start or not end:
        return MoyKlassResult(False, error="Неверный месяц. Формат: YYYY-MM или YYYY-MM-DD", endpoint="teacher-month")
    lessons_result = self.get_lessons_between(start, end, limit=3000)
    records_result = self._scan_lesson_records_for_month(start, end, limit=12000)
    lessons = [x for x in extract_items(lessons_result.data) if isinstance(x, dict)] if lessons_result.ok else []
    records = [x for x in extract_items(records_result.data) if isinstance(x, dict)] if records_result.ok else []
    data = {
        "month": month_label,
        "lessons": lessons,
        "lessonRecords": records,
        "lessonsOk": lessons_result.ok,
        "recordsOk": records_result.ok,
        "lessonsError": lessons_result.error,
        "recordsError": records_result.error,
    }
    return MoyKlassResult(True, data=data, status=200, endpoint="teacher-month")


def _v399_get_daily_tasks(self: MoyKlassClient, day: str | date | None = None) -> MoyKlassResult:
    if day is None or str(day).strip() == "":
        d = date.today()
    else:
        d = parse_date(day) if isinstance(day, str) else day
    if not d:
        return MoyKlassResult(False, error="Неверная дата. Формат: YYYY-MM-DD", endpoint="daily-tasks")
    end = d + timedelta(days=1)
    lessons_result = self.get_lessons_between(d, end, limit=300)
    records_result = self._scan_lesson_records_for_month(d, end, limit=4000)
    payments_result = self._scan_payments_for_month(d, end, limit=2000)
    data = {
        "day": d.isoformat(),
        "lessons": extract_items(lessons_result.data) if lessons_result.ok else [],
        "lessonRecords": extract_items(records_result.data) if records_result.ok else [],
        "payments": extract_items(payments_result.data) if payments_result.ok else [],
        "lessonsOk": lessons_result.ok,
        "recordsOk": records_result.ok,
        "paymentsOk": payments_result.ok,
        "lessonsError": lessons_result.error,
        "recordsError": records_result.error,
        "paymentsError": payments_result.error,
    }
    return MoyKlassResult(True, data=data, status=200, endpoint="daily-tasks")


def format_absences_result(result: MoyKlassResult, limit: int = 40) -> str:
    if not result.ok:
        return f"🔁 Пропуски недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    records = [x for x in extract_items(result.data) if isinstance(x, dict)]
    missed_records = [x for x in records if x.get("visit") is not None and not _truthy(x.get("visit")) and not _truthy(x.get("skip"))]
    stats = _v399_build_user_stats(records)
    rows = [row for row in stats.values() if int(row.get("missed") or 0) > 0]
    rows.sort(key=lambda x: (-int(x.get("missed") or 0), -int(x.get("records") or 0), str(x.get("name") or "")))
    lines = [
        f"🔁 Пропуски и отработки {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Пропусков всего: {len(missed_records)}",
        f"Учеников с пропусками: {len(rows)}",
        "",
        "Кому нужна проверка отработок:",
    ]
    if not rows:
        lines.append("- пропусков за период не найдено")
    for row in rows[:limit]:
        groups = ", ".join(row.get("topGroups") or [])
        groups_text = f" | группы: {groups}" if groups else ""
        lines.append(f"- {row.get('name')} #{row.get('userId')}: пропусков {row.get('missed')}, посещений {row.get('visits')}, записей {row.get('records')}{groups_text}")
        for example in (row.get("examplesMissed") or [])[:2]:
            lines.append(f"  · {example}")
    lines += [
        "",
        "Важно: список показывает пропуски по visit=false. Факт назначенной отработки нужно проверять отдельно, если в МК это ведётся через отдельную запись/комментарий.",
    ]
    return "\n".join(lines)


def format_low_attendance_result(result: MoyKlassResult, limit: int = 40) -> str:
    if not result.ok:
        return f"📉 Низкая посещаемость недоступна.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    threshold = float(data.get("threshold") or 60.0)
    records = [x for x in extract_items(result.data) if isinstance(x, dict)]
    stats = _v399_build_user_stats(records)
    rows = [row for row in stats.values() if int(row.get("records") or 0) >= 2 and float(row.get("attendanceRate") or 0) < threshold]
    rows.sort(key=lambda x: (float(x.get("attendanceRate") or 0), -int(x.get("missed") or 0), str(x.get("name") or "")))
    lines = [
        f"📉 Низкая посещаемость {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Порог: ниже {threshold:g}%",
        f"Учеников ниже порога: {len(rows)}",
        "",
        "Список:",
    ]
    if not rows:
        lines.append("- учеников ниже порога не найдено")
    for row in rows[:limit]:
        lines.append(f"- {row.get('name')} #{row.get('userId')}: {row.get('attendanceRate')}% | посещений {row.get('visits')}, пропусков {row.get('missed')}, записей {row.get('records')}")
    return "\n".join(lines)


def format_unpaid_active_clients_result(result: MoyKlassResult, limit: int = 50) -> str:
    if not result.ok:
        return f"💳 Ученики без оплат недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    month = data.get("month") or ""
    records = [x for x in data.get("lessonRecords") or [] if isinstance(x, dict)]
    payments = [x for x in data.get("payments") or [] if isinstance(x, dict)]
    user_stats = _v399_build_user_stats(records)
    payment_stats = _v399_payment_user_stats(payments)
    active_ids = set(user_stats.keys())
    paid_ids = set(payment_stats.keys())
    unpaid_ids = sorted(active_ids - paid_ids, key=lambda uid: (-int(user_stats[uid].get("records") or 0), str(user_stats[uid].get("name") or "")))
    lines = [
        f"💳 Активные ученики без оплат {month}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Активных учеников по записям: {len(active_ids)}",
        f"Учеников с оплатами в месяце: {len(paid_ids)}",
        f"Активных без входящих оплат в этом месяце: {len(unpaid_ids)}",
        "",
        "Список для проверки:",
    ]
    if not unpaid_ids:
        lines.append("- таких учеников не найдено")
    for uid in unpaid_ids[:limit]:
        row = user_stats[uid]
        groups = ", ".join(row.get("topGroups") or [])
        groups_text = f" | {groups}" if groups else ""
        lines.append(f"- {row.get('name')} #{uid}: записей {row.get('records')}, посещений {row.get('visits')}, пропусков {row.get('missed')}{groups_text}")
    lines += [
        "",
        "Важно: это не точная задолженность. Ученик мог оплатить раньше, семейным платежом, через другой способ или за другой период. Команда нужна как список на проверку менеджеру.",
    ]
    return "\n".join(lines)


def _v399_teacher_key_from_lesson(lesson: dict[str, Any]) -> list[str]:
    teacher_ids = lesson_teacher_ids(lesson)
    teacher_name = _lesson_teacher_value(lesson)
    if teacher_ids:
        if teacher_name and not teacher_name.startswith("Преподаватель ID"):
            names = [x.strip() for x in teacher_name.split(",") if x.strip()]
            if len(names) == len(teacher_ids):
                return [f"{names[i]} #{teacher_ids[i]}" for i in range(len(teacher_ids))]
        return [f"{teacher_name or 'Преподаватель'} #{tid}" for tid in teacher_ids]
    return [teacher_name or "преподаватель не указан"]


def format_teacher_month_report_result(result: MoyKlassResult, limit: int = 20) -> str:
    if not result.ok:
        return f"👨‍🏫 Отчёт по преподавателям недоступен.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    lessons = [x for x in data.get("lessons") or [] if isinstance(x, dict)]
    records = [x for x in data.get("lessonRecords") or [] if isinstance(x, dict)]
    teachers: dict[str, dict[str, Any]] = {}

    for lesson in lessons:
        for key in _v399_teacher_key_from_lesson(lesson):
            row = teachers.setdefault(key, {"lessons": 0, "conducted": 0, "planned": 0, "missingTopic": 0, "missingComment": 0, "records": 0, "visits": 0, "missed": 0})
            row["lessons"] += 1
            status = str(_pick(lesson, ("status",)) or "")
            if status == "1":
                row["conducted"] += 1
            else:
                row["planned"] += 1
            if not _pick(lesson, ("topic", "name", "title")):
                row["missingTopic"] += 1
            if not _pick(lesson, ("comment", "description")):
                row["missingComment"] += 1

    for rec in records:
        lesson = rec.get("lesson") if isinstance(rec, dict) else None
        if not isinstance(lesson, dict):
            continue
        for key in _v399_teacher_key_from_lesson(lesson):
            row = teachers.setdefault(key, {"lessons": 0, "conducted": 0, "planned": 0, "missingTopic": 0, "missingComment": 0, "records": 0, "visits": 0, "missed": 0})
            if _truthy(rec.get("skip")):
                continue
            row["records"] += 1
            if _truthy(rec.get("visit")):
                row["visits"] += 1
            elif rec.get("visit") is not None:
                row["missed"] += 1

    rows = sorted(teachers.items(), key=lambda kv: (-int(kv[1].get("lessons") or 0), kv[0]))
    lines = [
        f"👨‍🏫 Отчёт по преподавателям {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Преподавателей в отчёте: {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("Нет данных по преподавателям.")
    for name, row in rows[:limit]:
        lines.append(f"{name}")
        lines.append(f"- занятий: {row.get('lessons')} | проведено: {row.get('conducted')} | не проведено/план: {row.get('planned')}")
        lines.append(f"- записей: {row.get('records')} | посещений: {row.get('visits')} | пропусков: {row.get('missed')}")
        lines.append(f"- без темы: {row.get('missingTopic')} | без комментария: {row.get('missingComment')}")
    return "\n".join(lines)


def format_daily_tasks_result(result: MoyKlassResult, limit: int = 25) -> str:
    if not result.ok:
        return f"📅 Задачи на день недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    day = data.get("day") or ""
    lessons = [x for x in data.get("lessons") or [] if isinstance(x, dict)]
    records = [x for x in data.get("lessonRecords") or [] if isinstance(x, dict)]
    payments = [x for x in data.get("payments") or [] if isinstance(x, dict)]
    missing_topic = [x for x in lessons if not _pick(x, ("topic", "name", "title"))]
    missing_comment = [x for x in lessons if not _pick(x, ("comment", "description"))]
    no_teacher = [x for x in lessons if not _lesson_teacher_value(x)]
    no_room = [x for x in lessons if not _lesson_room_value(x)]
    attended = [x for x in records if _truthy(x.get("visit")) and not _truthy(x.get("skip"))]
    missed = [x for x in records if x.get("visit") is not None and not _truthy(x.get("visit")) and not _truthy(x.get("skip"))]
    pay_items = [x for x in payments if str(x.get("optype") or "income").lower() == "income"]
    lines = [
        f"📅 Ежедневные задачи МойКласс {day}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Занятий: {len(lessons)}",
        f"Записей на занятия: {len(records)}",
        f"Посещений: {len(attended)}",
        f"Пропусков: {len(missed)}",
        f"Оплат за день: {len(pay_items)} на сумму {_money_text(_sum_payment_amounts(pay_items))}",
        "",
        "Что проверить:",
        f"1. Занятия без темы: {len(missing_topic)}",
        f"2. Занятия без комментария: {len(missing_comment)}",
        f"3. Занятия без преподавателя: {len(no_teacher)}",
        f"4. Занятия без кабинета: {len(no_room)}",
        f"5. Пропуски: {len(missed)}",
        "",
        "Занятия дня:",
    ]
    if not lessons:
        lines.append("- занятий на день не найдено")
    for lesson in lessons[:limit]:
        group = _lesson_group_value(lesson) or "группа не указана"
        time_value = _lesson_time_value(lesson) or "время не указано"
        teacher = _lesson_teacher_value(lesson) or "преподаватель не указан"
        room = _lesson_room_value(lesson) or "кабинет не указан"
        markers = []
        if lesson in missing_topic:
            markers.append("без темы")
        if lesson in missing_comment:
            markers.append("без комментария")
        marker_text = f" ({', '.join(markers)})" if markers else ""
        lines.append(f"- {time_value} | {group} | {teacher} | {room}{marker_text}")
    if missed:
        lines += ["", "Пропуски:"]
        for rec in missed[:10]:
            lines.append(f"- {_v398_format_record_user(rec)} | {_v398_format_record_lesson(rec)}")
    return "\n".join(lines)


# Apply v3.9.9 operational task methods.
MoyKlassClient.get_month_absences = _v399_get_absences
MoyKlassClient.get_month_low_attendance = _v399_get_low_attendance
MoyKlassClient.get_unpaid_active_clients = _v399_get_unpaid_active_clients
MoyKlassClient.get_teacher_month_report = _v399_get_teacher_month_report
MoyKlassClient.get_daily_tasks = _v399_get_daily_tasks

# Small formatting correction: _lesson_time_value already returns begin-end when both
# values exist, so do not append endTime a second time in lessonRecords output.
def _v398_format_record_lesson(record: dict[str, Any]) -> str:  # type: ignore[no-redef]
    lesson = record.get("lesson") if isinstance(record, dict) else None
    if isinstance(lesson, dict):
        d = _lesson_date_value(lesson)
        t = _lesson_time_value(lesson)
        group = _lesson_group_value(lesson)
        return " | ".join(x for x in [group, d, t] if x)
    return str(_pick(record, ("lessonId", "idLesson")) or "")

# ---------------------------------------------------------------------------
# v3.9.9.1 user names in operational reports.
# MoyKlass lessonRecords/payments often return only userId. This enrichment
# resolves IDs through official /v1/company/users and adds readable names to
# attendance, payments, active clients, absences, unpaid and daily task reports.
# ---------------------------------------------------------------------------

import time as _yc_time


def _v3991_user_id_from_any(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("user", "student", "client", "customer"):
        value = item.get(key)
        if isinstance(value, dict):
            uid = _pick(value, ("id", "userId", "studentId", "clientId", "customerId", "idUser"))
            if uid:
                return str(uid).strip()
    return str(_pick(item, ("userId", "studentId", "clientId", "customerId", "idUser")) or "").strip()


def _v3991_user_name_from_user_obj(user: dict[str, Any]) -> str:
    if not isinstance(user, dict):
        return ""
    name = _pick(user, ("name", "fullName", "fio", "title", "displayName"))
    if name:
        return str(name).strip()
    parts = []
    for key in ("lastName", "surname", "secondName", "firstName", "middleName", "patronymic"):
        value = user.get(key)
        if value:
            parts.append(str(value).strip())
    return " ".join(x for x in parts if x).strip()


def _v3991_user_name_from_item(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    cached = str(item.get("_ycUserName") or "").strip()
    if cached:
        return cached
    for key in ("user", "student", "client", "customer"):
        value = item.get(key)
        if isinstance(value, dict):
            name = _v3991_user_name_from_user_obj(value)
            if name:
                return name
    name = _pick(item, ("userName", "studentName", "clientName", "customerName", "fullName", "fio", "name"))
    if name:
        return str(name).strip()
    return ""


def _v3991_user_label(name: str, uid: str) -> str:
    name = str(name or "").strip()
    uid = str(uid or "").strip()
    if name and uid and name != uid and name != f"#{uid}":
        return f"{name} #{uid}"
    if name and (not uid or name != uid):
        return name
    if uid:
        return f"ученик #{uid}"
    return "ученик"


def _v3991_record_user_label(record: dict[str, Any]) -> str:
    uid = _v3991_user_id_from_any(record)
    name = _v3991_user_name_from_item(record)
    return _v3991_user_label(name, uid)


# Override old formatter globally. All existing report builders call this name
# at runtime, so the readable names automatically appear in old and new reports.
def _v398_format_record_user(record: dict[str, Any]) -> str:  # type: ignore[no-redef]
    return _v3991_record_user_label(record)


def _v3991_extract_users_from_response(data: Any) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for key in ("users", "items", "data", "rows", "records", "result"):
            value = data.get(key)
            if isinstance(value, list):
                users.extend([x for x in value if isinstance(x, dict)])
        if any(k in data for k in ("id", "name", "fullName", "fio")):
            users.append(data)
    elif isinstance(data, list):
        users.extend([x for x in data if isinstance(x, dict)])
    return users


def _v3991_get_cache(self: MoyKlassClient) -> dict[str, str]:
    cache = getattr(self, "_yc_user_name_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(self, "_yc_user_name_cache", cache)
    return cache


def _v3991_fetch_user_names(self: MoyKlassClient, user_ids: list[str], max_single: int = 120) -> dict[str, str]:
    clean: list[str] = []
    for uid in user_ids:
        uid = str(uid or "").strip()
        if uid and uid not in clean:
            clean.append(uid)
    cache = _v3991_get_cache(self)
    result_map: dict[str, str] = {uid: cache[uid] for uid in clean if cache.get(uid)}
    missing = [uid for uid in clean if uid not in result_map]
    if not missing:
        return result_map

    # Official users endpoint supports filtering by userIds. Try both common
    # array encodings used by MoyKlass installations.
    batches = [missing[i:i + 50] for i in range(0, len(missing), 50)]
    for batch in batches:
        variants = [
            {"userIds": batch, "limit": str(max(len(batch), 50))},
            {"userIds": ",".join(batch), "limit": str(max(len(batch), 50))},
        ]
        for params in variants:
            response = self.request("GET", "/v1/company/users", params=params)
            if not response.ok:
                continue
            for user in _v3991_extract_users_from_response(response.data):
                uid = str(_pick(user, ("id", "userId", "studentId", "clientId", "customerId")) or "").strip()
                name = _v3991_user_name_from_user_obj(user)
                if uid and name:
                    cache[uid] = name
                    result_map[uid] = name
        missing = [uid for uid in clean if uid not in result_map]
        if not missing:
            return result_map

    # Fallback: single user cards. Limit it to avoid very long Telegram waits.
    for idx, uid in enumerate(missing[:max_single], start=1):
        response = self.request("GET", f"/v1/company/users/{uid}")
        if response.ok and isinstance(response.data, dict):
            name = _v3991_user_name_from_user_obj(response.data)
            if name:
                cache[uid] = name
                result_map[uid] = name
        if idx % 5 == 0:
            _yc_time.sleep(0.2)
    return result_map


def _v3991_fetch_user_objects(self: MoyKlassClient, user_ids: list[str], max_single: int = 120) -> "dict[str, dict[str, Any]]":
    """Batch-fetch full user objects by ID. Returns uid → user dict (with rawKeys, name fields, etc.)."""
    clean: list[str] = []
    for uid in user_ids:
        uid = str(uid or "").strip()
        if uid and uid not in clean:
            clean.append(uid)
    if not clean:
        return {}
    result_map: dict[str, Any] = {}
    missing = list(clean)
    batches = [missing[i:i + 50] for i in range(0, len(missing), 50)]
    for batch in batches:
        variants = [
            {"userIds": batch, "limit": str(max(len(batch), 50))},
            {"userIds": ",".join(batch), "limit": str(max(len(batch), 50))},
        ]
        for params in variants:
            response = self.request("GET", "/v1/company/users", params=params)
            if not response.ok:
                continue
            for user in _v3991_extract_users_from_response(response.data):
                uid = str(_pick(user, ("id", "userId", "studentId", "clientId", "customerId")) or "").strip()
                if uid and uid in missing and uid not in result_map:
                    result_map[uid] = user
        missing = [uid for uid in clean if uid not in result_map]
        if not missing:
            return result_map
    for idx, uid in enumerate(missing[:max_single], start=1):
        response = self.request("GET", f"/v1/company/users/{uid}")
        if response.ok and isinstance(response.data, dict):
            result_map[uid] = response.data
        if idx % 5 == 0:
            _yc_time.sleep(0.2)
    return result_map


def _v3991_enrich_records(self: MoyKlassClient, records: list[dict[str, Any]]) -> dict[str, str]:
    ids = [_v3991_user_id_from_any(rec) for rec in records if isinstance(rec, dict)]
    names = _v3991_fetch_user_names(self, ids)
    for rec in records:
        uid = _v3991_user_id_from_any(rec)
        if uid and names.get(uid):
            rec["_ycUserName"] = names[uid]
    return names


def _v3991_enrich_payments(self: MoyKlassClient, payments: list[dict[str, Any]]) -> dict[str, str]:
    ids = [_v3991_user_id_from_any(p) for p in payments if isinstance(p, dict)]
    names = _v3991_fetch_user_names(self, ids)
    for p in payments:
        uid = _v3991_user_id_from_any(p)
        if uid and names.get(uid):
            p["_ycUserName"] = names[uid]
    return names


def _v3991_enrich_records_result(self: MoyKlassClient, result: MoyKlassResult) -> MoyKlassResult:
    if result.ok and isinstance(result.data, dict):
        records = [x for x in result.data.get("lessonRecords") or [] if isinstance(x, dict)]
        names = _v3991_enrich_records(self, records)
        result.data["_userNames"] = names
        diagnostics = result.data.get("_diagnostics")
        if isinstance(diagnostics, list):
            diagnostics.append({"endpoint": "/v1/company/users", "strategy": "resolve user names", "requested": len({_v3991_user_id_from_any(r) for r in records if _v3991_user_id_from_any(r)}), "resolved": len(names)})
    return result


def _v3991_enrich_payments_result(self: MoyKlassClient, result: MoyKlassResult) -> MoyKlassResult:
    if result.ok and isinstance(result.data, dict):
        payments = [x for x in result.data.get("payments") or [] if isinstance(x, dict)]
        names = _v3991_enrich_payments(self, payments)
        result.data["_userNames"] = names
        diagnostics = result.data.get("_diagnostics")
        if isinstance(diagnostics, list):
            diagnostics.append({"endpoint": "/v1/company/users", "strategy": "resolve payment user names", "requested": len({_v3991_user_id_from_any(p) for p in payments if _v3991_user_id_from_any(p)}), "resolved": len(names)})
    return result


_v3991_base_get_month_lesson_records = MoyKlassClient.get_month_lesson_records
_v3991_base_get_month_payments = MoyKlassClient.get_month_payments
_v3991_base_records_for_month_result = _v399_records_for_month_result
_v3991_base_payments_for_month_result = _v399_payments_for_month_result
_v3991_base_month_records_and_payments = _v399_month_records_and_payments


def _v3991_get_month_lesson_records(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    return _v3991_enrich_records_result(self, _v3991_base_get_month_lesson_records(self, month))


def _v3991_get_month_payments(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    return _v3991_enrich_payments_result(self, _v3991_base_get_month_payments(self, month))


def _v3991_records_for_month_result(self: MoyKlassClient, month: str | None = None, limit: int = 12000) -> MoyKlassResult:
    return _v3991_enrich_records_result(self, _v3991_base_records_for_month_result(self, month, limit))


def _v3991_payments_for_month_result(self: MoyKlassClient, month: str | None = None, limit: int = 12000) -> MoyKlassResult:
    return _v3991_enrich_payments_result(self, _v3991_base_payments_for_month_result(self, month, limit))


def _v3991_month_records_and_payments(self: MoyKlassClient, month: str | None = None) -> MoyKlassResult:
    records_result = _v3991_records_for_month_result(self, month)
    payments_result = _v3991_payments_for_month_result(self, month)
    month_label = ""
    if isinstance(records_result.data, dict):
        month_label = str(records_result.data.get("month") or "")
    if not month_label and isinstance(payments_result.data, dict):
        month_label = str(payments_result.data.get("month") or "")
    return MoyKlassResult(
        bool(records_result.ok or payments_result.ok),
        data={
            "month": month_label,
            "recordsResult": records_result,
            "paymentsResult": payments_result,
            "lessonRecords": extract_items(records_result.data) if records_result.ok else [],
            "payments": extract_items(payments_result.data) if payments_result.ok else [],
        },
        status=200 if (records_result.ok or payments_result.ok) else (records_result.status or payments_result.status),
        error="" if (records_result.ok or payments_result.ok) else (records_result.error or payments_result.error),
        endpoint="operational-analytics",
    )


def _v3991_payment_label(payment: dict[str, Any]) -> str:
    uid = _v3991_user_id_from_any(payment)
    name = _v3991_user_name_from_item(payment)
    return _v3991_user_label(name, uid)


def format_payments_result(result: MoyKlassResult, limit: int = 30) -> str:  # type: ignore[no-redef]
    if not result.ok:
        return f"💳 Оплаты недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    items_all = [x for x in extract_items(result.data) if isinstance(x, dict)]
    items = [x for x in items_all if str(x.get("optype") or "income").lower() == "income"]
    total = _sum_payment_amounts(items)
    unique_students = _unique_ids_from_records(items, ("userId", "studentId", "clientId", "customerId", "idUser"))
    by_type: dict[str, int] = {}
    for item in items:
        typ = str(_pick(item, ("paymentTypeId", "type", "paymentType", "method")) or "не указан")
        by_type[typ] = by_type.get(typ, 0) + 1
    lines = [
        f"💳 Оплаты МойКласс {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Входящих оплат: {len(items)}",
        f"Сумма оплат: {_money_text(total)}",
        f"Уникальных учеников с оплатами: {len(unique_students)}",
    ]
    if by_type:
        lines += ["", "По типам оплаты:"]
        for key, count in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)[:10]:
            lines.append(f"- {key}: {count}")
    if items:
        lines += ["", "Последние оплаты:"]
        for p in items[:limit]:
            amount = _pick(p, ("summa", "amount", "sum", "value", "paid", "price", "total", "money")) or "0"
            d = str(_pick(p, ("date", "paymentDate", "createdAt", "paidAt")) or "")[:10]
            lines.append(f"- {d} | {_v3991_payment_label(p)} | {_money_text(amount)}")
    return "\n".join(lines)


# Rebind helpers and class methods so all operational commands use enrichment.
_v399_records_for_month_result = _v3991_records_for_month_result  # type: ignore[assignment]
_v399_payments_for_month_result = _v3991_payments_for_month_result  # type: ignore[assignment]
_v399_month_records_and_payments = _v3991_month_records_and_payments  # type: ignore[assignment]
MoyKlassClient.get_month_lesson_records = _v3991_get_month_lesson_records
MoyKlassClient.get_month_payments = _v3991_get_month_payments
MoyKlassClient.get_month_absences = _v399_get_absences
MoyKlassClient.get_month_low_attendance = _v399_get_low_attendance
MoyKlassClient.get_unpaid_active_clients = _v399_get_unpaid_active_clients
MoyKlassClient.get_teacher_month_report = _v399_get_teacher_month_report
MoyKlassClient.get_daily_tasks = _v399_get_daily_tasks


# v3.9.9.1 small correction: user stats store the raw student name,
# while report lines add #userId separately. This prevents "Name #id #id".
def _v3991_raw_record_user_name(record: dict[str, Any]) -> str:
    name = _v3991_user_name_from_item(record)
    uid = _v3991_user_id_from_any(record)
    if name and name != uid:
        return name
    return "ученик"


def _v399_build_user_stats(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:  # type: ignore[no-redef]
    stats: dict[str, dict[str, Any]] = {}
    seen_by_user: dict[str, set[str]] = {}
    for rec in records:
        if not isinstance(rec, dict) or _truthy(rec.get("skip")):
            continue
        uid = _v399_user_id(rec)
        if not uid:
            continue
        rec_key = _v399_record_key(rec)
        seen = seen_by_user.setdefault(uid, set())
        if rec_key in seen:
            continue
        seen.add(rec_key)
        row = stats.setdefault(uid, {
            "userId": uid,
            "name": _v3991_raw_record_user_name(rec),
            "records": 0,
            "visits": 0,
            "missed": 0,
            "test": 0,
            "paidRecords": 0,
            "examplesMissed": [],
            "examplesVisited": [],
            "groups": {},
        })
        if not row.get("name") or str(row.get("name")) == "ученик":
            row["name"] = _v3991_raw_record_user_name(rec)
        row["records"] += 1
        if _truthy(rec.get("test")):
            row["test"] += 1
        if _truthy(rec.get("paid")):
            row["paidRecords"] += 1
        lesson_text = _v398_format_record_lesson(rec)
        group_name = ""
        lesson = rec.get("lesson")
        if isinstance(lesson, dict):
            group_name = _lesson_group_value(lesson)
        if group_name:
            row["groups"][group_name] = row["groups"].get(group_name, 0) + 1
        if _truthy(rec.get("visit")):
            row["visits"] += 1
            if len(row["examplesVisited"]) < 3 and lesson_text:
                row["examplesVisited"].append(lesson_text)
        elif rec.get("visit") is not None:
            row["missed"] += 1
            if len(row["examplesMissed"]) < 5 and lesson_text:
                row["examplesMissed"].append(lesson_text)
    for row in stats.values():
        records_count = int(row.get("records") or 0)
        visits = int(row.get("visits") or 0)
        row["attendanceRate"] = round((visits / records_count) * 100, 1) if records_count else 0.0
        groups = row.get("groups") or {}
        row["topGroups"] = [name for name, _count in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return stats



# ---------------------------------------------------------------------------
# v3.9.10 student cards and per-student actions.
# ---------------------------------------------------------------------------


def _v3910_clean_user_id(value: Any) -> str:
    raw = str(value or "").strip()
    raw = raw.replace("#", "")
    m = re.search(r"\d+", raw)
    return m.group(0) if m else ""


def _v3910_user_name_from_card(user: dict[str, Any]) -> str:
    if not isinstance(user, dict):
        return ""
    return _v3991_user_name_from_user_obj(user) or str(_pick(user, ("name", "fullName", "fio")) or "").strip()


def _v3910_user_label(user: dict[str, Any] | None, user_id: str) -> str:
    user = user if isinstance(user, dict) else {}
    name = _v3910_user_name_from_card(user)
    return _v3991_user_label(name, user_id)


def _v3910_get_user_by_id(self: MoyKlassClient, user_id: int | str) -> MoyKlassResult:
    uid = _v3910_clean_user_id(user_id)
    if not uid:
        return MoyKlassResult(False, error="Нужен ID ученика", endpoint="/v1/company/users/{userId}")
    # includeJoins exists in official OpenAPI and returns user's group records when available.
    return self.request("GET", f"/v1/company/users/{uid}", params={"includeJoins": "true"})


def _v3910_get_user_joins(self: MoyKlassClient, user_id: int | str, limit: int = 100) -> MoyKlassResult:
    uid = _v3910_clean_user_id(user_id)
    if not uid:
        return MoyKlassResult(False, error="Нужен ID ученика", endpoint="/v1/company/joins")
    result = self.request("GET", "/v1/company/joins", params={"userId": uid, "limit": int(limit), "sort": "createdAt", "sortDirection": "desc"})
    if result.ok and isinstance(result.data, dict):
        result.data.setdefault("userId", uid)
    return result


def _v3910_get_user_subscriptions(self: MoyKlassClient, user_id: int | str, limit: int = 100) -> MoyKlassResult:
    uid = _v3910_clean_user_id(user_id)
    if not uid:
        return MoyKlassResult(False, error="Нужен ID ученика", endpoint="/v1/company/userSubscriptions")
    result = self.request("GET", "/v1/company/userSubscriptions", params={"userId": uid, "limit": int(limit)})
    if result.ok and isinstance(result.data, dict):
        result.data.setdefault("userId", uid)
    return result


def _v3910_filter_records_by_user(records: list[dict[str, Any]], user_id: str) -> list[dict[str, Any]]:
    uid = _v3910_clean_user_id(user_id)
    return [r for r in records if isinstance(r, dict) and _v3910_clean_user_id(_v399_user_id(r)) == uid]


def _v3910_filter_payments_by_user(payments: list[dict[str, Any]], user_id: str) -> list[dict[str, Any]]:
    uid = _v3910_clean_user_id(user_id)
    return [p for p in payments if isinstance(p, dict) and _v3910_clean_user_id(_v399_payment_user_id(p)) == uid]


def _v3910_user_month_bundle(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    uid = _v3910_clean_user_id(user_id)
    if not uid:
        return MoyKlassResult(False, error="Нужен ID ученика", endpoint="user-card")

    user_result = _v3910_get_user_by_id(self, uid)
    combined = _v3991_month_records_and_payments(self, month)
    joins_result = _v3910_get_user_joins(self, uid, limit=120)
    subs_result = _v3910_get_user_subscriptions(self, uid, limit=120)

    records_all = [x for x in (combined.data or {}).get("lessonRecords") or [] if isinstance(x, dict)] if combined.ok and isinstance(combined.data, dict) else []
    payments_all = [x for x in (combined.data or {}).get("payments") or [] if isinstance(x, dict)] if combined.ok and isinstance(combined.data, dict) else []
    records = _v3910_filter_records_by_user(records_all, uid)
    payments = _v3910_filter_payments_by_user(payments_all, uid)

    user_obj = user_result.data if user_result.ok and isinstance(user_result.data, dict) else {}
    name = _v3910_user_name_from_card(user_obj)
    for rec in records:
        if name:
            rec["_ycUserName"] = name
    for pay in payments:
        if name:
            pay["_ycUserName"] = name

    month_label = ""
    if combined.ok and isinstance(combined.data, dict):
        month_label = str(combined.data.get("month") or "")
    if not month_label:
        _s, _e, month_label = _month_bounds(month)

    data = {
        "userId": uid,
        "month": month_label,
        "user": user_obj,
        "userOk": user_result.ok,
        "userError": user_result.error,
        "records": records,
        "payments": payments,
        "joins": extract_items(joins_result.data) if joins_result.ok else [],
        "subscriptions": extract_items(subs_result.data) if subs_result.ok else [],
        "recordsOk": combined.ok,
        "paymentsOk": combined.ok,
        "joinsOk": joins_result.ok,
        "subscriptionsOk": subs_result.ok,
        "recordsError": combined.error if not combined.ok else "",
        "joinsError": joins_result.error if not joins_result.ok else "",
        "subscriptionsError": subs_result.error if not subs_result.ok else "",
    }
    ok = bool(user_result.ok or records or payments or joins_result.ok or subs_result.ok)
    return MoyKlassResult(ok, data=data, status=200 if ok else (user_result.status or combined.status or joins_result.status), error="" if ok else (user_result.error or combined.error or joins_result.error), endpoint="user-card")


def _v3910_get_user_card(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v3910_user_month_bundle(self, user_id, month)


def _v3910_get_user_lessons(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v3910_user_month_bundle(self, user_id, month)


def _v3910_get_user_payments(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v3910_user_month_bundle(self, user_id, month)


def _v3910_get_user_absences(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v3910_user_month_bundle(self, user_id, month)


def _v3910_join_class_text(join: dict[str, Any]) -> str:
    if not isinstance(join, dict):
        return "группа"
    cls = join.get("class") or join.get("group")
    if isinstance(cls, dict):
        name = _pick(cls, ("name", "title", "className", "groupName"))
        cid = _pick(cls, ("id", "classId", "groupId"))
        if name and cid:
            return f"{name} #{cid}"
        if name:
            return str(name)
        if cid:
            return f"группа #{cid}"
    cid = _pick(join, ("classId", "groupId", "courseClassId"))
    name = _pick(join, ("className", "groupName", "name"))
    if name and cid:
        return f"{name} #{cid}"
    if name:
        return str(name)
    if cid:
        return f"группа #{cid}"
    return "группа"


def _v3910_join_status_text(join: dict[str, Any]) -> str:
    status = str(_pick(join, ("statusId", "joinStatusId", "clientStateId", "status")) or "").strip()
    if not status:
        return "статус не указан"
    known = {
        "1": "новая / активная",
        "2": "учится / активная",
        "3": "завершена",
        "4": "отказ / архив",
    }
    return known.get(status, f"статус {status}")


def _v3910_user_contact_line(user: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    # Keep this concise: contacts are useful for manager, but we do not print too much personal data.
    phone = str(_pick(user, ("phone", "mainPhone")) or "").strip()
    email = str(_pick(user, ("email", "mail")) or "").strip()
    if phone:
        lines.append(f"Телефон: {phone}")
    if email:
        lines.append(f"Email: {email}")
    return lines


def _v3910_user_status_line(user: dict[str, Any]) -> str:
    client_state = str(_pick(user, ("clientStateId", "statusId", "stateId")) or "").strip()
    if client_state:
        return f"Статус клиента: {client_state}"
    return "Статус клиента: не указан"


def _v3910_user_balance_lines(user: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, label in (("balans", "Баланс"), ("availableBalance", "Доступный баланс"), ("bonusBalance", "Бонусы")):
        if key in user and user.get(key) not in (None, ""):
            lines.append(f"{label}: {_money_text(user.get(key))}")
    return lines


def _v3910_records_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    visits = 0
    missed = 0
    tests = 0
    paid_records = 0
    for rec in records:
        if not isinstance(rec, dict) or _truthy(rec.get("skip")):
            continue
        total += 1
        if _truthy(rec.get("visit")):
            visits += 1
        elif rec.get("visit") is not None:
            missed += 1
        if _truthy(rec.get("test")):
            tests += 1
        if _truthy(rec.get("paid")):
            paid_records += 1
    rate = round((visits / total) * 100, 1) if total else 0.0
    return {"records": total, "visits": visits, "missed": missed, "tests": tests, "paidRecords": paid_records, "rate": rate}


def _v3910_payment_stats(payments: list[dict[str, Any]]) -> dict[str, Any]:
    income = [p for p in payments if isinstance(p, dict) and str(p.get("optype") or "income").lower() == "income"]
    return {"payments": len(income), "sum": round(_sum_payment_amounts(income), 2)}


def _v3910_short_record_line(rec: dict[str, Any]) -> str:
    status = "✅" if _truthy(rec.get("visit")) else ("❌" if rec.get("visit") is not None else "▫️")
    free = " бесплатно" if _truthy(rec.get("free")) else ""
    test = " пробное" if _truthy(rec.get("test")) else ""
    paid = " платное" if _truthy(rec.get("paid")) else ""
    suffix = (free + test + paid).strip()
    suffix = f" | {suffix}" if suffix else ""
    return f"{status} {_v398_format_record_lesson(rec)}{suffix}"


def _v3910_short_payment_line(pay: dict[str, Any]) -> str:
    d = str(_pick(pay, ("date", "paymentDate", "createdAt", "paidAt")) or "")[:10]
    amount = _pick(pay, ("summa", "amount", "sum", "value", "paid", "price", "total", "money")) or "0"
    typ = str(_pick(pay, ("paymentTypeId", "type", "paymentType", "method")) or "тип не указан")
    return f"{d or 'дата?'} | {_money_text(amount)} | {typ}"


def format_user_card_result(result: MoyKlassResult, limit: int = 8) -> str:
    if not result.ok:
        return f"👤 Карточка ученика недоступна.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    uid = str(data.get("userId") or "").strip()
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    records = [x for x in data.get("records") or [] if isinstance(x, dict)]
    payments = [x for x in data.get("payments") or [] if isinstance(x, dict)]
    joins = [x for x in data.get("joins") or [] if isinstance(x, dict)]
    subs = [x for x in data.get("subscriptions") or [] if isinstance(x, dict)]
    rstat = _v3910_records_stats(records)
    pstat = _v3910_payment_stats(payments)
    label = _v3910_user_label(user, uid)
    created = str(_pick(user, ("createdAt",)) or "")[:10]
    updated = str(_pick(user, ("updatedAt",)) or "")[:10]

    lines = [
        f"👤 Карточка ученика {label}",
        "━━━━━━━━━━━━━━━━",
    ]
    if created:
        lines.append(f"Создан в МК: {created}")
    if updated:
        lines.append(f"Обновлён: {updated}")
    lines.append(_v3910_user_status_line(user))
    lines.extend(_v3910_user_contact_line(user))
    bal_lines = _v3910_user_balance_lines(user)
    if bal_lines:
        lines.append("")
        lines.append("💰 Балансы")
        lines.extend(bal_lines)

    month = data.get("month") or ""
    lines += [
        "",
        f"📊 Показатели за {month}".strip(),
        f"Записей на занятия: {rstat['records']}",
        f"Посещений: {rstat['visits']}",
        f"Пропусков: {rstat['missed']}",
        f"Посещаемость: {rstat['rate']}%",
        f"Оплат: {pstat['payments']}",
        f"Сумма оплат: {_money_text(pstat['sum'])}",
    ]

    if joins:
        lines += ["", "👥 Группы / записи"]
        for join in joins[:limit]:
            d = str(_pick(join, ("createdAt", "updatedAt", "stateChangedAt")) or "")[:10]
            d_text = f" | {d}" if d else ""
            lines.append(f"- {_v3910_join_class_text(join)} | {_v3910_join_status_text(join)}{d_text}")
    else:
        lines += ["", "👥 Группы / записи: не найдены или endpoint не отдал данные"]

    if subs:
        lines += ["", "🎫 Абонементы"]
        for sub in subs[:limit]:
            sid = _pick(sub, ("id", "userSubscriptionId", "subscriptionId")) or "?"
            status = _pick(sub, ("statusId", "status")) or "?"
            begin = str(_pick(sub, ("beginDate", "startDate", "sellDate")) or "")[:10]
            end = str(_pick(sub, ("endDate", "finishDate")) or "")[:10]
            dates = f" | {begin}-{end}" if begin or end else ""
            lines.append(f"- абонемент #{sid} | статус {status}{dates}")

    lines += [
        "",
        "Команды:",
        f"/mk_user_lessons {uid} {month} - занятия ученика",
        f"/mk_user_payments {uid} {month} - оплаты ученика",
        f"/mk_user_absences {uid} {month} - пропуски ученика",
        f"/mk_user_joins {uid} - группы ученика",
        f"/mk_prepare_user_comment {uid} текст - подготовить комментарий в МК",
    ]
    return "\n".join(lines)


def format_user_lessons_result(result: MoyKlassResult, limit: int = 40) -> str:
    if not result.ok:
        return f"📚 Занятия ученика недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    uid = str(data.get("userId") or "").strip()
    label = _v3910_user_label(data.get("user") if isinstance(data.get("user"), dict) else {}, uid)
    records = [x for x in data.get("records") or [] if isinstance(x, dict)]
    records.sort(key=lambda r: (_lesson_date_value(r.get("lesson") if isinstance(r.get("lesson"), dict) else r), _lesson_time_value(r.get("lesson") if isinstance(r.get("lesson"), dict) else r)))
    lines = [
        f"📚 Занятия ученика {label} {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Записей найдено: {len(records)}",
        "",
    ]
    if not records:
        lines.append("Записей за период не найдено.")
    for rec in records[:limit]:
        lines.append(f"- {_v3910_short_record_line(rec)}")
    if len(records) > limit:
        lines.append(f"...ещё {len(records) - limit}")
    return "\n".join(lines)


def format_user_payments_result(result: MoyKlassResult, limit: int = 40) -> str:
    if not result.ok:
        return f"💳 Оплаты ученика недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    uid = str(data.get("userId") or "").strip()
    label = _v3910_user_label(data.get("user") if isinstance(data.get("user"), dict) else {}, uid)
    payments_all = [x for x in data.get("payments") or [] if isinstance(x, dict)]
    payments = [p for p in payments_all if str(p.get("optype") or "income").lower() == "income"]
    payments.sort(key=lambda p: str(_pick(p, ("date", "paymentDate", "createdAt", "paidAt")) or ""))
    lines = [
        f"💳 Оплаты ученика {label} {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Оплат найдено: {len(payments)}",
        f"Сумма: {_money_text(_sum_payment_amounts(payments))}",
        "",
    ]
    if not payments:
        lines.append("Входящих оплат за период не найдено.")
    for pay in payments[:limit]:
        lines.append(f"- {_v3910_short_payment_line(pay)}")
    if len(payments) > limit:
        lines.append(f"...ещё {len(payments) - limit}")
    return "\n".join(lines)


def format_user_absences_result(result: MoyKlassResult, limit: int = 40) -> str:
    if not result.ok:
        return f"🔁 Пропуски ученика недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    uid = str(data.get("userId") or "").strip()
    label = _v3910_user_label(data.get("user") if isinstance(data.get("user"), dict) else {}, uid)
    records = [x for x in data.get("records") or [] if isinstance(x, dict)]
    missed = [r for r in records if r.get("visit") is not None and not _truthy(r.get("visit")) and not _truthy(r.get("skip"))]
    lines = [
        f"🔁 Пропуски ученика {label} {data.get('month', '')}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Пропусков найдено: {len(missed)}",
        "",
    ]
    if not missed:
        lines.append("Пропусков за период не найдено.")
    for rec in missed[:limit]:
        lines.append(f"- {_v398_format_record_lesson(rec)}")
    if len(missed) > limit:
        lines.append(f"...ещё {len(missed) - limit}")
    return "\n".join(lines)


def format_user_joins_result(result: MoyKlassResult, limit: int = 40) -> str:
    if not result.ok:
        return f"👥 Группы ученика недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    uid = str(data.get("userId") or "").strip()
    joins = [x for x in extract_items(result.data) if isinstance(x, dict)]
    lines = [
        f"👥 Группы ученика #{uid}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Записей в группы найдено: {len(joins)}",
        "",
    ]
    if not joins:
        lines.append("Записей в группы не найдено.")
    for join in joins[:limit]:
        d = str(_pick(join, ("createdAt", "updatedAt", "stateChangedAt")) or "")[:10]
        d_text = f" | {d}" if d else ""
        jid = _pick(join, ("id", "joinId")) or "?"
        lines.append(f"- #{jid} | {_v3910_join_class_text(join)} | {_v3910_join_status_text(join)}{d_text}")
    if len(joins) > limit:
        lines.append(f"...ещё {len(joins) - limit}")
    return "\n".join(lines)


MoyKlassClient.get_user_by_id = _v3910_get_user_by_id
MoyKlassClient.get_user_card = _v3910_get_user_card
MoyKlassClient.get_user_lessons_month = _v3910_get_user_lessons
MoyKlassClient.get_user_payments_month = _v3910_get_user_payments
MoyKlassClient.get_user_absences_month = _v3910_get_user_absences
MoyKlassClient.get_user_joins = _v3910_get_user_joins
MoyKlassClient.get_user_subscriptions = _v3910_get_user_subscriptions

# ---------------------------------------------------------------------------
# v3.9.10.1 improved student cards: readable group/status/subscription names,
# attention block, and safer contact visibility.
# ---------------------------------------------------------------------------

def _v39101_text_date(value: Any) -> str:
    raw = str(value or "").strip()
    return raw[:10] if raw else ""


def _v39101_get_ref_maps(self: MoyKlassClient) -> dict[str, dict[str, str]]:
    cached = getattr(self, "_yc_v39101_ref_maps", None)
    if isinstance(cached, dict):
        return cached

    base = self._lookup_maps_cached()
    maps: dict[str, dict[str, str]] = {
        "classes": dict(base.get("classes") or {}),
        "rooms": dict(base.get("rooms") or {}),
        "teachers": dict(base.get("teachers") or {}),
        "joinStatuses": {},
        "clientStatuses": {},
        "subscriptions": {},
    }

    join_statuses = self.request("GET", "/v1/company/joinStatuses", params={"limit": "1000"})
    if join_statuses.ok:
        maps["joinStatuses"].update(self._id_name_map_from_result(join_statuses, ("id", "statusId", "joinStatusId"), ("name", "title", "displayName")))

    client_statuses = self.request("GET", "/v1/company/clientStatuses", params={"limit": "1000"})
    if client_statuses.ok:
        maps["clientStatuses"].update(self._id_name_map_from_result(client_statuses, ("id", "statusId", "clientStateId"), ("name", "title", "displayName")))

    subscriptions = self.request("GET", "/v1/company/subscriptions", params={"limit": "1000"})
    if subscriptions.ok:
        maps["subscriptions"].update(self._id_name_map_from_result(subscriptions, ("id", "subscriptionId"), ("name", "title", "displayName", "externalId")))

    setattr(self, "_yc_v39101_ref_maps", maps)
    return maps


def _v39101_enrich_user(self: MoyKlassClient, user: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(user, dict):
        return user
    maps = _v39101_get_ref_maps(self)
    state_id = _pick(user, ("clientStateId", "statusId", "stateId"))
    if state_id and maps.get("clientStatuses", {}).get(str(state_id)):
        user["_ycClientStatusName"] = maps["clientStatuses"][str(state_id)]
    return user


def _v39101_enrich_join(self: MoyKlassClient, join: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(join, dict):
        return join
    maps = _v39101_get_ref_maps(self)
    class_id = _pick(join, ("classId", "groupId", "courseClassId", "mainClassId"))
    if class_id and maps.get("classes", {}).get(str(class_id)):
        join["_ycClassName"] = maps["classes"][str(class_id)]
    status_id = _pick(join, ("statusId", "joinStatusId", "clientStateId", "status"))
    if status_id and maps.get("joinStatuses", {}).get(str(status_id)):
        join["_ycJoinStatusName"] = maps["joinStatuses"][str(status_id)]
    return join


def _v39101_enrich_subscription(self: MoyKlassClient, sub: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(sub, dict):
        return sub
    maps = _v39101_get_ref_maps(self)
    subscription_id = _pick(sub, ("subscriptionId", "id"))
    if subscription_id and maps.get("subscriptions", {}).get(str(subscription_id)):
        sub["_ycSubscriptionName"] = maps["subscriptions"][str(subscription_id)]
    class_names: list[str] = []
    raw_class_ids = sub.get("classIds")
    if not isinstance(raw_class_ids, list):
        raw_class_ids = []
    main_class = _pick(sub, ("mainClassId",))
    ids = []
    if main_class:
        ids.append(str(main_class))
    for cid in raw_class_ids:
        if str(cid) not in ids:
            ids.append(str(cid))
    for cid in ids[:5]:
        name = maps.get("classes", {}).get(str(cid))
        class_names.append(f"{name} #{cid}" if name else f"группа #{cid}")
    if class_names:
        sub["_ycClassNames"] = class_names
    return sub


def _v39101_get_user_joins(self: MoyKlassClient, user_id: int | str, limit: int = 100) -> MoyKlassResult:
    result = _v3910_get_user_joins(self, user_id, limit=limit)
    if result.ok and isinstance(result.data, dict):
        for key in ("joins", "items", "data", "rows"):
            if isinstance(result.data.get(key), list):
                result.data[key] = [_v39101_enrich_join(self, x) if isinstance(x, dict) else x for x in result.data[key]]
        if isinstance(result.data, list):
            result.data = [_v39101_enrich_join(self, x) if isinstance(x, dict) else x for x in result.data]
    return result


def _v39101_get_user_subscriptions(self: MoyKlassClient, user_id: int | str, limit: int = 100) -> MoyKlassResult:
    result = _v3910_get_user_subscriptions(self, user_id, limit=limit)
    if result.ok and isinstance(result.data, dict):
        for key in ("subscriptions", "items", "data", "rows"):
            if isinstance(result.data.get(key), list):
                result.data[key] = [_v39101_enrich_subscription(self, x) if isinstance(x, dict) else x for x in result.data[key]]
        if isinstance(result.data, list):
            result.data = [_v39101_enrich_subscription(self, x) if isinstance(x, dict) else x for x in result.data]
    return result


def _v39101_user_month_bundle(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    uid = _v3910_clean_user_id(user_id)
    if not uid:
        return MoyKlassResult(False, error="Нужен ID ученика", endpoint="user-card")

    user_result = _v3910_get_user_by_id(self, uid)
    combined = _v3991_month_records_and_payments(self, month)
    joins_result = _v39101_get_user_joins(self, uid, limit=120)
    subs_result = _v39101_get_user_subscriptions(self, uid, limit=120)

    records_all = [x for x in (combined.data or {}).get("lessonRecords") or [] if isinstance(x, dict)] if combined.ok and isinstance(combined.data, dict) else []
    payments_all = [x for x in (combined.data or {}).get("payments") or [] if isinstance(x, dict)] if combined.ok and isinstance(combined.data, dict) else []
    records = _v3910_filter_records_by_user(records_all, uid)
    payments = _v3910_filter_payments_by_user(payments_all, uid)

    user_obj = user_result.data if user_result.ok and isinstance(user_result.data, dict) else {}
    if user_obj:
        user_obj = _v39101_enrich_user(self, user_obj)
    name = _v3910_user_name_from_card(user_obj)
    for rec in records:
        if name:
            rec["_ycUserName"] = name
        lesson = rec.get("lesson")
        if isinstance(lesson, dict):
            rec["lesson"] = self._enrich_lesson_item(lesson)
    for pay in payments:
        if name:
            pay["_ycUserName"] = name

    month_label = ""
    if combined.ok and isinstance(combined.data, dict):
        month_label = str(combined.data.get("month") or "")
    if not month_label:
        _s, _e, month_label = _month_bounds(month)

    data = {
        "userId": uid,
        "month": month_label,
        "user": user_obj,
        "userOk": user_result.ok,
        "userError": user_result.error,
        "records": records,
        "payments": payments,
        "joins": extract_items(joins_result.data) if joins_result.ok else [],
        "subscriptions": extract_items(subs_result.data) if subs_result.ok else [],
        "recordsOk": combined.ok,
        "paymentsOk": combined.ok,
        "joinsOk": joins_result.ok,
        "subscriptionsOk": subs_result.ok,
        "recordsError": combined.error if not combined.ok else "",
        "joinsError": joins_result.error if not joins_result.ok else "",
        "subscriptionsError": subs_result.error if not subs_result.ok else "",
    }
    ok = bool(user_result.ok or records or payments or joins_result.ok or subs_result.ok)
    return MoyKlassResult(ok, data=data, status=200 if ok else (user_result.status or combined.status or joins_result.status), error="" if ok else (user_result.error or combined.error or joins_result.error), endpoint="user-card")


def _v39101_get_user_card(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39101_user_month_bundle(self, user_id, month)


def _v39101_get_user_lessons(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39101_user_month_bundle(self, user_id, month)


def _v39101_get_user_payments(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39101_user_month_bundle(self, user_id, month)


def _v39101_get_user_absences(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39101_user_month_bundle(self, user_id, month)


def _v39101_join_class_text(join: dict[str, Any]) -> str:
    if not isinstance(join, dict):
        return "группа"
    pretty = str(join.get("_ycClassName") or "").strip()
    class_id = _pick(join, ("classId", "groupId", "courseClassId", "mainClassId"))
    if pretty and class_id:
        return f"{pretty} #{class_id}"
    if pretty:
        return pretty
    return _v3910_join_class_text(join)


def _v39101_join_status_text(join: dict[str, Any]) -> str:
    if not isinstance(join, dict):
        return "статус не указан"
    pretty = str(join.get("_ycJoinStatusName") or "").strip()
    status_id = str(_pick(join, ("statusId", "joinStatusId", "clientStateId", "status")) or "").strip()
    if pretty and status_id:
        return f"{pretty} #{status_id}"
    if pretty:
        return pretty
    known = {
        "1": "новая / активная",
        "2": "учится / активная",
        "3": "завершена",
        "4": "отказ / архив",
        "5": "отказ / архив",
    }
    return known.get(status_id, f"статус {status_id}" if status_id else "статус не указан")


def _v39101_user_status_line(user: dict[str, Any]) -> str:
    if not isinstance(user, dict):
        return "Статус клиента: не указан"
    state_id = str(_pick(user, ("clientStateId", "statusId", "stateId")) or "").strip()
    pretty = str(user.get("_ycClientStatusName") or "").strip()
    if pretty and state_id:
        return f"Статус клиента: {pretty} #{state_id}"
    if pretty:
        return f"Статус клиента: {pretty}"
    if state_id:
        return f"Статус клиента: ID {state_id}"
    return "Статус клиента: не указан"


def _v39101_subscription_status_text(sub: dict[str, Any]) -> str:
    status = str(_pick(sub, ("statusId", "status")) or "").strip()
    mapping = {
        "1": "не активный",
        "2": "активный",
        "3": "заморожен",
        "4": "окончен",
    }
    return mapping.get(status, f"статус {status}" if status else "статус не указан")


def _v39101_subscription_line(sub: dict[str, Any]) -> str:
    sid = _pick(sub, ("id", "userSubscriptionId")) or "?"
    subscription_id = _pick(sub, ("subscriptionId",))
    name = str(sub.get("_ycSubscriptionName") or "").strip()
    title = name or (f"вид #{subscription_id}" if subscription_id else "абонемент")
    status = _v39101_subscription_status_text(sub)
    sell = _v39101_text_date(_pick(sub, ("sellDate",)))
    begin = _v39101_text_date(_pick(sub, ("beginDate", "startDate")))
    end = _v39101_text_date(_pick(sub, ("endDate", "finishDate", "overDate")))
    price = _pick(sub, ("price", "originalPrice"))
    payed = _pick(sub, ("payed", "paid", "paidSum"))
    debt = _pick(sub, ("remindSumm", "debt", "debtSum"))
    visit_count = _pick(sub, ("visitCount", "visitsCount", "count"))
    visited_count = _pick(sub, ("visitedCount", "usedVisitCount", "spentCount"))
    remaining = ""
    try:
        if str(visit_count).strip() != "" and str(visited_count).strip() != "":
            remaining_value = int(float(str(visit_count))) - int(float(str(visited_count)))
            remaining = f" | остаток {remaining_value}/{visit_count}"
    except Exception:
        remaining = ""
    dates = []
    if sell:
        dates.append(f"продажа {sell}")
    if begin or end:
        dates.append(f"срок {begin or '?'}-{end or '?'}")
    money = []
    if price not in (None, ""):
        money.append(f"цена {_money_text(price)}")
    if payed not in (None, ""):
        money.append(f"оплачено {_money_text(payed)}")
    if debt not in (None, "", 0, "0"):
        money.append(f"долг {_money_text(debt)}")
    parts = [f"- {title} #{sid}", status]
    if dates:
        parts.append(", ".join(dates))
    if money:
        parts.append(", ".join(money))
    if remaining:
        parts.append(remaining.strip(" |"))
    class_names = sub.get("_ycClassNames")
    if isinstance(class_names, list) and class_names:
        parts.append("группы: " + "; ".join(map(str, class_names[:3])))
    return " | ".join(str(x) for x in parts if str(x).strip())


def _v39101_attention_lines(user: dict[str, Any], rstat: dict[str, Any], pstat: dict[str, Any], subs: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    records = int(rstat.get("records") or 0)
    missed = int(rstat.get("missed") or 0)
    rate = float(rstat.get("rate") or 0)
    payments = int(pstat.get("payments") or 0)
    balance_raw = user.get("balans") if isinstance(user, dict) else None
    if records >= 2 and rate < 60:
        warnings.append(f"низкая посещаемость: {rate}%")
    if missed > 0:
        warnings.append(f"пропусков за период: {missed}")
    if records > 0 and payments == 0:
        warnings.append("нет входящих оплат за выбранный месяц")
    try:
        if balance_raw not in (None, "") and float(str(balance_raw).replace(",", ".")) < 0:
            warnings.append(f"отрицательный баланс: {_money_text(balance_raw)}")
    except Exception:
        pass
    for sub in subs[:10]:
        debt = _pick(sub, ("remindSumm", "debt", "debtSum"))
        try:
            if debt not in (None, "", 0, "0") and float(str(debt).replace(",", ".")) > 0:
                warnings.append(f"есть долг по абонементу: {_money_text(debt)}")
                break
        except Exception:
            continue
    if not warnings:
        return ["✅ Явных проблем по выбранному месяцу не найдено."]
    return [f"⚠️ {x}" for x in warnings[:6]]


def format_user_card_result(result: MoyKlassResult, limit: int = 8, *, role: str | None = None, show_contacts: bool | None = None) -> str:  # type: ignore[no-redef]
    if not result.ok:
        return f"👤 Карточка ученика недоступна.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    uid = str(data.get("userId") or "").strip()
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    records = [x for x in data.get("records") or [] if isinstance(x, dict)]
    payments = [x for x in data.get("payments") or [] if isinstance(x, dict)]
    joins = [x for x in data.get("joins") or [] if isinstance(x, dict)]
    subs = [x for x in data.get("subscriptions") or [] if isinstance(x, dict)]
    rstat = _v3910_records_stats(records)
    pstat = _v3910_payment_stats(payments)
    label = _v3910_user_label(user, uid)
    created = _v39101_text_date(_pick(user, ("createdAt",)))
    updated = _v39101_text_date(_pick(user, ("updatedAt",)))
    if show_contacts is None:
        # Safer default: do not print phone/email unless the caller explicitly allows it.
        show_contacts = False

    lines = [
        f"👤 Карточка ученика {label}",
        "━━━━━━━━━━━━━━━━",
    ]
    if created:
        lines.append(f"Создан в МК: {created}")
    if updated:
        lines.append(f"Обновлён: {updated}")
    lines.append(_v39101_user_status_line(user))
    if show_contacts:
        lines.extend(_v3910_user_contact_line(user))
    else:
        if _v3910_user_contact_line(user):
            lines.append("Контакты: скрыты")
    bal_lines = _v3910_user_balance_lines(user)
    if bal_lines:
        lines.append("")
        lines.append("💰 Балансы")
        lines.extend(bal_lines)

    month = data.get("month") or ""
    lines += [
        "",
        f"📊 Показатели за {month}".strip(),
        f"Записей на занятия: {rstat['records']}",
        f"Посещений: {rstat['visits']}",
        f"Пропусков: {rstat['missed']}",
        f"Посещаемость: {rstat['rate']}%",
        f"Оплат: {pstat['payments']}",
        f"Сумма оплат: {_money_text(pstat['sum'])}",
        "",
        "⚠️ Требует внимания",
    ]
    lines.extend(_v39101_attention_lines(user, rstat, pstat, subs))

    if joins:
        lines += ["", "👥 Группы / записи"]
        for join in joins[:limit]:
            d = _v39101_text_date(_pick(join, ("createdAt", "updatedAt", "stateChangedAt")))
            d_text = f" | {d}" if d else ""
            lines.append(f"- {_v39101_join_class_text(join)} | {_v39101_join_status_text(join)}{d_text}")
    else:
        lines += ["", "👥 Группы / записи: не найдены или endpoint не отдал данные"]

    if subs:
        lines += ["", "🎫 Абонементы"]
        for sub in subs[:limit]:
            lines.append(_v39101_subscription_line(sub))

    lines += [
        "",
        "Команды:",
        f"/mk_user_lessons {uid} {month} - занятия ученика",
        f"/mk_user_payments {uid} {month} - оплаты ученика",
        f"/mk_user_absences {uid} {month} - пропуски ученика",
        f"/mk_user_joins {uid} - группы ученика",
        f"/mk_prepare_user_comment {uid} текст - подготовить комментарий в МК",
    ]
    return "\n".join(lines)


def format_user_joins_result(result: MoyKlassResult, limit: int = 40) -> str:  # type: ignore[no-redef]
    if not result.ok:
        return f"👥 Группы ученика недоступны.\nstatus={result.status}\n{result.error}"
    data = result.data if isinstance(result.data, dict) else {}
    uid = str(data.get("userId") or "").strip()
    joins = [x for x in extract_items(result.data) if isinstance(x, dict)]
    lines = [
        f"👥 Группы ученика #{uid}".strip(),
        "━━━━━━━━━━━━━━━━",
        f"Записей в группы найдено: {len(joins)}",
        "",
    ]
    if not joins:
        lines.append("Записей в группы не найдено.")
    for join in joins[:limit]:
        created = _v39101_text_date(_pick(join, ("createdAt",)))
        changed = _v39101_text_date(_pick(join, ("stateChangedAt", "updatedAt")))
        dates = []
        if created:
            dates.append(f"создано {created}")
        if changed and changed != created:
            dates.append(f"изм. {changed}")
        d_text = f" | {', '.join(dates)}" if dates else ""
        jid = _pick(join, ("id", "joinId")) or "?"
        lines.append(f"- #{jid} | {_v39101_join_class_text(join)} | {_v39101_join_status_text(join)}{d_text}")
    if len(joins) > limit:
        lines.append(f"...ещё {len(joins) - limit}")
    return "\n".join(lines)


MoyKlassClient.get_user_card = _v39101_get_user_card
MoyKlassClient.get_user_lessons_month = _v39101_get_user_lessons
MoyKlassClient.get_user_payments_month = _v39101_get_user_payments
MoyKlassClient.get_user_absences_month = _v39101_get_user_absences
MoyKlassClient.get_user_joins = _v39101_get_user_joins
MoyKlassClient.get_user_subscriptions = _v39101_get_user_subscriptions

# ---------------------------------------------------------------------------
# v3.9.10.2 clickable student search results.
# ---------------------------------------------------------------------------

def user_id_value(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("user", "student", "client", "customer"):
        value = item.get(key)
        if isinstance(value, dict):
            nested = user_id_value(value)
            if nested:
                return nested
    return str(_pick(item, ("id", "userId", "studentId", "clientId", "customerId", "idUser")) or "").strip()


def user_name_value(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("user", "student", "client", "customer"):
        value = item.get(key)
        if isinstance(value, dict):
            nested = user_name_value(value)
            if nested:
                return nested
    direct = _pick(item, ("fullName", "fio", "title", "displayName", "name", "userName", "studentName", "clientName", "customerName"))
    if direct:
        return str(direct).strip()
    parts = []
    for key in ("lastName", "surname", "secondName", "firstName", "middleName", "patronymic"):
        value = item.get(key)
        if value:
            parts.append(str(value).strip())
    return " ".join(x for x in parts if x).strip()


def user_open_button_label(item: dict[str, Any], idx: int = 1) -> str:
    uid = user_id_value(item)
    name = user_name_value(item)
    base = name or (f"Ученик #{uid}" if uid else f"Ученик {idx}")
    base = re.sub(r"\s+", " ", str(base)).strip()
    if len(base) > 38:
        base = base[:35].rstrip() + "..."
    if uid and f"#{uid}" not in base:
        base = f"{base} #{uid}"
    return f"👤 {base}"


def _v39102_get_user_joins(self: MoyKlassClient, user_id: int | str, limit: int = 100) -> MoyKlassResult:
    result = _v3910_get_user_joins(self, user_id, limit=limit)
    if not result.ok:
        return result
    if isinstance(result.data, dict):
        for key in ("joins", "items", "data", "rows"):
            if isinstance(result.data.get(key), list):
                result.data[key] = [_v39101_enrich_join(self, x) if isinstance(x, dict) else x for x in result.data[key]]
    elif isinstance(result.data, list):
        result.data = [_v39101_enrich_join(self, x) if isinstance(x, dict) else x for x in result.data]
    return result


def _v39102_get_user_subscriptions(self: MoyKlassClient, user_id: int | str, limit: int = 100) -> MoyKlassResult:
    result = _v3910_get_user_subscriptions(self, user_id, limit=limit)
    if not result.ok:
        return result
    if isinstance(result.data, dict):
        for key in ("subscriptions", "items", "data", "rows"):
            if isinstance(result.data.get(key), list):
                result.data[key] = [_v39101_enrich_subscription(self, x) if isinstance(x, dict) else x for x in result.data[key]]
    elif isinstance(result.data, list):
        result.data = [_v39101_enrich_subscription(self, x) if isinstance(x, dict) else x for x in result.data]
    return result


def _v39102_user_month_bundle(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    uid = _v3910_clean_user_id(user_id)
    if not uid:
        return MoyKlassResult(False, error="Нужен ID ученика", endpoint="user-card")

    user_result = _v3910_get_user_by_id(self, uid)
    combined = _v3991_month_records_and_payments(self, month)
    joins_result = _v39102_get_user_joins(self, uid, limit=120)
    subs_result = _v39102_get_user_subscriptions(self, uid, limit=120)

    records_all = [x for x in (combined.data or {}).get("lessonRecords") or [] if isinstance(x, dict)] if combined.ok and isinstance(combined.data, dict) else []
    payments_all = [x for x in (combined.data or {}).get("payments") or [] if isinstance(x, dict)] if combined.ok and isinstance(combined.data, dict) else []
    records = _v3910_filter_records_by_user(records_all, uid)
    payments = _v3910_filter_payments_by_user(payments_all, uid)

    user_obj = user_result.data if user_result.ok and isinstance(user_result.data, dict) else {}
    if user_obj:
        user_obj = _v39101_enrich_user(self, user_obj)
    name = _v3910_user_name_from_card(user_obj)
    for rec in records:
        if name:
            rec["_ycUserName"] = name
        lesson = rec.get("lesson")
        if isinstance(lesson, dict):
            rec["lesson"] = self._enrich_lesson_item(lesson)
    for pay in payments:
        if name:
            pay["_ycUserName"] = name

    month_label = ""
    if combined.ok and isinstance(combined.data, dict):
        month_label = str(combined.data.get("month") or "")
    if not month_label:
        _s, _e, month_label = _month_bounds(month)

    data = {
        "userId": uid,
        "month": month_label,
        "user": user_obj,
        "userOk": user_result.ok,
        "userError": user_result.error,
        "records": records,
        "payments": payments,
        "joins": extract_items(joins_result.data) if joins_result.ok else [],
        "subscriptions": extract_items(subs_result.data) if subs_result.ok else [],
        "recordsOk": combined.ok,
        "paymentsOk": combined.ok,
        "joinsOk": joins_result.ok,
        "subscriptionsOk": subs_result.ok,
        "recordsError": combined.error if not combined.ok else "",
        "joinsError": joins_result.error if not joins_result.ok else "",
        "subscriptionsError": subs_result.error if not subs_result.ok else "",
    }
    ok = bool(user_result.ok or records or payments or joins_result.ok or subs_result.ok)
    return MoyKlassResult(ok, data=data, status=200 if ok else (user_result.status or combined.status or joins_result.status), error="" if ok else (user_result.error or combined.error or joins_result.error), endpoint="user-card")


def _v39102_get_user_card(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39102_user_month_bundle(self, user_id, month)


def _v39102_get_user_lessons(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39102_user_month_bundle(self, user_id, month)


def _v39102_get_user_payments(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39102_user_month_bundle(self, user_id, month)


def _v39102_get_user_absences(self: MoyKlassClient, user_id: int | str, month: str | None = None) -> MoyKlassResult:
    return _v39102_user_month_bundle(self, user_id, month)


MoyKlassClient.get_user_card = _v39102_get_user_card
MoyKlassClient.get_user_lessons_month = _v39102_get_user_lessons
MoyKlassClient.get_user_payments_month = _v39102_get_user_payments
MoyKlassClient.get_user_absences_month = _v39102_get_user_absences
MoyKlassClient.get_user_joins = _v39102_get_user_joins
MoyKlassClient.get_user_subscriptions = _v39102_get_user_subscriptions

