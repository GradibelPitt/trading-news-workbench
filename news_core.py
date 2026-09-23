from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
import json
import os
import urllib.error
import urllib.request

from ai_config import chat_completions_url, resolve_llm_config


SOURCES = {
    "zh": {
        "url": "https://news.futunn.com/news-site-api/main/get-flash-list",
        "referer": "https://news.futunn.com/main",
        "host": "news.futunn.com",
        "lang": "zh-cn",
    },
    "en": {
        "url": "https://news.moomoo.com/news-site-api/main/get-flash-list",
        "referer": "https://www.moomoo.com/news/main",
        "host": "www.moomoo.com",
        "lang": "en",
    },
}

ROOT = Path(__file__).resolve().parent
API_ENV_PATH = ROOT / "api" / ".env.local"


def source_config(source_key):
    return SOURCES.get(source_key, SOURCES["zh"])


def fetch_flash(source="zh", page_size=20, seq_mark="", timeout=20):
    source = source_config(source)
    api_url = f"{source['url']}?{urlencode({'pageSize': page_size, 'seqMark': seq_mark})}"
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": source["referer"],
            "x-news-nuxt-host": source["host"],
            "x-news-nuxt-country-code": "US",
            "x-news-site-lang": source["lang"],
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_news(payload):
    data = payload.get("data", {}).get("data", {})
    return data.get("news") or []


def fetch_news_page(source="zh", page_size=20, seq_mark="", timeout=20):
    payload = fetch_flash(source=source, page_size=page_size, seq_mark=seq_mark, timeout=timeout)
    return payload, extract_news(payload)


def clean_text(item):
    return (item.get("title") or item.get("content") or "").strip()


