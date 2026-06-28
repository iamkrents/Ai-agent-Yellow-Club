from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


NOTION_API_BASE = "https://api.notion.com"
UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})"
)


@dataclass
class NotionApiResult:
    ok: bool
    status: int = 0
    data: Any = None
    error: str = ""
    endpoint: str = ""


@dataclass
class NotionPageSyncResult:
    source_id: str
    title: str = ""
    filename: str = ""
    ok: bool = False
    status: int = 0
    error: str = ""
    truncated: bool = False
    skipped: bool = False
    changed: bool = True
    last_edited_time: str = ""
    notion_url: str = ""
    path_titles: list[str] = field(default_factory=list)
    unknown_block_ids: list[str] = field(default_factory=list)


@dataclass
class NotionSyncResult:
    ok: bool
    root_pages: int = 0
    root_databases: int = 0
    discovered_pages: int = 0
    discovered_databases: int = 0
    pages_total: int = 0
    pages_saved: int = 0
    pages_skipped: int = 0
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages: list[NotionPageSyncResult] = field(default_factory=list)
    sync_dir: str = ""
    recursive: bool = True
    clean_used: bool = False
    mode: str = "full"
    manifest_path: str = ""


class NotionClient:
    """Small read-only Notion client for syncing pages into Markdown files.

    The client keeps Notion data as local Markdown files in data/notion.
    Regular sync is safe by default: it overwrites files for the same page ID,
    but does not delete older files. Use clean sync only when you intentionally
    want to rebuild the Notion folder from zero.
    """

    def __init__(
        self,
        token: str,
        *,
        page_ids: list[str] | None = None,
        database_ids: list[str] | None = None,
        sync_dir: Path | str = "data/notion",
        timeout: int = 30,
        api_version: str = "2026-03-11",
        database_api_version: str = "2022-06-28",
        clean_before_sync: bool = False,
        recursive: bool = True,
        recursive_max_depth: int = 6,
    ):
        self.token = (token or "").strip().strip('"').strip("'")
        self.page_ids = normalize_id_list(page_ids or [])
        self.database_ids = normalize_id_list(database_ids or [])
        self.sync_dir = Path(sync_dir)
        self.manifest_path = self.sync_dir.parent / "notion_manifest.json"
        self.timeout = int(timeout or 30)
        self.api_version = (api_version or "2026-03-11").strip()
        self.database_api_version = (database_api_version or "2022-06-28").strip()
        self.clean_before_sync = bool(clean_before_sync)
        self.recursive = bool(recursive)
        self.recursive_max_depth = max(0, int(recursive_max_depth or 0))

    @property
    def enabled(self) -> bool:
        return bool(self.token and not self.token.upper().startswith("PASTE_") and (self.page_ids or self.database_ids))

    def test(self) -> NotionApiResult:
        if not self.token:
            return NotionApiResult(False, error="NOTION_TOKEN is empty")
        return self._request("GET", "/v1/users/me")

    def sync(self, *, clean: bool | None = None, fast: bool = False) -> NotionSyncResult:
        """Sync configured Notion roots.

        Full sync downloads every discovered page.
        Fast sync still walks the Notion tree, but skips Markdown download for
        pages whose last_edited_time is unchanged in data/notion_manifest.json.
        """
        result = NotionSyncResult(
            ok=False,
            root_pages=len(self.page_ids),
            root_databases=len(self.database_ids),
            sync_dir=str(self.sync_dir),
            recursive=self.recursive,
            clean_used=self.clean_before_sync if clean is None else bool(clean),
            mode="fast" if fast else "full",
            manifest_path=str(self.manifest_path),
        )
        if not self.token:
            result.errors.append("NOTION_TOKEN не указан в .env")
            return result
        if not self.page_ids and not self.database_ids:
            result.errors.append("NOTION_PAGE_IDS и NOTION_DATABASE_IDS пустые")
            return result

        self.sync_dir.mkdir(parents=True, exist_ok=True)
        if result.clean_used:
            self._clean_sync_dir()

        manifest = self.load_manifest()
        if result.clean_used:
            manifest = self._empty_manifest()

        page_titles, page_paths, discovered_databases, errors = self._discover_configured_pages()
        result.errors.extend(errors)
        result.discovered_pages = max(0, len(page_titles) - len(self.page_ids))
        result.discovered_databases = len(discovered_databases)
        result.pages_total = len(page_titles)

        manifest_pages = manifest.setdefault("pages", {})
        used_names: set[str] = set()
        for page_id, title in page_titles.items():
            old_entry = manifest_pages.get(page_id) if isinstance(manifest_pages, dict) else None
            item = self.sync_page(
                page_id,
                preferred_title=title,
                preferred_path=page_paths.get(page_id) or [],
                used_names=used_names,
                fast=bool(fast) and not result.clean_used,
                manifest_entry=old_entry if isinstance(old_entry, dict) else None,
            )
            result.pages.append(item)
            if item.ok:
                manifest_pages[page_id] = self._manifest_entry_from_item(item)
                if item.skipped:
                    result.pages_skipped += 1
                else:
                    result.pages_saved += 1
                    if item.filename:
                        result.files.append(item.filename)
            else:
                result.errors.append(f"Page {short_id(page_id)}: status={item.status} {item.error}")

        manifest["last_sync_mode"] = result.mode
        manifest["last_sync_pages_total"] = result.pages_total
        manifest["last_sync_pages_saved"] = result.pages_saved
        manifest["last_sync_pages_skipped"] = result.pages_skipped
        self.save_manifest(manifest)

        result.ok = result.pages_saved > 0 or result.pages_skipped > 0
        return result

    def sync_fast(self) -> NotionSyncResult:
        return self.sync(clean=False, fast=True)

    def sync_single_page(self, page_ref: str) -> NotionSyncResult:
        """Sync only one Notion page by URL or ID and update manifest."""
        page_id = normalize_notion_id(page_ref)
        result = NotionSyncResult(
            ok=False,
            root_pages=1 if page_id else 0,
            root_databases=0,
            sync_dir=str(self.sync_dir),
            recursive=False,
            clean_used=False,
            mode="page",
            manifest_path=str(self.manifest_path),
        )
        if not self.token:
            result.errors.append("NOTION_TOKEN не указан в .env")
            return result
        if not page_id:
            result.errors.append("Не удалось распознать ID страницы Notion. Пришлите ссылку на страницу или page ID.")
            return result

        self.sync_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.load_manifest()
        manifest_pages = manifest.setdefault("pages", {})
        old_entry = manifest_pages.get(page_id) if isinstance(manifest_pages, dict) else None
        preferred_path = []
        if isinstance(old_entry, dict) and isinstance(old_entry.get("path_titles"), list):
            preferred_path = [str(x) for x in old_entry.get("path_titles") if str(x or "").strip()]
        item = self.sync_page(
            page_id,
            preferred_title=str(old_entry.get("title") or "") if isinstance(old_entry, dict) else "",
            preferred_path=preferred_path,
            used_names=set(),
            fast=False,
            manifest_entry=old_entry if isinstance(old_entry, dict) else None,
        )
        result.pages.append(item)
        result.pages_total = 1
        if item.ok:
            manifest_pages[page_id] = self._manifest_entry_from_item(item)
            result.pages_saved = 1
            if item.filename:
                result.files.append(item.filename)
        else:
            result.errors.append(f"Page {short_id(page_id)}: status={item.status} {item.error}")
        manifest["last_sync_mode"] = result.mode
        manifest["last_sync_pages_total"] = result.pages_total
        manifest["last_sync_pages_saved"] = result.pages_saved
        manifest["last_sync_pages_skipped"] = 0
        self.save_manifest(manifest)
        result.ok = item.ok
        return result

    def _discover_configured_pages(self) -> tuple["OrderedDict[str, str]", "OrderedDict[str, list[str]]", set[str], list[str]]:
        page_titles: "OrderedDict[str, str]" = OrderedDict()
        page_paths: "OrderedDict[str, list[str]]" = OrderedDict()
        discovered_databases: set[str] = set()
        visited_pages_for_discovery: set[str] = set()
        visited_databases_for_discovery: set[str] = set()
        errors: list[str] = []

        def add_page(page_id: str, title: str = "", path: list[str] | None = None) -> None:
            pid = normalize_notion_id(page_id)
            if not pid:
                return
            if pid not in page_titles:
                page_titles[pid] = title or ""
            elif title and not page_titles.get(pid):
                page_titles[pid] = title
            if path:
                page_paths[pid] = [x for x in path if str(x or "").strip()]
            elif title and pid not in page_paths:
                page_paths[pid] = [title]

        for page_id in self.page_ids:
            root_title = ""
            page_info = self.retrieve_page(page_id)
            if page_info.ok:
                root_title = extract_page_title(page_info.data) or ""
            add_page(page_id, title=root_title, path=[root_title] if root_title else None)
            if self.recursive:
                self._discover_from_page(
                    page_id,
                    page_titles=page_titles,
                    page_paths=page_paths,
                    discovered_databases=discovered_databases,
                    visited_pages=visited_pages_for_discovery,
                    visited_databases=visited_databases_for_discovery,
                    errors=errors,
                    depth=0,
                )

        for db_id in self.database_ids:
            self._discover_from_database(
                db_id,
                page_titles=page_titles,
                page_paths=page_paths,
                discovered_databases=discovered_databases,
                visited_pages=visited_pages_for_discovery,
                visited_databases=visited_databases_for_discovery,
                errors=errors,
                depth=0,
            )
        return page_titles, page_paths, discovered_databases, errors

    def _discover_from_page(
        self,
        page_id: str,
        *,
        page_titles: "OrderedDict[str, str]",
        page_paths: "OrderedDict[str, list[str]]",
        discovered_databases: set[str],
        visited_pages: set[str],
        visited_databases: set[str],
        errors: list[str],
        depth: int,
    ) -> None:
        page_id = normalize_notion_id(page_id)
        if not page_id or page_id in visited_pages or depth > self.recursive_max_depth:
            return
        visited_pages.add(page_id)

        blocks = self.list_block_children(page_id)
        if not blocks.ok:
            errors.append(f"Discover page {short_id(page_id)}: status={blocks.status} {blocks.error}")
            return

        parent_path = page_paths.get(page_id) or ([page_titles.get(page_id)] if page_titles.get(page_id) else [short_id(page_id)])

        for block in _extract_items(blocks.data):
            if not isinstance(block, dict):
                continue
            block_id = normalize_notion_id(str(block.get("id") or ""))
            btype = block.get("type") or ""
            payload = block.get(btype) if isinstance(block.get(btype), dict) else {}

            if btype == "child_page" and block_id:
                title = str(payload.get("title") or "").strip()
                if block_id not in page_titles:
                    page_titles[block_id] = title
                elif title and not page_titles.get(block_id):
                    page_titles[block_id] = title
                if title:
                    page_paths[block_id] = [*parent_path, title]
                self._discover_from_page(
                    block_id,
                    page_titles=page_titles,
                    page_paths=page_paths,
                    discovered_databases=discovered_databases,
                    visited_pages=visited_pages,
                    visited_databases=visited_databases,
                    errors=errors,
                    depth=depth + 1,
                )
                continue

            if btype == "child_database" and block_id:
                discovered_databases.add(block_id)
                self._discover_from_database(
                    block_id,
                    page_titles=page_titles,
                    page_paths=page_paths,
                    discovered_databases=discovered_databases,
                    parent_path=[*parent_path, str(payload.get("title") or "Вложенная база")],
                    visited_pages=visited_pages,
                    visited_databases=visited_databases,
                    errors=errors,
                    depth=depth + 1,
                )
                continue

            # Some Notion blocks can contain nested lists/toggles/callouts.
            # Walk them too, so child pages inside toggles are not skipped.
            if block.get("has_children") and block_id and depth < self.recursive_max_depth:
                self._discover_from_page(
                    block_id,
                    page_titles=page_titles,
                    page_paths=page_paths,
                    discovered_databases=discovered_databases,
                    visited_pages=visited_pages,
                    visited_databases=visited_databases,
                    errors=errors,
                    depth=depth + 1,
                )

    def _discover_from_database(
        self,
        database_id: str,
        *,
        page_titles: "OrderedDict[str, str]",
        page_paths: "OrderedDict[str, list[str]]",
        discovered_databases: set[str],
        visited_pages: set[str],
        visited_databases: set[str],
        errors: list[str],
        depth: int,
        parent_path: list[str] | None = None,
    ) -> None:
        database_id = normalize_notion_id(database_id)
        if not database_id or database_id in visited_databases or depth > self.recursive_max_depth:
            return
        visited_databases.add(database_id)
        discovered_databases.add(database_id)

        pages = self.query_database_pages(database_id)
        if not pages.ok:
            errors.append(f"Database {short_id(database_id)}: status={pages.status} {pages.error}")
            return

        for page in _extract_items(pages.data):
            if not isinstance(page, dict):
                continue
            pid = normalize_notion_id(str(page.get("id") or ""))
            if not pid:
                continue
            title = extract_page_title(page)
            if pid not in page_titles:
                page_titles[pid] = title
            elif title and not page_titles.get(pid):
                page_titles[pid] = title
            if title:
                page_paths[pid] = [*(parent_path or []), title]
            if self.recursive and depth < self.recursive_max_depth:
                self._discover_from_page(
                    pid,
                    page_titles=page_titles,
                    page_paths=page_paths,
                    discovered_databases=discovered_databases,
                    visited_pages=visited_pages,
                    visited_databases=visited_databases,
                    errors=errors,
                    depth=depth + 1,
                )

    def sync_page(
        self,
        page_id: str,
        *,
        preferred_title: str = "",
        preferred_path: list[str] | None = None,
        used_names: set[str] | None = None,
        fast: bool = False,
        manifest_entry: dict[str, Any] | None = None,
    ) -> NotionPageSyncResult:
        page_id = normalize_notion_id(page_id)
        item = NotionPageSyncResult(source_id=page_id)
        if not page_id:
            item.error = "invalid page id"
            return item

        page_info = self.retrieve_page(page_id)
        title = preferred_title
        page_url = ""
        last_edited_time = ""
        if page_info.ok:
            page_data = page_info.data if isinstance(page_info.data, dict) else {}
            title = extract_page_title(page_data) or title
            page_url = str(page_data.get("url") or page_data.get("public_url") or "").strip()
            last_edited_time = str(page_data.get("last_edited_time") or "").strip()
        elif not title:
            title = f"Notion page {short_id(page_id)}"

        item.title = title or f"Notion page {short_id(page_id)}"
        item.notion_url = page_url
        item.last_edited_time = last_edited_time
        item.path_titles = [x for x in (preferred_path or []) if str(x or "").strip()]

        if used_names is None:
            used_names = set()

        old_file = ""
        old_last_edited = ""
        if isinstance(manifest_entry, dict):
            old_file = str(manifest_entry.get("file_path") or manifest_entry.get("filename") or "")
            old_last_edited = str(manifest_entry.get("last_edited_time") or "")

        # In fast mode, unchanged pages are not downloaded again.
        # Notion's last_edited_time changes when the page content/properties change.
        if fast and last_edited_time and old_last_edited == last_edited_time and old_file:
            old_path = self.sync_dir.parent / old_file
            if old_path.exists():
                item.ok = True
                item.status = page_info.status or 200
                item.filename = old_file.replace("\\", "/")
                item.skipped = True
                item.changed = False
                return item

        filename = unique_stable_filename(item.title or page_id, page_id, used_names)
        path = self.sync_dir / filename

        md_result = self.retrieve_page_markdown(page_id)
        markdown = ""
        truncated = False
        unknown_block_ids: list[str] = []
        if md_result.ok:
            data = md_result.data if isinstance(md_result.data, dict) else {}
            markdown = str(data.get("markdown") or "").strip()
            truncated = bool(data.get("truncated"))
            unknown_block_ids = [str(x) for x in (data.get("unknown_block_ids") or [])]
        else:
            block_result = self.retrieve_blocks_markdown(page_id)
            if block_result.ok:
                markdown = str(block_result.data or "").strip()
            else:
                item.status = md_result.status or block_result.status
                item.error = md_result.error or block_result.error
                return item

        if not markdown:
            markdown = "_Страница пуста или содержит только неподдерживаемые блоки._"

        item.truncated = truncated
        item.unknown_block_ids = unknown_block_ids

        # Remove the legacy v3.9.11 filename for this same title, if it exists.
        legacy_path = self.sync_dir / f"{slugify(item.title or page_id)}.md"
        if legacy_path.exists() and legacy_path.resolve() != path.resolve():
            try:
                legacy_path.unlink()
            except Exception:
                pass

        # If this page had another filename in the manifest, remove only that old file.
        if old_file:
            old_path = self.sync_dir.parent / old_file
            if old_path.exists() and old_path.resolve() != path.resolve():
                try:
                    old_path.unlink()
                except Exception:
                    pass

        header = [
            f"# {item.title}",
            "",
            "Источник: Notion",
            f"Notion page ID: {page_id}",
        ]
        if page_url:
            header.append(f"Notion URL: {page_url}")
        if last_edited_time:
            header.append(f"Notion last edited: {last_edited_time}")
        if item.path_titles:
            header.append("Путь Notion: " + " / ".join(item.path_titles))
        if truncated:
            header.append("Предупреждение: Notion вернул страницу не полностью, часть блоков была усечена.")
        header.append("")
        path.write_text("\n".join(header) + "\n" + markdown.strip() + "\n", encoding="utf-8")
        try:
            data_dir = self.sync_dir.parent
            item.filename = str(path.relative_to(data_dir)).replace("\\", "/")
        except Exception:
            item.filename = filename
        item.ok = True
        item.status = md_result.status or 200
        item.skipped = False
        item.changed = True
        return item

    def retrieve_page_markdown(self, page_id: str) -> NotionApiResult:
        page_id = normalize_notion_id(page_id)
        return self._request("GET", f"/v1/pages/{urllib.parse.quote(page_id)}/markdown", version=self.api_version)

    def retrieve_page(self, page_id: str) -> NotionApiResult:
        page_id = normalize_notion_id(page_id)
        return self._request("GET", f"/v1/pages/{urllib.parse.quote(page_id)}", version=self.api_version)

    def query_database_pages(self, database_id: str, *, page_size: int = 100) -> NotionApiResult:
        database_id = normalize_notion_id(database_id)
        all_pages: list[dict] = []
        cursor = None
        status = 0
        while True:
            body: dict[str, Any] = {"page_size": min(max(int(page_size), 1), 100)}
            if cursor:
                body["start_cursor"] = cursor
            res = self._request(
                "POST",
                f"/v1/databases/{urllib.parse.quote(database_id)}/query",
                body=body,
                version=self.database_api_version,
            )
            status = res.status
            if not res.ok:
                return res
            data = res.data if isinstance(res.data, dict) else {}
            all_pages.extend([x for x in (data.get("results") or []) if isinstance(x, dict)])
            if not data.get("has_more") or not data.get("next_cursor"):
                break
            cursor = data.get("next_cursor")
        return NotionApiResult(True, status=status or 200, data={"results": all_pages}, endpoint=f"/v1/databases/{database_id}/query")

    def list_block_children(self, block_id: str) -> NotionApiResult:
        block_id = normalize_notion_id(block_id)
        items: list[dict] = []
        cursor = None
        status = 0
        while True:
            query = "page_size=100"
            if cursor:
                query += "&start_cursor=" + urllib.parse.quote(str(cursor))
            res = self._request("GET", f"/v1/blocks/{urllib.parse.quote(block_id)}/children?{query}", version=self.database_api_version)
            status = res.status
            if not res.ok:
                return res
            data = res.data if isinstance(res.data, dict) else {}
            items.extend([x for x in (data.get("results") or []) if isinstance(x, dict)])
            if not data.get("has_more") or not data.get("next_cursor"):
                break
            cursor = data.get("next_cursor")
        return NotionApiResult(True, status=status or 200, data={"results": items}, endpoint=f"/v1/blocks/{block_id}/children")

    def retrieve_blocks_markdown(self, block_id: str, *, max_depth: int = 8) -> NotionApiResult:
        block_id = normalize_notion_id(block_id)
        res = self._collect_block_markdown(block_id, depth=0, max_depth=max_depth)
        return res

    def _collect_block_markdown(self, block_id: str, *, depth: int, max_depth: int) -> NotionApiResult:
        if depth > max_depth:
            return NotionApiResult(True, status=200, data="")
        blocks = self.list_block_children(block_id)
        if not blocks.ok:
            return blocks

        lines: list[str] = []
        for block in _extract_items(blocks.data):
            if not isinstance(block, dict):
                continue
            rendered = render_block_as_markdown(block).rstrip()
            if rendered:
                lines.append(rendered)
            if block.get("has_children"):
                child = self._collect_block_markdown(str(block.get("id") or ""), depth=depth + 1, max_depth=max_depth)
                if child.ok and str(child.data or "").strip():
                    lines.append(indent_child_markdown(str(child.data), block.get("type")))
        return NotionApiResult(True, status=blocks.status or 200, data="\n\n".join(lines).strip())

    def _request(self, method: str, path: str, *, body: dict | None = None, version: str | None = None) -> NotionApiResult:
        if not self.token:
            return NotionApiResult(False, error="NOTION_TOKEN is empty", endpoint=path)
        url = NOTION_API_BASE + path
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": version or self.api_version,
            "Accept": "application/json",
            "User-Agent": "YellowClubAgent/3 NotionSync",
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw else {}
                return NotionApiResult(True, status=int(resp.status), data=parsed, endpoint=path)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            parsed: Any = None
            message = raw.strip()
            try:
                parsed = json.loads(raw) if raw else {}
                if isinstance(parsed, dict):
                    message = parsed.get("message") or parsed.get("code") or message
            except Exception:
                parsed = raw
            return NotionApiResult(False, status=int(e.code), data=parsed, error=message, endpoint=path)
        except urllib.error.URLError as e:
            return NotionApiResult(False, status=0, error=str(e.reason), endpoint=path)
        except Exception as e:
            return NotionApiResult(False, status=0, error=str(e), endpoint=path)

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return self._empty_manifest()
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty_manifest()
        if not isinstance(data, dict):
            return self._empty_manifest()
        if not isinstance(data.get("pages"), dict):
            data["pages"] = {}
        return data

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(manifest.get("pages"), dict):
            manifest["pages"] = {}
        tmp = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def manifest_summary(self) -> dict[str, Any]:
        manifest = self.load_manifest()
        pages = manifest.get("pages") if isinstance(manifest.get("pages"), dict) else {}
        existing_files = 0
        missing_files = 0
        for entry in pages.values():
            if not isinstance(entry, dict):
                continue
            file_path = str(entry.get("file_path") or "")
            if file_path and (self.sync_dir.parent / file_path).exists():
                existing_files += 1
            else:
                missing_files += 1
        return {
            "manifest_path": str(self.manifest_path),
            "pages": len(pages),
            "existing_files": existing_files,
            "missing_files": missing_files,
            "last_sync_mode": manifest.get("last_sync_mode") or "",
            "last_sync_pages_total": manifest.get("last_sync_pages_total") or 0,
            "last_sync_pages_saved": manifest.get("last_sync_pages_saved") or 0,
            "last_sync_pages_skipped": manifest.get("last_sync_pages_skipped") or 0,
        }

    def _empty_manifest(self) -> dict[str, Any]:
        return {"version": 1, "pages": {}}

    def _manifest_entry_from_item(self, item: NotionPageSyncResult) -> dict[str, Any]:
        return {
            "page_id": item.source_id,
            "title": item.title,
            "file_path": item.filename,
            "notion_url": item.notion_url,
            "last_edited_time": item.last_edited_time,
            "path_titles": item.path_titles,
        }

    def _clean_sync_dir(self) -> None:
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        for path in self.sync_dir.glob("*.md"):
            try:
                path.unlink()
            except Exception:
                pass
        # Remove old nested folders if a previous version created them.
        for path in self.sync_dir.iterdir():
            if path.is_dir():
                try:
                    shutil.rmtree(path)
                except Exception:
                    pass


