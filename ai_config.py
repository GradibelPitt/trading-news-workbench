"""Server-side LLM provider configuration for AI Chat.

Secrets are kept in api/.env.local and are never returned to the browser.
The supported providers expose an OpenAI-compatible chat/completions endpoint.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import json
import os
import re
import tempfile
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent
API_ENV_PATH = ROOT / "api" / ".env.local"

PROVIDERS = {
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "requiresKey": True,
        "accountUrl": "https://platform.deepseek.com/",
        "keyUrl": "https://platform.deepseek.com/api_keys",
        "authMode": "api_key",
        "priority": 1,
    },
    "qwen": {
        "id": "qwen",
        "name": "阿里云百炼 · Qwen",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.7-plus",
        "requiresKey": True,
        "accountUrl": "https://bailian.console.aliyun.com/",
        "keyUrl": "https://bailian.console.aliyun.com/?tab=model#/api-key",
        "authMode": "api_key",
        "priority": 2,
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI API",
        "baseUrl": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "requiresKey": True,
        "accountUrl": "https://platform.openai.com/",
        "keyUrl": "https://platform.openai.com/api-keys",
        "authMode": "api_key",
        "priority": 3,
    },
    "ollama": {
        "id": "ollama",
        "name": "Ollama（本机）",
        "baseUrl": "http://127.0.0.1:11434/v1",
        "model": "qwen3:8b",
        "requiresKey": False,
        "accountUrl": "https://ollama.com/download",
        "keyUrl": "",
        "authMode": "local",
        "priority": 4,
    },
    "custom": {
        "id": "custom",
        "name": "其他 OpenAI 兼容服务",
        "baseUrl": "",
        "model": "",
        "requiresKey": True,
        "accountUrl": "",
        "keyUrl": "",
        "authMode": "api_key",
        "priority": 5,
    },
}

MANAGED_KEYS = {
    "LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
    "DASHSCOPE_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
}


def load_env(path: Path = API_ENV_PATH) -> dict[str, str]:
    config: dict[str, str] = {}
    if not path.exists():
        return config
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def _infer_provider(values: dict[str, str], fallback: str = "deepseek") -> str:
    joined = " ".join(f"{key}={value}" for key, value in values.items()).lower()
    if "dashscope" in joined or "aliyuncs" in joined or "qwen" in joined:
        return "qwen"
    if "deepseek" in joined:
        return "deepseek"
    if "openai" in joined or "api.openai.com" in joined:
        return "openai"
    if "ollama" in joined or "127.0.0.1:11434" in joined or "localhost:11434" in joined:
        return "ollama"
    return fallback


def resolve_llm_config(path: Path = API_ENV_PATH) -> dict:
    cfg = load_env(path)
    env = os.environ
    provider = (cfg.get("LLM_PROVIDER") or env.get("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = _infer_provider({**cfg, **{k: env.get(k, "") for k in MANAGED_KEYS}})
    if provider not in PROVIDERS:
        provider = "custom"
    preset = PROVIDERS[provider]

    provider_keys = {
        "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
        "qwen": ("DASHSCOPE_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL"),
        "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"),
    }
    key_name, base_name, model_name = provider_keys.get(provider, ("", "", ""))
    api_key = cfg.get("LLM_API_KEY") or env.get("LLM_API_KEY")
    base_url = cfg.get("LLM_BASE_URL") or env.get("LLM_BASE_URL")
    model = cfg.get("LLM_MODEL") or env.get("LLM_MODEL")
    if key_name:
        api_key = api_key or cfg.get(key_name) or env.get(key_name)
        base_url = base_url or cfg.get(base_name) or env.get(base_name)
        model = model or cfg.get(model_name) or env.get(model_name)

    return {
        "provider": provider,
        "apiKey": (api_key or "").strip(),
        "baseUrl": (base_url or preset["baseUrl"]).strip().rstrip("/"),
        "model": (model or preset["model"]).strip(),
        "requiresKey": bool(preset["requiresKey"]),
    }


def provider_catalog() -> list[dict]:
    result = []
    for provider in sorted(PROVIDERS.values(), key=lambda item: item["priority"]):
        entry = dict(provider)
        entry["oauthSupported"] = False
        if entry["authMode"] == "api_key":
            entry["authNote"] = "该模型 API 使用服务端 API Key；平台网页账号登录不能直接授权给第三方本地应用。"
        else:
            entry["authNote"] = "本机服务，无需平台账号。"
        result.append(entry)
    return result


def config_status(path: Path = API_ENV_PATH) -> dict:
    config = resolve_llm_config(path)
    preset = PROVIDERS.get(config["provider"], PROVIDERS["custom"])
    has_key = bool(config["apiKey"])
    configured = bool(config["baseUrl"] and config["model"] and (has_key or not preset["requiresKey"]))
    return {
        "configured": configured,
        "provider": config["provider"],
        "providerName": preset["name"],
        "baseUrl": config["baseUrl"],
        "model": config["model"],
        "hasKey": has_key,
        "keyLength": len(config["apiKey"]) if has_key else 0,
    }


def _string_values_from_blob(blob: str) -> dict[str, str]:
    text = (blob or "").strip()
    if not text:
        return {}
    values: dict[str, str] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if isinstance(value, (str, int, float)):
                    values[str(key).upper()] = str(value).strip()
            return values
    except json.JSONDecodeError:
        pass

    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("export ").strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip().upper()] = value.strip().strip('"').strip("'")
    bearer = re.search(r"(?:Authorization\s*:\s*)?Bearer\s+([^\s'\"]+)", text, re.I)
    if bearer:
        values.setdefault("API_KEY", bearer.group(1))
    if not values and "\n" not in text and len(text) >= 8:
        values["API_KEY"] = text.strip().strip('"').strip("'")
    return values


def parse_config_payload(payload: dict) -> dict[str, str]:
    values = _string_values_from_blob(str(payload.get("configText") or payload.get("apiKey") or ""))
    aliases = {
        "API_KEY": "apiKey", "LLM_API_KEY": "apiKey", "DEEPSEEK_API_KEY": "apiKey",
        "DASHSCOPE_API_KEY": "apiKey", "OPENAI_API_KEY": "apiKey", "KEY": "apiKey",
        "BASE_URL": "baseUrl", "LLM_BASE_URL": "baseUrl", "DEEPSEEK_BASE_URL": "baseUrl",
        "QWEN_BASE_URL": "baseUrl", "OPENAI_BASE_URL": "baseUrl", "API_BASE": "baseUrl",
        "MODEL": "model", "LLM_MODEL": "model", "DEEPSEEK_MODEL": "model",
        "QWEN_MODEL": "model", "OPENAI_MODEL": "model",
        "PROVIDER": "provider", "LLM_PROVIDER": "provider",
    }
    parsed: dict[str, str] = {}
    for key, value in values.items():
        normalized = aliases.get(key.upper())
        if normalized and value:
            parsed[normalized] = value

    requested_provider = str(payload.get("provider") or parsed.get("provider") or "auto").strip().lower()
    if requested_provider == "auto":
        requested_provider = _infer_provider(values)
    if requested_provider not in PROVIDERS:
        requested_provider = "custom"
    preset = PROVIDERS[requested_provider]

    config = {
        "provider": requested_provider,
        "apiKey": str(payload.get("apiKey") or parsed.get("apiKey") or "").strip(),
        "baseUrl": str(payload.get("baseUrl") or parsed.get("baseUrl") or preset["baseUrl"]).strip().rstrip("/"),
        "model": str(payload.get("model") or parsed.get("model") or preset["model"]).strip(),
    }
    _validate_shape(config)
    return config


def _validate_shape(config: dict[str, str]) -> None:
    preset = PROVIDERS[config["provider"]]
    if preset["requiresKey"] and not config["apiKey"]:
        raise ValueError("没有解析到 API Key，请粘贴裸 Key、.env 片段或 JSON 配置。")
    if not config["baseUrl"] or not config["model"]:
        raise ValueError("Base URL 和模型名称不能为空。")
    if any("\n" in config[key] or "\r" in config[key] for key in ("apiKey", "baseUrl", "model")):
        raise ValueError("配置字段不能包含换行。")
    parsed = urlparse(config["baseUrl"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Base URL 必须是有效的 http(s) 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL 不能包含账号、密码、查询参数或片段。")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("远程模型服务必须使用 HTTPS。")
    if "{" in config["baseUrl"] or "}" in config["baseUrl"]:
        raise ValueError("请把 Base URL 中的 WorkspaceId 占位符替换成真实值。")


def chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def validate_llm_config(config: dict[str, str], timeout: int = 20) -> tuple[bool, str]:
    _validate_shape(config)
    body = {
        "model": config["model"],
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "temperature": 0,
        "max_tokens": 4,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config["apiKey"]:
        headers["Authorization"] = f"Bearer {config['apiKey']}"
    request = urllib.request.Request(
        chat_completions_url(config["baseUrl"]),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("choices"):
            return False, "服务已响应，但未返回兼容的 choices 数据。"
        return True, "连接和模型调用验证成功。"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:500]
        try:
            data = json.loads(raw)
            detail = data.get("error", {}).get("message") or data.get("message") or ""
        except (json.JSONDecodeError, AttributeError):
            detail = ""
        safe_detail = re.sub(r"sk-[A-Za-z0-9_-]+", "[已隐藏密钥]", str(detail))[:220]
        return False, f"供应商返回 HTTP {exc.code}" + (f"：{safe_detail}" if safe_detail else "。")
    except Exception as exc:
        return False, f"无法连接模型服务：{type(exc).__name__}。"


def save_llm_config(config: dict[str, str], path: Path = API_ENV_PATH) -> None:
    _validate_shape(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key not in MANAGED_KEYS:
                kept.append(line)
    if kept and kept[-1].strip():
        kept.append("")
    kept.extend([
        "# AI Chat provider configuration (managed by the local settings page)",
        f"LLM_PROVIDER={config['provider']}",
        f"LLM_API_KEY={config['apiKey']}",
        f"LLM_BASE_URL={config['baseUrl']}",
        f"LLM_MODEL={config['model']}",
    ])
    content = "\n".join(kept).rstrip() + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".env.local.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def clear_llm_config(path: Path = API_ENV_PATH) -> None:
    if not path.exists():
        return
    kept = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key not in MANAGED_KEYS and "managed by the local settings page" not in line:
            kept.append(line)
    content = "\n".join(kept).rstrip()
    if content:
        path.write_text(content + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    else:
        path.unlink(missing_ok=True)
