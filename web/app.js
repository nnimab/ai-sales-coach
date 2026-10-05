'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const normKey = (s) => String(s || '').replace(/[\s,.!?;:'"、，。！？；：「」『』（）()~～…\-—]/g, '');

const SILENCE_MS = 1500;
const BOUNDARY = '。！？!?…～~\n';
const HISTORY_KEY = 'aicoach.history.v1';

const state = {
  scenarios: [],
  scenario: null,
  messages: [],
  mode: 'practice',
  checklist: {},
  trust: 45,
  alerts: [],
  startedAt: 0,
  endedAt: 0,
  timerId: 0,
  chatCtrl: null,
  partial: null,
  ended: true,
  providerReady: false,
  providerLabel: '',
  statusSig: '',
  report: null,
  // 教練：雲端模型可以同時跑好幾輪，每輪對應業務的第幾句話
  coachParallel: false,
  repTurn: 0,
  coachBusy: 0,
  coachCtrls: new Set(), // 還在跑的教練，每個記著看的是第 from～turn 句
  coach: newCoach(),
  evalPending: false, // 即時對話已掛斷、評分還沒成功：再按「結束並評分」只重新評分
  evalBusy: false,
  hitWords: new Set(), // 講話中已經跳過提醒的禁語，定案時不再重複跳
  rep: null, // 業務自己的背景（稱呼、職稱），由伺服器算好
  // 伺服器狀態：舊版伺服器沒有的欄位都當作本機
  status: null,
  hosted: !['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname),
  provider: '',
  active: '',
  canAdmin: true,
  fallbackNoted: false, // 這通電話已經提醒過「改用備援」
};

// ------------------------------------------------------------------ 圖示（線條風格）

const PHONE = '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>';
const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>',
  history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
  phone: PHONE,
  phoneIn: `${PHONE}<path d="M16 2v6h6"/><path d="m22 2-6 6"/>`,
  phoneOut: `${PHONE}<path d="M22 8V2h-6"/><path d="m16 8 6-6"/>`,
  phoneOff: '<path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.42 19.42 0 0 1-3.33-2.67m-2.67-3.34a19.79 19.79 0 0 1-3.07-8.63A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91"/><path d="M22 2 2 22"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
  micOff: '<path d="M2 2l20 20"/><path d="M18.89 13.23A7 7 0 0 0 19 12v-2"/><path d="M5 10v2a7 7 0 0 0 12 5"/><path d="M15 9.34V5a3 3 0 0 0-5.68-1.33"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12"/><path d="M12 19v3"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  back: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
  list: '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  printer: '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
  retry: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  headphones: '<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>',
  trend: '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
  play: '<path d="M6 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L7.5 3.64A1 1 0 0 0 6 4.5z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  volume: '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>',
  edit: '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
};

