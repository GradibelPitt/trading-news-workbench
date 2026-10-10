<div align="center">

# Trading News Workbench

**English** · [简体中文](README.zh-CN.md)

**Market news, stock research, and watchlists in one local workspace.**

[Features](#features) · [Quick start](#quick-start) · [CLI](#cli) · [Project layout](#project-layout) · [License](#license)

[![License: MIT](https://img.shields.io/badge/License-MIT-2f6b55.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.x-3776ab.svg)](https://www.python.org/)

</div>

Trading News Workbench is a locally run workspace for following market news and researching stocks. It brings Chinese and English market flashes, academic and open-source project feeds, stock analysis, a screener, a market heatmap, option chains, and watchlists into one browser interface. A CLI provides access to several news workflows. News and market data come from external services, so availability depends on those sources.

## Features

| Area | What it does |
| --- | --- |
| News | Browse Chinese and English market flashes, academic updates, and open-source projects; analyze a flash or ask follow-up questions. |
| Stock analysis | Search for stocks, inspect candlestick charts and technical indicators, and generate rule-based trade plans. |
| Screener and market | Filter stocks by fundamental and technical criteria; explore a market snapshot in the heatmap. |
| Options | View expiration dates and option chains for a symbol, and inspect contract data. |
| Watchlists | Save stocks and open their analysis panels from a list. |
| CLI | List, analyze, and ask questions about market flashes; retrieve raw API data and control selected stock-chart indicators. |

The stock analysis, screener, heatmap, and options pages depend on a separate stock-analysis service. Analysis is for research support and is not investment advice; the options page does not place trades. Data may be delayed, missing, or unavailable when upstream services change.

## Quick start

**Requirements:** Python 3 and `pip`. A full first run needs network access to install the two supporting services' Python dependencies and retrieve external data.

```bash
git clone https://github.com/GradibelPitt/trading-news-workbench.git
cd trading-news-workbench
```

### Windows

Double-click `start.bat`, or run it from PowerShell:

```powershell
.\start.bat
```

The launcher starts the main server and attempts to start the stock-analysis and open-source crawler services. The main site normally opens at [http://127.0.0.1:8765](http://127.0.0.1:8765). If that port is busy, the launcher selects a free port from 8765–8775; use the address printed in the terminal.

### macOS

Double-click `mac_start.command`, or run:

```bash
./mac_start.command
```

On first launch, the script creates virtual environments and installs dependencies for the stock-analysis and crawler services. Press `Control-C` to stop, or run `./mac_stop.command`.

### Run only the main server

```bash
python server.py
```

This starts only the main server. Stock analysis, screening, options, and the heatmap also require the FastAPI backend in `pages/stock-analysis-page`; see its [setup guide](pages/stock-analysis-page/README.md). If that backend is not on `127.0.0.1:8000`, set `STOCK_BACKEND_URL` before starting the main server.

## Configuration and local data

- On first launch, the server creates the local authentication database at `data/auth.db` and a demo account, `admin` / `12345`. It also creates an admin session automatically for the browser. **This setup is for local demos only; do not expose it directly to the public internet or a shared network.**
- Git ignores `data/auth.db`, local environment files, and crawler caches. Do not commit runtime files containing credentials, tokens, or other private data.
- Configure a compatible AI provider in the interface, or use [`api/.env.example`](api/.env.example) as a template for `api/.env.local`. Without an AI provider, the CLI's `--local` option uses rule-based analysis.
- The open-source crawler can use an optional `GITHUB_TOKEN` to increase its GitHub API rate limit. See the [crawler documentation](pages/news/opensource_community/README.md) for startup and endpoint details.

## CLI

Run these commands from the repository root:

```bash
python cli.py list --source zh
python cli.py analyze 1 --source zh --local
python cli.py ask 1 "Which sectors could this news affect?" --source zh --local
python cli.py raw --source en
python cli.py stock indicators
```

`zh` and `en` select the Chinese and English market-flash sources. `--local` skips the external AI API; without it, analysis and follow-up questions try the configured AI provider. See `python cli.py --help` and the [CLI guide](README_CLI.md) for more options.

## Project layout

| Path | Purpose |
| --- | --- |
| [`index.html`](index.html) | Main browser interface and navigation |
| [`server.py`](server.py) | Local server, authentication, and API proxy |
| [`news_core.py`](news_core.py) / [`cli.py`](cli.py) | Market-flash retrieval and analysis logic; CLI entry point |
| [`pages/stock-analysis-page/`](pages/stock-analysis-page/) | Stock-analysis frontend and FastAPI backend |
| [`pages/screener/`](pages/screener/), [`pages/market/`](pages/market/), [`pages/options/`](pages/options/) | Screener, heatmap, and options pages |
| [`pages/news/opensource_community/`](pages/news/opensource_community/) | Academic and open-source news crawler |
| [`auth/`](auth/) / [`data/`](data/) | Local authentication logic and runtime-data directory |

## License

This project is licensed under the [MIT License](LICENSE). Third-party dependencies, data, and external services remain subject to their own licenses and terms.

## Star history

[![Star history chart for Trading News Workbench](https://api.star-history.com/svg?repos=GradibelPitt/trading-news-workbench&type=Date)](https://www.star-history.com/#GradibelPitt/trading-news-workbench&Date)