def normalize_id_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        nid = normalize_notion_id(str(value or ""))
        if nid and nid not in result:
            result.append(nid)
    return result


def normalize_notion_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    match = None
    for match in UUID_RE.finditer(value):
        pass
    if not match:
        return ""
    raw = match.group(1).replace("-", "").lower()
    if len(raw) != 32:
        return ""
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def short_id(value: str, length: int = 8) -> str:
    value = normalize_notion_id(value) or str(value or "")
    return value.replace("-", "")[:length]


def rich_text_plain(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("plain_text")
        if text is None:
            text = ((item.get("text") or {}) if isinstance(item.get("text"), dict) else {}).get("content")
        if text:
            parts.append(str(text))
    return "".join(parts).strip()


def rich_text_markdown(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("plain_text") or ((item.get("text") or {}) if isinstance(item.get("text"), dict) else {}).get("content") or "")
        if not text:
            continue
        href = item.get("href") or ((item.get("text") or {}) if isinstance(item.get("text"), dict) else {}).get("link", {}).get("url")
        ann = item.get("annotations") or {}
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        if href:
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts).strip()


def extract_page_title(page: Any) -> str:
    if not isinstance(page, dict):
        return ""
    properties = page.get("properties") or {}
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = rich_text_plain(prop.get("title"))
                if title:
                    return title
        # Fallback for common property names.
        for name in ("Name", "Название", "Title", "Заголовок"):
            prop = properties.get(name)
            if isinstance(prop, dict):
                title = rich_text_plain(prop.get("title") or prop.get("rich_text"))
                if title:
                    return title
    # child_page blocks have a title field.
    child_page = page.get("child_page") if isinstance(page.get("child_page"), dict) else {}
    if child_page.get("title"):
        return str(child_page.get("title") or "").strip()
    return ""


