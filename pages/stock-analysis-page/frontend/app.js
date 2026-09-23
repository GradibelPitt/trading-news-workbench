const API_BASE = window.STOCK_API_BASE || '';

const $ = (id) => document.getElementById(id);
const statusEl = $('status');
const symbolInput = $('symbolInput');
const searchSuggestionsEl = $('stockSearchSuggestions');
const timeframeButtons = [...document.querySelectorAll('[data-timeframe-period][data-timeframe-interval]')];
const favoriteStockButton = $('favoriteStockButton');
const favoriteStockLabel = $('favoriteStockLabel');

let currentLang = localStorage.getItem('stockAnalysisLang') || 'zh';
let lastPayload = null;
let lastTradePlan = null;

const STOCK_FAVORITES_KEY = 'stockFavorites:v1';
let _watchlistCache = null;  // null = not loaded, [] = loaded
const HISTORY_EXPAND_ORDER = ['1mo', '3mo', '6mo', '1y', '2y', '5y'];
const EVENT_MARKER_BASELINE_VALUE = 0.08;
let lazyHistoryTimer = null;
let lazyHistoryLoading = false;
let searchSuggestionTimer = null;
let searchSuggestionSeq = 0;
let searchSuggestionItems = [];

const QUOTE_STAT_ITEMS = [
  { key: 'dayHigh', zh: '最高', en: 'High', format: 'price', tone: 'up' },
  { key: 'open', zh: '今开', en: 'Open', format: 'price' },
  { key: 'volume', zh: '成交量', en: 'Volume', format: 'volume' },
  { key: 'marketCap', zh: '市值', en: 'Market cap', format: 'large' },
  { key: 'floatMarketCap', zh: '流通值', en: 'Float cap', format: 'large' },
  { key: 'dayLow', zh: '最低', en: 'Low', format: 'price', tone: 'down' },
  { key: 'previousClose', zh: '昨收', en: 'Prev close', format: 'price' },
  { key: 'amount', zh: '成交额', en: 'Amount', format: 'large' },
  { key: 'totalShares', zh: '总股本', en: 'Shares out', format: 'shares' },
  { key: 'floatShares', zh: '流通股', en: 'Float shares', format: 'shares' },
  { key: 'peTtm', zh: '市盈率TTM', en: 'PE TTM', format: 'pe', epsKey: 'epsTtm' },
  { key: 'pb', zh: '市净率', en: 'PB', format: 'ratio' },
  { key: 'orderRatioPct', zh: '委比', en: 'Order ratio', format: 'percent' },
  { key: 'dividendTtm', zh: '股息TTM', en: 'Dividend TTM', format: 'price' },
  { key: 'fiftyTwoWeekHigh', zh: '52周最高', en: '52W high', format: 'price', tone: 'up' },
  { key: 'peStatic', zh: '市盈率(静)', en: 'PE static', format: 'pe', epsKey: 'epsTtm' },
  { key: 'peForward', zh: '市盈率(动)', en: 'PE forward', format: 'pe', epsKey: 'epsForward' },
  { key: 'volumeRatio', zh: '量比', en: 'Vol ratio', format: 'ratio' },
  { key: 'beta', zh: 'Beta', en: 'Beta', format: 'ratio' },
  { key: 'fiftyTwoWeekLow', zh: '52周最低', en: '52W low', format: 'price', tone: 'down' },
  { key: 'rangeHigh', zh: '区间最高', en: 'Range high', format: 'price', tone: 'up' },
  { key: 'averagePrice', zh: '平均价', en: 'Avg price', format: 'price' },
  { key: 'amplitudePct', zh: '振幅', en: 'Amplitude', format: 'percent' },
  { key: 'turnoverRatePct', zh: '换手率', en: 'Turnover', format: 'percent' },
  { key: 'lotSize', zh: '每手', en: 'Lot', format: 'lot' },
  { key: 'rangeLow', zh: '区间最低', en: 'Range low', format: 'price', tone: 'down' },
];

// ---------------------------------------------------------------------------
// Frontend localStorage cache — LRU, max 10 entries.
// ---------------------------------------------------------------------------
const STOCK_CACHE_PREFIX = 'stock-cache:';
const STOCK_CACHE_MAX = 10;

function _stockCacheKey(symbol, period, interval) {
  return `${STOCK_CACHE_PREFIX}${symbol}:${period}:${interval}`;
}

function _stockCacheGetAll() {
  const entries = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.startsWith(STOCK_CACHE_PREFIX)) {
      try {
        const item = JSON.parse(localStorage.getItem(key));
        if (item && item._cachedAt) entries.push({ key, cachedAt: item._cachedAt });
      } catch (e) { /* skip corrupt */ }
    }
  }
  return entries.sort((a, b) => b.cachedAt - a.cachedAt);
}

function _stockCacheEvict() {
  const all = _stockCacheGetAll();
  while (all.length >= STOCK_CACHE_MAX) {
    const oldest = all.pop();
    if (oldest) localStorage.removeItem(oldest.key);
  }
}

function stockCacheGet(symbol, period, interval) {
  const key = _stockCacheKey(symbol, period, interval);
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (data && data.candles && data.candles.length > 0) return data;
  } catch (e) { /* ignore */ }
  return null;
}

function stockCacheSet(symbol, period, interval, payload) {
  try {
    _stockCacheEvict();
    const toStore = { ...payload, _cachedAt: Date.now() };
    const key = _stockCacheKey(symbol, period, interval);
    localStorage.setItem(key, JSON.stringify(toStore));
  } catch (e) {
    // QuotaExceededError — clear oldest and retry once.
    try {
      const all = _stockCacheGetAll();
      if (all.length > 0) {
        localStorage.removeItem(all[all.length - 1].key);
        localStorage.setItem(
          _stockCacheKey(symbol, period, interval),
          JSON.stringify({ ...payload, _cachedAt: Date.now() }),
        );
      }
    } catch (e2) { /* give up */ }
  }
}

function normalizeWatchlistItems(items) {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item) => item && item.symbol)
    .map(function(it) {
      return {
        symbol: String(it.symbol || '').trim().toUpperCase(),
        name: it.name || it.symbol || '',
        exchange: it.exchange || '',
        lastPrice: it.last_price ?? it.lastPrice ?? null,
        changePct: it.change_pct ?? it.changePct ?? null,
      };
    });
}

function syncLocalWatchlistMirror(items) {
  try {
    localStorage.setItem(STOCK_FAVORITES_KEY, JSON.stringify(normalizeWatchlistItems(items)));
  } catch (_) {
    // Local mirror is best-effort. The server database remains authoritative.
  }
}

async function loadWatchlist() {
  // Load legacy localStorage data first (may be migrated)
  let legacy = [];
  try {
    legacy = JSON.parse(localStorage.getItem(STOCK_FAVORITES_KEY) || '[]');
  } catch (_) {}

  try {
    const res = await fetch('/api/watchlist');
    const data = await res.json();
    if (res.ok && data.code === 0 && Array.isArray(data.items)) {
      _watchlistCache = normalizeWatchlistItems(data.items);
      if (_watchlistCache.length > 0) {
        syncLocalWatchlistMirror(_watchlistCache);
      } else if (Array.isArray(legacy) && legacy.length > 0) {
        _watchlistCache = normalizeWatchlistItems(legacy);
        syncLocalWatchlistMirror(_watchlistCache);
        _watchlistCache.forEach((item) => {
          fetch('/api/watchlist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(item),
          }).catch(() => {});
        });
      } else {
        syncLocalWatchlistMirror([]);
      }
      return;
    }
  } catch (e) {
    // Server unavailable — use legacy
  }

  // Server empty or unreachable — fall back to localStorage
  _watchlistCache = normalizeWatchlistItems(legacy);
  // Migrate legacy data to server in background
  if (legacy.length > 0) {
    legacy.forEach((item) => {
      fetch('/api/watchlist/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: item.symbol,
          name: item.name || item.symbol,
          exchange: item.exchange || '',
          lastPrice: item.lastPrice ?? null,
          changePct: item.changePct ?? null,
        }),
      }).catch(() => {});
    });
  }
}

function readStockFavorites() {
  return (_watchlistCache || []).filter((item) => item && item.symbol);
}

function isStockFavorited(symbol) {
  const clean = String(symbol || '').trim().toUpperCase();
  if (!clean) return false;
  return readStockFavorites().some((item) => String(item.symbol || '').toUpperCase() === clean);
}

function currentFavoritePayload() {
  const symbol = String(lastPayload?.symbol || symbolInput.value || '').trim().toUpperCase();
  if (!symbol) return null;
  return {
    symbol,
    name: lastPayload?.name || symbol,
    exchange: lastPayload?.exchange || '',
    lastPrice: lastPayload?.lastPrice ?? null,
    changePct: lastPayload?.changePct ?? null,
  };
}

function updateFavoriteButton() {
  if (!favoriteStockButton || !favoriteStockLabel) return;
  const symbol = String(lastPayload?.symbol || symbolInput.value || '').trim().toUpperCase();
  const active = isStockFavorited(symbol);
  favoriteStockButton.disabled = !symbol;
  favoriteStockButton.classList.toggle('is-favorited', active);
  favoriteStockButton.setAttribute('aria-pressed', active ? 'true' : 'false');
  favoriteStockButton.title = active
    ? (currentLang === 'zh' ? '已加入自选' : 'Already in favorites')
    : (currentLang === 'zh' ? '加入自选' : 'Add to favorites');
  favoriteStockLabel.textContent = active ? t('summary.favorited') : t('summary.addFavorite');
}

function notifyWatchlistChanged(action, item) {
  if (window.parent === window) return;

  window.parent.postMessage({
    app: 'stock-analysis',
    type: 'STOCK_WATCHLIST_UPDATED',
    version: 1,
    action: action,
    item: item || null,
    symbol: item?.symbol || lastPayload?.symbol || null,
    at: new Date().toISOString()
  }, window.location.origin);
}

