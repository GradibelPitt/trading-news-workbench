# opensource_community crawler service

放置路径：`pages/news/opensource_community`

用于调试时可以直接双击：

```bat
start.bat
```

默认端口：`8010-8020`。启动后看窗口输出：

```text
http://127.0.0.1:8010/health
http://127.0.0.1:8010/api/crawl?refresh=false&days=7&fallback_days=15
http://127.0.0.1:8010/api/github/preview?repo=owner/repository
```

## 页面能力

- GitHub 项目使用大、中、小三档卡片密集排版。
- crawler 根据标题与摘要的加权关键词表输出 `domain` 和 `domain_keywords`，旧缓存由前端同规则兜底归类。
- 顶部细分领域标签支持按分类筛选。
- “页内预览”通过本地 crawler 代理读取 GitHub 仓库元数据和 README；抽屉中仍保留 GitHub 与项目主页跳转。
- 可选环境变量 `GITHUB_TOKEN` 用于提高 GitHub API 请求额度，禁止把 token 写入前端。

主网站 `start.ps1` 也可以后台启动这个 `start.bat`。

日志：

```text
crawler_service.log
```
