(function (global) {
  'use strict';

  const aliases = {
    '苹果': 'aapl apple', '苹果公司': 'aapl apple',
    '英伟达': 'nvda nvidia', '辉达': 'nvda nvidia',
    '微软': 'msft microsoft', '谷歌': 'goog googl alphabet',
    '亚马逊': 'amzn amazon', '特斯拉': 'tsla tesla',
    '脸书': 'meta facebook', '奈飞': 'nflx netflix',
    '台积电': 'tsm taiwan semiconductor', '阿里巴巴': 'baba alibaba',
    '腾讯': 'tcehy 0700.hk tencent', '百度': 'bidu baidu',
    '京东': 'jd jingdong', '拼多多': 'pdd pinduoduo',
    '哔哩哔哩': 'bili bilibili', '网易': 'ntes netease',
    '蔚来': 'nio', '小鹏': 'xpev', '理想汽车': 'li', '比亚迪': 'byddy 1211.hk byd',
    '小米': 'xiacy 1810.hk xiaomi', '摩根大通': 'jpm', '伯克希尔': 'brk-b berkshire',
    '可口可乐': 'ko coca cola', '麦当劳': 'mcd mcdonalds',
    '沃尔玛': 'wmt walmart', '迪士尼': 'dis disney', '高盛': 'gs goldman sachs',
    '波音': 'ba boeing', '英特尔': 'intc intel', '超微': 'amd advanced micro devices',
    '博通': 'avgo broadcom', '高通': 'qcom qualcomm', '甲骨文': 'orcl oracle',
    '科技': 'technology tech', '技术': 'technology tech', '半导体': 'semiconductor chip',
    '金融': 'financial finance', '银行': 'bank banking', '医疗': 'healthcare health',
    '能源': 'energy oil gas', '工业': 'industrials industrial', '房地产': 'real estate',
    '消费': 'consumer retail', '通信': 'communication telecom', '通讯': 'communication telecom',
    '公用事业': 'utilities utility', '材料': 'materials', '软件': 'software',
    '汽车': 'auto automotive', '电动车': 'ev electric vehicle',
    '市场': 'market', '股票': 'stock equity', '公司': 'company', '指数': 'index',
    '上涨': 'gain rising bullish positive', '下跌': 'loss falling bearish negative',
    '财报': 'earnings financial report', '分红': 'dividend', '回购': 'buyback'
  };

  function normalize(value) {
    return String(value == null ? '' : value)
      .normalize('NFKC')
      .toLocaleLowerCase('zh-CN')
      .replace(/[\s\-_./,:;，。；：、()（）\[\]【】'"“”‘’]+/g, ' ')
      .trim();
  }

  function expand(value) {
    const normalized = normalize(value);
    const additions = aliasTerms(normalized);
    return normalize([normalized, ...additions].join(' '));
  }

  function aliasTerms(value) {
    const normalized = normalize(value);
    const additions = [];
    Object.entries(aliases).forEach(([zh, terms]) => {
      const fuzzyChinese = normalized.length >= 2 && zh.length >= 2 && Math.abs(normalized.length - zh.length) <= 1 && distance(normalized, zh, 1) <= 1;
      if (normalized.includes(zh) || zh.includes(normalized) || fuzzyChinese) additions.push(...terms.split(' '));
    });
    return [...new Set(additions.filter(Boolean))];
  }

  function subsequence(query, text) {
    let qi = 0;
    for (let i = 0; i < text.length && qi < query.length; i += 1) {
      if (text[i] === query[qi]) qi += 1;
    }
    return qi === query.length;
  }

  function distance(a, b, maxDistance) {
    if (Math.abs(a.length - b.length) > maxDistance) return maxDistance + 1;
    let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i += 1) {
      const current = [i];
      let rowMin = current[0];
      for (let j = 1; j <= b.length; j += 1) {
        current[j] = Math.min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        rowMin = Math.min(rowMin, current[j]);
      }
      if (rowMin > maxDistance) return maxDistance + 1;
      previous = current;
    }
    return previous[b.length];
  }

  function score(queryValue, fields) {
    const normalizedQuery = normalize(queryValue);
    if (!normalizedQuery) return 1;
    const rawText = normalize(Array.isArray(fields) ? fields.join(' ') : fields);
    const text = expand(rawText);
    if (!text) return 0;
    const textTokens = text.split(' ').filter(Boolean);
    const queryVariants = [normalizedQuery, ...aliasTerms(normalizedQuery)];
    let variantBest = 0;
    for (const query of queryVariants) {
      if (rawText === query) variantBest = Math.max(variantBest, 1);
      else if (rawText.startsWith(query)) variantBest = Math.max(variantBest, 0.96);
      else if (rawText.includes(query)) variantBest = Math.max(variantBest, 0.92);
      const tokens = query.split(' ').filter(Boolean);
      let total = 0;
      for (const token of tokens) {
        let best = 0;
        for (const candidate of textTokens) {
          if (candidate === token) best = Math.max(best, 1);
          else if (candidate.startsWith(token)) best = Math.max(best, 0.92);
          else if (candidate.includes(token)) best = Math.max(best, 0.84);
          else if (token.length >= 2 && subsequence(token, candidate)) best = Math.max(best, 0.68);
          else if (token.length >= 3) {
            const maxDistance = token.length <= 5 ? 1 : 2;
            const edit = distance(token, candidate, maxDistance);
            if (edit <= maxDistance) best = Math.max(best, 0.72 - edit * 0.1);
          }
        }
        total += best;
      }
      variantBest = Math.max(variantBest, total / Math.max(tokens.length, 1));
    }
    return variantBest;
  }

  function matches(query, fields, threshold) {
    return score(query, fields) >= (threshold == null ? 0.55 : threshold);
  }

  function rank(query, items, fieldSelector, threshold) {
    if (!normalize(query)) return items.slice();
    return items.map((item, index) => ({ item, index, score: score(query, fieldSelector(item)) }))
      .filter(entry => entry.score >= (threshold == null ? 0.55 : threshold))
      .sort((a, b) => b.score - a.score || a.index - b.index)
      .map(entry => entry.item);
  }

  global.GVIWFuzzy = { aliases, normalize, expand, score, matches, rank };
})(window);