def local_time(seconds):
    try:
        return datetime.fromtimestamp(int(seconds)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def analyze_news(item, use_ai=True):
    if use_ai:
        try:
            return analyze_news_with_deepseek(item)
        except Exception as exc:
            fallback = analyze_news_local(item)
            fallback["apiNote"] = f"AI API 调用失败，已回退到本地规则分析。错误类型：{type(exc).__name__}。"
            return fallback

    return analyze_news_local(item)


def analyze_news_local(item):
    text = " ".join(str(item.get(key) or "") for key in ("title", "content", "source"))
    lower = text.lower()
    quotes = item.get("quote") or []
    sectors = infer_sectors(lower)
    stocks = infer_stocks(lower, quotes)
    sentiment = infer_sentiment(lower)

    if not sectors:
        sectors = [{"name": "宏观 / 综合新闻", "impact": "当前文本未出现明确行业关键词，先按宏观事件处理。"}]

    context = build_context(lower, sectors)
    watch = build_watch_list(lower, sectors, stocks)

    return {
        "sentiment": sentiment["label"],
        "summary": sentiment["summary"],
        "sectors": sectors,
        "stocks": stocks,
        "context": context,
        "watch": watch,
        "apiNote": "当前使用本地规则分析。DeepSeek 可用时会优先调用 API；网页和 CLI 共用 news_core.py 的同一套输出结构。",
    }


def build_skills_context():
    """Return a concise summary of available trading analysis frameworks.

    This context is injected into the system prompt so the AI can apply
    these professional methodologies and display relevant parameters in output.
    """
    return """
## 可用分析框架（必须根据新闻类型选用并在输出中显式展示参数）

选择 2-4 个最相关的框架，在分析中显式写出框架名称和关键指标：

### 1. 板块轮动 (Sector Rotation)
展示格式示例：
  「📍 板块轮动：当前处于[早/中/晚周期/收缩期]，利率[上行/下行/高位/低位]，该新闻利好[板块A, 板块B]，利空[板块C]」

### 2. 事件驱动 (Event-Driven)
展示格式示例：
  「📌 事件驱动：事件类型=[并购/监管/重组/...]，影响周期=[短期/中期/长期]，确定性=[高/中/低]」

### 3. 情绪-现实缺口 (Sentiment-Reality Gap)
展示格式示例：
  「🎭 情绪缺口：市场反应=[过度/合理/不足]，性质=[临时性/周期性/结构性/叙事驱动]，基本面偏离度=[显著/轻微/无]」

### 4. 科技估值 (Tech Hype vs Fundamentals)
展示格式示例：
  「💻 估值诊断：[标的] P/S=[X]倍 vs 行业=[Y]倍，收入CAGR=[Z]%，FCF yield=[W]%，结论=[估值透支/合理/低估]」

### 5. 量化因子 (Quant Factor)
展示格式示例：
  「📊 因子暴露：受益因子=[价值/动量/质量/低波/规模/成长]，受损因子=[...]，当前宏观因子择时偏向=[...]」

### 6. 价值发现 (Value Screening)
展示格式示例：
  「💰 价值筛查：[标的] PE=[X] vs 行业5年均值=[Y]，D/E=[Z] vs 行业中位数=[W]，FCF yield=[V]%，ROIC=[U]%」

### 7. 内幕交易信号 (Insider Trading)
展示格式示例：
  「🔍 内幕信号：近90天内部人买入=[N]笔，集群=[是/否]，信号强度=[强/中/弱]」

### 8. 风险收益 (Risk-Adjusted)
展示格式示例：
  「⚖️ 风险指标：Beta=[X]，波动率=[Y]%，最大回撤=[Z]%，夏普比率=[W]」

### 9. 财报质量 (Financial Quality)
展示格式示例：
  「📋 财报质量：盈利质量=[高/中/低]，有机增长占比=[X]%，FCF转化率=[Y]%，应收账款/营收=[Z]%」

## 输出规则
- 每个分析区块开头用 emoji + 框架名标注来源方法论
- 关键参数用"="列出具体数值或评估结论
- 没有任何实际数据时标注"[需实时数据验证]"，不得编造数字
- 非科技类新闻不要用科技估值框架，非事件类不要用事件驱动，选相关的即可
"""


def analyze_news_with_deepseek(item):
    config = resolve_llm_config()
    api_key = config["apiKey"]
    base_url = config["baseUrl"]
    model = config["model"]
    if config["requiresKey"] and not api_key:
        raise RuntimeError("Missing LLM_API_KEY")

    prompt_payload = {
        "title": item.get("title") or "",
        "content": item.get("content") or "",
        "source": item.get("source") or item.get("sourceId") or "",
        "time": local_time(item.get("time")),
        "detailUrl": item.get("detailUrl") or "",
        "quotes": [
            {
                "code": q.get("code"),
                "name": q.get("name"),
                "price": q.get("price"),
                "changeRatio": q.get("changeRatio"),
                "pmahChangeRatio": q.get("pmahChangeRatio"),
            }
            for q in (item.get("quote") or [])
        ],
    }
    req_body = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个面向交易研究的中文新闻分析助手。"
                    + build_skills_context() +
                    "只输出 JSON，不要 markdown。"
                    "判断新闻对领域、板块、个股的利好/利空/中性影响，并补充背景解释。"
                    "不要给投资建议，不要编造未提供的事实。"
                    "JSON 必须包含 sentiment, summary, sectors, stocks, context, watch, apiNote。"
                    "sectors 是数组，每项含 name 和 impact。stocks 是数组，每项含 code, name, impact。watch 是字符串数组。"
                    "重要：在 summary、context 和 impact 字段中，用「📍板块轮动」「📌事件驱动」「🎭情绪缺口」「💻估值诊断」「📊因子暴露」「💰价值筛查」「⚖️风险指标」等标签显式标注所用框架及参数，让分析显得专业。没有实际数据时标注[需实时数据验证]。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(prompt_payload, ensure_ascii=False),
            },
        ],
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        chat_completions_url(base_url),
        data=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))

    content = payload["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return normalize_analysis(parsed)


def chat_about_news(item, question, history=None, analysis=None, use_ai=True):
    """Ask a follow-up question about a news item. Returns {answer, apiNote}."""
    if use_ai:
        try:
            return chat_about_news_with_deepseek(item, question, history=history, analysis=analysis)
        except Exception as exc:
            fallback = chat_about_news_local(item, question, analysis=analysis)
            fallback["apiNote"] = f"AI API 调用失败，已回退到本地规则回答。错误类型：{type(exc).__name__}。"
            return fallback

    return chat_about_news_local(item, question, analysis=analysis)


def chat_about_news_with_deepseek(item, question, history=None, analysis=None):
    config = resolve_llm_config()
    api_key = config["apiKey"]
    base_url = config["baseUrl"]
    model = config["model"]
    if config["requiresKey"] and not api_key:
        raise RuntimeError("Missing LLM_API_KEY")

    news_context = {
        "title": item.get("title") or "",
        "content": item.get("content") or "",
        "source": item.get("source") or item.get("sourceId") or "",
        "time": local_time(item.get("time")),
        "detailUrl": item.get("detailUrl") or "",
        "quotes": [
            {"code": q.get("code"), "name": q.get("name"),
             "price": q.get("price"), "changeRatio": q.get("changeRatio")}
            for q in (item.get("quote") or [])
        ],
    }

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个面向交易研究的中文新闻分析助手。用户正在针对一条特定新闻进行追问。"
                + build_skills_context() +
                "只输出 JSON，不要 markdown。"
                "基于新闻内容和已有分析，简洁、有信息量地回答用户的问题。"
                "不要给投资建议，不要编造未提供的事实。"
                "如果问题超出新闻范围，诚实说明。"
                "重要：回答中要用「📍板块轮动」「📌事件驱动」「🎭情绪缺口」「💻估值诊断」「📊因子暴露」「💰价值筛查」「⚖️风险指标」等标签显式标注所用框架及参数，让回答显得专业。没有实际数据时标注[需实时数据验证]。"
                "JSON 必须包含 answer, apiNote 两个字段。answer 是回答内容，apiNote 是简短提示（如分析来源或限制说明）。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "newsItem": news_context,
                "previousAnalysis": analysis,
                "conversationHistory": history or [],
                "question": question,
            }, ensure_ascii=False),
        },
    ]

    req_body = {
        "model": model,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        chat_completions_url(base_url),
        data=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))

    content = payload["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return {
        "answer": str(parsed.get("answer") or "").strip() or "API 未返回有效回答。",
        "apiNote": str(parsed.get("apiNote") or f"追问分析来自 {config['provider']} API。"),
    }


