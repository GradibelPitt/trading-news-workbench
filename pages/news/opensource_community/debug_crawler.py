from __future__ import annotations

import datetime as dt
import json
import os
import platform
import socket
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
REPORT_TXT = ROOT / "debug_report.txt"
REPORT_JSON = ROOT / "debug_results.json"

URLS = [
    ("GitHubTrendingRSS weekly", "https://mshibanami.github.io/GitHubTrendingRSS/weekly/all.xml"),
    ("GitHubTrendingRSS monthly", "https://mshibanami.github.io/GitHubTrendingRSS/monthly/all.xml"),
    ("huchen github-trending-api weekly", "https://ghapi.huchen.dev/repositories?since=weekly"),
    ("huchen github-trending-api monthly", "https://ghapi.huchen.dev/repositories?since=monthly"),
    ("github.com/trending weekly", "https://github.com/trending?since=weekly"),
    ("arXiv recent cs.AI", "https://export.arxiv.org/api/query?search_query=cat:cs.AI&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending"),
]


def line(msg: str = "") -> None:
    print(msg, flush=True)


def short(text: Any, n: int = 500) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " ")
    return value[:n]


def check_dns(host: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {"host": host, "ok": False}
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        addrs = sorted({info[4][0] for info in infos})
        item.update({"ok": True, "addresses": addrs[:8]})
    except Exception as exc:
        item.update({"error": repr(exc)})
    return item


def check_url(session: Any, name: str, url: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {"name": name, "url": url, "ok": False}
    try:
        resp = session.get(url, timeout=25)
        item.update({
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "length": len(resp.content),
            "sample": short(resp.text, 350),
        })
        resp.raise_for_status()
        item["ok"] = True
    except Exception as exc:
        item.update({"error": repr(exc), "traceback": traceback.format_exc(limit=2)})
    return item


def run_crawler_functions() -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False}
    try:
        import crawler

        result["module"] = str(Path(crawler.__file__).resolve())
        gh = crawler.GitHubTrendingClient()
        github_checks: List[Dict[str, Any]] = []
        for since in ("weekly", "monthly"):
            for fn_name in ("trending_rss", "trending_public_api", "trending_html"):
                fn = getattr(gh, fn_name)
                row: Dict[str, Any] = {"source": f"github.{fn_name}.{since}", "ok": False}
                try:
                    records = fn(since)
                    row.update({
                        "ok": True,
                        "count": len(records),
                        "first_titles": [r.title for r in records[:5]],
                    })
                except Exception as exc:
                    row.update({"error": repr(exc), "traceback": traceback.format_exc(limit=2)})
                github_checks.append(row)
        result["github_source_checks"] = github_checks

        arxiv_row: Dict[str, Any] = {"source": "arxiv.fetch_recent", "ok": False}
        try:
            records = crawler.ArxivClient().fetch_recent(days=15)
            arxiv_row.update({
                "ok": True,
                "count": len(records),
                "first_titles": [r.title for r in records[:5]],
            })
        except Exception as exc:
            arxiv_row.update({"error": repr(exc), "traceback": traceback.format_exc(limit=2)})
        result["arxiv_check"] = arxiv_row

        crawl_row: Dict[str, Any] = {"source": "crawler.crawl", "ok": False}
        try:
            data = crawler.crawl(days=7, fallback_days=15)
            crawl_row.update({
                "ok": True,
                "item_count": data.get("item_count"),
                "github_count": data.get("github_count"),
                "arxiv_count": data.get("arxiv_count"),
                "errors": data.get("errors"),
                "first_items": [
                    {
                        "kind": item.get("kind"),
                        "name": item.get("name"),
                        "link": item.get("link"),
                        "date": item.get("date"),
                    }
                    for item in (data.get("items") or [])[:8]
                ],
            })
        except Exception as exc:
            crawl_row.update({"error": repr(exc), "traceback": traceback.format_exc(limit=4)})
        result["full_crawl_check"] = crawl_row
        result["ok"] = True
    except Exception as exc:
        result.update({"error": repr(exc), "traceback": traceback.format_exc(limit=8)})
    return result


def main() -> int:
    started = dt.datetime.now().isoformat(timespec="seconds")
    line("=" * 72)
    line("OpenSource Community Crawler DEBUG")
    line("=" * 72)
    line(f"Time: {started}")
    line(f"CWD:  {os.getcwd()}")
    line(f"File: {__file__}")
    line(f"Python: {sys.version}")
    line(f"Executable: {sys.executable}")
    line(f"Platform: {platform.platform()}")
    line("")

    report: Dict[str, Any] = {
        "started_at": started,
        "cwd": os.getcwd(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "dns": [],
        "url_checks": [],
        "crawler_checks": {},
    }

    line("[1/3] DNS checks")
    for host in ["github.com", "mshibanami.github.io", "ghapi.huchen.dev", "export.arxiv.org"]:
        row = check_dns(host)
        report["dns"].append(row)
        line(f"  {'OK ' if row.get('ok') else 'ERR'} {host} {row.get('addresses') or row.get('error')}")
    line("")

    line("[2/3] Raw URL checks")
    try:
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 AI-News-Crawler-Debug/1.0",
            "Accept": "application/json, application/xml, text/html;q=0.9, */*;q=0.8",
        })
        for name, url in URLS:
            row = check_url(session, name, url)
            report["url_checks"].append(row)
            if row.get("ok"):
                line(f"  OK  {name}: HTTP {row.get('status_code')} / {row.get('length')} bytes")
            else:
                line(f"  ERR {name}: {row.get('error')}")
    except Exception as exc:
        report["url_checks_error"] = repr(exc)
        line(f"  ERR requests import/check failed: {exc!r}")
    line("")

    line("[3/3] crawler.py function checks")
    crawler_result = run_crawler_functions()
    report["crawler_checks"] = crawler_result

    for row in crawler_result.get("github_source_checks", []):
        if row.get("ok"):
            line(f"  OK  {row['source']}: {row.get('count')} records | {row.get('first_titles')}")
        else:
            line(f"  ERR {row.get('source')}: {row.get('error')}")
    arxiv = crawler_result.get("arxiv_check") or {}
    if arxiv:
        if arxiv.get("ok"):
            line(f"  OK  arxiv.fetch_recent: {arxiv.get('count')} records | {arxiv.get('first_titles')}")
        else:
            line(f"  ERR arxiv.fetch_recent: {arxiv.get('error')}")
    full = crawler_result.get("full_crawl_check") or {}
    if full:
        if full.get("ok"):
            line(f"  OK  crawler.crawl: items={full.get('item_count')} github={full.get('github_count')} arxiv={full.get('arxiv_count')}")
            line(f"      first_items={full.get('first_items')}")
            if full.get("errors"):
                line(f"      warnings={full.get('errors')}")
        else:
            line(f"  ERR crawler.crawl: {full.get('error')}")

    report["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Capture a readable text report too.
    lines: List[str] = []
    lines.append("OpenSource Community Crawler DEBUG REPORT")
    lines.append(f"started_at: {report['started_at']}")
    lines.append(f"finished_at: {report['finished_at']}")
    lines.append(f"python: {report['executable']}")
    lines.append("")
    lines.append("DNS:")
    for row in report["dns"]:
        lines.append(f"- {row['host']}: {'OK' if row.get('ok') else 'ERR'} {row.get('addresses') or row.get('error')}")
    lines.append("")
    lines.append("URL checks:")
    for row in report["url_checks"]:
        lines.append(f"- {row['name']}: {'OK' if row.get('ok') else 'ERR'} status={row.get('status_code')} len={row.get('length')} error={row.get('error')}")
    lines.append("")
    lines.append("Full crawl:")
    lines.append(json.dumps(full, ensure_ascii=False, indent=2))
    REPORT_TXT.write_text("\n".join(lines), encoding="utf-8")

    line("")
    line(f"Report written: {REPORT_TXT}")
    line(f"JSON written:   {REPORT_JSON}")
    line("=" * 72)

    # Non-zero exit only if absolutely nothing worked.
    full_ok = bool(full.get("ok") and (full.get("item_count") or 0) > 0)
    raw_ok = any(row.get("ok") for row in report.get("url_checks", []))
    return 0 if full_ok or raw_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