def _extract_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "items", "data", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def render_block_as_markdown(block: dict) -> str:
    btype = block.get("type")
    payload = block.get(btype) if isinstance(block.get(btype), dict) else {}
    text = rich_text_markdown(payload.get("rich_text"))

    if btype == "paragraph":
        return text
    if btype == "heading_1":
        return f"# {text}" if text else ""
    if btype == "heading_2":
        return f"## {text}" if text else ""
    if btype == "heading_3":
        return f"### {text}" if text else ""
    if btype == "bulleted_list_item":
        return f"- {text}" if text else "-"
    if btype == "numbered_list_item":
        return f"1. {text}" if text else "1."
    if btype == "to_do":
        mark = "x" if payload.get("checked") else " "
        return f"- [{mark}] {text}" if text else f"- [{mark}]"
    if btype == "toggle":
        return f"<details><summary>{text or 'Подробнее'}</summary>"
    if btype == "quote":
        return "> " + text.replace("\n", "\n> ") if text else ""
    if btype == "callout":
        icon = payload.get("icon") or {}
        emoji = icon.get("emoji") if isinstance(icon, dict) else ""
        prefix = f"> {emoji} " if emoji else "> "
        return prefix + text.replace("\n", "\n> ") if text else ""
    if btype == "code":
        language = payload.get("language") or ""
        return f"```{language}\n{text}\n```" if text else ""
    if btype == "divider":
        return "---"
    if btype == "child_page":
        title = payload.get("title") or "Вложенная страница"
        return f"## {title}"
    if btype == "child_database":
        title = payload.get("title") or "Вложенная база данных"
        return f"## {title}"
    if btype == "bookmark":
        url = payload.get("url") or ""
        caption = rich_text_plain(payload.get("caption"))
        return f"[{caption or url}]({url})" if url else caption
    if btype in {"image", "file", "pdf", "video"}:
        url = ""
        if isinstance(payload.get("external"), dict):
            url = payload["external"].get("url") or ""
        elif isinstance(payload.get("file"), dict):
            url = payload["file"].get("url") or ""
        caption = rich_text_plain(payload.get("caption")) or btype
        return f"[{caption}]({url})" if url else caption
    if btype == "table_row":
        cells = payload.get("cells") or []
        rendered = [rich_text_plain(cell) for cell in cells if isinstance(cell, list)]
        return "| " + " | ".join(rendered) + " |" if rendered else ""
    if text:
        return text
    return ""


