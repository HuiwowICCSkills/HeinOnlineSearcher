#!/usr/bin/env python3
"""Command-line entry point for the standalone HeinOnlineSearcher skill.

Commands:
  search    single-box HeinOnline search with the official Search Help syntax
  advanced  multi-field advanced search (Section Title / Book Title / Author /
            Full Text with AND/OR/NOT and an optional year range)
  download  section-PDF download through the GDUFS library session
  doctor    check deployment prerequisites before the first live run

Every command prints a JSON report on stdout.  Institutional access is
delegated to the separate ``gdufs-auth-session`` skill: install it as a
sibling skill, or point ``--auth-skill`` / ``GDUFS_AUTH_SKILL`` at it.

Boundaries: search results are HeinOnline metadata and summaries, not full
text; search and advanced support result offsets through HeinOnline's
non-scrolling result pages; ``download`` writes the PDF and reports
bytes/SHA-256 without extracting text.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

_ADAPTER: Any = None


def load_adapter() -> Any:
    """Import the adapter lazily so ``doctor`` can report missing packages."""
    global _ADAPTER
    if _ADAPTER is None:
        import heinonline_adapter  # noqa: PLC0415 - deliberate lazy import

        _ADAPTER = heinonline_adapter
    return _ADAPTER


def _add_auth_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--auth-skill",
        help="Path to the gdufs-auth-session skill directory (defaults to "
        "GDUFS_AUTH_SKILL or a sibling skill next to this one).",
    )
    parser.add_argument("--auth-session", help="Path to the cached session.json")
    parser.add_argument("--auth-config", help="Path to the GDUFS credentials config.json")
    parser.add_argument(
        "--chrome-path",
        help="Chrome/Chromium/Edge executable; defaults to CHROME_PATH or "
        "the helper's own discovery.",
    )


def _add_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", help="Write the JSON report to this file")


def _emit(payload: dict[str, Any], out: str | None, summary: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        summary = {"output": str(path.resolve()), **summary}
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(text)


def _find_chrome(explicit: str | None) -> str:
    if explicit:
        return explicit if Path(explicit).is_file() else ""
    for name in ("CHROME_PATH", "CHROME_BIN"):
        value = os.environ.get(name)
        if value and Path(value).is_file():
            return value
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    for name in (
        "chrome",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "microsoft-edge",
    ):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _find_powershell() -> str:
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _run_auth_status(
    auth_skill: Path,
    session_path: str | None,
    config_path: str | None,
    timeout: int = 120,
) -> dict[str, Any]:
    ps = _find_powershell()
    if not ps:
        return {"status": "no-powershell"}
    command = [
        ps,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(auth_skill / "run.ps1"),
        "status",
    ]
    if config_path:
        command += ["--config", config_path]
    if session_path:
        command += ["--session", session_path]
    result = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = {
            "status": "unparsable",
            "detail": (result.stdout or result.stderr or "")[:500],
        }
    payload["exit_code"] = result.returncode
    return payload


def doctor(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    next_steps: list[str] = []

    python_detail = platform.python_version()
    try:
        import bs4  # noqa: PLC0415 - probe only

        checks.append(
            {
                "name": "python",
                "ok": True,
                "detail": f"Python {python_detail}, beautifulsoup4 {bs4.__version__}",
            }
        )
    except ImportError as exc:
        checks.append(
            {
                "name": "python",
                "ok": False,
                "detail": f"Python {python_detail}; beautifulsoup4 missing ({exc})",
            }
        )
        next_steps.append(
            "Install the Python dependencies: "
            "python -m pip install -r requirements.txt"
        )

    ps = _find_powershell()
    checks.append(
        {
            "name": "powershell",
            "ok": bool(ps),
            "detail": ps or "pwsh/powershell not found on PATH",
        }
    )
    if not ps:
        next_steps.append(
            "Install PowerShell (pwsh) - gdufs-auth-session delegates login "
            "and session handling to its run.ps1 entry point."
        )

    node = shutil.which("node")
    node_ok = False
    node_detail = "node not found on PATH"
    if node:
        try:
            version = subprocess.run(
                [node, "--version"],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            ).stdout.strip()
            major = 0
            if version.startswith("v"):
                major = int(version[1:].split(".")[0] or 0)
            node_ok = major >= 22
            node_detail = f"{node} ({version or 'unknown version'})"
            if not node_ok:
                node_detail += "; the headless helpers need the global WebSocket API (Node.js 22+)"
        except Exception as exc:  # pragma: no cover - defensive
            node_detail = f"{node}; version check failed: {exc}"
    checks.append({"name": "node", "ok": node_ok, "detail": node_detail})
    if not node_ok:
        next_steps.append(
            "Install Node.js 22 or newer; the gdufs-auth-session headless "
            "helpers use the global WebSocket API."
        )

    chrome = _find_chrome(args.chrome_path)
    checks.append(
        {
            "name": "chrome",
            "ok": bool(chrome),
            "detail": chrome
            or "no Chrome/Chromium/Edge found; set CHROME_PATH or pass --chrome-path",
        }
    )
    if not chrome:
        next_steps.append(
            "Install Chrome/Chromium/Edge and set CHROME_PATH (or pass "
            "--chrome-path) so LibVPN pages can be rendered."
        )

    auth_skill: Path | None = None
    try:
        auth_skill = load_adapter()._resolve_auth_skill(args.auth_skill)
        checks.append(
            {"name": "auth_skill", "ok": True, "detail": str(auth_skill)}
        )
    except Exception as exc:
        checks.append({"name": "auth_skill", "ok": False, "detail": str(exc)})
        next_steps.append(
            "Install gdufs-auth-session (private repo, request access from "
            "the GDUFSICCSkills organisation) as a sibling skill or set "
            "GDUFS_AUTH_SKILL."
        )

    if auth_skill and ps:
        status = _run_auth_status(auth_skill, args.auth_session, args.auth_config)
        session_ok = bool(status.get("valid")) or status.get("status") == "valid"
        checks.append(
            {
                "name": "session",
                "ok": session_ok,
                "detail": status,
            }
        )
        if not session_ok:
            next_steps.append(
                "Log in once: "
                f"& '{auth_skill / 'run.ps1'}' ensure --config <config.json>"
            )

    ok = all(check["ok"] for check in checks)
    return {
        "command": "doctor",
        "ok": ok,
        "checks": checks,
        "next_steps": next_steps,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Single-box HeinOnline search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Zero-based result offset (for example 10 starts at result 11)",
    )
    search.add_argument("--timeout", type=int, default=240)
    _add_auth_options(search)
    _add_output_option(search)

    advanced = sub.add_parser("advanced", help="Multi-field advanced search")
    advanced.add_argument(
        "--field",
        action="append",
        required=True,
        help="FIELD=QUERY with FIELD in title|voltitle|creator|text; repeat up to 6",
    )
    advanced.add_argument("--join", default="AND", help="AND|OR|NOT between fields")
    advanced.add_argument("--year-lo", default="")
    advanced.add_argument("--year-hi", default="")
    advanced.add_argument("--collection", default="all")
    advanced.add_argument("--limit", type=int, default=5)
    advanced.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Zero-based result offset (for example 10 starts at result 11)",
    )
    advanced.add_argument("--timeout", type=int, default=240)
    _add_auth_options(advanced)
    _add_output_option(advanced)

    download = sub.add_parser("download", help="Download a section PDF")
    download.add_argument("url")
    download.add_argument("--pdf-out", required=True)
    download.add_argument("--wait-ms", type=int, default=20000)
    download.add_argument("--timeout", type=int, default=300)
    _add_auth_options(download)
    _add_output_option(download)

    doctor_parser = sub.add_parser("doctor", help="Check deployment prerequisites")
    _add_auth_options(doctor_parser)
    _add_output_option(doctor_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        payload = doctor(args)
        _emit(payload, args.out, {"ok": payload["ok"]})
        return 0 if payload["ok"] else 1

    adapter = load_adapter()
    out = getattr(args, "out", None)

    if args.command == "search":
        if not 1 <= args.limit <= 50 or args.offset < 0:
            parser.error("Bounds: limit 1..50, offset >= 0")
        if not 1 <= args.timeout <= 1800:
            parser.error("Bounds: timeout 1..1800 seconds")
        payload = adapter.search(
            args.query,
            args.limit,
            args.offset,
            browser_enabled=True,
            auth_skill=args.auth_skill,
            session_path=args.auth_session,
            config_path=args.auth_config,
            chrome_path=args.chrome_path,
            timeout=args.timeout,
        )
        _emit(
            payload,
            out,
            {
                "status": payload.get("status"),
                "total": payload.get("total"),
                "results": len(payload.get("results") or []),
            },
        )
    elif args.command == "advanced":
        if not 1 <= len(args.field) <= 6:
            parser.error("advanced needs 1..6 --field values")
        if not 1 <= args.limit <= 50 or args.offset < 0:
            parser.error("Bounds: limit 1..50, offset >= 0")
        fields = []
        for spec in args.field:
            name, sep, value = spec.partition("=")
            if not sep:
                parser.error("--field must look like FIELD=QUERY: " + spec)
            fields.append({"field": name.strip(), "terms": value, "join": args.join})
        payload = adapter.search_advanced(
            fields,
            args.limit,
            args.offset,
            browser_enabled=True,
            auth_skill=args.auth_skill,
            year_lo=args.year_lo,
            year_hi=args.year_hi,
            collection=args.collection,
            session_path=args.auth_session,
            config_path=args.auth_config,
            chrome_path=args.chrome_path,
            timeout=args.timeout,
        )
        _emit(
            payload,
            out,
            {
                "status": payload.get("status"),
                "total": payload.get("total"),
                "results": len(payload.get("results") or []),
            },
        )
    else:
        if not 1 <= args.timeout <= 1800 or not 0 <= args.wait_ms <= 120000:
            parser.error("Bounds: timeout 1..1800 seconds, wait-ms 0..120000")
        payload = adapter.download(
            args.url,
            args.pdf_out,
            auth_skill=args.auth_skill,
            session_path=args.auth_session,
            config_path=args.auth_config,
            chrome_path=args.chrome_path,
            timeout=args.timeout,
            wait_ms=args.wait_ms,
        )
        _emit(
            payload,
            out,
            {
                "status": payload.get("status"),
                "pdf": payload.get("out_path"),
                "bytes": payload.get("bytes"),
                "sha256": payload.get("sha256"),
                "warnings": payload.get("warnings", []),
            },
        )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
