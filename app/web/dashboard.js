'use strict';
const $ = id => document.getElementById(id);
const number = value => value == null ? '—' : new Intl.NumberFormat('ko-KR', {maximumFractionDigits: 2}).format(value);
let overview = null, historyCursor = null, historyLoaded = false, selectedDay = null, pollTimer = null;
let historyRequest = 0, pollCount = 0;
function notice(message) { $('notice').textContent = message; }
async function api(path, options = {}) {
  const response = await fetch('/stock-briefing/api' + path, {
    ...options, credentials: 'same-origin', cache: 'no-store',
    headers: {'X-Dashboard-Request': '1', ...(options.body ? {'Content-Type': 'application/json'} : {})},
    signal: AbortSignal.timeout(45000)
  });
  if (response.status === 401) { window.location.replace('/stock-briefing/login'); throw new Error('로그인이 필요합니다.'); }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(response.status === 401 ? '인증이 만료되었습니다. 페이지를 다시 열어 로그인하세요.' :
      typeof error.detail === 'string' ? error.detail : '요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.');
  }
  return response.json();
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function showView(view) {
  const titles = {home: '오늘의 시장을 한눈에.', watch: '관심을 기록하고, 흐름을 읽다.', history: '지난 시장의 흐름을 다시.'};
  if (!(view in titles)) view = 'home';
  document.querySelectorAll('.view').forEach(node => { node.hidden = node.id !== view; });
  document.querySelectorAll('nav [data-view]').forEach(node => {
    if (node.dataset.view === view) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  });
  $('page-title').textContent = titles[view];
  if (view === 'history' && !historyLoaded) loadHistory().catch(error => notice(error.message));
}
function renderWatch() {
  const root = $('watch-list'); root.replaceChildren();
  if (!overview.watchlist.length) {
    root.append(element('p', '아직 관심종목이 없습니다. 첫 종목을 추가해 보세요.', 'empty')); return;
  }
  for (const record of overview.watchlist) {
    const card = element('article', null, 'card stock-card');
    const head = element('div', null, 'stock-head'), label = element('div', null, 'stock-label');
    const name = element('h2', record.name); name.title = record.name;
    label.append(name, element('small', record.symbol));
    const action = {'매수': '매수 검토', '매도': '매도 검토'}[record.action] || record.action;
    let opinion = element('span', action, 'pill');
    if (record.reason) {
      opinion = element('details', null, 'stock-opinion');
      const summary = element('summary', action, 'pill');
      summary.setAttribute('aria-label', action + ' · 분석 사유 보기');
      opinion.append(summary, element('p', record.reason, 'stock-reason'));
    }
    head.append(label, opinion);
    const quote = element('div', null, 'stock-quote');
    quote.append(element('span', record.close == null ? '분석 대기' : number(record.close) + '원', 'price'));
    if (record.daily_return != null) {
      const change = element('span', (record.daily_return > 0 ? '+' : '') + (record.daily_return * 100).toFixed(1) + '%', record.daily_return >= 0 ? 'up' : 'down');
      change.setAttribute('aria-label', '전일 대비 ' + change.textContent);
      quote.append(change);
    }
    const basis = element('span', record.day ? record.day.slice(5) + ' 종가' : '기준일 없음', 'stock-basis');
    basis.title = record.day ? record.day + ' 종가' : '갱신 후 의견 표시';
    basis.setAttribute('aria-label', basis.title);
    quote.append(basis);
    const remove = element('button', '제거', 'remove'); remove.type = 'button';
    remove.setAttribute('aria-label', record.name + ' 관심종목에서 제거');
    remove.addEventListener('click', async () => {
      if (!confirm(record.name + '을(를) 관심종목에서 제거할까요? 과거 기록은 유지됩니다.')) return;
      remove.disabled = true;
      try { const result = await api('/watchlist/' + encodeURIComponent(record.symbol), {method: 'DELETE'}); await loadOverview(); notice(result.message); }
      catch (error) { notice(error.message); }
      finally { remove.disabled = false; }
    });
    head.append(remove); card.append(head, quote); root.append(card);
  }
}
function schedulePoll() {
  clearTimeout(pollTimer);
  const busy = overview?.job && ['queued', 'running'].includes(overview.job.status);
  $('refresh').disabled = !!busy;
  $('refresh').textContent = busy ? '갱신 진행 중…' : '브리핑 갱신 ↗';
  if (busy && pollCount < 720) pollTimer = setTimeout(async () => {
    pollCount++;
    try { await loadOverview(); }
    catch (error) { notice(error.message); $('refresh').disabled = false; }
  }, 10000);
  else if (busy) notice('갱신이 오래 걸리고 있습니다. 페이지를 다시 열어 상태를 확인하세요.');
}
async function loadOverview() {
  overview = await api('/overview');
  $('watch-count').replaceChildren(document.createTextNode(String(overview.watchlist.length)), element('small', '종목'));
  $('latest-day').textContent = overview.latest?.day || '기록 없음';
  $('briefing').textContent = overview.latest?.body || '아직 저장된 브리핑이 없습니다. 첫 브리핑을 갱신해 보세요.';
  const statuses = {queued: '갱신 대기 중', running: '브리핑 분석 중', done: '최근 갱신 완료', failed: '최근 갱신 실패 · 서버 로그를 확인하세요'};
  $('job-state').textContent = statuses[overview.job?.status] || '갱신 요청 이력 없음';
  const m = overview.market;
  $('kospi').textContent = m ? number(m.close) : '—';
  $('phase').textContent = m?.phase || '분석 대기';
  $('market-basis').textContent = m ? m.day + ' 종가 · 저장된 코스피 분석' : '브리핑 생성 후 코스피 분석을 표시합니다.';
  $('market-detail').textContent = m ? '20일선 ' + number(m.ma20) + ' / 60일선 ' + number(m.ma60) + ' · K ' + number(m.k) + ' / D ' + number(m.d) : '';
  renderWatch(); schedulePoll();
}
async function chooseHistory(day, button) {
  const request = ++historyRequest; selectedDay = day;
  $('history-title').textContent = day + ' 브리핑'; $('history-body').textContent = '불러오는 중…';
  document.querySelectorAll('.history-item').forEach(node => node.removeAttribute('aria-current'));
  button.setAttribute('aria-current', 'true');
  try { const result = await api('/history/' + day); if (request === historyRequest) $('history-body').textContent = result.body; }
  catch (error) { if (request === historyRequest) { $('history-body').textContent = '브리핑을 불러오지 못했습니다.'; notice(error.message); } }
}
async function loadHistory(append = false) {
  $('older').disabled = true;
  try {
    const result = await api('/history' + (append && historyCursor ? '?before=' + historyCursor : ''));
    if (!append) $('history-list').replaceChildren();
    for (const item of result.items) {
      const button = element('button', item.day, 'history-item'); button.type = 'button';
      button.append(element('small', '전체 브리핑 보기 →'));
      if (item.day === selectedDay) button.setAttribute('aria-current', 'true');
      button.addEventListener('click', () => chooseHistory(item.day, button));
      $('history-list').append(button);
    }
    if (!append && !result.items.length) $('history-list').append(element('p', '저장된 기록이 없습니다.', 'muted'));
    historyCursor = result.next; $('older').hidden = !historyCursor; historyLoaded = true;
  } finally { $('older').disabled = false; }
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
$('older').addEventListener('click', () => loadHistory(true).catch(error => notice(error.message)));
$('refresh').addEventListener('click', async () => {
  $('refresh').disabled = true;
  try { const result = await api('/refresh', {method: 'POST'}); pollCount = 0; historyLoaded = false; notice(result.message + ' 완료까지 이 화면에서 기다릴 수 있습니다.'); await loadOverview(); }
  catch (error) { notice(error.message); $('refresh').disabled = false; }
});
$('watch-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button');
  const query = $('query').value.trim(); if (!query) return;
  button.disabled = true; notice('종목을 확인하고 있습니다…');
  try { const result = await api('/watchlist', {method: 'POST', body: JSON.stringify({query})}); $('query').value = ''; await loadOverview(); notice(result.name + ' 추가 완료. 브리핑 갱신 후 최신 의견을 볼 수 있습니다.'); }
  catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});
$('today').textContent = new Intl.DateTimeFormat('ko-KR', {timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long'}).format(new Date()) + ' · DAILY NOTE';
loadOverview().then(() => notice('저장된 최신 분석입니다. 새 분석은 브리핑 갱신으로 요청하세요.')).catch(error => notice(error.message));

$('logout').addEventListener('click', async () => {
  try {
    const response = await fetch('/stock-briefing/api/logout', {method: 'POST', credentials: 'same-origin', headers: {'X-Dashboard-Request': '1'}});
    if (!response.ok) throw new Error('로그아웃하지 못했습니다. 다시 시도하세요.');
    window.location.replace('/stock-briefing/login');
  } catch (error) { notice(error.message); }
});
