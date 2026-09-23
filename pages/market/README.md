# 市场热力图

暗色、紧凑、可嵌入 iframe 的市场热力图前端。页面不会生成模拟行情；接口失败、数据缺失或快照过期时均显式展示状态。

## API 契约

```http
GET {API_BASE}/api/market/heatmap?market=US&size_by=market_cap
```

参数：

- `market`: `US`、`HK` 或 `CN`
- `size_by`: `market_cap` 或 `turnover`

推荐响应：

```json
{
  "status": "ok",
  "source": "crawler/yfinance",
  "as_of": "2026-07-09T15:40:00-04:00",
  "stale": false,
  "items": [
    {
      "symbol": "AAPL",
      "name": "Apple Inc.",
      "sector": "Technology",
      "price": 220.1,
      "change_pct": 1.25,
      "market_cap": 3300000000000,
      "turnover": 8200000000,
      "currency": "USD",
      "exchange": "NASDAQ",
      "data_time": "2026-07-09T15:40:00-04:00"
    }
  ]
}
```

也兼容 `stocks` / `data` 扁平数组，或 `sectors[].items`、`industries[].stocks` 分组数组。每个可绘制标的必须有代码、涨跌幅，以及当前所选面积指标。缺失值不会被虚构。

## API_BASE 注入

页面依次读取：

1. `window.MARKET_API_BASE`
2. `window.API_BASE`
3. URL 查询参数 `?apiBase=...`
4. 默认值 `../..`

若 base 已以 `/api` 结尾，页面请求 `{base}/market/heatmap`；否则请求 `{base}/api/market/heatmap`。

点击股票时，iframe 会向父窗口发送：

```js
{ type: "open-stock-analysis", symbol: "AAPL" }
```

独立打开时会跳转到 `../../stock-analysis?symbol=AAPL`。