async function addCurrentStockToFavorites() {
  const item = currentFavoritePayload();
  if (!item) return;
  const clean = item.symbol.toUpperCase();
  const previous = readStockFavorites();

  if (isStockFavorited(clean)) {
    // Remove from favorites (optimistic)
    _watchlistCache = (_watchlistCache || []).filter(
      (f) => String(f.symbol || '').toUpperCase() !== clean
    );
    syncLocalWatchlistMirror(_watchlistCache);
    updateFavoriteButton();
    try {
      const res = await fetch(`/api/watchlist/remove?symbol=${encodeURIComponent(clean)}`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (error) {
      _watchlistCache = previous;
      syncLocalWatchlistMirror(_watchlistCache);
      updateFavoriteButton();
      showStatus(currentLang === 'zh' ? `${clean} 取消收藏失败，已恢复` : `Could not remove ${clean}; restored favorite`, true);
      return;
    }
    showStatus(currentLang === 'zh' ? `${clean} 已取消收藏` : `${clean} removed from favorites`);
    notifyWatchlistChanged('remove', item);
  } else {
    // Add to favorites (optimistic cache update + server sync)
    _watchlistCache = (_watchlistCache || []).filter(
      (f) => String(f.symbol || '').toUpperCase() !== clean
    );
    _watchlistCache.unshift({ symbol: clean, ...item });
    syncLocalWatchlistMirror(_watchlistCache);
    updateFavoriteButton();
    try {
      const res = await fetch('/api/watchlist/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.code !== 0) throw new Error(data.error || `HTTP ${res.status}`);
      if (data.item) {
        _watchlistCache = normalizeWatchlistItems([data.item, ..._watchlistCache.filter(
          (f) => String(f.symbol || '').toUpperCase() !== clean
        )]);
        syncLocalWatchlistMirror(_watchlistCache);
      }
    } catch (error) {
      _watchlistCache = previous;
      syncLocalWatchlistMirror(_watchlistCache);
      updateFavoriteButton();
      showStatus(currentLang === 'zh' ? `${clean} 收藏失败，数据库未保存` : `Could not save ${clean} to favorites`, true);
      return;
    }
    showStatus(currentLang === 'zh' ? `${clean} 已加入收藏` : `${clean} added to favorites`);
    notifyWatchlistChanged('add', item);
  }
}


const indicatorState = {
  candles: true,
  sma20: false,
  sma50: false,
  bollinger: false,
  signals: false,
  events: true,
};

const activeIndicators = new Set();

const legacyIndicatorChartMap = {};

function normalizeIndicatorKeyForLegacyChart(key) {
  return String(key || '').trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
}

function isLegacyIndicatorKey(key) {
  return !!legacyIndicatorChartMap[normalizeIndicatorKeyForLegacyChart(key)];
}

function syncLegacyIndicatorCharts() {
  const grid = document.querySelector('.dashboard-grid');
  if (!grid) return;

  grid.classList.remove('show-volume-chart', 'show-macd-chart', 'show-rsi-chart');

  activeIndicators.forEach((key) => {
    const className = legacyIndicatorChartMap[normalizeIndicatorKeyForLegacyChart(key)];
    if (className) grid.classList.add(className);
  });

  const btnVol = $('btn3dVol');
  if (btnVol) {
    const isVolActive = activeIndicators.has('volume') || activeIndicators.has('vol') || activeIndicators.has('amount');
    btnVol.classList.toggle('active', isVolActive);
    if (chartState.chart3D) {
      chartState.chart3D.toggleVolume(isVolActive);
    }
  }

  // Sync the sub-indicator panel rendering
  renderActiveIndicators();

  requestAnimationFrame(() => {
    window.dispatchEvent(new Event('resize'));
  });
}

const chartState = {
  price: null,
  volume: null,
  custom: null,
  priceSyncHandler: null,
  customSyncHandler: null,
  chart3D: null,
  chart3DSub: null,
  currentChartViewMode: '2d',
  fusion2D: true,
  fusion3D: false,
  dynamicPanes: [],
  technicalMarkerApi: null,
  eventMarkerApi: null,
  candleSeries: null,
  eventMarkerSeries: null,
  currentMarkers: [],
  eventMarkers: [],
  events: [],
  eventByTime: new Map(),
  eventTooltip: null,
  lazyHistoryArmed: false,
  priceSeries: {},
  priceData: {},
  dynamicPriceSeries: [],
  dynamicPaneSeries: [],
  tradePlanLines: [],
};

const I18N = {
  en: {
    pageTitle: 'Stock Analysis Dashboard',
    langToggle: '中文',
    hero: {
      eyebrow: 'Quant stock analysis',
      title: 'TradingView-style stock analysis page',
      subcopy: 'Search a ticker, fetch yfinance data, calculate SMA, Bollinger Bands, MACD, RSI, and show technical entry/exit candidates.',
    },
    search: {
      placeholder: 'Ticker or company name, e.g. AAPL or Apple',
      rangeTitle: 'Range',
      intervalTitle: 'Interval',
      button: 'Analyze',
    },
    summary: {
      symbol: 'Symbol',
      waiting: 'Waiting for data',
      last: 'Last',
      analystRating: 'Rating',
      ratingHelp: 'Based on analyst consensus',
      clickDetails: 'Click for details',
      signal: 'Signal',
      favorite: 'Favorite',
      addFavorite: 'Add favorite',
      favorited: 'Favorited',
    },
    ratings: {
      strongBuy: 'Strong Buy',
      buy: 'Buy',
      hold: 'Hold',
      sell: 'Sell',
    },
    modal: {
      analystTitle: 'Analyst Consensus Details',
      techTitle: 'Technical Indicators Details',
      totalAnalysts: 'Based on {total} analysts',
      targetPrice: '12-Month Target Price',
      currentPrice: 'Current Price',
      strongBuy: 'Strong Buy',
      buy: 'Buy',
      hold: 'Hold',
      underperform: 'Underperform',
      sell: 'Sell',
    },
    panels: {
      priceTitle: 'Price action',
      priceDesc: 'Candles, SMA20, SMA50, Bollinger Bands, and buy/sell markers.',
      reasonsTitle: 'Signal reasons',
      readingTitle: 'Current indicator reading',
      readingDesc: 'Auto-generated from the latest candle and indicator values.',
      integrationTitle: 'Integration notes',
      integrationDesc: 'Use <code>/api/analyze/VST?period=1y&amp;interval=1d</code> from your existing stock page. Replace the yfinance layer later if you need production-grade real-time quotes.',
      volumeTitle: 'Volume',
      quoteStatsTitle: 'Quote statistics',
      fusionView: 'Fusion View',
    },
    legend: {
      candles: 'Candles',
      bollinger: 'Bollinger',
    },
    guide: {
      title: 'Indicator guide / 指标术语解释',
      desc: 'Quick explanations for the signals shown above. These are technical references, not financial advice.',
      terms: [
        ['SMA20 / SMA50', 'Simple moving averages. SMA20 tracks short-term trend; SMA50 tracks medium-term trend. Price above both usually means trend support; price losing SMA20 can mean short-term weakness.'],
        ['Bollinger Bands', 'A volatility channel built around SMA20. Near the upper band means price is strong but may be stretched; near the lower band means price is weak or potentially oversold.'],
        ['MACD', 'Momentum indicator using EMA12 minus EMA26. MACD crossing above the signal line is a bullish crossover; crossing below is a bearish crossover. Histogram shows momentum expansion or contraction.'],
        ['RSI', 'Relative Strength Index, usually read on a 0–100 scale. Above 70 is often called overbought; below 30 is often called oversold. Strong trends can stay overbought or oversold for a while.'],
        ['Volume', 'Trading activity. High volume on a green candle suggests stronger buying participation; high volume on a red candle suggests stronger selling participation.'],
        ['Buy / Sell markers', 'Technical candidate points generated by the scoring rules. They are not guaranteed best entries or exits; use them as prompts for deeper review.'],
      ],
    },
    states: {
      noData: 'No data yet.',
      waitingTitle: 'Waiting for data',
      waitingBody: 'Search a ticker to generate RSI, MACD, Bollinger, SMA, and volume interpretation.',
      noStrongCandidate: 'No strong technical candidate from the latest candle.',
      servedCache: 'Served from cache',
      freshFetch: 'Fresh yfinance fetch',
      loading: 'Loading {symbol} from yfinance...',
      loadFail: 'Could not load {symbol}: {message}. Please check if the ticker symbol is correct.',
    },
    signals: {
      buy_candidate: 'Buy candidate',
      sell_candidate: 'Sell candidate',
      neutral_bullish: 'Neutral bullish',
      neutral_bearish: 'Neutral bearish',
      neutral: 'Neutral',
      insufficient_data: 'Need more data',
    },
    chartLabels: {
      bbUpper: 'BB Upper',
      bbMiddle: 'BB Mid',
      bbLower: 'BB Lower',
      macdSignal: 'Signal',
    },
    readings: {
      rsiOverboughtTitle: 'RSI overbought',
      rsiOverboughtBody: 'Current RSI = {rsi}. The stock is in an overbought zone; short-term pullback risk is elevated.',
      rsiOversoldTitle: 'RSI oversold',
      rsiOversoldBody: 'Current RSI = {rsi}. The stock is in an oversold zone; a rebound is possible, but weakness may continue without confirmation.',
      rsiBullTitle: 'RSI bullish bias',
      rsiBullBody: 'Current RSI = {rsi}. Momentum is above neutral but not yet overheated.',
      rsiBearTitle: 'RSI bearish bias',
      rsiBearBody: 'Current RSI = {rsi}. Momentum is below neutral, so buyers are not clearly in control.',
      rsiNeutralTitle: 'RSI neutral',
      rsiNeutralBody: 'Current RSI = {rsi}. Momentum is near the middle of the range.',
      macdBullCrossTitle: 'MACD bullish crossover',
      macdBullCrossBody: 'MACD just crossed above the signal line. Momentum is turning positive; histogram is {direction}.',
      macdBearCrossTitle: 'MACD bearish crossover',
      macdBearCrossBody: 'MACD just crossed below the signal line. Momentum is turning negative; histogram is {direction}.',
      macdPositiveTitle: 'MACD positive momentum',
      macdPositiveBody: 'MACD is above the signal line. Momentum remains bullish; histogram is {direction}.',
      macdNegativeTitle: 'MACD negative momentum',
      macdNegativeBody: 'MACD is below the signal line. Momentum remains bearish; histogram is {direction}.',
      macdNeutralTitle: 'MACD neutral',
      macdNeutralBody: 'MACD and signal line are nearly equal, so momentum direction is not clear.',
      histExpanding: 'expanding',
      histContracting: 'contracting',
      histFlat: 'flat',
      histUnknown: 'unknown',
      bbUpperTitle: 'Bollinger upper-band stretch',
      bbUpperBody: 'Price is at or above the upper band. This confirms strength, but chasing risk is higher.',
      bbLowerTitle: 'Bollinger lower-band pressure',
      bbLowerBody: 'Price is at or below the lower band. It may be oversold, but trend weakness needs confirmation before assuming a rebound.',
      bbStrongTitle: 'Bollinger strong zone',
      bbStrongBody: 'Price is in the upper {pct}% of the band range, showing strength but limited room before the upper band.',
      bbWeakTitle: 'Bollinger weak zone',
      bbWeakBody: 'Price is near the lower side of the band range, showing weak short-term positioning.',
      bbMidSupportTitle: 'Bollinger midline support',
      bbMidSupportBody: 'Price is above the Bollinger midline, so short-term structure is mildly constructive.',
      bbMidResistTitle: 'Bollinger midline resistance',
      bbMidResistBody: 'Price is below the Bollinger midline, so short-term structure is mildly weak.',
      trendSupportTitle: 'Trend support',
      trendSupportBody: 'SMA20 is above SMA50 and price is above SMA20. The trend structure is bullish.',
      trendPullbackTitle: 'Bullish trend, short-term pullback',
      trendPullbackBody: 'SMA20 is above SMA50, but price slipped below SMA20. Watch whether it reclaims the short-term average.',
      trendPressureTitle: 'Trend pressure',
      trendPressureBody: 'SMA20 is below SMA50 and price is below SMA20. The trend structure is bearish.',
      trendMixedTitle: 'Mixed moving averages',
      trendMixedBody: 'SMA structure is mixed. Trend confirmation is weaker than a clean SMA20/SMA50 alignment.',
      volumeGreenTitle: 'High-volume green candle',
      volumeGreenBody: 'Latest volume is {ratio}× the recent average. Buying participation is elevated.',
      volumeRedTitle: 'High-volume red candle',
      volumeRedBody: 'Latest volume is {ratio}× the recent average. Selling participation is elevated.',
      volumeNormalTitle: 'Normal volume',
      volumeNormalBody: 'Latest volume is {ratio}× the recent average. No abnormal volume confirmation.',
      overallTitle: 'Overall technical read',
      overallBuyBody: 'Buy candidate score {buy}/100 is stronger than sell score {sell}/100. Treat it as a setup candidate, not a blind entry.',
      overallSellBody: 'Sell candidate score {sell}/100 is stronger than buy score {buy}/100. Short-term risk is elevated.',
      overallNeutralBody: 'No high-confidence technical candidate. Buy score {buy}/100 vs sell score {sell}/100.',
      needMoreTitle: 'Need more data',
      needMoreBody: 'There is not enough indicator data yet. Try a longer range such as 6M or 1Y.',
    },
    reasons: {
      'MACD bullish crossover': 'MACD bullish crossover',
      'MACD bearish crossover': 'MACD bearish crossover',
      'RSI oversold zone': 'RSI oversold zone',
      'RSI overbought zone': 'RSI overbought zone',
      'Close reclaimed SMA20': 'Close reclaimed SMA20',
      'Close lost SMA20': 'Close lost SMA20',
      'Close below lower Bollinger Band': 'Close below lower Bollinger Band',
      'Close above upper Bollinger Band': 'Close above upper Bollinger Band',
      'SMA20 above SMA50 trend support': 'SMA20 above SMA50 trend support',
      'SMA20 below SMA50 trend pressure': 'SMA20 below SMA50 trend pressure',
      'High-volume green candle': 'High-volume green candle',
      'High-volume red candle': 'High-volume red candle',
    },
  },
  zh: {
    pageTitle: '股票分析面板',
    langToggle: 'English',
    hero: {
      eyebrow: '量化股票分析',
      title: 'TradingView 风格股票分析页面',
      subcopy: '搜索股票代码，使用 yfinance 获取行情数据，计算 SMA、布林带、MACD、RSI，并标注技术买入/卖出候选点。',
    },
    search: {
      placeholder: '搜索股票代码或公司名称',
      rangeTitle: '时间范围',
      intervalTitle: 'K线周期',
      button: '分析',
    },
    summary: {
      symbol: '股票代码',
      waiting: '等待数据',
      last: '最新价',
      analystRating: '评级',
      ratingHelp: '基于分析师共识',
      clickDetails: '点击查看详情',
      signal: '信号',
      favorite: '收藏',
      addFavorite: '加入收藏',
      favorited: '已收藏',
    },
    ratings: {
      strongBuy: '强力推荐',
      buy: '买入',
      hold: '持有',
      sell: '卖出',
    },
    modal: {
      analystTitle: '分析师评级详情',
      techTitle: '技术指标评级详情',
      totalAnalysts: '{total} 位分析师参与',
      targetPrice: '12个月目标价',
      currentPrice: '当前价格',
      strongBuy: '强力买入',
      buy: '买入',
      hold: '持有',
      underperform: '弱于大盘',
      sell: '卖出',
    },
    panels: {
      priceTitle: '价格走势',
      priceDesc: 'K线、SMA20、SMA50、布林带，以及买入/卖出候选标记。',
      reasonsTitle: '信号依据',
      readingTitle: '当前指标解读',
      readingDesc: '根据最新一根 K 线和技术指标自动生成。',
      integrationTitle: '接入说明',
      integrationDesc: '你现有的股票页面可以直接请求 <code>/api/analyze/VST?period=1y&amp;interval=1d</code>。如果后续需要生产级实时行情，再把 yfinance 数据层替换成正式行情 API。',
      volumeTitle: '成交量',
      quoteStatsTitle: '行情统计',
      trendTitle: '趋势指标',
      fusionView: '融合视图',
    },
    legend: {
      candles: 'K线',
      bollinger: '布林带',
    },
    guide: {
      title: '指标术语解释 / Indicator Guide',
      desc: '下面是页面中常见技术指标和信号的解释，仅用于技术分析参考，不构成投资建议。',
      terms: [
        ['SMA20 / SMA50', '简单移动平均线。SMA20 更偏短期趋势，SMA50 更偏中期趋势。价格站在两条均线上方通常代表趋势有支撑；跌破 SMA20 可能代表短线转弱。'],
        ['布林带 Bollinger Bands', '围绕 SMA20 构建的波动通道。价格接近上轨说明走势较强，但可能短线偏热；接近下轨说明走势较弱，也可能进入超跌区域。'],
        ['MACD', '趋势动能指标，核心是 EMA12 减 EMA26。MACD 上穿信号线常被称为金叉，偏多；下穿信号线常被称为死叉，偏空。柱状图反映动能扩张或收缩。'],
        ['RSI', '相对强弱指标，通常在 0–100 之间。高于 70 常被视为超买，低于 30 常被视为超卖。强趋势里 RSI 可以长时间维持高位或低位。'],
        ['Volume 成交量', '成交活跃度。放量上涨说明买盘参与增强；放量下跌说明卖盘参与增强。成交量本身不判断方向，要结合 K 线涨跌看。'],
        ['Buy / Sell 标记', '由当前打分规则生成的技术候选点，不代表确定的最佳买卖点，也不等于投资建议，只适合作为进一步分析的提示。'],
      ],
    },
    states: {
      noData: '暂无数据。',
      waitingTitle: '等待数据',
      waitingBody: '搜索股票代码后，会自动生成 RSI、MACD、布林带、均线和成交量解读。',
      noStrongCandidate: '最新 K 线没有形成强技术候选信号。',
      servedCache: '来自缓存数据',
      freshFetch: '从 yfinance 实时拉取',
      loading: '正在从 yfinance 加载 {symbol}...',
      loadFail: '无法加载 {symbol}：{message}。请检查股票代码是否正确。',
    },
    signals: {
      buy_candidate: '买入候选',
      sell_candidate: '卖出候选',
      neutral_bullish: '中性偏多',
      neutral_bearish: '中性偏空',
      neutral: '中性',
      insufficient_data: '数据不足',
    },
    chartLabels: {
      bbUpper: '布林上轨',
      bbMiddle: '布林中轨',
      bbLower: '布林下轨',
      macdSignal: '信号线',
    },
    readings: {
      rsiOverboughtTitle: 'RSI 超买',
      rsiOverboughtBody: '当前 RSI = {rsi}。股票处于超买区域，短线回调风险上升。',
      rsiOversoldTitle: 'RSI 超卖',
      rsiOversoldBody: '当前 RSI = {rsi}。股票处于超卖区域，可能出现反弹，但没有确认信号前，弱势也可能继续。',
      rsiBullTitle: 'RSI 偏多',
      rsiBullBody: '当前 RSI = {rsi}。动能高于中性区，但还没有明显过热。',
      rsiBearTitle: 'RSI 偏空',
      rsiBearBody: '当前 RSI = {rsi}。动能低于中性区，买方暂时没有明显掌控。',
      rsiNeutralTitle: 'RSI 中性',
      rsiNeutralBody: '当前 RSI = {rsi}。动能接近区间中部，方向性不强。',
      macdBullCrossTitle: 'MACD 金叉',
      macdBullCrossBody: 'MACD 刚刚上穿信号线，动能开始转正；柱状图状态：{direction}。',
      macdBearCrossTitle: 'MACD 死叉',
      macdBearCrossBody: 'MACD 刚刚下穿信号线，动能开始转负；柱状图状态：{direction}。',
      macdPositiveTitle: 'MACD 多头动能',
      macdPositiveBody: 'MACD 位于信号线上方，动能仍偏多；柱状图状态：{direction}。',
      macdNegativeTitle: 'MACD 空头动能',
      macdNegativeBody: 'MACD 位于信号线下方，动能仍偏空；柱状图状态：{direction}。',
      macdNeutralTitle: 'MACD 中性',
      macdNeutralBody: 'MACD 与信号线非常接近，当前动能方向不清晰。',
      histExpanding: '扩张',
      histContracting: '收缩',
      histFlat: '持平',
      histUnknown: '未知',
      bbUpperTitle: '布林上轨拉伸',
      bbUpperBody: '价格位于或高于布林上轨，说明走势强，但追高风险也更高。',
      bbLowerTitle: '布林下轨承压',
      bbLowerBody: '价格位于或低于布林下轨，可能超跌，但不能直接假设会反弹，需要等待弱势缓解的确认。',
      bbStrongTitle: '布林带强势区',
      bbStrongBody: '价格处于布林带区间上方 {pct}% 附近，说明短线强势，但距离上轨空间有限。',
      bbWeakTitle: '布林带弱势区',
      bbWeakBody: '价格靠近布林带下侧，说明短线位置偏弱。',
      bbMidSupportTitle: '布林中轨支撑',
      bbMidSupportBody: '价格高于布林中轨，短线结构略偏正面。',
      bbMidResistTitle: '布林中轨压力',
      bbMidResistBody: '价格低于布林中轨，短线结构略偏弱。',
      trendSupportTitle: '趋势支撑',
      trendSupportBody: 'SMA20 高于 SMA50，且价格高于 SMA20。趋势结构偏多。',
      trendPullbackTitle: '多头趋势中的短线回踩',
      trendPullbackBody: 'SMA20 仍高于 SMA50，但价格跌破 SMA20。需要观察能否重新站回短期均线。',
      trendPressureTitle: '趋势承压',
      trendPressureBody: 'SMA20 低于 SMA50，且价格低于 SMA20。趋势结构偏空。',
      trendMixedTitle: '均线结构混合',
      trendMixedBody: 'SMA 结构不够清晰，趋势确认弱于 SMA20/SMA50 完全同向排列的情况。',
      volumeGreenTitle: '放量阳线',
      volumeGreenBody: '最新成交量是近期均量的 {ratio} 倍，买盘参与度提升。',
      volumeRedTitle: '放量阴线',
      volumeRedBody: '最新成交量是近期均量的 {ratio} 倍，卖盘参与度提升。',
      volumeNormalTitle: '成交量正常',
      volumeNormalBody: '最新成交量是近期均量的 {ratio} 倍，暂时没有明显异常放量确认。',
      overallTitle: '综合技术解读',
      overallBuyBody: '买入候选评分 {buy}/100，高于卖出评分 {sell}/100。把它当成 setup 候选，不要盲目入场。',
      overallSellBody: '卖出候选评分 {sell}/100，高于买入评分 {buy}/100。短线风险升高。',
      overallNeutralBody: '当前没有高置信度技术候选。买入评分 {buy}/100，卖出评分 {sell}/100。',
      needMoreTitle: '需要更多数据',
      needMoreBody: '当前指标数据不足。可以尝试更长时间范围，例如 6M 或 1Y。',
    },
    reasons: {
      'MACD bullish crossover': 'MACD 金叉',
      'MACD bearish crossover': 'MACD 死叉',
      'RSI oversold zone': 'RSI 超卖区域',
      'RSI overbought zone': 'RSI 超买区域',
      'Close reclaimed SMA20': '收盘价重新站上 SMA20',
      'Close lost SMA20': '收盘价跌破 SMA20',
      'Close below lower Bollinger Band': '收盘价低于布林下轨',
      'Close above upper Bollinger Band': '收盘价高于布林上轨',
      'SMA20 above SMA50 trend support': 'SMA20 高于 SMA50，趋势有支撑',
      'SMA20 below SMA50 trend pressure': 'SMA20 低于 SMA50，趋势承压',
      'High-volume green candle': '放量阳线',
      'High-volume red candle': '放量阴线',
    },
  },
};

const chartTheme = {
  layout: {
    background: { color: 'transparent' },
    textColor: '#cbd5e1',
    fontSize: 12,
  },
  grid: {
    vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
    horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
  },
  rightPriceScale: {
    borderColor: 'rgba(148, 163, 184, 0.18)',
  },
  timeScale: {
    borderColor: 'rgba(148, 163, 184, 0.18)',
    timeVisible: true,
    secondsVisible: false,
  },
  crosshair: {
    mode: 1,
  },
};

function t(key, params = {}) {
  const parts = key.split('.');
  let value = I18N[currentLang];
  for (const part of parts) value = value?.[part];
  if (value === undefined || value === null) {
    value = I18N.en;
    for (const part of parts) value = value?.[part];
  }
  if (typeof value !== 'string') return key;
  return value.replace(/\{(\w+)\}/g, (_, name) => params[name] ?? '');
}

function showStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.remove('hidden', 'error');
  if (isError) statusEl.classList.add('error');
}

function hideStatus() {
  statusEl.classList.add('hidden');
}

function renderTerms() {
  const grid = $('termGrid');
  if (!grid) return;
  const terms = I18N[currentLang].guide.terms;
  grid.innerHTML = terms.map(([title, body]) => `
    <article class="term-card">
      <h4>${escapeHtml(title)}</h4>
      <p>${escapeHtml(body)}</p>
    </article>
  `).join('');
}

function updateEmptyIndicatorReading() {
  const container = $('indicatorReading');
  if (!container) return;
  container.innerHTML = makeReading(t('states.waitingTitle'), t('states.waitingBody'), 'neutral');
}

function applyLanguage() {
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  document.title = t('pageTitle');

  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder));
  });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => {
    el.setAttribute('title', t(el.dataset.i18nTitle));
  });
  document.querySelectorAll('[data-i18n-html]').forEach((el) => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });

  const langToggle = $('langToggle');
  if (langToggle) langToggle.textContent = t('langToggle');
  const clearBtn = $('clearCustomIndicators');
  if (clearBtn) clearBtn.textContent = currentLang === 'zh' ? '清空' : 'Clear';
  renderTerms();
  renderIndicatorCatalog();

  if (lastPayload) {
    updateSummary(lastPayload);
    createCharts(lastPayload);
  } else {
    renderQuoteStats(null);
    updateEmptyIndicatorReading();
  }
  updateFavoriteButton();
}

