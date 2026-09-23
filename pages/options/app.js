(function () {
  'use strict';

  const API_BASE = String(window.OPTIONS_API_BASE || window.STOCK_API_BASE || '/stock-analysis').replace(/\/$/, '');
  const $ = (id) => document.getElementById(id);
  const state = { symbol: 'AAPL', expiration: '', side: 'calls', data: null, searchTimer: null, requestId: 0 };

  function finite(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function pick(obj, keys, fallback = null) {
    for (const key of keys) if (obj && obj[key] !== undefined && obj[key] !== null) return obj[key];
    return fallback;
  }

  function normalizeContract(raw, side) {
    return {
      side,
      contractSymbol: String(pick(raw, ['contractSymbol', 'contract_symbol', 'symbol'], '')),
      strike: finite(pick(raw, ['strike', 'strikePrice'])),
      lastPrice: finite(pick(raw, ['lastPrice', 'last', 'mark'])),
      bid: finite(raw && raw.bid),
      ask: finite(raw && raw.ask),
      volume: finite(raw && raw.volume),
      openInterest: finite(pick(raw, ['openInterest', 'open_interest', 'oi'])),
      impliedVolatility: finite(pick(raw, ['impliedVolatility', 'implied_volatility', 'iv'])),
      inTheMoney: pick(raw, ['inTheMoney', 'in_the_money'], null)
    };
  }

  function normalizePayload(raw) {
    const chain = raw && raw.optionChain ? raw.optionChain : raw || {};
    const options = Array.isArray(chain.options) ? (chain.options[0] || {}) : {};
    const calls = pick(chain, ['calls'], options.calls || []);
    const puts = pick(chain, ['puts'], options.puts || []);
    return {
      status: String(pick(chain, ['status'], 'ok')),
      symbol: String(pick(chain, ['symbol', 'underlyingSymbol'], state.symbol)).toUpperCase(),
      name: String(pick(chain, ['name', 'underlyingName', 'shortName'], '')),
      underlyingPrice: finite(pick(chain, ['underlyingPrice', 'underlying_price', 'price', 'regularMarketPrice'])),
      currency: String(pick(chain, ['currency'], 'USD')),
      asOf: pick(chain, ['asOf', 'as_of', 'quoteTime', 'timestamp'], null),
      delayed: Boolean(pick(chain, ['delayed', 'isDelayed'], true)),
      source: String(pick(chain, ['source', 'dataSource'], '未标注')),
      expirationDates: (pick(chain, ['expirationDates', 'expiration_dates', 'expirations'], []) || []).map(String),
      selectedExpiration: String(pick(chain, ['selectedExpiration', 'selected_expiration', 'expiration'], state.expiration || '')),
      calls: Array.isArray(calls) ? calls.map((row) => normalizeContract(row, 'calls')) : [],
      puts: Array.isArray(puts) ? puts.map((row) => normalizeContract(row, 'puts')) : [],
      missingFields: (pick(chain, ['missingFields', 'missing_fields'], []) || []).map(String),
      message: String(pick(chain, ['message', 'detail'], ''))
    };
  }

  function fmtNumber(value, digits = 2) { return value === null ? '<span class="missing-value">--</span>' : value.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits }); }
  function fmtCompact(value) { return value === null ? '<span class="missing-value">--</span>' : new Intl.NumberFormat('en-US', { notation: value >= 100000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value); }
  function fmtIV(value) {
    if (value === null) return '<span class="missing-value">--</span>';
    const pct = Math.abs(value) <= 5 ? value * 100 : value;
    return `${pct.toFixed(1)}%`;
  }
  function fmtTime(value) {
    if (!value) return '--';
    const date = typeof value === 'number' && value < 1e12 ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
  }
  function safe(text) { const el = document.createElement('span'); el.textContent = String(text ?? ''); return el.innerHTML; }
  function dte(expiration) { if (!expiration) return '--'; const today = new Date(); const end = new Date(`${expiration}T16:00:00`); return Number.isNaN(end.getTime()) ? '--' : `${Math.max(0, Math.ceil((end - today) / 86400000))} DTE`; }

  function setMessage(kind, title, detail) {
    $('messagePanel').className = `message-panel ${kind || ''}`;
    $('messagePanel').innerHTML = `<div class="message-icon">${kind === 'loading' ? '◌' : kind === 'error' ? '!' : '⌁'}</div><div><strong>${safe(title)}</strong><p>${safe(detail)}</p></div>`;
    $('messagePanel').hidden = false;
    $('content').hidden = true;
  }

  function updateExpirations(data) {
    const select = $('expirationSelect');
    const dates = [...new Set(data.expirationDates)];
    if (data.selectedExpiration && !dates.includes(data.selectedExpiration)) dates.unshift(data.selectedExpiration);
    select.innerHTML = dates.length ? dates.map((date) => `<option value="${safe(date)}"${date === data.selectedExpiration ? ' selected' : ''}>${safe(date)} · ${dte(date)}</option>`).join('') : '<option value="">无可用到期日</option>';
    select.disabled = !dates.length;
    state.expiration = data.selectedExpiration || dates[0] || '';
    if (state.expiration) select.value = state.expiration;
  }

  function classify(contract, side, price) {
    if (contract.strike === null || price === null || !price) return 'unknown';
    if (Math.abs(contract.strike - price) / price <= .0125) return 'atm';
    const itm = side === 'calls' ? contract.strike < price : contract.strike > price;
    return itm ? 'itm' : 'otm';
  }

  function renderChain() {
    const data = state.data;
    const side = state.side;
    let rows = data ? data[side] : [];
    const price = data && data.underlyingPrice;
    if ($('nearMoneyOnly').checked && price) rows = rows.filter((row) => row.strike !== null && Math.abs(row.strike / price - 1) <= .2);
    rows = [...rows].sort((a, b) => (a.strike ?? Infinity) - (b.strike ?? Infinity));
    $('contractCount').textContent = `${rows.length} 张合约`;
    $('chainEmpty').hidden = rows.length > 0;
    $('chainBody').innerHTML = rows.map((row) => {
      const money = classify(row, side, price);
      const label = money === 'itm' ? 'ITM' : money === 'atm' ? 'ATM' : money === 'otm' ? 'OTM' : '--';
      const spread = row.bid !== null && row.ask !== null ? Math.max(0, row.ask - row.bid) : null;
      return `<tr class="${money === 'atm' ? 'atm-row' : ''}"><td><span class="money-badge ${money}">${label}</span></td><td>${fmtNumber(row.strike)}</td><td>${fmtNumber(row.lastPrice)}</td><td>${fmtNumber(row.bid)}</td><td>${fmtNumber(row.ask)}</td><td>${fmtNumber(spread)}</td><td>${fmtCompact(row.volume)}</td><td>${fmtCompact(row.openInterest)}</td><td>${fmtIV(row.impliedVolatility)}</td><td class="contract-code">${row.contractSymbol ? safe(row.contractSymbol) : '<span class="missing-value">--</span>'}</td></tr>`;
    }).join('');
  }

  function liquidScore(row) {
    const oi = row.openInterest || 0, volume = row.volume || 0;
    const mid = row.bid !== null && row.ask !== null ? (row.bid + row.ask) / 2 : null;
    const spreadRatio = mid && mid > 0 ? Math.max(0, row.ask - row.bid) / mid : 1;
    return Math.log10(oi + 1) * 2 + Math.log10(volume + 1) - spreadRatio * 4;
  }

  function selectCandidate(rows, predicate) {
    return rows.filter((row) => row.strike !== null && predicate(row)).sort((a, b) => liquidScore(b) - liquidScore(a))[0] || null;
  }

  function strategyCard(title, tag, row, reason) {
    if (!row) return `<article class="strategy-card"><header><h3>${safe(title)}</h3><span class="strategy-tag">${safe(tag)}</span></header><div class="strategy-missing">当前链缺少满足基础筛选条件的合约</div></article>`;
    const mid = row.bid !== null && row.ask !== null ? (row.bid + row.ask) / 2 : row.lastPrice;
    return `<article class="strategy-card"><header><h3>${safe(title)}</h3><span class="strategy-tag">${safe(tag)}</span></header><div class="strategy-contract">${safe(row.contractSymbol || `${row.side === 'calls' ? 'CALL' : 'PUT'} ${row.strike}`)}</div><div class="strategy-stats"><div><span>Strike</span><strong>${fmtNumber(row.strike)}</strong></div><div><span>参考中间价</span><strong>${fmtNumber(mid)}</strong></div><div><span>OI</span><strong>${fmtCompact(row.openInterest)}</strong></div></div><p class="strategy-reason">${safe(reason)}</p></article>`;
  }

  function renderStrategies(data) {
    const price = data.underlyingPrice;
    if (!price) {
      $('strategyGrid').innerHTML = ['Covered Call', 'Cash-Secured Put', 'Long Call', 'Long Put'].map((name) => strategyCard(name, '数据不足', null, '')).join('');
      return;
    }
    const covered = selectCandidate(data.calls, (r) => r.strike >= price * 1.02 && r.strike <= price * 1.10);
    const csp = selectCandidate(data.puts, (r) => r.strike <= price * .98 && r.strike >= price * .90);
    const longCall = selectCandidate(data.calls, (r) => Math.abs(r.strike / price - 1) <= .03);
    const longPut = selectCandidate(data.puts, (r) => Math.abs(r.strike / price - 1) <= .03);
    $('strategyGrid').innerHTML = [
      strategyCard('Covered Call', '持股覆盖', covered, '筛选现价上方 2%–10% 的 Call，并优先相对活跃的成交量、OI 与价差。需要已有 100 股标的。'),
      strategyCard('Cash-Secured Put', '现金担保', csp, '筛选现价下方 2%–10% 的 Put，并优先相对活跃合约。需要准备执行价 × 100 的现金义务。'),
      strategyCard('Long Call', '方向性 · 风险有限', longCall, '筛选近平值 Call 并优先相对活跃合约。最大损失通常为权利金，时间价值会衰减。'),
      strategyCard('Long Put', '方向性 · 风险有限', longPut, '筛选近平值 Put 并优先相对活跃合约。最大损失通常为权利金，需留意波动率回落。')
    ].join('');
  }

  function render(data) {
    $('messagePanel').hidden = true;
    $('content').hidden = false;
    $('metricSymbol').textContent = data.symbol || '--';
    $('metricName').textContent = data.name || '公司名称未提供';
    $('metricPrice').innerHTML = fmtNumber(data.underlyingPrice);
    $('metricCurrency').textContent = data.currency || '币种未提供';
    $('metricExpiration').textContent = data.selectedExpiration || '--';
    $('metricDte').textContent = dte(data.selectedExpiration);
    $('metricAsOf').textContent = fmtTime(data.asOf);
    $('metricDelay').textContent = data.delayed ? '延迟/非交易所实时' : '数据源标记为实时';
    $('sourceLabel').textContent = `来源：${data.source || '未标注'}`;
    $('missingLabel').textContent = `缺失字段：${data.missingFields.length ? data.missingFields.join('、') : '接口未报告'}`;
    $('sourceState').className = 'source-state live';
    $('sourceState').innerHTML = `<span class="status-dot"></span><span>${safe(data.source)} · ${data.delayed ? '延迟数据' : '实时标记'}</span>`;
    updateExpirations(data);
    renderStrategies(data);
    renderChain();
  }

  async function loadChain({ keepExpiration = false } = {}) {
    const symbol = $('symbolInput').value.trim().toUpperCase().replace(/[^A-Z0-9.\-^]/g, '');
    if (!symbol) return setMessage('error', '请输入有效标的', '可输入股票代码，或先从公司名称建议中选择。');
    state.symbol = symbol;
    if (!keepExpiration) state.expiration = '';
    const id = ++state.requestId;
    $('loadButton').disabled = true;
    setMessage('loading', `正在读取 ${symbol} 期权链`, '等待真实行情接口返回；不会使用演示数据填充。');
    try {
      const query = state.expiration ? `?expiration=${encodeURIComponent(state.expiration)}` : '';
      const response = await fetch(`${API_BASE}/api/options/${encodeURIComponent(symbol)}${query}`, { headers: { Accept: 'application/json' } });
      const text = await response.text();
      let raw;
      try { raw = JSON.parse(text); } catch (_) { throw new Error(`HTTP ${response.status} 返回非 JSON：${text.slice(0, 100)}`); }
      if (!response.ok) throw new Error(raw.detail || raw.message || `HTTP ${response.status}`);
      if (id !== state.requestId) return;
      const data = normalizePayload(raw);
      if (data.status === 'missing' || data.status === 'data_missing' || (!data.calls.length && !data.puts.length && !data.expirationDates.length)) throw new Error(data.message || '数据源没有返回可用期权链');
      state.data = data;
      render(data);
    } catch (error) {
      if (id !== state.requestId) return;
      $('sourceState').className = 'source-state error';
      $('sourceState').innerHTML = '<span class="status-dot"></span><span>期权数据不可用</span>';
      setMessage('error', `${symbol} 期权链读取失败`, error.message || '接口不可用或数据源没有覆盖该标的。');
    } finally { if (id === state.requestId) $('loadButton').disabled = false; }
  }

  function renderSuggestions(items) {
    const box = $('searchSuggestions');
    if (!items.length) { box.hidden = true; return; }
    box.innerHTML = items.slice(0, 8).map((item) => {
      const symbol = String(pick(item, ['symbol', 'ticker'], '')).toUpperCase();
      const name = String(pick(item, ['name', 'shortName', 'longName'], ''));
      return `<button class="suggestion" type="button" data-symbol="${safe(symbol)}"><strong>${safe(symbol)}</strong><span>${safe(name)}</span></button>`;
    }).join('');
    box.hidden = false;
  }

  async function searchSymbols(query, show = true) {
    try {
      const response = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}`, { headers: { Accept: 'application/json' } });
      if (!response.ok) { if (show) renderSuggestions([]); return []; }
      const raw = await response.json();
      const items = Array.isArray(raw) ? raw : pick(raw, ['results', 'quotes', 'items', 'data'], []);
      const normalized = Array.isArray(items) ? items : [];
      if (show) renderSuggestions(normalized);
      return normalized;
    } catch (_) { if (show) renderSuggestions([]); return []; }
  }

  async function resolveOptionSymbol(input) {
    const raw = String(input || '').trim();
    const exact = raw.toUpperCase();
    if (/^[A-Z0-9.\-^]+$/.test(exact)) return exact;
    const items = await searchSymbols(raw, false);
    return String(pick(items[0], ['symbol', 'ticker'], '')).toUpperCase();
  }

  $('optionForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const symbol = await resolveOptionSymbol($('symbolInput').value);
    if (!symbol) return setMessage('error', '没有找到匹配标的', '请尝试股票代码、英文公司名或常用中文公司名。');
    $('symbolInput').value = symbol;
    renderSuggestions([]);
    loadChain();
  });
  $('expirationSelect').addEventListener('change', () => { state.expiration = $('expirationSelect').value; loadChain({ keepExpiration: true }); });
  $('nearMoneyOnly').addEventListener('change', renderChain);
  document.querySelectorAll('[data-side]').forEach((button) => button.addEventListener('click', () => {
    state.side = button.dataset.side;
    document.querySelectorAll('[data-side]').forEach((item) => item.classList.toggle('active', item === button));
    renderChain();
  }));
  document.querySelectorAll('.quick-symbols [data-symbol]').forEach((button) => button.addEventListener('click', () => { $('symbolInput').value = button.dataset.symbol; loadChain(); }));
  $('symbolInput').addEventListener('input', () => {
    clearTimeout(state.searchTimer);
    const query = $('symbolInput').value.trim();
    const hasChinese = /[\u3400-\u9fff]/.test(query);
    if (query.length < (hasChinese ? 1 : 2)) return renderSuggestions([]);
    state.searchTimer = setTimeout(() => searchSymbols(query), 220);
  });
  $('searchSuggestions').addEventListener('click', (event) => {
    const button = event.target.closest('[data-symbol]');
    if (!button) return;
    $('symbolInput').value = button.dataset.symbol;
    $('searchSuggestions').hidden = true;
    loadChain();
  });
  document.addEventListener('click', (event) => { if (!event.target.closest('.input-wrap')) $('searchSuggestions').hidden = true; });

  const initial = new URLSearchParams(location.search).get('symbol');
  if (initial) $('symbolInput').value = initial;
  loadChain();
}());
