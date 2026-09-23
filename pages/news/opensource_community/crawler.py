from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CACHE_JSON = DATA_DIR / "latest.json"
CACHE_MARKDOWN = DATA_DIR / "latest.md"
RAW_JSON = DATA_DIR / "raw_sources.json"

GITHUB_TRENDING_RSS_URL = "https://mshibanami.github.io/GitHubTrendingRSS/{since}/all.xml"
GITHUB_TRENDING_PUBLIC_API = "https://ghapi.huchen.dev/repositories"
GITHUB_TRENDING_HTML_URL = "https://github.com/trending"
ARXIV_API = "https://export.arxiv.org/api/query"

GITHUB_TRENDING_WINDOWS = ["weekly", "monthly"]
ARXIV_CATEGORIES = ["cs.AI", "cs.CL", "cs.CV", "cs.LG", "cs.SE", "cs.CR"]
ARXIV_KEYWORDS = [
    "agent", "agents", "llm", "large language model", "multimodal", "reasoning",
    "video", "benchmark", "dataset", "coding", "code generation", "repository",
    "rag", "retrieval", "mcp", "embodied", "cyber range", "post-exploitation",
]

# Ordered, weighted taxonomy used for both GitHub repositories and papers.
# Title matches receive more weight than description matches so a project name
# such as "vector database" is not overridden by incidental README wording.
DOMAIN_KEYWORDS = {
    "AI Agent": {
        "agent": 3, "agents": 3, "agentic": 4, "autonomous agent": 5,
        "multi-agent": 5, "computer use": 4, "tool calling": 4,
    },
    "大语言模型": {
        "llm": 4, "large language model": 5, "language model": 3,
        "transformer": 2, "inference": 2, "fine-tuning": 3, "finetuning": 3,
        "prompt": 2, "reasoning model": 4,
    },
    "RAG 与搜索": {
        "rag": 5, "retrieval augmented": 5, "retrieval-augmented": 5,
        "semantic search": 4, "search engine": 3, "reranker": 4,
        "embedding search": 4,
    },
    "数据与向量库": {
        "vector database": 5, "vector index": 5, "vector store": 5,
        "database": 2, "data pipeline": 3, "etl": 3, "analytics": 2,
        "quantization": 3, "faiss": 4,
    },
    "开发者工具": {
        "developer tool": 4, "devtool": 4, "code generation": 4,
        "coding assistant": 5, "code editor": 3, "ide": 3, "sdk": 2,
        "cli": 3, "debugger": 3, "compiler": 3, "api": 1,
    },
    "计算机视觉": {
        "computer vision": 5, "vision-language": 5, "image generation": 4,
        "object detection": 4, "segmentation": 3, "diffusion": 3,
        "video generation": 4, "ocr": 3,
    },
    "语音与音频": {
        "speech": 4, "audio": 3, "text-to-speech": 5, "tts": 4,
        "voice": 3, "music generation": 4, "speech recognition": 5,
    },
    "安全与隐私": {
        "cybersecurity": 5, "security": 3, "vulnerability": 4,
        "exploit": 4, "malware": 4, "privacy": 3, "authentication": 2,
        "pentest": 5, "post-exploitation": 5,
    },
    "机器人与具身智能": {
        "robotics": 5, "robot": 4, "embodied": 5, "autonomous driving": 5,
        "slam": 4, "manipulation": 3,
    },
    "云原生与基础设施": {
        "kubernetes": 5, "container": 3, "cloud native": 5, "observability": 4,
        "distributed system": 4, "infrastructure": 3, "deployment": 2,
        "serverless": 4, "devops": 4,
    },
    "Web 与应用": {
        "web app": 3, "frontend": 3, "browser": 3, "mobile app": 3,
        "desktop app": 3, "react": 2, "vue": 2, "svelte": 2,
    },
    "评测与研究": {
        "benchmark": 4, "evaluation": 3, "dataset": 3, "survey": 3,
        "research": 2, "paper": 1, "leaderboard": 4,
    },
}

REQUEST_TIMEOUT = 20
REQUEST_SLEEP_SECONDS = 0.35
MAX_TRENDING_PER_WINDOW = 40
MAX_ARXIV_RESULTS = 160


