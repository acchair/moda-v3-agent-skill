const $ = (id) => document.getElementById(id);
const state = {
  pool: [], filtered: [], quotes: new Map(), selected: null, chain: null, report: null,
  discovery: null, pressure: null, view: 'graph', stateFilter: '', industry: '', query: '',
  graphKind: 'all', graphScale: 1, graphPositions: new Map(), jobId: '', logSeq: 0,
};

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `请求失败 (${response.status})`);
  return payload;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function numberText(value, digits = 2) {
  return value == null || Number.isNaN(Number(value)) ? '-' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: digits});
}

function pctText(value, signed = true) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  const number = Number(value);
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function statusText(value) {
  return ({live:'实时', cache:'缓存', partial:'部分可用', unavailable:'不可用', ready:'已分析', missing:'待分析', core:'核心跟踪', watch:'观察研究', ignore:'暂不关注', success:'完成', success_with_warnings:'部分完成', failed:'失败', running:'运行中', queued:'排队中'})[value] || value || '-';
}

function quoteFor(code) { return state.quotes.get(code) || {status: 'unavailable'}; }

async function loadStatus() {
  try {
    const data = await api('/api/status');
    const ok = Boolean(data.easy_tdx?.ok);
    $('connectionDot').className = `live-dot ${ok ? 'ok' : 'warn'}`;
    $('statusLine').textContent = ok ? 'easy_tdx 已连接' : '行情源部分可用';
  } catch (error) {
    $('connectionDot').className = 'live-dot warn';
    $('statusLine').textContent = `状态检查失败: ${error.message}`;
  }
}

async function loadQuotes(codes, refresh = false) {
  const unique = [...new Set(codes.filter(Boolean))].slice(0, 50);
  if (!unique.length) return;
  try {
    const data = await api(`/api/quotes?codes=${encodeURIComponent(unique.join(','))}&refresh=${refresh}`);
    data.quotes.forEach((quote) => state.quotes.set(quote.code, quote));
  } catch (error) {
    unique.forEach((code) => state.quotes.set(code, {code, status:'unavailable', error:error.message}));
  }
}

async function loadPool(refreshQuotes = false) {
  const data = await api('/api/pool?limit=200');
  state.pool = data.items || [];
  await loadQuotes(state.pool.map((item) => item.code), refreshQuotes);
  renderIndustryFilter(data.industries || []);
  applyFilters();
}

function renderIndustryFilter(industries) {
  const current = $('industryFilter').value;
  $('industryFilter').innerHTML = '<option value="">全部行业</option>' + industries.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
  $('industryFilter').value = industries.includes(current) ? current : '';
  $('industryCount').textContent = industries.length;
}

function applyFilters() {
  const query = state.query.trim().toLowerCase();
  state.filtered = state.pool.filter((item) => {
    const haystack = `${item.code} ${item.name} ${item.full_name || ''} ${item.industry || ''}`.toLowerCase();
    return (!query || haystack.includes(query)) && (!state.stateFilter || item.state === state.stateFilter) && (!state.industry || item.industry === state.industry);
  });
  renderSummary();
  renderMatrix();
  renderList();
  $('visibleCount').textContent = state.filtered.length;
  $('totalCount').textContent = state.pool.length;
}

function renderSummary() {
  const gaps = state.pool.filter((item) => item.summary?.score == null || !['live','cache'].includes(quoteFor(item.code).status)).length;
  $('poolMetric').textContent = state.pool.length;
  $('analyzedMetric').textContent = state.pool.filter((item) => item.analysis_status === 'ready').length;
  $('coreMetric').textContent = state.pool.filter((item) => item.state === 'core').length;
  $('gapMetric').textContent = gaps;
}

