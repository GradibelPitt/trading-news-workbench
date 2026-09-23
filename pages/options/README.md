# Options Workbench

独立暗色期权链页面。只展示真实接口返回的数据，并提供规则型合约观察；不下单、不预测、不补造缺失字段。

## API base injection

页面按以下顺序读取 API base：

```js
window.OPTIONS_API_BASE
window.STOCK_API_BASE
'/stock-analysis'
```

主站嵌入页面时可以在 `app.js` 前注入：

```html
<script>window.OPTIONS_API_BASE = '/stock-analysis';</script>
```

## Option chain contract

```http
GET {API_BASE}/api/options/{symbol}?expiration=YYYY-MM-DD
```

建议响应：

```json
{
  "status": "ok",
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "underlyingPrice": 210.12,
  "currency": "USD",
  "asOf": "2026-07-09T19:55:00Z",
  "delayed": true,
  "source": "Yahoo Finance via yfinance",
  "expirationDates": ["2026-07-10", "2026-07-17"],
  "selectedExpiration": "2026-07-10",
  "calls": [
    {
      "contractSymbol": "AAPL260710C00210000",
      "strike": 210,
      "lastPrice": 3.2,
      "bid": 3.1,
      "ask": 3.3,
      "volume": 1234,
      "openInterest": 4567,
      "impliedVolatility": 0.284,
      "inTheMoney": true
    }
  ],
  "puts": [],
  "missingFields": []
}
```

前端兼容 snake_case，以及 `last`、`oi`、`iv` 等常见别名。如果数据不可用，接口应返回 `status: "missing"` 或 `status: "data_missing"`，并附带 `message/detail`，不要返回示例链。

## Search contract

```http
GET {API_BASE}/api/search?q=apple
```

响应中的建议数组可以位于 `results`、`quotes`、`items` 或 `data`；建议项至少包含 `symbol/ticker` 与 `name/shortName/longName`。