def indent_child_markdown(text: str, parent_type: str | None = None) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if parent_type == "toggle":
        return text + "\n</details>"
    if parent_type in {"bulleted_list_item", "numbered_list_item", "to_do"}:
        return "\n".join("  " + line if line else "" for line in text.splitlines())
    return text


def slugify(value: str) -> str:
    value = (value or "notion_page").strip().lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:80] or "notion_page"


def unique_stable_filename(title: str, page_id: str, used_names: set[str]) -> str:
    base = slugify(title or "notion_page")
    suffix = short_id(page_id, 12) or "unknown"
    candidate = f"{base}__{suffix}.md"
    idx = 2
    while candidate in used_names:
        candidate = f"{base}__{suffix}_{idx}.md"
        idx += 1
    used_names.add(candidate)
    return candidate


def format_notion_status(client: NotionClient, test_result: NotionApiResult | None = None) -> str:
    lines = [
        "📄 Notion Sync",
        "",
        f"Токен: {'указан' if client.token else 'не указан'}",
        f"Корневых страниц в .env: {len(client.page_ids)}",
        f"Корневых баз в .env: {len(client.database_ids)}",
        f"Рекурсивный обход: {'включён' if client.recursive else 'выключен'}",
        f"Глубина обхода: {client.recursive_max_depth}",
        f"Удалять старые .md перед sync: {'да' if client.clean_before_sync else 'нет'}",
        f"Папка синхронизации: {client.sync_dir}",
        f"Manifest: {client.manifest_path}",
        f"API version: {client.api_version}",
    ]
    if test_result is not None:
        if test_result.ok:
            bot = test_result.data.get("bot", {}) if isinstance(test_result.data, dict) else {}
            owner = bot.get("owner", {}) if isinstance(bot, dict) else {}
            workspace = owner.get("workspace", True) if isinstance(owner, dict) else True
            lines.extend(["", f"Проверка API: работает, status={test_result.status}"])
            if workspace:
                lines.append("Доступ connection получен. Если страницы не синхронизируются, добавьте connection к корневой странице или базе в Notion.")
        else:
            lines.extend(["", f"Проверка API: ошибка, status={test_result.status}", test_result.error or "unknown error"])
    if not client.token:
        lines.extend(["", "Добавьте в .env:", "NOTION_TOKEN=secret_... или ntn_..."])
    if not client.page_ids and not client.database_ids:
        lines.extend(["", "Добавьте хотя бы одну корневую страницу или базу:", "NOTION_PAGE_IDS=...", "NOTION_DATABASE_IDS=..."])
    return "\n".join(lines)


