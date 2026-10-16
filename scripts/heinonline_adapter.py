#!/usr/bin/env python3
"""
HeinOnline retrieval core for the standalone HeinOnlineSearcher skill.

This module owns HeinOnline search, advanced search and section-PDF download
only.  It does not own credentials or login logic: institutional access is
delegated to the independent ``gdufs-auth-session`` skill, whose headless
browser helpers render the GDUFS LibVPN pages and write downloads to disk.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


SEARCH_RESULT_TEXT = r"results searching for|Databases found|result"
ADVANCED_RESULT_TEXT = r"results searching for|Databases found"
DOWNLOAD_HELPER = "headless_download.mjs"
FETCH_HELPER = "headless_fetch.mjs"
SECTION_DOWNLOAD_LABEL = "Download PDF of This Section"
SEARCH_URL = "https://access.heinonline.com/HOL/LuceneSearch"
ADVANCED_SEARCH_URL = "https://access.heinonline.com/HOL/LuceneSearch"
ADVANCED_FIELD_TYPES = ("title", "voltitle", "creator", "text")
ADVANCED_JOINS = ("AND", "OR", "NOT")
ADVANCED_MAX_FIELDS = 6
RESULT_PAGE_SIZES = (10, 20, 25, 50, 75, 100)
LIBVPN_ENTRY_PREFIX = "https://libvpn.gdufs.edu.cn/-https://"


def _run(command: list[str], timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _find_auth_skill() -> Path:
    configured = os.environ.get("GDUFS_AUTH_SKILL")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if (candidate / "run.ps1").is_file():
            return candidate
        raise RuntimeError(f"GDUFS_AUTH_SKILL is not a skill directory: {candidate}")

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "gdufs-auth-session"
        if (candidate / "run.ps1").is_file():
            return candidate
    raise RuntimeError(
        "Cannot locate gdufs-auth-session. Install it as a sibling skill or set "
        "GDUFS_AUTH_SKILL."
    )


def _resolve_auth_skill(auth_skill: str | Path | None = None) -> Path:
    """Return the ``gdufs-auth-session`` directory to use.

    An explicit ``auth_skill`` argument wins over the ``GDUFS_AUTH_SKILL``
    environment variable and the sibling-skill search, so callers can pin one
    installation on a machine that has several.
    """
    if auth_skill is None:
        return _find_auth_skill()
    candidate = Path(auth_skill).expanduser().resolve()
    if (candidate / "run.ps1").is_file():
        return candidate
    raise RuntimeError(f"Not a gdufs-auth-session skill directory: {candidate}")


def _find_powershell() -> str:
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("PowerShell is required to invoke gdufs-auth-session/run.ps1")


def _run_auth(
    auth_skill: Path,
    *args: str,
    timeout: int = 240,
) -> dict[str, Any]:
    ps = _find_powershell()
    command = [
        ps,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(auth_skill / "run.ps1"),
        *args,
    ]
    result = _run(command, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stdout or result.stderr or "").strip()
        raise RuntimeError(
            f"gdufs-auth-session {args[0] if args else ''} failed: {detail[:800]}"
        )
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(
            "gdufs-auth-session returned non-JSON output: "
            + (result.stdout or result.stderr or "")[:800]
        ) from exc


def browser_preference(
    session_key: str | None = None,
    auth_skill: str | Path | None = None,
) -> dict[str, Any]:
    skill = _resolve_auth_skill(auth_skill)
    args = ["browser-preference", "get"]
    if session_key:
        args += ["--session-key", session_key]
    return _run_auth(skill, *args)


def set_browser_preference(
    enabled: bool,
    session_key: str | None = None,
    auth_skill: str | Path | None = None,
) -> dict[str, Any]:
    skill = _resolve_auth_skill(auth_skill)
    args = [
        "browser-preference",
        "set",
        "--enabled",
        "true" if enabled else "false",
    ]
    if session_key:
        args += ["--session-key", session_key]
    return _run_auth(skill, *args)


def _summary_index(
    soup: BeautifulSoup, containers: list[Any]
) -> dict[int, Any]:
    """Map each result index to the HeinOnline summary that belongs to it.

    HeinOnline pools every result row into one shared ``div.row.g-0`` and
    emits the generated summaries as sibling blocks in document order, between
    the row they describe and the next one.  Results without a summary leave
    no placeholder, so the summary blocks cannot be zipped with the result
    list; each summary is paired with the nearest preceding result instead.
    """
    index_of = {id(node): index for index, node in enumerate(containers)}
    mapping: dict[int, Any] = {}
    last_result: int | None = None
    for element in soup.find_all(True):
        position = index_of.get(id(element))
        if position is not None:
            last_result = position
            continue
        if "full_summary_text" in (element.get("class") or []):
            if last_result is not None:
                mapping[last_result] = element
    return mapping


def _result_page_size(limit: int) -> int:
    """Return the smallest HeinOnline page size that can satisfy ``limit``."""
    for size in RESULT_PAGE_SIZES:
        if size >= limit:
            return size
    return RESULT_PAGE_SIZES[-1]


def _parse_results(page_html: str, limit: int, offset: int = 0) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "html.parser")
    body_text = soup.get_text(" ", strip=True)
    total = None
    match = re.search(r"([\d,]+)\s+results searching for", body_text, re.I)
    if not match:
        match = re.search(
            r"Results\s+\d+-\d+\s+of\s+([\d,]+)\s+items searching for",
            body_text,
            re.I,
        )
    if not match:
        match = re.search(r"([\d,]+)\s+Databases found", body_text, re.I)
    if match:
        total = int(match.group(1).replace(",", ""))

    containers = soup.select(
        "div.lucene_search_result_number_b"
    )
    summary_by_result = _summary_index(soup, containers)
    results: list[dict[str, Any]] = []
    for index, node in enumerate(containers):
        container = node.parent
        if container is None:
            continue
        heading = container.select_one("h2.search-result-heading a")
        if not heading:
            continue
        title = " ".join(heading.get_text(" ", strip=True).split())
        href = heading.get("href") or ""
        lines = []
        for line in container.select("div.search_result_line"):
            text = " ".join(line.get_text(" ", strip=True).split())
            if text and text not in lines and text != title:
                lines.append(text)
        summary_node = summary_by_result.get(index)
        summary = (
            " ".join(summary_node.get_text(" ", strip=True).split())
            if summary_node
            else ""
        )
        subjects = []
        for anchor in container.select("div.topics a"):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            if text and text not in subjects:
                subjects.append(text)
        authors = next(
            (
                line
                for line in lines
                if re.search(
                    r"(?:Cited \d+|^[A-Z][A-Za-z'.-]+,\s+[A-Z][A-Za-z'.-]+)",
                    line,
                )
            ),
            "",
        )
        journal = next(
            (
                line
                for line in lines
                if re.search(r"\b(?:Vol\.|Issue|pp\.|\(\d{4}\))", line)
            ),
            "",
        )
        citation = next(
            (
                line
                for line in lines
                if re.search(r"\b\d{4}\b", line) and line != journal
            ),
            "",
        )
        results.append(
            {
                "title": title,
                "url": href,
                "authors": authors,
                "journal": journal,
                "citation": citation,
                "summary": summary,
                "subjects": subjects,
                "via": "heinonline",
                "result_type": "search_result",
                "access": (
                    "HeinOnline institutional search metadata and summary; "
                    "full text not fetched by this adapter"
                ),
            }
        )

    selected = results[:limit]
    warnings = []
    if not results and total is not None and offset >= total:
        warnings.append(
            f"HeinOnline returned no results at offset {offset}; total={total}."
        )
    if limit > len(results):
        warnings.append(
            "HeinOnline result page returned "
            f"{len(results)} records at offset {offset}; requested limit={limit}."
        )
    return {
        "total": total,
        "results": selected,
        "warnings": warnings,
        "page_result_count": len(results),
    }


def _pagination_report(
    parsed: dict[str, Any],
    *,
    offset: int,
    limit: int,
    page_size: int,
) -> dict[str, Any]:
    returned = len(parsed["results"])
    total = parsed["total"]
    next_offset = offset + returned if returned else None
    if next_offset is not None and total is not None and next_offset >= total:
        next_offset = None
    return {
        "offset": offset,
        "limit": limit,
        "page_size": page_size,
        "returned": returned,
        "page_result_count": parsed["page_result_count"],
        "total": total,
        "next_offset": next_offset,
        "firstdoc": offset,
        "maxresults": page_size,
        "infinite_scroll": False,
        "note": (
            "HeinOnline non-scrolling result page requested with "
            f"firstdoc={offset} and maxresults={page_size}; next_offset is a "
            "result offset, not a page number."
        ),
    }


def build_search_url(
    query: str,
    *,
    collection: str = "all",
    firstdoc: int = 0,
    maxresults: int = 10,
) -> str:
    """Build the single-box result URL used by the search command.

    HeinOnline's welcome-page search box submits these parameters.  The
    adapter explicitly turns off infinite scroll so ``firstdoc`` can select a
    stable result window and ``maxresults`` can return up to 100 records.
    """
    pairs = [
        ("terms", query),
        ("collection", collection or "all"),
        ("searchtype", "advanced"),
        ("typea", "text"),
        ("tabfrom", ""),
        ("submit", "Go"),
        ("sendit", ""),
        ("all", "true"),
        ("maxresults", str(maxresults)),
        ("firstdoc", str(max(0, firstdoc))),
        ("infinite_scroll", "off"),
    ]
    return SEARCH_URL + "?" + urlencode(pairs)


def search(
    query: str,
    limit: int = 5,
    offset: int = 0,
    *,
    browser_enabled: bool = False,
    auth_skill: str | Path | None = None,
    session_path: str | None = None,
    config_path: str | None = None,
    chrome_path: str | None = None,
    timeout: int = 240,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "source": "heinonline",
        "source_name": "HeinOnline (GDUFS institutional access)",
        "status": "unavailable",
        "scope": (
            "HeinOnline All Databases search through the GDUFS library session; "
            "paginated results with title, journal, author, citation, subjects "
            "and summary."
        ),
        "results": [],
        "warnings": [],
    }
    if not browser_enabled:
        report["status"] = "disabled"
        report["warnings"].append(
            "Browser-based sources are not enabled for this session."
        )
        return report
    if not query.strip():
        report["warnings"].append("Empty HeinOnline query.")
        return report

    try:
        auth_skill = _resolve_auth_skill(auth_skill)
        ensure_args = ["ensure"]
        if config_path:
            ensure_args += ["--config", config_path]
        if session_path:
            ensure_args += ["--session", session_path]
        ensure = _run_auth(auth_skill, *ensure_args, timeout=timeout)
        if ensure.get("status") not in {"reused", "saved"}:
            raise RuntimeError(
                "Session is not valid: " + json.dumps(ensure, ensure_ascii=False)
            )

        page_size = _result_page_size(limit)
        target = build_search_url(
            query,
            firstdoc=offset,
            maxresults=page_size,
        )
        report["requested_url"] = target

        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required for the headless browser helper.")
        with tempfile.TemporaryDirectory(prefix="heinonline-") as temp:
            temp_path = Path(temp)
            cookies_path = temp_path / "cookies.json"
            cookies_args = ["cookies"]
            if session_path:
                cookies_args += ["--session", session_path]
            cookies_args += ["--out", str(cookies_path)]
            _run_auth(auth_skill, *cookies_args, timeout=timeout)
            rendered_path = temp_path / "heinonline.html"
            command = [
                node,
                str(auth_skill / "scripts" / "headless_fetch.mjs"),
                "--cookies",
                str(cookies_path),
                "--url",
                _libvpn_entry_url(target),
                "--out",
                str(rendered_path),
                "--wait-ms",
                "15000",
                "--result-text",
                SEARCH_RESULT_TEXT,
                "--result-selector",
                "div.lucene_search_result_number_b",
                "--result-min-count",
                str(limit),
            ]
            if chrome_path:
                command += ["--chrome", chrome_path]
            result = _run(command, timeout=timeout)
            if result.returncode != 0:
                raise RuntimeError(
                    (result.stderr or result.stdout or "headless fetch failed")[
                        :1000
                    ]
                )
            if not rendered_path.is_file():
                raise RuntimeError("Headless helper did not produce a rendered page.")
            page_html = rendered_path.read_text(encoding="utf-8", errors="replace")
            parsed = _parse_results(page_html, limit, offset)
        report.update(
            {
                "status": "ok" if parsed["results"] else "no-results-in-queried-index",
                "total": parsed["total"],
                "results": parsed["results"],
                "warnings": parsed["warnings"],
                "query_echo": query_echo(page_html),
                "pagination": _pagination_report(
                    parsed,
                    offset=offset,
                    limit=limit,
                    page_size=page_size,
                ),
            }
        )
    except Exception as exc:
        report["warnings"].append(str(exc))
    return report


def _advanced_rows(fields: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(fields, list) or not fields:
        raise ValueError("Advanced search needs at least one field row.")
    if len(fields) > ADVANCED_MAX_FIELDS:
        raise ValueError(
            f"Advanced search supports at most {ADVANCED_MAX_FIELDS} field rows."
        )
    rows: list[dict[str, str]] = []
    for index, row in enumerate(fields):
        field = str(row.get("field", "")).strip().lower()
        terms = str(row.get("terms", "")).strip()
        join = str(row.get("join", "AND")).strip().upper()
        if field not in ADVANCED_FIELD_TYPES:
            raise ValueError(
                f"Unknown advanced field {row.get('field')!r}; use one of "
                + ", ".join(ADVANCED_FIELD_TYPES)
                + "."
            )
        if not terms:
            raise ValueError(f"Advanced field row {index + 1} has no search terms.")
        if join not in ADVANCED_JOINS:
            raise ValueError(
                f"Unsupported between-field operator {row.get('join')!r}; use "
                "AND, OR or NOT. For proximity, put both terms in one field "
                "and use w/N or \"phrase\"~N."
            )
        rows.append({"field": field, "terms": terms, "join": join})
    return rows


def build_advanced_url(
    fields: list[dict[str, Any]],
    *,
    year_lo: str = "",
    year_hi: str = "",
    collection: str = "all",
    firstdoc: int = 0,
    maxresults: int = 10,
    infinite_scroll: bool = False,
) -> str:
    """Build the HeinOnline multi-field advanced-search URL.

    ``fields`` holds up to six rows of ``{"field", "terms", "join"}``.  Field
    names are ``title`` (Section Title), ``voltitle`` (Book Title), ``creator``
    (Author) and ``text`` (Full Text).  ``join`` is the operator between this
    row and the next one (AND/OR/NOT, default AND).  Verified 2026-10-16
    against the live advanced-search form (``searchtype=field``).
    """
    rows = _advanced_rows(fields)
    # Form naming: rows are termsa/typea .. termsf/typef, and the operator
    # between rows a/b is plain "operator"; later pairs add the earlier row's
    # letter (operatorb, operatorc, ...).
    row_letters = ["a", "b", "c", "d", "e", "f"]
    pairs: list[tuple[str, str]] = []
    for index, row in enumerate(rows):
        letter = row_letters[index]
        pairs.append(("terms" + letter, row["terms"]))
        pairs.append(("type" + letter, row["field"]))
        if index < len(rows) - 1:
            operator_name = "operator" + ("" if index == 0 else letter)
            pairs.append((operator_name, row["join"]))
    pairs += [
        ("yearlo", str(year_lo or "")),
        ("yearhi", str(year_hi or "")),
        ("searchtype", "field"),
        ("collection", collection or "all"),
        ("maxresults", str(maxresults)),
        ("firstdoc", str(max(0, firstdoc))),
        ("submit", "Go"),
    ]
    if not infinite_scroll:
        pairs.append(("infinite_scroll", "off"))
    return ADVANCED_SEARCH_URL + "?" + urlencode(pairs)


def _libvpn_entry_url(target: str) -> str:
    parts = urlsplit(target)
    query = f"?{parts.query}" if parts.query else ""
    return LIBVPN_ENTRY_PREFIX + parts.netloc + parts.path + query


def query_echo(page_html: str) -> str:
    """Return the query text as HeinOnline interpreted it, if present."""
    text = BeautifulSoup(page_html, "html.parser").get_text(" ", strip=True)
    match = re.search(r"results searching for\s+(.+?)\s+in\s+", text)
    return " ".join(match.group(1).split())[:400] if match else ""


def search_advanced(
    fields: list[dict[str, Any]],
    limit: int = 5,
    offset: int = 0,
    *,
    browser_enabled: bool = True,
    auth_skill: str | Path | None = None,
    year_lo: str = "",
    year_hi: str = "",
    collection: str = "all",
    session_path: str | None = None,
    config_path: str | None = None,
    chrome_path: str | None = None,
    timeout: int = 240,
) -> dict[str, Any]:
    """Run a HeinOnline multi-field advanced search.

    Combines Section Title / Book Title / Author / Full Text rows with
    AND/OR/NOT operators and an optional year range.  Uses the same browser
    session and parsing pipeline as :func:`search`.
    """
    report: dict[str, Any] = {
        "source": "heinonline",
        "source_name": "HeinOnline (GDUFS institutional access)",
        "mode": "advanced",
        "status": "unavailable",
        "scope": (
            "HeinOnline multi-field advanced search (Section Title, Book "
            "Title, Author, Full Text with AND/OR/NOT between rows and an "
            "optional year range) through the GDUFS library session; "
            "paginated results with title, journal, author, citation, "
            "subjects and summary."
        ),
        "results": [],
        "warnings": [],
    }
    if not browser_enabled:
        report["status"] = "disabled"
        report["warnings"].append(
            "Browser-based sources are not enabled for this session."
        )
        return report
    try:
        page_size = _result_page_size(limit)
        target = build_advanced_url(
            fields,
            year_lo=year_lo,
            year_hi=year_hi,
            collection=collection,
            firstdoc=offset,
            maxresults=page_size,
            infinite_scroll=False,
        )
    except ValueError as exc:
        report["warnings"].append(str(exc))
        return report
    report["requested_url"] = target

    try:
        auth_skill = _resolve_auth_skill(auth_skill)
        ensure_args = ["ensure"]
        if config_path:
            ensure_args += ["--config", config_path]
        if session_path:
            ensure_args += ["--session", session_path]
        ensure = _run_auth(auth_skill, *ensure_args, timeout=timeout)
        if ensure.get("status") not in {"reused", "saved"}:
            raise RuntimeError(
                "Session is not valid: " + json.dumps(ensure, ensure_ascii=False)
            )

        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required for the headless browser helper.")
        with tempfile.TemporaryDirectory(prefix="heinonline-advanced-") as temp:
            temp_path = Path(temp)
            cookies_path = temp_path / "cookies.json"
            cookies_args = ["cookies"]
            if session_path:
                cookies_args += ["--session", session_path]
            cookies_args += ["--out", str(cookies_path)]
            _run_auth(auth_skill, *cookies_args, timeout=timeout)
            rendered_path = temp_path / "heinonline-advanced.html"
            command = [
                node,
                str(auth_skill / "scripts" / "headless_fetch.mjs"),
                "--cookies",
                str(cookies_path),
                "--url",
                _libvpn_entry_url(target),
                "--out",
                str(rendered_path),
                "--wait-ms",
                "15000",
                "--result-text",
                ADVANCED_RESULT_TEXT,
                "--result-selector",
                "div.lucene_search_result_number_b",
                "--result-min-count",
                str(limit),
            ]
            if chrome_path:
                command += ["--chrome", chrome_path]
            result = _run(command, timeout=timeout)
            if result.returncode != 0:
                raise RuntimeError(
                    (result.stderr or result.stdout or "headless fetch failed")[
                        :1000
                    ]
                )
            if not rendered_path.is_file():
                raise RuntimeError("Headless helper did not produce a rendered page.")
            page_html = rendered_path.read_text(encoding="utf-8", errors="replace")
            parsed = _parse_results(page_html, limit, offset)

        if parsed["results"]:
            status = "ok"
        elif (
            parsed["total"] is not None
            or "results searching for" in page_html
            or "items searching for" in page_html
        ):
            status = "no-results-in-queried-index"
        else:
            status = "unexpected-page"
            report["warnings"].append(
                "Rendered page did not contain a HeinOnline result list; the "
                "query may have been rejected or timed out. Check query_echo "
                "and requested_url."
            )
        report.update(
            {
                "status": status,
                "total": parsed["total"],
                "results": parsed["results"],
                "warnings": report["warnings"] + parsed["warnings"],
                "query_echo": query_echo(page_html),
                "pagination": _pagination_report(
                    parsed,
                    offset=offset,
                    limit=limit,
                    page_size=page_size,
                ),
            }
        )
    except Exception as exc:
        report["warnings"].append(str(exc))
    return report


def section_pdf_url(page_html: str, page_url: str) -> str:
    """Return the 'Download PDF of This Section' target of a rendered page."""
    soup = BeautifulSoup(page_html, "html.parser")
    fallback = ""
    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        if "PrintRequest" not in href:
            continue
        label = " ".join(anchor.get_text(" ", strip=True).split())
        title = anchor.get("title") or ""
        if SECTION_DOWNLOAD_LABEL in label or SECTION_DOWNLOAD_LABEL in title:
            return urljoin(page_url, href)
        if not fallback and "print=section" in href:
            fallback = urljoin(page_url, href)
    return fallback


def normalize_libvpn_url(url: str) -> str:
    """Rewrite a LibVPN ``__proto__``/``__host__`` URL to its entry form.

    LibVPN only serves proxied content when the request is first entered
    through ``/-https://<host><path>``; the ``__proto__``/``__host__`` form
    that HeinOnline result rows carry assumes the site's Service Worker is
    already installed.  Plain fetches of that form get the Service Worker
    bootstrap page, so normalise before rendering.
    """
    parts = urlsplit(url)
    if "libvpn.gdufs.edu.cn" not in parts.netloc:
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    host = next((value for key, value in query if key == "__host__"), "")
    if not host:
        return url
    remaining = [(key, value) for key, value in query if not key.startswith("__")]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            "/-https://" + host + parts.path,
            urlencode(remaining),
            "",
        )
    )


def download(
    url: str,
    out: str,
    *,
    auth_skill: str | Path | None = None,
    session_path: str | None = None,
    config_path: str | None = None,
    chrome_path: str | None = None,
    timeout: int = 300,
    wait_ms: int = 20000,
) -> dict[str, Any]:
    """Download a HeinOnline section PDF through the GDUFS browser session.

    ``url`` may be either a document page (the adapter locates its
    'Download PDF of This Section' link) or an already-resolved
    ``HOL/PrintRequest`` download URL.
    """
    report: dict[str, Any] = {
        "source": "heinonline",
        "source_name": "HeinOnline (GDUFS institutional access)",
        "status": "unavailable",
        "requested_url": url,
        "page_url": "",
        "pdf_url": "",
        "out_path": "",
        "bytes": 0,
        "sha256": "",
        "warnings": [],
        "access": (
            "Institutional session download through the GDUFS library VPN; "
            "PDF only, no text extraction by this adapter."
        ),
    }
    if not url.strip():
        report["warnings"].append("Empty download URL.")
        return report

    try:
        auth_skill = _resolve_auth_skill(auth_skill)
        ensure_args = ["ensure"]
        if config_path:
            ensure_args += ["--config", config_path]
        if session_path:
            ensure_args += ["--session", session_path]
        ensure = _run_auth(auth_skill, *ensure_args, timeout=timeout)
        if ensure.get("status") not in {"reused", "saved"}:
            raise RuntimeError(
                "Session is not valid: " + json.dumps(ensure, ensure_ascii=False)
            )

        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required for the headless helper.")
        with tempfile.TemporaryDirectory(prefix="heinonline-download-") as temp:
            temp_path = Path(temp)
            cookies_path = temp_path / "cookies.json"
            cookies_args = ["cookies"]
            if session_path:
                cookies_args += ["--session", session_path]
            cookies_args += ["--out", str(cookies_path)]
            _run_auth(auth_skill, *cookies_args, timeout=timeout)

            pdf_url = url
            page_url = ""
            if "PrintRequest" not in url:
                page_url = normalize_libvpn_url(url)
                rendered_path = temp_path / "page.html"
                render = _run(
                    [
                        node,
                        str(auth_skill / "scripts" / FETCH_HELPER),
                        "--cookies",
                        str(cookies_path),
                        "--url",
                        page_url,
                        "--out",
                        str(rendered_path),
                        "--wait-ms",
                        str(wait_ms),
                    ]
                    + (["--chrome", chrome_path] if chrome_path else []),
                    timeout=timeout,
                )
                if render.returncode != 0 or not rendered_path.is_file():
                    raise RuntimeError(
                        (
                            render.stderr
                            or render.stdout
                            or "page render failed"
                        )[:800]
                    )
                pdf_url = section_pdf_url(
                    rendered_path.read_text(encoding="utf-8", errors="replace"),
                    url,
                )
                if not pdf_url:
                    raise RuntimeError(
                        "No 'Download PDF of This Section' link on the page; "
                        "the document may be metadata-only for this account."
                    )
            report["page_url"] = page_url
            report["pdf_url"] = pdf_url

            download_args = [
                node,
                str(auth_skill / "scripts" / DOWNLOAD_HELPER),
                "--cookies",
                str(cookies_path),
                "--url",
                pdf_url,
                "--out",
                out,
                "--wait-ms",
                str(wait_ms),
                "--timeout-ms",
                str(timeout * 1000),
            ]
            if page_url:
                download_args += ["--warmup", page_url]
            if chrome_path:
                download_args += ["--chrome", chrome_path]
            result = _run(download_args, timeout=timeout + 60)
            try:
                parsed = json.loads(result.stdout or "{}")
            except ValueError:
                parsed = {}
            if not parsed:
                raise RuntimeError(
                    (result.stderr or result.stdout or "download helper failed")[
                        :800
                    ]
                )
            report["status"] = parsed.get("status", "failed")
            report["out_path"] = parsed.get("out_path", "")
            report["bytes"] = parsed.get("bytes", 0)
            report["sha256"] = parsed.get("sha256", "")
            report["method"] = parsed.get("method", "")
            report["service_worker_controlled"] = parsed.get(
                "service_worker_controlled", False
            )
            report["warmup_url"] = parsed.get("warmup_url", "")
            report["warnings"] += parsed.get("warnings", [])
            if report["status"] != "ok":
                report["status"] = "unavailable"
    except Exception as exc:
        report["warnings"].append(str(exc))
    return report