@dataclass
class SourceRecord:
    source_type: str
    source_name: str
    title: str
    url: str
    date: str
    timestamp: int
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsItem:
    key: str
    name: str
    description: str
    link: str = ""
    date: str = ""
    latest_ts: int = 0
    kind: str = ""
    source_names: List[str] = field(default_factory=list)
    source_records: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    stars: Optional[int] = None
    language: str = ""
    domain: str = "其他"
    domain_keywords: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


def now_ts() -> int:
    return int(dt.datetime.now().timestamp())


def cutoff_date(days: int) -> str:
    return (dt.datetime.now() - dt.timedelta(days=days)).date().isoformat()


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value: str) -> Tuple[str, int]:
    if not value:
        today = dt.datetime.now().date().isoformat()
        return today, now_ts()
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return parsed.date().isoformat(), int(parsed.timestamp())
    except Exception:
        pass
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return parsed.date().isoformat(), int(parsed.timestamp())
    except Exception:
        today = dt.datetime.now().date().isoformat()
        return today, now_ts()


def strip_owner_repo(url_or_path: str) -> str:
    path = urlparse(url_or_path).path if str(url_or_path).startswith("http") else str(url_or_path)
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def normalize_github_url(url: str) -> str:
    repo = strip_owner_repo(url)
    return f"https://github.com/{repo}" if repo else str(url or "").strip()


def dedupe_key(name: str, link: str, kind: str) -> str:
    if kind == "github" and link:
        return normalize_github_url(link).lower()
    if "arxiv.org/abs/" in (link or ""):
        return "arxiv:" + link.rstrip("/").split("/")[-1].lower()
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "", (name or "").lower())
    return f"{kind}:{cleaned or (name or '').lower().strip()}"


def keyword_tags(text: str) -> List[str]:
    low = (text or "").lower()
    rules = [
        ("Agent", ["agent", "agents"]),
        ("LLM", ["llm", "large language model"]),
        ("RAG", ["rag", "retrieval"]),
        ("Coding", ["coding", "code generation", "repository", "developer"]),
        ("Multimodal", ["multimodal", "vision-language", "audio-visual", "video"]),
        ("Benchmark", ["benchmark", "evaluation", "dataset"]),
        ("Security", ["cyber", "security", "exploit", "post-exploitation"]),
        ("MCP", ["mcp", "model context protocol"]),
    ]
    tags: List[str] = []
    for tag, keys in rules:
        if any(k in low for k in keys):
            tags.append(tag)
    return tags[:6]


def _keyword_matches(text: str, keyword: str) -> bool:
    if not keyword:
        return False
    # Boundaries prevent short terms such as RAG from matching "storage".
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", text))


def classify_domain(title: str, description: str) -> Tuple[str, List[str]]:
    title_text = clean_text(title).lower()
    description_text = clean_text(description).lower()
    ranked: List[Tuple[int, int, str, List[str]]] = []
    for order, (domain, keywords) in enumerate(DOMAIN_KEYWORDS.items()):
        score = 0
        matched: List[str] = []
        for keyword, weight in keywords.items():
            in_title = _keyword_matches(title_text, keyword)
            in_description = _keyword_matches(description_text, keyword)
            if in_title or in_description:
                score += weight * (3 if in_title else 1)
                matched.append(keyword)
        if score:
            ranked.append((score, -order, domain, matched))
    if not ranked:
        return "其他", []
    _, _, domain, matched = max(ranked, key=lambda row: (row[0], row[1]))
    return domain, matched[:4]


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 AI-News-Crawler/0.6",
        "Accept": "application/json, application/xml, text/html;q=0.9, */*;q=0.8",
    })
    return s


