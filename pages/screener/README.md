# 股票选股器模块

独立、可 iframe 嵌入的暗色选股页面。目录内包含：

- `index.html`：筛选条件、结果表格与分页结构。
- `styles.css`：紧凑暗色布局。
- `app.js`：API 查询、预设策略、分页、结果内搜索和 CSV 导出。

## 注入接口地址

页面加载前可以注入：

```html
<script>window.SCREENER_API_BASE = '/stock-analysis';</script>
```

默认请求：

```text
GET {SCREENER_API_BASE}/api/screener
```

也可以直接设置完整端点：

```js
window.SCREENER_API_ENDPOINT = '/custom/api/screener';
```

## 请求参数

分页与排序：`page`、`page_size`、`sort_by`、`sort_order`。

筛选字段包括：`market`、`exchanges`、`sector`、`industry`、`price_min/max`、
`market_cap_min/max`、`change_pct_min/max`、`volume_min`、`avg_volume_min`、
`relative_volume_min`、`beta_max`、`pe_min/max`、`forward_pe_max`、`peg_max`、
`pb_max`、`ps_max`、`eps_growth_pct_min`、`revenue_growth_pct_min`、
`dividend_yield_pct_min`、`payout_ratio_pct_max`、`roe_pct_min`、
`debt_to_equity_max`、`technical`、`rsi14_min/max`、
`distance_from_52w_high_pct_max`、`atr_pct_min`、`unusual_volume`、`near_52w_high`。

## 响应契约

```json
{
  "items": [{
    "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ",
    "sector": "Technology", "industry": "Consumer Electronics",
    "price": 210.42, "change_pct": 1.25, "market_cap": 3200000000000,
    "volume": 51500000, "avg_volume": 48200000, "relative_volume": 1.07,
    "pe": 32.1, "forward_pe": 28.4, "dividend_yield": 0.0043,
    "rsi14": 61.2, "ema20": 207.4, "ema50": 201.3,
    "technical_status": "多头排列"
  }],
  "page": 1, "pages": 3, "total": 124,
  "source": "provider name", "as_of": "2026-07-09T15:30:00-04:00",
  "warning": null
}
```

兼容 `results` / `data` 数组和 `meta` 元数据包装。数值字段允许为 `null`；页面会明确显示 `—`，不会构造模拟值。`dividend_yield` 推荐用小数比率（例如 `0.043` 表示 4.3%）。