function addLine(chart, options = {}) {
  if (chart.addLineSeries) return chart.addLineSeries(options);
  return chart.addSeries(LightweightCharts.LineSeries, options);
}

function addCandles(chart, options = {}) {
  if (chart.addCandlestickSeries) return chart.addCandlestickSeries(options);
  return chart.addSeries(LightweightCharts.CandlestickSeries, options);
}

function addHistogram(chart, options = {}) {
  if (chart.addHistogramSeries) return chart.addHistogramSeries(options);
  return chart.addSeries(LightweightCharts.HistogramSeries, options);
}

function markerSortValue(marker) {
  const value = marker?.time;
  if (typeof value === 'number') return value;
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : value;
  }
  return 0;
}

function getVisibleTechnicalMarkers() {
  return indicatorState.signals ? (chartState.currentMarkers || []) : [];
}

function getVisibleCorporateMarkers() {
  return indicatorState.events !== false ? (chartState.eventMarkers || []) : [];
}

function buildEventMarkerTrackData(markers) {
  const byTime = new Map();

  (markers || []).forEach((marker) => {
    const key = normalizeChartTimeKey(marker.time);
    if (!key || byTime.has(key)) return;
    byTime.set(key, { time: marker.time, value: EVENT_MARKER_BASELINE_VALUE });
  });

  // A transparent anchor keeps the corporate-event series scaled from 0 to 1.
  // Without this, a constant hidden line can be auto-scaled into the middle of
  // the candle pane, which is why E/D dots were floating inside the K-line area.
  const candles = chartState.priceData?.candles || [];
  if (candles.length) {
    const firstTime = candles[0]?.time;
    const lastTime = candles[candles.length - 1]?.time;
    const firstKey = normalizeChartTimeKey(firstTime);
    const lastKey = normalizeChartTimeKey(lastTime);
    if (firstTime && !byTime.has(firstKey)) byTime.set(`anchor-start:${firstKey}`, { time: firstTime, value: 1 });
    if (lastTime && !byTime.has(lastKey)) byTime.set(`anchor-end:${lastKey}`, { time: lastTime, value: 1 });
  }

  return Array.from(byTime.values()).sort((a, b) => {
    const av = markerSortValue(a);
    const bv = markerSortValue(b);
    return av === bv ? 0 : (av > bv ? 1 : -1);
  });
}

function normalizeCorporateMarkers(markers) {
  if (!markers || !markers.length) return [];
  
  const timeGroups = new Map();
  markers.forEach((m) => {
    const key = normalizeChartTimeKey(m.time);
    if (!key) return;
    if (!timeGroups.has(key)) {
      timeGroups.set(key, []);
    }
    timeGroups.get(key).push(m);
  });

  const merged = [];
  timeGroups.forEach((group, key) => {
    if (group.length === 1) {
      merged.push({
        time: group[0].time,
        position: 'aboveBar',
        color: group[0].color || (group[0].eventType === 'earnings' ? '#2563eb' : '#f59e0b'),
        shape: 'circle',
        text: group[0].text || (group[0].eventType === 'earnings' ? 'E' : 'D'),
        size: 1,
        eventId: group[0].eventId,
        eventType: group[0].eventType,
      });
    } else {
      const types = new Set(group.map(g => g.eventType));
      let text = 'E+D';
      let color = '#a78bfa';
      
      if (types.has('earnings') && !types.has('dividend')) {
        text = 'E';
        color = '#2563eb';
      } else if (!types.has('earnings') && types.has('dividend')) {
        text = 'D';
        color = '#f59e0b';
      }
      
      merged.push({
        time: group[0].time,
        position: 'aboveBar',
        color: color,
        shape: 'circle',
        text: text,
        size: 1.1,
        eventId: 'multiple',
        eventType: 'multiple',
      });
    }
  });

  return merged;
}

function applyMarkersToSeries(series, apiKey, markers) {
  if (!series) return;
  if (window.LightweightCharts?.createSeriesMarkers) {
    if (chartState[apiKey]) {
      chartState[apiKey].setMarkers(markers);
    } else {
      chartState[apiKey] = LightweightCharts.createSeriesMarkers(series, markers);
    }
  } else if (series.setMarkers) {
    series.setMarkers(markers);
  }
}

function applyMarkerVisibility() {
  applyMarkersToSeries(chartState.candleSeries, 'technicalMarkerApi', getVisibleTechnicalMarkers());

  const eventSeries = chartState.eventMarkerSeries;
  const corporateMarkers = normalizeCorporateMarkers(getVisibleCorporateMarkers());
  if (eventSeries && typeof eventSeries.setData === 'function') {
    eventSeries.setData(buildEventMarkerTrackData(corporateMarkers));
  }
  applyMarkersToSeries(eventSeries, 'eventMarkerApi', corporateMarkers);
}

function setMarkers(series, markers) {
  chartState.candleSeries = series;
  chartState.currentMarkers = Array.isArray(markers) ? markers : [];
  applyMarkerVisibility();
}

function setEventMarkers(markers) {
  chartState.eventMarkers = Array.isArray(markers) ? markers : [];
  applyMarkerVisibility();
}

function setSeriesVisibility(series, data, visible) {
  if (!series || typeof series.setData !== 'function') return;
  series.setData(visible ? (data || []) : []);
  if (typeof series.applyOptions === 'function') {
    try {
      series.applyOptions({ visible: !!visible });
    } catch (error) {
      // Some older Lightweight Charts builds may ignore unsupported options.
    }
  }
}

function applyPriceIndicatorVisibility() {
  const series = chartState.priceSeries || {};
  const data = chartState.priceData || {};

  setSeriesVisibility(series.candles, data.candles, indicatorState.candles);
  setSeriesVisibility(series.sma20, data.sma20, indicatorState.sma20);
  setSeriesVisibility(series.sma50, data.sma50, indicatorState.sma50);
  setSeriesVisibility(series.bbUpper, data.bbUpper, indicatorState.bollinger);
  setSeriesVisibility(series.bbMiddle, data.bbMiddle, indicatorState.bollinger);
  setSeriesVisibility(series.bbLower, data.bbLower, indicatorState.bollinger);
  applyMarkerVisibility();
}

function syncIndicatorToggleStyles() {
  document.querySelectorAll('.indicator-chip').forEach((chip) => {
    const input = chip.querySelector('input[type="checkbox"]');
    chip.classList.toggle('active', !!(input && input.checked));
  });
}

function bindIndicatorToggle(inputId, stateKey) {
  const input = $(inputId);
  if (!input) return;
  input.checked = !!indicatorState[stateKey];
  input.addEventListener('change', () => {
    indicatorState[stateKey] = !!input.checked;
    syncIndicatorToggleStyles();
    applyPriceIndicatorVisibility();
  });
}

function initIndicatorToggles() {
  bindIndicatorToggle('toggleCandles', 'candles');
  bindIndicatorToggle('toggleSma20', 'sma20');
  bindIndicatorToggle('toggleSma50', 'sma50');
  bindIndicatorToggle('toggleBollinger', 'bollinger');
  bindIndicatorToggle('toggleSignals', 'signals');
  syncIndicatorToggleStyles();
}

function baseChartOptions(height) {
  return {
    ...chartTheme,
    height,
    localization: {
      priceFormatter: (price) => Number(price).toFixed(price > 100 ? 2 : 3),
    },
  };
}

function destroyCharts() {
  if (chartState.price && chartState.priceSyncHandler) {
    try {
      chartState.price.timeScale().unsubscribeVisibleLogicalRangeChange(chartState.priceSyncHandler);
    } catch (e) {}
    chartState.priceSyncHandler = null;
  }
  if (chartState.custom && chartState.customSyncHandler) {
    try {
      chartState.custom.timeScale().unsubscribeVisibleLogicalRangeChange(chartState.customSyncHandler);
    } catch (e) {}
    chartState.customSyncHandler = null;
  }

  for (const key of ['price', 'volume', 'custom']) {
    if (chartState[key]) {
      try { chartState[key].remove(); } catch (e) {}
      chartState[key] = null;
    }
  }
  if (chartState.chart3D) {
    chartState.chart3D.destroy();
    chartState.chart3D = null;
  }
  if (chartState.chart3DSub) {
    chartState.chart3DSub.destroy();
    chartState.chart3DSub = null;
  }
  if (chartState.dynamicPanes && chartState.dynamicPanes.length) {
    chartState.dynamicPanes.forEach((paneObj) => {
      try {
        if (paneObj.chart && paneObj.syncHandler) {
          paneObj.chart.timeScale().unsubscribeVisibleLogicalRangeChange(paneObj.syncHandler);
        }
      } catch (e) {}
      try { paneObj.chart.remove(); } catch (e) {}
    });
  }
  chartState.dynamicPanes = [];

  const paneContainer = $('paneContainer');
  if (paneContainer) {
    paneContainer.innerHTML = '';
  }
  chartState.technicalMarkerApi = null;
  chartState.eventMarkerApi = null;
  chartState.candleSeries = null;
  chartState.eventMarkerSeries = null;
  chartState.currentMarkers = [];
  chartState.eventMarkers = [];
  chartState.events = [];
  chartState.eventByTime = new Map();
  chartState.lazyHistoryArmed = false;
  if (chartState.eventTooltip) chartState.eventTooltip.classList.add('hidden');
  chartState.priceSeries = {};
  chartState.priceData = {};
  chartState.dynamicPriceSeries = [];
  chartState.dynamicPaneSeries = [];
}