class GitHubTrendingClient:
    def __init__(self) -> None:
        self.session = http_session()

    def trending_rss(self, since: str) -> List[SourceRecord]:
        url = GITHUB_TRENDING_RSS_URL.format(since=since)
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = root.findall("./channel/item") or root.findall(".//item")
        records: List[SourceRecord] = []
        for item in items[:MAX_TRENDING_PER_WINDOW]:
            title = clean_text(item.findtext("title", default=""))
            link = clean_text(item.findtext("link", default=""))
            raw_desc = item.findtext("description", default="") or ""
            description = clean_text(BeautifulSoup(html.unescape(raw_desc), "html.parser").get_text(" ", strip=True))
            repo = strip_owner_repo(link) or strip_owner_repo(title)
            if not repo:
                m = re.search(r"([A-Za-z0-9_.-]+)\s*/\s*([A-Za-z0-9_.-]+)", f"{title} {description}")
                repo = f"{m.group(1)}/{m.group(2)}" if m else ""
            if not repo:
                continue
            date, ts = parse_date(item.findtext("pubDate", default=""))
            records.append(SourceRecord(
                source_type="github",
                source_name=f"GitHub Trending RSS {since}",
                title=repo,
                url=normalize_github_url(link or f"https://github.com/{repo}"),
                date=date,
                timestamp=ts,
                description=description,
                metadata={"window": since, "source_mode": "rss"},
            ))
        return records

    def trending_public_api(self, since: str) -> List[SourceRecord]:
        resp = self.session.get(GITHUB_TRENDING_PUBLIC_API, params={"since": since}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("repositories") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        today = dt.datetime.now().date().isoformat()
        ts = now_ts()
        records: List[SourceRecord] = []
        for row in rows[:MAX_TRENDING_PER_WINDOW]:
            if not isinstance(row, dict):
                continue
            author = clean_text(row.get("author"))
            name = clean_text(row.get("name"))
            url = row.get("url") or ""
            full_name = f"{author}/{name}" if author and name else strip_owner_repo(url)
            if not full_name:
                continue
            records.append(SourceRecord(
                source_type="github",
                source_name=f"GitHub Trending API {since}",
                title=full_name,
                url=normalize_github_url(url or f"https://github.com/{full_name}"),
                date=today,
                timestamp=ts,
                description=clean_text(row.get("description")),
                metadata={
                    "window": since,
                    "source_mode": "public_api",
                    "language": clean_text(row.get("language")),
                    "stars": row.get("stars"),
                    "forks": row.get("forks"),
                    "current_period_stars": row.get("currentPeriodStars"),
                },
            ))
        return records

    def trending_html(self, since: str) -> List[SourceRecord]:
        resp = self.session.get(GITHUB_TRENDING_HTML_URL, params={"since": since}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        today = dt.datetime.now().date().isoformat()
        ts = now_ts()
        records: List[SourceRecord] = []
        for article in soup.select("article.Box-row")[:MAX_TRENDING_PER_WINDOW]:
            a = article.select_one("h2 a")
            if not a or not a.get("href"):
                continue
            full_name = strip_owner_repo(a.get("href", ""))
            if not full_name:
                continue
            desc_node = article.select_one("p")
            language_node = article.select_one('[itemprop="programmingLanguage"]')
            total_stars = None
            star_links = article.select('a.Link--muted[href$="/stargazers"]')
            if star_links:
                raw = clean_text(star_links[0].get_text(" ", strip=True)).replace(",", "")
                if raw.isdigit():
                    total_stars = int(raw)
            records.append(SourceRecord(
                source_type="github",
                source_name=f"GitHub Trending HTML {since}",
                title=full_name,
                url=f"https://github.com/{full_name}",
                date=today,
                timestamp=ts,
                description=clean_text(desc_node.get_text(" ", strip=True) if desc_node else ""),
                metadata={
                    "window": since,
                    "source_mode": "html_fallback",
                    "language": clean_text(language_node.get_text(" ", strip=True) if language_node else ""),
                    "stars": total_stars,
                },
            ))
        return records

    def trending(self, since: str) -> List[SourceRecord]:
        errors: List[str] = []
        for fetcher in (self.trending_rss, self.trending_public_api, self.trending_html):
            try:
                records = fetcher(since)
                if records:
                    return records
                errors.append(f"{fetcher.__name__} returned empty")
            except Exception as exc:
                errors.append(f"{fetcher.__name__}: {exc}")
        raise RuntimeError("; ".join(errors))


class ArxivClient:
    def __init__(self) -> None:
        self.session = http_session()

    def fetch_recent(self, days: int) -> List[SourceRecord]:
        query = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
        resp = self.session.get(ARXIV_API, params={
            "search_query": query,
            "start": 0,
            "max_results": MAX_ARXIV_RESULTS,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        records: List[SourceRecord] = []
        for entry in root.findall("atom:entry", ns):
            title = clean_text(entry.findtext("atom:title", default="", namespaces=ns))
            summary = clean_text(entry.findtext("atom:summary", default="", namespaces=ns))
            published_raw = entry.findtext("atom:published", default="", namespaces=ns)
            try:
                published_dt = dt.datetime.fromisoformat((published_raw or "").replace("Z", "+00:00"))
            except Exception:
                continue
            if published_dt < cutoff:
                continue
            combined = f"{title}\n{summary}".lower()
            if not any(k in combined for k in ARXIV_KEYWORDS):
                continue
            link = entry.findtext("atom:id", default="", namespaces=ns)
            for link_node in entry.findall("atom:link", ns):
                if link_node.attrib.get("rel") == "alternate":
                    link = link_node.attrib.get("href", link)
                    break
            cats = [c.attrib.get("term", "") for c in entry.findall("atom:category", ns) if c.attrib.get("term")]
            authors = [clean_text(a.findtext("atom:name", default="", namespaces=ns)) for a in entry.findall("atom:author", ns)]
            records.append(SourceRecord(
                source_type="arxiv",
                source_name="arXiv recent",
                title=title,
                url=link,
                date=published_dt.date().isoformat(),
                timestamp=int(published_dt.timestamp()),
                description=summary,
                metadata={"categories": cats, "authors": authors[:8]},
            ))
        return records


def github_record_to_item(record: SourceRecord) -> NewsItem:
    meta = record.metadata or {}
    desc_parts = [record.description] if record.description else []
    details = []
    if meta.get("stars") is not None:
        details.append(f"⭐ {meta.get('stars')}")
    if meta.get("current_period_stars") is not None:
        details.append(f"period +{meta.get('current_period_stars')}")
    if meta.get("language"):
        details.append(str(meta.get("language")))
    if details:
        desc_parts.append(" / ".join(details))
    tags = ["GitHub"] + keyword_tags(f"{record.title} {record.description}")
    domain, domain_keywords = classify_domain(record.title, record.description)
    return NewsItem(
        key=dedupe_key(record.title, record.url, "github"),
        name=record.title,
        description="\n".join(desc_parts).strip() or "GitHub repository",
        link=normalize_github_url(record.url),
        date=record.date,
        latest_ts=record.timestamp,
        kind="github",
        source_names=[record.source_name],
        source_records=[asdict(record)],
        tags=list(dict.fromkeys(tags))[:7],
        stars=meta.get("stars"),
        language=meta.get("language") or "",
        domain=domain,
        domain_keywords=domain_keywords,
        raw=meta,
    )


def arxiv_record_to_item(record: SourceRecord) -> NewsItem:
    meta = record.metadata or {}
    tags = ["arXiv"] + keyword_tags(f"{record.title} {record.description}")
    domain, domain_keywords = classify_domain(record.title, record.description)
    for cat in (meta.get("categories") or [])[:2]:
        if cat not in tags:
            tags.append(cat)
    desc = record.description
    authors = meta.get("authors") or []
    if authors:
        desc += "\n作者：" + ", ".join(authors[:5])
    return NewsItem(
        key=dedupe_key(record.title, record.url, "arxiv"),
        name=record.title,
        description=desc.strip(),
        link=record.url,
        date=record.date,
        latest_ts=record.timestamp,
        kind="arxiv",
        source_names=[record.source_name],
        source_records=[asdict(record)],
        tags=list(dict.fromkeys(tags))[:8],
        domain=domain,
        domain_keywords=domain_keywords,
        raw=meta,
    )


def merge_items(items: Iterable[NewsItem]) -> List[NewsItem]:
    merged: Dict[str, NewsItem] = {}
    for item in items:
        if item.key not in merged:
            merged[item.key] = item
            continue
        existing = merged[item.key]
        if item.latest_ts > existing.latest_ts:
            existing.date = item.date
            existing.latest_ts = item.latest_ts
            existing.description = item.description or existing.description
            existing.name = item.name or existing.name
            existing.link = item.link or existing.link
        for s in item.source_names:
            if s not in existing.source_names:
                existing.source_names.append(s)
        for t in item.tags:
            if t not in existing.tags:
                existing.tags.append(t)
    result = list(merged.values())
    result.sort(key=lambda x: (x.latest_ts, x.stars or 0, x.name.lower()), reverse=True)
    return result


def fetch_records(days: int) -> Tuple[List[SourceRecord], List[str]]:
    records: List[SourceRecord] = []
    errors: List[str] = []
    gh = GitHubTrendingClient()
    for since in GITHUB_TRENDING_WINDOWS:
        try:
            records.extend(gh.trending(since))
        except Exception as exc:
            errors.append(f"GitHub Trending {since} failed: {exc}")
        time.sleep(REQUEST_SLEEP_SECONDS)
    try:
        records.extend(ArxivClient().fetch_recent(days))
    except Exception as exc:
        errors.append(f"arXiv failed: {exc}")
    unique: Dict[str, SourceRecord] = {}
    for r in records:
        key = f"{r.source_type}:{r.url.lower()}"
        if key not in unique or r.timestamp > unique[key].timestamp:
            unique[key] = r
    return sorted(unique.values(), key=lambda r: r.timestamp, reverse=True), errors


def build_markdown(items: List[NewsItem], days_used: int, errors: List[str]) -> str:
    lines = [
        "# AI / GitHub / arXiv 资讯抓取结果",
        "",
        f"- 生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 时间窗口：最近 {days_used} 天",
        f"- 去重后条目数：{len(items)}",
        f"- GitHub：{sum(1 for i in items if i.kind == 'github')}",
        f"- arXiv：{sum(1 for i in items if i.kind == 'arxiv')}",
        "",
    ]
    current_date = ""
    for item in items:
        if item.date != current_date:
            current_date = item.date
            lines += [f"## {current_date}", ""]
        lines += [f"### {item.name}", f"- 类型：{item.kind}"]
        if item.link:
            lines.append(f"- 链接：{item.link}")
        if item.tags:
            lines.append(f"- 标签：{', '.join(item.tags)}")
        if item.source_names:
            lines.append(f"- 来源：{', '.join(item.source_names)}")
        lines.append("- 简介：")
        for line in item.description.splitlines():
            lines.append(f"  {line}")
        lines.append("")
    if errors:
        lines += ["---", "", "## 抓取警告", ""]
        lines += [f"- {e}" for e in errors]
    return "\n".join(lines)


def crawl(days: int = 7, fallback_days: int = 15) -> Dict[str, Any]:
    records, errors = fetch_records(days)
    days_used = days
    items: List[NewsItem] = []
    for r in records:
        if r.source_type == "github":
            items.append(github_record_to_item(r))
        elif r.source_type == "arxiv":
            items.append(arxiv_record_to_item(r))
    items = merge_items(items)
    if not items and fallback_days > days:
        records, errors = fetch_records(fallback_days)
        days_used = fallback_days
        items = merge_items(github_record_to_item(r) if r.source_type == "github" else arxiv_record_to_item(r) for r in records)
    markdown = build_markdown(items, days_used, errors)
    result = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "days_used": days_used,
        "source_count": len(records),
        "record_count": len(records),
        "item_count": len(items),
        "github_count": sum(1 for i in items if i.kind == "github"),
        "arxiv_count": sum(1 for i in items if i.kind == "arxiv"),
        "items": [asdict(i) for i in items],
        "records": [asdict(r) for r in records],
        "markdown": markdown,
        "errors": errors,
        "config": {
            "github_trending_windows": GITHUB_TRENDING_WINDOWS,
            "arxiv_categories": ARXIV_CATEGORIES,
            "arxiv_keywords": ARXIV_KEYWORDS,
        },
    }
    CACHE_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    CACHE_MARKDOWN.write_text(markdown, encoding="utf-8")
    RAW_JSON.write_text(json.dumps(result["records"], ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_cache() -> Optional[Dict[str, Any]]:
    if not CACHE_JSON.exists():
        return None
    try:
        return json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    data = crawl()
    print(data["markdown"])
