import argparse
import json
import sys
import urllib.request
import urllib.error

from news_core import (
    analysis_to_markdown,
    analyze_news,
    chat_about_news,
    clean_text,
    fetch_news_page,
    local_time,
)


# ---------------------------------------------------------------------------
# stock-analysis indicator button helpers (CLI-side button click simulation)
# ---------------------------------------------------------------------------

STOCK_API_DEFAULT_PORT = 8765  # main server default port

# All indicator catalog keys (must match INDICATOR_KEYS in app.py).
INDICATOR_CATALOG = {
    # -- 主图指标 (price pane) --
    "ma":           "MA 移动平均线 (MA5/10/20/60)",
    "ema":          "EMA 指数移动平均线 (EMA5/10/20/60/120/250)",
    "bbi":          "BBI 多空指数",
    "sar":          "SAR 停损点转向指标",
    "alligator":    "ALLIGATOR 鳄鱼线指标 (Jaw/Teeth/Lips)",
    "gmma":         "GMMA 顾比均线 (3/5/8/10/12/15/30/35/40/45/50/60)",
    "twap":         "TWAP 时间加权平均价",
    "vwma":         "VWMA 成交量加权移动平均",
    "wma":          "WMA 加权移动平均线",
    "tema":         "TEMA 三重指数移动平均线",
    "dema":         "DEMA 双重指数移动平均线",
    "hma":          "HMA 船体移动平均线",
    "lsma":         "LSMA 最小二乘移动平均线",
    "tsf":          "TSF 时间序列预测指标",
    "kc":           "KC 肯特纳通道 (Upper/Mid/Lower)",
    "kama":         "KAMA 适应性移动平均线",
    # -- 副图指标 (sub pane) --
    "macd":         "MACD 指数平滑异同移动平均线 (Hist/MACD/Signal)",
    "dma":          "DMA 平行线差指标",
    "dmi":          "DMI 趋向指标 (+DI/-DI/ADX)",
    "emv":          "EMV 简易波动指标",
    "atr":          "ATR 真实波幅",
    "fisher":       "FISHER 费雪变换 (Fisher/Signal)",
    "iv":           "IV 隐含波动率 (realized-vol proxy)",
    "iv_rank":      "IV_RANK 隐含波动率等级",
    "iv_percentile":"IV_PERCENTILE 隐含波动率百分位",
    "volat":        "VOLAT 历史波动率 (HV20)",
    "vmacd":        "VMACD 量指数平滑异同 (VMACD/Signal)",
    "trix":         "TRIX 三重指数平滑平均线 (TRIX/Signal)",
    "priceosc":     "PRICEOSC 价格摆动指标",
    "ddi":          "DDI 方向标准离差指数",
    "mi":           "MI 动量指标 (Mass Index)",
    "si":           "SI 摆动指标 (Swing Index)",
    "ao":           "AO 动量震荡指标 (Awesome Oscillator)",
    "tower":        "TOWER 宝塔线",
    "ewo":          "EWO 艾略特波浪震荡指标",
    "rvi":          "RVI 相对离散指标 (RVI/Signal)",
    "hlvol":        "HLVOL HL波动率",
    "rmi":          "RMI 相对动量指数",
    "smie":         "SMIE 随机动量指数 (SMIE/Signal)",
    "smieo":        "SMIEO 随机动量指数震荡",
    "aroon":        "AROON 阿隆指标 (Up/Down)",
    "chop":         "CHOP 波动指数 (Choppiness)",
    "hadiff":       "HADIFF 平均K线差 (Heikin-Ashi Diff)",
    "er":           "ER 效率比 (Efficiency Ratio)",
    "squeeze":      "SQUEEZE 挤牌指标",
    "fo":           "FO 预测震荡指标 (Forecast Oscillator)",
    "kdj":          "KDJ 随机指标 (K/D/J)",
    "mfi":          "MFI 资金流量指标",
    "obv":          "OBV 能量潮指标",
    "cci":          "CCI 顺势指标",
    "bop":          "BOP 均势指标 (Balance of Power)",
    "ultosc":       "ULTOSC 终极波动指标",
    "roc":          "ROC 变动率指标",
    "ppo":          "PPO 价格震荡百分比",
    "apo":          "APO 绝对价格振荡器",
    "cmo":          "CMO 钱德动量摆动指标",
    "natr":         "NATR 归一化波动幅度均值",
    "ad":           "AD 累积/派发线",
    "adosc":        "ADOSC 佳庆离散指标 (Chaikin Oscillator)",
}