function localizeMarkers(markers) {
  if (!Array.isArray(markers)) return [];
  return markers.map((marker) => {
    if (currentLang !== 'zh' || !marker.text) return marker;
    return {
      ...marker,
      text: String(marker.text).replace('BUY', '买入').replace('SELL', '卖出'),
    };
  });
}

function getIndicatorLabel(item) {
  if (!item) return '';
  return currentLang === 'zh' ? (item.nameZh || item.nameEn || item.key) : (item.nameEn || item.nameZh || item.key);
}

function getIndicatorGroups() {
  const catalog = lastPayload?.indicatorCatalog || [];
  return {
    main: catalog.filter((item) => item.pane === 'price'),
    sub: catalog.filter((item) => item.pane !== 'price'),
  };
}

function renderIndicatorCatalog() {
  const container = $('indicatorCatalog');
  if (!container) return;
  const groups = getIndicatorGroups();

  function renderGroup(title, items) {
    if (!items.length) return '';
    const rows = items.map((item) => {
      const active = activeIndicators.has(item.key);
      const star = item.favorite ? '★' : '';
      const label = getIndicatorLabel(item);
      const note = item.note ? `<div class="indicator-note">${escapeHtml(item.note)}</div>` : '';
      return `
        <button class="indicator-row ${active ? 'active' : ''}" type="button" data-indicator-key="${escapeHtml(item.key)}" title="${escapeHtml(label)}">
          <span class="indicator-star">${star}</span>
          <span class="indicator-row-name">${escapeHtml(label)}</span>
          <span class="indicator-add">${active ? '−' : '+'}</span>
        </button>
        ${active && item.note ? note : ''}
      `;
    }).join('');
    return `
      <section class="indicator-category">
        <div class="indicator-category-title"><span>${escapeHtml(title)}</span><span>⌄</span></div>
        <div class="indicator-list">${rows}</div>
      </section>
    `;
  }

  container.innerHTML =
    renderGroup(currentLang === 'zh' ? '主图指标' : 'Main chart indicators', groups.main) +
    renderGroup(currentLang === 'zh' ? '副图指标' : 'Sub-chart indicators', groups.sub);

  container.querySelectorAll('[data-indicator-key]').forEach((button) => {
    button.addEventListener('click', () => toggleIndicator(button.dataset.indicatorKey));
  });
}

function toggleIndicator(key) {
  if (!key) return;

  if (activeIndicators.has(key)) activeIndicators.delete(key);
  else activeIndicators.add(key);

  renderIndicatorCatalog();
  renderActiveIndicators();
  syncLegacyIndicatorCharts();
  syncIndicatorPanelState();
  scheduleReportActiveIndicators();
}

function clearDynamicIndicators() {
  activeIndicators.clear();
  renderIndicatorCatalog();
  renderActiveIndicators();
  syncLegacyIndicatorCharts();
  syncIndicatorPanelState();
  scheduleReportActiveIndicators();
}

function setIndicatorActiveFromExternal(key, enabled) {
  if (!key) return;

  if (enabled) activeIndicators.add(key);
  else activeIndicators.delete(key);

  renderIndicatorCatalog();
  renderActiveIndicators();
  syncLegacyIndicatorCharts();
  syncIndicatorPanelState();
  scheduleReportActiveIndicators();
}

// ---------------------------------------------------------------------------
// Frontend → backend state reporting (so CLI `stock active` knows real UI state)
// ---------------------------------------------------------------------------
let _reportTimer = null;

function collectActiveIndicatorState() {
  // Collect header button states
  const isVolActive = activeIndicators.has('volume') || activeIndicators.has('vol') || activeIndicators.has('amount');
  const isMAActive = !!(indicatorState.sma20 || indicatorState.sma50);
  const isEventsActive = indicatorState.events !== false;
  // Collect catalog keys (exclude volume/vol/amount pseudo-keys)
  const activeKeys = [];
  activeIndicators.forEach(function(k) {
    const lower = k.trim().toLowerCase();
    if (lower !== 'volume' && lower !== 'vol' && lower !== 'amount') {
      activeKeys.push(k);
    }
  });

  return {
    volume: isVolActive,
    ma: isMAActive,
    events: isEventsActive,
    activeKeys: activeKeys,
  };
}

function applyPersistedIndicatorState(state) {
  if (!state || !state.lastReportedAt) return false;

  activeIndicators.clear();
  const keys = Array.isArray(state.activeKeys) ? state.activeKeys : [];
  keys.forEach((key) => {
    const clean = String(key || '').trim().toLowerCase();
    if (clean) activeIndicators.add(clean);
  });

  if (state.volume) activeIndicators.add('volume');
  else activeIndicators.delete('volume');

  const maActive = !!state.ma;
  indicatorState.sma20 = maActive;
  indicatorState.sma50 = maActive;
  indicatorState.events = state.events !== false;

  return true;
}

async function loadPersistedIndicatorState() {
  try {
    const resp = await fetch(`${API_BASE}/api/indicator/active`);
    if (!resp.ok) return false;
    const state = await resp.json();
    return applyPersistedIndicatorState(state);
  } catch (_) {
    return false;
  }
}

function scheduleReportActiveIndicators() {
  if (_reportTimer) clearTimeout(_reportTimer);
  _reportTimer = setTimeout(reportActiveIndicators, 400);
}

async function reportActiveIndicators() {
  try {
    const state = collectActiveIndicatorState();
    await fetch(API_BASE + '/api/indicator/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state),
    });
  } catch (_) {
    // Best-effort; server may not be ready.
  }
}

function syncIndicatorPanelState() {
  const frame = document.getElementById('indicatorPanelFrame');
  if (!frame || !frame.contentWindow || typeof activeIndicators === 'undefined') return;
  frame.contentWindow.postMessage({
    type: 'stock-indicator-state',
    activeKeys: Array.from(activeIndicators),
  }, window.location.origin);
}

window.addEventListener('message', (event) => {
  const frame = document.getElementById('indicatorPanelFrame');
  if (!frame || event.source !== frame.contentWindow) return;
  if (event.origin !== window.location.origin) return;
  const data = event.data || {};
  if (data.type !== 'stock-indicator-toggle') return;
  setIndicatorActiveFromExternal(data.key, !!data.enabled);
});

function seriesDataHasPoints(seriesDef) {
  return Array.isArray(seriesDef?.data) && seriesDef.data.length > 0;
}

function addIndicatorSeries(chart, seriesDef, options = {}) {
  if (!chart || !seriesDataHasPoints(seriesDef)) return null;
  const fusionOverlay = options.fusionOverlay ?? true;
  const paneKey = options.paneKey || '';
  const isVolume = paneKey === 'volume' || seriesDef.key === 'volume';

  const chartOpts = {
    color: seriesDef.color || '#38bdf8',
    lineWidth: seriesDef.type === 'histogram' ? undefined : 2,
    title: seriesDef.name || '',
  };
  let series;
  if (seriesDef.type === 'histogram') {
    if (isVolume) {
      series = addHistogram(chart, {
        priceScaleId: fusionOverlay ? 'volume-scale' : '',
        priceFormat: { type: 'volume' },
      });
      if (fusionOverlay) {
        chart.priceScale('volume-scale').applyOptions({
          scaleMargins: { top: 0.8, bottom: 0 },
          borderVisible: false,
        });
      }
    } else {
      series = addHistogram(chart, { priceFormat: { type: 'price', precision: 3, minMove: 0.001 } });
    }
  } else {
    series = addLine(chart, chartOpts);
  }
  series.setData(seriesDef.data || []);
  return series;
}

function removeDynamicSeries() {
  if (chartState.price) {
    chartState.dynamicPriceSeries.forEach((series) => {
      try { chartState.price.removeSeries(series); } catch (error) {}
    });
  }
  chartState.dynamicPriceSeries = [];

  if (chartState.custom) {
    if (chartState.customSyncHandler) {
      try {
        chartState.custom.timeScale().unsubscribeVisibleLogicalRangeChange(chartState.customSyncHandler);
      } catch (e) {}
      chartState.customSyncHandler = null;
    }
    try { chartState.custom.remove(); } catch (e) {}
    chartState.custom = null;
  }
  chartState.dynamicPaneSeries = [];

  if (chartState.dynamicPanes && chartState.dynamicPanes.length) {
    chartState.dynamicPanes.forEach((paneObj) => {
      try {
        if (paneObj.chart && paneObj.syncHandler) {
          paneObj.chart.timeScale().unsubscribeVisibleLogicalRangeChange(paneObj.syncHandler);
        }
      } catch (e) {}
      try { paneObj.chart.remove(); } catch (e) {}
    });
  }
  chartState.dynamicPanes = [];

  const paneContainer = $('paneContainer');
  if (paneContainer) {
    paneContainer.innerHTML = '';
  }
}

function renderActiveIndicators() {
  if (!lastPayload) return;
  removeDynamicSeries();

  const indicators = lastPayload.indicators || {};
  const activeItems = [...activeIndicators]
    .map((key) => {
      const lowerKey = key.trim().toLowerCase();
      if (lowerKey === 'volume' || lowerKey === 'vol' || lowerKey === 'amount') {
        return {
          key: 'volume',
          nameZh: '成交量',
          nameEn: 'Volume',
          pane: 'pane',
          series: [
            {
              key: 'volume',
              type: 'histogram',
              name: currentLang === 'zh' ? '成交量' : 'Volume',
              color: 'rgba(34, 197, 94, 0.45)',
              data: lastPayload.volume || []
            }
          ]
        };
      }
      return indicators[key];
    })
    .filter(Boolean);

  const priceItems = activeItems.filter((item) => item.pane === 'price');
  let paneItems = activeItems.filter((item) => item.pane !== 'price');

  priceItems.forEach((item) => {
    (item.series || []).forEach((seriesDef) => {
      const series = addIndicatorSeries(chartState.price, seriesDef, { fusionOverlay: false, paneKey: item.key });
      if (series) chartState.dynamicPriceSeries.push(series);
    });
  });

  if (chartState.chart3D && chartState.chart3D.updatePriceIndicators) {
    chartState.chart3D.updatePriceIndicators(priceItems);
  }

  const customPanel = $('customIndicatorPanel');
  const customTitle = $('customIndicatorTitle');
  const customEl = $('customIndicatorChart');
  const customEl3d = $('customIndicatorChart3d');
  const paneContainer = $('paneContainer');

  if (!customPanel || !customEl) return;

  // Filter out volume from 3D sub-chart in non-fusion mode (since volume is in main chart)
  if (chartState.currentChartViewMode === '3d' && !chartState.fusion3D) {
    paneItems = paneItems.filter((item) => item.key !== 'volume');
  }

  if (!paneItems.length) {
    customPanel.classList.add('hidden');
    if (paneContainer) {
      paneContainer.innerHTML = '';
    }
    // Clear fused indicators in case they were active
    if (chartState.chart3D && chartState.chart3D.updateFusedIndicators) {
      chartState.chart3D.updateFusedIndicators([]);
    }
    if (chartState.chart3DSub) {
      chartState.chart3DSub.destroy();
      chartState.chart3DSub = null;
    }
    updateSyncRegistry();
    return;
  }

  if (chartState.currentChartViewMode === '3d') {
    // 3D Mode: Clear 2D separate cards/custom chart
    if (paneContainer) {
      paneContainer.innerHTML = '';
    }
    if (chartState.custom) {
      if (chartState.customSyncHandler) {
        try {
          chartState.custom.timeScale().unsubscribeVisibleLogicalRangeChange(chartState.customSyncHandler);
        } catch (e) {}
          chartState.customSyncHandler = null;
      }
      chartState.custom.remove();
      chartState.custom = null;
    }
    chartState.dynamicPaneSeries = [];

    if (chartState.fusion3D) {
      // 3D Fusion Mode
      customPanel.classList.add('hidden');
      if (customEl3d) {
        customEl3d.classList.add('hidden');
      }
      if (chartState.chart3D && chartState.chart3D.updateFusedIndicators) {
        chartState.chart3D.updateFusedIndicators(paneItems);
      }
      if (chartState.chart3DSub) {
        chartState.chart3DSub.destroy();
        chartState.chart3DSub = null;
      }
    } else {
      // 3D Non-Fusion Mode
      customPanel.classList.remove('hidden');
      if (customTitle) {
        customTitle.textContent = paneItems.map(getIndicatorLabel).join(' / ');
      }
      if (chartState.chart3D && chartState.chart3D.updateFusedIndicators) {
        chartState.chart3D.updateFusedIndicators([]);
      }

      customEl.classList.add('hidden');
      if (customEl3d) {
        customEl3d.classList.remove('hidden');
        if (chartState.chart3DSub) {
          chartState.chart3DSub.updatePaneIndicators(paneItems);
        } else {
          if (window.Stock3DSubChart) {
            chartState.chart3DSub = new window.Stock3DSubChart(customEl3d, lastPayload, paneItems);
          }
        }
      }
    }
  } else {
    // 2D Mode: Clear 3D instances
    if (chartState.chart3DSub) {
      chartState.chart3DSub.destroy();
      chartState.chart3DSub = null;
    }
    if (chartState.chart3D && chartState.chart3D.updateFusedIndicators) {
      chartState.chart3D.updateFusedIndicators([]);
    }

    if (chartState.fusion2D) {
      // 2D Fusion Mode: Overlay in customIndicatorPanel
      customPanel.classList.remove('hidden');
      if (customTitle) {
        customTitle.textContent = paneItems.map(getIndicatorLabel).join(' / ');
      }
      if (customEl3d) {
        customEl3d.classList.add('hidden');
      }
      customEl.classList.remove('hidden');
      if (paneContainer) {
        paneContainer.innerHTML = '';
      }

      chartState.custom = LightweightCharts.createChart(customEl, baseChartOptions(customEl.clientHeight));

      const customSyncHandler = (range) => handleTimeScaleChange(range, chartState.custom);
      chartState.custom.timeScale().subscribeVisibleLogicalRangeChange(customSyncHandler);
      chartState.customSyncHandler = customSyncHandler;

      paneItems.forEach((item) => {
        (item.series || []).forEach((seriesDef) => {
          const series = addIndicatorSeries(chartState.custom, seriesDef, { fusionOverlay: true, paneKey: item.key });
          if (series) chartState.dynamicPaneSeries.push(series);
        });
      });
      chartState.custom.timeScale().fitContent();

      if (!customEl.__resizeAttached) {
        const observer = new ResizeObserver(() => {
          if (chartState.custom) chartState.custom.applyOptions({ width: customEl.clientWidth, height: customEl.clientHeight });
        });
        observer.observe(customEl);
        customEl.__resizeAttached = true;
      }
    } else {
      // 2D Non-Fusion Mode: Separate cards in paneContainer
      customPanel.classList.add('hidden');
      if (customEl3d) {
        customEl3d.classList.add('hidden');
      }
      customEl.classList.add('hidden');
      if (chartState.custom) {
        if (chartState.customSyncHandler) {
          try {
            chartState.custom.timeScale().unsubscribeVisibleLogicalRangeChange(chartState.customSyncHandler);
          } catch (e) {}
          chartState.customSyncHandler = null;
        }
        chartState.custom.remove();
        chartState.custom = null;
      }

      if (paneContainer) {
        paneContainer.innerHTML = '';
        paneItems.forEach((item) => {
          const card = document.createElement('section');
          card.className = 'chart-panel';
          card.style.width = '100%';

          const header = document.createElement('div');
          header.className = 'panel-header small';
          const title = document.createElement('h3');
          title.textContent = getIndicatorLabel(item);
          header.appendChild(title);
          card.appendChild(header);

          const chartEl = document.createElement('div');
          chartEl.className = 'chart';
          chartEl.style.height = '260px';
          card.appendChild(chartEl);

          paneContainer.appendChild(card);

          const subChart = LightweightCharts.createChart(chartEl, baseChartOptions(260));

          const syncHandler = (range) => handleTimeScaleChange(range, subChart);
          subChart.timeScale().subscribeVisibleLogicalRangeChange(syncHandler);

          (item.series || []).forEach((seriesDef) => {
            const series = addIndicatorSeries(subChart, seriesDef, { fusionOverlay: false, paneKey: item.key });
            if (series) chartState.dynamicPaneSeries.push(series);
          });
          subChart.timeScale().fitContent();

          const observer = new ResizeObserver(() => {
            subChart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight });
          });
          observer.observe(chartEl);
          chartEl.__resizeObserver = observer;

          chartState.dynamicPanes.push({
            chart: subChart,
            syncHandler: syncHandler
          });
        });
      }
    }
  }

  // Update sync registry for the newly active charts
  updateSyncRegistry();
}