function icon(name) {
  return `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}

function hydrateIcons(root = document) {
  $$('[data-icon]', root).forEach((el) => { el.outerHTML = icon(el.dataset.icon); });
}

// ------------------------------------------------------------------ 共用

const offline = () => (state.hosted
  ? '連不到雲端網站，請檢查網路後重新整理'
  : '連不到對練程式，請確認黑色視窗還開著，或重新執行 start.bat');
const httpError = (status) => `伺服器回應錯誤（代碼 ${status}）`;
const ADMIN_ONLY = '需要管理者通行碼：請登出後用管理者通行碼登入';

// 雲端登入過期或還沒設定好：回登入頁（登入頁會列出缺什麼）
let leaving = false;
function toLogin() {
  if (leaving) return;
  leaving = true;
  location.href = '/login';
}

// 連不到伺服器時，瀏覽器只會給英文的 Failed to fetch：換成看得懂的中文；自己取消的照原樣丟出
async function request(path, opts) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    if (e.name === 'AbortError' || e.name === 'TimeoutError') throw e;
    throw new Error(offline());
  }
  if (res.status === 401 || res.status === 503) {
    const d = await res.clone().json().catch(() => ({}));
    if (d.login || d.setup) {
      toLogin();
      throw new Error('請先登入');
    }
  }
  return res;
}

async function api(path, body, signal) {
  const opts = body === undefined
    ? { signal }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal };
  const res = await request(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // 保留伺服器回的欄位（例如 field），編輯器才知道要標紅哪一格；需要管理者的由呼叫端照常跳通知
    const e = new Error(res.status === 403 && data.admin ? ADMIN_ONLY : data.error || httpError(res.status));
    e.data = data;
    throw e;
  }
  return data;
}

// 只接受 ChatGPT 自己的網址，伺服器給的值怪怪的就用預設
function usageUrl() {
  const u = state.status?.chatgpt?.manage_usage_url;
  return typeof u === 'string' && /^https:\/\/chatgpt\.com\//.test(u) ? u : 'https://chatgpt.com/settings/usage';
}

// text 可含 HTML：呼叫端要自行跳脫使用者或 AI 產生的內容
function toast(text, kind = 'info', ms = 3600, title = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.setAttribute('role', kind === 'danger' || kind === 'error' ? 'alert' : 'status');
  el.innerHTML = `${icon(kind === 'info' ? 'info' : 'alert')}<div>${title ? `<b>${esc(title)}</b>` : ''}${text}</div>`;
  $('#toasts').appendChild(el);
  setTimeout(() => el.classList.add('out'), ms);
  setTimeout(() => el.remove(), ms + 250);
}

function animateNumber(el, from, to, ms) {
  if (!ms) { el.textContent = to; return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / ms);
    el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function fmtDuration(secs) {
  return `${Math.floor(secs / 60)} 分 ${secs % 60} 秒`;
}

function fmtDate(ts) {
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// ------------------------------------------------------------------ 頁面切換

function show(name) {
  // 離開編輯器就清掉表單：AI 草稿裡的隱藏設定不會留到通話或報告畫面
  if (name !== 'editor') ScenarioAdmin.close();
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  $('#app').classList.toggle('in-call', name === 'call');
  $('.main').scrollTop = 0;
}

function setNav(name) {
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.nav === name));
}

// 正在看的表單有沒有改到一半
function formDirty() {
  if ($('#view-rep').classList.contains('active') && Rep.dirty) return true;
  return $('#view-editor').classList.contains('active') && ScenarioAdmin.dirty;
}

function leaveFormIfNeeded() {
  return !formDirty() || window.confirm('還沒儲存，要放棄這次的修改嗎？');
}

function leaveCallIfNeeded() {
  if (!leaveFormIfNeeded()) return false;
  if (state.ended || !$('#view-call').classList.contains('active')) return true;
  if (state.messages.some((m) => m.role === 'rep') && !window.confirm('要放棄這次對練嗎？')) return false;
  stopSession();
  return true;
}

function go(name) {
  if (!leaveCallIfNeeded()) return;
  if (name === 'history') renderHistory();
  if (name === 'rep') Rep.render();
  if (name === 'scenarios') ScenarioAdmin.renderList();
  if (name === 'voices') renderVoices();
  else stopPreview();
  if (name === 'brain') Brain.render(true);
  show(name);
  setNav(name);
}

window.addEventListener('beforeunload', (e) => {
  if (!formDirty()) return;
  e.preventDefault();
  e.returnValue = '';
});

// ------------------------------------------------------------------ AI 大腦狀態

const shortLabel = (label) => String(label || '').replace(/（.*$/, '').trim();
// 名字代進句子後，中文和中文之間不留空白（「改用 本機模型」→「改用本機模型」）；英文前後的空白保留
const tidy = (s) => String(s).replace(CJK_GAP, '$1'); // CJK_GAP 在下面「即時對話」那段

// ChatGPT 不能用時接手的大腦：照設定好的備援順序，沒有就籠統說
function backupName() {
  const ok = (id) => (state.status?.providers || []).some((p) => p.id === id && p.configured);
  if (ok('gemini')) return 'Gemini';
  return ok('ollama') ? '本機模型' : '其他大腦';
}

function brainName(id) {
  if (id === 'chatgpt') return 'ChatGPT';
  const p = (state.status?.providers || []).find((x) => x.id === id);
  return shortLabel(p?.label) || id;
}

// 沒設定好的選項不能選；ChatGPT 要先登入（雲端版是從本機同步上去）
function providerOptions(s) {
  const why = (p) => (p.id !== 'chatgpt' ? '（未設定）' : s.chatgpt?.mode === 'synced' ? '（未同步）' : '（未登入）');
  return (s.providers || []).map((p) =>
    `<option value="${esc(p.id)}"${p.id === s.provider ? ' selected' : ''}${p.configured ? '' : ' disabled'}>${esc(p.label)}${p.configured ? '' : why(p)}</option>`).join('');
}

// 雲端版：資料存在雲端資料庫，不是這台電腦的檔案
function hostedCopy() {
  const kind = state.status?.storage?.kind;
  const cloud = state.hosted || (!!kind && kind !== 'file');
  $('#historyNote').textContent = cloud ? '紀錄只存在這個瀏覽器裡。' : '紀錄只存在這台電腦的瀏覽器裡。';
  $('#repNote').textContent = !cloud ? '只存在這台電腦的 profile.json，下一通電話開始生效。'
    : kind === 'memory' ? '雲端資料庫還沒接上，重新部署後會不見；下一通電話開始生效。'
      : '存在雲端資料庫（Upstash），下一通電話開始生效。';
}

// 通話畫面：正在用 ChatGPT 方案時照 OpenAI 的規定標示，並附上 Manage usage
function updatePlanTag() {
  const tag = $('#planTag');
  tag.classList.toggle('hidden', state.active !== 'chatgpt');
  tag.querySelector('a').href = usageUrl();
}

// ChatGPT 用量到上限或登入過期：狀態一變就說一次（用量上限要附 Manage usage）
function chatgptAlerts(prev, s) {
  const c = s.chatgpt;
  if (!prev || !c || s.provider !== 'chatgpt') return;
  const backup = s.active && s.active !== 'chatgpt' ? brainName(s.active) : 'Gemini';
  if (c.cooldown_until > Date.now() / 1000 && c.cooldown_until !== prev.cooldown_until) {
    toast(`${esc(tidy(`用量已達上限，先改用 ${backup}。`))} <a href="${esc(usageUrl())}" target="_blank" rel="noopener">Manage usage</a>`, 'warn', 10000, 'ChatGPT 方案');
  } else if (c.needs_login && !prev.needs_login) {
    const how = s.hosted ? '請在本機版按「登入並同步到雲端」' : '請到「AI 大腦」重新登入';
    toast(esc(tidy(`登入過期：${how}。先改用 ${backup}。`)), 'warn', 10000, 'ChatGPT');
  }
}

function applyStatus(s) {
  const prev = state.status;
  state.status = s;
  if (typeof s.hosted === 'boolean') state.hosted = s.hosted;
  state.provider = s.provider || '';
  state.active = s.active || s.provider || '';
  state.canAdmin = s.can_admin !== false;
  const sig = JSON.stringify(s.providers) + s.provider + (s.chatgpt?.mode || '') + state.canAdmin;
  if (sig !== state.statusSig) {
    state.statusSig = sig;
    $('#providerSelect').innerHTML = providerOptions(s);
  }
  $('#providerSelect').disabled = !state.canAdmin;
  $('#provider').classList.toggle('ready', s.ready);
  $('#provider').classList.toggle('down', !s.ready);
  $('#providerMsg').textContent = s.message;
  state.providerReady = s.ready;
  state.providerLabel = s.label;
  state.live = !!s.live;
  state.coachParallel = !!s.coach_parallel;
  updatePlanTag();
  hostedCopy();
  chatgptAlerts(prev?.chatgpt, s);
  if ($('#view-brain').classList.contains('active')) Brain.render();
}

let statusAt = 0;
async function refreshStatus() {
  let s;
  try {
    s = await api('/api/status');
  } catch {
    $('#provider').classList.remove('ready');
    $('#provider').classList.add('down');
    $('#providerMsg').textContent = state.hosted ? '連不到雲端網站，請重新整理' : '伺服器沒有回應，請重新執行 start.bat';
    state.providerReady = false;
    return;
  }
  statusAt = Date.now();
  applyStatus(s);
}

// 本機 6 秒問一次；雲端每問一次都算一次函式呼叫，改成 30 秒；分頁沒在看就不問
let pollTimer = 0;
function pollStatus() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!document.hidden) await refreshStatus();
    pollStatus();
  }, state.hosted ? 30000 : 6000);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && statusAt && Date.now() - statusAt > 5000) refreshStatus();
});

async function setProvider(id) {
  try {
    await api('/api/provider', { provider: id });
    toast('已切換 AI 大腦', 'info', 2000);
  } catch (err) {
    toast(esc(err.message), 'error', 7000);
  }
  state.statusSig = '';
  refreshStatus();
}

$('#providerSelect').addEventListener('change', (e) => setProvider(e.target.value));

// 主要大腦臨時不能用、這句改由備援回答：每通電話只提醒一次
function noteFallback(label, brain) {
  if (state.fallbackNoted) return;
  state.fallbackNoted = true;
  toast(esc(tidy(`${brainName(state.provider)} 暫時不能用，改用 ${shortLabel(label) || brainName(brain)}`)), 'warn', 5000);
  setTimeout(refreshStatus, 800);
}

// ------------------------------------------------------------------ AI 大腦頁（文字大腦、ChatGPT 方案、即時通話）

const Brain = (() => {
  let cardSig = '';
  let noticeSig = '';
  let modelsKey = ''; // 已經載入哪個帳號的模型清單
  let modelSig = '';
  let models = [];
  let urlEdited = false;
  let opened = false;
  let cloudBusy = false;
  let liveBusy = false;

  const nowSec = () => Date.now() / 1000;
  const toMs = (t) => (typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t) || 0);
  const hhmm = (sec) => {
    const d = new Date(sec * 1000);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };
  const tag = (text, kind = '') => `<span class="tag ${kind}">${esc(text)}</span>`;
  const usageLink = (primary) => `<a class="pill${primary ? ' primary' : ''}" href="${esc(usageUrl())}" target="_blank" rel="noopener" title="到 ChatGPT 查看和調整用量">Manage usage${icon('external')}</a>`;
  // 按鈕文字照 OpenAI 規定用 Continue with ChatGPT；官方圖示還沒確認，先只用文字
  const signInBtn = () => '<button class="pill primary" type="button" data-act="signin">Continue with ChatGPT</button>';

  // local：這台電腦自己登入；synced：本機登入後同步到雲端；hosted：OpenAI 核准的雲端登入
  function phaseOf(c) {
    const synced = c.mode === 'synced';
    if (c.mode === 'off') return 'off';
    if (!synced && c.available === false && c.blocked_reason) return 'blocked';
    if (c.needs_login) return 'expired';
    if (!c.signed_in) return synced ? 'unsynced' : 'signin';
    if (c.cooldown_until > nowSec()) return 'cooldown';
    if (c.plan_usage === false) return 'noplan';
    return 'on';
  }

  function cardHTML(s, c, phase) {
    const fb = esc(backupName());
    const synced = c.mode === 'synced';
    const inUse = state.active === 'chatgpt';
    const email = c.email ? `<p class="cg-who">${esc(c.email)}</p>` : '';
    const logout = synced
      ? (state.canAdmin ? '<button class="pill ghost" type="button" data-act="clear">清除雲端的 ChatGPT 憑證</button>' : '')
      : '<button class="pill ghost" type="button" data-act="logout">登出</button>';
    let head = '';
    let body = '';
    let acts = '';
    switch (phase) {
      case 'off':
        head = tag('沒有開啟');
        body = `<p>${esc(c.blocked_reason || (s.hosted
          ? '雲端版還沒開 ChatGPT：到 Vercel 新增 CHATGPT_SYNC_KEY 後 Redeploy，再從本機版同步。'
          : '這裡不能用 ChatGPT 登入。'))}</p>`;
        break;
      case 'blocked':
        head = tag('不能使用');
        body = `<p>${esc(c.blocked_reason)}</p>`;
        break;
      case 'unsynced':
        head = tag('還沒同步');
        body = `<p>還沒同步：請在本機版按「登入並同步到雲端」。</p><p class="muted">同步之前由 ${fb} 回答。</p>`;
        break;
      case 'expired':
        head = tag('登入過期', 'amber');
        body = synced
          ? `<p>ChatGPT 登入過期：請在本機版按「登入並同步到雲端」。</p><p class="muted">這段時間由 ${fb} 回答。</p>`
          : `${email}<p>登入過期，請重新登入。這段時間由 ${fb} 回答。</p>`;
        acts = (synced ? '' : signInBtn()) + logout;
        break;
      case 'signin':
        body = '<p>用你自己的 ChatGPT Plus 或 Pro 方案當文字大腦，不用另外付 API 費用。只限你本人的帳號。</p>'
          + `<p class="muted">${c.mode === 'hosted' ? '' : '這是這台電腦的登入；雲端版在下面另外同步。'}ChatGPT 不能用時自動改用 ${fb}。</p>`;
        acts = signInBtn();
        break;
      case 'cooldown':
        head = tag('用量已達上限', 'amber');
        body = `${email}<p>用量已達上限，暫用 ${fb}${Number.isFinite(c.cooldown_until) ? `，約 ${hhmm(c.cooldown_until)} 後再試` : ''}。</p>`;
        acts = usageLink(true) + logout;
        break;
      case 'noplan':
        head = tag('不能用方案', 'amber');
        body = `${email}<p>這個帳號不能用 ChatGPT 方案（要 Plus 或 Pro），先用 ${fb}。</p>`;
        acts = (synced ? '' : signInBtn()) + logout;
        break;
      default:
        head = inUse ? tag('使用 ChatGPT 方案', 'green') : tag('已登入');
        body = email + (inUse
          ? `<p class="muted">一般通話的客戶、教練和評分都用你的 ChatGPT 方案；不能用時自動改用 ${fb}。</p>`
          : '<p class="muted">已登入，但上面的文字大腦沒有選 ChatGPT。</p>')
          + (synced ? '<p class="muted">憑證加密存在雲端資料庫，由雲端自己續期。</p>' : '');
        acts = usageLink(false)
          + (!inUse && state.canAdmin ? '<button class="pill" type="button" data-act="use">改用 ChatGPT</button>' : '')
          + logout;
    }
    return tidy(`<div class="cg-head"><b>ChatGPT</b>${head}</div>${body}${acts ? `<div class="cg-actions">${acts}</div>` : ''}`);
  }

  function fillModels(c) {
    const sig = JSON.stringify([models, c.model, c.coach_model, state.canAdmin]);
    if (sig === modelSig) return; // 沒變就不重畫，下拉選單開著也不會被關掉
    modelSig = sig;
    const first = models[0]?.display_name || models[0]?.slug || '';
    const opts = (cur) => [`<option value="">自動${first ? `（${esc(first)}）` : ''}</option>`]
      .concat(models.map((m) => `<option value="${esc(m.slug)}"${m.slug === cur ? ' selected' : ''}>${esc(m.display_name || m.slug)}</option>`))
      .concat(cur && !models.some((m) => m.slug === cur) ? [`<option value="${esc(cur)}" selected>${esc(cur)}（不在清單裡）</option>`] : [])
      .join('');
    const pick = (v) => (v === 'auto' ? '' : v || ''); // 「自動」有的版本存成 auto
    $('#cgModel').innerHTML = opts(pick(c.model));
    $('#cgCoachModel').innerHTML = opts(pick(c.coach_model));
    $('#cgModel').disabled = !state.canAdmin;
    $('#cgCoachModel').disabled = !state.canAdmin;
  }

  async function loadModels(c, force) {
    const key = `${c.mode}|${c.email}`;
    if (!force && key === modelsKey) { fillModels(c); return; }
    modelsKey = key;
    $('#cgModelNote').textContent = '載入模型清單中…';
    try {
      const d = await api('/api/chatgpt/models');
      models = Array.isArray(d.models) ? d.models.filter((m) => m && m.slug) : [];
      $('#cgModelNote').textContent = models.length ? '「自動」用清單的第一個。' : '這個帳號沒有可用的模型。';
    } catch (err) {
      models = []; // 定時更新狀態時不重抓，下次進這一頁再試
      $('#cgModelNote').textContent = `讀不到模型清單：${err.message}`;
    }
    fillModels(state.status?.chatgpt || c);
  }

  function cloud(s, c) {
    const box = $('#cgCloud');
    const show = !s.hosted && (c.mode || 'local') === 'local';
    box.classList.toggle('hidden', !show);
    if (!show) return;
    const cl = c.cloud || {};
    const url = $('#cgCloudUrl');
    if (cl.url && !urlEdited && document.activeElement !== url) url.value = cl.url;
    if (!opened) {
      opened = true;
      if (cl.configured || cl.last_error) box.open = true;
    }
    // 同步密碼不會從伺服器拿回來，只顯示有沒有存
    $('#cgSyncKey').placeholder = cl.configured ? '已儲存；要換才重新填' : '和 Vercel 的 CHATGPT_SYNC_KEY 一樣';
    const at = cl.last_sync_at ? toMs(cl.last_sync_at) : 0;
    const t = $('#cgCloudTag');
    if (cl.last_error) { t.textContent = '同步失敗'; t.className = 'tag red'; }
    else if (at) { t.textContent = '已同步'; t.className = 'tag green'; }
    else if (cl.configured) { t.textContent = '已連上'; t.className = 'tag'; }
    else { t.textContent = ''; t.className = 'tag'; }
    let info = '先填雲端網址和同步密碼，按「儲存並測試」。';
    if (at) info = `上次同步：${fmtDate(at)}${cl.last_sync_email ? ` · ${cl.last_sync_email}` : ''}`;
    else if (cl.configured) info = '已連上雲端，按「登入並同步到雲端」用你的帳號登入。';
    $('#cgCloudInfo').innerHTML = esc(info) + (cl.last_error ? `<span class="err-text">${esc(cl.last_error)}</span>` : '');
    $('#btnCloudSave').disabled = cloudBusy || !state.canAdmin;
    $('#btnCloudSync').disabled = !cl.configured || !state.canAdmin;
    url.disabled = !state.canAdmin;
    $('#cgSyncKey').disabled = !state.canAdmin;
  }

  function chatgpt(s, enter) {
    const c = s.chatgpt;
    $('#cgSection').classList.toggle('hidden', !c);
    if (!c) return; // 舊版伺服器沒有 ChatGPT
    const phase = phaseOf(c);
    const sig = JSON.stringify([phase, c.email, c.mode, c.blocked_reason, c.cooldown_until, state.active, state.canAdmin, s.hosted, usageUrl(), backupName()]);
    if (sig !== cardSig) {
      cardSig = sig;
      $('#cgCard').innerHTML = cardHTML(s, c, phase);
    }
    const showModels = phase === 'on' || phase === 'cooldown';
    $('#cgModels').classList.toggle('hidden', !showModels);
    if (showModels) loadModels(c, enter);
    cloud(s, c);
  }

  function live(s) {
    const has = typeof s.live_enabled === 'boolean';
    $('#liveSection').classList.toggle('hidden', !has);
    if (!has) return; // 舊版伺服器：即時通話跟著「Gemini」大腦
    const sw = $('#liveSwitch');
    const avail = s.live_available !== false;
    if (!liveBusy) sw.checked = s.live_enabled;
    // 不能用但還開著（例如金鑰被拿掉）：還是讓人關掉
    sw.disabled = liveBusy || !state.canAdmin || (!avail && !s.live_enabled);
    let desc = '關閉時，客戶用文字大腦回話再念出來；講完停一下才會送出。';
    if (!avail) desc = s.live_reason || '要先設定 Gemini 金鑰才能開。';
    else if (s.live_enabled) desc = '通話用 Gemini Live：可以直接插話，客戶會停下來聽。教練和評分仍用上面的文字大腦。';
    $('#liveDesc').textContent = desc;
  }

  function notices(s) {
    const out = [];
    const probs = Array.isArray(s.setup_problems) ? s.setup_problems.filter(Boolean) : [];
    if (probs.length) out.push(`<div class="notice"><b>雲端還沒設定好</b><ul>${probs.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>`);
    if (s.storage?.warning) out.push(`<div class="notice">${esc(s.storage.warning)}</div>`);
    const html = out.join('');
    if (html !== noticeSig) { noticeSig = html; $('#brainNotices').innerHTML = html; }
  }

  function auth(s) {
    const a = s.auth;
    const show = !!a?.required;
    $('#authSection').classList.toggle('hidden', !show);
    if (!show) return;
    $('#authName').textContent = a.name ? `已登入：${a.name}` : '已登入';
    $('#authRole').textContent = state.canAdmin ? '可以改設定。' : '只能練習，不能改設定；要改請用管理者通行碼登入。';
  }

  function render(enter = false) {
    const s = state.status;
    if (!s) { $('#brainMsg').textContent = '連線中…'; return; }
    $('#brainBadge').textContent = s.label ? `目前：${shortLabel(s.label)}` : '';
    const sel = $('#brainSelect');
    // 切換失敗時要畫回原本的選擇
    if (sel.dataset.sig !== state.statusSig || sel.value !== s.provider) {
      sel.dataset.sig = state.statusSig;
      sel.innerHTML = providerOptions(s);
    }
    sel.disabled = !state.canAdmin;
    $('#brainMsg').textContent = s.message || '';
    // 備援：主要大腦不能用時依序改用
    const rest = (Array.isArray(s.chain) ? s.chain : []).filter((id) => id !== state.active);
    $('#brainChain').textContent = [
      rest.length ? `不能用時依序改用：${rest.map(brainName).join('、')}。` : '',
      state.canAdmin ? '' : '只能看；要改設定請用管理者通行碼登入。',
    ].join('');
    notices(s);
    chatgpt(s, enter);
    live(s);
    auth(s);
  }

  async function saveModel(field, value) {
    try {
      const s = await api('/api/chatgpt/config', { [field]: value });
      toast('已更換模型', 'info', 2000);
      if (Array.isArray(s?.providers)) applyStatus(s);
      else refreshStatus();
    } catch (err) {
      toast(esc(err.message), 'error', 6000, '換模型失敗');
      modelSig = ''; // 畫回原本的選擇
      refreshStatus();
    }
  }

  async function saveCloud() {
    const btn = $('#btnCloudSave');
    const cloudUrl = $('#cgCloudUrl').value.trim().replace(/\/+$/, '');
    const syncKey = $('#cgSyncKey').value.trim();
    const saved = !!state.status?.chatgpt?.cloud?.configured;
    // 兩格都清空：取消雲端同步的設定
    const clearing = !cloudUrl && !syncKey;
    if (clearing && (!saved || !window.confirm('要清除雲端網址和同步密碼嗎？'))) {
      if (!saved) $('#cgCloudUrl').focus();
      return;
    }
    if (!clearing && !/^https?:\/\/[^\s/]+/i.test(cloudUrl)) {
      toast('請填完整的雲端網址，例如 https://你的專案.vercel.app', 'warn', 4000);
      $('#cgCloudUrl').focus();
      return;
    }
    // 已經存過的同步密碼可以留空沿用
    if (!clearing && !syncKey && !saved) {
      toast('請填同步密碼（和 Vercel 的 CHATGPT_SYNC_KEY 一樣）', 'warn', 4000);
      $('#cgSyncKey').focus();
      return;
    }
    cloudBusy = true;
    btn.disabled = true;
    btn.textContent = '測試中…';
    try {
      await api('/api/chatgpt/cloud', { cloud_url: cloudUrl, sync_key: syncKey });
      $('#cgSyncKey').value = ''; // 存好就清掉，不留在畫面上
      urlEdited = false;
      toast(clearing ? '已清除雲端設定' : '連上雲端了，接著按「登入並同步到雲端」', 'info', 4000);
    } catch (err) {
      toast(esc(err.message), 'error', 8000, '雲端測試沒過');
    } finally {
      cloudBusy = false;
      btn.disabled = !state.canAdmin;
      btn.textContent = '儲存並測試';
      refreshStatus();
    }
  }

  $('#brainSelect').addEventListener('change', (e) => setProvider(e.target.value));
  $('#cgModel').addEventListener('change', (e) => saveModel('model', e.target.value));
  $('#cgCoachModel').addEventListener('change', (e) => saveModel('coach_model', e.target.value));
  $('#cgCloudUrl').addEventListener('input', () => { urlEdited = true; });
  $('#btnCloudSave').addEventListener('click', saveCloud);
  // 登入一律整頁跳轉：憑證只在伺服器之間傳，不經過這個網頁
  $('#btnCloudSync').addEventListener('click', () => { location.href = '/auth/chatgpt/start?target=cloud'; });

  $('#cgCard').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const act = b.dataset.act;
    if (act === 'signin') { location.href = '/auth/chatgpt/start'; return; }
    if (act === 'use') { setProvider('chatgpt'); return; }
    const ask = act === 'clear'
      ? `要清除雲端的 ChatGPT 憑證嗎？之後改用 ${backupName()}；要再用就到本機版重新同步。`
      : `要登出 ChatGPT 嗎？之後改用 ${backupName()}。`;
    if (!window.confirm(tidy(ask))) return;
    b.disabled = true;
    try {
      const d = await api('/api/chatgpt/logout', {});
      let msg = '已登出 ChatGPT';
      if (act === 'clear') msg = '已清除雲端的 ChatGPT 憑證';
      else if (d.remote_revoked === false) msg = '已登出。ChatGPT 那邊的授權可以到 ChatGPT 設定 → Security 移除';
      toast(msg, 'info', 5000);
      if (Array.isArray(d.status?.providers)) applyStatus(d.status);
      else refreshStatus();
    } catch (err) {
      b.disabled = false;
      toast(esc(err.message), 'error', 6000, act === 'clear' ? '清除失敗' : '登出失敗');
    }
  });

  $('#liveSwitch').addEventListener('change', async (e) => {
    const sw = e.target;
    const on = sw.checked;
    liveBusy = true;
    sw.disabled = true;
    try {
      const s = await api('/api/live/config', { enabled: on });
      toast(on ? '已開啟即時通話，下一通電話開始' : '已關閉即時通話', 'info', 2500);
      liveBusy = false;
      if (Array.isArray(s?.providers)) applyStatus(s);
      else refreshStatus();
    } catch (err) {
      liveBusy = false;
      sw.checked = !on;
      toast(esc(err.message), 'error', 6000, '切換失敗');
      refreshStatus();
    }
  });

  $('#btnLogout').addEventListener('click', async () => {
    try { await api('/api/logout', {}); } catch { /* 照樣回登入頁 */ }
    location.href = '/login';
  });

  return { render };
})();

// ------------------------------------------------------------------ ChatGPT 登入回來

// OpenAI 的規定：第一次用 ChatGPT 方案登入時說明一次，之後不再出現
const WELCOME_KEY = 'aicoach.cgWelcomed';

function showCgWelcome() {
  try { if (localStorage.getItem(WELCOME_KEY)) return false; } catch { /* 讀不到就照樣說明 */ }
  $('#cgWelcomeUsage').href = usageUrl();
  $('#cgWelcome').classList.remove('hidden');
  $('#app').inert = true;
  $('#btnCgWelcomeOk').focus();
  return true;
}

function closeCgWelcome() {
  $('#cgWelcome').classList.add('hidden');
  $('#app').inert = false;
  try { localStorage.setItem(WELCOME_KEY, '1'); } catch { /* 存不了就下次再說一次 */ }
}

$('#btnCgWelcomeOk').addEventListener('click', closeCgWelcome);
$('#cgWelcome').addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  e.preventDefault();
  closeCgWelcome();
});

// 伺服器回的代碼換成看得懂的話；沒列到的直接顯示代碼
const CG_REASON = {
  denied: '你在 ChatGPT 的頁面按了取消',
  no_plan: '這個帳號不能用 ChatGPT 方案（要 Plus 或 Pro）',
  no_plan_scope: '這個帳號不能用 ChatGPT 方案（要 Plus 或 Pro）',
  bad_state: '登入逾時或已經用過，請重新按一次',
  state: '登入逾時或已經用過，請重新按一次',
  expired: '登入逾時，請重新按一次',
  no_code: 'ChatGPT 沒有回傳登入資料，請再試一次',
  no_client_id: 'ChatGPT 沒有回傳登入資料，請再試一次',
  client_mismatch: '登入資料對不上，請再試一次',
  account_mismatch: '登入的帳號和原本的不同，請先登出再登入',
  exchange_failed: '換取憑證失敗，請再試一次',
  no_id_token: '身分驗證失敗，請再試一次',
  bad_id_token: '身分驗證失敗，請再試一次',
  network: '連不到 OpenAI，請檢查網路',
  timeout: '連線逾時，請再試一次',
  store: '憑證存不進去，請再試一次',
  blocked: '這裡不能用 ChatGPT 登入',
  synced: '雲端版不能直接登入：請在本機版按「登入並同步到雲端」',
  local_only: '要在本機版（start.bat）按「登入並同步到雲端」',
  no_server: '請用 start.bat 開啟再登入',
  host: '請用 http://127.0.0.1 開啟再登入',
  unsupported: '這個版本還不能同步到雲端',
  cloud_not_set: '還沒填雲端網址和同步密碼',
  bad_cloud_url: '雲端網址不對',
  sync_off: '雲端還沒設定 CHATGPT_SYNC_KEY，或網址不對（記得 Redeploy）',
  sync_key: '同步密碼不對：要和 Vercel 上的 CHATGPT_SYNC_KEY 一樣',
  rate_limited: '試太多次了，請 10 分鐘後再試',
  redirect: '雲端網址被轉址了：請貼瀏覽器網址列最後顯示的網址',
  cloud: '雲端沒有收下憑證，請看「AI 大腦」裡的說明',
  cloud_not_app: '這個網址不是對練教練的雲端版',
  cloud_unreachable: '連不到雲端網站',
  cloud_blocked: '雲端擋住了：請用正式網址（https://專案名.vercel.app），或關掉 Deployment Protection',
  sync_failed: '雲端沒有收下憑證，請再試一次',
  admin: '需要管理者通行碼才能同步到雲端',
  host_mismatch: '這份登入不是給這個雲端網站的，請重新按「登入並同步到雲端」',
  bad_client: '登入資料格式不對，請再試一次',
  missing_fields: '登入資料不完整，請再試一次',
  bad_request: '資料格式錯誤，請再試一次',
  no_port: '請用 start.bat 開啟再登入',
  bad_redirect: '請用 http://127.0.0.1 開啟再登入',
  no_host_id: '拿不到雲端的主機代號，請先按「儲存並測試」',
  bad_host_id: '雲端的主機代號不對，請先按「儲存並測試」',
};

// 從 ChatGPT 登入回來：網址帶 ?chatgpt=結果&reason=代碼，說明結果後把網址清乾淨
function signInReturn() {
  const q = new URLSearchParams(location.search);
  const result = q.get('chatgpt');
  if (!result) return;
  const code = (q.get('reason') || '').replace(/[^\w.-]/g, '').slice(0, 40);
  q.delete('chatgpt');
  q.delete('reason');
  const rest = q.toString();
  history.replaceState(null, '', location.pathname + (rest ? `?${rest}` : '') + location.hash);
  const why = code ? CG_REASON[code] || `代碼：${code}` : '請再試一次';
  go('brain');
  if (result === 'ok') {
    if (!showCgWelcome()) toast('之後由你的 ChatGPT 方案回答。', 'info', 4000, '已登入 ChatGPT');
  } else if (result === 'no_plan') {
    toast(esc(tidy(`這個帳號沒有開放 ChatGPT 方案（要 Plus 或 Pro），先用 ${backupName()}。`)), 'warn', 8000, 'ChatGPT 方案不能用');
  } else if (result === 'denied') {
    toast('你取消了 ChatGPT 登入。', 'info', 4000);
  } else if (result === 'synced') {
    toast('雲端版現在用你的 ChatGPT 方案，之後由雲端自己續期。', 'info', 6000, '已同步到雲端');
  } else if (result === 'sync_failed') {
    toast(esc(why), 'error', 9000, '同步到雲端失敗');
  } else {
    toast(esc(why), 'error', 8000, 'ChatGPT 登入失敗');
  }
}

// ------------------------------------------------------------------ 對練情境

function diffDots(level) {
  return `<span class="diff-dots">${[1, 2, 3].map((i) => `<i class="${i <= level ? 'on' : ''}"></i>`).join('')}</span>`;
}

function renderHome() {
  $('#scenarioGrid').innerHTML = state.scenarios.map((s) => {
    const outgoing = s.call_direction === 'outgoing';
    return `<article class="scn-card" data-id="${esc(s.id)}" tabindex="0">
      <div class="scn-top">
        <div class="scn-avatar">${avatarSVG(s.customer.avatar)}</div>
        <div class="scn-who"><b>${esc(s.customer.name)}</b><span>${esc(s.customer.age)} 歲 · ${esc(s.customer.job)}</span></div>
      </div>
      <div class="scn-title">${esc(s.title)}</div>
      <p class="scn-desc">${esc(s.customer.summary)}</p>
      <div class="scn-meta">
        <span>${icon(outgoing ? 'phoneOut' : 'phoneIn')}${outgoing ? '由你撥出' : '客戶來電'}</span>
        <span>${icon('clock')}${esc(s.duration_min)} 分鐘</span>
        <span>${diffDots(s.difficulty_level || 2)}${esc(s.difficulty)}</span>
      </div>
    </article>`;
  }).join('');
  $$('.scn-card').forEach((card) => {
    card.addEventListener('click', () => startCall(card.dataset.id));
    card.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault(); // 焦點會移到「接聽」，不讓同一個 Enter 直接按下去
      startCall(card.dataset.id);
    });
  });
}

function envHint() {
  const hints = [];
  if (!(window.SpeechRecognition || window.webkitSpeechRecognition)) hints.push('這個瀏覽器不支援語音辨識，只能打字對練');
  if (!/Edg\//.test(navigator.userAgent)) hints.push('建議用 Microsoft Edge 開啟，客戶的聲音最自然');
  $('#envHint').textContent = hints.join('；');
}

// 情境改過（新增、修改、刪除、換名字）之後，首頁和最近練習跟著更新
async function reloadScenarios() {
  const data = await api('/api/scenarios');
  state.scenarios = data.scenarios || [];
  renderHome();
  renderRecent();
}

// ------------------------------------------------------------------ 練習紀錄（存在瀏覽器）

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch { return []; }
}

function saveHistory(list) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, 50))); } catch { /* 瀏覽器不允許儲存就不留紀錄 */ }
  updateHistoryCount();
  renderRecent();
}

const GRADE_TAG = { 優秀: 'green', 良好: '', 及格: 'amber', 需加強: 'red' };

function openHistoryEntry(h) {
  renderReport(h.report, { scenario: scenarioFor(h), messages: h.messages, trust: h.trust, secs: h.secs, at: h.at, from: 'history' });
}

function renderRecent() {
  const list = loadHistory().slice(0, 8);
  if (!list.length) {
    $('#recentList').innerHTML = '<div class="recent-empty">還沒有紀錄</div>';
    return;
  }
  $('#recentList').innerHTML = list.map((h, i) => {
    const s = scenarioFor(h);
    return `<button class="recent-item" type="button" data-i="${i}" title="${esc(`${s.customer.name} · ${s.title}`)}"><span>${esc(s.customer.name)} · ${esc(s.title)}</span><b>${h.report.total}</b></button>`;
  }).join('');
  $$('#recentList .recent-item').forEach((b) => b.addEventListener('click', () => {
    if (leaveCallIfNeeded()) openHistoryEntry(list[Number(b.dataset.i)]);
  }));
}

function updateHistoryCount() {
  const n = loadHistory().length;
  $('#historyCount').textContent = n ? String(n) : '';
}

function scenarioFor(entry) {
  return state.scenarios.find((s) => s.id === entry.scenario_id) || entry.scenario;
}

function renderHistory() {
  const list = loadHistory();
  $('#btnClearHistory').style.display = list.length ? '' : 'none';
  if (!list.length) {
    $('#historyBox').innerHTML = `<div class="empty"><b>還沒有練習紀錄</b>完成一次對練並評分後，就會出現在這裡。<br><button class="pill primary" type="button" id="btnFirst">開始第一次對練</button></div>`;
    $('#btnFirst').addEventListener('click', () => go('home'));
    return;
  }
  $('#historyBox').innerHTML = list.map((h, i) => {
    const s = scenarioFor(h);
    const alerts = h.report.violations.length ? ` · 合規警示 ${h.report.violations.length} 則` : '';
    return `<button class="list-row" type="button" data-i="${i}">
      <div class="av">${avatarSVG(s.customer.avatar)}</div>
      <div class="list-main"><b>${esc(s.customer.name)} · ${esc(s.title)}</b><span>${esc(fmtDate(h.at))} · ${h.mode === 'exam' ? '考核' : '練習'} · ${esc(fmtDuration(h.secs))}${alerts}</span></div>
      <div class="list-score"><span class="tag ${GRADE_TAG[h.report.grade] ?? ''}">${esc(h.report.grade)}</span><b>${h.report.total}</b></div>
    </button>`;
  }).join('');
  $$('#historyBox .list-row').forEach((row) => row.addEventListener('click', () => openHistoryEntry(list[Number(row.dataset.i)])));
}

$('#btnClearHistory').addEventListener('click', () => {
  if (!window.confirm('要刪除所有練習紀錄嗎？')) return;
  saveHistory([]);
  renderHistory();
});

// ------------------------------------------------------------------ 聲音設定

const TTS_INFO = {
  elevenlabs: {
    desc: '最自然。聲音庫有台灣口音的男女老少，也可以克隆真人聲音。',
    meta: '每月 US$6 起 · 資料在國外',
    steps: [
      '到 <a href="https://elevenlabs.io" target="_blank" rel="noopener">elevenlabs.io</a> 註冊，方案選 Starter（每月 US$6）以上',
      '點左下角的帳號 → API Keys → Create API Key',
      '把金鑰貼到下面，按「儲存金鑰」',
    ],
  },
  gemini: {
    desc: 'Google 最新的 3.8 語音。可以用文字描述，幫每位客戶設計台灣腔的聲音。',
    meta: '用多少付多少，很便宜 · 可先免費試 · 資料在國外',
    steps: [
      '到 <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a> 用 Google 帳號登入',
      '按「Create API key」建立金鑰',
      '把金鑰貼到下面，按「儲存」。免費版送出的內容 Google 會拿去改進產品，公司正式使用請先開啟付費',
    ],
    note: '免費版送出的內容 Google 會拿去改進產品，公司正式使用請先開啟付費。',
  },
  yating: {
    desc: '台灣廠商（台灣人工智慧實驗室），道地台灣口音，另有台語聲音。',
    meta: '用多少付多少 · 新會員送 NT$1,000',
    steps: [
      '到 <a href="https://developer.yating.tw" target="_blank" rel="noopener">developer.yating.tw</a> 註冊',
      '在控制台建立 API Key',
      '把金鑰貼到下面，按「儲存金鑰」',
    ],
  },
  browser: { desc: '免費、不用設定，但聽起來像在念稿。', meta: '免費', steps: [] },
};
const AGE_LABEL = { young: '年輕', middle_aged: '中年', old: '年長' };
const KEY_ENV = { elevenlabs: 'ELEVENLABS_API_KEY', gemini: 'GEMINI_API_KEY', yating: 'YATING_API_KEY' };

// 雲端版的金鑰放在 Vercel 環境變數：網頁上不能貼，改成告訴他去哪裡設定
function keyFromEnv(t, pid) {
  const src = t.key_source;
  return src === 'env' || (!!src && typeof src === 'object' && src[pid] === 'env');
}

function keySteps(t, pid) {
  const info = TTS_INFO[pid];
  if (!keyFromEnv(t, pid)) return info.steps;
  const name = t.env_names?.[pid] || KEY_ENV[pid] || 'API 金鑰';
  return [
    ...info.steps.slice(0, -1),
    `到 Vercel 專案 → Settings → Environment Variables，新增 <code>${esc(name)}</code>，值貼上金鑰`,
    '到 Deployments → 最新一筆右邊的「⋯」→ Redeploy，完成後重新整理這一頁',
  ];
}

let preview = null;

function stopPreview() {
  if (preview) { preview.pause(); preview = null; }
  $$('.voice-row .play.playing').forEach((b) => { b.classList.remove('playing'); b.innerHTML = icon('play'); });
  if (window.speechSynthesis) speechSynthesis.cancel();
}

async function playPreview(button, source) {
  const wasPlaying = button?.classList.contains('playing');
  stopPreview();
  if (wasPlaying) return;
  if (button) { button.classList.add('playing'); button.innerHTML = icon('stop'); }
  const reset = () => { if (button) { button.classList.remove('playing'); button.innerHTML = icon('play'); } };
  try {
    let url = source.url;
    if (!url) {
      const res = await request('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar: source.avatar, text: source.text, voice_id: source.voiceId || undefined }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.error || httpError(res.status));
      }
      url = URL.createObjectURL(await res.blob());
    }
    preview = new Audio(url);
    preview.onended = reset;
    preview.onerror = reset;
    await preview.play();
  } catch (err) {
    reset();
    toast(esc(err.message), 'error', 7000, '試聽失敗');
  }
}

async function loadTts() {
  try { state.tts = await api('/api/tts/status'); } catch { /* 保留上一次的設定 */ }
  return state.tts;
}

async function renderVoices() {
  const t = await loadTts();
  if (!t) {
    $('#voiceCustomers').innerHTML = `<p class="notice">${state.hosted ? '讀不到語音設定，請重新整理這一頁' : '讀不到語音設定，請重新執行 start.bat'}</p>`;
    return;
  }
  const current = t.providers.find((p) => p.id === t.provider);
  $('#ttsBadge').textContent = `目前使用：${current.label}`;
  $('#ttsBadge').className = `tag ${t.provider === 'browser' ? '' : 'green'}`;
  $('#ttsProviders').innerHTML = t.providers.map((p) => {
    const info = TTS_INFO[p.id];
    let status = '<span class="tag">未設定</span>';
    if (p.id === 'browser') status = '<span class="tag">免費</span>';
    else if (p.configured) status = `<span class="tag green">${icon('check')}已設定</span>`;
    return `<button class="svc${p.id === t.selected ? ' on' : ''}" type="button" data-pid="${esc(p.id)}">
      <div class="svc-top"><b>${esc(p.label)}</b>${status}</div>
      <p>${esc(info.desc)}</p>
      <div class="svc-meta">${esc(info.meta)}</div>
    </button>`;
  }).join('');

  const selected = t.providers.find((p) => p.id === t.selected) || current;
  const needKey = selected.id !== 'browser' && !selected.configured;
  $('#keyBox').classList.toggle('hidden', !needKey);
  if (needKey) {
    const env = keyFromEnv(t, selected.id);
    $('#keySteps').innerHTML = keySteps(t, selected.id).map((s) => `<li>${s}</li>`).join('');
    $('#keyRow').classList.toggle('hidden', env);
    $('#keyNote').textContent = env
      ? `雲端版的金鑰只放在 Vercel 的環境變數，網頁上不能貼。${TTS_INFO[selected.id].note || ''}`
      : '金鑰只會存在這台電腦的 .env 檔案裡，不會上傳到別的地方。';
  }

  $('#modelRow').classList.toggle('hidden', !t.models.length);
  $('#ttsModel').innerHTML = t.models.map((m) => `<option value="${esc(m.id)}"${m.id === t.model ? ' selected' : ''}>${esc(m.label)}</option>`).join('');
  $('#modelNote').textContent = t.model_note || '';

  const serverMode = t.provider !== 'browser';
  const autoVoice = t.provider === 'gemini' ? '第一次通話時自動設計台灣腔' : '第一次通話時自動挑台灣口音';
  $('#voiceCustomers').innerHTML = t.customers.map((c) => `
    <div class="voice-card" data-avatar="${esc(c.avatar)}">
      <div class="vc-head">
        <div class="vc-avatar">${avatarSVG(c.avatar)}</div>
        <div class="vc-who"><b>${esc(c.name)}</b><span>${c.gender === 'male' ? '男' : '女'} · ${esc(c.age)} 歲</span></div>
        <div class="vc-current"><span>目前聲音</span><b>${esc(serverMode ? (c.voice?.name || autoVoice) : '瀏覽器內建')}</b></div>
        <button class="pill" type="button" data-act="test">${icon('play')}試聽</button>
        ${serverMode ? `<button class="pill ghost" type="button" data-act="pick">更換${icon('chevronDown')}</button>` : ''}
      </div>
      <div class="vc-picker hidden"></div>
    </div>`).join('');

  $$('.voice-card').forEach((card) => {
    const c = t.customers.find((x) => x.avatar === card.dataset.avatar);
    card.querySelector('[data-act="test"]').addEventListener('click', () => {
      if (serverMode) {
        if (t.provider === 'gemini' && !c.voice) toast(`第一次要先幫${esc(c.name)}設計聲音，請稍等幾秒…`, 'info', 5000);
        playPreview(null, { avatar: c.avatar, text: c.opening });
      } else {
        stopPreview();
        BrowserVoice.configure({ avatar: c.avatar, voice: { gender: c.gender } });
        BrowserVoice.speakOnce(c.opening);
      }
    });
    card.querySelector('[data-act="pick"]')?.addEventListener('click', () => togglePicker(card, c));
  });
}

function voiceRow(v, c) {
  const inUse = c.voice && c.voice.voice_id === v.voice_id;
  const meta = [v.accent, AGE_LABEL[v.age] || '', v.gender ? (v.gender === 'male' ? '男' : '女') : '', v.description].filter(Boolean).join(' · ');
  return `<div class="voice-row${inUse ? ' current' : ''}" data-id="${esc(v.voice_id)}">
    <button class="play" type="button" title="試聽">${icon('play')}</button>
    <div class="vr-main"><b>${esc(v.name)}</b><span>${esc(meta || '—')}</span></div>
    ${inUse ? '<span class="tag green">使用中</span>' : '<button class="pill sm" type="button" data-use>使用</button>'}
  </div>`;
}

async function togglePicker(card, c) {
  const box = card.querySelector('.vc-picker');
  if (!box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML = '<div class="vc-loading">載入聲音清單中…</div>';
  try {
    const data = await api(`/api/tts/voices?avatar=${encodeURIComponent(c.avatar)}`);
    const gem = data.provider === 'gemini';
    const lists = [];
    if (gem) {
      lists.push(['mine', '我設計的聲音', data.mine]);
      lists.push(['library', 'Google 內建', data.library]);
    } else {
      if (data.library.length) lists.push(['library', '推薦：台灣口音', data.library]);
      lists.push(['mine', data.provider === 'yating' ? '雅婷的聲音' : '我帳號裡的聲音', data.mine]);
    }
    const empty = gem ? '還沒有設計過聲音，用上面的描述設計一個。' : '這裡還沒有聲音。可以到 ElevenLabs 網站的 Voice Library 加入聲音。';
    const design = gem ? `<div class="vc-design">
        <textarea rows="2" maxlength="300" spellcheck="false" aria-label="聲音描述">${esc(c.brief || '')}</textarea>
        <button class="pill sm primary" type="button" data-design>設計新聲音</button>
      </div>
      <p class="vc-hint">寫一兩句年齡、個性和口音，設計好會自動換上並試聽。內建聲音講中文不一定是台灣腔。</p>` : '';
    const byId = new Map([...data.library, ...data.mine].map((v) => [v.voice_id, v]));
    box.innerHTML = design + `<div class="vc-tabs">${lists.map(([k, label, arr], i) =>
      `<button type="button" class="${i === 0 ? 'on' : ''}" data-tab="${k}">${esc(label)} <span>${arr.length}</span></button>`).join('')}</div>`
      + lists.map(([k, , arr], i) => `<div class="vc-list${i === 0 ? '' : ' hidden'}" data-list="${k}">${arr.length
        ? arr.map((v) => voiceRow(v, c)).join('')
        : `<div class="vc-empty">${empty}</div>`}</div>`).join('');
    box.querySelector('[data-design]')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = '設計中…';
      try {
        await api('/api/tts/design', { avatar: c.avatar, brief: $('.vc-design textarea', box).value.trim() });
        toast(`已幫${esc(c.name)}換上新設計的聲音`, 'info', 2500);
        await renderVoices();
        playPreview(null, { avatar: c.avatar, text: c.opening });
      } catch (err) {
        btn.disabled = false;
        btn.textContent = '設計新聲音';
        toast(esc(err.message), 'error', 8000, '設計聲音失敗');
      }
    });
    $$('.vc-tabs button', box).forEach((b) => b.addEventListener('click', () => {
      $$('.vc-tabs button', box).forEach((x) => x.classList.toggle('on', x === b));
      $$('.vc-list', box).forEach((l) => l.classList.toggle('hidden', l.dataset.list !== b.dataset.tab));
    }));
    $$('.voice-row', box).forEach((row) => {
      const v = byId.get(row.dataset.id);
      row.querySelector('.play').addEventListener('click', (e) => playPreview(e.currentTarget, {
        url: v.preview_url, avatar: c.avatar, text: c.opening, voiceId: v.voice_id,
      }));
      row.querySelector('[data-use]')?.addEventListener('click', async (e) => {
        e.currentTarget.disabled = true;
        try {
          await api('/api/tts/assign', {
            avatar: c.avatar, voice_id: v.voice_id, public_owner_id: v.public_owner_id, name: v.name,
            brief: gem && v.source === 'mine' ? v.description : undefined,
          });
          toast(`已幫${esc(c.name)}換成「${esc(v.name)}」`, 'info', 2500);
          renderVoices();
        } catch (err) {
          e.currentTarget.disabled = false;
          toast(esc(err.message), 'error', 7000, '換聲音失敗');
        }
      });
    });
  } catch (err) {
    box.innerHTML = `<div class="vc-error">${esc(err.message)}</div>`;
  }
}

$('#ttsProviders').addEventListener('click', async (e) => {
  const b = e.target.closest('.svc');
  if (!b) return;
  try { await api('/api/tts/config', { provider: b.dataset.pid }); } catch (err) { toast(esc(err.message), 'error'); }
  renderVoices();
});

$('#btnSaveKey').addEventListener('click', async () => {
  const key = $('#keyInput').value.trim();
  if (!key) { $('#keyInput').focus(); return; }
  try {
    await api('/api/tts/key', { provider: state.tts.selected, key });
    $('#keyInput').value = '';
    toast('金鑰已儲存', 'info', 2500);
    renderVoices();
  } catch (err) {
    toast(esc(err.message), 'error', 8000, '儲存失敗');
    if (err.data?.env_name) renderVoices(); // 雲端版：改顯示 Vercel 的設定步驟
  }
});

$('#ttsModel').addEventListener('change', async (e) => {
  try { await api('/api/tts/config', { model: e.target.value }); } catch (err) { toast(esc(err.message), 'error'); }
  renderVoices();
});

// ------------------------------------------------------------------ 我的背景（業務自己的姓名、職稱、公司規定）

const Rep = (() => {
  let data = null;
  let role = 'wealth';
  let dirty = false;
  const f = (id) => $(`#${id}`);

  function setRole(r) {
    role = r;
    $$('#repRole button').forEach((b) => b.classList.toggle('on', b.dataset.role === r));
    f('repCustomTitle').classList.toggle('hidden', r !== 'custom');
  }

  function setDirty(v) {
    dirty = v;
    f('btnRepSave').disabled = !v;
  }

  function count() {
    f('repKnowledgeCount').textContent = `${f('repKnowledge').value.length} / ${data?.limits?.knowledge ?? 2000}`;
  }

  // 客戶常用的叫法，點一下就填進去
  function renderSuggest() {
    const s = f('repSurname').value.trim() || data?.profile?.surname || '';
    const g = f('repGiven').value.trim();
    const opts = s ? [`小${s}`, `${s}先生`, `${s}小姐`, `${s}經理`, g] : [g];
    $('#repSuggest').innerHTML = [...new Set(opts.filter(Boolean))]
      .map((o) => `<button class="pill sm ghost" type="button">${esc(o)}</button>`).join('');
    // 沒填就用「小＋姓」，換姓時預設叫法跟著變
    f('repAddress').placeholder = s ? `小${s}（沒填就這樣叫你）` : '';
  }

  function fill(d) {
    data = d;
    const p = d.profile || {};
    const eff = d.effective || {};
    f('repSurname').value = p.surname || '';
    f('repGiven').value = p.given_name || '';
    // 「小＋姓」就是預設叫法：欄位留空，之後換姓才會跟著變
    f('repAddress').value = p.address && p.address !== `小${p.surname}` ? p.address : '';
    f('repCustomTitle').value = p.custom_title || '';
    f('repCompany').value = p.company || '';
    f('repCompany').placeholder = '證券公司';
    f('repBranch').value = p.branch || '';
    f('repYears').value = p.years ?? '';
    f('repProducts').value = (p.products || []).join('\n');
    f('repKnowledge').value = p.knowledge || '';
    setRole(p.role || 'wealth');
    $('#repBadge').textContent = d.saved ? `${eff.title} · ${eff.address}` : '尚未設定，使用預設';
    $('#repPreview').innerHTML = (d.scenarios || []).map((s) =>
      `<div class="rep-line"><b>${esc(s.customer.name)}</b><span>${esc(s.opening_line)}</span></div>`).join('');
    renderSuggest();
    count();
    setDirty(false);
  }

  // 存好後首頁卡片、開場白預覽馬上換成新名字
  function applied(d) {
    state.scenarios = d.scenarios || state.scenarios;
    state.rep = d.effective || null;
    renderHome();
    renderRecent();
    fill(d);
  }

  function collect() {
    const y = f('repYears').value.trim();
    return {
      surname: f('repSurname').value.trim(),
      given_name: f('repGiven').value.trim(),
      address: f('repAddress').value.trim(),
      role,
      custom_title: f('repCustomTitle').value.trim(),
      company: f('repCompany').value.trim(),
      branch: f('repBranch').value.trim(),
      years: y === '' ? null : /^\d+$/.test(y) ? Number(y) : y, // 不是整數就原樣送，讓伺服器回清楚的錯誤
      products: f('repProducts').value.split('\n').map((x) => x.trim()).filter(Boolean),
      knowledge: f('repKnowledge').value,
    };
  }

  async function render() {
    try {
      fill(await api('/api/profile'));
    } catch (err) {
      toast(esc(err.message), 'error', 6000, '讀不到背景設定');
    }
  }

  async function save() {
    f('btnRepSave').disabled = true;
    try {
      applied(await api('/api/profile', { profile: collect() }));
      toast('已儲存，下一通電話開始生效', 'info', 2500);
    } catch (err) {
      setDirty(true);
      toast(esc(err.message), 'error', 6000, '儲存失敗');
    }
  }

  async function reset() {
    if (!window.confirm('要恢復成預設的業務設定嗎？')) return;
    try {
      applied(await api('/api/profile', { reset: true }));
      toast('已恢復預設', 'info', 2500);
    } catch (err) {
      toast(esc(err.message), 'error', 6000, '儲存失敗');
    }
  }

  $('#view-rep').addEventListener('input', (e) => {
    if (e.target.id === 'repKnowledge') count();
    if (e.target.id === 'repSurname' || e.target.id === 'repGiven') renderSuggest();
    setDirty(true);
  });
  $('#repSuggest').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    f('repAddress').value = b.textContent;
    setDirty(true);
  });
  $('#repRole').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    setRole(b.dataset.role);
    setDirty(true);
    if (role === 'custom') f('repCustomTitle').focus();
  });
  f('btnRepSave').addEventListener('click', save);
  f('btnRepReset').addEventListener('click', reset);

  return { render, get dirty() { return dirty; } };
})();

