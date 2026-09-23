(function () {
  'use strict';

  const API_BASE = String(window.SCREENER_API_BASE || window.API_BASE || '').replace(/\/$/, '');
  const ENDPOINT = window.SCREENER_API_ENDPOINT || `${API_BASE}/api/screener`;
  const PAGE_SIZE = 50;
  let state = { rows: [], filtered: [], page: 1, total: 0, pages: 0, source: null, asOf: null, loading: false };

  const $ = id => document.getElementById(id);
  const ids = ['market','sector','industry','priceMin','priceMax','marketCapMin','marketCapMax','changeMin','changeMax','volumeMin','avgVolumeMin','relativeVolumeMin','betaMax','peMin','peMax','forwardPeMax','pegMax','pbMax','psMax','epsGrowthMin','revenueGrowthMin','dividendYieldMin','payoutRatioMax','roeMin','debtEquityMax','trend','rsiMin','rsiMax','fromHighMax','atrPctMin'];
  const paramMap = {
    market:'market',sector:'sector',industry:'industry',priceMin:'price_min',priceMax:'price_max',marketCapMin:'market_cap_min',marketCapMax:'market_cap_max',changeMin:'change_pct_min',changeMax:'change_pct_max',volumeMin:'volume_min',avgVolumeMin:'avg_volume_min',relativeVolumeMin:'relative_volume_min',betaMax:'beta_max',peMin:'pe_min',peMax:'pe_max',forwardPeMax:'forward_pe_max',pegMax:'peg_max',pbMax:'pb_max',psMax:'ps_max',epsGrowthMin:'eps_growth_pct_min',revenueGrowthMin:'revenue_growth_pct_min',dividendYieldMin:'dividend_yield_pct_min',payoutRatioMax:'payout_ratio_pct_max',roeMin:'roe_pct_min',debtEquityMax:'debt_to_equity_max',trend:'technical',rsiMin:'rsi14_min',rsiMax:'rsi14_max',fromHighMax:'distance_from_52w_high_pct_max',atrPctMin:'atr_pct_min'
  };
  const presets = {
    active:{marketCapMin:'10000000000',priceMin:'5',avgVolumeMin:'1000000',relativeVolumeMin:'1'},
    momentum:{changeMin:'2',relativeVolumeMin:'1.5',volumeMin:'1000000',trend:'above_ema50',rsiMin:'50',rsiMax:'75'},
    value:{marketCapMin:'2000000000',peMin:'0',peMax:'15',forwardPeMax:'15',pbMax:'3'},
    income:{marketCapMin:'2000000000',dividendYieldMin:'3',payoutRatioMax:'80',peMin:'0'},
    oversold:{marketCapMin:'300000000',avgVolumeMin:'500000',rsiMax:'30'},
    breakout:{marketCapMin:'300000000',volumeMin:'1000000',relativeVolumeMin:'1.5',fromHighMax:'3',trend:'above_ema20',newHigh:true}
  };

  function clearFilters(keepMarket) {
    ids.forEach(id => { const el=$(id); if (el) el.value = id === 'market' && keepMarket ? 'US' : ''; });
    document.querySelectorAll('#exchangeGroup input, #unusualVolume, #newHigh').forEach(el => { el.checked=false; });
    document.querySelectorAll('[data-preset]').forEach(el => el.classList.remove('active'));
    updateActiveCount();
  }

  function applyPreset(name) {
    clearFilters(true);
    Object.entries(presets[name] || {}).forEach(([id,value]) => { const el=$(id); if (el) typeof value === 'boolean' ? el.checked=value : el.value=value; });
    document.querySelector(`[data-preset="${name}"]`)?.classList.add('active');
    updateActiveCount();
    runScreen(1);
  }

  function buildParams(page) {
    const p = new URLSearchParams({page:String(page),page_size:String(PAGE_SIZE)});
    ids.forEach(id => { let value=$(id)?.value?.trim();if(id==='industry'&&value&&window.GVIWFuzzy?.aliases[value])value=window.GVIWFuzzy.aliases[value].split(' ')[0]; if (value !== '' && !(id === 'market' && value === 'ALL')) p.set(paramMap[id], value); });
    const exchanges=[...document.querySelectorAll('#exchangeGroup input:checked')].map(el=>el.value);
    if(exchanges.length) p.set('exchanges', exchanges.join(','));
    if($('unusualVolume').checked) p.set('unusual_volume','true');
    if($('newHigh').checked) p.set('near_52w_high','true');
    const [sortBy,sortOrder]=$('sortSelect').value.split(':'); p.set('sort_by',sortBy); p.set('sort_order',sortOrder);
    return p;
  }

  async function runScreen(page=1) {
    if(state.loading) return;
    state.loading=true; state.page=page; setLoading(true);
    const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),20000);
    try {
      const response=await fetch(`${ENDPOINT}?${buildParams(page)}`,{headers:{Accept:'application/json'},signal:controller.signal});
      const payload=await response.json().catch(()=>null);
      if(!response.ok) throw new Error(payload?.detail || payload?.error || `接口返回 HTTP ${response.status}`);
      const normalized=normalizeResponse(payload);
      populateSectorOptions(normalized.sectors);
      state={...state,...normalized,page:Number(normalized.page||page),loading:false};
      applyTableSearch();
      setNotice(normalized.warning || `筛选完成 · 数据时间 ${formatTime(normalized.asOf)}`, normalized.warning?'warning':'');
      const pill=$('sourcePill'); pill.textContent=`数据源：${normalized.source || '未提供'}`; pill.classList.toggle('live',Boolean(normalized.source));
    } catch(error) {
      state={...state,rows:[],filtered:[],total:0,pages:0,loading:false}; renderRows(); updateMeta();
      const message=error.name==='AbortError'?'数据接口请求超时':error.message;
      setNotice(`无法取得筛选数据：${message}。未展示任何模拟数据。`,'error');
      $('sourcePill').textContent='数据源：连接失败'; $('sourcePill').classList.remove('live');
    } finally { clearTimeout(timeout); setLoading(false); }
  }

  function normalizeResponse(payload) {
    const rows=Array.isArray(payload)?payload:(payload?.items || payload?.results || payload?.data || []);
    if(!Array.isArray(rows)) throw new Error('接口响应缺少 items/results 数组');
    const meta=payload?.meta || {};
    return {rows,total:Number(payload?.total ?? meta.total ?? rows.length),pages:Number(payload?.pages ?? meta.pages ?? Math.ceil(Number(payload?.total ?? rows.length)/PAGE_SIZE)),page:Number(payload?.page ?? meta.page ?? 1),source:payload?.source ?? meta.source ?? null,asOf:payload?.as_of ?? payload?.asOf ?? meta.as_of ?? null,warning:payload?.warning ?? meta.warning ?? null,sectors:payload?.availableSectors ?? meta.availableSectors ?? []};
  }

  function populateSectorOptions(sectors){const select=$('sector');if(!select||!Array.isArray(sectors))return;const current=select.value;select.innerHTML='<option value="">全部板块</option>'+sectors.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');if([...select.options].some(option=>option.value===current))select.value=current;}

  function value(row,...keys){for(const key of keys){if(row[key]!==null&&row[key]!==undefined&&row[key]!=='')return row[key];}return null}
  function number(row,...keys){const v=value(row,...keys);if(v===null)return null;const n=Number(v);return Number.isFinite(n)?n:null}
  function formatNumber(v,d=2){return v===null?'<span class="missing" title="接口未提供该字段">—</span>':Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d})}
  function compact(v){if(v===null)return '<span class="missing" title="接口未提供该字段">—</span>';const a=Math.abs(v);if(a>=1e12)return `${(v/1e12).toFixed(2)}T`;if(a>=1e9)return `${(v/1e9).toFixed(2)}B`;if(a>=1e6)return `${(v/1e6).toFixed(2)}M`;if(a>=1e3)return `${(v/1e3).toFixed(1)}K`;return formatNumber(v,0)}
  function pct(v,ratio=false){if(v===null)return '<span class="missing" title="接口未提供该字段">—</span>';const n=ratio&&Math.abs(v)<=1?v*100:v;return `${formatNumber(n,2)}%`}
  function esc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}

  function trendLabel(row){
    const raw=value(row,'technical_status','trend','signal');
    if(raw){const s=String(raw);const cls=/bull|up|多|buy/i.test(s)?'bull':/bear|down|空|sell/i.test(s)?'bear':'';return `<span class="status-tag ${cls}">${esc(s)}</span>`;}
    const p=number(row,'price','regularMarketPrice'),e20=number(row,'ema20'),e50=number(row,'ema50');
    if(p!==null&&e20!==null&&e50!==null)return p>e20&&e20>e50?'<span class="status-tag bull">多头排列</span>':p<e20&&e20<e50?'<span class="status-tag bear">空头排列</span>':'<span class="status-tag">震荡</span>';
    return '<span class="missing" title="缺少技术指标">—</span>';
  }

  function renderRows(){
    const body=$('resultBody');
    if(!state.filtered.length){body.innerHTML='<tr><td colspan="10"><div class="empty-state"><b>没有匹配结果</b><span>放宽条件，或确认数据源覆盖了所选市场和字段</span></div></td></tr>';return;}
    body.innerHTML=state.filtered.map(row=>{
      const symbol=esc(value(row,'symbol','ticker')||'—'),name=esc(value(row,'name','company_name','shortName')||'名称缺失'),exchange=esc(value(row,'exchange')||'');
      const price=number(row,'price','regularMarketPrice','close'),change=number(row,'change_pct','changePercent','regularMarketChangePercent'),cap=number(row,'market_cap','marketCap'),volume=number(row,'volume','regularMarketVolume'),rv=number(row,'relative_volume','relativeVolume'),pe=number(row,'pe','trailingPE'),dy=number(row,'dividend_yield','dividendYield'),rsi=number(row,'rsi14','rsi');
      return `<tr><td><div class="symbol-cell"><span class="exchange-tag">${exchange||'—'}</span><div><b>${symbol}</b><span title="${name}">${name}</span></div></div></td><td>${price===null?formatNumber(null):'$'+formatNumber(price)}</td><td class="${change===null?'':change>=0?'positive':'negative'}">${pct(change)}</td><td>${compact(cap)}</td><td>${compact(volume)}</td><td>${formatNumber(rv,2)}</td><td>${formatNumber(pe,2)}</td><td>${pct(dy,true)}</td><td>${formatNumber(rsi,1)}</td><td>${trendLabel(row)}</td></tr>`;
    }).join('');
  }

  function applyTableSearch(){const q=$('tableSearch').value.trim();const fields=r=>[value(r,'symbol','ticker'),value(r,'name','company_name','shortName'),value(r,'sector'),value(r,'industry')];state.filtered=!q?state.rows:window.GVIWFuzzy?window.GVIWFuzzy.rank(q,state.rows,fields,0.5):state.rows.filter(r=>fields(r).some(v=>String(v||'').toLowerCase().includes(q.toLowerCase())));renderRows();updateMeta();}
  function updateMeta(){$('resultMeta').textContent=state.total?`${state.total.toLocaleString()} 只股票 · 本页 ${state.filtered.length} 只`:'0 只股票';$('pageMeta').textContent=`第 ${state.pages?state.page:0} / ${state.pages||0} 页`;$('prevButton').disabled=state.loading||state.page<=1;$('nextButton').disabled=state.loading||!state.pages||state.page>=state.pages;$('exportButton').disabled=!state.filtered.length;}
  function setLoading(on){$('runButton').disabled=on;$('runButton').textContent=on?'筛选中…':'运行筛选';if(on){$('resultBody').innerHTML='<tr><td colspan="10"><div class="loading-row"></div><div class="loading-row"></div><div class="loading-row"></div></td></tr>';setNotice('正在读取实时行情与基本面数据…','');}updateMeta();}
  function setNotice(text,type){const n=$('notice');n.textContent=text;n.className=`notice ${type||''}`;}
  function formatTime(raw){if(!raw)return '未提供';const d=new Date(raw);return Number.isNaN(d.getTime())?String(raw):d.toLocaleString('zh-CN',{hour12:false});}
  function updateActiveCount(){let n=0;ids.forEach(id=>{const el=$(id);if(el&&el.value!==''&&!(id==='market'&&el.value==='US'))n++;});n+=document.querySelectorAll('#exchangeGroup input:checked, #unusualVolume:checked, #newHigh:checked').length;$('activeFilterCount').textContent=`${n} 个已启用`;}
  function exportCsv(){const keys=['symbol','name','exchange','price','change_pct','market_cap','volume','relative_volume','pe','dividend_yield','rsi14','technical_status'];const csv=[keys.join(','),...state.filtered.map(r=>keys.map(k=>`"${String(r[k]??'').replace(/"/g,'""')}"`).join(','))].join('\n');const blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`screener-${new Date().toISOString().slice(0,10)}.csv`;a.click();URL.revokeObjectURL(a.href);}

  $('runButton').addEventListener('click',()=>runScreen(1));$('resetButton').addEventListener('click',()=>{clearFilters(true);state={...state,rows:[],filtered:[],page:1,total:0,pages:0};renderRows();updateMeta();setNotice('筛选条件已重置。','');});
  $('prevButton').addEventListener('click',()=>runScreen(state.page-1));$('nextButton').addEventListener('click',()=>runScreen(state.page+1));$('sortSelect').addEventListener('change',()=>runScreen(1));$('tableSearch').addEventListener('input',applyTableSearch);$('exportButton').addEventListener('click',exportCsv);
  $('collapseAllButton').addEventListener('click',()=>{const ds=[...document.querySelectorAll('.filter-panel details')];const shouldClose=ds.some(d=>d.open);ds.forEach(d=>d.open=!shouldClose);$('collapseAllButton').textContent=shouldClose?'全部展开':'全部收起';});
  document.querySelectorAll('[data-preset]').forEach(el=>el.addEventListener('click',()=>applyPreset(el.dataset.preset)));
  document.querySelectorAll('.filter-panel input,.filter-panel select').forEach(el=>el.addEventListener('change',updateActiveCount));
  window.addEventListener('message',event=>{if(event.data?.type==='screener:set-api-base'&&event.data.apiBase){window.SCREENER_API_BASE=event.data.apiBase;location.reload();}});
  updateActiveCount();updateMeta();
})();