function normalizeChartTimeKey(time) {
  if (typeof time === 'string' || typeof time === 'number') return String(time);
  if (time && typeof time === 'object') {
    const y = time.year ?? time.y;
    const m = time.month ?? time.m;
    const d = time.day ?? time.d;
    if (y && m && d) return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }
  return '';
}

function getEventDisplayLabel(event) {
  if (!event) return currentLang === 'zh' ? '事件' : 'Event';
  if (event.type === 'earnings') return currentLang === 'zh' ? '财报发布' : 'Earnings';
  if (event.type === 'dividend') return currentLang === 'zh' ? '权息变动' : 'Dividend / Corporate Action';
  return event.label || (currentLang === 'zh' ? '事件' : 'Event');
}

function buildEventTimeMap(payload) {
  const eventsById = new Map((payload.events || []).map((event) => [event.id, event]));
  const map = new Map();
  (payload.eventMarkers || []).forEach((marker) => {
    const key = normalizeChartTimeKey(marker.time);
    if (!key) return;
    const event = eventsById.get(marker.eventId) || {
      id: marker.eventId,
      type: marker.eventType,
      date: marker.eventDate,
      label: marker.eventLabel,
      title: marker.eventLabel,
    };
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(event);
  });
  return map;
}

function ensureEventTooltip(container) {
  if (!container) return null;
  let tooltip = container.querySelector('.chart-event-tooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.className = 'chart-event-tooltip hidden';
    container.appendChild(tooltip);
  }
  chartState.eventTooltip = tooltip;
  return tooltip;
}

function renderEventTooltipHtml(events) {
  return events.map((event) => {
    const label = getEventDisplayLabel(event);
    const title = event.title && event.title !== label ? event.title : label;
    const detail = event.detail ? `<div class="event-tooltip-detail">${escapeHtml(event.detail)}</div>` : '';
    const source = event.source ? `<span class="event-tooltip-source">${escapeHtml(event.source)}</span>` : '';
    const date = event.date ? `<span>${escapeHtml(event.date)}</span>` : '';
    const typeClass = event.type === 'earnings' ? 'earnings' : 'dividend';
    return `
      <div class="event-tooltip-item ${typeClass}">
        <div class="event-tooltip-title"><span class="event-tooltip-dot"></span>${escapeHtml(title)}</div>
        <div class="event-tooltip-meta">${date}${source}</div>
        ${detail}
      </div>
    `;
  }).join('');
}

function attachEventTooltip(chart, container, payload) {
  if (!chart || !container) return;
  chartState.events = payload.events || [];
  chartState.eventByTime = buildEventTimeMap(payload);
  const tooltip = ensureEventTooltip(container);
  if (!tooltip) return;

  chart.subscribeCrosshairMove((param) => {
    const key = normalizeChartTimeKey(param?.time);
    const events = key ? chartState.eventByTime.get(key) : null;

    const priceEl = $('priceChart');
    if (priceEl) {
      if (events && events.length && param?.point) {
        priceEl.style.cursor = 'pointer';
      } else {
        priceEl.style.cursor = '';
      }
    }

    if (!events || !events.length || !param?.point) {
      tooltip.classList.add('hidden');
      return;
    }

    tooltip.innerHTML = renderEventTooltipHtml(events);
    tooltip.classList.remove('hidden');

    const width = tooltip.offsetWidth || 260;
    const height = tooltip.offsetHeight || 120;
    const left = Math.max(8, Math.min(param.point.x + 14, container.clientWidth - width - 8));
    const top = Math.max(8, Math.min(param.point.y + 14, container.clientHeight - height - 8));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  });
}

function clearTradePlanOverlays() {
  const series = chartState.priceSeries?.candles;
  if (series && Array.isArray(chartState.tradePlanLines)) {
    chartState.tradePlanLines.forEach((line) => {
      try { series.removePriceLine(line); } catch (_) {}
    });
  }
  chartState.tradePlanLines = [];
  if (chartState.chart3D?.updateTradePlan) chartState.chart3D.updateTradePlan(null);
}

function addTradePlanPriceLine(price, title, color, lineStyle = 2) {
  const series = chartState.priceSeries?.candles;
  if (!series || !Number.isFinite(Number(price))) return;
  try {
    const line = series.createPriceLine({
      price: Number(price), color, lineWidth: 2, lineStyle,
      axisLabelVisible: true, title,
    });
    chartState.tradePlanLines.push(line);
  } catch (_) {}
}

function applyTradePlanOverlays(plan) {
  clearTradePlanOverlays();
  if (!plan || plan.symbol !== lastPayload?.symbol) return;
  const scenario = plan.preferredSide ? plan[plan.preferredSide] : null;
  if (scenario?.entryZone) {
    addTradePlanPriceLine(scenario.entryZone.low, 'ENTRY LOW', '#2dd4bf', 2);
    addTradePlanPriceLine(scenario.entryZone.high, 'ENTRY HIGH', '#2dd4bf', 2);
    addTradePlanPriceLine(scenario.stop, 'STOP', '#ef4444', 0);
    addTradePlanPriceLine(scenario.target1, 'T1', '#f59e0b', 1);
    addTradePlanPriceLine(scenario.target2, 'T2', '#60a5fa', 1);
  }
  addTradePlanPriceLine(plan.levels?.vwap, 'VWAP', '#a78bfa', 2);
  addTradePlanPriceLine(plan.levels?.anchoredVwap, 'AVWAP', '#e879f9', 2);
  if (chartState.chart3D?.updateTradePlan) chartState.chart3D.updateTradePlan(plan);
}

function planPrice(value) {
  if (!Number.isFinite(Number(value))) return '—';
  const number = Number(value);
  return number.toFixed(Math.abs(number) < 10 ? 4 : Math.abs(number) < 100 ? 3 : 2);
}

function tradePlanRegimeLabel(name) {
  return ({ trend_up: '上涨趋势', trend_down: '下跌趋势', range: '震荡市场', transition: '过渡状态' })[name] || '状态不明确';
}

function tradePlanActionLabel(action) {
  return ({ entry_zone_reached: '价格进入观察区', wait_for_pullback: '等待回踩/反抽', wait: '暂不交易' })[action] || '暂不交易';
}

function renderTradeScenario(plan, scenario, side) {
  if (!scenario) return '';
  const preferred = plan.preferredSide === side;
  const title = side === 'long' ? '多头买入计划' : '做空入场计划';
  const reasons = (scenario.noTradeReasons || []).map((reason) => ({
    reward_to_risk_below_1_5: '第一目标扣除成本后盈亏比不足1.5R',
    regime_not_supportive: '当前市场状态不支持该方向',
    price_more_than_2_atr_from_entry: '当前价格距离合理入场区超过2 ATR',
  })[reason] || reason);
  return `
    <article class="trade-scenario ${preferred ? 'preferred' : ''}">
      <h4>${title}${preferred ? ' · 当前优先' : ''} · ${scenario.status === 'ready' ? '可观察' : '不交易'}</h4>
      <div class="trade-level-grid">
        <div class="trade-level"><span>入场区间</span><strong>${planPrice(scenario.entryZone?.low)}–${planPrice(scenario.entryZone?.high)}</strong></div>
        <div class="trade-level"><span>失效止损</span><strong>${planPrice(scenario.stop)}</strong></div>
        <div class="trade-level"><span>第一目标</span><strong>${planPrice(scenario.target1)}</strong></div>
        <div class="trade-level"><span>第二目标</span><strong>${planPrice(scenario.target2)}</strong></div>
        <div class="trade-level"><span>净盈亏比</span><strong>${Number(scenario.riskReward1 || 0).toFixed(2)}R / ${Number(scenario.riskReward2 || 0).toFixed(2)}R</strong></div>
      </div>
      <p class="trade-sources">共振依据：${escapeHtml((scenario.sources || []).join(' + ') || '无足够共振')}</p>
      ${reasons.length ? `<p class="trade-sources">放弃条件：${escapeHtml(reasons.join('；'))}</p>` : ''}
    </article>
  `;
}

function renderTradePlan(plan) {
  const container = $('tradePlanResult');
  if (!container) return;
  container.classList.remove('hidden');
  if (!plan || plan.status === 'insufficient_data') {
    const missing = plan?.dataQuality?.missing || [];
    container.innerHTML = `<div class="trade-plan-error"><strong>不生成点位：</strong>${escapeHtml(plan?.message || '市场数据不足')}<br><span>${escapeHtml(missing.join('、'))}</span></div>`;
    return;
  }
  const quality = plan.dataQuality || {};
  const qualityClass = quality.isStale || quality.status !== 'ready' ? 'danger' : 'ready';
  container.innerHTML = `
    <div class="trade-plan-status-row">
      <span class="trade-plan-badge ${plan.status === 'ready' ? 'ready' : 'warning'}">${tradePlanActionLabel(plan.action)}</span>
      <span class="trade-plan-badge">${tradePlanRegimeLabel(plan.regime?.name)}</span>
      <span class="trade-plan-badge ${qualityClass}">成交量覆盖 ${Math.round(Number(quality.volumeCoverage || 0) * 100)}%</span>
      <span class="trade-plan-badge ${qualityClass}">最新K线 ${escapeHtml(quality.lastBarAt || '未知')}</span>
      <span class="trade-plan-badge">ATR ${planPrice(plan.atr)} · ADX ${Number(plan.regime?.adx || 0).toFixed(1)}</span>
    </div>
    <div class="trade-plan-grid">
      ${renderTradeScenario(plan, plan.long, 'long')}
      ${renderTradeScenario(plan, plan.short, 'short')}
    </div>
    <p class="trade-plan-caveat">成本假设 ${Number(plan.costAssumptionBps || 0).toFixed(1)} bps/单边。Volume Profile 为OHLCV近似；数据源不含实时bid/ask。${escapeHtml(plan.disclaimer || '')}</p>
  `;
}