def chat_about_news_local(item, question, analysis=None):
    """Local rule-based Q&A about a news item."""
    text = " ".join(str(item.get(key) or "") for key in ("title", "content", "source"))
    lower = text.lower()
    q_lower = question.lower()

    # Detect question type and give relevant answer
    if any(kw in q_lower for kw in ["利好", "受益", "利多", "正面"]):
        sectors = infer_sectors(lower)
        if sectors:
            names = "、".join(s["name"] for s in sectors)
            answer = f"根据本地规则分析，这条新闻可能利好以下板块：{names}。建议结合实时盘面和相关标的成交量进一步验证。"
        else:
            answer = "当前文本未出现明确行业关键词，暂无法从本地规则判断具体利好方向。建议使用 AI 模式获取更深入的分析。"
    elif any(kw in q_lower for kw in ["利空", "不利", "负面", "下跌"]):
        answer = "本地规则分析检测到文本中可能包含风险或限制信号。若涉及地缘冲突、出口限制或监管收紧，可能对相关板块形成短期利空或提高波动率。建议查看 AI 分析获取更完整的评估。"
    elif any(kw in q_lower for kw in ["背景", "原因", "为什么", "what", "why"]):
        answer = f"这条新闻的背景涉及：{clean_text(item)}。如需更详细的背景解释，建议使用 AI 分析模式。"
    elif any(kw in q_lower for kw in ["验证", "关注", "后续", "信号"]):
        answer = "建议关注以下验证信号：1) 确认消息来源和是否有官方声明；2) 观察相关标的盘前/盘后价格和成交量变化；3) 跟踪期权隐含波动率是否出现异常；4) 等待更多细节披露后再做判断。"
    elif any(kw in q_lower for kw in ["盘前", "盘后", "美股", "开盘"]):
        answer = "关于盘前/盘后影响，本地规则无法提供实时价格预测。建议关注相关标的在盘前/盘后交易中的价格和成交量变化，以及是否有对应新闻的进一步发酵。"
    else:
        answer = f"基于本地规则，针对「{question}」的回答较为有限。本地模式主要支持板块利好/利空判断、背景解释和验证信号类问题。建议使用 AI 模式（不加 --local）获取更全面的分析。"

    return {
        "answer": answer,
        "apiNote": "当前使用本地规则分析。DeepSeek 可用时会优先调用 API。",
    }