// ------------------------------------------------------------------ 情境設定（新增、修改、複製、刪除、AI 草稿）

const ScenarioAdmin = (() => {
  let meta = { scenarios: [], avatars: [], placeholders: [] };
  // 編輯中的情境只放在這裡，不放進 state：練習流程拿不到隱藏設定
  let draft = null;
  let editingId = '';
  let copyFrom = '';
  let dirty = false;
  let hiddenLen = {}; // 已存的隱藏設定有幾個字；內容本身不會傳到瀏覽器
  let rowMap = { checklist: [], demo_lines: [] }; // 送出時略過空白列，錯誤的第幾項要對回畫面上的第幾列
  let lastDemo = null; // 最後點過的示範句，代稱按鈕插到這一句
  let gen = 0;
  let saving = false;

  const SOURCE_TAG = { builtin: '內建', modified: '內建·已修改', custom: '自訂' };
  const AVATARS = [{ id: 'chen', gender: 'male' }, { id: 'lin', gender: 'female' }, { id: 'wang', gender: 'female' }];
  const AV_DESC = { chen: '戴眼鏡的中年男性', lin: '年長女性', wang: '年輕女性' };
  const DIFF = [[1, '簡單'], [2, '中等'], [3, '困難']];
  const DIRECTION = [['incoming', '客戶來電'], ['outgoing', '由你撥出']];
  const HIDDEN = [
    ['persona', '客戶人設', 1500, 5, '用「你是……」開頭：身分、家庭、投資經歷、個性和說話方式'],
    ['hidden_info', '隱藏資訊', 1000, 4, '沒被問到就不會說的關鍵事實，例如資金用途、投資期限、預備金'],
    ['reveal_rules', '問到才說（業務問…→ 回答…）', 1500, 4, '一行一條，例如：業務問這筆錢什麼時候要用 → 回答：兩年後孩子出國念書'],
    ['behavior', '反應方式', 1500, 4, '業務做錯時怎麼反應，做對時怎麼慢慢放心'],
    ['coach_notes', '教練重點', 1000, 4, '好的應對步驟和常見錯誤，給 AI 教練評分用'],
  ];
  const TOKENS = ['業務稱呼', '職稱簡稱', '公司'];
  const BRACE = /\{([^{}\n]{1,20})\}/g;

  const phKey = (k) => String(k || '').replace(/[{}]/g, '');
  const phValue = (k) => meta.placeholders?.find((p) => phKey(p.key) === k)?.value ?? '';
  const avatars = () => (meta.avatars?.length ? meta.avatars : AVATARS);
  const genderOf = (id) => avatars().find((a) => a.id === id)?.gender || 'male';
  const form = () => $('#editorForm');

  async function loadMeta() {
    meta = { scenarios: [], avatars: [], placeholders: [], ...(await api('/api/editor/list', {})) };
  }

  // ---- 情境清單

  async function renderList() {
    const box = $('#scnAdminList');
    try {
      await loadMeta();
    } catch (err) {
      box.innerHTML = `<p class="notice">讀不到情境：${esc(err.message)}</p>`;
      return;
    }
    if (!meta.scenarios.length) {
      box.innerHTML = '<div class="empty"><b>還沒有情境</b>按右上角「新增情境」開始。</div>';
      return;
    }
    box.innerHTML = meta.scenarios.map((s) => {
      const acts = [`<button class="pill sm ghost" type="button" data-act="copy">複製</button>`];
      if (s.source === 'custom') acts.push('<button class="pill sm ghost" type="button" data-act="delete">刪除</button>');
      if (s.source === 'modified') acts.push('<button class="pill sm ghost" type="button" data-act="restore">還原預設</button>');
      return `<div class="list-row" role="button" tabindex="0" data-id="${esc(s.id)}">
        <div class="av">${avatarSVG(s.avatar)}</div>
        <div class="list-main"><b>${esc(s.name)} · ${esc(s.title)}</b></div>
        <span class="tag">${esc(SOURCE_TAG[s.source] || '')}</span>
        <div class="row-acts">${acts.join('')}</div>
      </div>`;
    }).join('');
    $$('.list-row', box).forEach((row) => {
      const s = meta.scenarios.find((x) => x.id === row.dataset.id);
      row.addEventListener('click', () => open(s.id));
      row.addEventListener('keydown', (e) => {
        if (e.target !== row || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        open(s.id);
      });
      $$('[data-act]', row).forEach((b) => b.addEventListener('click', (e) => {
        e.stopPropagation();
        if (b.dataset.act === 'copy') open(s.id, { copy: true });
        else remove(s, b.dataset.act === 'restore');
      }));
    });
  }

  async function remove(s, restore) {
    const ask = restore ? '要還原成原本的內容嗎？' : `要刪除「${s.name} · ${s.title}」嗎？`;
    if (!window.confirm(ask)) return;
    try {
      const d = await api('/api/editor/delete', { id: s.id });
      toast(d.restored ? '已還原成原本的內容' : '已刪除', 'info', 2500);
      await reloadScenarios().catch(() => {});
      renderList();
    } catch (err) {
      toast(esc(err.message), 'error', 6000, restore ? '還原失敗' : '刪除失敗');
    }
  }

  // ---- 編輯表單

  function blankScenario() {
    return {
      title: '', product: '', difficulty_level: 2, duration_min: 8, call_direction: 'incoming', initial_trust: 45,
      customer: { name: '', age: 45, job: '', avatar: 'chen', summary: '' },
      learner_task: '', opening_line: '', first_tip: '',
      checklist: [{ id: '', label: '先同理客戶情緒', hint: '' }, { id: '', label: '約定具體下一步', hint: '例如約時間、寄資料' }],
      demo_lines: [],
    };
  }

  async function open(id, { copy = false } = {}) {
    const myGen = ++gen;
    let sc;
    let lens = {};
    try {
      if (!meta.avatars?.length || !meta.placeholders?.length) await loadMeta();
      if (id) {
        sc = (await api('/api/editor/get', { id })).scenario;
        lens = sc.hidden || {};
      } else {
        sc = blankScenario();
      }
    } catch (err) {
      toast(esc(err.message), 'error', 6000, '打不開這個情境');
      return;
    }
    if (myGen !== gen) return;
    delete sc.hidden;
    delete sc.source;
    if (copy) sc.title = `${String(sc.title || '').slice(0, 26)}（複本）`;
    draft = sc;
    hiddenLen = lens;
    editingId = id && !copy ? id : '';
    copyFrom = id && copy ? id : '';
    dirty = false;
    $('#aiDesc').value = '';
    $('#editorTitle').textContent = editingId ? '修改情境' : copyFrom ? '複製情境' : '新增情境';
    renderForm(sc, { openHidden: !id });
    show('editor');
    setNav('scenarios');
  }

  const attrs = (o) => Object.entries(o).filter(([, v]) => v !== undefined && v !== false)
    .map(([k, v]) => (v === true ? ` ${k}` : ` ${k}="${esc(v)}"`)).join('');

  function text(k, label, v, max, { wide = false, ph = '' } = {}) {
    return `<label class="field${wide ? ' wide' : ''}"><span>${esc(label)}</span><input${attrs({ type: 'text', 'data-k': k, maxlength: max, value: v ?? '', placeholder: ph, autocomplete: 'off' })}></label>`;
  }

  function area(k, label, v, max, rows, { ph = '', chips = false } = {}) {
    return `<label class="field wide"><span>${esc(label)}</span><textarea${attrs({ 'data-k': k, maxlength: max, rows, placeholder: ph })}>${esc(v ?? '')}</textarea></label>${chips ? chipRow(k) : ''}`;
  }

  function num(k, label, v, min, max) {
    return `<label class="field"><span>${esc(label)}</span><input${attrs({ type: 'number', 'data-k': k, 'data-num': true, min, max, step: 1, inputmode: 'numeric', value: v ?? '' })}></label>`;
  }

  function seg(k, label, v, opts, isNum) {
    return `<div class="field"><span>${esc(label)}</span><div class="seg" role="group"${attrs({ 'aria-label': label, 'data-k': k, 'data-num': !!isNum })}>${opts.map(([val, name]) =>
      `<button type="button" data-v="${esc(val)}" class="${String(val) === String(v) ? 'on' : ''}">${esc(name)}</button>`).join('')}</div></div>`;
  }

  // 代稱小按鈕：插到游標位置，通話時伺服器會換成真的名字
  function chipRow(target) {
    const hint = TOKENS.map((t) => `「${phValue(t)}」`).filter((x) => x !== '「」').join('');
    return `<div class="ph-chips wide" data-for="${esc(target)}">${TOKENS.map((t) =>
      `<button class="pill sm ghost" type="button" data-tok="{${esc(t)}}" title="${esc(`通話時會換成「${phValue(t)}」`)}">{${esc(t)}}</button>`).join('')}${hint ? `<span>通話時會換成${esc(hint)}</span>` : ''}</div>`;
  }

  function avatarPick(v) {
    return `<div class="field wide"><span>頭像</span><div class="av-pick" data-k="customer.avatar">${avatars().map((a) =>
      `<button type="button" data-v="${esc(a.id)}" class="${a.id === v ? 'on' : ''}" title="${esc(AV_DESC[a.id] || (a.gender === 'female' ? '女性' : '男性'))}" aria-label="${esc(`頭像：${AV_DESC[a.id] || (a.gender === 'female' ? '女性' : '男性')}`)}">${avatarSVG(a.id)}</button>`).join('')}
      <span class="muted" id="avGender">${genderOf(v) === 'female' ? '女' : '男'}</span></div>
      <span class="muted">頭像決定性別和聲音；同一個頭像的客戶共用同一個聲音。</span></div>`;
  }

  function checkRow(c, i) {
    return `<div class="edit-item" data-id="${esc(c.id || '')}">
      <input class="ed-label"${attrs({ type: 'text', 'data-k': `checklist.${i}.label`, maxlength: 20, value: c.label || '', placeholder: '要問到或做到的事', 'aria-label': '檢核項目' })}>
      <input class="ed-hint"${attrs({ type: 'text', 'data-k': `checklist.${i}.hint`, maxlength: 60, value: c.hint || '', placeholder: '提示（可不填）', 'aria-label': '提示' })}>
      <button class="icon-btn" type="button" data-del aria-label="刪除這項">${icon('x')}</button>
    </div>`;
  }

  function demoRow(line, i) {
    return `<div class="edit-item demo">
      <span class="d${checkCompliance(line || '').length ? ' bad' : ''}"></span>
      <input${attrs({ type: 'text', 'data-k': `demo_lines.${i}`, maxlength: 80, value: line || '', placeholder: '業務可以直接說的一句話', 'aria-label': '示範句' })}>
      <button class="icon-btn" type="button" data-del aria-label="刪除這句">${icon('x')}</button>
    </div>`;
  }

  function renderForm(sc, { openHidden = false } = {}) {
    const c = sc.customer || {};
    const so = sc.server_only || {};
    const hidden = HIDDEN.map(([k, label, max, rows, example]) => {
      const n = hiddenLen[k] || 0;
      const ph = n && !so[k] ? `已設定 ${n} 字（學員看不到，這裡也不顯示）。要修改請整段重寫；留空就保留原本內容。` : example;
      return area(`server_only.${k}`, label, so[k] || '', max, rows, { ph, chips: k === 'persona' });
    }).join('');
    const trust = Number.isFinite(Number(sc.initial_trust)) ? Number(sc.initial_trust) : 45;
    form().innerHTML = `
      <h2 class="sec-title">客戶資料</h2>
      <div class="form-grid">
        ${text('customer.name', '稱呼', c.name, 12, { ph: '例如：陳先生' })}
        ${num('customer.age', '年齡', c.age, 18, 95)}
        ${text('customer.job', '職業', c.job, 30, { wide: true, ph: '例如：科技業工程師' })}
        ${avatarPick(c.avatar || 'chen')}
        ${area('customer.summary', '背景（學員看得到）', c.summary, 200, 2, { ph: '只寫表面狀況，不要寫隱藏資訊' })}
      </div>

      <h2 class="sec-title">情境</h2>
      <div class="form-grid">
        ${text('title', '標題', sc.title, 30, { wide: true, ph: '例如：市場大跌，客戶想停扣贖回' })}
        ${text('product', '商品', sc.product, 30, { ph: '例如：股票型基金定期定額' })}
        ${num('duration_min', '建議時間（分鐘）', sc.duration_min, 3, 30)}
        ${seg('difficulty_level', '難度', sc.difficulty_level || 2, DIFF, true)}
        ${seg('call_direction', '通話方向', sc.call_direction || 'incoming', DIRECTION, false)}
        <div class="field wide"><span>一開始的信任度</span><div class="range-row">
          <input type="range" min="0" max="100" step="1" value="${trust}" data-sync="initial_trust" aria-label="一開始的信任度">
          <input${attrs({ type: 'number', 'data-k': 'initial_trust', 'data-num': true, min: 0, max: 100, step: 1, value: trust, 'aria-label': '一開始的信任度數字' })}>
        </div></div>
        ${area('opening_line', '客戶第一句話', sc.opening_line, 150, 2, { ph: '電話接通後客戶說的第一句，例如：喂，{業務稱呼}嗎？', chips: true })}
        ${area('learner_task', '本次任務', sc.learner_task, 120, 2, { ph: '學員這通電話要做到的事' })}
        ${text('first_tip', '第一個提示', sc.first_tip, 40, { wide: true, ph: '例如：先同理客戶的情緒' })}
      </div>

      <details class="transcript-details hidden-box"${openHidden ? ' open' : ''}>
        <summary>隱藏設定（學員看不到）</summary>
        <p class="muted">只有 AI 客戶和 AI 教練看得到。存好之後這裡不會再顯示內容。</p>
        <div class="form-grid">${hidden}</div>
      </details>

      <h2 class="sec-title">檢核項目</h2>
      <p class="muted form-note">業務這通電話要問到或做到的事，AI 教練會即時打勾。</p>
      <div class="edit-list" data-list="checklist">${(sc.checklist || []).map(checkRow).join('')}</div>
      <button class="pill sm ghost edit-add" type="button" data-add="checklist">${icon('plus')}新增檢核項目</button>

      <h2 class="sec-title">示範句</h2>
      <p class="muted form-note">通話時點一下就能送出；紅點是違規示範。</p>
      <div class="edit-list" data-list="demo_lines">${(sc.demo_lines || []).map(demoRow).join('')}</div>
      <div class="ph-chips" data-for="demo_lines">
        <button class="pill sm ghost edit-add" type="button" data-add="demo_lines">${icon('plus')}新增示範句</button>
        ${TOKENS.map((t) => `<button class="pill sm ghost" type="button" data-tok="{${esc(t)}}" title="${esc(`通話時會換成「${phValue(t)}」`)}">{${esc(t)}}</button>`).join('')}
      </div>`;
    lastDemo = null;
    form().querySelectorAll('[data-k]').forEach(checkTokens);
  }

  // 列表增減後重新編號，錯誤訊息的 checklist.2.label 才對得到
  function reindex(list) {
    $$('.edit-item', list).forEach((row, i) => {
      $$('[data-k]', row).forEach((el) => { el.dataset.k = el.dataset.k.replace(/^(\w+)\.\d+/, `$1.${i}`); });
    });
  }

  // 有打錯的代稱（例如 {業務名}）就先標紅；存檔時伺服器也會再擋一次
  function checkTokens(el) {
    if (!('value' in el) || !meta.placeholders?.length) return;
    const ok = new Set(meta.placeholders.map((p) => phKey(p.key)));
    const bad = [...String(el.value).matchAll(BRACE)].some((m) => !ok.has(m[1]));
    el.classList.toggle('bad', bad);
    el.closest('.field')?.classList.toggle('bad', bad);
  }

  function insertToken(tok, target) {
    let el;
    if (target === 'demo_lines') {
      el = lastDemo && lastDemo.isConnected ? lastDemo : $$('[data-list="demo_lines"] input', form()).pop();
      if (!el) {
        addRow('demo_lines');
        el = $$('[data-list="demo_lines"] input', form()).pop();
      }
    } else {
      el = form().querySelector(`[data-k="${target}"]`);
    }
    if (!el) return;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? el.value.length;
    el.setRangeText(tok, start, end, 'end');
    el.focus();
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function addRow(kind) {
    const list = form().querySelector(`[data-list="${kind}"]`);
    const n = $$('.edit-item', list).length;
    if (n >= 10) { toast('最多 10 項', 'warn', 2500); return; }
    list.insertAdjacentHTML('beforeend', kind === 'checklist' ? checkRow({}, n) : demoRow('', n));
    list.lastElementChild.querySelector('input').focus();
    dirty = true;
  }

  function setPath(obj, path, v) {
    const keys = path.split('.');
    let o = obj;
    keys.slice(0, -1).forEach((k) => { if (!o[k] || typeof o[k] !== 'object') o[k] = {}; o = o[k]; });
    o[keys[keys.length - 1]] = v;
  }

  // 從畫面上的欄位組回整個情境；空白列不送
  function collect() {
    const sc = structuredClone(draft || blankScenario());
    const f = form();
    sc.server_only = {};
    $$('[data-k]', f).forEach((el) => {
      const k = el.dataset.k;
      if (/^(checklist|demo_lines)\./.test(k)) return;
      let v = el.matches('.seg, .av-pick') ? $('button.on', el)?.dataset.v ?? '' : el.value;
      if (el.dataset.num !== undefined) v = Number(v);
      else if (typeof v === 'string' && el.tagName === 'INPUT') v = v.trim();
      setPath(sc, k, v);
    });
    rowMap = { checklist: [], demo_lines: [] };
    sc.checklist = [];
    $$('[data-list="checklist"] .edit-item', f).forEach((row, i) => {
      const label = $('.ed-label', row).value.trim();
      const hint = $('.ed-hint', row).value.trim();
      if (!label && !hint) return;
      rowMap.checklist.push(i);
      sc.checklist.push({ id: row.dataset.id || '', label, hint });
    });
    sc.demo_lines = [];
    $$('[data-list="demo_lines"] input', f).forEach((el, i) => {
      const line = el.value.trim();
      if (!line) return;
      rowMap.demo_lines.push(i);
      sc.demo_lines.push(line);
    });
    return sc;
  }

  // 伺服器回的欄位（例如 checklist.1.label）找回畫面上那一格
  function findField(field) {
    const f = String(field || '');
    const m = f.match(/^(checklist|demo_lines)\.(\d+)(.*)$/);
    const k = m ? `${m[1]}.${rowMap[m[1]]?.[Number(m[2])] ?? m[2]}${m[3]}` : f;
    const root = form();
    const q = (sel) => { try { return root.querySelector(sel); } catch { return null; } };
    return q(`[data-k="${CSS.escape(k)}"]`) || q(`[data-k^="${CSS.escape(k)}."]`)
      || q(`[data-list="${CSS.escape(f.split('.')[0])}"]`) || q(`[data-add="${CSS.escape(f.split('.')[0])}"]`);
  }

  function markBad(el) {
    el.classList.add('bad');
    el.closest('.field')?.classList.add('bad');
    const det = el.closest('details');
    if (det) det.open = true;
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (typeof el.focus === 'function' && el.matches('input, textarea')) el.focus({ preventScroll: true });
  }

  function clearBad() {
    $$('.bad', form()).forEach((el) => { if (!el.classList.contains('d')) el.classList.remove('bad'); });
  }

  async function save() {
    if (saving || !draft) return;
    clearBad();
    const body = { id: editingId, copy_from: copyFrom, scenario: collect() };
    const btn = $('#btnEditorSave');
    saving = true;
    btn.disabled = true;
    try {
      const d = await api('/api/editor/save', body);
      dirty = false;
      await reloadScenarios().catch(() => {});
      const warn = (d.warnings || []).filter(Boolean);
      toast(warn.length ? warn.map(esc).join('<br>') : '首頁已經可以練習這個情境', warn.length ? 'warn' : 'info', warn.length ? 7000 : 3000, '已儲存');
      go('scenarios');
    } catch (err) {
      const el = err.data?.field ? findField(err.data.field) : null;
      if (el) markBad(el);
      toast(esc(err.message), 'error', 7000, '還不能儲存');
    } finally {
      saving = false;
      btn.disabled = false;
    }
  }

  async function aiDraft() {
    const desc = $('#aiDesc').value.trim();
    if (desc.length < 10) {
      toast('請用一段話描述客戶和狀況（至少 10 個字）', 'warn', 3500);
      $('#aiDesc').focus();
      return;
    }
    if (dirty && !window.confirm('AI 寫好會取代目前表單的內容，要繼續嗎？')) return;
    const myGen = gen;
    const btn = $('#btnAiDraft');
    const note = $('#aiNote');
    btn.disabled = true;
    btn.textContent = 'AI 撰寫中…';
    note.textContent = state.providerLabel.startsWith('本機') ? '本機模型大約要 30～90 秒' : '大約要 5～20 秒';
    try {
      const d = await api('/api/editor/draft', { description: desc });
      if (myGen !== gen || !$('#view-editor').classList.contains('active')) return; // 已經離開編輯器
      draft = d.scenario || blankScenario();
      delete draft.hidden;
      delete draft.source;
      renderForm(draft, { openHidden: true });
      dirty = true;
      const warn = (d.warnings || []).filter(Boolean);
      toast(`由 ${esc(d.model || 'AI')} 撰寫，請檢查後再儲存${warn.length ? `<br>${warn.map(esc).join('<br>')}` : ''}`, 'info', 6000, 'AI 已填好');
    } catch (err) {
      if (myGen === gen) toast(esc(err.message), 'error', 7000, 'AI 沒有寫好');
    } finally {
      btn.disabled = false;
      btn.textContent = '用 AI 幫我寫';
      note.textContent = 'AI 會把整個情境填好，你再修改。';
    }
  }

  function close() {
    gen += 1;
    draft = null;
    dirty = false;
    editingId = '';
    copyFrom = '';
    hiddenLen = {};
    lastDemo = null;
    form().innerHTML = '';
    $('#aiDesc').value = '';
  }

  // ---- 事件（表單內容每次重畫，所以綁在外層）

  form().addEventListener('input', (e) => {
    const el = e.target;
    dirty = true;
    if (el.dataset.sync) {
      const n = form().querySelector(`[data-k="${el.dataset.sync}"]`);
      if (n) n.value = el.value;
      return;
    }
    if (el.dataset.k === 'initial_trust') {
      const r = form().querySelector('[data-sync="initial_trust"]');
      if (r) r.value = el.value;
    }
    el.classList.remove('bad');
    el.closest('.field')?.classList.remove('bad');
    checkTokens(el);
    if (el.closest('[data-list="demo_lines"]')) {
      el.closest('.edit-item').querySelector('.d').classList.toggle('bad', checkCompliance(el.value).length > 0);
    }
  });

  form().addEventListener('focusin', (e) => {
    if (e.target.closest('[data-list="demo_lines"]') && e.target.matches('input')) lastDemo = e.target;
  });

  form().addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || !form().contains(b)) return;
    if (b.dataset.tok) {
      insertToken(b.dataset.tok, b.closest('[data-for]')?.dataset.for || '');
      return;
    }
    if (b.dataset.add) { addRow(b.dataset.add); return; }
    if (b.hasAttribute('data-del')) {
      const list = b.closest('.edit-list');
      b.closest('.edit-item').remove();
      reindex(list);
      dirty = true;
      return;
    }
    const group = b.closest('.seg, .av-pick');
    if (group && b.dataset.v !== undefined) {
      $$('button', group).forEach((x) => x.classList.toggle('on', x === b));
      group.classList.remove('bad');
      group.closest('.field')?.classList.remove('bad');
      if (group.matches('.av-pick')) $('#avGender').textContent = genderOf(b.dataset.v) === 'female' ? '女' : '男';
      dirty = true;
    }
  });

  $('#btnNewScenario').addEventListener('click', () => open(''));
  $('#btnEditorSave').addEventListener('click', save);
  $('#btnEditorCancel').addEventListener('click', () => go('scenarios'));
  $('#btnEditorBack').addEventListener('click', () => go('scenarios'));
  $('#btnAiDraft').addEventListener('click', aiDraft);

  return { renderList, open, close, get dirty() { return dirty; } };
})();