def _stock_req(method, path, body=None, port=STOCK_API_DEFAULT_PORT):
    """Minimal HTTP request to the stock-analysis backend (via main server proxy)."""
    url = f"http://127.0.0.1:{port}/stock-analysis{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise SystemExit(f"stock-analysis API 返回 {e.code}: {body_text or e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(
            f"无法连接 stock-analysis 服务 (http://127.0.0.1:{port})\n"
            f"请确认主 server 已启动 (双击 start.bat): {e.reason}"
        )
    except OSError as e:
        raise SystemExit(f"连接失败: {e}")


def _stock_toggle(button, port):
    """POST /api/indicator/toggle and print the new state."""
    result = _stock_req("POST", "/api/indicator/toggle", {"button": button}, port=port)
    st = result.get("state", {})
    labels = {"volume": "Volume", "ma": "MA20/50", "events": "Events"}
    label = labels.get(button, button)
    if button == "reset":
        print(f"Reset 已触发 (seq={st.get('resetSeq', '?')})")
    else:
        print(f"{label}: {'ON' if st.get(button) else 'OFF'}")


# ---------------------------------------------------------------------------
# stock subcommand handlers
# ---------------------------------------------------------------------------

def cmd_stock_volume(args):
    """切换 Volume 显示"""
    _stock_toggle("volume", args.port)


def cmd_stock_ma(args):
    """切换 MA20/50 显示"""
    _stock_toggle("ma", args.port)


def cmd_stock_events(args):
    """切换 Events 显示"""
    _stock_toggle("events", args.port)


def cmd_stock_reset(args):
    """重置图表视图"""
    _stock_toggle("reset", args.port)


def cmd_stock_state(args):
    """查看当前指标按钮状态"""
    st = _stock_req("GET", "/api/indicator/state", port=args.port)
    print(f"Volume:  {'ON' if st.get('volume') else 'OFF'}")
    print(f"MA20/50: {'ON' if st.get('ma') else 'OFF'}")
    print(f"Events:  {'ON' if st.get('events') else 'OFF'}")
    print(f"Reset:   seq={st.get('resetSeq', '?')}")
    print(f"Indicator: toggleSeq={st.get('toggleSeq', 0)}  lastKey={st.get('toggledKey') or '-'}")


def cmd_stock_indicator(args):
    """切换指定的技术指标 (catalog key)"""
    key = (args.key or "").strip().lower()
    if key not in INDICATOR_CATALOG:
        print(f"未知指标 key: '{key}'")
        print(f"可用 key 列表请运行: python cli.py stock indicators")
        raise SystemExit(1)
    _stock_req("POST", "/api/indicator/toggle", {"button": "indicator", "key": key}, port=args.port)
    print(f"已触发切换: {key} ({INDICATOR_CATALOG[key]})")


def cmd_stock_active(args):
    """Query which indicators the user currently has checked in the browser."""
    st = _stock_req("GET", "/api/indicator/active", port=args.port)
    print(f"Volume:  {'ON' if st.get('volume') else 'OFF'}")
    print(f"MA20/50: {'ON' if st.get('ma') else 'OFF'}")
    print(f"Events:  {'ON' if st.get('events') else 'OFF'}")
    active_keys = st.get("activeKeys", [])
    if active_keys:
        print(f"Active ({st.get('count', len(active_keys))}): {', '.join(active_keys)}")
    else:
        print("Active: (none)")
    reported = st.get("lastReportedAt")
    if reported:
        print(f"Reported at: {reported}")


def cmd_stock_indicators(args):
    """列出所有可用的技术指标 key"""
    print("主图指标 (price pane):")
    price_keys = ["ma", "ema", "bbi", "sar", "alligator", "gmma", "twap", "vwma",
                  "wma", "tema", "dema", "hma", "lsma", "tsf", "kc", "kama"]
    for k in price_keys:
        print(f"  {k:<16} {INDICATOR_CATALOG.get(k, '')}")
    print()
    print("副图指标 (sub pane):")
    sub_keys = ["macd", "dma", "dmi", "emv", "atr", "fisher", "iv", "iv_rank",
                "iv_percentile", "volat", "vmacd", "trix", "priceosc", "ddi", "mi",
                "si", "ao", "tower", "ewo", "rvi", "hlvol", "rmi", "smie", "smieo",
                "aroon", "chop", "hadiff", "er", "squeeze", "fo", "kdj", "mfi",
                "obv", "cci", "bop", "ultosc", "roc", "ppo", "apo", "cmo", "natr",
                "ad", "adosc"]
    for k in sub_keys:
        print(f"  {k:<16} {INDICATOR_CATALOG.get(k, '')}")
    print()
    print(f"用法: python cli.py stock indicator <key>")
    print(f"用法: python cli.py stock volume|ma|events|reset|state")


def cmd_list(args):
    _, news = fetch_news_page(source=args.source, page_size=args.page_size)
    for idx, item in enumerate(news, start=1):
        stocks = ", ".join(q.get("code", "") for q in item.get("quote") or [] if q.get("code"))
        suffix = f" [{stocks}]" if stocks else ""
        print(f"{idx:>2}. {local_time(item.get('time'))} | {clean_text(item)}{suffix}")


def cmd_analyze(args):
    _, news = fetch_news_page(source=args.source, page_size=args.page_size)
    index = args.index_opt if args.index_opt is not None else args.index
    if index is None:
        raise SystemExit("请提供新闻序号，例如: python cli.py analyze 1 --source zh")
    if index < 1 or index > len(news):
        raise SystemExit(f"序号超出范围：{index}，当前只加载了 {len(news)} 条。")

    item = news[index - 1]
    analysis = analyze_news(item, use_ai=not args.local)
    if args.json:
        print(json.dumps({"item": item, "analysis": analysis}, ensure_ascii=False, indent=2))
    else:
        print(analysis_to_markdown(item, analysis, index=index))


def cmd_ask(args):
    _, news = fetch_news_page(source=args.source, page_size=args.page_size)
    index = args.index_opt if args.index_opt is not None else args.index
    if index is None:
        raise SystemExit("请提供新闻序号，例如: python cli.py ask 1 \"这条新闻主要利好哪些板块？\" --source zh")
    if index < 1 or index > len(news):
        raise SystemExit(f"序号超出范围：{index}，当前只加载了 {len(news)} 条。")

    item = news[index - 1]
    question = args.question
    if not question or not question.strip():
        raise SystemExit("请提供追问内容，例如: python cli.py ask 1 \"这条新闻主要利好哪些板块？\"")

    # Run initial analysis first to provide context
    analysis = analyze_news(item, use_ai=not args.local)
    result = chat_about_news(item, question, analysis=analysis, use_ai=not args.local)

    print(f"新闻: {clean_text(item)}")
    print(f"时间: {local_time(item.get('time'))}")
    print()
    print(f"追问: {question}")
    print("-" * 40)
    print(result["answer"])
    print()
    print(f"[{result['apiNote']}]")


def cmd_raw(args):
    payload, _ = fetch_news_page(source=args.source, page_size=args.page_size)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="7x24 Market Wire CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="列出当前快讯")
    list_parser.add_argument("--source", choices=["zh", "en"], default="zh")
    list_parser.add_argument("--page-size", type=int, default=20)
    list_parser.set_defaults(func=cmd_list)

    analyze_parser = sub.add_parser("analyze", help="按序号分析一条快讯")
    analyze_parser.add_argument("index", type=int, nargs="?", help="新闻序号，例如 1")
    analyze_parser.add_argument("--index", dest="index_opt", type=int, help="新闻序号，例如 --index 1")
    analyze_parser.add_argument("--source", choices=["zh", "en"], default="zh")
    analyze_parser.add_argument("--page-size", type=int, default=20)
    analyze_parser.add_argument("--json", action="store_true", help="输出 JSON，便于脚本继续处理")
    analyze_parser.add_argument("--local", action="store_true", help="跳过 AI API，只使用本地规则分析")
    analyze_parser.set_defaults(func=cmd_analyze)

    ask_parser = sub.add_parser("ask", help="对一条快讯进行追问")
    ask_parser.add_argument("index", type=int, nargs="?", help="新闻序号，例如 1")
    ask_parser.add_argument("question", nargs="?", help="追问内容，例如 \"这条新闻主要利好哪些板块？\"")
    ask_parser.add_argument("--index", dest="index_opt", type=int, help="新闻序号，例如 --index 1")
    ask_parser.add_argument("--source", choices=["zh", "en"], default="zh")
    ask_parser.add_argument("--page-size", type=int, default=20)
    ask_parser.add_argument("--local", action="store_true", help="跳过 AI API，只使用本地规则回答")
    ask_parser.set_defaults(func=cmd_ask)

    raw_parser = sub.add_parser("raw", help="输出原始接口 JSON")
    raw_parser.add_argument("--source", choices=["zh", "en"], default="zh")
    raw_parser.add_argument("--page-size", type=int, default=20)
    raw_parser.set_defaults(func=cmd_raw)

    # stock-analysis indicator button control
    stock_parser = sub.add_parser("stock", help="控制 stock-analysis 指标按钮 (Volume / MA20-50 / Events / Reset)")
    stock_parser.add_argument("--port", type=int, default=8765, help="主 server 端口 (默认 8765)")
    stock_sub = stock_parser.add_subparsers(dest="stock_command")

    vol_p = stock_sub.add_parser("volume", help="切换 Volume 显示")
    vol_p.set_defaults(func=cmd_stock_volume)

    ma_p = stock_sub.add_parser("ma", help="切换 MA20/50 显示")
    ma_p.set_defaults(func=cmd_stock_ma)

    events_p = stock_sub.add_parser("events", help="切换 Events 显示")
    events_p.set_defaults(func=cmd_stock_events)

    reset_p = stock_sub.add_parser("reset", help="重置图表视图 (2D fitContent / 3D resetView)")
    reset_p.set_defaults(func=cmd_stock_reset)

    state_p = stock_sub.add_parser("state", help="查看当前指标按钮状态")
    state_p.set_defaults(func=cmd_stock_state)

    ind_p = stock_sub.add_parser("indicator", help="切换指定技术指标，例如 macd / kdj / rsi")
    ind_p.add_argument("key", help="指标 key，运行 stock indicators 查看完整列表")
    ind_p.set_defaults(func=cmd_stock_indicator)

    inds_p = stock_sub.add_parser("indicators", help="列出所有可用的技术指标 key及说明")
    inds_p.set_defaults(func=cmd_stock_indicators)

    active_p = stock_sub.add_parser("active", help="查询当前用户浏览器中已勾选的技术指标")
    active_p.set_defaults(func=cmd_stock_active)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    # stock subcommand requires a second-level command
    if args.command == "stock" and getattr(args, "stock_command", None) is None:
        # Print stock sub-help
        for action in parser._actions:
            if action.dest == "command" and hasattr(action, "choices") and "stock" in action.choices:
                action.choices["stock"].print_help()
                return
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
