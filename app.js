(function () {
  const M = window.MARKET;
  const $ = (s) => document.querySelector(s);

  // ---------- 没有数据时 ----------
  if (!M || !M.categories.length) {
    document.querySelector('.layout').innerHTML = `
      <div class="nodata card">
        <h2>还没有价格数据</h2>
        <p>先在项目文件夹里运行一次采集脚本：</p>
        <pre>python scraper/fetch_prices.py</pre>
        <p>运行后会生成 <code>data/prices.js</code>，刷新本页即可。</p>
      </div>`;
    return;
  }

  const REGIONS = [
    { id: 'CN', label: '国内' },
    { id: 'US', label: '美国' },
    { id: 'CA', label: '加拿大' },
    { id: 'GLOBAL', label: '全球现货' },
  ].filter((r) => M.categories.some((c) => c.region === r.id));
  const regionLabel = (id) => (REGIONS.find((r) => r.id === id) || { label: id }).label;
  const regionOf = (e) => (e.kind === 'item' ? e.cat.region : e.region);
  const catsIn = (region) => M.categories.filter((c) => c.region === region);

  const state = {
    region: REGIONS[0].id,
    tab: 'index',          // 'index' 大盘 | 'detail' 细节
    selected: catsIn(REGIONS[0].id)[0].id,
    range: 90,
    type: 'candle',        // 'candle' | 'line'
    search: '',
    expanded: {},          // 细节页中展开的品类
    sort: { key: 'price', dir: -1 },
    hover: null,
  };

  // ---------- 工具函数 ----------
  const fmt = (n, d = 0) =>
    Number(n).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
  const pad2 = (n) => String(n).padStart(2, '0');
  const fmtDate = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const fmtShort = (d) => `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const cls = (v) => (v == null ? 'flat' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat');
  const sgn = (v) => (v > 0 ? '+' : '');
  const pctStr = (v) => (v == null ? '--' : `${sgn(v)}${(v * 100).toFixed(2)}%`);
  const last = (e) => e.bars[e.bars.length - 1];
  const DAY = 86400000;

  const dec = (e) => e.decimals || 0;
  const px = (e, n) => fmt(n, dec(e));
  const SYM = { USD: '$', CAD: 'C$', PTS: '', CNY: '¥' };
  const UNIT = { USD: '美元', CAD: '加元', PTS: '点', CNY: '元' };
  const sym = (e) => SYM[e.currency] ?? '¥';
  const unit = (e) => UNIT[e.currency] || '元';
  // 折合人民币（用最新汇率）；没有汇率或是指数点时返回 null
  const toCNY = (e, v) => {
    if (e.currency === 'CNY') return v;
    const r = M.fxLatest && M.fxLatest[e.currency];
    return r ? v * r : null;
  };
  const isPoints = (e) => e.currency === 'PTS';

  // 单点数据（媒体报道整理的价格点）：没有日K意义，不算日涨跌和均线
  const isPoint = (e, b) => !!(b && e.points_meta && e.points_meta[b.day] && b.h === b.l);

  // 最新一根数据离全站最新日期超过 60 天：多半是停产 / 退市后没有报价了，最新价不能当现价看
  const STALE_DAYS = 60;
  const staleSince = (e) => {
    const d = last(e).day;
    return M.latestDay && (new Date(M.latestDay) - new Date(d)) / 864e5 > STALE_DAYS ? d : null;
  };

  function dayChange(e) {
    const b = e.bars, n = b.length;
    if (isPoint(e, b[n - 1])) return null;
    if (n < 2) {
      // 首日：现货数据源自带涨跌（开盘由涨跌幅反推），ZOL 首日开盘=收盘则视为无涨跌
      const f = b[0];
      return f && f.o !== f.c ? { abs: f.c - f.o, pct: (f.c - f.o) / f.o } : null;
    }
    if (b[n - 2].src !== b[n - 1].src) return null; // 数据源切换处不算涨跌（口径不同）
    const d = b[n - 1].c - b[n - 2].c;
    return { abs: d, pct: d / b[n - 2].c };
  }
  // 与 days 天前相比：起点必须落在目标日期附近（允许 max(3 天, 15%) 的空缺），否则返回 null
  function baseBar(e, days) {
    const b = e.bars;
    const target = last(e).t.getTime() - days * DAY;
    const tol = Math.max(3, days * 0.15) * DAY;
    for (let i = b.length - 1; i >= 0; i--) {
      const t = b[i].t.getTime();
      if (t <= target) return target - t <= tol ? b[i] : null;
    }
    return null;
  }
  function periodChange(e, days) {
    if (days < 90 && isPoint(e, last(e))) return null; // 报道点很稀疏，短周期涨跌没有意义
    const base = baseBar(e, days);
    return base ? last(e).c / base.c - 1 : null;
  }
  // 涨跌的起点和终点来自不同数据源时，加 * 提示
  function periodStat(k, e, days) {
    const v = periodChange(e, days);
    const base = baseBar(e, days);
    const cross = base && base.src !== last(e).src;
    return [cross ? `<span title="起点来自 ${esc(base.src)}，终点来自 ${esc(last(e).src)}，口径不同">${k}*</span>` : k,
      pctStr(v), cls(v)];
  }
  function sinceFirst(e) {
    return e.bars.length < 2 ? null : last(e).c / e.bars[0].c - 1;
  }
  function hiLo(bars) {
    let h = -Infinity, l = Infinity;
    for (const x of bars) { if (x.h > h) h = x.h; if (x.l < l) l = x.l; }
    return { h, l };
  }
  function cssVar(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  }
  function sparkline(bars, w = 96, h = 26) {
    if (bars.length < 2) return `<span class="muted">积累中</span>`;
    const c = bars.map((b) => b.c);
    const min = Math.min(...c), max = Math.max(...c), span = max - min || 1;
    const pts = c.map((v, i) => `${(i / (c.length - 1)) * w},${h - 2 - ((v - min) / span) * (h - 4)}`).join(' ');
    const col = c[c.length - 1] > c[0] ? 'var(--up)' : c[c.length - 1] < c[0] ? 'var(--down)' : 'var(--flat)';
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  }
  function matches(text) {
    const q = state.search.trim().toLowerCase();
    return !q || text.toLowerCase().includes(q);
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));

  // ---------- 左侧列表 ----------
  // 当天参与计算的报价条数（= 最后一天有价格的细节条目数）；没有细节的大盘返回 null
  function samples(cat) {
    if (!cat.items || !cat.items.length) return null;
    const d = last(cat).day;
    return cat.items.filter((it) => last(it).day === d).length;
  }

  function rowHtml(e, sub) {
    const ch = dayChange(e);
    const n = e.kind === 'index' ? samples(e) : null;
    const old = e.kind === 'index' ? staleSince(e) : null;
    const few = old ? `<span class="few" title="数据止于 ${old}，之后没有找到这个型号的报价（可能已停产或退市）">停更</span>`
      : n != null && n < 3 ? `<span class="few" title="当天只有 ${n} 条报价，价格参考性低">样本少</span>` : '';
    const titleHtml = e.kind === 'index' ? esc(e.name) + few : esc(e.brand);
    const subtitle = e.kind === 'index' ? e.spec : e.model;
    const stale = e.kind === 'item' && last(e).day < last(e.cat).day;
    return `<div class="row${sub ? ' sub' : ''}${state.selected === e.id ? ' active' : ''}${stale ? ' stale' : ''}" data-id="${esc(e.id)}" title="${esc(e.fullname || e.spec || '')}">
      <div class="nm"><b>${titleHtml}</b><small>${esc(subtitle)}</small></div>
      <div class="px num ${cls(ch && ch.abs)}">${px(e, last(e).c)}</div>
      <div><span class="pill num ${cls(ch && ch.abs)}">${pctStr(ch && ch.pct)}</span></div>
    </div>`;
  }

  function renderList() {
    const list = $('#list');
    let html = '';
    const cats = catsIn(state.region);
    if (state.tab === 'index') {
      let group = null;
      for (const cat of cats) {
        if (!matches(`${cat.name} ${cat.code} ${cat.spec} ${cat.group}`)) continue;
        if (cat.group !== group) { group = cat.group; html += `<div class="group-label">${esc(group)}</div>`; }
        html += rowHtml(cat, false);
      }
    } else {
      const searching = !!state.search.trim();
      for (const cat of cats) {
        if (!cat.items.length) continue;
        const catHit = matches(`${cat.name} ${cat.code} ${cat.group}`);
        const items = cat.items.filter((it) => catHit || matches(`${it.brand} ${it.model} ${it.fullname || ''}`));
        if (!items.length) continue;
        const open = state.expanded[cat.id] || searching;
        const ch = dayChange(cat);
        html += `<div class="cat-head${open ? '' : ' collapsed'}" data-cat="${esc(cat.id)}">
          <span class="arrow">▾</span>
          <span>${esc(cat.name)}<span class="cnt">${items.length}</span></span>
          <span class="num ${cls(ch && ch.abs)}">${pctStr(ch && ch.pct)}</span>
        </div>`;
        if (open) html += items.map((it) => rowHtml(it, true)).join('');
      }
    }
    list.innerHTML = html || `<div class="empty">${state.tab === 'detail' && !state.search.trim() ? '该地区没有细分品牌' : '没有匹配的结果'}</div>`;
  }

  function renderRegions() {
    $('#regionSeg').innerHTML = REGIONS.map((r) =>
      `<button class="region${r.id === state.region ? ' active' : ''}" data-region="${r.id}" role="tab">${r.label}</button>`).join('');
  }

  // 切换地区：优先选中同一型号在新地区的大盘（如 国内 5070 → 美国 5070）
  function switchRegion(region) {
    if (region === state.region) return;
    const cur = M.byId[state.selected];
    const curCat = cur.kind === 'item' ? cur.cat : cur;
    const cats = catsIn(region);
    const peer = curCat.compare && cats.find((c) => c.compare === curCat.compare);
    const target = peer || cats[0];
    // 地区本身由 select() 根据目标实体切换（同时重绘地区按钮和滚动行情）
    if (state.tab === 'detail' && target.items.length) select(target.items[0].id);
    else { if (state.tab === 'detail') setTab('index', false); select(target.id); }
  }

  function visibleRowIds() {
    return [...document.querySelectorAll('#list .row')].map((r) => r.dataset.id);
  }

  // ---------- 滚动行情条 ----------
  function renderTicker() {
    const one = catsIn(state.region).map((c) => {
      const ch = dayChange(c);
      return `<span class="tick-item" data-id="${esc(c.id)}"><span class="nm">${esc(c.name)}</span><span class="num">${sym(c)}${px(c, last(c).c)}</span><span class="num ${cls(ch && ch.abs)}">${pctStr(ch && ch.pct)}</span></span>`;
    }).join('');
    const track = $('#tickerTrack');
    track.innerHTML = one + one;
    track.style.animationDuration = `${Math.max(20, catsIn(state.region).length * 6)}s`;
  }

  // ---------- 跨地区对比 ----------
  function renderCompare(e) {
    const cat = e.kind === 'index' ? e : e.cat;
    const card = $('#compareCard');
    const peers = cat.compare ? M.categories.filter((c) => c.compare === cat.compare) : [];
    if (peers.length < 2) { card.hidden = true; return; }
    card.hidden = false;
    const order = ['CN', 'US', 'CA'];
    peers.sort((a, b) => order.indexOf(a.region) - order.indexOf(b.region));
    const cn = peers.find((p) => p.region === 'CN');
    const cnPrice = cn ? last(cn).c : null;
    const rows = peers.map((p) => {
      const local = last(p).c;
      const rmb = toCNY(p, local);
      const vs = cnPrice && rmb != null && p !== cn ? rmb / cnPrice - 1 : null;
      const ch = dayChange(p);
      const lastDay = last(p).day;
      const staleDays = Math.round((Date.now() - last(p).t.getTime()) / DAY);
      return `<tr class="r${p.id === cat.id ? ' current' : ''}" data-id="${esc(p.id)}">
        <td><span class="brand">${regionLabel(p.region)}</span><span class="model">${esc(p.spec)}${staleDays > 7 ? ` · <span class="q-hist">数据截至 ${lastDay}</span>` : ''}</span></td>
        <td class="num">${sym(p)}${px(p, local)}</td>
        <td class="num">${rmb == null ? '--' : '¥' + fmt(rmb)}</td>
        <td class="num">${p === cn ? '基准' : pctStr(vs)}</td>
        <td class="num ${cls(ch && ch.pct)}">${pctStr(ch && ch.pct)}</td>
        <td class="num">${p.items.length}</td>
      </tr>`;
    }).join('');
    $('#compareTable').innerHTML = `<thead><tr><th>地区</th><th>本地中位价</th><th>折合人民币</th><th>相对国内</th><th>日涨跌</th><th>报价数</th></tr></thead><tbody>${rows}</tbody>`;
    $('#compareSub').textContent = `${cat.name} · 点击行切换地区`;
    const fx = M.fxLatest;
    $('#compareNote').innerHTML = (fx
      ? `汇率 ${M.fxDay}：1 美元 = ${fx.USD} 元，1 加元 = ${fx.CAD} 元（欧洲央行参考汇率）。`
      : '暂无汇率数据。') +
      ' 各地区口径不同，仅供参考：' +
      peers.map((p) => `${regionLabel(p.region)} = ${esc(last(p).src || p.source)}`).join('；') +
      '。美国 / 加拿大标价不含销售税，国内价格含 13% 增值税。';
  }

  // ---------- 报价头 ----------
  function renderQuote(e) {
    const b = e.bars, n = b.length, lb = b[n - 1], prev = n > 1 ? b[n - 2] : null;
    const ch = dayChange(e);
    const y = hiLo(b);
    const isIdx = e.kind === 'index';
    const cat = isIdx ? e : e.cat;

    const stat = (k, v, c = '') => [k, v, c];
    const stats = [
      stat('今开', px(e, lb.o)), stat('最高', px(e, lb.h)), stat('最低', px(e, lb.l)),
      stat('昨收', prev && prev.src === lb.src && !isPoint(e, lb) ? px(e, prev.c) : '--'),
      periodStat('7日', e, 7), periodStat('30日', e, 30),
      periodStat('1年', e, 365), stat('起始日', b[0].day),
      stat('历史最高', px(e, y.h)), stat('历史最低', px(e, y.l)),
    ];
    if (!isIdx) {
      if (isPoints(cat)) {
        stats.push(stat('系列指数', px(cat, last(cat).c)));
      } else {
        const vs = lb.c / last(cat).c - 1;
        stats.push(stat('相对大盘', pctStr(vs)));
        stats.push(stat('大盘中位价', px(cat, last(cat).c)));
      }
    } else {
      stats.push(stat('记录天数', String(n)));
      if (e.msrp) stats.push(stat('首发价', `${sym(e)}${px(e, e.msrp.p)}`));
      const sn = samples(e);
      stats.push(sn != null ? stat('当天报价', `${sn} 条`, sn < 3 ? 'down' : '') : stat('单位', unit(e)));
    }

    const linkLabel = e.store ? `${e.store} ↗` : 'ZOL 产品页 ↗';
    const onlyHistory = (e.segments || []).length && (e.segments || []).every((s) => s.source === e.source);
    const hist = onlyHistory
      ? `${esc(e.source)}，数据截至 ${lb.day}；这个地区的该品类暂时没有每日现价来源`
      : (e.segments || []).map((s) => `${esc(s.source)} ${s.from} → ${s.to}`).join('；') +
        (e.source === 'ZOL 参考价' ? '注意：ZOL 参考价基本不随市场变动，可能明显低于实际售价' : '');
    const sub = isIdx
      ? `${regionLabel(e.region)} · ${esc(e.spec)} · <a class="q-link" href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.source)} ↗</a>` +
        (onlyHistory ? `<br><span class="q-hist">${hist}</span>`
          : hist && e.segments && e.segments.length ? `<br><span class="q-hist">历史：${hist}${staleSince(e) ? '' : `；之后为 ${esc(e.source)}，口径不同`}</span>`
          : hist ? `<br><span class="q-hist">${hist}</span>` : '') +
        (e.est && e.est.length ? `<br><span class="q-hist">${e.msrp ? `首发（${e.msrp.day}）` : e.est[0].day.slice(0, 7)}以来有 ${e.est.length} 个月没有实测数据，图上用灰带标出估算区间（非实测，悬停看依据），切到「全部」可看全程</span>` : '') +
        (staleSince(e) ? `<br><span class="q-hist warn">⚠ 数据止于 ${staleSince(e)}，之后没有找到这个型号的报价（可能已停产或退市），下面的价格不是现价</span>` : '')
      : `${regionLabel(cat.region)} · 所属 <span class="q-link" data-goto="${esc(cat.id)}">${esc(cat.name)} 大盘 →</span> · <a class="q-link" href="${esc(e.url)}" target="_blank" rel="noopener nofollow">${esc(linkLabel)}</a>`;
    const rmb = cat.region === 'GLOBAL' ? null : toCNY(e, lb.c);
    const rmbHint = rmb != null && e.currency !== 'CNY' ? `<span class="rmb">≈ ¥${fmt(rmb)}</span>` : '';

    $('#quote').innerHTML = `
      <div class="q-main">
        <div class="q-title">
          <h1 title="${esc(e.fullname || e.name)}">${esc(e.name)}</h1>
          <span class="q-code num">${esc(cat.code)}</span>
          <span class="badge">${isIdx ? '大盘' : '细节'}</span>
        </div>
        <div class="q-sub">${sub}</div>
      </div>
      <div class="q-price ${cls(ch && ch.abs)}">
        <div class="big num">${sym(e)}${px(e, lb.c)}${isPoints(e) ? '<small> 点</small>' : ''}</div>
        <div class="chg num">${ch
          ? `<span>${sgn(ch.abs)}${px(e, ch.abs)}</span><span>${pctStr(ch.pct)}</span>`
          : `<span class="flat">${isPoint(e, lb) ? `${esc(String(lb.src || '').replace(/（.*$/, ''))}单点 · 不计涨跌` : '首日记录 · 暂无涨跌'}</span>`}${rmbHint}</div>
      </div>
      <div class="q-stats">
        ${stats.map(([k, v, c]) => `<div class="stat"><span class="k">${k}</span><span class="num ${c}">${v}</span></div>`).join('')}
      </div>`;
  }

  // ---------- 品牌表格 ----------
  function renderTable(e) {
    const cat = e.kind === 'index' ? e : e.cat;
    const card = $('.table-card');
    if (!cat.items.length) {
      $('#tableTitle').innerHTML = `${esc(cat.name)}<small>该指标是整体现货价，没有细分品牌</small>`;
      $('#table').innerHTML = '';
      card.classList.add('empty-table');
      return;
    }
    card.classList.remove('empty-table');
    const pts = isPoints(cat);
    const COLS = [
      { key: 'name', label: '品牌 / 型号' },
      { key: 'price', label: '最新价' },
      { key: 'pct', label: '日涨跌' },
      { key: 'w', label: '7日' },
      { key: 'm', label: '30日' },
      { key: 'all', label: '累计' },
      { key: 'prem', label: pts ? '' : '相对大盘', nosort: pts },
      { key: 'spark', label: '近30日', nosort: true },
    ];
    const avg = last(cat).c;
    const rows = cat.items.map((it) => {
      const ch = dayChange(it);
      return {
        it,
        name: it.name,
        price: last(it).c,
        pct: ch ? ch.pct : null,
        w: periodChange(it, 7),
        m: periodChange(it, 30),
        all: sinceFirst(it),
        prem: pts ? null : last(it).c / avg - 1,
        stale: last(it).day < last(cat).day,
      };
    });
    const { key, dir } = state.sort;
    const val = (v) => (v == null ? -Infinity : v);
    rows.sort((a, b) => (key === 'name' ? a.name.localeCompare(b.name, 'zh') : val(a[key]) - val(b[key])) * dir);

    $('#tableTitle').innerHTML = e.kind === 'index'
      ? `${esc(cat.name)} · 跟踪型号<small>${cat.items.length} 款，点击查看细节</small>`
      : `同类对比 · ${esc(cat.name)}<small>当前：${esc(e.name)}</small>`;

    const head = `<tr>${COLS.map((c) => {
      const s = state.sort.key === c.key;
      return `<th data-sort="${c.nosort ? '' : c.key}" class="${s ? 'sorted' : ''}">${c.label}${s ? (dir > 0 ? ' ↑' : ' ↓') : ''}</th>`;
    }).join('')}</tr>`;

    const body = rows.map((r) => `<tr class="r${r.it.id === e.id ? ' current' : ''}${r.stale ? ' stale' : ''}" data-id="${esc(r.it.id)}" title="${esc(r.it.fullname || r.it.name)}${r.stale ? `（${r.it.bars[r.it.bars.length - 1].day} 之后未再出现）` : ''}">
      <td><span class="brand">${esc(r.it.brand)}</span><span class="model">${esc(r.it.model)}</span></td>
      <td class="num">${px(r.it, r.price)}</td>
      <td class="num ${cls(r.pct)}">${pctStr(r.pct)}</td>
      <td class="num ${cls(r.w)}">${pctStr(r.w)}</td>
      <td class="num ${cls(r.m)}">${pctStr(r.m)}</td>
      <td class="num ${cls(r.all)}">${pctStr(r.all)}</td>
      <td class="num">${pts ? '' : pctStr(r.prem)}</td>
      <td>${sparkline(r.it.bars.slice(-30))}</td>
    </tr>`).join('');

    $('#table').innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  }

  // ---------- K线图 ----------
  const canvas = $('#chart');
  const ctx = canvas.getContext('2d');
  let view = null;
  let geom = null;

  function weekKey(d) {
    const x = new Date(d);
    x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); // 本周一
    return x.getTime();
  }
  function aggregateWeekly(bars) {
    const out = [];
    let cur = null, key = null;
    for (const b of bars) {
      const k = weekKey(b.t);
      if (k !== key || b.src !== cur.src) {
        cur = { t: b.t, day: b.day, o: b.o, h: b.h, l: b.l, c: b.c, src: b.src };
        out.push(cur);
        key = k;
      } else {
        cur.t = b.t; cur.day = b.day; cur.c = b.c;
        cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l);
      }
    }
    return out;
  }
  function movingAvg(bars, n) {
    let sum = 0;
    return bars.map((b, i) => {
      sum += b.c;
      if (i >= n) sum -= bars[i - n].c;
      return i >= n - 1 ? sum / n : null;
    });
  }

  function buildView(e) {
    const weekly = state.range > 130;
    const all = weekly ? aggregateWeekly(e.bars) : e.bars;
    const cutoff = last(e).t.getTime() - state.range * DAY;
    let start = all.findIndex((b) => b.t.getTime() > cutoff);
    if (start < 0) start = all.length - 1;
    // 均线只在连续的日K数据上计算：窗口里含报道单点的位置不画
    const maskPts = (arr, n) => arr.map((v, i) => {
      for (let k = Math.max(0, i - n + 1); k <= i; k++) if (isPoint(e, all[k])) return null;
      return v;
    });
    const ma5 = maskPts(movingAvg(all, 5), 5), ma20 = maskPts(movingAvg(all, 20), 20);
    // 早期估算区间：只取时间窗内、实测数据开始之前的月份
    const est = (e.est || []).filter((b) => b.t.getTime() > cutoff);
    const launchIn = e.msrp && e.msrp.t.getTime() > cutoff;
    view = {
      entity: e,
      est,
      msrp: e.msrp || null,
      // 首发价线：窗口里有估算区间或首发日时，纵轴把首发价也包进来；否则只在落在纵轴范围内时才画
      msrpFit: !!(e.msrp && (est.length || launchIn)),
      weekly,
      bars: all.slice(start),
      prevClose: start > 0 ? all[start - 1].c : all[0].o,
      ma5: ma5.slice(start),
      ma20: ma20.slice(start),
      totalDays: e.bars.length,
    };
  }

  // 坐标轴小数位随价格区间大小变化
  const axisDecimals = (span) => (span >= 50 ? 0 : span >= 5 ? 1 : 2);

  function drawChart() {
    if (!view) return;
    const wrap = canvas.parentElement;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const C = {
      up: cssVar('--up'), down: cssVar('--down'), flat: cssVar('--flat'), grid: cssVar('--grid'),
      muted: cssVar('--muted'), text: cssVar('--text'), ma5: cssVar('--ma5'), ma20: cssVar('--ma20'),
      panel: cssVar('--panel-2'), border: cssVar('--border'),
    };
    const mono = '11px ' + cssVar('--mono');
    const pad = { l: 8, r: 72, t: 12, b: 24 };
    const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const bars = view.bars, n = bars.length;
    const e = view.entity;
    // 横轴按真实时间排列（历史数据有缺口、季度报道点很稀疏，按序号等距会把时间压扁）
    const est = view.est || [];
    const HALF = 15 * DAY; // 估算区间每月一格，左右各半个月
    const t0 = Math.min(bars[0].t.getTime(), est.length ? est[0].t.getTime() - HALF : Infinity);
    const t1 = Math.max(bars[n - 1].t.getTime(), est.length ? est[est.length - 1].t.getTime() + HALF : 0);
    const edge = Math.min(10, pw / (2 * Math.max(n, 1)));
    const tx = (t) => (t1 > t0 ? pad.l + edge + ((t - t0) / (t1 - t0)) * (pw - 2 * edge) : pad.l + pw / 2);
    const xs = bars.map((b) => tx(b.t.getTime()));
    const exs = est.map((b) => tx(b.t.getTime()));
    const dxs = xs.slice(1).map((v, i) => v - xs[i]).sort((a, b) => a - b);
    const step = dxs.length ? Math.max(1, dxs[Math.floor(dxs.length / 2)]) : pw / 2; // 典型K线间距

    let lo = Infinity, hi = -Infinity;
    const consider = (v) => { if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; } };
    bars.forEach((b, i) => {
      if (state.type === 'candle') { consider(b.h); consider(b.l); } else consider(b.c);
      consider(view.ma5[i]); consider(view.ma20[i]);
    });
    const dataLo = lo, dataHi = hi;
    est.forEach((b) => { consider(b.lo); consider(b.hi); });
    if (view.msrpFit) consider(view.msrp.p);
    const m = (hi - lo) * 0.08 || Math.abs(hi) * 0.03 || 1;
    lo = lo >= 0 ? Math.max(0, lo - m) : lo - m; hi += m;
    const y = (p) => pad.t + ((hi - p) / (hi - lo)) * ph;
    const x = (i) => xs[i];
    const ad = axisDecimals(hi - lo);
    const axisFmt = (v) => fmt(v, ad);
    geom = { pad, step, n, hi, lo, ph, pw, W, H, axisFmt, xs, exs };
    const showMsrp = view.msrp && (view.msrpFit || (view.msrp.p >= dataLo && view.msrp.p <= dataHi));

    // 网格 + 价格轴
    ctx.font = mono;
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 1;
    const ticks = 5;
    for (let k = 0; k <= ticks; k++) {
      const p = lo + ((hi - lo) * k) / ticks;
      const yy = Math.round(y(p)) + 0.5;
      ctx.strokeStyle = C.grid;
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(pad.l + pw, yy); ctx.stroke();
      ctx.fillStyle = C.muted;
      ctx.textAlign = 'left';
      ctx.fillText(axisFmt(p), pad.l + pw + 8, yy);
    }
    // 日期轴
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const multiYear = t1 - t0 > 300 * DAY;
    const dateLabel = (d) => (multiYear ? `${d.getFullYear()}-${pad2(d.getMonth() + 1)}` : fmtShort(d));
    const labelPts = est.map((b, j) => [exs[j], b.t]).concat(bars.map((b, i) => [xs[i], b.t])).sort((p, q) => p[0] - q[0]);
    let lastLabelX = Infinity;
    for (let i = labelPts.length - 1; i >= 0; i--) {
      const [lx0, lt] = labelPts[i];
      if (lastLabelX - lx0 < 90) continue; // 标签之间至少留 90px
      lastLabelX = lx0;
      const xx = Math.round(lx0) + 0.5;
      ctx.strokeStyle = C.grid;
      ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, pad.t + ph); ctx.stroke();
      ctx.fillStyle = C.muted;
      ctx.fillText(dateLabel(lt), xx, pad.t + ph + 7);
    }

    // 早期估算区间：灰色带（高 = 区间上沿，低 = 下沿），中线虚线；有报道 / 成交数据的月份颜色深一些
    if (est.length) {
      const btimes = bars.map((b) => b.t.getTime());
      const gapPx = Math.max(2, step / 2);
      // 每格估算的左右边界：不压到前后的实测K线
      const bandEdges = (b) => {
        const tc = b.t.getTime();
        let j = 0;
        while (j < n && btimes[j] < tc) j++;
        let xa = Math.max(pad.l, tx(tc - HALF)), xb = Math.min(pad.l + pw, tx(tc + HALF));
        if (j > 0) xa = Math.max(xa, xs[j - 1] + gapPx);
        if (j < n) xb = Math.min(xb, xs[j] - gapPx);
        return [xa, xb];
      };
      view.bandEdges = bandEdges;
      for (let j = 0; j < est.length; j++) {
        const b = est[j];
        const [xa, xb] = bandEdges(b);
        if (xb <= xa) continue;
        ctx.fillStyle = hexA(C.flat, b.k === 'a' ? 0.32 : 0.16);
        ctx.fillRect(xa, y(b.hi), xb - xa - 0.5, Math.max(1, y(b.lo) - y(b.hi)));
      }
      // 中线：只连相邻月份，跨过实测数据的地方断开
      ctx.beginPath();
      est.forEach((b, j) => {
        const m = (b.lo + b.hi) / 2;
        const cont = j && b.t - est[j - 1].t < 45 * DAY;
        cont ? ctx.lineTo(exs[j], y(m)) : ctx.moveTo(exs[j], y(m));
      });
      ctx.strokeStyle = C.flat;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = '11px ' + cssVar('--mono');
      ctx.fillStyle = C.muted;
      ctx.textBaseline = 'bottom';
      ctx.textAlign = 'left';
      // 放在左下角：顶部留给数据来源切换标签
      ctx.fillText('灰带 = 估算区间（非实测，悬停看依据）', pad.l + 4, pad.t + ph - 4);
      ctx.font = mono;
    }

    // 首发价参考线
    if (showMsrp) {
      const my = Math.round(y(view.msrp.p)) + 0.5;
      ctx.strokeStyle = C.ma20;
      ctx.setLineDash([1, 3]);
      ctx.beginPath(); ctx.moveTo(pad.l, my); ctx.lineTo(pad.l + pw, my); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = '11px ' + cssVar('--mono');
      ctx.fillStyle = C.ma20;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText(`首发价 ${sym(e)}${px(e, view.msrp.p)}（${view.msrp.day}）`, pad.l + pw - 4, my - 2);
      const lt = view.msrp.t.getTime();
      if (lt >= t0 && lt <= t1) {
        const lx = tx(lt);
        ctx.beginPath();
        ctx.moveTo(lx, my - 5); ctx.lineTo(lx + 5, my); ctx.lineTo(lx, my + 5); ctx.lineTo(lx - 5, my); ctx.closePath();
        ctx.fill();
      }
      ctx.font = mono;
    }

    const barColor = (i) => {
      const b = bars[i];
      if (b.c > b.o) return C.up;
      if (b.c < b.o) return C.down;
      const pc = i ? bars[i - 1].c : view.prevClose;
      return b.c > pc ? C.up : b.c < pc ? C.down : C.flat;
    };

    // 主图
    if (state.type === 'candle') {
      const bw = Math.max(1, Math.min(14, step * 0.66));
      for (let i = 0; i < n; i++) {
        const b = bars[i];
        const col = barColor(i);
        ctx.strokeStyle = col;
        ctx.fillStyle = col;
        const xx = Math.round(x(i)) + 0.5;
        if (b.h === b.l && e.points_meta && e.points_meta[b.day]) {
          // 单点数据（媒体报道等）：画圆点
          ctx.beginPath(); ctx.arc(x(i), y(b.c), 4, 0, Math.PI * 2); ctx.fill();
          continue;
        }
        ctx.beginPath(); ctx.moveTo(xx, y(b.h)); ctx.lineTo(xx, y(b.l)); ctx.stroke();
        const top = y(Math.max(b.o, b.c)), bot = y(Math.min(b.o, b.c));
        ctx.fillRect(Math.round(x(i) - bw / 2), top, Math.round(bw), Math.max(2, bot - top));
      }
    } else {
      const first = view.prevClose, lastC = bars[n - 1].c;
      const col = lastC > first ? C.up : lastC < first ? C.down : C.flat;
      if (n > 1) {
        const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ph);
        grad.addColorStop(0, hexA(col, 0.25));
        grad.addColorStop(1, hexA(col, 0));
        ctx.beginPath();
        bars.forEach((b, i) => (i ? ctx.lineTo(x(i), y(b.c)) : ctx.moveTo(x(i), y(b.c))));
        ctx.lineTo(x(n - 1), pad.t + ph);
        ctx.lineTo(x(0), pad.t + ph);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.beginPath();
        bars.forEach((b, i) => (i ? ctx.lineTo(x(i), y(b.c)) : ctx.moveTo(x(i), y(b.c))));
        ctx.strokeStyle = col;
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.stroke();
        ctx.lineWidth = 1;
      }
      // 最后一个点
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(x(n - 1), y(lastC), 4, 0, Math.PI * 2); ctx.fill();
    }

    // 数据源切换线
    let lastMarkX = -Infinity;
    for (let i = 1; i < n; i++) {
      if (bars[i].src === bars[i - 1].src) continue;
      // 单点数据之间（报道 ↔ 月度参考价）切换不画线：圆点和悬停说明已经标明来源
      if (isPoint(e, bars[i]) && isPoint(e, bars[i - 1])) continue;
      if (x(i) - lastMarkX < 140) continue; // 避免标签挤在一起
      lastMarkX = x(i);
      const xx = Math.round((x(i - 1) + x(i)) / 2) + 0.5;
      ctx.strokeStyle = C.ma5;
      ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, pad.t + ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = '11px ' + cssVar('--mono');
      ctx.fillStyle = C.ma5;
      ctx.textBaseline = 'top';
      const gapDays = Math.round((bars[i].t - bars[i - 1].t) / DAY);
      const label = `→ ${bars[i].src}${gapDays > 3 ? `（间隔 ${gapDays} 天）` : ''}`;
      const tw = ctx.measureText(label).width;
      const right = xx + 4 + tw <= pad.l + pw;
      ctx.textAlign = right ? 'left' : 'right';
      ctx.fillText(right ? label : `${bars[i - 1].src} ←  ${label}`, right ? xx + 4 : xx - 4, pad.t + 2);
    }

    // 均线
    const drawMA = (arr, color) => {
      ctx.beginPath();
      let started = false;
      arr.forEach((v, i) => {
        if (v == null) return;
        if (!started) { ctx.moveTo(x(i), y(v)); started = true; } else ctx.lineTo(x(i), y(v));
      });
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.25;
      ctx.stroke();
      ctx.lineWidth = 1;
    };
    drawMA(view.ma20, C.ma20);
    drawMA(view.ma5, C.ma5);

    // 最新价虚线 + 标签
    const lastC = bars[n - 1].c;
    const lastCol = barColor(n - 1);
    const ly = Math.round(y(lastC)) + 0.5;
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = lastCol;
    ctx.beginPath(); ctx.moveTo(pad.l, ly); ctx.lineTo(pad.l + pw, ly); ctx.stroke();
    ctx.setLineDash([]);
    axisTag(px(e, lastC), pad.l + pw, ly, lastCol, '#fff');

    // 十字光标
    if (state.hover && state.hover.est != null && est[state.hover.est]) {
      const j = state.hover.est, b = est[j];
      const xx = Math.round(exs[j]) + 0.5;
      ctx.strokeStyle = C.muted;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, pad.t + ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = C.text;
      const [ea, eb] = view.bandEdges ? view.bandEdges(b) : [tx(b.t.getTime() - HALF), tx(b.t.getTime() + HALF)];
      ctx.strokeRect(Math.round(ea) + 0.5, Math.round(y(b.hi)) + 0.5,
        Math.max(2, Math.round(eb - ea) - 1), Math.max(1, Math.round(y(b.lo) - y(b.hi))));
      const label = `${b.day.slice(0, 7)} 估算`;
      ctx.font = mono;
      const tw = ctx.measureText(label).width + 12;
      const lx = Math.max(pad.l, Math.min(pad.l + pw - tw, xx - tw / 2));
      ctx.fillStyle = C.panel;
      ctx.fillRect(lx, pad.t + ph + 2, tw, 18);
      ctx.strokeStyle = C.border;
      ctx.strokeRect(lx + 0.5, pad.t + ph + 2.5, tw - 1, 17);
      ctx.fillStyle = C.text;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, lx + tw / 2, pad.t + ph + 11);
    } else if (state.hover && state.hover.i != null) {
      const i = state.hover.i;
      const xx = Math.round(x(i)) + 0.5;
      const yy = Math.max(pad.t, Math.min(pad.t + ph, state.hover.y));
      ctx.strokeStyle = C.muted;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, pad.t + ph); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(pad.l, Math.round(yy) + 0.5); ctx.lineTo(pad.l + pw, Math.round(yy) + 0.5); ctx.stroke();
      ctx.setLineDash([]);
      const price = hi - ((yy - pad.t) / ph) * (hi - lo);
      axisTag(axisFmt(price), pad.l + pw, yy, C.panel, C.text, C.border);
      const label = fmtDate(bars[i].t) + (view.weekly ? ' 周' : '');
      ctx.font = mono;
      const tw = ctx.measureText(label).width + 12;
      const lx = Math.max(pad.l, Math.min(pad.l + pw - tw, xx - tw / 2));
      ctx.fillStyle = C.panel;
      ctx.fillRect(lx, pad.t + ph + 2, tw, 18);
      ctx.strokeStyle = C.border;
      ctx.strokeRect(lx + 0.5, pad.t + ph + 2.5, tw - 1, 17);
      ctx.fillStyle = C.text;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, lx + tw / 2, pad.t + ph + 11);
    }
  }

  function axisTag(text, x0, yy, bg, fg, border) {
    ctx.font = '11px ' + cssVar('--mono');
    const w = Math.max(60, ctx.measureText(text).width + 10), h = 18;
    ctx.fillStyle = bg;
    ctx.fillRect(x0 + 2, yy - h / 2, w, h);
    if (border) { ctx.strokeStyle = border; ctx.strokeRect(x0 + 2.5, yy - h / 2 + 0.5, w - 1, h - 1); }
    ctx.fillStyle = fg;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x0 + 6, yy);
  }

  function hexA(hex, a) {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }

  function renderInfo(i) {
    const bars = view.bars, e = view.entity;
    if (i == null) i = bars.length - 1;
    const b = bars[i];
    const prevBar = i > 0 ? bars[i - 1] : null;
    const prev = prevBar ? prevBar.c : view.prevClose;
    const sameSrc = (!prevBar || prevBar.src === b.src) && !isPoint(e, b);
    const ch = (i > 0 || view.bars.length < view.totalDays) && sameSrc ? b.c / prev - 1 : null;
    const ma = (v) => (v == null ? '--' : px(e, v));
    const pm = e.points_meta && e.points_meta[b.day];
    if (pm && b.h === b.l) {
      let host = '';
      try { host = new URL(pm.url).hostname.replace(/^www\./, ''); } catch (err) { /* ignore */ }
      $('#ohlcInfo').innerHTML = `
        <span><span class="k">${fmtDate(b.t)}</span></span>
        <span><span class="k">价格</span>${sym(e)}${px(e, b.c)}</span>
        <span><span class="k">口径</span>${esc(pm.kind)}</span>
        <span><a class="q-link" href="${esc(pm.url)}" target="_blank" rel="noopener nofollow">出处：${esc(host)} ↗</a></span>
        <span class="${cls(ch)}">${pctStr(ch)}</span>`;
    } else
    $('#ohlcInfo').innerHTML = `
      <span><span class="k">${fmtDate(b.t)}${view.weekly ? ' 周K' : ''}</span></span>
      <span><span class="k">开</span>${px(e, b.o)}</span>
      <span><span class="k">高</span>${px(e, b.h)}</span>
      <span><span class="k">低</span>${px(e, b.l)}</span>
      <span><span class="k">收</span>${px(e, b.c)}</span>
      <span class="${cls(ch)}">${pctStr(ch)}</span>
      <span><span class="sw" style="background:var(--ma5)"></span><span class="k">MA5</span>${ma(view.ma5[i])}</span>
      <span><span class="sw" style="background:var(--ma20)"></span><span class="k">MA20</span>${ma(view.ma20[i])}</span>`;

    const note = $('#chartNote');
    if (view.totalDays < 5) {
      note.hidden = false;
      note.textContent = `历史从 ${e.bars[0].day} 开始记录，目前只有 ${view.totalDays} 天。每天运行一次采集脚本，K线会逐渐丰富。`;
    } else {
      note.hidden = true;
    }
  }

  // 找横向距离最近的K线或估算月份
  function pick(mx, my) {
    let i = 0;
    for (let k = 1; k < geom.n; k++) if (Math.abs(geom.xs[k] - mx) < Math.abs(geom.xs[i] - mx)) i = k;
    let j = -1;
    (geom.exs || []).forEach((v, k) => { if (j < 0 || Math.abs(v - mx) < Math.abs(geom.exs[j] - mx)) j = k; });
    if (j >= 0 && Math.abs(geom.exs[j] - mx) < Math.abs(geom.xs[i] - mx)) return { est: j, y: my };
    return { i, y: my };
  }
  function showHover(h) {
    state.hover = h;
    if (h.est != null) renderEstInfo(h.est); else renderInfo(h.i);
    drawChart();
  }

  // 估算月份的说明：区间、中值 ± 误差、依据和出处
  function renderEstInfo(j) {
    const e = view.entity, b = view.est[j];
    const step = e.currency === 'CNY' ? 50 : 10;
    const r = (v) => Math.round(v / step) * step;
    const mid = r((b.lo + b.hi) / 2), pm = r((b.hi - b.lo) / 2);
    const kind = b.k === 'a' ? '当月有报道 / 成交数据'
      : b.k === 'i' ? '前后两个已知价格之间插值，离已知点越远区间越宽'
      : b.k === 'x' ? '按其他地区 / 渠道同期价格 × 两地价差比例推算，价格波动越大区间越宽'
      : '没有加元数据：按美国估算 × 当年美元兑加元汇率折算，误差放宽';
    const seen = new Set();
    const links = b.src.map((k) => M.estSources[k]).filter(Boolean).map((s) => {
      const peer = /^按(.+?)同期价格/.exec(s.label);
      if (peer) return seen.has(peer[1]) ? '' : (seen.add(peer[1]), `<span class="k">参照</span>${esc(peer[1])}`);
      if (!s.url || /插值/.test(s.label)) return '';
      let host = '';
      try { host = new URL(s.url).hostname.replace(/^www\./, ''); } catch (err) { /* ignore */ }
      if (seen.has(host)) return '';
      seen.add(host);
      return `<a class="q-link" href="${esc(s.url)}" target="_blank" rel="noopener nofollow" title="${esc(s.label)}">${esc(host)} ↗</a>`;
    }).filter(Boolean).join(' ');
    $('#ohlcInfo').innerHTML = `
      <span><span class="k">${b.day.slice(0, 7)} 估算</span></span>
      <span>${sym(e)}${fmt(mid)} ± ${fmt(pm)}</span>
      <span><span class="k">区间</span>${fmt(b.lo)}–${fmt(b.hi)}</span>
      <span><span class="k">依据</span>${kind}</span>
      <span>${links}</span>`;
  }

  canvas.addEventListener('mousemove', (ev) => {
    if (!geom) return;
    const r = canvas.getBoundingClientRect();
    showHover(pick(ev.clientX - r.left, ev.clientY - r.top));
  });
  canvas.addEventListener('mouseleave', () => {
    state.hover = null;
    renderInfo();
    drawChart();
  });
  // 触摸：手指按住/横向拖动显示十字光标，松手 1.5 秒后恢复
  let touchTimer = null;
  const onTouch = (ev) => {
    if (!geom || !ev.touches.length) return;
    const t = ev.touches[0];
    const r = canvas.getBoundingClientRect();
    showHover(pick(t.clientX - r.left, t.clientY - r.top));
    clearTimeout(touchTimer);
  };
  canvas.addEventListener('touchstart', onTouch, { passive: true });
  canvas.addEventListener('touchmove', onTouch, { passive: true });
  canvas.addEventListener('touchend', () => {
    clearTimeout(touchTimer);
    touchTimer = setTimeout(() => { state.hover = null; renderInfo(); drawChart(); }, 1500);
  });
  new ResizeObserver(() => drawChart()).observe($('#chartWrap'));

  // ---------- 总渲染 ----------
  function renderMain() {
    const e = M.byId[state.selected];
    renderQuote(e);
    buildView(e);
    state.hover = null;
    renderInfo();
    drawChart();
    renderCompare(e);
    renderTable(e);
    document.title = `${e.name} ${sym(e)}${px(e, last(e).c)} · 装机行情`;
  }

  function select(id, opts = {}) {
    if (!M.byId[id]) return;
    state.selected = id;
    const e = M.byId[id];
    if (e.kind === 'item') state.expanded[e.cat.id] = true;
    const region = regionOf(e);
    if (region !== state.region || !$('#regionSeg').children.length) {
      state.region = region;
      renderRegions();
      renderTicker();
    }
    if (opts.syncTab) {
      const want = e.kind === 'index' ? 'index' : 'detail';
      if (state.tab !== want) setTab(want, false);
    }
    if (decodeURIComponent(location.hash.slice(1)) !== id) history.replaceState(null, '', '#' + encodeURIComponent(id));
    renderList();
    renderMain();
    const row = document.querySelector(`#list .row[data-id="${CSS.escape(id)}"]`);
    if (row) row.scrollIntoView({ block: 'nearest' });
  }

  function setTab(tab, render = true) {
    state.tab = tab;
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
    if (render) renderList();
  }

  // ---------- 事件 ----------
  document.querySelectorAll('.tab').forEach((t) =>
    t.addEventListener('click', () => {
      setTab(t.dataset.tab);
      const e = M.byId[state.selected];
      // 切到细节时，若当前选的是大盘，自动选中该大盘的第一个型号（没有细分的现货指标则选第一个有型号的大盘）
      if (state.tab === 'detail' && e.kind === 'index') {
        const cat = e.items.length ? e : catsIn(state.region).find((c) => c.items.length);
        if (cat) select(cat.items[0].id);
      }
      if (state.tab === 'index' && e.kind === 'item') select(e.cat.id);
    })
  );

  $('#list').addEventListener('click', (ev) => {
    const head = ev.target.closest('.cat-head');
    if (head) {
      const id = head.dataset.cat;
      state.expanded[id] = !state.expanded[id];
      renderList();
      return;
    }
    const row = ev.target.closest('.row');
    if (row) { select(row.dataset.id); closeMenu(); }
  });

  // 手机：侧栏是抽屉
  const isMobile = () => window.matchMedia('(max-width: 760px)').matches;
  function closeMenu() {
    if (!document.body.classList.contains('menu-open')) return;
    document.body.classList.remove('menu-open');
    if (isMobile()) $('.main').scrollTop = 0;
  }
  $('#menuBtn').addEventListener('click', () => document.body.classList.toggle('menu-open'));
  $('#scrim').addEventListener('click', closeMenu);
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeMenu(); });

  $('#search').addEventListener('input', (ev) => {
    state.search = ev.target.value;
    renderList();
  });

  $('#table').addEventListener('click', (ev) => {
    const th = ev.target.closest('th');
    if (th && th.dataset.sort) {
      const k = th.dataset.sort;
      state.sort = state.sort.key === k ? { key: k, dir: -state.sort.dir } : { key: k, dir: k === 'name' ? 1 : -1 };
      renderTable(M.byId[state.selected]);
      return;
    }
    const tr = ev.target.closest('tr.r');
    if (tr) select(tr.dataset.id, { syncTab: true });
  });

  $('#quote').addEventListener('click', (ev) => {
    const a = ev.target.closest('[data-goto]');
    if (a) select(a.dataset.goto, { syncTab: true });
  });

  $('#regionSeg').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-region]');
    if (b) switchRegion(b.dataset.region);
  });

  $('#compareTable').addEventListener('click', (ev) => {
    const tr = ev.target.closest('tr.r');
    if (tr) select(tr.dataset.id, { syncTab: true });
  });

  $('#tickerTrack').addEventListener('click', (ev) => {
    const t = ev.target.closest('.tick-item');
    if (t) select(t.dataset.id, { syncTab: true });
  });

  $('#rangeSeg').addEventListener('click', (ev) => {
    const b = ev.target.closest('button');
    if (!b) return;
    state.range = +b.dataset.range;
    $('#rangeSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    renderMain();
  });
  $('#typeSeg').addEventListener('click', (ev) => {
    const b = ev.target.closest('button');
    if (!b) return;
    state.type = b.dataset.type;
    $('#typeSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    drawChart();
  });

  // 键盘 ↑↓ 切换
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowUp' && ev.key !== 'ArrowDown') return;
    const ids = visibleRowIds();
    if (!ids.length) return;
    ev.preventDefault();
    let i = ids.indexOf(state.selected);
    i = i < 0 ? 0 : Math.max(0, Math.min(ids.length - 1, i + (ev.key === 'ArrowDown' ? 1 : -1)));
    select(ids[i]);
  });

  // 涨跌配色
  function applyColor(greenUp) {
    document.body.classList.toggle('green-up', greenUp);
    $('#colorToggle').textContent = greenUp ? '绿涨红跌' : '红涨绿跌';
    localStorage.setItem('pcm-green-up', greenUp ? '1' : '0');
  }
  $('#colorToggle').addEventListener('click', () => {
    applyColor(!document.body.classList.contains('green-up'));
    drawChart();
  });

  window.addEventListener('hashchange', () => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (id && id !== state.selected) select(id, { syncTab: true });
  });

  // ---------- 数据源状态提示 ----------
  const SOURCE_NAMES = {
    'smzdm-xianka': '国内显卡（什么值得买 · 京东好价）',
    'smzdm-neicun': '国内内存（什么值得买 · 京东好价）',
    'smzdm-gutaiyingpan': '国内固态（什么值得买 · 京东好价）',
    'smzdm-cpu': '国内 CPU（什么值得买 · 京东好价）',
    gpuprix: '美国 / 加拿大显卡（GPUPrix）',
    'bestbuy-ca': '加拿大内存 / 固态 / CPU（Best Buy）',
    dramexchange: '全球现货（DRAMeXchange）',
    fx: '汇率',
    'pcpartpicker-archive': '美国 / 加拿大历史追更（PCPartPicker 存档）',
    frocloud: '国内社区月度参考价（frocloud）',
    pricify: '美国每周零售价（Pricify）',
    techfuel: '美国显卡补档报价（TechFuelHQ）',
    cfm: '国内渠道周报价（CFM 闪存市场）',
    netlify: '网站自动发布',
    backup: 'Google Drive 备份',
  };
  function renderHealth() {
    const box = $('#health');
    const msgs = [];
    const upd = M.updated ? new Date(M.updated.replace(' ', 'T')) : null;
    if (upd) {
      const days = Math.floor((Date.now() - upd.getTime()) / DAY);
      if (days >= 2) msgs.push(`数据已经 ${days} 天没有更新（每天由 GitHub Actions 自动采集，可能是云端任务出错了）`);
    }
    for (const [k, h] of Object.entries(M.health || {})) {
      if (!h.fails) continue;
      const name = SOURCE_NAMES[k] || k;
      const why = h.last_error_msg ? `：${h.last_error_msg}` : '';
      msgs.push(`${name} ${h.fails >= 2 ? `已连续失败 ${h.fails} 次` : '最近一次采集失败'}${why}${h.last_ok ? `（上次成功 ${h.last_ok}）` : ''}`);
    }
    box.hidden = !msgs.length;
    box.innerHTML = msgs.length
      ? `<b>数据源提示</b><ul>${msgs.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>`
      : '';
  }

  // ---------- 启动 ----------
  renderHealth();
  $('#clock').textContent = `更新于 ${M.updated}`;
  applyColor(localStorage.getItem('pcm-green-up') === '1');
  const initial = decodeURIComponent(location.hash.slice(1));
  select(M.byId[initial] ? initial : state.selected, { syncTab: true });
})();