// ------------------------------------------------------------------ 電話鈴聲（Web Audio 合成）

const Ring = (() => {
  let ctx = null;
  let timer = 0;
  let stopAt = 0;
  function beep(freqs, dur, when = 0) {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    const t = ctx.currentTime + when;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.05, t + 0.03);
    g.gain.setValueAtTime(0.05, t + dur - 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    g.connect(ctx.destination);
    freqs.forEach((f) => {
      const o = ctx.createOscillator();
      o.frequency.value = f;
      o.connect(g);
      o.start(t);
      o.stop(t + dur + 0.02);
    });
  }
  function start(kind) {
    stop();
    const incoming = kind === 'incoming';
    const play = () => {
      try {
        if (incoming) { beep([440, 480], 0.4); beep([440, 480], 0.4, 0.55); } else { beep([440, 480], 1.4); }
      } catch { /* 沒有音效裝置就安靜 */ }
    };
    play();
    timer = setInterval(play, incoming ? 2600 : 3000);
    stopAt = setTimeout(stop, 20000);
  }
  function stop() { clearInterval(timer); clearTimeout(stopAt); }
  return { start, stop };
})();

// ------------------------------------------------------------------ 文字處理

const HAS_WORDS = /[\p{L}\p{N}]/u;

function splitSentences(text) {
  const parts = [];
  let buf = '';
  const chars = [...text];
  for (let i = 0; i < chars.length; i += 1) {
    const ch = chars[i];
    buf += ch;
    const next = chars[i + 1];
    // 「……」「？！」這類連續標點要跟前一句黏在一起，不要自己變成一句
    if (next && (BOUNDARY.includes(next) || '」』）)'.includes(next))) continue;
    if (BOUNDARY.includes(ch) || (buf.length > 38 && ',，、'.includes(ch))) {
      if (HAS_WORDS.test(buf)) parts.push(buf.trim());
      else if (parts.length) parts[parts.length - 1] += buf.trim();
      buf = '';
    }
  }
  if (HAS_WORDS.test(buf)) parts.push(buf.trim());
  return parts;
}

