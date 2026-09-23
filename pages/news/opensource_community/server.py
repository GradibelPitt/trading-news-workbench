from __future__ import annotations

import os
import re
from typing import Any, Dict

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from crawler import CACHE_JSON, CACHE_MARKDOWN, crawl, load_cache

APP_TITLE = "AI News Crawler"
APP_VERSION = "0.7.0"
DEFAULT_PORT = 8010

app = FastAPI(title=APP_TITLE, version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_payload(refresh: bool = False, days: int = 7, fallback_days: int = 15) -> Dict[str, Any]:
    if not refresh:
        cached = load_cache()
        if cached:
            return cached
    return crawl(days=days, fallback_days=fallback_days)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """
    <!doctype html><meta charset='utf-8'>
    <title>AI News Crawler</title>
    <body style="font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0b0f15;color:#e7edf7;line-height:1.6;padding:32px;">
      <h1>AI News Crawler is running</h1>
      <p>Main endpoint: <code>/api/crawl?refresh=false&amp;days=7&amp;fallback_days=15</code></p>
      <p>Health check: <code>/health</code></p>
    </body>
    """


@app.get("/api/crawl")
def api_crawl(
    refresh: bool = Query(False),
    days: int = Query(7, ge=1, le=60),
    fallback_days: int = Query(15, ge=1, le=90),
) -> Dict[str, Any]:
    return get_payload(refresh=refresh, days=days, fallback_days=fallback_days)


@app.get("/api/news")
def api_news(
    refresh: bool = Query(False),
    days: int = Query(7, ge=1, le=60),
    fallback_days: int = Query(15, ge=1, le=90),
) -> Dict[str, Any]:
    return get_payload(refresh=refresh, days=days, fallback_days=fallback_days)


@app.get("/api/github/preview")
def github_preview(repo: str = Query(..., min_length=3, max_length=180)) -> Dict[str, Any]:
    repo = repo.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise HTTPException(status_code=400, detail="Invalid GitHub repository path")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Gradibel-Local-Workbench/0.6",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"GitHub request failed: {exc}") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository not found or not public")
    if not response.ok:
        detail = response.json().get("message", "GitHub API unavailable") if response.content else "GitHub API unavailable"
        raise HTTPException(status_code=502, detail=detail)

    data = response.json()
    readme = ""
    try:
        readme_response = requests.get(
            f"https://api.github.com/repos/{repo}/readme",
            headers={**headers, "Accept": "application/vnd.github.raw+json"},
            timeout=15,
        )
        if readme_response.ok:
            readme = readme_response.text[:30000]
    except requests.RequestException:
        pass

    return {
        "status": "ok",
        "repo": data.get("full_name") or repo,
        "name": data.get("name") or repo.split("/", 1)[1],
        "owner": (data.get("owner") or {}).get("login") or repo.split("/", 1)[0],
        "description": data.get("description") or "",
        "html_url": data.get("html_url") or f"https://github.com/{repo}",
        "homepage": data.get("homepage") or "",
        "language": data.get("language") or "",
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "issues": data.get("open_issues_count"),
        "license": (data.get("license") or {}).get("spdx_id") or "",
        "topics": data.get("topics") or [],
        "updated_at": data.get("updated_at") or "",
        "readme": readme,
    }


@app.get("/api/markdown", response_class=PlainTextResponse)
def api_markdown(refresh: bool = Query(False)) -> str:
    if refresh or not CACHE_MARKDOWN.exists():
        return get_payload(refresh=True).get("markdown", "")
    return CACHE_MARKDOWN.read_text(encoding="utf-8")


@app.get("/api/download/json")
def download_json() -> FileResponse:
    if not CACHE_JSON.exists():
        get_payload(refresh=True)
    return FileResponse(str(CACHE_JSON), filename="ai_news_latest.json", media_type="application/json")


@app.get("/api/download/markdown")
def download_markdown() -> FileResponse:
    if not CACHE_MARKDOWN.exists():
        get_payload(refresh=True)
    return FileResponse(str(CACHE_MARKDOWN), filename="ai_news_latest.md", media_type="text/markdown")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"ok": "true", "app": APP_TITLE, "version": APP_VERSION}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("OSC_PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