def load_api_config(path=API_ENV_PATH):
    config = {}
    if not path.exists():
        return config
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def normalize_analysis(data):
    def text(value, default=""):
        result = str(value if value is not None else default).strip()
        return result or default

    sectors = data.get("sectors") if isinstance(data.get("sectors"), list) else []
    stocks = data.get("stocks") if isinstance(data.get("stocks"), list) else []
    watch = data.get("watch") if isinstance(data.get("watch"), list) else []

    normalized = {
        "sentiment": text(data.get("sentiment"), "中性 / 待确认"),
        "summary": text(data.get("summary"), "API 未返回摘要。"),
        "sectors": [
            {"name": text(row.get("name"), "未分类"), "impact": text(row.get("impact"), "待确认")}
            for row in sectors
            if isinstance(row, dict)
        ],
        "stocks": [
            {"code": text(row.get("code"), "UNKNOWN"), "name": text(row.get("name")), "impact": text(row.get("impact"), "待确认")}
            for row in stocks
            if isinstance(row, dict)
        ],
        "context": text(data.get("context"), "API 未返回背景解释。"),
        "watch": [text(row) for row in watch if text(row)],
        "apiNote": text(data.get("apiNote"), "分析来自 DeepSeek API；API key 仅在服务端读取。"),
    }
    if not normalized["sectors"]:
        normalized["sectors"] = [{"name": "宏观 / 综合新闻", "impact": "API 未返回明确板块，暂按综合事件处理。"}]
    if not normalized["watch"]:
        normalized["watch"] = ["关注后续官方确认、相关资产价格和成交量变化。"]
    return normalized


def infer_sectors(lower):
    rules = [
        ("AI / 软件", ["ai", "artificial intelligence", "anthropic", "palantir", "software"], "可能影响 AI 应用、数据分析软件和相关云服务估值。"),
        ("半导体", ["chip", "semiconductor", "nvidia", "micron", "export limits"], "若涉及出口限制或算力需求，通常会影响芯片链、设备和云算力预期。"),
        ("能源 / 航运", ["hormuz", "strait", "tanker", "oil", "ship", "vessel"], "霍尔木兹、油轮和航运风险会影响油价、航运、保险和通胀预期。"),
        ("地缘政治 / 国防", ["iran", "israel", "missile", "military", "g7", "war"], "地缘冲突会抬升避险情绪，并影响军工、能源和全球风险偏好。"),
        ("利率 / 宏观", ["fed", "rate", "cme", "tariff", "trade deal", "gdp", "inflation"], "利率和贸易政策变化会影响指数估值、美元和周期板块。"),
        ("医疗健康", ["clinical", "patient", "ebola", "fda", "drug"], "临床、公共卫生或监管进展会直接影响医药和生物科技风险溢价。"),
        ("加密资产", ["bitcoin", "crypto", "coinglass", "liquidation"], "加密清算和价格波动可能传导到交易平台、矿企和高风险偏好资产。"),
    ]
    sectors = []
    for name, keys, impact in rules:
        if any(key in lower for key in keys):
            sectors.append({"name": name, "impact": impact})
    return sectors[:4]


