#!/usr/bin/env python3
"""Offline tests for the standalone HeinOnlineSearcher skill.

Run from the skill directory:

    python scripts/test_heinonline_search.py

The suite stays offline: parsing, URL building and CLI wiring are exercised
with fixtures and patched adapters.  A live run is a separate, manual step
because it needs the GDUFS library session.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import heinonline_adapter as adapter  # noqa: E402
import heinonline_search as cli  # noqa: E402


RESULT_PAGE = """
<html><body>
<div>1,234 results searching for (command responsibility) in All Databases.</div>
<div class="row g-0">
  <div class="section_type_article_b">
    <div class="row">
      <div class="lucene_search_result_number_b">1.</div>
      <h2 class="search-result-heading"><a href="/HOL/Page?handle=h1&amp;div=1">Command Responsibility in Practice</a></h2>
      <div class="search_result_line">Bemba, Jean-Pierre</div>
      <div class="search_result_line">Journal of International Criminal Justice, Vol. 7, Issue 3, pp. 621-640 (2009)</div>
      <div class="topics"><a>War crimes</a><a>Command responsibility</a></div>
      <span class="full_summary_text">Summary one.</span>
    </div>
  </div>
  <div class="section_type_notes_b">
    <div class="row">
      <div class="lucene_search_result_number_b">2.</div>
      <h2 class="search-result-heading"><a href="/HOL/Page?handle=h2&amp;div=2">Superior Responsibility</a></h2>
      <div class="search_result_line">Cited 12 times</div>
      <div class="search_result_line">Some Journal, Vol. 1, Issue 2, pp. 1-20 (2001)</div>
    </div>
  </div>
  <div class="section_type_article_b">
    <div class="row">
      <div class="lucene_search_result_number_b">3.</div>
      <h2 class="search-result-heading"><a href="/HOL/Page?handle=h3&amp;div=3">Customary Law and the ICC</a></h2>
      <div class="search_result_line">A, B</div>
      <span class="full_summary_text">Summary three.</span>
    </div>
  </div>
