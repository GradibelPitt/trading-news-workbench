(() => {
  "use strict";

  const el = {
    market: document.getElementById("marketSelect"),
    size: document.getElementById("sizeSelect"),
    refresh: document.getElementById("refreshButton"),
    retry: document.getElementById("emptyRetry"),
    heatmap: document.getElementById("heatmap"),
    tooltip: document.getElementById("tooltip"),
    loading: document.getElementById("loadingState"),
    empty: document.getElementById("emptyState"),
    emptyTitle: document.getElementById("emptyTitle"),
    emptyMessage: document.getElementById("emptyMessage"),
    status: document.getElementById("marketStatus"),
    advances: document.getElementById("advanceCount"),
    declines: document.getElementById("declineCount"),
    flats: document.getElementById("flatCount"),
    coverage: document.getElementById("coverageCount"),
    source: document.getElementById("sourceName"),
    asOf: document.getElementById("asOf"),
  };

  const query = new URLSearchParams(location.search);
  const apiBase = String(window.MARKET_API_BASE || window.API_BASE || query.get("apiBase") || "../..").replace(/\/$/, "");
  const endpoint = apiBase.endsWith("/api") ? `${apiBase}/market/heatmap` : `${apiBase}/api/market/heatmap`;
  let stocks = [];
  let metadata = {};
  let requestController = null;
  let renderFrame = 0;

  const number = value => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  function firstNumber(...values) {
    for (const value of values) {
      const parsed = number(value);
      if (parsed !== null) return parsed;
    }
    return null;
  }

  function flattenPayload(payload) {
    if (Array.isArray(payload)) return payload;
    const direct = payload?.items || payload?.stocks || payload?.data;
    if (Array.isArray(direct)) return direct;
    const groups = payload?.sectors || payload?.industries || payload?.groups;
    if (!Array.isArray(groups)) return [];
    return groups.flatMap(group => {
      const children = group.items || group.stocks || group.children || [];
      return children.map(item => ({ ...item, sector: item.sector || group.name || group.sector }));
    });
  }

  function normalizeStock(raw) {
    const symbol = String(raw?.symbol || raw?.ticker || raw?.code || "").trim().toUpperCase();
    const changePct = firstNumber(raw?.change_pct, raw?.changePercent, raw?.percent_change, raw?.regularMarketChangePercent);
    const marketCap = firstNumber(raw?.market_cap, raw?.marketCap, raw?.total_market_cap);
    const turnover = firstNumber(raw?.turnover, raw?.turnover_value, raw?.amount, raw?.traded_value);
    const price = firstNumber(raw?.price, raw?.last, raw?.close, raw?.regularMarketPrice);
    if (!symbol || changePct === null) return null;
    return {
      symbol,
      name: String(raw?.name || raw?.short_name || raw?.company_name || symbol),
      sector: String(raw?.sector || raw?.industry || raw?.group || "未分类"),
      changePct,
      marketCap,
      turnover,
      price,
      currency: String(raw?.currency || ""),
      exchange: String(raw?.exchange || raw?.market || ""),
      dataTime: raw?.data_time || raw?.as_of || raw?.timestamp || null,
      source: raw?.source || null,
    };
  }

  function buildUrl() {
    const url = new URL(endpoint, location.href);
    url.searchParams.set("market", el.market.value);
    url.searchParams.set("size_by", el.size.value);
    return url;
  }

  async function loadMarket() {
    requestController?.abort();
    requestController = new AbortController();
    setLoading(true);
    el.empty.hidden = true;
    setStatus("正在读取行情快照", "loading");
    try {
      const response = await fetch(buildUrl(), { headers: { Accept: "application/json" }, signal: requestController.signal });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || payload?.message || `行情接口返回 ${response.status}`);
      if (payload?.status && !["ok", "success", "partial", "stale"].includes(String(payload.status).toLowerCase())) {
        throw new Error(payload.message || payload.detail || "行情源暂时不可用");
      }
      stocks = flattenPayload(payload).map(normalizeStock).filter(Boolean);
      metadata = {
        source: payload?.source || payload?.meta?.source || stocks.find(item => item.source)?.source || "未标注",
        asOf: payload?.as_of || payload?.data_time || payload?.timestamp || payload?.meta?.as_of || latestDataTime(stocks),
        stale: Boolean(payload?.stale || String(payload?.status).toLowerCase() === "stale"),
        message: payload?.message || "",
      };
      updateSummary();
      if (!stocks.length) {
        showEmpty("暂无可绘制行情", payload?.message || "接口已连接，但没有返回包含涨跌幅的标的。");
      } else {
        setStatus(metadata.stale ? "行情快照已过期" : "行情快照已同步", metadata.stale ? "stale" : "live");
        scheduleRender();
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      stocks = [];
      metadata = {};
      updateSummary();
      setStatus("行情连接失败", "error");
      showEmpty("无法获取市场数据", error.message || "请检查行情服务后重试。", true);
    } finally {
      setLoading(false);
    }
  }

  function latestDataTime(items) {
    const times = items.map(item => item.dataTime).filter(Boolean).map(value => new Date(value)).filter(value => !Number.isNaN(value.getTime()));
    if (!times.length) return null;
    return new Date(Math.max(...times.map(value => value.getTime()))).toISOString();
  }

  function setLoading(active) {
    el.loading.hidden = !active;
    el.refresh.disabled = active;
  }

  function setStatus(text, tone) {
    el.status.textContent = text;
    el.status.dataset.tone = tone;
  }

  function showEmpty(title, message, isError = false) {
    el.heatmap.replaceChildren();
    el.emptyTitle.textContent = title;
    el.emptyMessage.textContent = message;
    el.empty.hidden = false;
    if (!isError) setStatus("数据缺失", "stale");
  }

  function formatTime(value) {
    if (!value) return "未提供";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }

  function compact(value, currency = "") {
    if (value === null || !Number.isFinite(value)) return "缺失";
    const unit = currency ? `${currency} ` : "";
    return unit + new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 }).format(value);
  }

  function updateSummary() {
    el.advances.textContent = stocks.filter(item => item.changePct > .005).length || (stocks.length ? "0" : "—");
    el.declines.textContent = stocks.filter(item => item.changePct < -.005).length || (stocks.length ? "0" : "—");
    el.flats.textContent = stocks.filter(item => Math.abs(item.changePct) <= .005).length || (stocks.length ? "0" : "—");
    el.coverage.textContent = stocks.length || "—";
    el.source.textContent = metadata.source || "—";
    el.source.title = metadata.source || "";
    el.asOf.textContent = metadata.asOf ? formatTime(metadata.asOf) : "—";
  }

  function weight(item) {
    const raw = el.size.value === "turnover" ? item.turnover : item.marketCap;
    return raw && raw > 0 ? raw : 0;
  }

  function usableItems() {
    const weighted = stocks.filter(item => weight(item) > 0);
    return weighted.length ? weighted : [];
  }

  function partition(items, rect, depth = 0) {
    if (!items.length || rect.w <= 0 || rect.h <= 0) return [];
    if (items.length === 1) return [{ item: items[0], ...rect }];
    const sorted = [...items].sort((a, b) => b.value - a.value);
    const total = sorted.reduce((sum, item) => sum + item.value, 0);
    let split = 1;
    let accumulated = sorted[0].value;
    while (split < sorted.length - 1 && accumulated + sorted[split].value <= total / 2) {
      accumulated += sorted[split].value;
      split += 1;
    }
    const ratio = Math.max(.08, Math.min(.92, accumulated / total));
    const vertical = rect.w / Math.max(rect.h, 1) > 1.15 || (depth % 2 === 0 && rect.w >= rect.h);
    const first = sorted.slice(0, split);
    const second = sorted.slice(split);
    if (vertical) {
      const width = rect.w * ratio;
      return [...partition(first, { x: rect.x, y: rect.y, w: width, h: rect.h }, depth + 1), ...partition(second, { x: rect.x + width, y: rect.y, w: rect.w - width, h: rect.h }, depth + 1)];
    }
    const height = rect.h * ratio;
    return [...partition(first, { x: rect.x, y: rect.y, w: rect.w, h: height }, depth + 1), ...partition(second, { x: rect.x, y: rect.y + height, w: rect.w, h: rect.h - height }, depth + 1)];
  }

  function scheduleRender() {
    cancelAnimationFrame(renderFrame);
    renderFrame = requestAnimationFrame(render);
  }

  function render() {
    el.empty.hidden = true;
    el.heatmap.replaceChildren();
    if (!stocks.length) return;
    const items = usableItems();
    if (!items.length) {
      const label = el.size.value === "turnover" ? "成交额" : "总市值";
      showEmpty(`缺少${label}数据`, `当前市场快照没有可用于矩形面积计算的${label}字段。`);
      return;
    }
    const missingCount = stocks.length - items.length;
    el.coverage.textContent = missingCount ? `${items.length}/${stocks.length}` : String(items.length);
    if (missingCount && !metadata.stale) {
      const label = el.size.value === "turnover" ? "成交额" : "总市值";
      setStatus(`行情部分可用 · ${missingCount} 项缺${label}`, "partial");
    }
    const groups = new Map();
    items.forEach(item => {
      if (!groups.has(item.sector)) groups.set(item.sector, []);
      groups.get(item.sector).push(item);
    });
    const groupItems = [...groups].map(([name, members]) => ({ name, members, value: members.reduce((sum, item) => sum + weight(item), 0) }));
    const width = el.heatmap.clientWidth;
    const height = el.heatmap.clientHeight;
    if (!width || !height) return;
    const groupRects = partition(groupItems, { x: 0, y: 0, w: width, h: height });
    const fragment = document.createDocumentFragment();
    groupRects.forEach(frame => fragment.appendChild(renderSector(frame)));
    el.heatmap.appendChild(fragment);
  }

  function renderSector(frame) {
    const sector = document.createElement("section");
    sector.className = "sector";
    position(sector, frame, 1);
    const average = frame.item.members.reduce((sum, item) => sum + item.changePct * weight(item), 0) / frame.item.value;
    const title = document.createElement("div");
    title.className = "sector-title";
    title.innerHTML = `<b>${escapeHtml(frame.item.name)}</b><span>${signed(average)}</span>`;
    sector.appendChild(title);
    const bodyHeight = Math.max(0, frame.h - 22);
    const childData = frame.item.members.map(item => ({ item, value: weight(item) }));
    const children = partition(childData, { x: 0, y: 22, w: frame.w - 2, h: bodyHeight - 1 });
    children.forEach(child => sector.appendChild(renderStock(child)));
    return sector;
  }

  function renderStock(frame) {
    const stock = frame.item.item;
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "stock-tile";
    const symbolSize = Math.max(8, Math.min(18, frame.h * .28, (frame.w - 8) / Math.max(stock.symbol.length * .66, 1)));
    const changeSize = Math.max(8, Math.min(12, frame.h * .18, (frame.w - 8) / 7));
    tile.style.setProperty("--tile-symbol-size", `${symbolSize}px`);
    tile.style.setProperty("--tile-change-size", `${changeSize}px`);
    if (frame.w < 58 || frame.h < 36) tile.classList.add("small");
    // Keep the ticker visible in compact tiles whenever there is enough room
    // for a readable label. Only hide it when the tile is physically too small.
    if (frame.w < 19 || frame.h < 16) tile.classList.add("tiny");
    tile.style.background = colorForChange(stock.changePct);
    position(tile, frame, .5);
    tile.setAttribute("aria-label", `${stock.name} ${stock.symbol}，涨跌幅 ${signed(stock.changePct)}`);
    tile.innerHTML = `<span class="stock-symbol">${escapeHtml(stock.symbol)}</span><span class="stock-change">${signed(stock.changePct)}</span>`;
    tile.addEventListener("pointerenter", event => showTooltip(event, stock));
    tile.addEventListener("pointermove", moveTooltip);
    tile.addEventListener("pointerleave", hideTooltip);
    tile.addEventListener("focus", event => showTooltip(event, stock));
    tile.addEventListener("blur", hideTooltip);
    tile.addEventListener("click", () => openStock(stock.symbol));
    return tile;
  }

  function position(node, rect, gap = 0) {
    node.style.left = `${rect.x + gap}px`;
    node.style.top = `${rect.y + gap}px`;
    node.style.width = `${Math.max(0, rect.w - gap * 2)}px`;
    node.style.height = `${Math.max(0, rect.h - gap * 2)}px`;
  }

  function colorForChange(value) {
    const pct = Math.max(-5, Math.min(5, value));
    if (Math.abs(pct) < .08) return "#27313d";
    const strength = Math.min(1, Math.abs(pct) / 5);
    if (pct > 0) return mix("#173b32", "#0eaa70", strength);
    return mix("#3b2630", "#b92d4a", strength);
  }

  function mix(from, to, amount) {
    const a = from.match(/\w\w/g).map(value => parseInt(value, 16));
    const b = to.match(/\w\w/g).map(value => parseInt(value, 16));
    return `rgb(${a.map((value, index) => Math.round(value + (b[index] - value) * amount)).join(",")})`;
  }

  function signed(value) {
    return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
  }

  function showTooltip(event, stock) {
    const sizeName = el.size.value === "turnover" ? "成交额" : "总市值";
    const sizeValue = el.size.value === "turnover" ? stock.turnover : stock.marketCap;
    el.tooltip.innerHTML = `
      <div class="tip-head"><strong>${escapeHtml(stock.symbol)}</strong><span>${escapeHtml(stock.name)}</span></div>
      <div class="tip-grid">
        <span>行业</span><b>${escapeHtml(stock.sector)}</b>
        <span>最新价</span><b>${stock.price === null ? "缺失" : `${escapeHtml(stock.currency)} ${stock.price.toLocaleString("zh-CN")}`}</b>
        <span>当日涨跌</span><b style="color:${stock.changePct >= 0 ? "var(--green)" : "var(--red)"}">${signed(stock.changePct)}</b>
        <span>${sizeName}</span><b>${compact(sizeValue, stock.currency)}</b>
      </div>
      <div class="tip-note">${escapeHtml(stock.exchange || "交易所未标注")} · 数据 ${escapeHtml(formatTime(stock.dataTime || metadata.asOf))}</div>`;
    el.tooltip.hidden = false;
    moveTooltip(event);
  }

  function moveTooltip(event) {
    if (el.tooltip.hidden) return;
    const pointX = Number.isFinite(event.clientX) ? event.clientX : event.currentTarget.getBoundingClientRect().right;
    const pointY = Number.isFinite(event.clientY) ? event.clientY : event.currentTarget.getBoundingClientRect().top;
    const margin = 12;
    const x = Math.min(innerWidth - el.tooltip.offsetWidth - margin, pointX + 14);
    const y = Math.min(innerHeight - el.tooltip.offsetHeight - margin, pointY + 14);
    el.tooltip.style.left = `${Math.max(margin, x)}px`;
    el.tooltip.style.top = `${Math.max(margin, y)}px`;
  }

  function hideTooltip() { el.tooltip.hidden = true; }

  function openStock(symbol) {
    const message = { type: "open-stock-analysis", symbol };
    if (window.parent !== window) window.parent.postMessage(message, location.origin);
    else location.href = `../../stock-analysis?symbol=${encodeURIComponent(symbol)}`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  }

  el.market.addEventListener("change", loadMarket);
  el.size.addEventListener("change", loadMarket);
  el.refresh.addEventListener("click", loadMarket);
  el.retry.addEventListener("click", loadMarket);
  new ResizeObserver(scheduleRender).observe(el.heatmap);
  loadMarket();
})();
