let dashboardData = null;
let drilldownSort = { key: 'amount', dir: 'desc' };
let viewState = { view: 'dashboard', team: null, rep: null };
let repDealsTab = 'closed_won_deals';
let drilldownState = { type: null, scope: {} };
let selectedMonth = localStorage.getItem('mbr.selectedMonth') || '';
let monthsList = [];

// Column definitions per drilldown type
const DRILLDOWN_COLUMNS = {
  closed_won: [
    { key: 'name', label: 'Deal Name' },
    { key: 'amount', label: 'Amount (USD)', num: true, fmt: 'usd' },
    { key: 'owner', label: 'Deal Owner' },
    { key: 'country', label: 'Country' },
    { key: 'team', label: 'Team', fmt: 'team' },
    { key: 'create_date', label: 'Create Date' },
    { key: 'close_date', label: 'Close Date' },
    { key: 'age_days', label: 'Age (Days)', num: true },
  ],
  deals_lost: [
    { key: 'name', label: 'Deal Name' },
    { key: 'amount', label: 'Amount (USD)', num: true, fmt: 'usd' },
    { key: 'owner', label: 'Deal Owner' },
    { key: 'country', label: 'Country' },
    { key: 'team', label: 'Team', fmt: 'team' },
    { key: 'stage', label: 'Stage' },
    { key: 'close_date', label: 'Close Date' },
    { key: 'lost_reason', label: 'Lost Reason' },
  ],
  open_pipeline: [
    { key: 'name', label: 'Deal Name' },
    { key: 'amount', label: 'Amount (USD)', num: true, fmt: 'usd' },
    { key: 'owner', label: 'Deal Owner' },
    { key: 'stage', label: 'Stage' },
    { key: 'team', label: 'Team', fmt: 'team' },
    { key: 'create_date', label: 'Create Date' },
    { key: 'close_date', label: 'Close Date (Expected)' },
  ],
};

const DRILLDOWN_TITLES = {
  closed_won: 'Closed Won Deals — This Month',
  deals_lost: 'Closed Lost Deals — This Month',
  open_pipeline: 'Open Pipeline Deals',
};

const DRILLDOWN_SOURCES = {
  closed_won: 'closed_won_deals',
  deals_lost: 'deals_lost_deals',
  open_pipeline: 'open_pipeline_deals',
};

const fmtUSD = (n) => '$' + (Number(n) || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
const fmtNum = (n) => (Number(n) || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
const fmtPct = (n) => (Number(n) || 0).toFixed(1) + '%';
const fmtDays = (n) => (Number(n) || 0).toFixed(1) + ' days';

function attainmentClass(pct) {
  if (pct >= 80) return 'good';
  if (pct >= 50) return 'mid';
  return 'low';
}
function progressClass(pct) {
  if (pct >= 80) return '';
  if (pct >= 50) return 'mid';
  return 'low';
}
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

async function loadMonthsList() {
  try {
    const res = await fetch('/api/months');
    const json = await res.json();
    if (!json.success) return;
    monthsList = json.data || [];
    renderMonthSelector();
  } catch (e) {
    // ignore — selector just stays empty
  }
}

function renderMonthSelector() {
  const sel = document.getElementById('month-select');
  if (!sel) return;
  const current = (monthsList[0] && monthsList[0].key) || '';
  sel.innerHTML = monthsList.map(m =>
    `<option value="${m.key}">${m.label}${m.key === current ? '  (current)' : ''}</option>`
  ).join('');
  // Default to stored selection if it's still in the list, else current month
  const valid = monthsList.some(m => m.key === selectedMonth);
  sel.value = valid ? selectedMonth : current;
  selectedMonth = sel.value;
}

let _errorAutoHideTimer = null;

function showTransientError(message) {
  const errorEl = document.getElementById('error');
  errorEl.textContent = message;
  errorEl.style.display = 'block';
  if (_errorAutoHideTimer) clearTimeout(_errorAutoHideTimer);
  _errorAutoHideTimer = setTimeout(() => { errorEl.style.display = 'none'; }, 8000);
}

async function loadData(refresh = false) {
  const loading = document.getElementById('loading');
  const errorEl = document.getElementById('error');
  const btn = document.getElementById('refresh-btn');
  const hasExistingData = !!dashboardData;

  // Only blank the screen on the FIRST load. On a refresh keep current data
  // visible so a transient error doesn't wipe the dashboard.
  if (!hasExistingData) {
    document.querySelectorAll('.view').forEach(v => v.style.display = 'none');
    document.getElementById('breadcrumb').style.display = 'none';
    loading.style.display = 'block';
  }
  errorEl.style.display = 'none';
  if (_errorAutoHideTimer) clearTimeout(_errorAutoHideTimer);

  btn.disabled = true;
  btn.classList.add('loading');

  try {
    const params = new URLSearchParams();
    if (refresh) params.set('refresh', 'true');
    if (selectedMonth) params.set('month', selectedMonth);
    const url = '/api/dashboard' + (params.toString() ? '?' + params.toString() : '');
    const res = await fetch(url);
    const ctype = (res.headers.get('content-type') || '').toLowerCase();
    let json;
    if (!ctype.includes('application/json')) {
      // Proxy returned an HTML error page (timeout / 502 / etc.) — don't
      // try to parse it as JSON. Surface a friendly retry message.
      const status = res.status || 0;
      throw new Error(
        status === 504 || status === 502
          ? `Server timed out (HTTP ${status}). HubSpot took too long — click Sync to retry.`
          : `Server returned a non-JSON response${status ? ' (HTTP ' + status + ')' : ''}. Try Sync again.`);
    }
    json = await res.json();
    if (!json.success) throw new Error(json.error || 'Unknown error');
    dashboardData = json.data;
    setView(viewState.view, viewState.team, viewState.rep);
    if (document.getElementById('drilldown-modal').style.display !== 'none'
        && drilldownState.type) {
      renderDrilldown();
    }
  } catch (e) {
    if (hasExistingData) {
      // Keep showing the previous data; just flash a non-blocking message
      showTransientError('Sync failed: ' + e.message + '  (showing last successful data)');
    } else {
      errorEl.textContent = 'Failed to load: ' + e.message;
      errorEl.style.display = 'block';
    }
  } finally {
    loading.style.display = 'none';
    btn.disabled = false;
    btn.classList.remove('loading');
  }
}

// ----- View routing ---------------------------------------------------------

function setView(view, team = null, rep = null) {
  viewState = { view, team, rep };
  document.querySelectorAll('.view').forEach(v => v.style.display = 'none');

  const d = dashboardData;
  if (!d) return;

  document.getElementById('month-label').textContent =
    `${d.month}  •  ${d.pipeline_name}`;
  document.getElementById('last-updated').textContent =
    'Last updated: ' + d.last_updated;

  renderBreadcrumb();

  if (view === 'dashboard') {
    renderDashboard();
    document.getElementById('view-dashboard').style.display = 'block';
  } else if (view === 'team') {
    renderTeamView(team);
    document.getElementById('view-team').style.display = 'block';
  } else if (view === 'rep') {
    renderRepView(team, rep);
    document.getElementById('view-rep').style.display = 'block';
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderBreadcrumb() {
  const bc = document.getElementById('breadcrumb');
  const { view, team, rep } = viewState;
  if (view === 'dashboard') {
    bc.style.display = 'none';
    return;
  }
  bc.style.display = 'flex';
  let html = `<a href="#" data-nav="dashboard">Dashboard</a>`;
  if (view === 'team' || view === 'rep') {
    html += `<span class="bc-sep">›</span>`;
    if (view === 'rep') {
      html += `<a href="#" data-nav="team" data-team="${escapeHtml(team)}">${escapeHtml(team)} Team</a>`;
      html += `<span class="bc-sep">›</span>`;
      html += `<span class="bc-current">${escapeHtml(rep)}</span>`;
    } else {
      html += `<span class="bc-current">${escapeHtml(team)} Team</span>`;
    }
  }
  bc.innerHTML = html;
  bc.querySelectorAll('a[data-nav]').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const target = a.dataset.nav;
      if (target === 'dashboard') setView('dashboard');
      else if (target === 'team') setView('team', a.dataset.team);
    });
  });
}

// ----- Dashboard view -------------------------------------------------------

function renderDashboard() {
  renderKPIs(dashboardData.kpis, document.getElementById('kpi-section'),
             { scope: {}, overview: true });
  renderEntQuarterlyPanel(dashboardData.kpis);
  renderTeamCards(dashboardData.teams);
}

