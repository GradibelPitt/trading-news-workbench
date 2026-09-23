# Stock Analysis Page

A complete front-end + back-end stock analysis page that can be dropped into an existing website.

It uses:

- **FastAPI** as the backend API
- **yfinance.download** for OHLCV stock data, matching the simpler standalone yfinance approach
- **pandas / numpy** for technical indicators
- **TradingView Lightweight Charts** for the front-end chart UI

The page supports:

- Ticker search / analysis input
- Candlestick chart
- SMA20 / SMA50
- Bollinger Bands
- Volume
- MACD
- RSI with 30 / 70 lines
- Buy / sell candidate markers
- Latest technical score panel
- Simple in-memory cache

## Quick start

### macOS / Linux

```bash
cd stock-analysis-page
./start.sh
```

### Windows

```bat
cd stock-analysis-page
start.bat
```

Then open:

```txt
http://127.0.0.1:8000
```

## Manual setup

```bash
cd stock-analysis-page
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade -r backend/requirements.txt
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```


## VST / yfinance note

This patched version uses `yf.download(...)` as the primary data path instead of `yf.Ticker(symbol).history(...)`. That matches the earlier standalone demo style and avoids some cases where yfinance prints `possibly delisted` for valid symbols such as VST.

If you already created the virtual environment from the older ZIP, run this once inside the project folder:

```bash
.venv\Scripts\python.exe -m pip install --upgrade yfinance curl_cffi
```

Then restart Uvicorn.

## API endpoints

### Health check

```txt
GET /health
```

### Minimal ticker search

```txt
GET /api/search?q=VST
```

Returns one validated ticker result if yfinance can find data.

### Analyze ticker

```txt
GET /api/analyze/VST?period=1y&interval=1d
```

### Calculate an explainable trade plan

```txt
GET /api/trade-plan/VST?horizon=intraday_short&cost_bps=5
```

Supported horizons are `intraday_short`, `intraday_trend`, `swing_1_3d`, and
`weekly`. The response contains candidate entry zones, structural invalidation,
targets, cost-adjusted risk/reward, data-quality checks, and explicit no-trade
conditions. It is deterministic technical analysis, not a price prediction.

### Search, screen, heatmap and options

```txt
GET /api/search?q=Apple
GET /api/screener?page=1&page_size=50&market=US&sort_by=market_cap
GET /api/market/heatmap?market=US&size_by=market_cap
GET /api/options/AAPL?expiration=2026-07-17
```

Search accepts company names and ticker symbols. Screener and heatmap results
come from Yahoo Finance screen/sector datasets. Option responses contain real
listed chains and are explicitly marked as delayed; missing provider fields are
returned as missing rather than synthesized.

Supported `period` values:

```txt
1mo, 3mo, 6mo, 1y, 2y, 5y, max
```

Supported `interval` values:

```txt
1d, 1wk, 1mo, 1h, 30m, 15m, 5m
```

Example JSON response shape:

```json
{
  "symbol": "VST",
  "name": "Vistra Corp.",
  "currency": "USD",
  "lastPrice": 153.95,
  "candles": [],
  "sma20": [],
  "sma50": [],
  "bbUpper": [],
  "bbMiddle": [],
  "bbLower": [],
  "volume": [],
  "macd": [],
  "macdSignal": [],
  "macdHist": [],
  "rsi": [],
  "markers": [],
  "score": {
    "buy": 68,
    "sell": 32,
    "signal": "neutral_bullish",
    "reasons": []
  }
}
```

## How to connect this to your existing site

You can integrate this in either of two ways.

### Option A: Use it as a standalone page

Keep the FastAPI server and route your website's "Stock Analysis" nav item to:

```txt
http://your-backend-host:8000/
```

### Option B: Move only the front-end into your site

Copy these files into your front-end project:

```txt
frontend/index.html
frontend/styles.css
frontend/app.js
```

If your API lives on another domain, set this before loading `app.js`:

```html
<script>
  window.STOCK_API_BASE = "https://your-api-domain.com";
</script>
<script src="/static/app.js"></script>
```

Then point the page to:

```txt
GET https://your-api-domain.com/api/analyze/VST?period=1y&interval=1d
```

### Option C: Use only the backend API

Keep your existing UI and call:

```txt
/api/analyze/{symbol}
```

Then map the returned arrays into your own chart components.

## Production notes

This package is designed as a practical MVP / portfolio-grade module.

For a production website:

- Replace yfinance with a stable paid or official market data API if you need reliability or real-time quotes.
- Move cache from in-memory dict to Redis.
- Add rate limiting by IP / user.
- Add symbol search from a real symbol database or market data provider.
- Do not label the signals as guaranteed "best buy/sell points." They are technical candidates only.

## Signal logic

The backend creates buy/sell candidate scores from:

- MACD crossover
- RSI overbought / oversold zone
- Close reclaiming or losing SMA20
- Close breaking Bollinger Bands
- SMA20/SMA50 trend structure
- High-volume green/red candles

Thresholds are intentionally simple so you can customize them later.
