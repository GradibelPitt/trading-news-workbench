import json
from pathlib import Path
import urllib.request
import os
import sys

# 动态定位项目根目录
CURRENT_DIR = Path(__file__).resolve().parent
if CURRENT_DIR.name == "agents" and CURRENT_DIR.parent.name == "ai_chat":
    PROJECT_ROOT = CURRENT_DIR.parent.parent.parent
else:
    PROJECT_ROOT = CURRENT_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 动态定位配置文件路径
AGENT_PROFILE_PATH = CURRENT_DIR / "agent_profiles.json"
if not AGENT_PROFILE_PATH.exists():
    AGENT_PROFILE_PATH = Path("agents/agent_profiles.json")

# 引入已注册的爬虫桥接
try:
    from dataflows.crawler_bridge import build_agent_context
except ImportError:
    sys.path.append(str(PROJECT_ROOT))
    from dataflows.crawler_bridge import build_agent_context


def load_agent_profiles():
    with AGENT_PROFILE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_api_config():
    path = PROJECT_ROOT / "api" / ".env.local"
    config = {}
    if not path.exists():
        return config
    try:
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"').strip("'")
    except Exception as e:
        print(f"Error loading API config: {e}")
    return config


def call_llm(prompt: str) -> str:
    cfg = load_api_config()
    api_key = cfg.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    base_url = (cfg.get("DEEPSEEK_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = cfg.get("DEEPSEEK_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
    
    if not api_key:
        return "Error: Missing DEEPSEEK_API_KEY in api/.env.local"

    req_body = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }
    
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            return resp_data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error calling LLM: {str(e)}"


def build_agent_prompt(agent_name: str, user_message: str, context: dict):
    profiles = load_agent_profiles()
    global_style = profiles["global_style"]
    agent = profiles["agents"][agent_name]

    return f"""
你是：{agent["role"]}

目标：
{agent["goal"]}

全局风格：
{json.dumps(global_style, ensure_ascii=False, indent=2)}

你的输出格式必须符合：
{json.dumps(agent["output_schema"], ensure_ascii=False, indent=2)}

你的风格要求：
{json.dumps(agent.get("style", {}), ensure_ascii=False, indent=2)}

用户问题：
{user_message}

可用数据上下文：
{json.dumps(context, ensure_ascii=False, indent=2)}

重要规则：
1. 只能基于“可用数据上下文”回答。
2. 如果数据不足，明确写 data_missing。
3. 不要编造新闻、价格、来源、指标。
4. 输出必须尽量结构化。
"""


def ai_chat_pipeline(user_message, active_page=None, ticker=None, item=None):
    # 1. router 先判断 intent
    router_prompt = build_agent_prompt("router_agent", user_message, {})
    router_res = call_llm(router_prompt)
    
    intent = "market_analysis"
    try:
        cleaned = router_res.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()
        router_data = json.loads(cleaned)
        intent = router_data.get("intent", "market_analysis")
        if router_data.get("ticker"):
            ticker = router_data.get("ticker")
    except Exception as e:
        print(f"Router parsing failed, fallback to market_analysis: {e}")
    
    # 2. 根据 intent 接爬虫数据
    context = build_agent_context(intent=intent, ticker=ticker)
    if item:
        context["selected_item"] = item

    # 3. 调技术 / 新闻 / 风险 agent
    news_prompt = build_agent_prompt("news_agent", user_message, context)
    risk_prompt = build_agent_prompt("risk_agent", user_message, context)

    # 4. 调 LLM
    news_report = call_llm(news_prompt)
    risk_report = call_llm(risk_prompt)

    # 5. 最终整合
    final_context = {
        "news_report": news_report,
        "risk_report": risk_report,
        "original_context": context
    }

    final_prompt = build_agent_prompt("answer_composer", user_message, final_context)
    final_answer = call_llm(final_prompt)

    # 剥离可能存在的 Markdown 代码块标记 (如 ```json)
    cleaned_answer = final_answer.strip()
    if cleaned_answer.startswith("```"):
        lines = cleaned_answer.splitlines()
        if len(lines) > 2:
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                cleaned_answer = "\n".join(lines[1:-1]).strip()
                return cleaned_answer

    return final_answer
