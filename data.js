/*
 * 把采集脚本生成的 window.PRICE_DATA（data/prices.js）整理成页面使用的结构。
 * 数据来源：scraper/fetch_prices.py
 */
(function () {
  const raw = window.PRICE_DATA;
  if (!raw || !raw.entities) {
    window.MARKET = null;
    return;
  }

  const parseDay = (s) => {
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
  };
  // 每根K线标上数据来源：落在 segments 区间内的用该段来源，其余是本站自己采集的
  const toBars = (arr, e) => {
    const segs = (e && e.segments) || [];
    const own = (e && e.source) || '';
    const pm = (e && e.points_meta) || {};
    const pointSources = new Set(Object.values(pm).map((m) => m.source || '媒体报道整理（口径不一）'));
    // 单点来源（报道、月度参考价）按点判断；其余按区间（PCPartPicker 等）
    const rangeSegs = segs.filter((s) => !pointSources.has(s.source));
    return (arr || []).map(([day, o, h, l, c]) => {
      if (pm[day]) return { t: parseDay(day), day, o, h, l, c, src: pm[day].source || '媒体报道整理（口径不一）' };
      const seg = rangeSegs.find((s) => s.from <= day && day <= s.to);
      return { t: parseDay(day), day, o, h, l, c, src: seg ? seg.source : own };
    });
  };

  const E = raw.entities;
  const order = (raw.order || []).filter((id) => E[id] && E[id].kind === 'index');
  for (const id of Object.keys(E)) if (E[id].kind === 'index' && !order.includes(id)) order.push(id);

  const categories = [];
  const byId = {};
  for (const id of order) {
    const e = E[id];
    if (!e.bars || !e.bars.length) continue;
    const region = e.region || (String(e.group || '').includes('DRAMeXchange') ? 'GLOBAL' : 'CN');
    const cat = Object.assign({}, e, { id, kind: 'index', region, bars: toBars(e.bars, e), items: [] });
    categories.push(cat);
    byId[id] = cat;
  }

  const items = [];
  for (const id of Object.keys(E)) {
    const e = E[id];
    if (e.kind !== 'item' || !e.bars || !e.bars.length) continue;
    const cat = byId[e.cat];
    if (!cat) continue;
    const it = Object.assign({}, e, { id, kind: 'item', region: cat.region, name: `${e.brand} ${e.model}`, cat, bars: toBars(e.bars, e) });
    cat.items.push(it);
    items.push(it);
    byId[id] = it;
  }

  // 同一地区内按品类排序：内存 → 显卡 → 处理器 → 硬盘（数据文件里的顺序取决于采集顺序）
  const GROUP_ORDER = ['内存', 'NVIDIA 显卡', 'AMD 显卡', 'Intel 显卡', '显卡', '处理器', '硬盘'];
  const gi = (c) => { const i = GROUP_ORDER.indexOf(c.group); return i < 0 ? 99 : i; };
  // 组内顺序：先按固定顺序，其余（显卡）按数据文件里的顺序，也就是 scraper/gpus.json 的型号顺序
  const KEY_ORDER = ['ddr5', 'ddr5ch', 'ddr4', 'ddr4ch', 'ryzen9000', 'r5z5', 'r7z5', 'r9z5', 'ultra200s', 'u5al', 'u7al', 'u9al', 'ssd2t', 'ssd2tch'];
  const strip = (id) => id.replace(/^(us|ca)-/, '').replace(/^5090d$/, '5090');
  const orderIdx = {};
  (raw.order || []).forEach((id, i) => { const k = strip(id); if (!(k in orderIdx)) orderIdx[k] = i; });
  const ki = (c) => {
    const k = strip(c.id), i = KEY_ORDER.indexOf(k);
    return i >= 0 ? i : 100 + (k in orderIdx ? orderIdx[k] : 9999);
  };
  categories.sort((a, b) => gi(a) - gi(b) || ki(a) - ki(b));

  let latestDay = null;
  for (const c of categories) {
    const d = c.bars[c.bars.length - 1].day;
    if (!latestDay || d > latestDay) latestDay = d;
  }

  // 汇率：{ 'YYYY-MM-DD': { USD: 每美元人民币, CAD: 每加元人民币 } }
  const fx = raw.fx || {};
  const fxDay = Object.keys(fx).sort().pop() || null;

  window.MARKET = {
    categories, items, byId, updated: raw.updated, latestDay, runs: raw.runs || [],
    fx, fxDay, fxLatest: fxDay ? fx[fxDay] : null,
    health: raw.health || {},
  };
})();