def infer_stocks(lower, quotes):
    stocks = []
    for quote in quotes:
        code = quote.get("code") or "UNKNOWN"
        name = quote.get("name") or ""
        impact = "新闻提及该标的，需结合正文判断直接或间接影响。"
        if "over palantir" in lower and "pltr" in code.lower():
            impact = "若消息属实，项目落选倾向利空，重点看订单预期和管理层回应。"
        elif "positive data" in lower or "clinical" in lower:
            impact = "若公司相关，临床数据正面倾向利好，但仍需看样本规模、终点和监管路径。"
        elif any(word in lower for word in ["export limit", "concern", "not expect", "attack", "blocked"]):
            impact = "事件包含限制、冲突或不确定性关键词，短线偏利空或提高波动率。"
        elif any(word in lower for word in ["deal", "agreement", "reopen", "approved", "positive"]):
            impact = "事件包含协议、恢复或正面进展关键词，可能降低风险溢价或改善预期。"
        stocks.append({"code": code, "name": name, "impact": impact})
    return stocks


def infer_sentiment(lower):
    bearish = ["blocked", "attack", "concern", "risk", "war", "limit", "not expect", "jeopardize", "down", "liquidation"]
    bullish = ["positive", "approved", "deal", "agreement", "reopen", "breakthrough", "growth", "selected"]
    bear_score = sum(1 for word in bearish if word in lower)
    bull_score = sum(1 for word in bullish if word in lower)

    if bear_score > bull_score:
        return {"label": "偏利空 / 提升波动", "summary": "文本中的风险、限制或冲突信号更强，可能压制相关资产风险偏好。"}
    if bull_score > bear_score:
        return {"label": "偏利好 / 降低不确定性", "summary": "文本中的协议、正面进展或恢复信号更强，可能改善相关资产预期。"}
    return {"label": "中性 / 待确认", "summary": "当前文本更像事实更新，方向性取决于后续细节和市场定价。"}


def build_context(lower, sectors):
    names = "、".join(sector["name"] for sector in sectors)
    if "hormuz" in lower:
        return "霍尔木兹海峡是全球能源运输关键通道，任何封锁、收费或通行受限消息都会被市场用于重估原油供应风险、航运成本和通胀路径。"
    if "fed" in lower or "rate" in lower:
        return "利率预期直接影响估值折现率。维持高利率通常压制久期较长的成长股，降息概率上升则通常改善风险资产情绪。"
    if "anthropic" in lower or "ai" in lower:
        return "AI 相关监管、出口和订单消息会影响算力需求、软件公司收入可见度，以及市场对 AI 资本开支持续性的判断。"
    return f"这条新闻目前主要落在 {names}。实际交易影响要看事件是否可量化、是否改变盈利预期，以及是否已经被价格提前反映。"


def build_watch_list(lower, sectors, stocks):
    watch = [
        "确认消息来源和是否有后续官方声明。",
        "观察相关标的盘前/盘后价格、成交量和期权隐含波动率。",
    ]
    if stocks:
        watch.append("把新闻影响拆成收入、成本、监管、估值和情绪五类，再对应到具体股票。")
    if any(sector["name"] == "能源 / 航运" for sector in sectors):
        watch.append("同步观察原油、油轮运价、航运保险和能源股表现。")
    if any(sector["name"] == "AI / 软件" for sector in sectors):
        watch.append("跟踪订单归属、出口限制范围和云算力需求变化。")
    return watch


def analysis_to_markdown(item, analysis, index=None):
    title = clean_text(item)
    prefix = f"新闻 {index}: " if index is not None else ""
    lines = [
        f"{prefix}{title}",
        f"时间: {local_time(item.get('time'))}",
        f"链接: {item.get('detailUrl') or ''}",
        "",
        f"影响判断: {analysis['sentiment']}",
        analysis["summary"],
        "",
        "相关领域 / 板块:",
    ]
    lines.extend(f"- {row['name']}: {row['impact']}" for row in analysis["sectors"])
    lines.append("")
    lines.append("相关股票:")
    if analysis["stocks"]:
        lines.extend(f"- {row['code']} {row['name']}: {row['impact']}" for row in analysis["stocks"])
    else:
        lines.append("- 未返回明确相关股票。")
    lines.append("")
    lines.append("背景解释:")
    lines.append(analysis["context"])
    lines.append("")
    lines.append("后续关注:")
    lines.extend(f"- {row}" for row in analysis["watch"])
    return "\n".join(lines)