// 串流時把已經完整的句子先交給語音，剩下的留著等下一段
function takeSentences(buf) {
  let last = -1;
  for (let i = 0; i < buf.length; i += 1) if (BOUNDARY.includes(buf[i])) last = i;
  if (last < 0 && buf.length > 40) {
    const c = Math.max(buf.lastIndexOf('，'), buf.lastIndexOf(','));
    if (c > 10) last = c;
  }
  if (last < 0) return [[], buf];
  return [splitSentences(buf.slice(0, last + 1)), buf.slice(last + 1)];
}

function cleanReply(t) {
  const name = state.scenario?.customer?.name || '客戶';
  return String(t)
    .replace(/<\/?think>/g, '')
    .replace(/[*#`_]/g, '')
    .replace(new RegExp(`^\\s*(?:${escapeRe(name)}|客戶)\\s*[:：]\\s*`), '')
    .replace(/^\s+/, '');
}

function speakable(t) {
  return cleanReply(t)
    .replace(/[（(][^）)]{0,14}[）)]/g, '')
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// ------------------------------------------------------------------ 客戶語音：瀏覽器內建（免費備援）

const BrowserVoice = (() => {
  const synth = window.speechSynthesis;
  let voice = null;
  let profile = {};
  let pending = 0;
  let streamOpen = false;
  let onDone = null;
  let gen = 0;
  const live = new Set(); // 保留參照，避免 Chrome 回收 utterance 後不觸發 onend

  function pickVoice(gender) {
    if (!synth) return null;
    const all = synth.getVoices();
    const tw = all.filter((v) => /zh[-_]TW/i.test(v.lang) || /Taiwan|臺灣|台灣/i.test(v.name));
    const pool = tw.length ? tw : all.filter((v) => /^zh/i.test(v.lang));
    if (!pool.length) return null;
    const prefs = gender === 'male'
      ? ['YunJhe', 'Zhiwei']
      : (profile.pitch || 1) < 1 ? ['HsiaoChen', 'HsiaoYu', 'Yating', 'Hanhan'] : ['HsiaoYu', 'HsiaoChen', 'Hanhan', 'Yating'];
    const score = (v) => {
      let s = /Natural|Online/i.test(v.name) ? 10 : 0;
      const idx = prefs.findIndex((n) => v.name.includes(n));
      if (idx >= 0) s += 20 - idx;
      if (gender === 'male' && /Hsiao|Yating|Hanhan/.test(v.name)) s -= 15;
      if (gender === 'female' && /YunJhe|Zhiwei/.test(v.name)) s -= 15;
      return s;
    };
    return pool.slice().sort((a, b) => score(b) - score(a))[0];
  }

  function configure(customer) {
    profile = customer.voice || {};
    voice = pickVoice(profile.gender);
    return voice;
  }

  function check() {
    if (pending === 0 && !streamOpen && onDone) {
      const cb = onDone;
      onDone = null;
      cb();
    }
  }

  function enqueue(text) {
    const t = speakable(text);
    if (!t || !synth) return;
    // Edge 的自然語音常在頁面載入後才出現，有更好的聲音就換過去
    if (!voice || !/Natural|Online/i.test(voice.name)) voice = pickVoice(profile.gender) || voice;
    const u = new SpeechSynthesisUtterance(t);
    u.lang = voice ? voice.lang : 'zh-TW';
    if (voice) u.voice = voice;
    // 調音高會讓瀏覽器語音更不自然，只保留些微語速差異
    u.rate = Math.min(1.08, Math.max(0.95, profile.rate || 1));
    u.pitch = 1;
    showVoiceName();
    const myGen = gen;
    let finished = false;
    let guard = 0;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(guard);
      live.delete(u);
      if (myGen !== gen) return;
      pending = Math.max(0, pending - 1);
      check();
    };
    guard = setTimeout(finish, 3000 + t.length * 400);
    u.onstart = () => {
      if (myGen !== gen) return;
      setCustomerState('speaking');
      setCaption(t);
    };
    u.onend = finish;
    u.onerror = finish;
    pending += 1;
    live.add(u);
    synth.speak(u);
  }

  function begin(cb) { streamOpen = true; onDone = cb; }
  function end() { streamOpen = false; check(); }
  function say(text, cb) { begin(cb); splitSentences(text).forEach(enqueue); end(); }
  function cancel() {
    gen += 1;
    onDone = null;
    streamOpen = false;
    pending = 0;
    live.clear();
    if (synth) synth.cancel();
  }

  // 付費語音某一句失敗時，用瀏覽器聲音補念，念完才繼續
  function speakOnce(text) {
    return new Promise((resolve) => {
      const t = speakable(text);
      if (!t || !synth) { resolve(); return; }
      if (!voice) voice = pickVoice(profile.gender);
      const u = new SpeechSynthesisUtterance(t);
      u.lang = voice ? voice.lang : 'zh-TW';
      if (voice) u.voice = voice;
      live.add(u);
      const done = () => { clearTimeout(guard); live.delete(u); resolve(); };
      const guard = setTimeout(done, 3000 + t.length * 400);
      u.onend = done;
      u.onerror = done;
      synth.speak(u);
    });
  }

  return {
    configure, enqueue, begin, end, say, cancel, speakOnce,
    hasVoice: () => !!(synth && (voice || pickVoice(profile.gender))),
    get voiceName() { return voice ? voice.name.replace(/^Microsoft\s+/, '').replace(/\s+-\s+.*$/, '') : '瀏覽器預設'; },
  };
})();

// 客戶頭像的嘴巴跟著聲音大小動；回傳停止用的函式
function animateMouth(analyser) {
  const tile = $('#avatarStage');
  const buf = new Uint8Array(analyser.fftSize);
  let smooth = 0;
  let raf = 0;
  const loop = () => {
    analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i += 1) { const x = (buf[i] - 128) / 128; sum += x * x; }
    const level = Math.min(1, Math.sqrt(sum / buf.length) * 6);
    smooth = smooth * 0.55 + level * 0.45;
    tile.style.setProperty('--mouth', (0.2 + 0.8 * smooth).toFixed(3));
    tile.style.setProperty('--level', smooth.toFixed(3));
    tile.classList.toggle('mouth-on', smooth > 0.07);
    raf = requestAnimationFrame(loop);
  };
  loop();
  return () => {
    cancelAnimationFrame(raf);
    tile.classList.remove('mouth-on');
    tile.style.setProperty('--mouth', '0.2');
    tile.style.setProperty('--level', '0');
  };
}

// ------------------------------------------------------------------ 客戶語音：付費 AI 語音（ElevenLabs／Gemini／雅婷）

const ServerVoice = (() => {
  let ctx = null;
  let audio = null;
  let analyser = null;
  let stopMouthLoop = null;
  let avatar = '';
  let queue = [];
  let playing = false;
  let streamOpen = false;
  let onDone = null;
  let gen = 0;

  // 必須在使用者點擊時呼叫一次，瀏覽器才允許播放聲音
  function unlock() {
    try {
      if (!ctx) {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
        audio = new Audio();
        const src = ctx.createMediaElementSource(audio);
        analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        src.connect(analyser);
        analyser.connect(ctx.destination);
      }
      if (ctx.state === 'suspended') ctx.resume();
    } catch {
      if (!audio) audio = new Audio();
    }
  }

  function configure(customer) { avatar = customer.avatar; }

  function onError(err) {
    if (state.ttsBroken) return;
    state.ttsBroken = true;
    toast(`${esc(err.message)}<br>這通電話先改用瀏覽器內建聲音。`, 'warn', 8000, '付費語音暫時不能用');
  }

  // 一收到句子就先去合成，前一句還在播時下一句已經準備好
  function prefetch(text) {
    const myGen = gen;
    const item = { text, ctrl: new AbortController(), error: null };
    item.promise = request('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ avatar, text }),
      signal: item.ctrl.signal,
    }).then(async (r) => {
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.error || httpError(r.status));
      }
      return r.blob();
    }).catch((err) => {
      if (err.name !== 'AbortError') {
        item.error = err;
        if (myGen === gen) onError(err);
      }
      return null;
    });
    return item;
  }

  // 來電響鈴時先合成開場白，接起來就能馬上講
  const preloaded = new Map();
  function preload(text) {
    const t = speakable(text);
    if (t && HAS_WORDS.test(t)) preloaded.set(t, prefetch(t)); // 每次響鈴都重拿，換過聲音也不會播到舊的
  }

  function check() {
    if (!playing && !queue.length && !streamOpen && onDone) {
      const cb = onDone;
      onDone = null;
      cb();
    }
  }

  async function playNext() {
    const myGen = gen;
    const item = queue.shift();
    if (!item) {
      playing = false;
      stopMouth();
      check();
      return;
    }
    playing = true;
    const blob = await item.promise;
    if (myGen !== gen) return;
    if (!blob) {
      if (item.error) onError(item.error); // 響鈴時先準備的那段失敗，接通後才告訴使用者
      setCustomerState('speaking');
      setCaption(item.text);
      $('#avatarStage').classList.remove('audio-live');
      await BrowserVoice.speakOnce(item.text);
      if (myGen !== gen) return;
      $('#avatarStage').classList.toggle('audio-live', !!analyser);
      playNext();
      return;
    }
    const url = URL.createObjectURL(blob);
    const next = () => {
      URL.revokeObjectURL(url);
      if (myGen === gen) playNext();
    };
    audio.onended = next;
    audio.onerror = next;
    audio.src = url;
    try {
      await audio.play();
    } catch {
      next();
      return;
    }
    if (myGen !== gen) return;
    setCustomerState('speaking');
    setCaption(item.text);
    showVoiceName();
    startMouth();
  }

  function enqueue(text) {
    const t = speakable(text);
    if (!t || !HAS_WORDS.test(t)) return;
    if (!audio) unlock();
    const ready = preloaded.get(t);
    preloaded.delete(t);
    queue.push(ready || prefetch(t));
    if (!playing) playNext();
  }

  function startMouth() {
    if (analyser && !stopMouthLoop) stopMouthLoop = animateMouth(analyser);
  }

  function stopMouth() {
    if (stopMouthLoop) stopMouthLoop();
    stopMouthLoop = null;
  }

  function begin(cb) { streamOpen = true; onDone = cb; }
  function end() { streamOpen = false; check(); }
  // 開場白這種整段已知的話，整段只送一次（付費語音常按次數算額度），伺服器也會存起來重複使用
  function say(text, cb) { begin(cb); enqueue(text); end(); }
  function cancel() {
    gen += 1;
    queue.forEach((i) => i.ctrl.abort());
    queue = [];
    playing = false;
    streamOpen = false;
    onDone = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    stopMouth();
  }

  return {
    unlock, configure, preload, enqueue, begin, end, say, cancel,
    canAnalyse: () => !!analyser,
    get voiceName() {
      const t = state.tts;
      const label = t?.providers.find((p) => p.id === t.provider)?.label || 'AI 語音';
      const name = t?.customers.find((c) => c.avatar === avatar)?.voice?.name;
      return `${label} · ${name || '自動挑選'}`;
    },
  };
})();

// 依設定選用付費語音或瀏覽器語音；其他程式只跟這個物件打交道
const Speech = {
  mode: 'browser',
  engine: BrowserVoice,
  use(mode) {
    this.mode = mode;
    this.engine = mode === 'server' ? ServerVoice : BrowserVoice;
    $('#avatarStage').classList.toggle('audio-live', mode === 'server' && ServerVoice.canAnalyse());
  },
  configure(customer) {
    BrowserVoice.configure(customer);
    ServerVoice.configure(customer);
    return this.mode === 'server' || BrowserVoice.hasVoice();
  },
  begin(cb) { this.engine.begin(cb); },
  end() { this.engine.end(); },
  enqueue(text) { this.engine.enqueue(text); },
  say(text, cb) { this.engine.say(text, cb); },
  cancel() { ServerVoice.cancel(); BrowserVoice.cancel(); },
  get voiceName() { return this.engine.voiceName; },
};

function showVoiceName() {
  const el = $('#voiceName');
  if (el) el.textContent = state.liveCall ? LiveCall.voiceName : Speech.voiceName;
}

if (window.speechSynthesis) {
  speechSynthesis.getVoices();
  speechSynthesis.addEventListener?.('voiceschanged', () => { if (state.scenario) BrowserVoice.configure(state.scenario.customer); });
}

// ------------------------------------------------------------------ 客戶狀態

const STATE_TEXT = {
  idle: '換你說',
  speaking: '客戶說話中',
  thinking: '客戶想一下…',
  listening: '客戶在聽你說',
  connecting: '接通中…',
  ended: '通話已結束',
};

function setCustomerState(s) {
  const orb = $('#avatarStage');
  orb.classList.toggle('speaking', s === 'speaking');
  orb.classList.toggle('thinking', s === 'thinking' || s === 'connecting');
  orb.classList.toggle('listening', s === 'listening');
  $('#custState').dataset.state = s;
  $('#custStateText').textContent = STATE_TEXT[s] || '';
}

// 麥克風按鈕：off 沒開、listening 一般模式收音中、live 即時對話開麥、muted 即時對話靜音
const MIC_LABEL = { off: '開始說話', listening: '收音中，點一下送出', live: '麥克風開著，點一下靜音', muted: '已靜音，點一下開麥' };
const MIC_STATE = { off: '麥克風沒開 · 可以打字', live: '麥克風開著，隨時可以插話', muted: '已靜音 · 客戶聽不到你' };

function setMicUi(mode) {
  const b = $('#btnMic');
  b.classList.toggle('on', mode === 'listening' || mode === 'live');
  b.classList.toggle('muted', mode === 'muted');
  b.setAttribute('aria-pressed', String(mode === 'muted'));
  b.setAttribute('aria-label', MIC_LABEL[mode] || MIC_LABEL.off);
  // 只換圖示，保留外圈的音量動畫 .mic-level
  const want = mode === 'muted' ? 'micOff' : 'mic';
  const ic = $('.ic', b);
  if (ic && b.dataset.ic !== want) {
    ic.outerHTML = icon(want);
    b.dataset.ic = want;
  }
  const st = $('#micState');
  st.textContent = state.liveCall ? MIC_STATE[mode] || '' : '';
  st.classList.toggle('muted', mode === 'muted');
}

// 通話畫面中間的即時字幕：顯示客戶正在說的那一句
function setCaption(text, past = false) {
  const el = $('#caption');
  if (text !== undefined) el.textContent = text;
  el.classList.toggle('past', past);
}

// ------------------------------------------------------------------ 業務語音（瀏覽器內建語音辨識）

const Mic = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  let active = false;
  let finalText = '';
  let interim = '';
  let timer = 0;
  let media = null;
  let audioCtx = null;
  let raf = 0;
  let levelGen = 0; // 每次開、關音量動畫就 +1：晚到的麥克風知道自己已經過期

  function start() {
    if (state.ended || active) return;
    if (!SR) {
      toast('這個瀏覽器不支援語音辨識，請用 Edge 或 Chrome，或直接打字。', 'warn', 5000);
      $('#textInput').focus();
      return;
    }
    interruptCustomer();
    finalText = '';
    interim = '';
    state.hitWords.clear();
    rec = new SR();
    rec.lang = 'zh-TW';
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      if (!active) return;
      let tmp = '';
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript; else tmp += r[0].transcript;
      }
      previewCompliance(finalText); // 只看瀏覽器已確定的字，避免暫定結果亂跳提醒
      interim = tmp;
      showInterim(finalText + interim);
      clearTimeout(timer);
      timer = setTimeout(commit, SILENCE_MS);
    };
    rec.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      const msg = {
        'not-allowed': '麥克風權限被擋住了，請點網址列左邊的圖示允許麥克風。',
        'service-not-allowed': '瀏覽器不允許語音辨識，請改用 Chrome，或直接打字。',
        network: '語音辨識連不上網路（瀏覽器的辨識服務需要網路），請先用打字。',
        'audio-capture': '找不到麥克風，請確認有接上。',
        'language-not-supported': '這個瀏覽器不支援台灣華語辨識，請改用 Chrome。',
      }[e.error];
      toast(esc(msg || `語音辨識錯誤：${e.error}`), 'warn', 6000);
      stop();
    };
    // 瀏覽器安靜一段時間會自動停止辨識，通話中就自動重開
    rec.onend = () => { if (active) { try { rec.start(); } catch { stop(); } } };
    try {
      rec.start();
    } catch (err) {
      toast(`無法啟動語音辨識：${esc(err.message)}`, 'warn');
      return;
    }
    active = true;
    setMicUi('listening');
    setCustomerState('listening');
    showInterim('');
    startLevel();
  }

  function commit() {
    const text = (finalText + interim).trim();
    stop();
    if (text) sendRep(text);
  }

  function stop() {
    clearTimeout(timer);
    const wasActive = active;
    active = false;
    if (rec) {
      try { rec.abort(); } catch { /* 已停止 */ }
      rec = null;
    }
    stopLevel();
    showInterim('');
    if (!wasActive) return; // 沒在收音就不動按鈕（即時對話的麥克風狀態由 LiveCall 管）
    setMicUi('off');
    if (!state.ended) setCustomerState('idle');
  }

  function toggle() { if (active) commit(); else start(); }

  async function startLevel() {
    const my = ++levelGen;
    let m = null;
    let ctx = null;
    try {
      m = await navigator.mediaDevices.getUserMedia({ audio: true });
      // 等權限的時候已經停了或又重開：這次拿到的直接關掉，不然瀏覽器的麥克風燈會一直亮
      if (my !== levelGen || !active) throw new Error('stale');
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(m).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      const loop = () => {
        if (my !== levelGen) return;
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) { const x = (v - 128) / 128; sum += x * x; }
        $('#btnMic').style.setProperty('--lvl', Math.min(1, Math.sqrt(sum / buf.length) * 5).toFixed(3));
        raf = requestAnimationFrame(loop);
      };
      if (media) media.getTracks().forEach((t) => t.stop());
      if (audioCtx) audioCtx.close().catch(() => {});
      media = m;
      audioCtx = ctx;
      loop();
    } catch {
      // 音量動畫失敗不影響辨識；沒用上的麥克風要還回去
      if (m && media !== m) m.getTracks().forEach((t) => t.stop());
      if (ctx && audioCtx !== ctx) ctx.close().catch(() => {});
    }
  }

  function stopLevel() {
    levelGen += 1;
    cancelAnimationFrame(raf);
    if (media) { media.getTracks().forEach((t) => t.stop()); media = null; }
    if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; }
    $('#btnMic').style.setProperty('--lvl', 0);
  }

  return { start, stop, toggle, supported: () => !!SR, get active() { return active; } };
})();