function renderEntQuarterlyPanel(k) {
  const panel = document.getElementById('ent-quarterly-panel');
  if (!panel) return;
  if (k.ent_quarter_target === undefined) {
    panel.style.display = 'none';
    return;
  }
  const attain = Number(k.ent_quarter_attainment) || 0;
  const cls = attainmentClass(attain);
  panel.style.display = '';
  document.getElementById('ent-q-title').textContent =
    `ENT Team — ${k.ent_quarter_label || 'Quarterly'}`;
  document.getElementById('ent-q-sub').textContent =
    'Reported separately from monthly totals · ENT runs on quarterly targets';
  document.getElementById('ent-q-pill').textContent =
    `${fmtPct(attain)} attainment`;
  document.getElementById('ent-q-pill').className = `ent-q-pill ${cls}`;
  document.getElementById('ent-q-revenue').textContent = fmtUSD(k.ent_quarter_revenue);
  document.getElementById('ent-q-revenue-sub').textContent =
    `${fmtNum(k.ent_quarter_won_count)} closed-won deals this quarter`;
  document.getElementById('ent-q-target').textContent = fmtUSD(k.ent_quarter_target);
  document.getElementById('ent-q-attainment').textContent = fmtPct(attain);
  document.getElementById('ent-q-attain-sub').textContent =
    `${fmtUSD(k.ent_quarter_revenue)} of ${fmtUSD(k.ent_quarter_target)}`;
}

function buildKpiCards(k, opts = {}) {
  const breakdownParts = opts.mqlByTeam
    ? Object.entries(opts.mqlByTeam).map(([t, n]) => `${t}: ${n}`)
    : [];
  // Unassigned MQLs intentionally hidden from the subtitle per request.
  const mqlBreakdown = breakdownParts.join(' · ');
  const overview = !!opts.overview;
  const cards = [
    { label: 'Total Revenue (This Month)', value: fmtUSD(k.total_revenue),
      sub: overview ? 'SMB + AM · monthly (excl. ENT)' : 'Closed Won this month',
      cls: 'success' },
    { label: 'Closed Won', value: fmtNum(k.closed_won_count),
      sub: 'deals this month · click for detail', cls: 'success',
      action: 'closed_won', enabled: k.closed_won_count > 0 },
    { label: 'Total Target', value: fmtUSD(k.total_target),
      sub: overview ? 'SMB + AM · monthly (excl. ENT)' : 'monthly target',
      cls: 'info' },
    { label: 'Attainment %', value: fmtPct(k.attainment_pct),
      sub: 'revenue ÷ target',
      cls: k.attainment_pct >= 80 ? 'success' : k.attainment_pct >= 50 ? 'warning' : 'danger' },
    { label: 'Opp → Win %', value: fmtPct(k.opp_win_pct),
      sub: `${fmtNum(k.closed_won_count)} won / ${fmtNum(k.total_opps)} opps (created this month)`, cls: 'info' },
    { label: 'Deals Lost', value: fmtNum(k.deals_lost),
      sub: 'Closed Lost this month · click for detail', cls: 'danger',
      action: 'deals_lost', enabled: k.deals_lost > 0 },
    { label: 'Open Pipeline', value: fmtUSD(k.open_pipeline),
      sub: `${fmtNum(k.open_pipeline_count)} open deals · click for detail`, cls: 'info',
      action: 'open_pipeline', enabled: k.open_pipeline_count > 0 },
    { label: 'Avg Deal Size (Closed Won)', value: fmtUSD(k.avg_deal_size),
      sub: 'revenue ÷ deals won', cls: 'info' },
    { label: 'Avg Deal Age (Closed Won)', value: fmtDays(k.avg_deal_age),
      sub: 'this month · click for detail',
      action: 'closed_won', enabled: k.closed_won_count > 0,
      ageVariant: true },
  ];
  if (k.mql_count !== undefined) {
    cards.push({
      label: 'MQL Assigned (This Month)',
      value: fmtNum(k.mql_count),
      sub: opts.mqlSub || mqlBreakdown || 'Business MQL',
      cls: 'info',
    });
  }
  return cards;
}

function renderKPIs(k, container, opts = {}) {
  const scope = opts.scope || {};
  const cards = buildKpiCards(k, { ...opts, mqlByTeam: k.mql_by_team });
  container.innerHTML = '';
  cards.forEach(c => {
    const isActionable = c.action && c.enabled !== false;
    const div = document.createElement('div');
    div.className = 'kpi-card ' + (c.cls || '') + (isActionable ? ' clickable' : '');
    div.innerHTML = `
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
      ${c.sub ? `<div class="kpi-sub">${c.sub}</div>` : ''}
    `;
    if (isActionable) {
      const handler = () => openDealsDrilldown(c.action, scope, !!c.ageVariant);
      div.addEventListener('click', handler);
      div.setAttribute('role', 'button');
      div.setAttribute('tabindex', '0');
      div.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' || e.key === ' ') handler();
      });
    }
    container.appendChild(div);
  });
}

function renderTeamCards(teams) {
  const grid = document.getElementById('teams-grid');
  grid.innerHTML = '';
  ['SMB', 'AM', 'ENT'].forEach(teamName => {
    const t = teams[teamName];
    if (!t) return;
    const aClass = attainmentClass(t.attainment);
    const pClass = progressClass(t.attainment);
    const card = document.createElement('div');
    card.className = `team-card team-${teamName} clickable`;
    card.innerHTML = `
      <div class="team-header">
        <div class="team-name">${teamName} Team</div>
        <div class="team-attainment ${aClass}">${fmtPct(t.attainment)}</div>
      </div>
      <div class="team-stats">
        <div>
          <div class="team-stat-label">Revenue</div>
          <div class="team-stat-value">${fmtUSD(t.revenue)}</div>
        </div>
        <div>
          <div class="team-stat-label">Target</div>
          <div class="team-stat-value">${fmtUSD(t.target)}</div>
        </div>
        <div>
          <div class="team-stat-label">Closed Won</div>
          <div class="team-stat-value">${fmtNum(t.closed_won)}</div>
        </div>
        <div>
          <div class="team-stat-label">Open Pipeline</div>
          <div class="team-stat-value">${fmtUSD(t.open_pipeline)}</div>
        </div>
      </div>
      <div>
        <div class="progress-bar">
          <div class="progress-fill ${pClass}"
               style="width:${Math.min(t.attainment, 100)}%"></div>
        </div>
        <div class="progress-meta">
          <span>${fmtPct(t.attainment)} attainment</span>
          <span>${fmtUSD(t.revenue)} / ${fmtUSD(t.target)}</span>
        </div>
      </div>
      <div class="card-cta">View team detail →</div>
    `;
    card.addEventListener('click', () => setView('team', teamName));
    grid.appendChild(card);
  });
}

// ----- Team view ------------------------------------------------------------

function renderTeamView(teamName) {
  const t = dashboardData.teams[teamName];
  if (!t) return;
  document.getElementById('team-view-title').textContent = `${teamName} Team`;
  document.getElementById('team-view-sub').textContent =
    `${dashboardData.month} · ${fmtUSD(t.revenue)} of ${fmtUSD(t.target)} target · ${fmtPct(t.attainment)} attainment`;

  const teamKpis = {
    total_revenue: t.revenue,
    closed_won_count: t.closed_won,
    total_target: t.target,
    attainment_pct: t.attainment,
    opp_win_pct: t.opp_win_pct,
    total_opps: t.total_opps,
    deals_lost: t.deals_lost,
    open_pipeline: t.open_pipeline,
    open_pipeline_count: t.open_pipeline_count,
    avg_deal_size: t.avg_deal_size,
    avg_deal_age: t.avg_deal_age,
    mql_count: t.mql_count,
    mql_by_team: { [teamName]: t.mql_count },
  };
  renderKPIs(teamKpis, document.getElementById('team-kpis'),
             { scope: { team: teamName },
               mqlSub: `Business MQL · ${teamName}` });

  const reps = Object.values(t.reps).sort((a, b) => b.revenue - a.revenue);
  renderRepPerfTable(teamName, reps);
  renderAMCoverageTable(teamName, reps);
  renderAMDealTypeTable(teamName, reps, t);
  renderLeadStatusPivot(t);
  renderSmbUnqualified(teamName, reps);
  renderTeamRolling90(teamName, reps);
  renderLostThemes(t);
  renderRepFunnelTable(teamName, reps);
}