async function calculateTradePlan() {
  const button = $('calculateTradePlan');
  const symbol = String(lastPayload?.symbol || symbolInput.value || '').trim().toUpperCase();
  const horizon = $('tradePlanHorizon')?.value || 'intraday_short';
  const costBps = Math.max(0, Math.min(100, Number($('tradePlanCost')?.value || 5)));
  if (!symbol || !button) return;
  button.disabled = true;
  button.textContent = '正在校验成交量…';
  const container = $('tradePlanResult');
  if (container) {
    container.classList.remove('hidden');
    container.innerHTML = '<div class="trade-plan-caveat">正在读取多周期真实K线、校验最新成交量并计算点位…</div>';
  }
  try {
    const url = `${API_BASE}/api/trade-plan/${encodeURIComponent(symbol)}?horizon=${encodeURIComponent(horizon)}&cost_bps=${encodeURIComponent(costBps)}`;
    const response = await fetch(url);
    const rawBody = await response.text();
    let plan;
    try { plan = rawBody ? JSON.parse(rawBody) : {}; }
    catch (_) { throw new Error(`服务返回了非 JSON 响应（HTTP ${response.status}）：${rawBody.slice(0, 160) || '无响应内容'}`); }
    if (!response.ok) throw new Error(plan.detail || `HTTP ${response.status}`);
    lastTradePlan = plan;
    renderTradePlan(plan);
    applyTradePlanOverlays(plan);
  } catch (error) {
    lastTradePlan = null;
    clearTradePlanOverlays();
    if (container) container.innerHTML = `<div class="trade-plan-error">计算失败：${escapeHtml(error.message || error)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = '一键计算买卖点';
  }
}

function createCharts(payload) {
  destroyCharts();

  const priceEl = $('priceChart');

  chartState.price = LightweightCharts.createChart(priceEl, baseChartOptions(priceEl.clientHeight));
  chartState.price.subscribeClick((param) => {
    const key = normalizeChartTimeKey(param?.time);
    const events = key ? chartState.eventByTime.get(key) : null;
    if (events && events.length) {
      showEventModal(events);
    }
  });

  const priceSyncHandler = (range) => handleTimeScaleChange(range, chartState.price);
  chartState.price.timeScale().subscribeVisibleLogicalRangeChange(priceSyncHandler);
  chartState.priceSyncHandler = priceSyncHandler;

  chartState.volume = null; 

  const candleSeries = addCandles(chartState.price, {
    upColor: '#22c55e',
    downColor: '#ef4444',
    wickUpColor: '#22c55e',
    wickDownColor: '#ef4444',
    borderVisible: false,
    priceLineColor: '#94a3b8',
  });

  const eventMarkerSeries = addLine(chartState.price, {
    color: 'rgba(0, 0, 0, 0)',
    lineWidth: 1,
    priceScaleId: 'corporate-events',
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
    pointMarkersVisible: false,
    lineVisible: false,
    autoscaleInfoProvider: () => ({
      priceRange: { minValue: 0, maxValue: 1 },
    }),
  });
  if (eventMarkerSeries?.priceScale) {
    try {
      eventMarkerSeries.priceScale().applyOptions({
        visible: false,
        scaleMargins: { top: 0.9, bottom: 0.015 },
      });
    } catch (error) {
      // Ignore if this Lightweight Charts build does not support overlay scale options.
    }
  }
  chartState.eventMarkerSeries = eventMarkerSeries;

  const sma20Series = addLine(chartState.price, { color: '#38bdf8', lineWidth: 2, title: 'SMA20' });
  const sma50Series = addLine(chartState.price, { color: '#f59e0b', lineWidth: 2, title: 'SMA50' });
  const bbUpperSeries = addLine(chartState.price, { color: 'rgba(167, 139, 250, 0.78)', lineWidth: 1, title: t('chartLabels.bbUpper') });
  const bbMiddleSeries = addLine(chartState.price, { color: 'rgba(167, 139, 250, 0.42)', lineWidth: 1, title: t('chartLabels.bbMiddle') });
  const bbLowerSeries = addLine(chartState.price, { color: 'rgba(167, 139, 250, 0.78)', lineWidth: 1, title: t('chartLabels.bbLower') });

  chartState.priceSeries = {
    candles: candleSeries,
    sma20: sma20Series,
    sma50: sma50Series,
    bbUpper: bbUpperSeries,
    bbMiddle: bbMiddleSeries,
    bbLower: bbLowerSeries,
  };

  chartState.priceData = {
    candles: payload.candles || [],
    sma20: payload.sma20 || [],
    sma50: payload.sma50 || [],
    bbUpper: payload.bbUpper || [],
    bbMiddle: payload.bbMiddle || [],
    bbLower: payload.bbLower || [],
  };

  setEventMarkers(payload.eventMarkers || []);
  setMarkers(candleSeries, localizeMarkers(payload.markers || []));
  applyPriceIndicatorVisibility();

  if (chartState.price) {
    chartState.price.timeScale().fitContent();
  }

  updateSyncRegistry();

  attachLazyHistoryLoader(chartState.price);
  window.setTimeout(() => { chartState.lazyHistoryArmed = true; }, 900);
  attachEventTooltip(chartState.price, priceEl, payload);
  attachResizeObservers();
  renderIndicatorCatalog();
  renderActiveIndicators();
  syncLegacyIndicatorCharts();
  syncIndicatorPanelState();
  scheduleReportActiveIndicators();
  if (chartState.currentChartViewMode === '3d') {
    updateChartViewMode('3d');
  }
  applyTradePlanOverlays(lastTradePlan);
}

function getNextExpandedPeriod(currentPeriod) {
  const idx = HISTORY_EXPAND_ORDER.indexOf(currentPeriod);
  if (idx < 0 || idx >= HISTORY_EXPAND_ORDER.length - 1) return null;
  return HISTORY_EXPAND_ORDER[idx + 1];
}

function canLazyLoadDailyHistory() {
  return getSelectedTimeframe().interval === '1d' && !!lastPayload?.symbol && Array.isArray(lastPayload.candles) && lastPayload.candles.length > 30;
}

function scheduleLazyHistoryLoad(range) {
  if (!chartState.lazyHistoryArmed || !canLazyLoadDailyHistory() || lazyHistoryLoading || !range) return;

  // Trigger only when the user zooms/pans beyond the left edge. This avoids the
  // bad behavior of immediately downloading 5Y on first render after fitContent().
  if (Number(range.from) > -8) return;

  const nextPeriod = getNextExpandedPeriod(lastPayload.range || getSelectedTimeframe().period);
  if (!nextPeriod) return;

  window.clearTimeout(lazyHistoryTimer);
  lazyHistoryTimer = window.setTimeout(() => loadExpandedDailyHistory(nextPeriod, range), 180);
}

async function loadExpandedDailyHistory(nextPeriod, oldVisibleRange) {
  if (lazyHistoryLoading || !lastPayload?.symbol) return;
  lazyHistoryLoading = true;

  const oldPayload = lastPayload;
  const oldCount = oldPayload.candles?.length || 0;
  const clean = String(oldPayload.symbol || symbolInput.value || '').trim().toUpperCase();

  try {
    showStatus(currentLang === 'zh' ? `正在加载更早日线数据（${nextPeriod}）...` : `Loading older daily candles (${nextPeriod})...`);
    const url = `${API_BASE}/api/analyze/${encodeURIComponent(clean)}?period=${encodeURIComponent(nextPeriod)}&interval=1d`;
    const response = await fetch(url);
    const rawBody = await response.text();
    let payload;
    try {
      payload = rawBody ? JSON.parse(rawBody) : {};
    } catch (_) {
      throw new Error(`服务返回了非 JSON 响应（HTTP ${response.status}）：${rawBody.slice(0, 160) || '无响应内容'}`);
    }
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);

    const newCount = payload.candles?.length || 0;
    if (newCount <= oldCount) {
      hideStatus();
      return;
    }

    lastPayload = payload;
    updateSummary(payload);
    createCharts(payload);

    const added = newCount - oldCount;
    const restoredRange = {
      from: Math.max(0, Math.floor(Number(oldVisibleRange?.from ?? 0))),
      to: Math.min(newCount + 5, Math.ceil(Number(oldVisibleRange?.to ?? oldCount) + added)),
    };
    requestAnimationFrame(() => {
      try {
        chartState.price?.timeScale().setVisibleLogicalRange(restoredRange);
      } catch (error) {
        chartState.price?.timeScale().fitContent();
      }
    });
    hideStatus();
  } catch (error) {
    console.warn('Lazy history load failed:', error);
    hideStatus();
  } finally {
    lazyHistoryLoading = false;
  }
}

function attachLazyHistoryLoader(chart) {
  if (!chart || chart.__lazyHistoryAttached) return;
  chart.__lazyHistoryAttached = true;
  chart.timeScale().subscribeVisibleLogicalRangeChange((range) => scheduleLazyHistoryLoad(range));
}

let activeChartsToSync = [];
let syncingCharts = false;

function handleTimeScaleChange(range, sourceChart) {
  if (syncingCharts || !range) return;
  syncingCharts = true;
  activeChartsToSync.forEach((otherChart) => {
    if (otherChart !== sourceChart) {
      try {
        otherChart.timeScale().setVisibleLogicalRange(range);
      } catch (e) {}
    }
  });
  syncingCharts = false;
}

function updateSyncRegistry() {
  activeChartsToSync = [];
  if (chartState.currentChartViewMode === '3d') {
    return;
  }
  if (chartState.price) {
    activeChartsToSync.push(chartState.price);
  }
  if (chartState.fusion2D) {
    if (chartState.custom) {
      activeChartsToSync.push(chartState.custom);
    }
  } else {
    if (chartState.dynamicPanes && chartState.dynamicPanes.length) {
      chartState.dynamicPanes.forEach((paneObj) => {
        if (paneObj.chart) {
          activeChartsToSync.push(paneObj.chart);
        }
      });
    }
  }
}

function syncCharts(charts) {
  // Legacy compatibility stub
}

function attachResizeObservers() {
  const chartPairs = [
    ['priceChart', chartState.price],
  ];

  chartPairs.forEach(([id, chart]) => {
    const el = $(id);
    if (!el || !chart || el.__resizeAttached) return;
    const observer = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    });
    observer.observe(el);
    el.__resizeAttached = true;
  });
}

function formatCurrency(value, currency = 'USD') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return new Intl.NumberFormat(currentLang === 'zh' ? 'zh-CN' : 'en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function formatPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(2)}%`;
}

function isMissingStat(value) {
  return value === null || value === undefined || value === '' || Number.isNaN(Number(value));
}

function formatPlainNumber(value, digits = 2) {
  if (isMissingStat(value)) return '—';
  return Number(value).toLocaleString(currentLang === 'zh' ? 'zh-CN' : 'en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatQuotePrice(value) {
  if (isMissingStat(value)) return '—';
  return Number(value).toLocaleString(currentLang === 'zh' ? 'zh-CN' : 'en-US', {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  });
}

function formatLargeStat(value) {
  if (isMissingStat(value)) return '—';
  const number = Number(value);
  const abs = Math.abs(number);
  if (currentLang === 'zh') {
    if (abs >= 1e12) return `${formatPlainNumber(number / 1e12, 2)}万亿`;
    if (abs >= 1e8) return `${formatPlainNumber(number / 1e8, 2)}亿`;
    if (abs >= 1e4) return `${formatPlainNumber(number / 1e4, 2)}万`;
    return formatPlainNumber(number, 0);
  }
  if (abs >= 1e12) return `${formatPlainNumber(number / 1e12, 2)}T`;
  if (abs >= 1e9) return `${formatPlainNumber(number / 1e9, 2)}B`;
  if (abs >= 1e6) return `${formatPlainNumber(number / 1e6, 2)}M`;
  if (abs >= 1e3) return `${formatPlainNumber(number / 1e3, 2)}K`;
  return formatPlainNumber(number, 0);
}

function formatPeStat(value, epsValue) {
  if (!isMissingStat(value) && Number(value) > 0) return formatPlainNumber(value, 2);
  if (!isMissingStat(epsValue) && Number(epsValue) <= 0) {
    return currentLang === 'zh' ? '亏损' : 'Loss';
  }
  return '—';
}

function formatQuoteStatValue(item, stats) {
  const value = stats ? stats[item.key] : null;
  if (item.format === 'price') return formatQuotePrice(value);
  if (item.format === 'volume' || item.format === 'large' || item.format === 'shares') return formatLargeStat(value);
  if (item.format === 'percent') return isMissingStat(value) ? '—' : `${formatPlainNumber(value, 2)}%`;
  if (item.format === 'ratio') return formatPlainNumber(value, 2);
  if (item.format === 'pe') return formatPeStat(value, item.epsKey ? stats?.[item.epsKey] : null);
  if (item.format === 'lot') return isMissingStat(value) ? '—' : `${formatPlainNumber(value, 0)}${currentLang === 'zh' ? '股' : ''}`;
  return isMissingStat(value) ? '—' : String(value);
}

function quoteStatToneClass(item, stats) {
  const value = stats ? stats[item.key] : null;
  if (isMissingStat(value)) return 'muted';
  if (item.tone === 'up') return 'positive';
  if (item.tone === 'down') return 'negative';
  return '';
}

function renderQuoteStats(payload) {
  const grid = $('quoteStatsGrid');
  if (!grid) return;
  const stats = payload?.quoteStats || {};
  grid.innerHTML = QUOTE_STAT_ITEMS.map((item) => {
    const label = currentLang === 'zh' ? item.zh : item.en;
    const value = formatQuoteStatValue(item, stats);
    const tone = quoteStatToneClass(item, stats);
    return `
      <div class="quote-stat-item" data-stat-key="${escapeHtml(item.key)}">
        <span class="quote-stat-label">${escapeHtml(label)}</span>
        <span class="quote-stat-value ${tone}">${escapeHtml(value)}</span>
      </div>
    `;
  }).join('');
}

function prettifySignal(signal) {
  return I18N[currentLang].signals[signal] || signal || '—';
}

function translateReason(reason) {
  return I18N[currentLang].reasons[reason] || reason;
}

function latestPoint(series) {
  return Array.isArray(series) && series.length ? series[series.length - 1] : null;
}

function previousPoint(series) {
  return Array.isArray(series) && series.length >= 2 ? series[series.length - 2] : null;
}

function averageLast(series, count = 20) {
  if (!Array.isArray(series) || !series.length) return null;
  const slice = series.slice(Math.max(0, series.length - count));
  const values = slice.map((item) => Number(item.value)).filter((value) => Number.isFinite(value));
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function classifyReading(kind) {
  if (kind === 'bullish') return 'positive';
  if (kind === 'bearish') return 'negative';
  if (kind === 'warning') return 'warning';
  return 'neutral';
}

function makeReading(title, body, kind = 'neutral') {
  return `<article class="reading-item ${classifyReading(kind)}"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(body)}</span></article>`;
}

function histogramDirection(h, hp) {
  if (!Number.isFinite(h) || !Number.isFinite(hp)) return t('readings.histUnknown');
  if (h > hp) return t('readings.histExpanding');
  if (h < hp) return t('readings.histContracting');
  return t('readings.histFlat');
}

function updateIndicatorReading(payload) {
  const container = $('indicatorReading');
  if (!container) return;

  const candles = payload.candles || [];
  const lastCandle = latestPoint(candles);
  const prevCandle = previousPoint(candles);
  const close = lastCandle ? Number(lastCandle.close) : null;
  const readings = [];

  const lastRsi = latestPoint(payload.rsi);
  if (lastRsi && Number.isFinite(Number(lastRsi.value))) {
    const rsi = Number(lastRsi.value);
    const rsiText = rsi.toFixed(1);
    if (rsi >= 70) {
      readings.push(makeReading(t('readings.rsiOverboughtTitle'), t('readings.rsiOverboughtBody', { rsi: rsiText }), 'warning'));
    } else if (rsi <= 30) {
      readings.push(makeReading(t('readings.rsiOversoldTitle'), t('readings.rsiOversoldBody', { rsi: rsiText }), 'bullish'));
    } else if (rsi >= 55) {
      readings.push(makeReading(t('readings.rsiBullTitle'), t('readings.rsiBullBody', { rsi: rsiText }), 'bullish'));
    } else if (rsi <= 45) {
      readings.push(makeReading(t('readings.rsiBearTitle'), t('readings.rsiBearBody', { rsi: rsiText }), 'bearish'));
    } else {
      readings.push(makeReading(t('readings.rsiNeutralTitle'), t('readings.rsiNeutralBody', { rsi: rsiText }), 'neutral'));
    }
  }

  const macd = latestPoint(payload.macd);
  const macdPrev = previousPoint(payload.macd);
  const signal = latestPoint(payload.macdSignal);
  const signalPrev = previousPoint(payload.macdSignal);
  const hist = latestPoint(payload.macdHist);
  const histPrev = previousPoint(payload.macdHist);
  if (macd && macdPrev && signal && signalPrev) {
    const m = Number(macd.value);
    const mp = Number(macdPrev.value);
    const s = Number(signal.value);
    const sp = Number(signalPrev.value);
    const h = hist ? Number(hist.value) : m - s;
    const hp = histPrev ? Number(histPrev.value) : mp - sp;
    const direction = histogramDirection(h, hp);

    if (mp <= sp && m > s) {
      readings.push(makeReading(t('readings.macdBullCrossTitle'), t('readings.macdBullCrossBody', { direction }), 'bullish'));
    } else if (mp >= sp && m < s) {
      readings.push(makeReading(t('readings.macdBearCrossTitle'), t('readings.macdBearCrossBody', { direction }), 'bearish'));
    } else if (m > s) {
      readings.push(makeReading(t('readings.macdPositiveTitle'), t('readings.macdPositiveBody', { direction }), 'bullish'));
    } else if (m < s) {
      readings.push(makeReading(t('readings.macdNegativeTitle'), t('readings.macdNegativeBody', { direction }), 'bearish'));
    } else {
      readings.push(makeReading(t('readings.macdNeutralTitle'), t('readings.macdNeutralBody'), 'neutral'));
    }
  }

  const upper = latestPoint(payload.bbUpper);
  const middle = latestPoint(payload.bbMiddle);
  const lower = latestPoint(payload.bbLower);
  if (lastCandle && upper && middle && lower && Number.isFinite(close)) {
    const u = Number(upper.value);
    const mid = Number(middle.value);
    const l = Number(lower.value);
    const bandWidth = u - l;
    const bandPos = bandWidth > 0 ? (close - l) / bandWidth : 0.5;

    if (close >= u) {
      readings.push(makeReading(t('readings.bbUpperTitle'), t('readings.bbUpperBody'), 'warning'));
    } else if (close <= l) {
      readings.push(makeReading(t('readings.bbLowerTitle'), t('readings.bbLowerBody'), 'warning'));
    } else if (bandPos >= 0.75) {
      readings.push(makeReading(t('readings.bbStrongTitle'), t('readings.bbStrongBody', { pct: Math.round((1 - bandPos) * 100) }), 'bullish'));
    } else if (bandPos <= 0.25) {
      readings.push(makeReading(t('readings.bbWeakTitle'), t('readings.bbWeakBody'), 'bearish'));
    } else if (close >= mid) {
      readings.push(makeReading(t('readings.bbMidSupportTitle'), t('readings.bbMidSupportBody'), 'bullish'));
    } else {
      readings.push(makeReading(t('readings.bbMidResistTitle'), t('readings.bbMidResistBody'), 'bearish'));
    }
  }

  const sma20 = latestPoint(payload.sma20);
  const sma50 = latestPoint(payload.sma50);
  if (lastCandle && sma20 && sma50 && Number.isFinite(close)) {
    const s20 = Number(sma20.value);
    const s50 = Number(sma50.value);
    if (s20 > s50 && close > s20) {
      readings.push(makeReading(t('readings.trendSupportTitle'), t('readings.trendSupportBody'), 'bullish'));
    } else if (s20 > s50 && close < s20) {
      readings.push(makeReading(t('readings.trendPullbackTitle'), t('readings.trendPullbackBody'), 'warning'));
    } else if (s20 < s50 && close < s20) {
      readings.push(makeReading(t('readings.trendPressureTitle'), t('readings.trendPressureBody'), 'bearish'));
    } else {
      readings.push(makeReading(t('readings.trendMixedTitle'), t('readings.trendMixedBody'), 'neutral'));
    }
  }

  const lastVolume = latestPoint(payload.volume);
  const avgVolume = averageLast(payload.volume, 20);
  if (lastVolume && avgVolume && lastCandle && prevCandle) {
    const volRatio = Number(lastVolume.value) / avgVolume;
    const ratioText = volRatio.toFixed(1);
    const candleUp = Number(lastCandle.close) >= Number(lastCandle.open);
    if (volRatio >= 1.25 && candleUp) {
      readings.push(makeReading(t('readings.volumeGreenTitle'), t('readings.volumeGreenBody', { ratio: ratioText }), 'bullish'));
    } else if (volRatio >= 1.25 && !candleUp) {
      readings.push(makeReading(t('readings.volumeRedTitle'), t('readings.volumeRedBody', { ratio: ratioText }), 'bearish'));
    } else {
      readings.push(makeReading(t('readings.volumeNormalTitle'), t('readings.volumeNormalBody', { ratio: ratioText }), 'neutral'));
    }
  }

  const score = payload.score || {};
  if (score.signal) {
    const buy = Number(score.buy || 0);
    const sell = Number(score.sell || 0);
    if (buy >= 60 && buy > sell) {
      readings.unshift(makeReading(t('readings.overallTitle'), t('readings.overallBuyBody', { buy, sell }), 'bullish'));
    } else if (sell >= 60 && sell > buy) {
      readings.unshift(makeReading(t('readings.overallTitle'), t('readings.overallSellBody', { buy, sell }), 'bearish'));
    } else {
      readings.unshift(makeReading(t('readings.overallTitle'), t('readings.overallNeutralBody', { buy, sell }), 'neutral'));
    }
  }

  if (!readings.length) {
    readings.push(makeReading(t('readings.needMoreTitle'), t('readings.needMoreBody'), 'neutral'));
  }

  container.innerHTML = readings.slice(0, 7).join('');
}

function updateSummary(payload) {
  const title = `${payload.symbol || ''} ${payload.name ? '· ' + payload.name : ''}`.trim();
  $('stockTitle').textContent = title || '—';
  $('stockSub').textContent = [payload.exchange, payload.range, payload.interval].filter(Boolean).join(' · ') || '—';
  $('lastPrice').textContent = formatCurrency(payload.lastPrice, payload.currency || 'USD');

  const change = payload.change ?? null;
  const changePct = payload.changePct ?? null;
  const changeText = $('changeText');
  changeText.textContent = `${change >= 0 ? '+' : ''}${Number(change || 0).toFixed(2)} (${formatPct(changePct)})`;
  changeText.className = `muted ${change >= 0 ? 'positive' : 'negative'}`;

  const score = payload.score || {};
  const ratingEl = $('analystRatingText');
  if (ratingEl) {
    const ratingKey = score.rating || 'hold';
    ratingEl.textContent = t(`ratings.${ratingKey}`);
    
    // Style color dynamically
    ratingEl.className = '';
    if (ratingKey === 'strongBuy') ratingEl.classList.add('strong-buy');
    else if (ratingKey === 'buy') ratingEl.classList.add('positive');
    else if (ratingKey === 'sell') ratingEl.classList.add('negative');
    else ratingEl.classList.add('muted');
  }

  const ratingHelpEl = $('ratingHelpText');
  if (ratingHelpEl) {
    const isAnalyst = score.source === 'analyst';
    ratingHelpEl.textContent = isAnalyst
      ? (currentLang === 'zh' ? '基于分析师共识' : 'Based on analyst consensus')
      : (currentLang === 'zh' ? '基于技术指标' : 'Based on technical indicators');
  }

  if ($('signalText')) $('signalText').textContent = prettifySignal(score.signal);
  renderQuoteStats(payload);
  updateFavoriteButton();

  const reasons = score.reasons && score.reasons.length ? score.reasons : [t('states.noStrongCandidate')];
  $('reasonList').innerHTML = reasons.map((reason) => `<li>${escapeHtml(translateReason(reason))}</li>`).join('');

  updateIndicatorReading(payload);
}

function escapeHtml(str) {
  return String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function hideRatingModal() {
  const modal = $('ratingModal');
  if (modal) {
    modal.classList.add('hidden');
  }
}

function showEventModal(events) {
  const modal = $('ratingModal');
  const titleEl = $('modalTitle');
  const bodyEl = $('modalBody');
  if (!modal || !titleEl || !bodyEl) return;

  titleEl.textContent = currentLang === 'zh' ? '事件详情' : 'Event Details';
  
  const html = events.map((event) => {
    const label = getEventDisplayLabel(event);
    const title = event.title && event.title !== label ? event.title : label;
    const detail = event.detail ? `<div style="margin-top:8px;font-size:13px;line-height:1.5;color:#cbd5e1;">${escapeHtml(event.detail)}</div>` : '';
    const source = event.source ? `<span style="color:#38bdf8;margin-right:10px;">${escapeHtml(event.source)}</span>` : '';
    const date = event.date ? `<span>${escapeHtml(event.date)}</span>` : '';
    
    return `
      <div style="margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid rgba(148,163,184,0.12)">
        <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px;color:#f8fafc;">
          <span style="width:10px;height:10px;border-radius:999px;background:${event.type === 'earnings' ? '#38bdf8' : '#f59e0b'}"></span>
          ${escapeHtml(title)}
        </div>
        <div style="margin-top:6px;font-size:12px;color:#94a3b8;">
          ${source}${date}
        </div>
        ${detail}
      </div>
    `;
  }).join('');

  bodyEl.innerHTML = html;
  modal.classList.remove('hidden');
}

function showRatingModal() {
  if (!lastPayload || !lastPayload.score) return;
  const modal = $('ratingModal');
  const titleEl = $('modalTitle');
  const bodyEl = $('modalBody');
  if (!modal || !titleEl || !bodyEl) return;

  const score = lastPayload.score;
  const details = score.details || {};
  const isAnalyst = score.source === 'analyst';

  // Set Dynamic Title
  titleEl.textContent = isAnalyst ? t('modal.analystTitle') : t('modal.techTitle');

  let html = '';
  if (isAnalyst) {
    const ratingKey = score.rating || 'hold';
    const total = details.total || 0;
    
    // Header section with badge & analyst count
    html += `
      <div class="modal-rating-header">
        <span class="modal-rating-badge ${ratingKey === 'buy' ? 'positive' : (ratingKey === 'sell' ? 'negative' : 'muted')}">${t('ratings.' + ratingKey)}</span>
        <span class="modal-total-text">${t('modal.totalAnalysts', { total })}</span>
      </div>
    `;

    // Recommendations breakdown section
    html += `<div class="modal-section-title">${currentLang === 'zh' ? '评级分布' : 'Consensus Breakdown'}</div>`;
    
    const recommendations = [
      { key: 'strongBuy', label: t('modal.strongBuy'), barClass: 'buy-bar' },
      { key: 'buy', label: t('modal.buy'), barClass: 'buy-bar' },
      { key: 'hold', label: t('modal.hold'), barClass: 'hold-bar' },
      { key: 'underperform', label: t('modal.underperform'), barClass: 'sell-bar' },
      { key: 'sell', label: t('modal.sell'), barClass: 'sell-bar' },
    ];

    recommendations.forEach(rec => {
      const val = details[rec.key] ?? 0;
      const pctText = val.toFixed(1) + '%';
      html += `
        <div class="recommendation-row">
          <span class="recommendation-label">${rec.label}</span>
          <div class="recommendation-bar-container">
            <div class="recommendation-bar ${rec.barClass}" style="width: ${val}%;"></div>
          </div>
          <span class="recommendation-pct">${pctText}</span>
        </div>
      `;
    });

    // Target Price section
    if (details.average !== undefined && details.average !== null) {
      const currentPrice = lastPayload.lastPrice || 0;
      const avgTarget = details.average || 0;
      const hasAvg = avgTarget > 0;
      const percentVal = (hasAvg && currentPrice > 0) ? ((avgTarget - currentPrice) / currentPrice * 100) : 0;
      const percentText = percentVal >= 0 ? `+${percentVal.toFixed(1)}%` : `${percentVal.toFixed(1)}%`;

      html += `
        <div class="modal-section-title">${t('modal.targetPrice')}</div>
        <div class="target-price-stats">
          <div class="target-stat-card">
            <div class="stat-label">${currentLang === 'zh' ? '最低价' : 'Lowest'}</div>
            <div class="stat-value">${formatCurrency(details.lowest, lastPayload.currency)}</div>
          </div>
          <div class="target-stat-card" style="border-color: rgba(56, 189, 248, 0.3); background: rgba(56, 189, 248, 0.05);">
            <div class="stat-label" style="color: var(--blue);">${currentLang === 'zh' ? '平均价' : 'Average'}</div>
            <div class="stat-value" style="color: var(--blue);">${formatCurrency(details.average, lastPayload.currency)}</div>
          </div>
          <div class="target-stat-card">
            <div class="stat-label">${currentLang === 'zh' ? '最高价' : 'Highest'}</div>
            <div class="stat-value">${formatCurrency(details.highest, lastPayload.currency)}</div>
          </div>
        </div>
        <div class="target-comparison">
          <span>${t('modal.currentPrice')}: <strong>${formatCurrency(currentPrice, lastPayload.currency)}</strong></span>
          <span>${currentLang === 'zh' ? '目标均价空间' : 'Target Space'}: <strong class="${percentVal >= 0 ? 'positive' : 'negative'}">${percentText}</strong></span>
        </div>
      `;
    }
  } else {
    // Technical fallback modal representation
    const buyScore = details.buyScore || 0;
    const sellScore = details.sellScore || 0;
    html += `
      <div class="tech-score-row">
        <div class="tech-score-box">
          <div class="muted" style="font-size: 11px; text-transform: uppercase;">${currentLang === 'zh' ? '买入评分' : 'Buy Score'}</div>
          <div class="tech-score-val positive">${buyScore}</div>
        </div>
        <div class="tech-score-box">
          <div class="muted" style="font-size: 11px; text-transform: uppercase;">${currentLang === 'zh' ? '卖出评分' : 'Sell Score'}</div>
          <div class="tech-score-val negative">${sellScore}</div>
        </div>
      </div>
      <div class="tech-reasons-title">${t('panels.reasonsTitle')}</div>
    `;

    const reasons = score.reasons && score.reasons.length ? score.reasons : [t('states.noStrongCandidate')];
    html += `<ul class="tech-reasons-list">`;
    reasons.forEach(reason => {
      html += `<li>${escapeHtml(translateReason(reason))}</li>`;
    });
    html += `</ul>`;
  }

  bodyEl.innerHTML = html;
  modal.classList.remove('hidden');
}

function closeStockSearchSuggestions() {
  if (!searchSuggestionsEl) return;
  searchSuggestionsEl.classList.add('hidden');
  symbolInput?.setAttribute('aria-expanded', 'false');
}

function renderStockSearchSuggestions(items, query) {
  if (!searchSuggestionsEl) return;
  searchSuggestionItems = Array.isArray(items) ? items : [];
  if (!searchSuggestionItems.length) {
    searchSuggestionsEl.innerHTML = `<div class="stock-search-empty">没有找到“${escapeHtml(query)}”的股票候选项</div>`;
  } else {
    searchSuggestionsEl.innerHTML = searchSuggestionItems.map((item) => `
      <button class="stock-search-option" type="button" role="option" data-search-symbol="${escapeHtml(item.symbol)}">
        <span class="stock-search-symbol">${escapeHtml(item.symbol)}</span>
        <span class="stock-search-name">${escapeHtml(item.name || item.symbol)}</span>
        <span class="stock-search-exchange">${escapeHtml(item.exchange || item.quoteType || '')}</span>
      </button>
    `).join('');
  }
  searchSuggestionsEl.classList.remove('hidden');
  symbolInput?.setAttribute('aria-expanded', 'true');
}

async function fetchStockSearchSuggestions(query, show = true) {
  const cleanQuery = String(query || '').trim();
  if (cleanQuery.length < 1) {
    closeStockSearchSuggestions();
    return [];
  }
  const seq = ++searchSuggestionSeq;
  try {
    const response = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(cleanQuery)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    if (seq !== searchSuggestionSeq) return [];
    const items = Array.isArray(payload.results) ? payload.results : [];
    if (show) renderStockSearchSuggestions(items, cleanQuery);
    else searchSuggestionItems = items;
    return items;
  } catch (_) {
    if (seq === searchSuggestionSeq && show) renderStockSearchSuggestions([], cleanQuery);
    return [];
  }
}

async function resolveSearchSymbol(query) {
  const clean = String(query || '').trim();
  if (!clean) return '';
  const upper = clean.toUpperCase();
  const matchingCached = searchSuggestionItems.find((item) => item.symbol === upper || String(item.name || '').toLowerCase() === clean.toLowerCase());
  if (matchingCached) return matchingCached.symbol;
  const items = await fetchStockSearchSuggestions(clean, false);
  const exact = items.find((item) => item.symbol === upper);
  return (exact || items[0])?.symbol || upper;
}

async function analyzeSymbol(symbol) {
  const clean = String(symbol || '').trim().toUpperCase();
  if (!clean) return;

  if (lastTradePlan && lastTradePlan.symbol !== clean) {
    lastTradePlan = null;
    clearTradePlanOverlays();
    $('tradePlanResult')?.classList.add('hidden');
  }

  const { period, interval } = getSelectedTimeframe();

  // 1) Show locally cached data immediately (if any).
  const localCached = stockCacheGet(clean, period, interval);
  if (localCached) {
    lastPayload = localCached;
    updateSummary(localCached);
    createCharts(localCached);
    showStatus(currentLang === 'zh' ? '已从本地缓存加载，正在后台更新...' : 'Loaded from local cache, updating in background...');
  } else {
    showStatus(t('states.loading', { symbol: clean }));
  }

  // 2) Always fetch fresh data from server (may itself be served from server cache).
  try {
    const url = `${API_BASE}/api/analyze/${encodeURIComponent(clean)}?period=${encodeURIComponent(period)}&interval=${encodeURIComponent(interval)}`;
    const response = await fetch(url);
    const rawBody = await response.text();
    let payload;
    try {
      payload = rawBody ? JSON.parse(rawBody) : {};
    } catch (_) {
      throw new Error(`服务返回了非 JSON 响应（HTTP ${response.status}）：${rawBody.slice(0, 160) || '无响应内容'}`);
    }
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);

    lastPayload = payload;
    updateSummary(payload);
    createCharts(payload);

    // Save to localStorage for next time.
    stockCacheSet(clean, period, interval, payload);

    // Show stale indicator if server returned stale data.
    if (payload.cache && payload.cache.stale) {
      showStatus(currentLang === 'zh' ? '数据来自服务端缓存（后台正在刷新）' : 'Served from server cache (refreshing in background)');
      setTimeout(hideStatus, 3000);
    } else {
      hideStatus();
    }
    // Close modal on search if open
    hideRatingModal();
  } catch (error) {
    console.error(error);
    // If we already showed local cache, downgrade the error to a warning.
    if (localCached) {
      showStatus(currentLang === 'zh' ? '后台更新失败，当前显示本地缓存数据' : 'Background update failed, showing local cached data');
      setTimeout(hideStatus, 4000);
    } else {
      showStatus(t('states.loadFail', { symbol: clean, message: error.message }), true);
    }
  }
}

function getSelectedTimeframe() {
  const active = timeframeButtons.find((button) => button.classList.contains('active')) || timeframeButtons[0];
  return {
    period: active?.dataset.timeframePeriod || '1y',
    interval: active?.dataset.timeframeInterval || '1d',
  };
}

function selectTimeframe(button) {
  timeframeButtons.forEach((item) => {
    const active = item === button;
    item.classList.toggle('active', active);
    item.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function analyzeCurrentSelection() {
  const symbol = String(symbolInput.value || lastPayload?.symbol || '').trim().toUpperCase();
  if (!symbol) return;
  symbolInput.value = symbol;
  analyzeSymbol(symbol);
}

$('searchForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const resolved = await resolveSearchSymbol(symbolInput.value);
  if (!resolved) return;
  symbolInput.value = resolved;
  closeStockSearchSuggestions();
  analyzeSymbol(resolved);
});

symbolInput?.addEventListener('input', () => {
  window.clearTimeout(searchSuggestionTimer);
  const query = symbolInput.value.trim();
  if (!query) {
    closeStockSearchSuggestions();
    return;
  }
  searchSuggestionTimer = window.setTimeout(() => fetchStockSearchSuggestions(query, true), 220);
});

symbolInput?.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeStockSearchSuggestions();
});

searchSuggestionsEl?.addEventListener('click', (event) => {
  const option = event.target.closest('[data-search-symbol]');
  if (!option) return;
  const symbol = String(option.dataset.searchSymbol || '').toUpperCase();
  if (!symbol) return;
  symbolInput.value = symbol;
  closeStockSearchSuggestions();
  analyzeSymbol(symbol);
});

document.addEventListener('click', (event) => {
  if (!event.target.closest('.stock-search-field')) closeStockSearchSuggestions();
});

$('calculateTradePlan')?.addEventListener('click', calculateTradePlan);
$('tradePlanHorizon')?.addEventListener('change', () => {
  lastTradePlan = null;
  clearTradePlanOverlays();
  $('tradePlanResult')?.classList.add('hidden');
});

timeframeButtons.forEach((button) => {
  button.addEventListener('click', () => {
    selectTimeframe(button);
    analyzeCurrentSelection();
  });
});

document.querySelectorAll('[data-symbol]').forEach((button) => {
  button.addEventListener('click', () => {
    symbolInput.value = button.dataset.symbol;
    analyzeSymbol(button.dataset.symbol);
  });
});

$('langToggle')?.addEventListener('click', () => {
  currentLang = currentLang === 'zh' ? 'en' : 'zh';
  localStorage.setItem('stockAnalysisLang', currentLang);
  applyLanguage();
  
  // Refresh modal if currently open to translate keys dynamically
  const modal = $('ratingModal');
  if (modal && !modal.classList.contains('hidden')) {
    showRatingModal();
  }
});

// Modal Event Listeners
$('ratingCard')?.addEventListener('click', showRatingModal);
favoriteStockButton?.addEventListener('click', (event) => {
  event.stopPropagation();
  addCurrentStockToFavorites();
});
$('closeModal')?.addEventListener('click', hideRatingModal);
$('modalOverlay')?.addEventListener('click', hideRatingModal);

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    hideRatingModal();
  }
});