function showInterim(text) { $('#interim').textContent = text; }

// ------------------------------------------------------------------ 即時語音通話（Gemini Live）：聽、想、講一次包辦，業務可以隨時插話

// 麥克風的聲音降到 16kHz、轉成 16-bit PCM，每 40ms 交出一段（在音訊執行緒跑，不會卡畫面）
const PCM16_WORKLET = `
class Pcm16 extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.pos = 0;
    this.out = new Int16Array(640);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (; this.pos < ch.length; this.pos += this.step) {
      const i = Math.floor(this.pos);
      const s = i + 1 < ch.length ? ch[i] + (ch[i + 1] - ch[i]) * (this.pos - i) : ch[i];
      this.out[this.n++] = Math.max(-1, Math.min(1, s)) * 32767;
      if (this.n === this.out.length) {
        this.port.postMessage(this.out.slice(0));
        this.n = 0;
      }
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor('pcm16', Pcm16);
`;

// Gemini 的逐字稿偶爾會混進別種文字（例如把「已經」寫成希伯來文），只留中英文、數字和標點
const STRAY_SCRIPT = /[Ͱ-ϿЀ-ԯ֐-׿؀-ۿऀ-ॿ฀-๿぀-ヿ가-힯]/g;
const CJK_GAP = /([　-〿㐀-鿿＀-￯])\s+(?=[　-〿㐀-鿿＀-￯])/g;

function liveText(t) {
  return t.replace(STRAY_SCRIPT, '').replace(/([㐀-鿿])\s*,\s*/g, '$1，').replace(CJK_GAP, '$1');
}

const VOICE_RMS = 0.012; // 音量超過這個才算在講話（瀏覽器已經先做過降噪）
// 講完之後再多送 2 秒：Gemini Live 要自己聽到一段安靜才會判定講完、開始回話（只送 audioStreamEnd 它不會接話，實測過）
const VOICE_HANG_MS = 2000;
const VOICE_PREROLL = 8; // 開口前多送幾段（每段 40ms）