def format_notion_sync_result(result: NotionSyncResult, *, kb_chunks: int | None = None, kb_files: int | None = None) -> str:
    title = "📄 Синхронизация Notion завершена"
    if result.mode == "fast":
        title = "⚡ Быстрая синхронизация Notion завершена"
    elif result.mode == "page":
        title = "🔗 Синхронизация страницы Notion завершена"
    lines = [
        title,
        "",
        f"Режим: {result.mode}",
        f"Корневых страниц: {result.root_pages}",
        f"Корневых баз: {result.root_databases}",
        f"Найдено вложенных страниц: {result.discovered_pages}",
        f"Найдено вложенных баз: {result.discovered_databases}",
        f"Страниц всего: {result.pages_total}",
        f"Файлов сохранено/обновлено: {result.pages_saved}",
    ]
    if result.mode == "fast":
        lines.append(f"Без изменений, пропущено: {result.pages_skipped}")
    lines.extend([
        f"Рекурсивный обход: {'да' if result.recursive else 'нет'}",
        f"Очистка перед sync: {'да' if result.clean_used else 'нет'}",
        f"Папка: {result.sync_dir}",
        f"Manifest: {result.manifest_path or 'не указан'}",
    ])
    if kb_chunks is not None:
        lines.append(f"Фрагментов базы после перезагрузки: {kb_chunks}")
    if kb_files is not None:
        lines.append(f"Файлов базы после перезагрузки: {kb_files}")
    if result.files:
        lines.append("")
        lines.append("Сохранённые/обновлённые файлы:")
        for name in result.files[:30]:
            lines.append(f"- {name}")
        if len(result.files) > 30:
            lines.append(f"- ещё {len(result.files) - 30}")
    if result.mode == "fast" and not result.files and result.pages_skipped and not result.errors:
        lines.extend(["", "Изменённых страниц не найдено. Локальная база уже актуальна."])
    if result.errors:
        lines.append("")
        lines.append("Ошибки / предупреждения:")
        for err in result.errors[:10]:
            lines.append(f"- {err}")
        if len(result.errors) > 10:
            lines.append(f"- ещё {len(result.errors) - 10}")
    if not result.files and not result.errors and not result.pages_skipped:
        lines.extend(["", "Ничего не сохранено. Проверьте NOTION_PAGE_IDS / NOTION_DATABASE_IDS и доступ connection к страницам."])
    return "\n".join(lines)


