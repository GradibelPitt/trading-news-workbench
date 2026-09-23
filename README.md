# 交易资讯站v1.0



## 首次启动 / 认证数据库

`data/auth.db` 是本地运行时数据库，不提交到 Git。

首次启动时，服务端会自动创建：

- `data/auth.db`
- 认证相关表（users / sessions / watchlist）
- 默认 admin 账号（用户名 `admin`，密码 `12345`，仅用于本地 demo）

启动方式：

```powershell
# 推荐（双击）
start.bat

# 或直接运行
python server.py
```

### macOS

双击项目根目录的 `mac_start.command`，或双击桌面的“交易资讯站.command”快捷方式。首次运行会为两个子服务创建虚拟环境并安装其依赖。

```bash
./mac_start.command
```

停止全部属于本项目的主站、股票分析和 crawler 服务：

```bash
./mac_stop.command
```

也可以在 Finder 中直接双击 `mac_stop.command`。脚本只终止工作目录位于当前项目内、且监听项目端口范围的进程。

**不要手动提交 `data/auth.db`。**

## 新市场工具

主站侧栏已接入选股器、期权工作台和市场热力图。它们复用 stock-analysis FastAPI：

```text
/stock-analysis/api/screener
/stock-analysis/api/market/heatmap
/stock-analysis/api/options/AAPL
/stock-analysis/api/search?q=Apple
```

如果 stock-analysis 后端没有运行在8000端口，可启动主站前设置 `STOCK_BACKEND_URL` 指向实际地址。

股票分析和期权标的搜索支持股票代码、英文公司名和常用中文公司名；新闻与选股器结果搜索支持中英文模糊匹配及常见中文行业同义词。

## First Run / Auth Database

`data/auth.db` is a local runtime database and is intentionally ignored by Git.

On first start, the server will automatically create:

- `data/auth.db`
- Required auth tables (users / sessions / watchlist)
- Default admin account (username `admin`, password `12345`, for local demo only)

Run:

```powershell
start.bat
```

or:

```powershell
python server.py
```

Do not commit `data/auth.db`.