// ---------------------------------------------------------------------------
// Remote indicator state polling — allows CLI to simulate button clicks.
// The CLI POSTs to /api/indicator/toggle; this poll loop detects changes
// and calls .click() on the real <button>, so zero button logic is duplicated.
// ---------------------------------------------------------------------------
let _lastRemoteIndicatorState = null;
let _indicatorPollTimer = null;

async function pollRemoteIndicatorState() {
  try {
    const resp = await fetch(`${API_BASE}/api/indicator/state`);
    if (!resp.ok) return;
    const remote = await resp.json();
    if (!_lastRemoteIndicatorState) {
      _lastRemoteIndicatorState = remote;
      return;
    }
    // Compare remote vs local and click the real button when a toggle is detected
    if (remote.volume !== _lastRemoteIndicatorState.volume) {
      const btn = document.getElementById('btn3dVol');
      if (btn) btn.click();
    }
    if (remote.ma !== _lastRemoteIndicatorState.ma) {
      const btn = document.getElementById('btn3dMA');
      if (btn) btn.click();
    }
    if (remote.events !== _lastRemoteIndicatorState.events) {
      const btn = document.getElementById('btn3dEvents');
      if (btn) btn.click();
    }
    if (remote.resetSeq !== _lastRemoteIndicatorState.resetSeq) {
      const btn = document.getElementById('btn3dReset');
      if (btn) btn.click();
    }
    // Indicator catalog items (sidebar)
    if (remote.toggleSeq !== _lastRemoteIndicatorState.toggleSeq && remote.toggledKey) {
      toggleIndicator(remote.toggledKey);
    }
    _lastRemoteIndicatorState = remote;
  } catch (_) {
    // Server may not be ready; silently retry next cycle.
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  await loadWatchlist();
  await loadPersistedIndicatorState();
  initIndicatorToggles();
  $('clearCustomIndicators')?.addEventListener('click', clearDynamicIndicators);
  applyLanguage();
  init3DControls();
  const initialSymbol = new URLSearchParams(window.location.search).get('symbol') || symbolInput.value || 'VST';
  symbolInput.value = String(initialSymbol).trim().toUpperCase();
  analyzeSymbol(symbolInput.value || 'VST');
  // Start polling for CLI-driven button clicks
  _indicatorPollTimer = setInterval(pollRemoteIndicatorState, 800);
});