function renderMatrix() {
  const rows = state.filtered.map((item) => {
    const quote = quoteFor(item.code);
    const completeness = [item.summary?.score != null, ['live','cache'].includes(quote.status), Boolean(item.industry)].filter(Boolean).length;
    const changeClass = Number(quote.change_pct) > 0 ? 'up' : Number(quote.change_pct) < 0 ? 'down' : '';
    return `<tr class="clickable-row" data-code="${item.code}">
      <td><strong>${item.code}</strong><span>${escapeHtml(item.name)}</span></td>
      <td>${escapeHtml(item.industry || '-')}</td><td>${numberText(item.summary?.score, 0)}</td>
      <td><span class="status-badge rating-${escapeHtml(item.summary?.rating || 'none')}">${escapeHtml(item.summary?.rating || '待分析')}</span></td>
      <td>${numberText(quote.price)}</td><td class="${changeClass}">${pctText(quote.change_pct)}</td>
      <td>${statusText(item.state)}</td><td>${completeness}/3</td></tr>`;
  }).join('');
  $('matrixBody').innerHTML = rows || '<tr><td colspan="8" class="empty-cell">研究池为空，可从搜索或主动发现加入。</td></tr>';
}

function renderList() {
  $('stockList').innerHTML = state.filtered.map((item) => {
    const quote = quoteFor(item.code);
    const changeClass = Number(quote.change_pct) > 0 ? 'up' : Number(quote.change_pct) < 0 ? 'down' : '';
    return `<button class="stock-row" type="button" data-code="${item.code}">
      <span class="stock-identity"><strong>${item.code}</strong><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.industry || '行业待确认')}</small></span>
      <span><small>评分</small><strong>${numberText(item.summary?.score, 0)}</strong></span>
      <span><small>评级</small><strong>${escapeHtml(item.summary?.rating || '-')}</strong></span>
      <span><small>最新价</small><strong>${numberText(quote.price)}</strong></span>
      <span class="${changeClass}"><small>涨跌</small><strong>${pctText(quote.change_pct)}</strong></span>
      <span class="row-action">查看 ›</span>
    </button>`;
  }).join('') || '<div class="empty-state">没有符合筛选条件的股票。</div>';
}

async function doSearch() {
  const query = $('searchInput').value.trim();
  state.query = query;
  applyFilters();
  if (!query) { $('searchResults').hidden = true; return; }
  $('searchResults').hidden = false;
  $('searchResults').innerHTML = '<div class="search-loading">搜索中...</div>';
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    const stocks = data.stocks || [];
    $('searchResults').innerHTML = stocks.slice(0, 12).map((item) => `<button type="button" data-search-code="${escapeHtml(item.code)}" data-search-name="${escapeHtml(item.name)}"><strong>${escapeHtml(item.code)}</strong><span>${escapeHtml(item.name)}</span></button>`).join('') || '<div class="search-loading">没有找到股票。</div>';
  } catch (error) {
    $('searchResults').innerHTML = `<div class="search-loading">${escapeHtml(error.message)}</div>`;
  }
}

function showView(view) {
  state.view = view;
  document.querySelectorAll('.view-tabs button').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  document.querySelectorAll('.content-view').forEach((panel) => panel.classList.toggle('active', panel.dataset.panel === view));
  const hash = view === 'pressure' ? 'market-pressure' : view;
  history.replaceState(null, '', `#${hash}`);
  if (view === 'discovery' && !state.discovery) loadDiscovery();
  if (view === 'pressure' && !state.pressure) loadPressure();
}

async function openStock(code, name = '') {
  state.selected = {code, name};
  $('detailDrawer').setAttribute('aria-hidden', 'false');
  $('drawerBackdrop').hidden = false;
  $('drawerCode').textContent = code;
  $('drawerTitle').textContent = name || code;
  $('drawerContent').innerHTML = '<div class="empty-state">正在读取股票资料...</div>';
  try {
    const [chain, report, quoteData] = await Promise.all([
      api(`/api/chain/stock/${code}`), api(`/api/reports/${code}`), api(`/api/quotes?codes=${code}`),
    ]);
    state.chain = chain;
    state.report = report;
    const quote = quoteData.quotes?.[0] || {code, status:'unavailable'};
    state.quotes.set(code, quote);
    state.selected.name = chain.stock?.name || name || code;
    $('drawerTitle').textContent = state.selected.name;
    renderDrawer();
    state.graphPositions.clear();
    renderGraph();
  } catch (error) {
    $('drawerContent').innerHTML = `<div class="error-state">读取失败: ${escapeHtml(error.message)}</div>`;
  }
}