const LiveCall = (() => {
  let scenario = null;
  let ws = null;
  let pending = null; // 響鈴時就先連線，接起來客戶馬上能講
  let gen = 0;
  let handle = ''; // 連線大約 10 分鐘會被重置，拿它接回同一通電話
  let retries = 0;
  let active = false;
  let muted = false;
  let voice = '';
  let mic = null;
  let out = null;
  let sources = [];
  let playhead = 0;
  let stopMouthLoop = null;
  let rep = ''; // 業務正在講的這一句
  let lastRep = null; // 上一句業務的話：逐字稿晚到時補進去 { msg, el, raw, at, coachLen }
  let cust = null; // 客戶正在講的這一句 { text, el, start, end }
  let gate = { open: false, lastLoud: 0, pre: [] }; // 麥克風的「有沒有在講話」判斷
  let repIdle = 0; // 業務停下來一小段就先定案這句
  let reviseTimer = 0; // 逐字稿補了尾巴，稍等一下再請教練重看
  let dropStale = false; // 打字打斷客戶後，舊那段回話還在路上的聲音和字幕要丟掉
  let staleTimer = 0;
  let micGen = 0; // 每次開、關麥克風就 +1：晚到的麥克風知道自己已經過期

  // 業務講完、逐字稿也停了，就先把這句放進逐字稿（不用等客戶開口），教練和合規檢查可以早點開始
  function commitSoon(ms = 350) {
    clearTimeout(repIdle);
    repIdle = setTimeout(() => { if (rep && !cust && !gate.open) commitRep(); }, ms);
  }

  function toB64(buffer) {
    const bytes = new Uint8Array(buffer);
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function sendTurn(text) {
    send({ clientContent: { turns: [{ role: 'user', parts: [{ text }] }], turnComplete: true } });
  }

  function reason(e) {
    const r = e.reason || '';
    if (/quota|exhaust|rate.?limit|exceeded/i.test(r)) return 'Gemini 的用量到上限了，請稍後再試，或到 AI Studio 開啟付費';
    return r ? `Gemini 中斷連線：${r}` : `Gemini 中斷連線（代碼 ${e.code}）`;
  }

  // 伺服器發一張短效通行證（客戶的隱藏設定鎖在裡面），瀏覽器拿它直接連 Gemini
  async function open(resume) {
    const t = await api('/api/live/token', { scenario_id: scenario.id, handle: resume ? handle : '' });
    voice = t.voice || voice;
    return new Promise((resolve, reject) => {
      const sock = new WebSocket(`${t.url}?access_token=${encodeURIComponent(t.token)}`);
      sock.binaryType = 'arraybuffer';
      const dec = new TextDecoder();
      let ready = false;
      sock.onopen = () => sock.send(JSON.stringify({ setup: { model: `models/${t.model}` } }));
      sock.onmessage = (e) => {
        let m;
        try { m = JSON.parse(typeof e.data === 'string' ? e.data : dec.decode(e.data)); } catch { return; }
        if (m.setupComplete && !ready) { ready = true; resolve(sock); return; }
        if (sock === ws) onMessage(m);
      };
      sock.onclose = (e) => {
        if (!ready) reject(new Error(reason(e)));
        else if (sock === ws) onClosed(e);
      };
    });
  }

  function connect() {
    const myGen = gen;
    pending = open(false).then((sock) => {
      if (myGen !== gen) { sock.close(1000); throw new Error('cancelled'); }
      ws = sock;
      return sock;
    });
    // 接聽時再告訴使用者；響鈴時就失敗的，接聽時重連一次
    pending.catch(() => { if (myGen === gen && !active) pending = null; });
  }

  function prepare(s) {
    stop();
    scenario = s;
    handle = '';
    retries = 0;
    voice = '';
    connect();
  }

  async function start() {
    if (!pending || scenario !== state.scenario) prepare(state.scenario);
    active = true;
    muted = false;
    const myGen = gen;
    outCtx(); // 在使用者按下接聽的這一刻建立，瀏覽器才允許出聲
    setCustomerState('connecting');
    try {
      await pending;
      if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error('連線已經斷了');
    } catch (err) {
      if (!active || myGen !== gen) return;
      fallBack(`${esc(err.message)}<br>這通改用一般模式（講完停一下才會送出）`, '即時對話連不上');
      return;
    }
    if (!active || myGen !== gen) return;
    sendTurn('（電話接通）');
    setCustomerState('thinking');
    showVoiceName();
    try {
      await startMic();
      if (myGen !== gen) return;
      setMicUi(mic ? 'live' : 'off');
    } catch {
      if (myGen !== gen) return;
      setMicUi('off');
      toast('麥克風打不開，請點網址列左邊的圖示允許麥克風，或先用打字。', 'warn', 7000);
    }
  }

  // 連不上或斷線接不回：收掉即時對話，這通改用一般模式（打字和講話都改走 /api/chat）
  function fallBack(html, title) {
    stop();
    toast(html, 'warn', 9000, title);
    switchToNormalCall();
  }

  function endStale() {
    dropStale = false;
    clearTimeout(staleTimer);
  }

  function onMessage(m) {
    const up = m.sessionResumptionUpdate;
    if (up?.resumable && up.newHandle) handle = up.newHandle;
    const sc = m.serverContent;
    if (!sc || !active) return;
    // 打字打斷客戶後，舊那段回話的聲音和字幕還會晚到：等到 interrupted 或 turnComplete 之前都丟掉
    const stale = dropStale;
    if (stale && (sc.interrupted || sc.turnComplete)) endStale();
    if (sc.interrupted && !stale) cut(); // 業務開口了：客戶馬上停
    if (sc.inputTranscription?.text) heardRep(sc.inputTranscription.text);
    if (stale) return;
    (sc.modelTurn?.parts || []).forEach((p) => { if (p.inlineData?.data) play(p.inlineData.data); });
    if (sc.outputTranscription?.text) custSays(sc.outputTranscription.text);
    if (sc.turnComplete) custDone(false);
  }

  async function onClosed(e) {
    ws = null;
    if (!active) {
      // 響鈴時先開好的連線被關了：接聽時重新連
      pending = null;
      handle = '';
      return;
    }
    const myGen = gen;
    if (handle && retries < 3) {
      retries += 1;
      try {
        const sock = await open(true);
        if (myGen !== gen || !active) { sock.close(1000); return; }
        // 舊連線上客戶講到一半的那句不會有結尾了：先收掉（狀態也換回「在聽你說」），兩邊從頭開始
        if (cust) cut();
        endStale();
        gate = { open: false, lastLoud: 0, pre: [] };
        ws = sock;
        retries = 0;
        return;
      } catch { /* 接不回去就改用一般模式 */ }
    }
    if (myGen !== gen || !active) return;
    fallBack(`${esc(reason(e))}<br>接不回去，這通改用一般模式（講完停一下才會送出）`, '通話中斷');
  }

  function heardRep(t) {
    const now = performance.now();
    const text = liveText(t);
    if (!text.trim()) return;
    // 業務這一刻有沒有在出聲（gate.open 在講完後還會多開 2 秒，不能拿來判斷）
    const talking = now - gate.lastLoud < 400;
    // 1. 剛定案那句的尾巴晚到：接回同一個泡泡（業務正在插話打斷客戶時除外）
    if (!rep && lastRep && now - lastRep.at < 2000 && !(cust && talking)) {
      lastRep.raw += t;
      lastRep.at = now;
      reviseRep(lastRep, liveText(lastRep.raw).trim());
      // 多了幾個字就請教練重看這一句（同一句的信任度變化會取代，不會重複加）
      if (state.coachParallel && lastRep.msg.text.length - lastRep.coachLen >= 3) {
        lastRep.coachLen = lastRep.msg.text.length;
        const turn = lastRep.msg.turn;
        clearTimeout(reviseTimer);
        reviseTimer = setTimeout(() => runObserver(turn), 600);
      }
      return;
    }
    // 2. 客戶已經開始回話才收到這句：另開一個泡泡放在客戶那句前面，不黏到更早的泡泡
    if (!rep && cust && !talking) {
      const e = recordRep(text.trim());
      cust.el.before(e.el);
      lastRep = { ...e, raw: t, at: now, coachLen: e.msg.text.length };
      return;
    }
    // 3. 一般講話和插話
    rep += t;
    showInterim(liveText(rep));
    if (!cust || talking) setCustomerState('listening');
    previewCompliance(liveText(rep));
    if (!gate.open) commitSoon();
  }

  function commitRep() {
    clearTimeout(repIdle);
    const raw = rep;
    const text = liveText(raw).trim();
    rep = '';
    showInterim('');
    if (text) lastRep = { ...recordRep(text), raw, at: performance.now(), coachLen: text.length };
  }

  function custTurn() {
    commitRep();
    if (!cust) {
      cust = { text: '', el: addBubble('customer', '', null, true), start: 0, end: 0 };
      setCustomerState('speaking');
    }
    return cust;
  }

  function custSays(t) {
    const c = custTurn();
    c.text += t;
    setBubbleText(c.el, liveText(c.text));
    setCaption(liveText(c.text));
  }

  function custDone(interrupted) {
    const c = cust;
    if (!c) return;
    cust = null;
    let text = liveText(c.text).trim();
    if (interrupted && text && c.start) {
      // 字幕比聲音早到很多：用大約每秒 6 個字估整句要講多久，只留下業務真的聽到的部分
      const chars = [...text];
      const total = Math.max(c.end - c.start, chars.length / 6);
      const heard = Math.min(1, Math.max(0, (outCtx().currentTime - c.start) / total));
      text = `${chars.slice(0, Math.max(1, Math.round(chars.length * heard))).join('')}…`;
    }
    if (text) {
      state.messages.push({ role: 'customer', text });
      setBubbleText(c.el, text);
      setCaption(text);
    } else {
      c.el.remove();
      updateTurnCount();
    }
    lastRep = null;
    // 教練在業務那句定案時就開始跑；這裡只補看沒看成的句子
    retryCoach();
  }

  function cut() {
    custDone(true);
    stopPlayback();
    setCustomerState('listening');
  }

  // 業務用打字：客戶一樣會被打斷
  function sendText(text) {
    if (!active || !ws || ws.readyState !== WebSocket.OPEN) {
      // 還在接通或正在重新連線：這句先不送，放回輸入框
      if (!$('#textInput').value.trim()) $('#textInput').value = text;
      toast('即時對話還在連線，這句還沒送出，等一下再送。', 'warn', 4000);
      return;
    }
    if (cust) {
      dropStale = true;
      clearTimeout(staleTimer);
      staleTimer = setTimeout(endStale, 3000); // 保險：一直等不到結束訊號，也不會把之後的回話丟掉
    }
    cut();
    commitRep();
    recordRep(text);
    lastRep = null;
    sendTurn(text);
    setCustomerState('thinking');
  }

  function outCtx() {
    if (!out) {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      analyser.connect(ctx.destination);
      out = { ctx, analyser };
    }
    if (out.ctx.state === 'suspended') out.ctx.resume();
    return out.ctx;
  }

  // 客戶的聲音一段一段接著排進去播（24kHz、16-bit PCM）
  function play(b64) {
    const c = custTurn();
    const ctx = outCtx();
    const bin = atob(b64);
    const n = bin.length >> 1;
    const buffer = ctx.createBuffer(1, n, 24000);
    const ch = buffer.getChannelData(0);
    for (let i = 0; i < n; i += 1) {
      const v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
      ch[i] = (v >= 0x8000 ? v - 0x10000 : v) / 32768;
    }
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(out.analyser);
    const at = Math.max(ctx.currentTime + 0.05, playhead);
    src.start(at);
    playhead = at + buffer.duration;
    if (!c.start) c.start = at;
    c.end = playhead;
    sources.push(src);
    src.onended = () => {
      sources = sources.filter((x) => x !== src);
      if (sources.length) return;
      stopMouth();
      if (!cust && active) {
        setCustomerState('listening');
        setCaption(undefined, true);
      }
    };
    if (!stopMouthLoop) stopMouthLoop = animateMouth(out.analyser);
  }

  function stopMouth() {
    if (stopMouthLoop) stopMouthLoop();
    stopMouthLoop = null;
  }

  function stopPlayback() {
    sources.forEach((s) => {
      s.onended = null;
      try { s.stop(); } catch { /* 已經停了 */ }
    });
    sources = [];
    playhead = 0;
    stopMouth();
  }

  async function startMic() {
    const my = ++micGen;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    // 等權限、載入的時候已經掛斷或換了一通：這次拿到的麥克風直接關掉
    const stale = () => my !== micGen || !active;
    let ctx = null;
    try {
      if (stale()) return;
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      const url = URL.createObjectURL(new Blob([PCM16_WORKLET], { type: 'text/javascript' }));
      try {
        await ctx.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      if (stale()) return;
      const node = new AudioWorkletNode(ctx, 'pcm16');
      node.port.onmessage = (e) => micChunk(e.data);
      ctx.createMediaStreamSource(stream).connect(node);
      node.connect(ctx.destination); // 輸出是靜音；接上去瀏覽器才會持續處理
      if (mic) {
        mic.stream.getTracks().forEach((t) => t.stop());
        mic.ctx.close().catch(() => {});
      }
      mic = { stream, ctx };
    } finally {
      if (!mic || mic.stream !== stream) {
        stream.getTracks().forEach((t) => t.stop());
        if (ctx) ctx.close().catch(() => {});
      }
    }
  }

  // 只在有人講話時才送聲音：一直送安靜的聲音，Gemini 會把雜音聽成字（實測憑空聽出「你說什麼」），也白花用量。
  // 講完後多送的 2 秒讓 Gemini 自己判定講完；之後才停送並補一個 audioStreamEnd
  function micChunk(pcm) {
    let sum = 0;
    for (let i = 0; i < pcm.length; i += 1) sum += (pcm[i] / 32768) ** 2;
    const rms = Math.sqrt(sum / pcm.length);
    $('#btnMic').style.setProperty('--lvl', muted ? 0 : Math.min(1, rms * 5).toFixed(3));
    if (!active) return;
    const now = performance.now();
    if (muted) {
      // 剛講完就按靜音：剩下的 2 秒改送安靜的聲音，Gemini 聽完才會接話
      if (!gate.open) return;
      if (now - gate.lastLoud < VOICE_HANG_MS) {
        sendAudio(new Int16Array(pcm.length));
        return;
      }
      gate.open = false;
      send({ realtimeInput: { audioStreamEnd: true } });
      commitSoon();
      return;
    }
    if (rms > VOICE_RMS) gate.lastLoud = now;
    if (now - gate.lastLoud < VOICE_HANG_MS) {
      if (!gate.open) {
        gate.open = true;
        gate.pre.forEach(sendAudio); // 補上開口前的一小段，第一個字才不會被吃掉
        gate.pre = [];
      }
      sendAudio(pcm);
      return;
    }
    if (gate.open) {
      gate.open = false;
      send({ realtimeInput: { audioStreamEnd: true } });
      commitSoon(); // 客戶沒接話時，也在講完約 2 秒後把這句放進逐字稿
    }
    gate.pre.push(pcm);
    if (gate.pre.length > VOICE_PREROLL) gate.pre.shift();
  }

  function sendAudio(pcm) {
    send({ realtimeInput: { audio: { data: toB64(pcm.buffer), mimeType: 'audio/pcm;rate=16000' } } });
  }

  function stopMic() {
    micGen += 1;
    if (!mic) return;
    mic.stream.getTracks().forEach((t) => t.stop());
    mic.ctx.close().catch(() => {});
    mic = null;
    $('#btnMic').style.setProperty('--lvl', 0);
  }

  function toggleMute() {
    if (!active || !mic) return;
    muted = !muted;
    setMicUi(muted ? 'muted' : 'live');
    // 講完那 2 秒還沒送完就保留，交給 micChunk 補完；沒在講話就清掉開口前的暫存
    if (!gate.open) gate = { open: false, lastLoud: 0, pre: [] };
  }

  function stop() {
    clearTimeout(repIdle);
    clearTimeout(reviseTimer);
    endStale();
    gen += 1;
    const wasActive = active;
    active = false;
    if (wasActive) {
      custDone(true);
      commitRep();
    }
    cust = null;
    rep = '';
    lastRep = null;
    gate = { open: false, lastLoud: 0, pre: [] };
    stopPlayback();
    stopMic();
    const sock = ws;
    ws = null;
    pending = null;
    if (sock) {
      try { sock.close(1000, 'end'); } catch { /* 已經關了 */ }
    }
    setMicUi('off');
  }

  return {
    prepare, start, stop, toggleMute,
    send: sendText,
    get voiceName() { return `Gemini Live · ${voice || '預設聲音'}`; },
  };
})();

// ------------------------------------------------------------------ 合規禁語即時偵測（示範清單，待法遵確認）

const RULES = [
  { re: /保證(會)?(獲利|賺錢|賺|收益|報酬|回本|會漲|漲|配息|不虧|不會虧)/g, issue: '保證獲利或報酬' },
  { re: /(穩賺不賠|只賺不賠|穩賺|包賺|必賺|穩穩賺|穩賠不了)/g, issue: '穩賺不賠類誇大用語' },
  { re: /一定(會)?(賺|漲|回來|回本|獲利)/g, issue: '保證會漲' },
  { re: /(不會|不可能)(虧|賠)/g, issue: '保證不會虧損', questionGuard: true },
  { re: /(零風險|沒有風險|無風險|沒什麼風險|沒啥風險)/g, issue: '淡化或否認投資風險' },
  { re: /(跟|和|像|比)定存(一樣|還|更)?(安全|穩|保險)/g, issue: '拿定存比擬投資商品' },
  { re: /明牌/g, issue: '報明牌' },
  { re: /退(佣|傭)/g, issue: '私下退佣' },
  { re: /(代操|全權(交給|委託)我|帳戶借我|密碼給我)/g, issue: '代客操作或保管帳戶' },
];
const NEGATION = /[不沒無別勿免莫]|禁止|避免/;

function clauseBefore(text, idx) {
  return text.slice(Math.max(0, idx - 6), idx).split(/[,，。.!！?？;；\s]/).pop();
}

function sentenceAt(text, idx, len) {
  const stop = /[。！？!?；;\n]/;
  let s = idx;
  while (s > 0 && !stop.test(text[s - 1]) && idx - s < 30) s -= 1;
  let e = idx + len;
  while (e < text.length && !stop.test(text[e]) && e - idx < 40) e += 1;
  return text.slice(s, e).trim();
}

function checkCompliance(text) {
  const hits = [];
  const seen = new Set();
  for (const rule of RULES) {
    rule.re.lastIndex = 0;
    let m;
    while ((m = rule.re.exec(text))) {
      const clause = clauseBefore(text, m.index);
      if (rule.questionGuard && /會$/.test(clause)) continue; // 「會不會虧」是問句
      if (NEGATION.test(clause)) continue; // 「不能保證獲利」「沒有明牌」是正確說法
      if (seen.has(m[0])) continue;
      seen.add(m[0]);
      hits.push({ word: m[0], issue: rule.issue, quote: sentenceAt(text, m.index, m[0].length) });
    }
  }
  return hits;
}

function highlight(text, hits) {
  if (!hits?.length) return esc(text);
  const re = new RegExp([...new Set(hits.map((h) => h.word))].sort((a, b) => b.length - a.length).map(escapeRe).join('|'), 'g');
  let out = '';
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    out += esc(text.slice(last, m.index)) + `<mark class="bad">${esc(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

// ------------------------------------------------------------------ 對話泡泡

function bubbleHTML(role, text, hits, scenario = state.scenario) {
  const flagged = hits?.length;
  const flag = flagged ? `<div class="flag-tag">${icon('alert')}合規警示：${esc(hits.map((h) => h.issue).join('、'))}</div>` : '';
  if (role === 'customer') {
    return `<div class="msg customer"><div class="msg-head"><div class="msg-av">${avatarSVG(scenario.customer.avatar)}</div>${esc(scenario.customer.name)}</div><div class="txt">${highlight(text, hits)}</div></div>`;
  }
  return `<div class="msg rep${flagged ? ' flagged' : ''}"><div class="bubble"><span class="txt">${highlight(text, hits)}</span></div>${flag}</div>`;
}

// 使用者往上捲在看舊的內容時，不要硬把畫面拉回底部
function nearBottom(box) {
  return box.scrollHeight - box.scrollTop - box.clientHeight < 120;
}

function addBubble(role, text, hits, typing = false) {
  const box = $('#transcript');
  const near = nearBottom(box);
  box.insertAdjacentHTML('beforeend', bubbleHTML(role, text, hits));
  const el = box.lastElementChild;
  if (typing) el.querySelector('.txt').innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
  if (near || role === 'rep') box.scrollTop = box.scrollHeight;
  updateTurnCount();
  return el;
}

function setBubbleText(el, text) {
  const box = $('#transcript');
  const near = nearBottom(box);
  el.querySelector('.txt').textContent = text;
  if (near) box.scrollTop = box.scrollHeight;
}

function updateTurnCount() {
  $('#turnCount').textContent = String($('#transcript').children.length || '');
}

// ------------------------------------------------------------------ 教練面板

function renderChecklist(newly = []) {
  const items = state.scenario.checklist;
  const done = items.filter((c) => state.checklist[c.id]).length;
  $('#checklist').innerHTML = items.map((c) =>
    `<li class="${state.checklist[c.id] ? 'done' : ''}${newly.includes(c.id) ? ' pop' : ''}" title="${esc(c.hint || '')}"><span class="ck">${icon('check')}</span>${esc(c.label)}</li>`).join('');
  $('#checkCount').textContent = `${done} / ${items.length}`;
  $('#checkBar').style.width = `${items.length ? (done / items.length) * 100 : 0}%`;
}

let newestAlert = null;

function renderAlerts() {
  const list = $('#alerts');
  $('#alertCount').textContent = state.alerts.length ? `${state.alerts.length} 則` : '';
  if (!state.alerts.length) {
    list.innerHTML = '<li class="empty">目前沒有違規用語</li>';
    return;
  }
  list.innerHTML = state.alerts.slice().reverse().map((a) =>
    `<li${a === newestAlert ? ' class="new"' : ''}><b>${esc(a.issue)}</b>「${esc(a.quote)}」<small>${esc(a.source)}</small></li>`).join('');
}

// 同一句（或一句包含另一句）只留一則，問題用「、」接起來，數字才會跟泡泡對得上
function addAlert(quote, issue, source) {
  const key = normKey(quote);
  if (!key) return false;
  const old = state.alerts.find((a) => {
    const k = normKey(a.quote);
    return k === key || (k.length >= 6 && key.length >= 6 && (k.includes(key) || key.includes(k)));
  });
  if (old) {
    const longer = key.length > normKey(old.quote).length;
    if (longer) old.quote = quote;
    // 同一句已經有警示時，AI 教練比較長的說明就不再接上去，免得一長串看不完
    if (old.issue.split('、').includes(issue) || source === 'AI 教練') {
      if (longer) renderAlerts();
      return false;
    }
    old.issue += `、${issue}`;
    newestAlert = old;
    renderAlerts();
    return true;
  }
  const a = { quote, issue, source };
  state.alerts.push(a);
  newestAlert = a;
  renderAlerts();
  return true;
}

let tipTimer = 0;
function setTip(text) {
  const t = String(text ?? '').trim();
  const p = $('#tipText');
  if (!t || t === p.textContent) return;
  p.textContent = t;
  const card = $('#tipCard');
  card.classList.remove('fresh');
  void card.offsetWidth; // 重新觸發外框亮一下
  card.classList.add('fresh');
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => card.classList.remove('fresh'), 2500);
}

// 教練正在看幾輪：大於 0 時提示卡顯示「分析中…」
function setCoachBusy(d) {
  state.coachBusy = d === 0 ? 0 : Math.max(0, state.coachBusy + d);
  $('#tipCard').classList.toggle('pending', state.coachBusy > 0);
}

function abortCoach() {
  state.coachCtrls.forEach((c) => c.abort());
  state.coachCtrls.clear();
}

// 每句業務發言的教練紀錄（beginSession 重設）。turn 是業務的第幾句
function newCoach() {
  return {
    seq: 0, // 每送一次教練就 +1，用來分先後
    latest: {}, // turn → 這句最新一次送出的編號
    applied: {}, // turn → 已經套用的是第幾次送出的結果
    span: {}, // turn → 最近一次從第幾句一起看到這句
    owner: {}, // turn → 評過了，算在哪一句的結果裡（一起看的都記在最後那句）
    delta: {}, // turn → 這組實際加減的信任度（超過 0～100 被截掉的不算）
    ticks: {}, // turn → 這組打的勾
    missed: new Set(), // 最新一次沒看成、要補看的句子
    retried: new Set(), // 已經補看過一次的句子
  };
}

// 還在跑的教練有沒有涵蓋第 k 句
function coachFlying(k) {
  return [...state.coachCtrls].some((c) => c.from <= k && k <= c.turn);
}

// 這次從第幾句一起看到 turn：重看沿用上次的範圍；第一次看就往前帶上還沒評過、也沒在跑的，最多 3 句
function coachSpan(turn) {
  const c = state.coach;
  const again = c.owner[turn] === turn || [...state.coachCtrls].some((x) => x.turn === turn);
  if (again && c.span[turn]) return c.span[turn];
  let from = turn;
  while (turn - from < 2 && from > 1 && !c.owner[from - 1] && !coachFlying(from - 1)) from -= 1;
  return from;
}

// 補看沒看成的句子（雲端模型用；每句只補一次）。還是沒看成的，由業務下一句一起帶上
function retryCoach() {
  if (state.ended || !state.coachParallel) return;
  const c = state.coach;
  for (let k = state.repTurn; k >= 1; k -= 1) {
    const need = c.missed.has(k) || (!c.latest[k] && !c.owner[k]);
    if (!need || c.retried.has(k) || coachFlying(k)) continue;
    for (let j = coachSpan(k); j <= k; j += 1) c.retried.add(j);
    runObserver(k);
  }
}

let trustDeltaTimer = 0;
function setTrust(value, instant = false) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  const prev = state.trust;
  state.trust = v;
  const fill = $('#trustFill');
  fill.style.width = `${v}%`;
  fill.className = `trust-fill ${v < 35 ? 'low' : v < 65 ? 'mid' : 'high'}`;
  $('#trustLabel').textContent = v < 30 ? '不太信任你' : v < 50 ? '半信半疑' : v < 70 ? '開始放心了' : v < 85 ? '滿信任你的' : '非常信任你';
  const em = $('#trustDelta');
  const delta = v - prev;
  if (!instant && delta) {
    em.textContent = `${delta > 0 ? '+' : ''}${delta}`;
    em.className = `trust-delta ${delta > 0 ? 'up' : 'down'}`;
    clearTimeout(trustDeltaTimer);
    trustDeltaTimer = setTimeout(() => { em.className = 'trust-delta'; }, 2600);
  }
  animateNumber($('#trustVal'), instant ? v : prev, v, instant ? 0 : 600);
}

function setMode(mode) {
  state.mode = mode;
  $$('#modeSeg button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  $('#view-call').classList.toggle('exam-on', mode === 'exam');
  $('#coachPanel').classList.toggle('exam', mode === 'exam');
  $('.coach-live').scrollTop = 0; // 考核的遮罩從最上面蓋起
}

function renderChips() {
  const lines = state.scenario.demo_lines || [];
  $('#chips').innerHTML = `<div class="menu-title">點一下直接送出（紅點是違規示範）</div>${lines.map((l, i) =>
    `<button class="menu-item${checkCompliance(l).length ? ' bad' : ''}" type="button" data-i="${i}"><span class="d"></span>${esc(l)}</button>`).join('')}`;
  $$('#chips .menu-item').forEach((c) => c.addEventListener('click', () => {
    $('#chips').classList.add('hidden');
    Mic.stop();
    sendRep(lines[Number(c.dataset.i)]);
  }));
  $('#btnChips').style.display = lines.length ? '' : 'none';
}

// ------------------------------------------------------------------ 通話流程

function startCall(id) {
  if (!leaveCallIfNeeded()) return;
  const s = state.scenarios.find((x) => x.id === id);
  if (!s) {
    toast('這個情境已經被刪除了', 'warn');
    return;
  }
  state.scenario = s;
  if (!state.providerReady) toast(`AI 大腦還沒就緒：${esc($('#providerMsg').textContent)}`, 'warn', 5000);
  const outgoing = s.call_direction === 'outgoing';
  $('#phoneAvatarBox').innerHTML = avatarSVG(s.customer.avatar);
  $('#phoneName').textContent = s.customer.name;
  $('#phoneScn').textContent = s.title;
  $('#phoneStatus').textContent = outgoing ? `準備撥給${s.customer.name}` : '來電中…';
  $('#acceptLabel').textContent = outgoing ? '撥出' : '接聽';
  openCallOverlay();
  if (!outgoing) Ring.start('incoming');
  state.liveCall = state.live;
  if (state.liveCall) {
    LiveCall.prepare(s);
    return;
  }
  api('/api/warmup', {}).catch(() => {});
  loadTts().then((t) => {
    if (!t || t.provider === 'browser' || state.scenario !== s) return;
    ServerVoice.configure(s.customer);
    ServerVoice.preload(s.opening_line);
  });
}

// 來電視窗開著時：背後的畫面不能操作，焦點在視窗裡；關掉後焦點回到原本的位置
let callFocusBack = null;
let dialGen = 0; // 按「取消」就 +1，還在撥號的那次就不會接通

function openCallOverlay() {
  const ov = $('#callOverlay');
  if (ov.classList.contains('hidden')) callFocusBack = document.activeElement;
  dialGen += 1;
  ov.classList.remove('hidden', 'dialing');
  $('#btnAccept').disabled = false;
  $('#app').inert = true;
  $('#btnAccept').focus();
}

function closeCallOverlay(restore) {
  $('#callOverlay').classList.add('hidden');
  $('#app').inert = false;
  const el = callFocusBack;
  callFocusBack = null;
  if (restore && el?.isConnected && el.offsetParent !== null) el.focus();
  else if ($('#callOverlay').contains(document.activeElement)) document.activeElement.blur();
}

$('#btnAccept').addEventListener('click', async () => {
  const btn = $('#btnAccept');
  if (btn.disabled) return;
  const g = ++dialGen;
  const alive = () => g === dialGen && !$('#callOverlay').classList.contains('hidden');
  ServerVoice.unlock();
  Ring.stop();
  const s = state.scenario;
  if (s.call_direction === 'outgoing') {
    btn.disabled = true; // 撥號中不能再按一次
    $('#btnDecline').focus();
    $('#callOverlay').classList.add('dialing');
    $('#phoneStatus').textContent = '撥號中…';
    Ring.start('ringback');
    await sleep(2800);
    if (!alive()) return;
    Ring.stop();
    $('#phoneStatus').textContent = '已接通';
    await sleep(350);
    if (!alive()) return;
  }
  closeCallOverlay(false);
  beginSession();
});

$('#btnDecline').addEventListener('click', () => {
  dialGen += 1;
  Ring.stop();
  LiveCall.stop();
  closeCallOverlay(true);
});

// 來電視窗：Tab 只在兩個按鈕之間移動，Esc 等於取消
document.addEventListener('keydown', (e) => {
  const ov = $('#callOverlay');
  if (ov.classList.contains('hidden')) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    $('#btnDecline').click();
    return;
  }
  if (e.key !== 'Tab') return;
  const items = $$('button:not([disabled])', ov);
  if (!items.length) return;
  e.preventDefault();
  const i = items.indexOf(document.activeElement);
  items[e.shiftKey ? (i <= 0 ? items.length - 1 : i - 1) : (i + 1) % items.length].focus();
});

function beginSession() {
  const s = state.scenario;
  Object.assign(state, {
    messages: [], checklist: {}, alerts: [], ended: false, startedAt: Date.now(), partial: null,
    repTurn: 0, coach: newCoach(), evalPending: false, fallbackNoted: false,
  });
  state.hitWords.clear();
  updatePlanTag();
  newestAlert = null;
  setCoachBusy(0);
  state.trust = s.initial_trust ?? 45;
  $('#callTitle').textContent = `${s.customer.name} · ${s.title}`;
  const me = state.rep ? ` · 你是${state.rep.title}${state.rep.address}` : '';
  $('#callSub').textContent = `${s.call_direction === 'outgoing' ? '由你撥出' : '客戶來電'} · ${s.product}${me}`;
  $('#avatarBox').innerHTML = avatarSVG(s.customer.avatar);
  $('#custName').textContent = s.customer.name;
  setCaption('');
  $('#tipCard').classList.remove('fresh');
  $$('#modeSeg button').forEach((b) => { b.disabled = false; });
  $('#custProfile').innerHTML = [
    ['年齡', `${s.customer.age} 歲`],
    ['職業', s.customer.job],
    ['商品', s.product],
    ['背景', s.customer.summary],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + '<dt>聲音</dt><dd id="voiceName" class="muted"></dd>';
  $('#taskText').textContent = s.learner_task;
  $('#tipText').textContent = s.first_tip || '先同理客戶的情緒，再開始了解需求。';
  $('#transcript').innerHTML = '';
  updateTurnCount();
  renderChecklist();
  renderAlerts();
  setTrust(state.trust, true);
  renderChips();
  setMode(state.mode);
  setComposer(true);
  show('call');
  setNav('home');
  startTimer();

  setCallMode();
  setMicUi('off');
  if (state.liveCall) {
    $('#micState').textContent = ''; // 接通、開好麥克風之後才顯示麥克風狀態
    $('#avatarStage').classList.add('audio-live');
    showVoiceName();
    LiveCall.start();
    return;
  }
  startNormalCall();
}

// 即時對話一直開著麥克風、隨時可以插話；一般模式是講完停頓才送出
function setCallMode() {
  $('#autoListen').closest('.switch').classList.toggle('hidden', state.liveCall);
  $('#micHint').textContent = state.liveCall ? '建議戴耳機' : '停頓 1.5 秒自動送出 · 建議戴耳機';
  $('#btnMic').title = state.liveCall ? '點一下靜音（空白鍵）' : '點一下開始說話（空白鍵）';
}

// 輸入框、麥克風和示範句：通話掛斷後就關掉
function setComposer(on) {
  ['#textInput', '#btnSend', '#btnMic', '#btnChips'].forEach((s) => { $(s).disabled = !on; });
}

// 一般模式：瀏覽器聽業務的話，客戶經由 /api/chat 回話再念出來。還沒開口的通話由客戶先說開場白
function startNormalCall() {
  const s = state.scenario;
  state.ttsBroken = false;
  Speech.use(state.tts && state.tts.provider !== 'browser' ? 'server' : 'browser');
  if (!Speech.configure(s.customer)) toast('找不到台灣中文語音，建議改用 Edge 開啟，或到「聲音設定」換成付費語音。', 'warn', 6000);
  showVoiceName();

  const last = state.messages[state.messages.length - 1];
  if (last) {
    // 中途換過來的：業務剛說完就讓客戶接著回，不然換業務說
    if (last.role === 'rep') streamCustomer();
    else onCustomerDone();
    return;
  }
  const text = s.opening_line;
  addBubble('customer', text);
  state.messages.push({ role: 'customer', text });
  setCustomerState('speaking');
  Speech.say(text, onCustomerDone);
}

// 即時對話連不上或斷線接不回：這通改用一般模式繼續
function switchToNormalCall() {
  if (state.ended || !state.liveCall) return;
  state.liveCall = false;
  setCallMode();
  setMicUi('off');
  api('/api/warmup', {}).catch(() => {});
  startNormalCall();
}

function onCustomerDone() {
  if (state.ended) return;
  setCustomerState('idle');
  setCaption(undefined, true);
  const typing = document.activeElement === $('#textInput') || $('#textInput').value.trim();
  if ($('#autoListen').checked && Mic.supported() && !typing) Mic.start();
}

// 業務插話：保留客戶已說出口的部分，停止語音與串流
function interruptCustomer() {
  const p = state.partial;
  if (p && !p.consumed) {
    p.consumed = true;
    const t = cleanReply(p.text).trim();
    if (t) {
      state.messages.push({ role: 'customer', text: `${t}…` });
      setBubbleText(p.bubble, `${t}…`);
    } else {
      p.bubble.remove();
      updateTurnCount();
    }
  }
  state.partial = null;
  if (state.chatCtrl) { state.chatCtrl.abort(); state.chatCtrl = null; }
  Speech.cancel();
}

// 記下業務說的話：放進逐字稿，並即時檢查禁語
function recordRep(text) {
  const hits = checkCompliance(text);
  const msg = { role: 'rep', text, turn: ++state.repTurn };
  state.messages.push(msg);
  const el = addBubble('rep', text, state.mode === 'practice' ? hits : null);
  alertHits(hits);
  state.hitWords.clear();
  // 開口之後就不能換練習／考核模式
  if (msg.turn === 1) $$('#modeSeg button').forEach((b) => { b.disabled = true; });
  // 雲端模型：這句一定案就送教練，和客戶回話同時跑
  if (state.coachParallel) runObserver(msg.turn);
  return { msg, el };
}

// 即時對話的逐字稿可能晚一點才送完：更新同一句
function reviseRep(entry, text) {
  entry.msg.text = text;
  const hits = checkCompliance(entry.msg.text);
  const tmp = document.createElement('div');
  tmp.innerHTML = bubbleHTML('rep', entry.msg.text, state.mode === 'practice' ? hits : null);
  const fresh = tmp.firstElementChild;
  entry.el.replaceWith(fresh);
  entry.el = fresh;
  alertHits(hits);
}

function alertHits(hits) {
  let fresh = null;
  // 講話中已經跳過提醒的禁語，這裡只記進警示清單，不再跳一次
  hits.forEach((h) => { if (addAlert(h.quote, h.issue, '即時偵測') && !fresh && !state.hitWords.has(h.word)) fresh = h; });
  if (fresh && state.mode === 'practice') toast(`「${esc(fresh.word)}」${esc(fresh.issue)}`, 'danger', 2500, '合規警示');
}

// 還在講話就先比對禁語，講到的當下就提醒（只限練習模式）
function previewCompliance(text) {
  if (state.mode !== 'practice' || state.ended || !text) return;
  checkCompliance(text).forEach((h) => {
    if (state.hitWords.has(h.word)) return;
    state.hitWords.add(h.word);
    toast(`「${esc(h.word)}」${esc(h.issue)}`, 'danger', 2500, '合規警示');
  });
}

async function sendRep(raw) {
  const text = String(raw || '').trim();
  if (!text || state.ended) return;
  if (state.liveCall) {
    LiveCall.send(text);
    return;
  }
  interruptCustomer();
  if (!state.coachParallel) abortCoach(); // 本機模型一次只跑一件事，客戶回話優先；沒評到的那句下一輪一起看
  recordRep(text);
  await streamCustomer();
}

async function streamCustomer() {
  if (state.ttsBroken && Speech.mode === 'server') Speech.use('browser');
  const ctrl = new AbortController();
  state.chatCtrl = ctrl;
  setCustomerState('thinking');
  const bubble = addBubble('customer', '', null, true);
  const partial = { bubble, text: '', consumed: false };
  state.partial = partial;
  let speechBuf = '';
  Speech.begin(onCustomerDone);
  try {
    const res = await request('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario_id: state.scenario.id, messages: state.messages }),
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || httpError(res.status));
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
        buf = buf.slice(i + 2);
        if (!line) continue;
        const data = JSON.parse(line.slice(5));
        if (data.error) throw new Error(data.error);
        if (data.fallback && data.brain) noteFallback(data.label, data.brain); // 主要大腦不能用，這句由備援回答
        if (!data.delta) continue;
        partial.text += data.delta;
        setBubbleText(bubble, cleanReply(partial.text));
        speechBuf += data.delta;
        const [sentences, rest] = takeSentences(speechBuf);
        speechBuf = rest;
        sentences.forEach((x) => Speech.enqueue(x));
      }
    }
    if (speechBuf.trim()) Speech.enqueue(speechBuf);
    if (!partial.consumed) {
      partial.consumed = true;
      const final = cleanReply(partial.text).trim();
      if (final) state.messages.push({ role: 'customer', text: final });
      else { bubble.remove(); updateTurnCount(); }
    }
    if (state.partial === partial) state.partial = null;
    if (state.chatCtrl === ctrl) state.chatCtrl = null;
    Speech.end();
    // 本機模型：客戶回完才跑教練，避免兩件事搶同一張顯示卡（前面被插話跳過的句子會一起看）
    if (!state.coachParallel) runObserver();
    else retryCoach(); // 雲端：補看剛才沒看成的句子
  } catch (err) {
    if (err.name === 'AbortError') return; // 被業務插話，由 interruptCustomer 收尾；這句由下一輪教練一起看
    // 講到一半出錯：已經出現在畫面上的話也記進逐字稿，和業務看到的一致
    if (!partial.consumed) {
      partial.consumed = true;
      const t = cleanReply(partial.text).trim();
      if (t) {
        state.messages.push({ role: 'customer', text: `${t}…` });
        setBubbleText(bubble, `${t}…`);
      } else {
        bubble.remove();
        updateTurnCount();
      }
    }
    if (state.partial === partial) state.partial = null;
    if (state.chatCtrl === ctrl) state.chatCtrl = null;
    Speech.cancel();
    setCustomerState('idle');
    toast(esc(err.name === 'TypeError' ? offline() : err.message), 'error', 7000, '客戶沒有回應');
  }
}

// 請 AI 教練看業務的第 turn 句（連同前面還沒評過的）：雲端可以好幾輪同時跑，本機一次只跑一輪
async function runObserver(turn = state.repTurn) {
  if (state.ended || !turn) return;
  if (!state.coachParallel) abortCoach(); // 被取消的那句沒評到，下面算範圍時會一起帶上
  const c = state.coach;
  if (c.owner[turn] > turn) turn = c.owner[turn]; // 這句已經和後面那句一起評過：整組重看
  const from = coachSpan(turn);
  const seq = ++c.seq;
  c.latest[turn] = seq;
  c.span[turn] = from;
  const ctrl = Object.assign(new AbortController(), { turn, from });
  state.coachCtrls.add(ctrl);
  const limit = state.coachParallel ? 12000 : 30000;
  const signal = AbortSignal.any ? AbortSignal.any([ctrl.signal, AbortSignal.timeout(limit)]) : ctrl.signal;
  // 重看同一組：信任度從這組之前算起，這組自己打的勾也重新判斷，不會重複加
  const own = new Set(c.ticks[turn] || []);
  const base = state.trust - (c.delta[turn] || 0);
  const done = Object.keys(state.checklist).filter((k) => state.checklist[k] && !own.has(k));
  // 只送到這句（和客戶接著的回話）為止：伺服器標【新】的最後幾句業務發言，剛好就是這組
  const next = state.messages.findIndex((m) => m.role === 'rep' && m.turn > turn);
  const messages = next < 0 ? state.messages : state.messages.slice(0, next);
  setCoachBusy(1);
  try {
    const data = await api('/api/observe', {
      scenario_id: state.scenario.id,
      messages,
      trust: base,
      done,
      turn,
      focus: turn - from + 1,
    }, signal);
    if (ctrl.signal.aborted || state.ended) return;
    // 打勾和違規不會過期，晚到也照收
    const newly = [];
    Object.entries(data.checklist || {}).forEach(([k, v]) => {
      if (v && !state.checklist[k]) { state.checklist[k] = true; newly.push(k); }
    });
    if (newly.length) renderChecklist(newly);
    c.ticks[turn] = [...new Set([...(c.ticks[turn] || []), ...newly])];
    let fresh = null;
    (data.flags || []).forEach((f) => { if (addAlert(f.quote, f.issue, 'AI 教練') && !fresh) fresh = f; });
    if (fresh && state.mode === 'practice') toast(`「${esc(fresh.quote)}」${esc(fresh.issue)}`, 'danger', 2500, 'AI 教練提醒');
    // 同一組重看好幾次時，只收比已套用的更新的那次；舊的晚到不能蓋掉新的信任度和提示
    if (seq <= (c.applied[turn] || 0)) return;
    c.applied[turn] = seq;
    const d = Number.isFinite(data.trust_delta) ? data.trust_delta : Number.isFinite(data.trust) ? data.trust - base : 0;
    const before = state.trust - (c.delta[turn] || 0);
    setTrust(before + d);
    c.delta[turn] = state.trust - before; // 記實際加減的，被 0～100 截掉的不算
    for (let k = from; k <= turn; k += 1) {
      c.owner[k] = turn;
      if (k < turn) c.missed.delete(k);
    }
    if (seq === c.latest[turn]) c.missed.delete(turn);
    // 業務已經講了下一句，這則提示就過期了
    if (data.tip && state.mode === 'practice' && turn >= state.repTurn) setTip(data.tip);
  } catch {
    // 最新這次沒看成（逾時、伺服器略過、被下一句取消）：記下來，之後補看一次，或由下一句一起帶上
    if (!state.ended && seq === c.latest[turn]) c.missed.add(turn);
  } finally {
    state.coachCtrls.delete(ctrl);
    setCoachBusy(-1);
  }
}

const mmss = (x) => `${String(Math.floor(x / 60)).padStart(2, '0')}:${String(x % 60).padStart(2, '0')}`;

function startTimer() {
  clearInterval(state.timerId);
  const box = $('#callTimer');
  const el = $('#callTimerText');
  const mins = state.scenario.duration_min || 8;
  const limit = mins * 60;
  const tick = () => {
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    el.innerHTML = `${mmss(s)}<span class="of"> / ${mmss(limit)}</span>`;
    box.classList.toggle('near', s > limit * 0.8 && s <= limit);
    box.classList.toggle('over', s > limit);
    box.title = s > limit ? '已超過建議通話時間' : `建議 ${mins} 分鐘內完成`;
  };
  tick();
  state.timerId = setInterval(tick, 1000);
}

function stopSession() {
  state.ended = true;
  state.endedAt = Date.now();
  clearInterval(state.timerId);
  Mic.stop();
  LiveCall.stop();
  interruptCustomer();
  abortCoach();
  setCoachBusy(0);
  setCustomerState('idle');
}

async function endSession() {
  if (state.evalBusy) return;
  // 即時對話掛斷後評分失敗：電話不會再接回去，再按一次只重新評分
  const again = state.ended && state.evalPending;
  if (state.ended && !again) return;
  if (!again) {
    if (!state.messages.some((m) => m.role === 'rep')) {
      toast('你還沒開口喔，先跟客戶聊幾句再結束。', 'warn');
      return;
    }
    stopSession();
  }
  state.evalBusy = true;
  const steps = $$('#evalSteps li');
  steps.forEach((li) => { li.className = ''; });
  steps[0].className = 'active';
  $('#evalNote').textContent = state.providerLabel.startsWith('本機') ? '本機模型大約需要 20～60 秒' : '大約需要 5～15 秒';
  $('#evalOverlay').classList.remove('hidden');
  let k = 0;
  const stepTimer = setInterval(() => {
    if (k < steps.length - 1) { steps[k].className = 'done'; k += 1; steps[k].className = 'active'; }
  }, 4500);
  try {
    const report = await api('/api/evaluate', {
      scenario_id: state.scenario.id,
      messages: state.messages,
      client_flags: state.alerts.map(({ quote, issue }) => ({ quote, issue })),
    });
    clearInterval(stepTimer);
    state.evalPending = false;
    steps.forEach((li) => { li.className = 'done'; });
    await sleep(400);
    state.evalBusy = false;
    $('#evalOverlay').classList.add('hidden');
    const s = state.scenario;
    const ctx = {
      scenario: s,
      messages: state.messages.slice(),
      trust: state.trust,
      secs: Math.max(0, Math.floor((state.endedAt - state.startedAt) / 1000)),
      at: Date.now(),
      from: 'home',
    };
    saveHistory([{
      id: ctx.at.toString(36),
      at: ctx.at,
      scenario_id: s.id,
      scenario: { id: s.id, title: s.title, customer: { name: s.customer.name, avatar: s.customer.avatar } },
      mode: state.mode,
      secs: ctx.secs,
      trust: ctx.trust,
      report,
      messages: ctx.messages,
    }, ...loadHistory()]);
    renderReport(report, ctx);
    if (report.fallback) toast(esc(tidy(`${brainName(state.provider)} 暫時不能用，這次評分改用 ${shortLabel(report.model) || brainName(report.brain)}`)), 'warn', 6000);
  } catch (err) {
    clearInterval(stepTimer);
    state.evalBusy = false;
    $('#evalOverlay').classList.add('hidden');
    if (state.liveCall) {
      // 即時對話已經掛斷，不能接著講：留在結束的狀態，再按一次只重新評分
      state.evalPending = true;
      setComposer(false);
      $('#micState').textContent = ''; // 不能再打字了，拿掉「可以打字」
      setCustomerState('ended');
      toast(`${esc(err.message)}<br>通話已結束，可以再按一次「結束並評分」`, 'error', 8000, '評分失敗');
      return;
    }
    state.ended = false;
    startTimer();
    toast(`${esc(err.message)}<br>可以再按一次「結束並評分」`, 'error', 8000, '評分失敗');
  }
}

// ------------------------------------------------------------------ 評分報告

function radarSVG(dims) {
  const n = dims.length;
  const cx = 210;
  const cy = 150;
  const R = 100;
  const pt = (i, r) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const ratio = (d) => Math.max(0.03, (d.score ?? 0) / d.max);
  const poly = (f) => dims.map((_, i) => pt(i, R * f).map((v) => v.toFixed(1)).join(',')).join(' ');
  const grid = [0.25, 0.5, 0.75, 1].map((f) => `<polygon class="rg" points="${poly(f)}"/>`).join('');
  const axes = dims.map((_, i) => { const [x, y] = pt(i, R); return `<line class="ra" x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`; }).join('');
  const values = dims.map((d, i) => pt(i, R * ratio(d)).map((v) => v.toFixed(1)).join(',')).join(' ');
  const dots = dims.map((d, i) => { const [x, y] = pt(i, R * ratio(d)); return `<circle class="rd" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5"/>`; }).join('');
  const labels = dims.map((d, i) => {
    const [x, y] = pt(i, R + 24);
    const anchor = Math.abs(x - cx) < 5 ? 'middle' : x > cx ? 'start' : 'end';
    const dy = y < cy - 40 ? -6 : y > cy + 40 ? 12 : 0;
    return `<text class="rl" x="${x.toFixed(1)}" y="${(y + dy).toFixed(1)}" text-anchor="${anchor}">${esc(d.short)}</text>
      <text class="rv" x="${x.toFixed(1)}" y="${(y + dy + 16).toFixed(1)}" text-anchor="${anchor}">${d.score ?? '—'} / ${d.max}</text>`;
  }).join('');
  return `<svg class="radar" viewBox="0 0 420 316">${grid}${axes}<polygon class="rp" points="${values}"/>${dots}${labels}</svg>`;
}

function renderReport(r, ctx) {
  const s = ctx.scenario;
  state.report = { r, ctx };
  show('report');
  setNav(ctx.from === 'history' ? 'history' : 'home');

  $('#btnCrumb').textContent = ctx.from === 'history' ? '練習紀錄' : '對練情境';
  $('#reportTitle').textContent = `${s.customer.name} · ${s.title}`;
  $('#reportMeta').textContent = `${fmtDate(ctx.at)} · 評分模型 ${r.model}`;

  const arc = $('#scoreArc');
  const C = 2 * Math.PI * 52;
  arc.style.transition = 'none';
  arc.style.strokeDasharray = `${C}`;
  arc.style.strokeDashoffset = `${C}`;
  void arc.getBoundingClientRect();
  arc.style.transition = '';
  setTimeout(() => { arc.style.strokeDashoffset = `${C * (1 - r.total / 100)}`; }, 30);
  animateNumber($('#scoreNum'), 0, r.total, 1200);
  $('#gradeBadge').className = `tag ${GRADE_TAG[r.grade] ?? ''}`;
  $('#gradeBadge').textContent = r.grade;
  $('#reportSummary').textContent = r.summary || '';

  const stat = (label, value, unit = '') => `<div class="stat"><span>${esc(label)}</span><b>${esc(value)}${unit ? ` <small>${esc(unit)}</small>` : ''}</b></div>`;
  $('#reportStats').innerHTML = [
    stat('通話時間', fmtDuration(ctx.secs)),
    stat('發言次數', r.rep_turns, '次'),
    stat('客戶信任度', ctx.trust, '/ 100'),
    stat('合規警示', r.violations.length, '則'),
  ].join('');

  const notes = [];
  if (r.short_conversation) notes.push('這次對話比較短，分數僅供參考。');
  if (r.partial) notes.push('部分項目 AI 沒有給分，以「未評估」顯示。');
  $('#reportNotice').innerHTML = notes.length ? `<div class="notice">${esc(notes.join(' '))}</div>` : '';

  $('#radar').innerHTML = radarSVG(r.dimensions);
  $('#dimBars').innerHTML = r.dimensions.map((d) => {
    const pct = d.score == null ? 0 : (d.score / d.max) * 100;
    const level = pct < 40 ? 'low' : pct < 70 ? 'mid' : '';
    return `<div class="dim-row"><span>${esc(d.label)}</span><div class="bar"><i class="${level}" data-w="${pct.toFixed(0)}"></i></div><span class="v"><b>${d.score ?? '—'}</b> / ${d.max}</span></div>`;
  }).join('');
  setTimeout(() => {
    $$('#dimBars .bar i').forEach((i) => { i.style.width = `${i.dataset.w}%`; });
  }, 30);

  $('#complianceBox').innerHTML = r.violations.length
    ? r.violations.map((v) => `<div class="violation">${icon('alert')}<div><b>${esc(v.issue)}</b><p>「${esc(v.quote)}」</p></div></div>`).join('')
    : `<div class="compliance-ok">${icon('shield')}這次對話沒有發現保證獲利、明牌、私下退佣等違規用語。</div>`;

  $('#dimCards').innerHTML = r.dimensions.map((d) => {
    const extras = [
      d.evidence ? `<div class="quote"><small>你說的</small>「${esc(d.evidence)}」</div>` : '',
      d.better ? `<div class="better"><small>建議這樣說</small>${esc(d.better)}</div>` : '',
    ].filter(Boolean);
    return `<div class="fb">
      <div class="fb-head"><b>${esc(d.label)}</b><span class="fb-score">${d.score ?? '—'}<small> / ${d.max}</small></span></div>
      <p>${esc(d.reason)}</p>
      ${extras.length ? `<div class="fb-grid">${extras.join('')}</div>` : ''}
    </div>`;
  }).join('');

  const bullets = (arr, ic) => (arr.length ? arr.map((x) => `<li>${icon(ic)}<span>${esc(x)}</span></li>`).join('') : '<li class="none">（無）</li>');
  $('#strengths').innerHTML = bullets(r.strengths, 'check');
  $('#improvements').innerHTML = bullets(r.improvements, 'arrowRight');

  $('#reportTranscript').innerHTML = ctx.messages.map((m) =>
    bubbleHTML(m.role, m.text, m.role === 'rep' ? checkCompliance(m.text) : null, s)).join('');
}

// ------------------------------------------------------------------ 事件

$$('.nav-item').forEach((b) => b.addEventListener('click', () => go(b.dataset.nav)));
$('#btnNew').addEventListener('click', () => go('home'));

function applyThemeIcon() {
  const dark = document.documentElement.dataset.theme !== 'light';
  $('#btnTheme').innerHTML = icon(dark ? 'sun' : 'moon');
  $('#btnTheme').title = dark ? '切換成淺色' : '切換成深色';
}
$('#btnTheme').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('aicoach.theme', next); } catch { /* 無法儲存就只套用這次 */ }
  applyThemeIcon();
});
const toggleMic = () => (state.liveCall ? LiveCall.toggleMute() : Mic.toggle());
$('#btnMic').addEventListener('click', toggleMic);
$('#btnSend').addEventListener('click', () => {
  const input = $('#textInput');
  const text = input.value;
  input.value = '';
  sendRep(text);
});
$('#textInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.isComposing) {
    e.preventDefault();
    $('#btnSend').click();
  }
});
$('#textInput').addEventListener('focus', () => { if (!state.liveCall && Mic.active) Mic.stop(); });
$('#btnEnd').addEventListener('click', endSession);
$('#btnBack').addEventListener('click', () => go('home'));
$$('#modeSeg button').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
$('#btnChips').addEventListener('click', (e) => {
  e.stopPropagation();
  $('#chips').classList.toggle('hidden');
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.menu-wrap')) $('#chips').classList.add('hidden');
});
$('#btnRetry').addEventListener('click', () => startCall(state.report.ctx.scenario.id));
$('#btnHome').addEventListener('click', () => go('home'));
$('#btnCrumb').addEventListener('click', () => go(state.report?.ctx.from === 'history' ? 'history' : 'home'));
$('#btnPrint').addEventListener('click', () => {
  $$('details').forEach((d) => { d.open = true; });
  window.print();
});
document.addEventListener('keydown', (e) => {
  if (e.code !== 'Space' || !$('#view-call').classList.contains('active') || state.ended) return;
  // 焦點在按鈕、連結、輸入框或可展開的標題上：空白鍵照它原本的作用
  if (document.activeElement?.closest('button, a, input, textarea, select, summary, [contenteditable], [role="button"]')) return;
  e.preventDefault();
  toggleMic();
});

// ------------------------------------------------------------------ 啟動

async function init() {
  hydrateIcons();
  applyThemeIcon();
  updateHistoryCount();
  try {
    await reloadScenarios();
  } catch (err) {
    $('#scenarioGrid').innerHTML = `<p class="notice">讀不到情境：${esc(err.message)}</p>`;
  }
  envHint();
  hostedCopy();
  const first = refreshStatus();
  api('/api/warmup', {}).catch(() => {});
  api('/api/profile').then((d) => { state.rep = d.effective || null; }).catch(() => {});
  loadTts();
  await first;
  pollStatus();
  signInReturn();
}

init();