function applySubControlStates() {
  if (!chartState.chart3D) return;
  const btnVol = $('btn3dVol');
  const btnMA = $('btn3dMA');
  const btnEvents = $('btn3dEvents');
  if (btnVol) chartState.chart3D.toggleVolume(btnVol.classList.contains('active'));
  if (btnMA) chartState.chart3D.toggleMA(btnMA.classList.contains('active'));
  if (btnEvents) chartState.chart3D.toggleEvents(btnEvents.classList.contains('active'));
}

function updateChartViewMode(mode) {
  const panel = $('pricePanel');
  const chart2D = $('priceChart');
  const chart3D = $('priceChart3d');
  const btn2D = $('btn2D');
  const btn3D = $('btn3D');
  const controls3D = $('controls3d');
  const btnFusionView = $('btnFusionView');

  if (!panel || !chart2D || !chart3D || !btn2D || !btn3D || !controls3D) return;

  if (mode === '3d') {
    panel.classList.add('show-3d');
    chart2D.classList.add('hidden');
    chart3D.classList.remove('hidden');
    btn2D.classList.remove('active');
    btn3D.classList.add('active');
    if (btnFusionView) {
      btnFusionView.style.display = 'inline-block';
      btnFusionView.classList.toggle('active', chartState.fusion3D);
    }

    if (window.Stock3DChart && lastPayload) {
      if (chartState.chart3D) {
        chartState.chart3D.destroy();
      }
      const indicators = lastPayload.indicators || {};
      const activeItems = [...activeIndicators]
        .map((key) => {
          const lowerKey = key.trim().toLowerCase();
          if (lowerKey === 'volume' || lowerKey === 'vol' || lowerKey === 'amount') {
            return {
              key: 'volume',
              nameZh: '成交量',
              nameEn: 'Volume',
              pane: 'pane',
              series: [
                {
                  key: 'volume',
                  type: 'histogram',
                  name: currentLang === 'zh' ? '成交量' : 'Volume',
                  color: 'rgba(34, 197, 94, 0.45)',
                  data: lastPayload.volume || []
                }
              ]
            };
          }
          return indicators[key];
        })
        .filter(Boolean);
      const priceItems = activeItems.filter((item) => item.pane === 'price');

      chartState.chart3D = new window.Stock3DChart(chart3D, lastPayload, priceItems);
      applySubControlStates();
      if (chartState.chart3D.updateTradePlan) chartState.chart3D.updateTradePlan(lastTradePlan);

      if (chartState.fusion3D) {
        const paneItems = activeItems.filter((item) => item.pane !== 'price');
        if (chartState.chart3D.updateFusedIndicators) {
          chartState.chart3D.updateFusedIndicators(paneItems);
        }
      }
    }
  } else {
    if (btnFusionView) {
      btnFusionView.style.display = 'inline-block';
      btnFusionView.classList.toggle('active', chartState.fusion2D);
    }

    panel.classList.remove('show-3d');
    chart2D.classList.remove('hidden');
    chart3D.classList.add('hidden');
    btn2D.classList.add('active');
    btn3D.classList.remove('active');

    if (chartState.chart3D) {
      chartState.chart3D.destroy();
      chartState.chart3D = null;
    }
    if (chartState.chart3DSub) {
      chartState.chart3DSub.destroy();
      chartState.chart3DSub = null;
    }
    window.dispatchEvent(new Event('resize'));
  }

  // Sync sub indicators layout
  renderActiveIndicators();
}

function init3DControls() {
  const btn2D = $('btn2D');
  const btn3D = $('btn3D');
  const btnVol = $('btn3dVol');
  const btnMA = $('btn3dMA');
  const btnEvents = $('btn3dEvents');
  const btnFusionView = $('btnFusionView');
  const btnReset = $('btn3dReset');

  // Synchronize initial button states with actual 2D chart states
  if (btnVol) {
    const isVolActive = activeIndicators.has('volume') || activeIndicators.has('vol') || activeIndicators.has('amount');
    btnVol.classList.toggle('active', isVolActive);
  }
  if (btnMA) {
    const isMAActive = !!(indicatorState.sma20 || indicatorState.sma50);
    btnMA.classList.toggle('active', isMAActive);
  }
  if (btnEvents) {
    const isEventsActive = indicatorState.events !== false;
    btnEvents.classList.toggle('active', isEventsActive);
  }

  if (btn2D) {
    btn2D.addEventListener('click', () => {
      chartState.currentChartViewMode = '2d';
      updateChartViewMode('2d');
    });
  }
  if (btn3D) {
    btn3D.addEventListener('click', () => {
      chartState.currentChartViewMode = '3d';
      updateChartViewMode('3d');
    });
  }
  if (btnVol) {
    btnVol.addEventListener('click', () => {
      const active = btnVol.classList.toggle('active');
      
      // 3D Toggle
      if (chartState.chart3D) {
        chartState.chart3D.toggleVolume(active);
      }
      
      // 2D Toggle
      if (active) {
        activeIndicators.add('volume');
      } else {
        activeIndicators.delete('volume');
      }
      syncLegacyIndicatorCharts();
      scheduleReportActiveIndicators();
    });
  }
  if (btnMA) {
    btnMA.addEventListener('click', () => {
      const active = btnMA.classList.toggle('active');

      // 3D Toggle
      if (chartState.chart3D) {
        chartState.chart3D.toggleMA(active);
      }

      // 2D Toggle
      indicatorState.sma20 = active;
      indicatorState.sma50 = active;
      applyPriceIndicatorVisibility();
      scheduleReportActiveIndicators();
    });
  }
  if (btnEvents) {
    btnEvents.addEventListener('click', () => {
      const active = btnEvents.classList.toggle('active');

      // 3D Toggle
      if (chartState.chart3D) {
        chartState.chart3D.toggleEvents(active);
      }

      // 2D Toggle
      indicatorState.events = active;
      applyMarkerVisibility();
      scheduleReportActiveIndicators();
    });
  }
  if (btnFusionView) {
    btnFusionView.addEventListener('click', () => {
      const active = btnFusionView.classList.toggle('active');
      if (chartState.currentChartViewMode === '3d') {
        chartState.fusion3D = active;
      } else {
        chartState.fusion2D = active;
      }

      // Update layout showing/hiding sub-indicators
      renderActiveIndicators();

      // Trigger resize to reflow containers
      if (chartState.chart3D) {
        chartState.chart3D.resize();
      }
      window.dispatchEvent(new Event('resize'));
    });
  }
  if (btnReset) {
    btnReset.addEventListener('click', () => {
      if (chartState.chart3D) {
        chartState.chart3D.resetView();
      } else {
        // Reset 2D zoom for all active charts in the sync registry
        if (chartState.price) chartState.price.timeScale().fitContent();
        if (chartState.custom) chartState.custom.timeScale().fitContent();
        if (chartState.dynamicPanes && chartState.dynamicPanes.length) {
          chartState.dynamicPanes.forEach((paneObj) => {
            if (paneObj.chart) paneObj.chart.timeScale().fitContent();
          });
        }
      }
      clearDynamicIndicators();
    });
  }
}
