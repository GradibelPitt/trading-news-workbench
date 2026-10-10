<div align="center">

# Trading News Workbench

[English](README.md) · **简体中文**

**把市场资讯、股票研究和观察工具放在同一个本地工作台。**

Market news, stock research, and watchlists in one local workspace.

[功能概览](#features) · [快速开始](#quick-start) · [命令行](#cli) · [项目结构](#project-layout) · [许可证](#license)

[![License: MIT](https://img.shields.io/badge/License-MIT-2f6b55.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.x-3776ab.svg)](https://www.python.org/)

</div>

Trading News Workbench 是一个在本机运行的交易资讯与研究界面。它将中英文市场快讯、学术与开源项目资讯、股票分析、选股器、市场热力图、期权链和自选列表集中到一个浏览器工作台；部分新闻操作也可通过 CLI 完成。市场数据和资讯由外部服务提供，可用性取决于对应的数据源。

## Features

| 模块 | 当前能力 |
| --- | --- |
| 资讯 | 浏览中英文市场快讯，以及学术资讯和开源社区项目；支持对快讯进行分析与追问。 |
| 股票分析 | 查询股票、查看 K 线和技术指标，并生成基于规则的交易计划。 |
| 选股与市场 | 根据基本面和技术条件筛选股票；用热力图查看市场快照。 |
| 期权 | 查看标的的到期日与期权链，观察合约数据。 |
| 自选 | 保存股票，并从列表打开对应的分析面板。 |
| CLI | 列出、分析和追问快讯，读取原始接口数据；也可操作股票分析页的部分指标。 |

股票分析、选股器、热力图和期权页面依赖独立的 stock-analysis 服务。分析结果是研究辅助信息，不构成投资建议；期权页面不提供交易执行。数据可能延迟、缺失或因上游接口变化而不可用。

## Quick start

**环境：** Python 3，以及用于安装依赖的 `pip`。首次完整启动需要网络连接，以安装两个子服务的 Python 依赖并读取外部数据。

```bash
git clone https://github.com/GradibelPitt/trading-news-workbench.git
cd trading-news-workbench
```

### Windows

双击 `start.bat`，或在 PowerShell 中运行：

```powershell
.\start.bat
```

启动脚本会运行主站，并尝试启动股票分析和开源社区 crawler 子服务。主站默认打开在 [http://127.0.0.1:8765](http://127.0.0.1:8765)；如果端口已占用，会在 8765–8775 之间选择空闲端口，以终端输出的地址为准。

### macOS

双击 `mac_start.command`，或在终端运行：

```bash
./mac_start.command
```

首次运行会为股票分析和 crawler 子服务建立虚拟环境并安装依赖。停止服务时按 `Control-C`，或运行 `./mac_stop.command`。

### 只运行主站

```bash
python server.py
```

这只启动主站。股票分析、选股、期权和热力图需要另行启动 `pages/stock-analysis-page` 中的 FastAPI 后端；其独立运行方式见 [股票分析说明](pages/stock-analysis-page/README.md)。如后端不是运行在 `127.0.0.1:8000`，启动主站前设置 `STOCK_BACKEND_URL`。

## Configuration and local data

- 首次启动会在 `data/auth.db` 创建本地认证数据库，以及演示账号 `admin` / `12345`。主站还会为浏览器自动建立 admin 会话。**该配置仅适合本机演示，不应直接暴露到公网或共享网络。**
- `data/auth.db`、本地环境变量文件和 crawler 缓存已被 Git 忽略；不要提交包含账号、令牌或其他私密信息的运行时文件。
- AI 分析可在界面中配置兼容的模型服务，或参考 [`api/.env.example`](api/.env.example) 在 `api/.env.local` 中填写配置。未配置模型服务时，可使用 CLI 的 `--local` 选项进行本地规则分析。
- 开源社区 crawler 可选用 `GITHUB_TOKEN` 提高 GitHub API 请求额度。其运行与接口说明见 [crawler 文档](pages/news/opensource_community/README.md)。

## CLI

在项目根目录运行：

```bash
python cli.py list --source zh
python cli.py analyze 1 --source zh --local
python cli.py ask 1 "这条新闻涉及哪些板块？" --source zh --local
python cli.py raw --source en
python cli.py stock indicators
```

`zh` 和 `en` 分别选择中文和英文快讯源。`--local` 跳过外部 AI API；不加该选项时，分析和追问会尝试使用已配置的模型服务。完整参数见 `python cli.py --help` 和 [CLI 文档](README_CLI.md)。

## Project layout

| 路径 | 用途 |
| --- | --- |
| [`index.html`](index.html) | 主站界面和导航 |
| [`server.py`](server.py) | 本地主站、认证和 API 代理 |
| [`news_core.py`](news_core.py) / [`cli.py`](cli.py) | 快讯获取与分析逻辑、命令行入口 |
| [`pages/stock-analysis-page/`](pages/stock-analysis-page/) | 股票分析前端与 FastAPI 后端 |
| [`pages/screener/`](pages/screener/)、[`pages/market/`](pages/market/)、[`pages/options/`](pages/options/) | 选股、热力图和期权页面 |
| [`pages/news/opensource_community/`](pages/news/opensource_community/) | 学术与开源社区资讯 crawler |
| [`auth/`](auth/) / [`data/`](data/) | 本地认证逻辑与运行时数据目录 |

## License

本项目采用 [MIT License](LICENSE)。使用第三方依赖、数据和外部服务时，请遵守各自的许可证及服务条款。

## Star history

[![Star history chart for Trading News Workbench](https://api.star-history.com/svg?repos=GradibelPitt/trading-news-workbench&type=Date)](https://www.star-history.com/#GradibelPitt/trading-news-workbench&Date)