</div>
</body></html>
"""


class ParseResultsTests(unittest.TestCase):
    def test_parses_total_titles_authors_and_subjects(self):
        parsed = adapter._parse_results(RESULT_PAGE, limit=5)
        self.assertEqual(parsed["total"], 1234)
        self.assertEqual(len(parsed["results"]), 3)
        first = parsed["results"][0]
        self.assertEqual(first["title"], "Command Responsibility in Practice")
        self.assertEqual(first["url"], "/HOL/Page?handle=h1&div=1")
        self.assertEqual(first["authors"], "Bemba, Jean-Pierre")
        self.assertIn("pp. 621-640", first["journal"])
        self.assertEqual(first["summary"], "Summary one.")
        self.assertEqual(first["subjects"], ["War crimes", "Command responsibility"])
        self.assertEqual(first["via"], "heinonline")

    def test_summaries_stay_with_their_own_result(self):
        parsed = adapter._parse_results(RESULT_PAGE, limit=5)
        self.assertEqual(
            [item["summary"] for item in parsed["results"]],
            ["Summary one.", "", "Summary three."],
        )

    def test_limit_truncates_the_first_page(self):
        parsed = adapter._parse_results(RESULT_PAGE, limit=1)
        self.assertEqual(len(parsed["results"]), 1)

    def test_limit_above_the_page_size_warns(self):
        parsed = adapter._parse_results(RESULT_PAGE, limit=5)
        self.assertEqual(len(parsed["results"]), 3)
        self.assertTrue(any("requested limit=5" in w for w in parsed["warnings"]))

    def test_offset_does_not_reslice_an_already_positioned_page(self):
        parsed = adapter._parse_results(RESULT_PAGE, limit=5, offset=10)
        self.assertEqual(len(parsed["results"]), 3)

    def test_empty_page_reports_no_total(self):
        parsed = adapter._parse_results("<html><body>Nothing here</body></html>", limit=5)
        self.assertIsNone(parsed["total"])
        self.assertEqual(parsed["results"], [])

    def test_result_page_size_uses_supported_heinonline_sizes(self):
        self.assertEqual(adapter._result_page_size(1), 10)
        self.assertEqual(adapter._result_page_size(20), 20)
        self.assertEqual(adapter._result_page_size(21), 25)
        self.assertEqual(adapter._result_page_size(50), 50)

    def test_pagination_report_exposes_the_next_result_offset(self):
        parsed = adapter._parse_results(RESULT_PAGE, limit=5, offset=10)
        report = adapter._pagination_report(
            parsed,
            offset=10,
            limit=5,
            page_size=10,
        )
        self.assertEqual(report["firstdoc"], 10)
        self.assertEqual(report["returned"], 3)
        self.assertEqual(report["next_offset"], 13)
        self.assertFalse(report["infinite_scroll"])


class UrlTests(unittest.TestCase):
    def test_build_search_url_includes_pagination(self):
        url = adapter.build_search_url(
            "command responsibility",
            firstdoc=10,
            maxresults=20,
        )
        self.assertTrue(url.startswith("https://access.heinonline.com/HOL/LuceneSearch?"))
        self.assertIn("terms=command+responsibility", url)
        self.assertIn("typea=text", url)
        self.assertIn("maxresults=20", url)
        self.assertIn("firstdoc=10", url)
        self.assertIn("infinite_scroll=off", url)

    def test_build_advanced_url_encodes_fields_operators_and_years(self):
        url = adapter.build_advanced_url(
            [
                {"field": "title", "terms": "command responsibility", "join": "OR"},
                {"field": "text", "terms": "Article 28"},
            ],
            year_lo="2005",
            year_hi="2015",
            firstdoc=10,
            maxresults=20,
        )
        self.assertTrue(url.startswith("https://access.heinonline.com/HOL/LuceneSearch?"))
        self.assertIn("termsa=command+responsibility", url)
        self.assertIn("typea=title", url)
        self.assertIn("operator=OR", url)
        self.assertIn("termsb=Article+28", url)
        self.assertIn("typeb=text", url)
        self.assertIn("yearlo=2005", url)
        self.assertIn("yearhi=2015", url)
        self.assertIn("searchtype=field", url)
        self.assertIn("firstdoc=10", url)
        self.assertIn("maxresults=20", url)
        self.assertIn("infinite_scroll=off", url)
        self.assertIn("submit=Go", url)

    def test_build_advanced_url_rejects_bad_rows(self):
        for bad in (
            [],
            [{"field": "nope", "terms": "x"}],
            [{"field": "text", "terms": " "}],
            [{"field": "text", "terms": "x", "join": "within10"}],
            [{"field": "text", "terms": "x"}] * 7,
        ):
            with self.assertRaises(ValueError):
                adapter.build_advanced_url(bad)

    def test_libvpn_result_urls_are_normalised_to_the_entry_form(self):
        url = (
            "https://libvpn.gdufs.edu.cn/HOL/Page?public=true&handle=hein.journals/jicj7"
            "&div=67&collection=journals&__proto__=https&__host__=access.heinonline.com"
        )
        out = adapter.normalize_libvpn_url(url)
        self.assertTrue(
            out.startswith("https://libvpn.gdufs.edu.cn/-https://access.heinonline.com/HOL/Page?")
        )
        self.assertNotIn("__proto__", out)
        self.assertNotIn("__host__", out)
        self.assertIn("div=67", out)
        unchanged = "https://example.org/HOL/Page?handle=h"
        self.assertEqual(adapter.normalize_libvpn_url(unchanged), unchanged)

    def test_section_pdf_url_prefers_the_labelled_download_link(self):
        html = (
            "<html><body>"
            '<a href="/HOL/PrintRequest?handle=h&amp;div=1&amp;print=section&amp;format=PDFsearchable" '
            'title="Download PDF of This Section">Download PDF of This Section</a>'
            '<a href="/HOL/Page?handle=h">Other</a>'
            "</body></html>"
        )
        self.assertEqual(
            adapter.section_pdf_url(html, "https://libvpn.gdufs.edu.cn/HOL/Page?handle=h"),
            "https://libvpn.gdufs.edu.cn/HOL/PrintRequest?handle=h&div=1"
            "&print=section&format=PDFsearchable",
        )

    def test_section_pdf_url_falls_back_to_an_unlabelled_section_link(self):
        html = '<html><body><a href="/HOL/PrintRequest?handle=h&amp;print=section">x</a></body></html>'
        self.assertEqual(
            adapter.section_pdf_url(html, "https://libvpn.gdufs.edu.cn/HOL/Page?handle=h"),
            "https://libvpn.gdufs.edu.cn/HOL/PrintRequest?handle=h&print=section",
        )

    def test_section_pdf_url_returns_empty_without_a_section_link(self):
        html = '<html><body><a href="/HOL/Page?handle=h">No download here</a></body></html>'
        self.assertEqual(
            adapter.section_pdf_url(html, "https://libvpn.gdufs.edu.cn/HOL/Page?handle=h"), ""
        )

    def test_query_echo_extracts_the_interpreted_query(self):
        html = (
            "<html><body>151 results searching for "
            "((title:(command responsibility) AND (Article 28))) in "
            "151 results searching for ((title:(command responsibility) AND (Article 28))) in All Databases."
            "</body></html>"
        )
        self.assertEqual(
            adapter.query_echo(html), "((title:(command responsibility) AND (Article 28)))"
        )


class AuthSkillTests(unittest.TestCase):
    def test_resolve_prefers_the_explicit_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.ps1").write_text("", encoding="utf-8")
            self.assertEqual(adapter._resolve_auth_skill(tmp), Path(tmp).resolve())

    def test_resolve_uses_the_environment_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.ps1").write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"GDUFS_AUTH_SKILL": tmp}, clear=False):
                self.assertEqual(adapter._resolve_auth_skill(None), Path(tmp).resolve())

    def test_resolve_rejects_a_directory_without_run_ps1(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                adapter._resolve_auth_skill(tmp)

    def test_search_without_browser_reports_disabled(self):
        report = adapter.search("state immunity", browser_enabled=False)
        self.assertEqual(report["status"], "disabled")
        self.assertEqual(report["results"], [])


class CliTests(unittest.TestCase):
    def test_search_command_runs_the_adapter_in_browser_mode(self):
        captured = {}

        def fake_search(query, limit, offset, **kwargs):
            captured.update(query=query, limit=limit, offset=offset, kwargs=kwargs)
            return {"status": "ok", "total": 1, "results": [{"title": "x"}], "warnings": []}

        buffer = io.StringIO()
        with patch.object(cli, "load_adapter", return_value=SimpleNamespace(search=fake_search)), \
                contextlib.redirect_stdout(buffer):
            cli.main(["search", "command responsibility", "--limit", "3"])
        self.assertEqual(captured["query"], "command responsibility")
        self.assertEqual(captured["limit"], 3)
        self.assertTrue(captured["kwargs"]["browser_enabled"])
        self.assertEqual(json.loads(buffer.getvalue())["total"], 1)

    def test_search_command_writes_the_report_file(self):
        def fake_search(query, limit, offset, **kwargs):
            return {"status": "ok", "total": 2, "results": [], "warnings": []}

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "out", "search.json")
            buffer = io.StringIO()
            with patch.object(cli, "load_adapter", return_value=SimpleNamespace(search=fake_search)), \
                    contextlib.redirect_stdout(buffer):
                cli.main(["search", "x", "--out", report_path])
            summary = json.loads(buffer.getvalue())
            self.assertEqual(summary["output"], str(Path(report_path).resolve()))
            self.assertEqual(summary["status"], "ok")
            self.assertTrue(Path(report_path).is_file())
            self.assertEqual(
                json.loads(Path(report_path).read_text(encoding="utf-8"))["total"], 2
            )

    def test_advanced_command_parses_field_rows(self):
        captured = {}

        def fake_advanced(fields, limit, offset, **kwargs):
            captured.update(fields=fields, limit=limit, offset=offset, kwargs=kwargs)
            return {"status": "ok", "mode": "advanced", "results": [], "total": 0, "warnings": []}

        buffer = io.StringIO()
        with patch.object(
            cli, "load_adapter", return_value=SimpleNamespace(search_advanced=fake_advanced)
        ), contextlib.redirect_stdout(buffer):
            cli.main(
                [
                    "advanced",
                    "--field",
                    "title=command responsibility",
                    "--field",
                    "text=Article 28",
                    "--join",
                    "OR",
                    "--year-lo",
                    "2005",
                    "--limit",
                    "3",
                ]
            )
        self.assertEqual(
            captured["fields"][0],
            {"field": "title", "terms": "command responsibility", "join": "OR"},
        )
        self.assertEqual(captured["fields"][1]["terms"], "Article 28")
        self.assertEqual(captured["kwargs"]["year_lo"], "2005")
        self.assertTrue(captured["kwargs"]["browser_enabled"])

    def test_advanced_command_rejects_a_field_without_equals(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["advanced", "--field", "command responsibility"])

    def test_download_command_forwards_the_pdf_target(self):
        calls = {}

        def fake_download(url, out, **kwargs):
            calls["url"], calls["out"] = url, out
            return {
                "status": "ok",
                "out_path": out,
                "bytes": 12,
                "sha256": "a" * 64,
                "warnings": [],
            }

        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "a.pdf")
            report = os.path.join(tmp, "r.json")
            buffer = io.StringIO()
            with patch.object(
                cli, "load_adapter", return_value=SimpleNamespace(download=fake_download)
            ), contextlib.redirect_stdout(buffer):
                cli.main(
                    [
                        "download",
                        "https://libvpn.gdufs.edu.cn/HOL/Page?handle=h",
                        "--pdf-out",
                        pdf,
                        "--out",
                        report,
                    ]
                )
            self.assertEqual(calls["out"], pdf)
            self.assertEqual(json.loads(buffer.getvalue())["pdf"], pdf)

    def test_doctor_reports_ready_when_every_check_passes(self):
        fake_node = SimpleNamespace(returncode=0, stdout="v22.14.0", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.ps1").write_text("", encoding="utf-8")
            fake_adapter = SimpleNamespace(
                _resolve_auth_skill=lambda explicit=None: Path(tmp).resolve()
            )
            buffer = io.StringIO()
            with patch.object(cli, "load_adapter", return_value=fake_adapter), \
                    patch.object(cli, "_find_powershell", return_value="pwsh"), \
                    patch.object(cli, "_find_chrome", return_value="/usr/bin/chrome"), \
                    patch.object(cli.shutil, "which", return_value="/usr/bin/node"), \
                    patch.object(cli.subprocess, "run", return_value=fake_node), \
                    patch.object(
                        cli,
                        "_run_auth_status",
                        return_value={"status": "valid", "valid": True, "exit_code": 0},
                    ), contextlib.redirect_stdout(buffer):
                code = cli.main(["doctor"])
            payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["next_steps"], [])
        self.assertEqual(
            [check["name"] for check in payload["checks"]],
            ["python", "powershell", "node", "chrome", "auth_skill", "session"],
        )

    def test_doctor_explains_missing_prerequisites(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with patch.object(
                cli,
                "load_adapter",
                return_value=SimpleNamespace(_resolve_auth_skill=lambda explicit=None: Path(tmp).resolve()),
            ), patch.object(cli, "_find_powershell", return_value=""), \
                    patch.object(cli, "_find_chrome", return_value=""), \
                    patch.object(cli.shutil, "which", return_value=None), \
                    contextlib.redirect_stdout(buffer):
                code = cli.main(["doctor"])
            payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("Node.js 22" in step for step in payload["next_steps"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
