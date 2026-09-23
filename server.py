from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse
import json
import mimetypes
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from news_core import analyze_news, chat_about_news, fetch_flash
from auth import AuthDB
from agents.agent_runtime import ai_chat_pipeline
from ai_config import (
    clear_llm_config,
    config_status,
    parse_config_payload,
    provider_catalog,
    resolve_llm_config,
    save_llm_config,
    validate_llm_config,
)


ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("SERVER_PORT", 8765))

# Stock analysis page paths and backend
STOCK_PAGE_DIR = ROOT / "pages" / "stock-analysis-page" / "frontend"
STOCK_BACKEND_URL = os.environ.get("STOCK_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

# --- Auth ---
AUTH_DB = AuthDB(str(ROOT / "data" / "auth.db"))
AUTH_COOKIE_NAME = "auth_token"
AUTH_COOKIE_MAX_AGE = 604800  # 7 days


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/flash":
            self.handle_flash(parsed.query)
            return
        if parsed.path == "/api/websearch":
            self.handle_websearch(parsed.query)
            return
        if parsed.path == "/api/auth/session":
            self.handle_auth_session()
            return
        if parsed.path == "/api/watchlist":
            self.handle_watchlist_get()
            return
        if parsed.path == "/api/ai/config":
            self.handle_ai_config_get()
            return
        if parsed.path == "/api/ai/providers":
            self.respond_json(json.dumps({"code": 0, "providers": provider_catalog()}, ensure_ascii=False).encode("utf-8"))
            return
        # --- Stock analysis page proxy routes ---
        if parsed.path == "/stock-analysis" or parsed.path == "/stock-analysis/":
            self.handle_stock_page()
            return
        if parsed.path.startswith("/stock-analysis/static/"):
            self.handle_stock_static(parsed.path)
            return
        if parsed.path.startswith("/stock-analysis/api/"):
            self.proxy_stock_api(parsed.path, parsed.query)
            return
        if parsed.path == "/":
            # Auto-admin session if no valid session cookie
            user = self._get_session_user()
            if not user:
                user, token = self._auto_admin_session()
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={AUTH_COOKIE_MAX_AGE}")
                self.end_headers()
                return
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/analyze":
            self.handle_analyze()
            return
        if parsed.path == "/api/chat":
            self.handle_chat()
            return
        if parsed.path == "/api/ai/config":
            self.handle_ai_config_save()
            return
        if parsed.path == "/api/ai/config/clear":
            self.handle_ai_config_clear()
            return
        if parsed.path == "/api/auth/login":
            self.handle_auth_login()
            return
        if parsed.path == "/api/auth/register":
            self.handle_auth_register()
            return
        if parsed.path == "/api/auth/logout":
            self.handle_auth_logout()
            return
        if parsed.path == "/api/watchlist/add":
            self.handle_watchlist_add()
            return
        if parsed.path == "/api/watchlist/remove":
            self.handle_watchlist_remove(parsed.query)
            return
        if parsed.path.startswith("/stock-analysis/api/"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length else None
            self.proxy_stock_api(parsed.path, parsed.query, method="POST", body=body)
            return
        self.respond_error(404, "Not found")

    # --- Auth helpers ---

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _get_session_user(self) -> Optional[dict]:
        """Extract user from auth_token cookie."""
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            part = part.strip()
            if part.startswith(f"{AUTH_COOKIE_NAME}="):
                token = part[len(AUTH_COOKIE_NAME) + 1:]
                return AUTH_DB.validate_session(token)
        return None

    def _auto_admin_session(self) -> dict:
        """Create an admin session and return the user dict."""
        _, user = AUTH_DB.ensure_admin("12345")
        token = AUTH_DB.create_session(user["id"])
        return user, token

    @staticmethod
    def _user_cache_scope(user):
        """Return cache scope string. admin -> 'admin', others -> 'u_<id>'."""
        if not user or user.get("username") == "admin":
            return "admin"
        return f"u_{user['id']}"

    # --- Stock analysis page handlers ---

    def handle_stock_page(self):
        """Serve the stock-analysis frontend HTML with API_BASE rewritten."""
        html_path = STOCK_PAGE_DIR / "index.html"
        if not html_path.exists():
            self.respond_error(404, "Stock analysis page not found")
            return
        html = html_path.read_text(encoding="utf-8")
        # Rewrite static asset paths and inject API_BASE so the frontend
        # calls /stock-analysis/api/* instead of /api/* on the FastAPI port.
        html = html.replace('href="/static/', 'href="/stock-analysis/static/')
        html = html.replace('src="/static/', 'src="/stock-analysis/static/')
        # Inject API_BASE before closing </head> so app.js picks it up
        user = self._get_session_user()
        user_script = ""
        if user:
            user_script = f"<script>window.__USER__ = {{id: {user['id']}, username: '{user['username']}'}};</script>\n"
        else:
            user_script = "<script>window.__USER__ = null;</script>\n"
        html = html.replace(
            "</head>",
            user_script + "<script>window.STOCK_API_BASE = '/stock-analysis';</script>\n</head>"
        )
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_stock_static(self, path):
        """Serve static files from the stock-analysis frontend directory."""
        relative = path.replace("/stock-analysis/static/", "", 1)
        file_path = STOCK_PAGE_DIR / relative
        if not file_path.exists() or not file_path.is_file():
            self.respond_error(404, f"Static file not found: {relative}")
            return
        content_type, _ = mimetypes.guess_type(str(file_path))
        content_type = content_type or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def proxy_stock_api(self, path, query, method="GET", body=None):
        """Reverse-proxy requests to the stock-analysis FastAPI backend."""
        # Inject X-User-Id as cache scope (not numeric user ID)
        user = self._get_session_user()
        scope = self._user_cache_scope(user)
        backend_path = path.replace("/stock-analysis", "", 1)
        url = f"{STOCK_BACKEND_URL}{backend_path}"
        if query:
            url += f"?{query}"
        try:
            req_headers = {
                "Accept": "application/json",
                "X-User-Id": scope,
            }
            data_bytes = body.encode("utf-8") if body else None
            req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
            if data_bytes:
                req.add_header("Content-Type", "application/json; charset=utf-8")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)
        except Exception as exc:
            self.respond_error(502, f"Stock analysis backend error: {exc}")

    # --- Existing handlers ---

    def handle_flash(self, query):
        params = parse_qs(query)
        source = params.get("source", ["zh"])[0]
        page_size = int(params.get("pageSize", ["20"])[0])
        seq_mark = params.get("seqMark", [""])[0]

        try:
            payload = fetch_flash(source=source, page_size=page_size, seq_mark=seq_mark)
            self.respond_json(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.respond_error(exc.code, f"Upstream HTTP {exc.code}")
        except Exception as exc:
            self.respond_error(502, str(exc))

    def handle_websearch(self, query):
        params = parse_qs(query)
        search_query = (params.get("q", [""])[0] or "").strip()
        if not search_query:
            self.respond_error(400, "Missing q")
            return

        google_url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": search_query})
        rss_url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": search_query,
            "hl": "zh-CN",
            "gl": "US",
            "ceid": "US:zh-Hans",
        })

        results = []
        try:
            req = urllib.request.Request(
                rss_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/rss+xml, application/xml;q=0.9,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                xml_payload = response.read()
            root = ET.fromstring(xml_payload)
            for item in root.findall("./channel/item")[:8]:
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                source = item.findtext("source") or ""
                published = item.findtext("pubDate") or ""
                if title and link:
                    results.append({
                        "title": title,
                        "url": link,
                        "source": source,
                        "published": published,
                    })
        except Exception:
            results = []

        payload = {
            "code": 0,
            "query": search_query,
            "googleUrl": google_url,
            "results": results,
        }
        self.respond_json(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def handle_analyze(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            item = payload.get("item") or {}
            analysis = analyze_news(item)
            self.respond_json(json.dumps({"code": 0, "analysis": analysis}, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:
            self.respond_error(400, str(exc))

    def handle_ai_config_get(self):
        self.respond_json(json.dumps({"code": 0, "config": config_status()}, ensure_ascii=False).encode("utf-8"))

    def handle_ai_config_save(self):
        try:
            payload = self._read_json_body()
            if payload.get("keepExisting") and not str(payload.get("configText") or payload.get("apiKey") or "").strip():
                existing = resolve_llm_config()
                requested_provider = str(payload.get("provider") or existing["provider"]).strip().lower()
                if existing["apiKey"] and requested_provider == existing["provider"]:
                    payload["apiKey"] = existing["apiKey"]
            config = parse_config_payload(payload)
            ok, message = validate_llm_config(config)
            if not ok:
                self.respond_error(400, message)
                return
            save_llm_config(config)
            self.respond_json(json.dumps({
                "code": 0,
                "message": message,
                "config": config_status(),
            }, ensure_ascii=False).encode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond_error(400, str(exc))
        except Exception as exc:
            self.respond_error(500, f"保存 AI 配置失败：{type(exc).__name__}")

    def handle_ai_config_clear(self):
        try:
            clear_llm_config()
            self.respond_json(json.dumps({"code": 0, "config": config_status()}, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:
            self.respond_error(500, f"清除 AI 配置失败：{type(exc).__name__}")

    def handle_chat(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            item = payload.get("item") or {}
            question = payload.get("question") or ""
            history = payload.get("history") or []
            analysis = payload.get("analysis") or None
            if not question.strip():
                self.respond_error(400, "Missing question")
                return
            
            # 判断是否为全局性/通用问题，或者无具体新闻item
            is_general = False
            # 如果是内部推荐追问的请求，则绝非全局用户查询
            if payload.get("mode") == "suggestions":
                is_general = False
            elif not item or not item.get("title"):
                is_general = True
            else:
                q_lower = question.lower()
                general_keywords_pairs = [
                    ("今天", "新闻"), ("今日", "新闻"), ("今天", "快讯"), ("今日", "快讯"),
                    ("今天", "重点"), ("今日", "重点"), ("今天", "热点"), ("今日", "热点"),
                    ("ai", "新闻"), ("人工智能", "新闻"), ("ai", "重点"), ("人工智能", "重点"),
                    ("ai", "快讯"), ("人工智能", "快讯"), ("总结", "新闻"), ("总结", "ai"),
                    ("市场", "总结"), ("市场", "新闻"), ("大盘", "重点"),
                    ("这", "重点"), ("新闻", "重点")
                ]
                if any(k1 in q_lower and k2 in q_lower for k1, k2 in general_keywords_pairs):
                    is_general = True
                elif q_lower in ["今天 ai 新闻有什么重点？", "今天 ai 新闻有什么重点", "今天ai新闻有什么重点？", "今天ai新闻有什么重点"]:
                    is_general = True

            if is_general:
                # 调用 Agent Pipeline 汇总分析
                ticker = item.get("symbol") or item.get("code") or None
                final_answer = ai_chat_pipeline(question, ticker=ticker, item=item)
                active_provider = resolve_llm_config()["provider"]
                result = {
                    "answer": final_answer,
                    "apiNote": f"分析来自 GVIW Agent 智能流水线 ({active_provider})"
                }
            else:
                # 针对特定单条新闻的深入追问
                result = chat_about_news(item, question, history=history, analysis=analysis)
                
            self.respond_json(json.dumps({"code": 0, "result": result}, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:
            self.respond_error(400, str(exc))

    # --- Watchlist endpoints ---

    def _require_user(self):
        """Return current user dict or respond 401 and return None."""
        user = self._get_session_user()
        if not user:
            user, token = self._auto_admin_session()
        return user

    def handle_watchlist_get(self):
        user = self._require_user()
        if not user:
            self.respond_error(401, "Not authenticated")
            return
        items = AUTH_DB.watchlist_get(user["id"])
        self.respond_json(json.dumps({"code": 0, "items": items}, ensure_ascii=False).encode("utf-8"))

    def handle_watchlist_add(self):
        user = self._require_user()
        if not user:
            self.respond_error(401, "Not authenticated")
            return
        try:
            data = self._read_json_body()
            row = AUTH_DB.watchlist_upsert(
                user["id"],
                data.get("symbol", "").strip().upper(),
                data.get("name", ""),
                data.get("exchange", ""),
                data.get("lastPrice"),
                data.get("changePct"),
            )
            self.respond_json(json.dumps({"code": 0, "item": row}, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:
            self.respond_error(400, str(exc))

    def handle_watchlist_remove(self, query):
        user = self._require_user()
        if not user:
            self.respond_error(401, "Not authenticated")
            return
        params = parse_qs(query)
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if not symbol:
            self.respond_error(400, "Missing symbol")
            return
        deleted = AUTH_DB.watchlist_remove(user["id"], symbol)
        self.respond_json(json.dumps({"code": 0, "deleted": deleted}).encode("utf-8"))

    # --- Auth endpoints ---

    def handle_auth_session(self):
        """Return current session user info (used on page reload)."""
        user = self._get_session_user()
        if not user:
            # Auto-create admin session
            user, token = self._auto_admin_session()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={AUTH_COOKIE_MAX_AGE}")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        payload_data = {"code": 0, "user": {"id": user["id"], "username": user["username"]}}
        body = json.dumps(payload_data).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_auth_login(self):
        try:
            data = self._read_json_body()
            username = data.get("username", "").strip()
            password = data.get("password", "")
            if not username or not password:
                self.respond_error(400, "Username and password required")
                return
            user = AUTH_DB.authenticate(username, password)
            if not user:
                self.respond_error(401, "Invalid credentials")
                return
            token = AUTH_DB.create_session(user["id"])
            payload_data = {"code": 0, "token": token, "user": {"id": user["id"], "username": user["username"]}}
            body = json.dumps(payload_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={AUTH_COOKIE_MAX_AGE}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.respond_error(500, str(exc))

    def handle_auth_register(self):
        try:
            data = self._read_json_body()
            username = data.get("username", "").strip()
            password = data.get("password", "")
            if not username or not password:
                self.respond_error(400, "Username and password required")
                return
            if len(username) < 3 or len(password) < 4:
                self.respond_error(400, "Username min 3 chars, password min 4 chars")
                return
            user = AUTH_DB.create_user(username, password)
            token = AUTH_DB.create_session(user["id"])
            payload_data = {"code": 0, "token": token, "user": {"id": user["id"], "username": user["username"]}}
            body = json.dumps(payload_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={AUTH_COOKIE_MAX_AGE}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except ValueError as exc:
            self.respond_error(409, str(exc))
        except Exception as exc:
            self.respond_error(500, str(exc))

    def handle_auth_logout(self):
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            part = part.strip()
            if part.startswith(f"{AUTH_COOKIE_NAME}="):
                token = part[len(AUTH_COOKIE_NAME) + 1:]
                AUTH_DB.delete_session(token)
                break
        body = json.dumps({"code": 0, "message": "logged out"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}=; Path=/; Max-Age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_json(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def respond_error(self, status, message):
        payload = json.dumps({"code": status, "message": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    # Ensure admin user exists (idempotent, no database migration)
    AUTH_DB.cleanup_expired_sessions()
    created, _ = AUTH_DB.ensure_admin("12345")
    if created:
        print("[AUTH] Default admin user created")

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving 7x24 Market Wire at http://127.0.0.1:{PORT}")
    print(f"Stock analysis proxy: http://127.0.0.1:{PORT}/stock-analysis")
    print(f"  (requires stock-analysis FastAPI backend on {STOCK_BACKEND_URL})")
    print("CLI examples:")
    print("  python cli.py list --source zh")
    print("  python cli.py analyze 1 --source zh")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
