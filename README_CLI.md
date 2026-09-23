# 7x24 Market Wire CLI

所有网页按钮背后的核心功能都应该能从命令行直接调用。当前 CLI 已支持：

```powershell
python cli.py list --source zh
python cli.py analyze 1 --source zh
python cli.py analyze --index 3 --source zh
python cli.py analyze 1 --source en --json
python cli.py raw --source zh
```

约定：

- `news_core.py` 放共享逻辑：抓新闻、解析列表、分析新闻。
- `server.py` 只负责把共享逻辑暴露成网页 API。
- `cli.py` 只负责把共享逻辑暴露成命令行命令。
- 后续接真实 AI API 时，优先改 `news_core.py` 的 `analyze_news()`，网页按钮和 CLI 会自动复用。
