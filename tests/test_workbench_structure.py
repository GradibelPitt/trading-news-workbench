from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchStructureTests(unittest.TestCase):
    def test_feature_page_folders_exist(self):
        for folder in ["news", "screener", "options", "market", "watchlist", "quant", "stock-analysis-page"]:
            self.assertTrue((ROOT / "pages" / folder).is_dir(), folder)

    def test_sidebar_pages_are_declared_in_html(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        for page_id in ["news", "screener", "favorites", "options", "market", "watchlist", "quant"]:
            self.assertIn(f'data-page="{page_id}"', html)
            self.assertEqual(html.count(f'data-page="{page_id}"'), 1, page_id)
        self.assertIn('class="nav-list"', html)
        self.assertIn('data-page="watchlist" aria-label="分析"', html)
        self.assertIn('<span class="nav-icon">W</span><span class="nav-label">分析</span>', html)
        self.assertNotIn('aria-label="我的关注"', html)
        self.assertIn('进入“分析”的股票分析页', html)
        self.assertNotIn('进入“关注”的股票分析页', html)

    def test_news_layout_has_reference_sections(self):
        html = (ROOT / "pages" / "news" / "market_news.html").read_text(encoding="utf-8")
        for section_id in ["leadNews", "hotRank", "quickNews"]:
            self.assertIn(f'id="{section_id}"', html)

    def test_news_clicks_open_search_drawer_instead_of_moomoo_links(self):
        html = (ROOT / "pages" / "news" / "market_news.html").read_text(encoding="utf-8")
        self.assertIn('id="searchPanel"', html)
        self.assertIn('data-search-id=', html)
        self.assertIn("openSearchPanel", html)
        self.assertNotIn('href="${safeUrl(item.detailUrl)}"', html)
        self.assertNotIn('href="${safeUrl(lead.detailUrl)}"', html)

    def test_sidebar_is_compact(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: 84px minmax(0, 1fr);", html)

    def test_sidebar_music_player_uses_bilibili_bv(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="sidebarMusic"', html)
        self.assertIn('data-bvid="BV13USZBzEwY"', html)
        self.assertIn('id="bilibiliMusicFrame"', html)
        self.assertIn("player.bilibili.com/player.html", html)
        self.assertIn("toggleSidebarMusic", html)
        self.assertIn("music-turntable", html)
        self.assertIn("music-disc", html)
        self.assertIn("music-tonearm", html)
        self.assertIn("@keyframes music-spin", html)
        self.assertIn(".music-button.is-playing .music-disc", html)

    def test_favorites_page_reads_shared_stock_favorites(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="page-favorites"', html)
        self.assertIn('id="favoritesList"', html)
        self.assertIn('id="favoritesEmpty"', html)
        self.assertIn("stockFavorites:v1", html)
        self.assertIn("renderFavoritesPage", html)
        self.assertIn("openStockAnalysisFromFavorite", html)
        self.assertIsNone(re.search(r'class="[^"]*\bfavorites-tabs\b', html))
        self.assertIsNone(re.search(r'class="[^"]*\bfavorites-tab\b', html))
        self.assertIsNone(re.search(r'class="[^"]*\bfavorites-filter\b', html))
        self.assertNotIn(">全部</button>", html)
        self.assertNotIn(">龙头</button>", html)
        self.assertNotIn("光通信", html)
        self.assertIn('class="favorite-identity"', html)
        self.assertIn(".favorite-identity", html)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 76px 72px;", html)
        self.assertIn('id="favoritesAnalysisFrame"', html)
        self.assertIn('id="favoritesAnalysisLoading"', html)
        self.assertIn("loadFavoritesAnalysisFrame", html)
        self.assertIn("frame.src = `stock-analysis?symbol=", html)
        self.assertNotIn('openStockAnalysisFromFavorite(symbol) {\n      const clean = String(symbol || "").trim().toUpperCase();\n      if (!clean) return;\n      switchPage("watchlist");', html)

    def test_stock_analysis_has_add_to_favorites_button(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="favoriteStockButton"', html)
        self.assertIn('favorite-stock-button', html)
        self.assertIn("加入收藏", js)
        self.assertIn("stockFavorites:v1", js)
        self.assertIn("function addCurrentStockToFavorites", js)
        self.assertIn(".favorite-stock-button", css)

    def test_stock_analysis_has_quote_stats_panel(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="quoteStatsGrid"', html)
        self.assertIn("function renderQuoteStats", js)
        self.assertIn("quoteStats", js)
        self.assertIn(".quote-stats-panel", css)

    def test_indicator_catalog_is_expanded_by_default_but_still_collapsible(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        indicator_html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "indicators" / "indicator.html").read_text(encoding="utf-8")
        self.assertIn('class="chart-panel compact-panel indicator-host" id="indicatorHost"', html)
        self.assertNotIn('class="chart-panel compact-panel indicator-host is-collapsed" id="indicatorHost"', html)
        self.assertIn('aria-expanded="true"', html)
        self.assertIn('src="/static/indicators/indicator.html"', html)
        self.assertIn('let frameLoadedOnce = false;', html)
        self.assertIn('loadFrameIfNeeded();', html)
        self.assertIn("host.classList.toggle('is-collapsed')", html)
        self.assertIn('<section class="indicator-section" data-section>', indicator_html)
        self.assertNotIn('<section class="indicator-section collapsed" data-section>', indicator_html)
        self.assertIn("sectionToggle.closest('[data-section]')?.classList.toggle('collapsed')", indicator_html)

    def test_stock_analysis_header_uses_large_search_without_title(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "styles.css").read_text(encoding="utf-8")
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("<h1>股票分析</h1>", html)
        self.assertIn('placeholder="搜索股票代码或公司名称"', html)
        self.assertIn("placeholder: '搜索股票代码或公司名称'", js)
        self.assertIn("grid-template-columns: minmax(280px, 1fr) 118px;", css)
        self.assertIn(".search-row input::placeholder", css)

    def test_market_screener_and_options_replace_placeholders(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="screenerFrame"', html)
        self.assertIn('data-src="pages/screener/index.html"', html)
        self.assertIn('id="optionsFrame"', html)
        self.assertIn('data-src="pages/options/index.html"', html)
        self.assertIn('id="marketFrame"', html)
        self.assertIn('pages/market/index.html?apiBase=/stock-analysis', html)
        self.assertNotRegex(html, r'id="page-(?:screener|options|market)"[\s\S]{0,500}开发中')

    def test_stock_search_has_fuzzy_company_suggestions(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="stockSearchSuggestions"', html)
        self.assertIn("function fetchStockSearchSuggestions", js)
        self.assertIn("function resolveSearchSymbol", js)

    def test_shared_fuzzy_search_is_used_by_news_and_screener(self):
        fuzzy = (ROOT / "pages" / "shared" / "fuzzy-search.js").read_text(encoding="utf-8")
        news = (ROOT / "pages" / "news" / "market_news.html").read_text(encoding="utf-8")
        screener_html = (ROOT / "pages" / "screener" / "index.html").read_text(encoding="utf-8")
        screener_js = (ROOT / "pages" / "screener" / "app.js").read_text(encoding="utf-8")
        self.assertIn("global.GVIWFuzzy", fuzzy)
        self.assertIn("英伟达", fuzzy)
        self.assertIn("../shared/fuzzy-search.js", news)
        self.assertIn("GVIWFuzzy.rank(query, state.items", news)
        self.assertIn("../shared/fuzzy-search.js", screener_html)
        self.assertIn("GVIWFuzzy.rank(q,state.rows", screener_js)

    def test_stock_analysis_timeframe_controls_sit_between_stats_and_chart(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "styles.css").read_text(encoding="utf-8")
        search_form = re.search(r'<form id="searchForm" class="search-row">([\s\S]*?)</form>', html)
        self.assertIsNotNone(search_form)
        self.assertNotIn('id="rangeSelect"', search_form.group(1))
        self.assertNotIn('id="intervalSelect"', search_form.group(1))
        self.assertRegex(
            html,
            r'<section class="quote-stats-panel chart-panel"[\s\S]*?</section>\s*'
            r'<section class="market-timeframe-bar"[\s\S]*?data-timeframe-period="1y"[\s\S]*?data-timeframe-period="1mo"[\s\S]*?</section>\s*'
            r'<section class="trade-plan-panel chart-panel"[\s\S]*?</section>\s*'
            r'<section class="dashboard-grid">',
        )
        self.assertNotIn('id="rangeSelect"', html)
        self.assertNotIn('id="intervalSelect"', html)
        self.assertIn('class="timeframe-button active"', html)
        self.assertIn('data-timeframe-period="1y" data-timeframe-interval="1d">日K</button>', html)
        self.assertIn('data-timeframe-period="1mo" data-timeframe-interval="5m">5分</button>', html)
        self.assertIn('data-timeframe-period="1mo" data-timeframe-interval="30m">30分</button>', html)
        self.assertIn('data-timeframe-period="1mo" data-timeframe-interval="1d">1月</button>', html)
        self.assertIn('data-timeframe-period="5y" data-timeframe-interval="1d">5年</button>', html)
        self.assertIn('data-timeframe-period="max" data-timeframe-interval="1d">最大</button>', html)
        self.assertRegex(
            html,
            r'data-timeframe-period="5y" data-timeframe-interval="1d">5年</button>\s*'
            r'<button class="timeframe-button" type="button" data-timeframe-period="max" data-timeframe-interval="1d">最大</button>',
        )
        self.assertNotIn('>1分</button>', html)
        self.assertNotIn('>3分</button>', html)
        self.assertIn(".market-timeframe-bar", css)
        self.assertIn(".timeframe-button", css)

    def test_stock_analysis_has_trade_plan_calculator(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")
        stock_3d = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "stock-3d.js").read_text(encoding="utf-8")
        self.assertIn('id="calculateTradePlan"', html)
        self.assertIn('id="tradePlanHorizon"', html)
        self.assertIn("function calculateTradePlan()", js)
        self.assertIn("/api/trade-plan/", js)
        self.assertIn("function applyTradePlanOverlays", js)
        self.assertIn("updateTradePlan(plan)", stock_3d)

    def test_stock_analysis_range_and_interval_changes_trigger_analysis(self):
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("rangeSelect", js)
        self.assertNotIn("intervalSelect", js)
        self.assertIn("const timeframeButtons = [...document.querySelectorAll('[data-timeframe-period][data-timeframe-interval]')]", js)
        self.assertIn("function getSelectedTimeframe()", js)
        self.assertIn("function selectTimeframe(button)", js)
        self.assertIn("function analyzeCurrentSelection()", js)
        self.assertIn("timeframeButtons.forEach((button) => {", js)
        self.assertIn("button.addEventListener('click', () => {", js)
        self.assertIn("selectTimeframe(button);", js)
        self.assertIn("analyzeCurrentSelection();", js)

    def test_news_source_controls_include_requested_categories(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("pages/news/market_news.html", html)
        self.assertIn('data-index-feed="market"', html)
        self.assertIn('data-index-feed="academic"', html)
        self.assertIn('data-index-feed="opensource"', html)
        self.assertIn(">市场资讯</button>", html)
        self.assertIn(">学术资讯</button>", html)
        self.assertIn(">开源社区</button>", html)
        self.assertIn("toggleIndexFeedMenu", html)
        self.assertIn("closeIndexFeedMenu", html)

    def test_stock_analysis_has_3d_candles_chart(self):
        html = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "styles.css").read_text(encoding="utf-8")
        
        # Verify DOM elements in HTML
        self.assertIn('id="priceChart3d"', html)
        self.assertIn('id="btn3D"', html)
        self.assertIn('id="controls3d"', html)
        self.assertIn('src="/static/stock-3d.js"', html)
        
        # Verify Javascript logic references
        self.assertIn("updateChartViewMode", js)
        self.assertIn("Stock3DChart", js)
        
        # Verify CSS rules
        self.assertIn(".chart-controls-wrapper", css)
        self.assertIn(".show-3d", css)

    def test_watchlist_sync_via_postmessage(self):
        root_html = (ROOT / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "pages" / "stock-analysis-page" / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("STOCK_WATCHLIST_UPDATED", app_js)
        self.assertIn("notifyWatchlistChanged", app_js)
        self.assertIn("window.parent.postMessage(", app_js)
        self.assertNotIn("window.parent.loadWatchlist()", app_js)
        self.assertIn("window.parent === window", app_js)

        self.assertIn("STOCK_WATCHLIST_UPDATED", root_html)
        self.assertIn("event.origin !== window.location.origin", root_html)
        self.assertIn("renderFavoritesPage", root_html)

if __name__ == "__main__":
    unittest.main()