function renderTeamRolling90(teamName, reps) {
  const panel = document.getElementById('team-rolling-panel');
  if (!panel) return;
  // Hide rolling-90 for AM — AM accounts don't follow MQL→Opp→Won the same way.
  if (teamName === 'AM') { panel.style.display = 'none'; return; }
  const sub = (dashboardData.kpis && dashboardData.kpis.rolling_window_label) || '';
  document.getElementById('team-rolling-sub').textContent =
    `Past 90 days · ${sub}`;

  // Build rep rows from rolling_90 fields stamped on each rep.
  // Sort by MQL→Won conversion descending (frozen — uses backend values as-is).
  const rows = (reps || []).filter(r => {
    const r90 = r.rolling_90 || {};
    return (r90.mql || 0) + (r90.opps || 0) + (r90.won || 0) > 0;
  }).sort((a, b) => (b.rolling_90?.mql_to_won || 0) - (a.rolling_90?.mql_to_won || 0));
  if (!rows.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const tbody = document.getElementById('team-rolling-tbody');
  tbody.innerHTML = '';
  let tMql = 0, tOpp = 0, tWon = 0;
  rows.forEach(r => {
    const r90 = r.rolling_90 || {};
    const mql = r90.mql || 0, opps = r90.opps || 0, won = r90.won || 0;
    tMql += mql; tOpp += opps; tWon += won;
    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    tr.innerHTML = `
      <td class="col-rep">
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(r.name)}</span>
          <span class="row-rep-name">${escapeHtml(r.name)}</span>
        </div>
      </td>
      <td class="num">${fmtNum(mql)}</td>
      <td class="num">${fmtNum(opps)}</td>
      <td class="num">${fmtPct(r90.mql_to_opp || 0)}</td>
      <td class="num">${fmtNum(won)}</td>
      <td class="num">${fmtPct(r90.opp_to_won || 0)}</td>
      <td class="num"><strong>${fmtPct(r90.mql_to_won || 0)}</strong></td>
    `;
    tr.addEventListener('click', () => setView('rep', teamName, r.name));
    tbody.appendChild(tr);
  });
  const totMqlOpp = tMql ? (tOpp / tMql * 100) : 0;
  const totOppWon = tOpp ? (tWon / tOpp * 100) : 0;
  const totMqlWon = tMql ? (tWon / tMql * 100) : 0;
  document.getElementById('team-rolling-tfoot').innerHTML = `
    <td class="col-rep"><strong>Total</strong></td>
    <td class="num"><strong>${fmtNum(tMql)}</strong></td>
    <td class="num"><strong>${fmtNum(tOpp)}</strong></td>
    <td class="num"><strong>${fmtPct(totMqlOpp)}</strong></td>
    <td class="num"><strong>${fmtNum(tWon)}</strong></td>
    <td class="num"><strong>${fmtPct(totOppWon)}</strong></td>
    <td class="num"><strong>${fmtPct(totMqlWon)}</strong></td>
  `;
}

function renderSmbUnqualified(teamName, reps) {
  const panel = document.getElementById('smb-unq-panel');
  if (teamName !== 'SMB') { panel.style.display = 'none'; return; }
  const withCounts = reps.filter(r => (r.unqualified_count || 0) > 0);
  if (!withCounts.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  const tbody = document.getElementById('smb-unq-tbody');
  tbody.innerHTML = '';
  let total = 0;
  withCounts.sort((a, b) => (b.unqualified_count || 0) - (a.unqualified_count || 0));
  withCounts.forEach((r, idx) => {
    total += r.unqualified_count;
    const reasons = r.unqualified_reasons || [];
    const hasReasons = reasons.length > 0;
    const rowId = `unq-row-${idx}`;

    // Summary row — click to expand the reason breakdown
    const tr = document.createElement('tr');
    tr.className = 'clickable-row' + (hasReasons ? ' unq-expandable' : '');
    tr.innerHTML = `
      <td>
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(r.name)}</span>
          <span class="row-rep-name">${escapeHtml(r.name)}
            ${hasReasons ? `<span class="unq-caret" id="${rowId}-caret">▸</span>` : ''}
          </span>
        </div>
      </td>
      <td class="num"><strong>${fmtNum(r.unqualified_count)}</strong></td>
    `;
    tbody.appendChild(tr);

    if (!hasReasons) return;

    // Hidden expansion row with the reasons breakdown
    const expand = document.createElement('tr');
    expand.id = rowId;
    expand.className = 'unq-expand-row';
    expand.style.display = 'none';
    const reasonHtml = reasons.map(rs => `
      <div class="unq-reason">
        <div class="unq-reason-head">
          <span class="unq-reason-text">${escapeHtml(rs.reason)}</span>
          <span class="unq-reason-count">${fmtNum(rs.count)}</span>
        </div>
        ${rs.contacts && rs.contacts.length ? `
          <div class="unq-reason-contacts">
            ${rs.contacts.map(c => `<span class="unq-contact-chip" title="${escapeHtml(c.email||'')}">${escapeHtml(c.name)}</span>`).join('')}
          </div>` : ''}
      </div>
    `).join('');
    expand.innerHTML = `<td colspan="2"><div class="unq-expand">${reasonHtml}</div></td>`;
    tbody.appendChild(expand);

    tr.addEventListener('click', (e) => {
      // Stop the click from cascading to a setView navigation
      e.stopPropagation();
      const show = expand.style.display === 'none';
      expand.style.display = show ? 'table-row' : 'none';
      const caret = document.getElementById(rowId + '-caret');
      if (caret) caret.textContent = show ? '▾' : '▸';
    });
  });
  document.getElementById('smb-unq-tfoot').innerHTML = `
    <td><strong>Team Total</strong></td>
    <td class="num"><strong>${fmtNum(total)}</strong></td>
  `;
}

function renderLeadStatusPivot(teamData) {
  const panel = document.getElementById('lead-status-panel');
  const ls = teamData.lead_status || {};
  const statuses = ls.statuses || [];
  const rows = ls.rows || [];
  if (!statuses.length || !rows.length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';

  // Header
  const thead = document.getElementById('lead-status-thead');
  let h = `<th>Lead Status</th>`;
  rows.forEach(r => { h += `<th class="num">${escapeHtml(r.owner)}</th>`; });
  h += `<th class="num">Grand Total</th>`;
  thead.innerHTML = h;

  // Body — one row per status
  const tbody = document.getElementById('lead-status-tbody');
  tbody.innerHTML = '';
  statuses.forEach(s => {
    let html = `<td><strong>${escapeHtml(s)}</strong></td>`;
    let rowTotal = 0;
    rows.forEach(r => {
      const v = r.counts[s] || 0;
      rowTotal += v;
      html += `<td class="num${v ? '' : ' zero'}">${v ? fmtNum(v) : '0'}</td>`;
    });
    html += `<td class="num"><strong>${fmtNum(rowTotal)}</strong></td>`;
    const tr = document.createElement('tr');
    tr.innerHTML = html;
    tbody.appendChild(tr);
  });

  // Footer — Grand Total per rep
  const tfoot = document.getElementById('lead-status-tfoot');
  let f = `<td>Grand Total</td>`;
  rows.forEach(r => { f += `<td class="num">${fmtNum(r.total || 0)}</td>`; });
  f += `<td class="num"><strong>${fmtNum(ls.grand_total || 0)}</strong></td>`;
  tfoot.innerHTML = f;
}

function renderLostReasons(teamData) {
  const panel = document.getElementById('lost-reason-panel');
  const reasons = teamData.lost_reasons || [];
  const total = teamData.lost_total || 0;
  if (!reasons.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const tbody = document.getElementById('lost-reason-tbody');
  tbody.innerHTML = '';
  reasons.forEach(r => {
    const pct = total ? (r.count / total * 100) : 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(r.reason)}</td>
      <td class="num">${fmtNum(r.count)}</td>
      <td class="num">${fmtPct(pct)}</td>
    `;
    tbody.appendChild(tr);
  });

  document.getElementById('lost-reason-tfoot').innerHTML = `
    <td><strong>Total</strong></td>
    <td class="num"><strong>${fmtNum(total)}</strong></td>
    <td class="num"><strong>${fmtPct(total ? 100 : 0)}</strong></td>
  `;
}

let lostThemesData = [];
function renderLostReasonsTable(panelId, tbodyId, tfootId, reasons, total, scopeKey) {
  const panel = document.getElementById(panelId);
  if (!reasons || !reasons.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  const tbody = document.getElementById(tbodyId);
  tbody.innerHTML = '';
  reasons.forEach((r, i) => {
    const idx = lostThemesData.length;
    lostThemesData.push({ ...r, theme: r.reason || r.theme || '-' });
    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    tr.innerHTML = `
      <td>
        <strong>${escapeHtml(r.reason || r.theme || '-')}</strong>
        <a href="#" class="link-cell" style="margin-left:0.6rem">VIEW DESCRIPTIONS</a>
      </td>
      <td class="num">${fmtNum(r.count)}</td>
      <td class="num">${fmtPct(r.pct || 0)}</td>
    `;
    tr.addEventListener('click', () => openThemeModal(idx));
    tbody.appendChild(tr);
  });
  document.getElementById(tfootId).innerHTML = `
    <td><strong>Total</strong></td>
    <td class="num"><strong>${fmtNum(total)}</strong></td>
    <td class="num"><strong>${fmtPct(total ? 100 : 0)}</strong></td>
  `;
}

function renderLostThemes(teamData) {
  lostThemesData = [];  // reset shared lookup before re-render
  renderLostReasonsTable('lost-theme-panel', 'lost-theme-tbody',
                          'lost-theme-tfoot',
                          teamData.lost_reasons || teamData.lost_themes || [],
                          teamData.lost_total || 0, 'team');
}

function renderRepLostReasons(rep) {
  // Append rep entries to the same lostThemesData used for the modal
  const reasons = rep.lost_reasons || [];
  const total = rep.lost_total || 0;
  if (!reasons.length) {
    document.getElementById('rep-lost-panel').style.display = 'none';
    return;
  }
  // Re-init shared array for rep view (rep view replaces team view content)
  lostThemesData = [];
  renderLostReasonsTable('rep-lost-panel', 'rep-lost-tbody',
                          'rep-lost-tfoot', reasons, total, 'rep');
}

function openThemeModal(idx) {
  const t = lostThemesData[idx];
  if (!t) return;
  document.getElementById('theme-modal-title').textContent = `Lost Theme — ${t.theme}`;
  document.getElementById('theme-modal-sub').textContent =
    `${t.count} deal${t.count === 1 ? '' : 's'} · ${fmtPct(t.pct || 0)} of all closed-lost`;
  const tbody = document.getElementById('theme-modal-tbody');
  tbody.innerHTML = '';
  (t.items || []).forEach(it => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="white-space:pre-wrap">${escapeHtml(it.description)}</td>
      <td>${escapeHtml(it.deal_name || '-')}</td>
      <td>${escapeHtml(it.owner || '-')}</td>
      <td class="num">${fmtUSD(it.amount || 0)}</td>
      <td>${escapeHtml(it.close_date || '-')}</td>
    `;
    tbody.appendChild(tr);
  });
  document.getElementById('theme-modal').style.display = 'flex';
}
document.getElementById('close-theme').addEventListener('click', () => {
  document.getElementById('theme-modal').style.display = 'none';
});
document.getElementById('theme-modal').addEventListener('click', (e) => {
  if (e.target.id === 'theme-modal') {
    document.getElementById('theme-modal').style.display = 'none';
  }
});

function renderAMCoverageTable(teamName, reps) {
  const panel = document.getElementById('am-coverage-panel');
  if (teamName !== 'AM') {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';

  const tbody = document.getElementById('am-coverage-tbody');
  tbody.innerHTML = '';
  let totals = {
    companies: 0, won_companies: 0, deals_won: 0, no_activity: 0,
  };

  reps.forEach(r => {
    totals.companies += r.am_total_companies || 0;
    totals.won_companies += r.am_companies_with_closed_won || 0;
    totals.deals_won += r.closed_won || 0;
    totals.no_activity += r.am_closed_won_no_activity || 0;

    const pctClass = attainmentClass(r.am_pct_closed_won || 0);
    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    tr.innerHTML = `
      <td class="col-rep">
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(r.name)}</span>
          <span class="row-rep-name">${escapeHtml(r.name)}</span>
        </div>
      </td>
      <td class="num">${fmtNum(r.am_total_companies || 0)}</td>
      <td class="num">${fmtNum(r.closed_won || 0)}</td>
      <td class="num"><span class="pct-pill ${pctClass}">${fmtPct(r.am_pct_closed_won || 0)}</span></td>
      <td class="num">${fmtNum(r.am_closed_won_no_activity || 0)}</td>
    `;
    tr.addEventListener('click', () => setView('rep', teamName, r.name));
    tbody.appendChild(tr);
  });

  const teamPct = totals.companies
    ? (totals.won_companies / totals.companies * 100)
    : 0;
  const aClass = attainmentClass(teamPct);
  document.getElementById('am-coverage-tfoot').innerHTML = `
    <td class="col-rep">Team Total</td>
    <td class="num">${fmtNum(totals.companies)}</td>
    <td class="num">${fmtNum(totals.deals_won)}</td>
    <td class="num"><span class="pct-pill ${aClass}">${fmtPct(teamPct)}</span></td>
    <td class="num">${fmtNum(totals.no_activity)}</td>
  `;
}

function renderAMDealTypeTable(teamName, reps, teamData) {
  const panel = document.getElementById('am-dealtype-panel');
  if (teamName !== 'AM') {
    panel.style.display = 'none';
    return;
  }

  const columns = teamData.am_deal_type_columns || [];
  const grandTotals = teamData.am_deal_type_grand_totals || {};
  const overall = grandTotals['Grand Total'] || 0;

  // Hide the panel entirely when there are no closed-won deals to break down
  if (!columns.length || overall === 0) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';

  // Header row: Rep + each deal-type column + Grand Total
  const thead = document.getElementById('am-dealtype-thead');
  let headHtml = `<th class="col-rep">Rep</th>`;
  columns.forEach(c => { headHtml += `<th class="num">${escapeHtml(c)}</th>`; });
  headHtml += `<th class="num">Grand Total</th>`;
  thead.innerHTML = headHtml;

  // Body rows — one per AM rep
  const tbody = document.getElementById('am-dealtype-tbody');
  tbody.innerHTML = '';
  reps.forEach(r => {
    const row = r.am_deal_type_row || {};
    const rowTotal = row['Grand Total'] || 0;
    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    let html = `
      <td class="col-rep">
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(r.name)}</span>
          <span class="row-rep-name">${escapeHtml(r.name)}</span>
        </div>
      </td>`;
    columns.forEach(c => {
      const v = row[c] || 0;
      html += `<td class="num${v ? '' : ' zero'}">${v ? fmtNum(v) : '0'}</td>`;
    });
    html += `<td class="num"><strong>${fmtNum(rowTotal)}</strong></td>`;
    tr.innerHTML = html;
    tr.addEventListener('click', () => setView('rep', teamName, r.name));
    tbody.appendChild(tr);
  });

  // Grand totals row
  const tfoot = document.getElementById('am-dealtype-tfoot');
  let footHtml = `<td class="col-rep">Grand Total</td>`;
  columns.forEach(c => {
    footHtml += `<td class="num">${fmtNum(grandTotals[c] || 0)}</td>`;
  });
  footHtml += `<td class="num"><strong>${fmtNum(overall)}</strong></td>`;
  tfoot.innerHTML = footHtml;
}

function renderRepPerfTable(teamName, reps) {
  // Sort by Achieved % (attainment) descending
  reps = [...reps].sort((a, b) => (b.attainment || 0) - (a.attainment || 0));
  const tbody = document.getElementById('rep-perf-tbody');
  tbody.innerHTML = '';

  let totals = {
    revenue: 0, won: 0, lost: 0, open_n: 0, opps: 0,
    avgDealSizeNum: 0, avgDealSizeDen: 0,
    avgAgeNum: 0, avgAgeDen: 0,
    mql: 0, pipeline: 0, target: 0,
  };

  reps.forEach(r => {
    const aClass = attainmentClass(r.attainment);
    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    tr.innerHTML = `
      <td class="col-rep">
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(r.name)}</span>
          <span class="row-rep-name">${escapeHtml(r.name)}</span>
        </div>
      </td>
      <td class="num">${fmtUSD(r.revenue)}</td>
      <td class="num">${fmtNum(r.closed_won)}</td>
      <td class="num">${fmtNum(r.deals_lost)}</td>
      <td class="num">${fmtNum(r.open_pipeline_count)}</td>
      <td class="num">${fmtNum(r.total_opps)}</td>
      <td class="num">${fmtPct(r.opp_win_pct)}</td>
      <td class="num">${fmtUSD(r.avg_deal_size)}</td>
      <td class="num">${(Number(r.avg_deal_age) || 0).toFixed(1)}d</td>
      <td class="num">${fmtNum(r.mql_count)}</td>
      <td class="num">${fmtUSD(r.open_pipeline)}</td>
      <td class="num"><span class="pct-pill ${aClass}">${fmtPct(r.attainment)}</span></td>
      <td class="num">${fmtUSD(r.target)}</td>
    `;
    tr.addEventListener('click', () => setView('rep', teamName, r.name));
    tbody.appendChild(tr);

    totals.revenue += r.revenue;
    totals.won += r.closed_won;
    totals.lost += r.deals_lost;
    totals.open_n += r.open_pipeline_count;
    totals.opps += r.total_opps;
    totals.avgDealSizeNum += r.open_pipeline;
    totals.avgDealSizeDen += r.open_pipeline_count;
    totals.avgAgeNum += r.age_total || 0;
    totals.avgAgeDen += r.age_count || 0;
    totals.mql += r.mql_count;
    totals.pipeline += r.open_pipeline;
    totals.target += r.target;
  });

  const teamWinPct = totals.opps ? (totals.won / totals.opps * 100) : 0;
  const teamAvgDeal = totals.avgDealSizeDen ? (totals.avgDealSizeNum / totals.avgDealSizeDen) : 0;
  const teamAvgAge = totals.avgAgeDen ? (totals.avgAgeNum / totals.avgAgeDen) : 0;
  const teamAttain = totals.target ? (totals.revenue / totals.target * 100) : 0;
  const aClass = attainmentClass(teamAttain);

  document.getElementById('rep-perf-tfoot').innerHTML = `
    <td class="col-rep">Team Total</td>
    <td class="num">${fmtUSD(totals.revenue)}</td>
    <td class="num">${fmtNum(totals.won)}</td>
    <td class="num">${fmtNum(totals.lost)}</td>
    <td class="num">${fmtNum(totals.open_n)}</td>
    <td class="num">${fmtNum(totals.opps)}</td>
    <td class="num">${fmtPct(teamWinPct)}</td>
    <td class="num">${fmtUSD(teamAvgDeal)}</td>
    <td class="num">${teamAvgAge.toFixed(1)}d</td>
    <td class="num">${fmtNum(totals.mql)}</td>
    <td class="num">${fmtUSD(totals.pipeline)}</td>
    <td class="num"><span class="pct-pill ${aClass}">${fmtPct(teamAttain)}</span></td>
    <td class="num">${fmtUSD(totals.target)}</td>
  `;
}

function renderRepFunnelTable(teamName, reps) {
  // Hide MQL → Revenue funnel for AM team
  const panel = document.querySelector('#view-team .card-panel:has(#rep-funnel-tbody)')
    || document.getElementById('rep-funnel-tbody')?.closest('.card-panel');
  if (teamName === 'AM') {
    if (panel) panel.style.display = 'none';
    return;
  } else if (panel) {
    panel.style.display = '';
  }
  // Sort by MQL → Won % descending so top converters surface first
  reps = [...reps].sort((a, b) => {
    const aw = (a.mql_count || 0) ? (a.closed_won || 0) / a.mql_count * 100 : 0;
    const bw = (b.mql_count || 0) ? (b.closed_won || 0) / b.mql_count * 100 : 0;
    return bw - aw;
  });
  const tbody = document.getElementById('rep-funnel-tbody');
  tbody.innerHTML = '';
  let tMql = 0, tOpps = 0, tWon = 0;

  reps.forEach(r => {
    const mqlOpp = r.mql_count ? (r.total_opps / r.mql_count * 100) : 0;
    const oppWon = r.total_opps ? (r.closed_won / r.total_opps * 100) : 0;
    const mqlWon = r.mql_count ? (r.closed_won / r.mql_count * 100) : 0;
    tMql += r.mql_count;
    tOpps += r.total_opps;
    tWon += r.closed_won;

    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    tr.innerHTML = `
      <td class="col-rep">
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(r.name)}</span>
          <span class="row-rep-name">${escapeHtml(r.name)}</span>
        </div>
      </td>
      <td class="num">${fmtNum(r.mql_count)}</td>
      <td class="num">${fmtNum(r.total_opps)}</td>
      <td class="num">${fmtNum(r.closed_won)}</td>
      <td class="num">${r.mql_count ? fmtPct(mqlOpp) : '—'}</td>
      <td class="num">${r.total_opps ? fmtPct(oppWon) : '—'}</td>
      <td class="num">${r.mql_count ? fmtPct(mqlWon) : '—'}</td>
    `;
    tr.addEventListener('click', () => setView('rep', teamName, r.name));
    tbody.appendChild(tr);
  });

  const totMqlOpp = tMql ? (tOpps / tMql * 100) : 0;
  const totOppWon = tOpps ? (tWon / tOpps * 100) : 0;
  const totMqlWon = tMql ? (tWon / tMql * 100) : 0;
  document.getElementById('rep-funnel-tfoot').innerHTML = `
    <td class="col-rep">Team Total</td>
    <td class="num">${fmtNum(tMql)}</td>
    <td class="num">${fmtNum(tOpps)}</td>
    <td class="num">${fmtNum(tWon)}</td>
    <td class="num">${tMql ? fmtPct(totMqlOpp) : '—'}</td>
    <td class="num">${tOpps ? fmtPct(totOppWon) : '—'}</td>
    <td class="num">${tMql ? fmtPct(totMqlWon) : '—'}</td>
  `;
}

// ----- Rep view -------------------------------------------------------------

function repInitials(name) {
  return name.split(/\s+/).map(p => p[0]).filter(Boolean).slice(0, 2).join('').toUpperCase();
}

function renderRepView(teamName, repName) {
  const t = dashboardData.teams[teamName];
  if (!t) return;
  const rep = t.reps[repName];
  if (!rep) return;

  // Hero header
  document.getElementById('rep-view-title').textContent = repName;
  document.getElementById('rep-view-sub').textContent =
    `${teamName} Team · ${dashboardData.month}`;
  const avatar = document.getElementById('rep-avatar');
  avatar.textContent = repInitials(repName);
  avatar.className = `rep-avatar avatar-${teamName}`;

  const aClass = attainmentClass(rep.attainment);
  const pClass = progressClass(rep.attainment);
  const pill = document.getElementById('rep-attain-pill');
  pill.className = `rep-pill ${aClass}`;
  pill.textContent = `${fmtPct(rep.attainment)} attainment`;

  const fill = document.getElementById('rep-progress-fill');
  fill.className = `progress-fill ${pClass}`;
  fill.style.width = Math.min(rep.attainment, 100) + '%';
  document.getElementById('rep-progress-meta').innerHTML =
    `<span>${fmtUSD(rep.revenue)} earned</span><span>${fmtUSD(rep.target)} target</span>`;

  const repKpis = {
    total_revenue: rep.revenue,
    closed_won_count: rep.closed_won,
    total_target: rep.target,
    attainment_pct: rep.attainment,
    opp_win_pct: rep.opp_win_pct,
    total_opps: rep.total_opps,
    deals_lost: rep.deals_lost,
    open_pipeline: rep.open_pipeline,
    open_pipeline_count: rep.open_pipeline_count,
    avg_deal_size: rep.avg_deal_size,
    avg_deal_age: rep.avg_deal_age,
    mql_count: rep.mql_count,
  };
  renderKPIs(repKpis, document.getElementById('rep-kpis'),
             { scope: { team: teamName, rep: repName },
               mqlSub: `Business MQL · ${repName}` });

  // Audit notes / activity / funnel / rolling-90 / trends / lost-reasons / discount
  renderRepAudit(rep);
  renderRepActivity(rep);
  renderRepFunnelMonth(teamName, rep);
  renderRepRolling90(teamName, rep);
  renderRepTrends(rep);
  renderRepLostReasons(rep);
  renderRepDiscount(rep);

  // Country breakdown panel (only shows when there are won deals)
  const cPanel = document.getElementById('rep-countries-panel');
  const cPills = document.getElementById('rep-countries-pills');
  const countries = rep.closed_won_countries || [];
  if (countries.length) {
    cPills.innerHTML = countries.map(c => `
      <span class="country-pill">
        <span class="country-pill-name">${escapeHtml(c.country)}</span>
        <span class="country-pill-count">${c.count}</span>
      </span>`).join('');
    cPanel.style.display = '';
  } else {
    cPanel.style.display = 'none';
  }

  repDealsTab = 'closed_won_deals';
  document.querySelectorAll('#rep-deal-tabs .tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === repDealsTab);
  });
  renderRepDealsTable(rep);
}

function renderRepAudit(rep) {
  const panel = document.getElementById('rep-audit-panel');
  const notes = rep.audit_notes || [];
  if (!notes.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  document.getElementById('rep-audit-title').textContent =
    `Auditing — ${escapeHtml(rep.name || '')}`;
  const list = document.getElementById('rep-audit-list');
  list.innerHTML = '';
  notes.forEach(n => {
    const li = document.createElement('li');
    // Backward-compatible: notes may be plain strings (older format) or
    // {headline, deals[]} dicts (new format with explicit deal listing).
    if (typeof n === 'string') {
      li.textContent = n;
    } else {
      const headline = n.headline || '';
      const deals = n.deals || [];
      let html = `<div class="audit-headline">${escapeHtml(headline)}</div>`;
      if (deals.length) {
        html += '<ul class="audit-deal-list">';
        deals.forEach(d => {
          const amount = d.amount ? ` · $${Number(d.amount).toLocaleString('en-US',{maximumFractionDigits:0})}` : '';
          const meta = d.meta ? ` <span class="audit-deal-meta">(${escapeHtml(d.meta)})</span>` : '';
          html += `<li class="audit-deal-item"><span class="audit-deal-name">${escapeHtml(d.name)}</span>${amount}${meta}</li>`;
        });
        html += '</ul>';
      }
      li.innerHTML = html;
    }
    list.appendChild(li);
  });
}

function renderRepActivity(rep) {
  const panel = document.getElementById('rep-activity-panel');
  const a = rep.activity_data;
  if (!a) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const rows = [
    { label: 'Calls',     goal: a.call_goal,  actual: a.call_actual },
    { label: 'Emails',    goal: a.email_goal, actual: a.email_actual },
    { label: 'Talk Time', goal: a.talk_goal,  actual: a.talk_actual },
  ];
  const tbody = document.getElementById('rep-activity-tbody');
  tbody.innerHTML = '';
  rows.forEach(r => {
    const pct = r.goal ? (r.actual / r.goal * 100) : 0;
    const cls = attainmentClass(pct);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${escapeHtml(r.label)}</strong></td>
      <td class="num">${fmtNum(r.goal)}</td>
      <td class="num">${fmtNum(r.actual)}</td>
      <td class="num"><span class="pct-pill ${cls}">${fmtPct(pct)}</span></td>
    `;
    tbody.appendChild(tr);
  });
  if (a.days_worked != null) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>Days Worked</strong></td>
      <td class="num">—</td>
      <td class="num">${fmtNum(a.days_worked)}</td>
      <td class="num">—</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderRepFunnelMonth(teamName, rep) {
  // Show the full team funnel (all reps + Team Total) — matches the team-view
  // "MQL → Revenue Conversion Funnel" layout. The current rep is highlighted.
  const panel = document.getElementById('rep-funnel-panel');
  // AM team does not follow the MQL→Opp→Won motion in the same way; hide.
  if (teamName === 'AM') { panel.style.display = 'none'; return; }
  const team = dashboardData.teams[teamName];
  if (!team) { panel.style.display = 'none'; return; }
  // Sort by MQL → Won % descending so highest converters surface first
  const allReps = Object.values(team.reps || {}).sort((a, b) => {
    const aw = (a.mql_count || 0) ? (a.closed_won || 0) / a.mql_count * 100 : 0;
    const bw = (b.mql_count || 0) ? (b.closed_won || 0) / b.mql_count * 100 : 0;
    return bw - aw;
  });
  if (!allReps.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  document.getElementById('rep-funnel-sub').textContent =
    `${teamName} Team · ${dashboardData.month} · viewing ${rep.name}`;

  // Ensure tfoot exists for the Team Total row
  const table = document.getElementById('rep-view-funnel-table');
  let tfoot = table.querySelector('tfoot');
  if (!tfoot) {
    tfoot = document.createElement('tfoot');
    tfoot.innerHTML = '<tr id="rep-view-funnel-tfoot"></tr>';
    table.appendChild(tfoot);
  }

  let tMql = 0, tOpp = 0, tWon = 0;
  const tbody = document.getElementById('rep-view-funnel-tbody');
  tbody.innerHTML = '';
  allReps.forEach(r => {
    const mql = r.mql_count || 0;
    const opps = r.total_opps || 0;
    const won = r.closed_won || 0;
    tMql += mql; tOpp += opps; tWon += won;
    const mqlOpp = mql ? (opps / mql * 100) : null;
    const oppWon = opps ? (won / opps * 100) : null;
    const mqlWon = mql ? (won / mql * 100) : null;
    const tr = document.createElement('tr');
    tr.className = 'clickable-row' + (r.name === rep.name ? ' current-rep' : '');
    tr.innerHTML = `
      <td class="col-rep">
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(r.name)}</span>
          <span class="row-rep-name">${escapeHtml(r.name)}</span>
        </div>
      </td>
      <td class="num">${fmtNum(mql)}</td>
      <td class="num">${fmtNum(opps)}</td>
      <td class="num">${mqlOpp == null ? '—' : fmtPct(mqlOpp)}</td>
      <td class="num">${fmtNum(won)}</td>
      <td class="num">${oppWon == null ? '—' : fmtPct(oppWon)}</td>
      <td class="num"><strong>${mqlWon == null ? '—' : fmtPct(mqlWon)}</strong></td>
    `;
    tr.addEventListener('click', () => setView('rep', teamName, r.name));
    tbody.appendChild(tr);
  });

  const totMqlOpp = tMql ? (tOpp / tMql * 100) : 0;
  const totOppWon = tOpp ? (tWon / tOpp * 100) : 0;
  const totMqlWon = tMql ? (tWon / tMql * 100) : 0;
  document.getElementById('rep-view-funnel-tfoot').innerHTML = `
    <td class="col-rep"><strong>Team Total</strong></td>
    <td class="num"><strong>${fmtNum(tMql)}</strong></td>
    <td class="num"><strong>${fmtNum(tOpp)}</strong></td>
    <td class="num"><strong>${fmtPct(totMqlOpp)}</strong></td>
    <td class="num"><strong>${fmtNum(tWon)}</strong></td>
    <td class="num"><strong>${fmtPct(totOppWon)}</strong></td>
    <td class="num"><strong>${fmtPct(totMqlWon)}</strong></td>
  `;
}

function renderRepRolling90(teamName, rep) {
  const panel = document.getElementById('rep-rolling-panel');
  const r90 = rep.rolling_90 || {};
  if (!r90 || (!r90.mql && !r90.opps && !r90.won)) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';
  const sub = (dashboardData.kpis && dashboardData.kpis.rolling_window_label) || '';
  document.getElementById('rep-rolling-sub').textContent =
    `Past 90 days · ${sub}`;

  const tbody = document.getElementById('rep-rolling-tbody');
  tbody.innerHTML = `
    <tr>
      <td>
        <div class="row-rep-cell">
          <span class="row-avatar avatar-${teamName}">${repInitials(rep.name)}</span>
          <span class="row-rep-name">${escapeHtml(rep.name)}</span>
        </div>
      </td>
      <td class="num">${fmtNum(r90.mql || 0)}</td>
      <td class="num">${fmtNum(r90.opps || 0)}</td>
      <td class="num"><span class="pct-pill ${attainmentClass(r90.mql_to_opp || 0)}">${fmtPct(r90.mql_to_opp || 0)}</span></td>
      <td class="num">${fmtNum(r90.won || 0)}</td>
      <td class="num"><span class="pct-pill ${attainmentClass(r90.opp_to_won || 0)}">${fmtPct(r90.opp_to_won || 0)}</span></td>
      <td class="num"><span class="pct-pill ${attainmentClass(r90.mql_to_won || 0)}">${fmtPct(r90.mql_to_won || 0)}</span></td>
    </tr>
  `;
}

function renderRepTrends(rep) {
  const panel = document.getElementById('rep-trends-panel');
  const months = rep.trend_months || [];
  const rev = rep.trend_revenue || [];
  const mql = rep.trend_mql || [];
  const revGoals = rep.trend_revenue_goals || [];
  if (!months.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  // For revenue, scale bars relative to the max of actuals AND goals so the
  // goal marker fits on screen.
  const maxRev = Math.max(1, ...rev, ...revGoals);
  const maxMql = Math.max(1, ...mql);

  // Revenue bars include an optional goal marker + achieved % when a goal
  // is configured for the rep for that month.
  const revHtml = rev.map((v, i) => {
    const goal = revGoals[i] || 0;
    const pct = (v / maxRev) * 100;
    const goalPct = goal ? (goal / maxRev) * 100 : 0;
    const achieved = goal ? (v / goal * 100) : null;
    const achievedClass = achieved == null ? '' :
      (achieved >= 80 ? 'good' : achieved >= 50 ? 'mid' : 'low');
    return `
      <div class="trend-row">
        <div class="trend-row-label">${escapeHtml(months[i])}</div>
        <div class="trend-row-bar">
          <div class="trend-row-fill fill-revenue" style="width:${pct.toFixed(1)}%"></div>
          ${goal ? `<div class="trend-goal-marker" style="left:${goalPct.toFixed(1)}%" title="Goal: ${fmtUSD(goal)}"></div>` : ''}
        </div>
        <div class="trend-row-value">
          ${fmtUSD(v)}
          ${goal ? `<div class="trend-goal-meta">Goal ${fmtUSD(goal)} · <span class="trend-achv ${achievedClass}">${fmtPct(achieved)}</span></div>` : ''}
        </div>
      </div>
    `;
  }).join('');

  const mqlHtml = mql.map((v, i) => {
    const pct = (v / maxMql) * 100;
    return `
      <div class="trend-row">
        <div class="trend-row-label">${escapeHtml(months[i])}</div>
        <div class="trend-row-bar">
          <div class="trend-row-fill fill-mql" style="width:${pct.toFixed(1)}%"></div>
        </div>
        <div class="trend-row-value">${fmtNum(v)}</div>
      </div>
    `;
  }).join('');

  document.getElementById('rep-trend-revenue').innerHTML = revHtml;
  document.getElementById('rep-trend-mql').innerHTML = mqlHtml;
}

function renderRepDiscount(rep) {
  const panel = document.getElementById('rep-discount-panel');
  const ds = rep.discount_summary || {};
  const deals = ds.deals || [];
  if (!deals.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const tbody = document.getElementById('rep-discount-tbody');
  tbody.innerHTML = '';
  deals.forEach(d => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(d.name)}</td>
      <td class="num">${fmtUSD(d.total_amount)}</td>
      <td class="num">${fmtUSD(d.discount_amount)}</td>
      <td class="num">${fmtUSD(d.after_discount_amount)}</td>
      <td class="num">${fmtPct(d.discount_pct || 0)}</td>
    `;
    tbody.appendChild(tr);
  });
  document.getElementById('rep-discount-tfoot').innerHTML = `
    <td><strong>Total</strong></td>
    <td class="num"><strong>${fmtUSD(ds.total_amount || 0)}</strong></td>
    <td class="num"><strong>${fmtUSD(ds.discount_amount || 0)}</strong></td>
    <td class="num"><strong>${fmtUSD(ds.after_discount_amount || 0)}</strong></td>
    <td class="num"></td>
  `;
  document.getElementById('rep-discount-avg').innerHTML =
    `<strong>Avg. Discount Rate:</strong> ${fmtPct(ds.avg_discount_rate || 0)}`;
}

function renderRepDealsTable(rep) {
  const headingMap = {
    closed_won_deals: 'Closed Won (This Month)',
    open_pipeline_deals: 'Open Pipeline',
    deals_lost_deals: 'Deals Lost (This Month)',
  };
  const list = rep[repDealsTab] || [];
  document.getElementById('rep-deals-heading').textContent = headingMap[repDealsTab];

  // Build header row dynamically — Lost Reason only on the Deals Lost tab.
  const showLostReason = (repDealsTab === 'deals_lost_deals');
  const thead = document.getElementById('rep-deals-thead');
  thead.innerHTML = `
    <th class="col-deal">Deal Name</th>
    <th class="num col-amount">Amount (USD)</th>
    <th class="col-stage">Stage</th>
    <th>Country</th>
    <th class="col-date">Create Date</th>
    <th class="col-date">Close Date</th>
    <th class="num col-age">Age (Days)</th>
    ${showLostReason ? '<th>Lost Reason</th>' : ''}
  `;

  const total = list.reduce((s, d) => s + (d.amount || 0), 0);
  const summaryEl = document.getElementById('rep-deals-summary');
  if (list.length) {
    summaryEl.textContent = `${list.length} deal${list.length === 1 ? '' : 's'} · ${fmtUSD(total)} total`;
  } else {
    summaryEl.textContent = 'No deals in this category for the current month';
  }

  const tbody = document.querySelector('#rep-deals-table tbody');
  tbody.innerHTML = '';
  const colCount = showLostReason ? 8 : 7;
  if (!list.length) {
    tbody.innerHTML = `
      <tr><td colspan="${colCount}" class="empty-row">
        <div class="empty-state">
          <div class="empty-icon">∅</div>
          <div>No deals to show.</div>
        </div>
      </td></tr>`;
    return;
  }
  list.forEach(d => {
    const ageDays = (d.age_days != null && d.age_days !== undefined)
      ? d.age_days
      : (d.create_date && d.close_date
          ? Math.round((new Date(d.close_date) - new Date(d.create_date)) / 86400000)
          : '-');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="col-deal" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</td>
      <td class="num col-amount">${fmtUSD(d.amount)}</td>
      <td class="col-stage"><span class="stage-tag">${escapeHtml(d.stage || '-')}</span></td>
      <td>${escapeHtml(d.country || '-')}</td>
      <td class="col-date">${d.create_date || '-'}</td>
      <td class="col-date">${d.close_date || '-'}</td>
      <td class="num col-age">${ageDays}</td>
      ${showLostReason ? `<td title="${escapeHtml(d.lost_reason || '')}">${escapeHtml(d.lost_reason || '-')}</td>` : ''}
    `;
    tbody.appendChild(tr);
  });
}

// ----- Generic deal drilldown modal ----------------------------------------

function getDealsForScope(type, scope) {
  const sourceKey = DRILLDOWN_SOURCES[type];
  if (!sourceKey) return [];
  if (scope.rep) {
    return (dashboardData.teams[scope.team]?.reps[scope.rep]?.[sourceKey]) || [];
  }
  if (scope.team) {
    return dashboardData.teams[scope.team]?.[sourceKey] || [];
  }
  return dashboardData[sourceKey] || [];
}

function openDealsDrilldown(type, scope = {}, ageVariant = false) {
  drilldownState = { type, scope, ageVariant };
  drilldownSort = ageVariant
    ? { key: 'age_days', dir: 'desc' }
    : { key: 'amount', dir: 'desc' };
  document.getElementById('drilldown-search').value = '';

  const teamFilter = document.getElementById('drilldown-team-filter');
  if (scope.team) {
    teamFilter.value = scope.team;
    teamFilter.disabled = true;
  } else {
    teamFilter.value = '';
    teamFilter.disabled = false;
  }
  renderDrilldown();
  document.getElementById('drilldown-modal').style.display = 'flex';
}
function closeDrilldown() {
  document.getElementById('drilldown-modal').style.display = 'none';
}

function renderDrilldown() {
  const { type, scope, ageVariant } = drilldownState;
  if (!type) return;

  const columns = DRILLDOWN_COLUMNS[type] || [];
  let baseTitle = ageVariant
    ? 'Closed Won Deals — Avg Deal Age (This Month)'
    : (DRILLDOWN_TITLES[type] || 'Deals');
  if (scope.rep) baseTitle += ` · ${scope.rep}`;
  else if (scope.team) baseTitle += ` · ${scope.team} Team`;
  document.getElementById('drilldown-title').textContent = baseTitle;

  // Render header row dynamically
  const headRow = document.getElementById('drilldown-thead-row');
  headRow.innerHTML = '';
  columns.forEach(col => {
    const th = document.createElement('th');
    th.dataset.sort = col.key;
    th.textContent = col.label;
    if (col.num) th.classList.add('num');
    th.addEventListener('click', () => {
      if (drilldownSort.key === col.key) {
        drilldownSort.dir = drilldownSort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        drilldownSort = { key: col.key, dir: col.num ? 'desc' : 'asc' };
      }
      renderDrilldown();
    });
    headRow.appendChild(th);
  });

  let all = getDealsForScope(type, scope);
  const search = document.getElementById('drilldown-search').value.toLowerCase().trim();
  const teamFilter = document.getElementById('drilldown-team-filter').value;

  let rows = all.filter(d => {
    if (teamFilter && d.team !== teamFilter) return false;
    if (search) {
      const hay = `${d.name} ${d.owner} ${d.country || ''} ${d.stage || ''}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  const { key, dir } = drilldownSort;
  rows.sort((a, b) => {
    let av = a[key], bv = b[key];
    if (av == null) av = '';
    if (bv == null) bv = '';
    if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv || '').toString().toLowerCase(); }
    if (av < bv) return dir === 'asc' ? -1 : 1;
    if (av > bv) return dir === 'asc' ? 1 : -1;
    return 0;
  });

  const totalAmount = rows.reduce((s, r) => s + (Number(r.amount) || 0), 0);
  const summaryParts = [`${rows.length} deal${rows.length === 1 ? '' : 's'}`,
                       `Total ${fmtUSD(totalAmount)}`];
  if (type === 'closed_won' || ageVariant) {
    const ages = rows.filter(r => r.age_days != null).map(r => r.age_days);
    if (ages.length) {
      const avg = ages.reduce((s, a) => s + a, 0) / ages.length;
      summaryParts.push(`Avg age ${avg.toFixed(1)} days`);
    }
  }
  document.getElementById('drilldown-summary').textContent = summaryParts.join('  •  ');

  const tbody = document.querySelector('#drilldown-table tbody');
  tbody.innerHTML = '';
  if (!rows.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="${columns.length}" class="empty-row">
        <div class="empty-state"><div class="empty-icon">∅</div><div>No deals to show.</div></div>
      </td>`;
    tbody.appendChild(tr);
  } else {
    const isLost = type === 'deals_lost';
    rows.forEach(d => {
      const tr = document.createElement('tr');
      if (isLost) {
        tr.classList.add('clickable-row');
        tr.title = 'Click to view activity insights & why this deal was lost';
        tr.addEventListener('click', () => openDealInsights(d));
      }
      columns.forEach(col => {
        const td = document.createElement('td');
        if (col.num) td.classList.add('num');
        let raw = d[col.key];
        let display;
        if (col.fmt === 'usd') display = fmtUSD(raw);
        else if (col.fmt === 'team') {
          const t = raw || '-';
          display = `<span class="team-tag ${t}">${escapeHtml(t)}</span>`;
        }
        else if (raw == null || raw === '') display = '-';
        else display = escapeHtml(raw);
        td.innerHTML = display;
        if (col.key === 'name') {
          td.title = String(d.name || '');
          if (isLost) {
            td.innerHTML = `<span class="link-cell">${escapeHtml(d.name)} <span class="link-arrow">↗</span></span>`;
          }
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  document.querySelectorAll('#drilldown-table th').forEach(th => {
    th.classList.remove('sorted-asc', 'sorted-desc');
    if (th.dataset.sort === key) {
      th.classList.add(dir === 'asc' ? 'sorted-asc' : 'sorted-desc');
    }
  });
}

// ----- Deal Insights modal --------------------------------------------------

const TYPE_ICONS = {
  note: '📝', call: '📞', email: '✉', meeting: '🤝', task: '✓',
};
const TYPE_LABELS = {
  note: 'Note', call: 'Call', email: 'Email', meeting: 'Meeting', task: 'Task',
};

function fmtTimestamp(iso) {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    return d.toLocaleString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
  } catch (e) { return iso; }
}
function fmtDuration(ms) {
  if (!ms) return null;
  const n = Number(ms);
  if (!n) return null;
  const sec = Math.round(n / 1000);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

async function openDealInsights(deal) {
  const modal = document.getElementById('insights-modal');
  modal.style.display = 'flex';
  document.getElementById('insights-title').textContent = deal.name || 'Deal Insights';
  document.getElementById('insights-sub').textContent =
    `${fmtUSD(deal.amount)} · Owner: ${deal.owner || '-'} · Closed: ${deal.close_date || '-'}`;
  document.getElementById('insights-body').innerHTML = `
    <div class="insights-loading">
      <div class="spinner"></div>
      <p>Loading activity from HubSpot…</p>
    </div>`;

  try {
    const res = await fetch(`/api/deal/${encodeURIComponent(deal.id)}/insights`);
    const json = await res.json();
    if (!json.success) throw new Error(json.error || 'Failed to load insights');
    renderDealInsights(deal, json.data);
  } catch (e) {
    document.getElementById('insights-body').innerHTML = `
      <div class="error">Failed to load activity: ${escapeHtml(e.message)}</div>`;
  }
}

function closeInsights() {
  document.getElementById('insights-modal').style.display = 'none';
}

function renderDealInsights(deal, data) {
  const body = document.getElementById('insights-body');
  const counts = data.engagement_counts || {};
  const total = Object.values(counts).reduce((s, n) => s + (n || 0), 0);

  const lostReasonHTML = data.closed_lost_reason
    ? `<div class="lost-reason">
         <div class="lost-reason-label">Closed Lost Reason</div>
         <div class="lost-reason-value">${escapeHtml(data.closed_lost_reason)}</div>
       </div>`
    : `<div class="lost-reason muted">
         <div class="lost-reason-label">Closed Lost Reason</div>
         <div class="lost-reason-value">Not specified in HubSpot</div>
       </div>`;

  const inacc = data.inaccessible_counts || {};
  const inaccPairs = Object.entries(inacc).filter(([, n]) => n > 0);
  const inaccessibleHTML = inaccPairs.length
    ? `<div class="info-banner">
         <strong>Note:</strong> ${inaccPairs.map(([k, n]) => `${n} ${k}`).join(', ')}
         on record but content not accessible — the HubSpot private-app token is missing
         <code>sales-email-read</code> (or related) scope. Counts are still included in the totals above.
       </div>`
    : '';

  const factsHTML = `
    <div class="insights-facts">
      <div><span class="fact-label">Country</span><span class="fact-value">${escapeHtml(deal.country || '-')}</span></div>
      <div><span class="fact-label">Stage</span><span class="fact-value">${escapeHtml(deal.stage || '-')}</span></div>
      <div><span class="fact-label">Team</span><span class="fact-value">${escapeHtml(deal.team || '-')}</span></div>
      <div><span class="fact-label">Create Date</span><span class="fact-value">${escapeHtml(deal.create_date || '-')}</span></div>
      <div><span class="fact-label">Close Date</span><span class="fact-value">${escapeHtml(deal.close_date || '-')}</span></div>
      <div><span class="fact-label">Engagements</span><span class="fact-value">
        ${total} total
        ${counts.notes ? ` · ${counts.notes} note${counts.notes === 1 ? '' : 's'}` : ''}
        ${counts.calls ? ` · ${counts.calls} call${counts.calls === 1 ? '' : 's'}` : ''}
        ${counts.emails ? ` · ${counts.emails} email${counts.emails === 1 ? '' : 's'}` : ''}
        ${counts.meetings ? ` · ${counts.meetings} meeting${counts.meetings === 1 ? '' : 's'}` : ''}
        ${counts.tasks ? ` · ${counts.tasks} task${counts.tasks === 1 ? '' : 's'}` : ''}
      </span></div>
    </div>`;

  const tl = (data.timeline || []);
  let timelineHTML = '';
  if (!tl.length) {
    timelineHTML = `
      <div class="empty-state">
        <div class="empty-icon">∅</div>
        <div>No activity recorded against this deal in HubSpot.</div>
      </div>`;
  } else {
    timelineHTML = '<div class="timeline">' + tl.map(it => {
      const icon = TYPE_ICONS[it.type] || '•';
      const label = TYPE_LABELS[it.type] || it.type;
      const metaPills = [];
      if (it.meta) {
        if (it.meta.direction) metaPills.push(escapeHtml(String(it.meta.direction).toLowerCase()));
        if (it.meta.disposition) metaPills.push('disposition: ' + escapeHtml(String(it.meta.disposition)));
        if (it.meta.outcome) metaPills.push('outcome: ' + escapeHtml(String(it.meta.outcome).toLowerCase()));
        if (it.meta.status) metaPills.push('status: ' + escapeHtml(String(it.meta.status).toLowerCase()));
        const dur = fmtDuration(it.meta.duration_ms);
        if (dur) metaPills.push(dur);
      }
      const pillHTML = metaPills.length
        ? `<div class="tl-pills">${metaPills.map(p => `<span class="tl-pill">${p}</span>`).join('')}</div>`
        : '';
      const bodyHTML = it.body
        ? `<div class="tl-body">${escapeHtml(it.body)}${it.body_truncated ? ' …' : ''}</div>`
        : '';
      return `
        <div class="tl-item tl-${it.type}">
          <div class="tl-marker"><span class="tl-icon">${icon}</span></div>
          <div class="tl-content">
            <div class="tl-head">
              <span class="tl-type">${label}</span>
              <span class="tl-time">${escapeHtml(fmtTimestamp(it.timestamp))}</span>
            </div>
            <div class="tl-title">${escapeHtml(it.title || label)}</div>
            ${pillHTML}
            ${bodyHTML}
          </div>
        </div>`;
    }).join('') + '</div>';
  }

  body.innerHTML = `
    ${lostReasonHTML}
    ${factsHTML}
    ${inaccessibleHTML}
    <h3 class="insights-section-title">Activity Timeline</h3>
    ${timelineHTML}
  `;
}

document.getElementById('close-insights').addEventListener('click', closeInsights);
document.getElementById('insights-modal').addEventListener('click', (e) => {
  if (e.target.id === 'insights-modal') closeInsights();
});

// ----- Wire up events -------------------------------------------------------

document.getElementById('close-modal').addEventListener('click', closeDrilldown);
document.getElementById('drilldown-modal').addEventListener('click', (e) => {
  if (e.target.id === 'drilldown-modal') closeDrilldown();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeDrilldown();
});
document.getElementById('refresh-btn').addEventListener('click', () => loadData(true));
document.getElementById('month-select').addEventListener('change', (e) => {
  selectedMonth = e.target.value || '';
  localStorage.setItem('mbr.selectedMonth', selectedMonth);
  // Reset to dashboard view since rep/team context may have no data this month
  viewState = { view: 'dashboard', team: null, rep: null };
  // Close any open modal
  document.getElementById('drilldown-modal').style.display = 'none';
  document.getElementById('insights-modal').style.display = 'none';
  loadData(false);
});
document.getElementById('drilldown-search').addEventListener('input', renderDrilldown);
document.getElementById('drilldown-team-filter').addEventListener('change', renderDrilldown);

document.querySelectorAll('#rep-deal-tabs .tab').forEach(b => {
  b.addEventListener('click', () => {
    repDealsTab = b.dataset.tab;
    document.querySelectorAll('#rep-deal-tabs .tab').forEach(x => {
      x.classList.toggle('active', x === b);
    });
    const t = dashboardData.teams[viewState.team];
    const rep = t?.reps[viewState.rep];
    if (rep) renderRepDealsTable(rep);
  });
});

// Load month options first, then initial data
loadMonthsList().then(() => loadData());