def format_notion_manifest(client: NotionClient) -> str:
    summary = client.manifest_summary()
    lines = [
        "🧾 Notion manifest",
        "",
        f"Файл: {summary['manifest_path']}",
        f"Страниц в индексе: {summary['pages']}",
        f"Файлов найдено: {summary['existing_files']}",
        f"Файлов отсутствует: {summary['missing_files']}",
        f"Последний режим sync: {summary['last_sync_mode'] or 'нет данных'}",
        f"В прошлый sync страниц всего: {summary['last_sync_pages_total']}",
        f"В прошлый sync обновлено: {summary['last_sync_pages_saved']}",
        f"В прошлый sync пропущено: {summary['last_sync_pages_skipped']}",
        "",
        "Обычная схема:",
        "/notion_sync_fast - быстро обновить изменённые страницы",
        "/notion_sync_page ссылка - обновить одну страницу",
        "/notion_sync_clean - полная пересборка",
    ]
    return "\n".join(lines)

def format_notion_files(sync_dir: Path) -> str:
    if not sync_dir.exists():
        return f"Папка Notion не создана: {sync_dir}"
    files = sorted(sync_dir.glob("*.md"))
    if not files:
        return f"В папке {sync_dir} пока нет .md файлов. Запустите /notion_sync."
    lines = [f"Файлы Notion в {sync_dir}:", ""]
    for path in files[:100]:
        try:
            size = path.stat().st_size
        except Exception:
            size = 0
        lines.append(f"- {path.name} ({size} байт)")
    if len(files) > 100:
        lines.append(f"- ещё {len(files) - 100}")
    return "\n".join(lines)