function renderDrawer() {
  const code = state.selected.code;
  const poolItem = state.pool.find((item) => item.code === code);
  const quote = quoteFor(code);
  const summary = state.report?.summary || {};
  const factors = summary.factors || [];
  const industries = state.chain?.industries || [];
  const products = state.chain?.products || [];
  const reportBlocks = (state.report?.reports || []).filter((item) => item.exists).map((item) => `<details class="report-block"><summary>${escapeHtml(item.title)}</summary><pre>${escapeHtml(item.content)}</pre></details>`).join('');
  $('drawerContent').innerHTML = `
    <section class="drawer-quote"><div><span>最新价</span><strong>${numberText(quote.price)}</strong></div><div><span>涨跌幅</span><strong class="${Number(quote.change_pct) > 0 ? 'up' : Number(quote.change_pct) < 0 ? 'down' : ''}">${pctText(quote.change_pct)}</strong></div><div><span>评分</span><strong>${numberText(summary.score, 0)}</strong></div><div><span>评级</span><strong>${escapeHtml(summary.rating || '-')}</strong></div></section>
    <section class="drawer-section"><div class="drawer-section-head"><h3>研究状态</h3><span>${statusText(poolItem?.state || '未入池')}</span></div><textarea id="poolNote" maxlength="500" placeholder="研究备注">${escapeHtml(poolItem?.note || '')}</textarea><div class="action-row"><button type="button" data-pool-state="watch">观察</button><button type="button" data-pool-state="core">核心</button><button type="button" class="secondary" data-pool-state="ignore">忽略</button><button type="button" class="primary" id="runAnalysis">运行分析</button></div></section>
    <section class="drawer-section"><h3>五层评分</h3>${factors.length ? `<table class="factor-table"><tbody>${factors.map((factor) => `<tr><th>${escapeHtml(factor.factor)}</th><td>${numberText(factor.score,0)}/${numberText(factor.maximum,0)}</td><td>${escapeHtml(factor.evidence)}</td><td>${escapeHtml(factor.source || '-')}</td><td>${escapeHtml(factor.status)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">尚无评分报告。</p>'}</section>
    <section class="drawer-section"><h3>产业位置</h3><div class="chip-list">${industries.map((item) => `<span>${escapeHtml(item.name)}</span>`).join('') || '<span>行业待确认</span>'}</div><div class="chip-list products">${products.slice(0,12).map((item) => `<span>${escapeHtml(item.name)}</span>`).join('')}</div></section>
    <section class="drawer-section"><h3>原始报告</h3>${reportBlocks || '<p class="muted">运行分析后显示模块报告。</p>'}</section>`;
}

function closeDrawer() {
  $('detailDrawer').setAttribute('aria-hidden', 'true');
  $('drawerBackdrop').hidden = true;
}

async function updatePool(stateValue) {
  const code = state.selected?.code;
  if (!code) return;
  const note = $('poolNote')?.value || '';
  try {
    await api(`/api/pool/${code}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({state:stateValue, note})});
    await loadPool();
    if (stateValue === 'ignore') closeDrawer(); else renderDrawer();
  } catch (error) { alert(error.message); }
}

async function startAnalysis() {
  if (!state.selected) return;
  try {
    const data = await api('/api/analyze/stock', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code:state.selected.code, name:state.selected.name})});
    state.jobId = data.job_id;
    state.logSeq = 0;
    $('terminalOutput').textContent = '';
    $('taskDock').open = true;
    pollJob();
  } catch (error) { alert(error.message); }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const [job, logs] = await Promise.all([api(`/api/jobs/${state.jobId}`), api(`/api/jobs/${state.jobId}/logs?after=${state.logSeq}`)]);
    state.logSeq = logs.latest_seq;
    logs.logs.forEach((entry) => { $('terminalOutput').textContent += `[${entry.time}] ${entry.line}\n`; });
    $('terminalOutput').scrollTop = $('terminalOutput').scrollHeight;
    $('jobBadge').textContent = statusText(job.status);
    $('jobBadge').className = `status-badge status-${job.status}`;
    $('jobModules').innerHTML = Object.values(job.modules || {}).map((module) => `<span class="status-${module.status}">${escapeHtml(module.title)} · ${statusText(module.status)}</span>`).join('');
    if (['queued','running'].includes(job.status)) {
      setTimeout(pollJob, 1200);
    } else {
      const selectedCode = state.selected?.code;
      await loadPool(true);
      if (selectedCode) await openStock(selectedCode, state.selected?.name || '');
    }
  } catch (error) {
    $('jobBadge').textContent = '读取失败';
    $('terminalOutput').textContent += `\n${error.message}`;
  }
}

function graphNodeVisible(node) {
  if (state.graphKind === 'all' || node.kind === 'company') return true;
  if (state.graphKind === 'industry') return ['industry','upstream','downstream'].includes(node.kind);
  return ['product','upstream','downstream'].includes(node.kind);
}

function graphPosition(node, index, grouped) {
  if (state.graphPositions.has(node.id)) return state.graphPositions.get(node.id);
  const x = {upstream:130, center:480, downstream:830}[node.column] || 480;
  const group = grouped[node.column] || [];
  const y = 60 + (index + 1) * (430 / (group.length + 1));
  const position = {x, y};
  state.graphPositions.set(node.id, position);
  return position;
}

function renderGraph() {
  const graph = state.chain?.graph;
  if (!graph?.nodes?.length) {
    $('graphCanvas').className = 'graph-canvas empty-state';
    $('graphCanvas').textContent = state.selected ? '该股票暂无产业图谱。' : '从研究池、搜索结果或候选中选择股票。';
    return;
  }
  const nodes = graph.nodes.filter(graphNodeVisible);
  const ids = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  const grouped = {upstream:[], center:[], downstream:[]};
  nodes.forEach((node) => (grouped[node.column] || grouped.center).push(node));
  const positions = new Map();
  Object.values(grouped).forEach((group) => group.forEach((node, index) => positions.set(node.id, graphPosition(node, index, grouped))));
  const edgeSvg = edges.map((edge) => {
    const a = positions.get(edge.source), b = positions.get(edge.target), mid = (a.x + b.x) / 2;
    return `<path class="graph-edge" data-source="${escapeHtml(edge.source)}" data-target="${escapeHtml(edge.target)}" d="M ${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}"/>`;
  }).join('');
  const nodeSvg = nodes.map((node) => {
    const p = positions.get(node.id);
    return `<g class="graph-node kind-${escapeHtml(node.kind)}" data-id="${escapeHtml(node.id)}" transform="translate(${p.x - 78},${p.y - 22})"><rect width="156" height="44" rx="5"/><text x="78" y="18" text-anchor="middle">${escapeHtml(String(node.label).slice(0,12))}</text><text class="node-kind" x="78" y="34" text-anchor="middle">${escapeHtml(node.kind)}</text></g>`;
  }).join('');
  const transform = `translate(480 260) scale(${state.graphScale}) translate(-480 -260)`;
  $('graphCanvas').className = 'graph-canvas';
  $('graphCanvas').innerHTML = `<svg viewBox="0 0 960 520" role="img" aria-label="${escapeHtml(state.selected.name)}产业关系图"><g id="graphScene" transform="${transform}">${edgeSvg}${nodeSvg}</g></svg>`;
  $('zoomLabel').textContent = `${Math.round(state.graphScale * 100)}%`;
  const industries = state.chain.industries || [], products = state.chain.products || [];
  $('graphInsight').innerHTML = `<h3>${escapeHtml(state.selected.name)}</h3><p>${escapeHtml(industries.map((item) => item.name).join(' · ') || '行业待确认')}</p><dl><div><dt>行业关系</dt><dd>${industries.length}</dd></div><div><dt>产品关系</dt><dd>${products.length}</dd></div><div><dt>图谱节点</dt><dd>${nodes.length}</dd></div></dl><button type="button" class="secondary" data-open-selected="true">查看研究详情</button>`;
  enableGraphDrag();
}

function syncGraphEdges(svg) {
  svg.querySelectorAll('.graph-edge').forEach((edge) => {
    const a = state.graphPositions.get(edge.dataset.source), b = state.graphPositions.get(edge.dataset.target);
    if (!a || !b) return;
    const mid = (a.x + b.x) / 2;
    edge.setAttribute('d', `M ${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}`);
  });
}

function enableGraphDrag() {
  const svg = $('graphCanvas').querySelector('svg');
  if (!svg) return;
  svg.querySelectorAll('.graph-node').forEach((node) => {
    node.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      node.setPointerCapture(event.pointerId);
      const id = node.dataset.id;
      const start = state.graphPositions.get(id);
      const box = svg.getBoundingClientRect();
      const startX = event.clientX, startY = event.clientY;
      const move = (moveEvent) => {
        const dx = (moveEvent.clientX - startX) * 960 / box.width / state.graphScale;
        const dy = (moveEvent.clientY - startY) * 520 / box.height / state.graphScale;
        const next = {x:start.x + dx, y:start.y + dy};
        state.graphPositions.set(id, next);
        node.setAttribute('transform', `translate(${next.x - 78},${next.y - 22})`);
        syncGraphEdges(svg);
      };
      const end = () => { node.removeEventListener('pointermove', move); node.removeEventListener('pointerup', end); };
      node.addEventListener('pointermove', move);
      node.addEventListener('pointerup', end);
    });
  });
}

function setZoom(value) {
  state.graphScale = Math.max(.7, Math.min(1.6, value));
  renderGraph();
}

async function loadDiscovery(refresh = false) {
  $('discoveryList').innerHTML = '<div class="empty-state">正在生成行业升温候选...</div>';
  try {
    state.discovery = await api(`/api/discovery?limit=30&refresh=${refresh}`);
    renderDiscovery();
    renderDecision();
  } catch (error) {
    $('discoveryList').innerHTML = `<div class="error-state">候选加载失败: ${escapeHtml(error.message)}</div>`;
    $('decisionTitle').textContent = '候选数据暂不可用';
    $('decisionText').textContent = error.message;
  }
}

function renderDecision() {
  const candidates = state.discovery?.candidates || [];
  const newCandidates = candidates.filter((item) => !item.pool_state && item.analysis_status !== 'ready');
  $('decisionTitle').textContent = newCandidates.length ? `优先研究 ${Math.min(newCandidates.length, 5)} 只` : '今天暂无建议新增';
  $('decisionText').textContent = newCandidates.length ? newCandidates.slice(0,5).map((item) => `${item.name}(${item.code})`).join(' · ') : '候选已在研究池中，或连续行业数据不足。';
}

function renderDiscovery() {
  const data = state.discovery;
  $('discoveryNote').textContent = `${data.methodology || ''} 数据日期 ${data.as_of || '-'}。`;
  $('discoveryList').innerHTML = (data.candidates || []).map((item) => {
    const quote = item.quote || {};
    return `<article class="candidate-row"><div class="candidate-rank">#${item.industry_rank}</div><div class="candidate-main"><div><strong>${escapeHtml(item.name)}</strong><span>${item.code}</span><span class="industry-chip">${escapeHtml(item.industry)}</span></div><p>行业占比变化 ${pctText(item.warming_change, true)} · ${item.analysis_status === 'ready' ? `已有评分 ${numberText(item.summary?.score,0)}` : '待运行 moda 分析'}</p><small>来源：${escapeHtml(item.evidence.map((e) => `${e.label}/${e.source || '未知'}`).join('；'))}</small></div><div class="candidate-market"><span>${numberText(quote.price)}</span><strong class="${Number(quote.change_pct) > 0 ? 'up' : Number(quote.change_pct) < 0 ? 'down' : ''}">${pctText(quote.change_pct)}</strong></div><div class="candidate-actions"><button type="button" class="secondary" data-code="${item.code}" data-name="${escapeHtml(item.name)}">查看</button><button type="button" data-add-code="${item.code}">加入观察</button></div></article>`;
  }).join('') || '<div class="empty-state">需要连续 10 个交易日的行业历史数据。</div>';
}

async function loadPressure(refresh = false) {
  $('pressureHero').innerHTML = '<div><span>综合压力</span><strong>-</strong></div><p>正在计算...</p>';
  try {
    state.pressure = await api(`/api/market-pressure?days=60&refresh=${refresh}`);
    renderPressure();
  } catch (error) {
    $('pressureHero').innerHTML = `<div><span>综合压力</span><strong>-</strong></div><p>${escapeHtml(error.message)}</p>`;
  }
}

function pressureLevel(score) {
  if (score == null) return '数据不足';
  if (score >= 70) return '压力偏高';
  if (score >= 45) return '压力中性';
  return '压力偏低';
}

function renderPressure() {
  const data = state.pressure;
  $('pressureHero').innerHTML = `<div><span>综合压力</span><strong>${numberText(data.score,0)}</strong></div><p>${pressureLevel(data.score)} · 有效权重 ${data.available_weight}% · ${statusText(data.status)}</p>`;
  $('pressureFactors').innerHTML = data.factors.map((factor) => `<article class="factor-card"><div><span>${escapeHtml(factor.name)}</span><strong>${numberText(factor.score,0)}</strong></div><div class="meter"><i style="width:${factor.score ?? 0}%"></i></div><p>${escapeHtml(factor.logic)}</p><small>原值 ${numberText(factor.raw_value)} · 权重 ${factor.weight}% · ${escapeHtml(factor.as_of || '-')}</small><small>来源：${escapeHtml(factor.source || '缺失')}</small></article>`).join('');
  renderTrend(data.trend || []);
}

function renderTrend(rows) {
  const valid = rows.filter((row) => row.margin_ratio != null && row.sh_index != null);
  if (valid.length < 2) { $('pressureTrend').innerHTML = '<div class="empty-state">历史序列不足，暂不绘制趋势。</div>'; return; }
  const width = 900, height = 240, pad = 34;
  const line = (key) => {
    const values = valid.map((row) => Number(row[key]));
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    return values.map((value, index) => `${index ? 'L' : 'M'} ${pad + index * (width - pad * 2) / (values.length - 1)} ${height - pad - (value - min) / span * (height - pad * 2)}`).join(' ');
  };
  $('pressureTrend').innerHTML = `<div class="trend-head"><h3>融资占比与上证指数</h3><span>${escapeHtml(valid[0].date)} 至 ${escapeHtml(valid.at(-1).date)}</span></div><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="融资占比与上证指数趋势"><line x1="${pad}" y1="${height-pad}" x2="${width-pad}" y2="${height-pad}" class="chart-axis"/><path d="${line('margin_ratio')}" class="trend-line margin-line"/><path d="${line('sh_index')}" class="trend-line index-line"/><text x="${pad}" y="18" class="margin-label">融资占比</text><text x="${pad+90}" y="18" class="index-label">上证指数</text></svg>`;
}

function bindEvents() {
  $('searchBtn').addEventListener('click', doSearch);
  $('searchInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') doSearch(); });
  $('searchInput').addEventListener('input', () => { if (!$('searchInput').value) { state.query=''; $('searchResults').hidden=true; applyFilters(); } });
  $('searchResults').addEventListener('click', (event) => { const button = event.target.closest('[data-search-code]'); if (button) { $('searchResults').hidden=true; openStock(button.dataset.searchCode, button.dataset.searchName); } });
  $('stateFilter').addEventListener('click', (event) => { const button=event.target.closest('[data-state]'); if (!button) return; state.stateFilter=button.dataset.state; document.querySelectorAll('#stateFilter button').forEach((item)=>item.classList.toggle('active',item===button)); applyFilters(); });
  $('industryFilter').addEventListener('change', () => {
    state.industry=$('industryFilter').value;
    applyFilters();
    if (state.industry) { $('searchInput').value=state.industry; doSearch(); }
  });
  $('resetFilters').addEventListener('click', () => { state.query=''; state.stateFilter=''; state.industry=''; $('searchInput').value=''; $('industryFilter').value=''; document.querySelectorAll('#stateFilter button').forEach((item)=>item.classList.toggle('active',item.dataset.state==='')); applyFilters(); });
  document.querySelector('.view-tabs').addEventListener('click', (event) => { const button=event.target.closest('[data-view]'); if (button) showView(button.dataset.view); });
  $('matrixBody').addEventListener('click', (event) => { const row=event.target.closest('[data-code]'); if (row) openStock(row.dataset.code); });
  $('stockList').addEventListener('click', (event) => { const row=event.target.closest('[data-code]'); if (row) openStock(row.dataset.code); });
  $('closeDrawer').addEventListener('click', closeDrawer);
  $('drawerBackdrop').addEventListener('click', closeDrawer);
  $('drawerContent').addEventListener('click', (event) => { const pool=event.target.closest('[data-pool-state]'); if (pool) updatePool(pool.dataset.poolState); if (event.target.closest('#runAnalysis')) startAnalysis(); });
  $('graphFilter').addEventListener('click', (event) => { const button=event.target.closest('[data-kind]'); if (!button) return; state.graphKind=button.dataset.kind; document.querySelectorAll('#graphFilter button').forEach((item)=>item.classList.toggle('active',item===button)); state.graphPositions.clear(); renderGraph(); });
  $('zoomIn').addEventListener('click', () => setZoom(state.graphScale + .1));
  $('zoomOut').addEventListener('click', () => setZoom(state.graphScale - .1));
  $('zoomReset').addEventListener('click', () => { state.graphScale=1; state.graphPositions.clear(); renderGraph(); });
  $('graphInsight').addEventListener('click', (event) => { if (event.target.closest('[data-open-selected]') && state.selected) openStock(state.selected.code,state.selected.name); });
  $('openDiscovery').addEventListener('click', () => showView('discovery'));
  $('refreshDiscovery').addEventListener('click', () => loadDiscovery(true));
  $('discoveryList').addEventListener('click', async (event) => { const add=event.target.closest('[data-add-code]'); const open=event.target.closest('[data-code]'); if (add) { await api(`/api/pool/${add.dataset.addCode}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:'watch',note:'来自行业升温候选'})}); await loadPool(); renderDiscovery(); } else if (open) openStock(open.dataset.code,open.dataset.name); });
  $('refreshPressure').addEventListener('click', () => loadPressure(true));
  $('refreshAll').addEventListener('click', async () => { await Promise.all([loadStatus(),loadPool(true)]); if (state.discovery) loadDiscovery(true); if (state.pressure) loadPressure(true); });
}

async function init() {
  bindEvents();
  const hash = location.hash.replace('#','');
  showView(hash === 'market-pressure' ? 'pressure' : ['graph','matrix','list','discovery','pressure'].includes(hash) ? hash : 'graph');
  try {
    await Promise.all([loadStatus(), loadPool()]);
    loadDiscovery();
  } catch (error) {
    $('decisionTitle').textContent = '工作台加载失败';
    $('decisionText').textContent = error.message;
  }
}

init();
