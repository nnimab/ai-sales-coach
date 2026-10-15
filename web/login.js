'use strict';

(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // 只記名字（方便下次登入），通行碼一律不存
  const NAME_KEY = 'aicoach.loginName';
  const OFFLINE = '連不到網站，請檢查網路後再試';

  const form = $('#loginForm');
  const nameEl = $('#loginName');
  const codeEl = $('#loginCode');
  const btn = $('#btnLogin');
  const errEl = $('#loginErr');

  function showError(text) {
    errEl.textContent = text || '';
  }

  // 雲端還沒設定好（例如少了 APP_SECRET）：列出缺什麼，登入也不會成功
  function showSetup(list) {
    const items = Array.isArray(list) ? list.filter(Boolean) : [];
    const box = $('#setupBox');
    box.classList.toggle('hidden', !items.length);
    box.innerHTML = items.length
      ? `<b>網站還沒設定好</b><ul>${items.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>`
      : '';
  }

  async function session() {
    try {
      const r = await fetch('/api/session', { cache: 'no-store' });
      return await r.json().catch(() => ({}));
    } catch {
      return null;
    }
  }

  // 不用登入就能練習的網站（AUTH_MODE=open）：這頁只給管理者改設定用
  function adminOnly() {
    document.title = '管理者 · AI 對練教練';
    $('#loginSub').textContent = '練習不用登入，這裡的通行碼只用來改設定。';
    $('#codeLabel').textContent = '管理者通行碼';
    $('#loginFoot').innerHTML = '<a href="/">不用登入，直接去練習</a>';
  }

  async function init() {
    try { nameEl.value = localStorage.getItem(NAME_KEY) || ''; } catch { /* 讀不到就空白 */ }
    const s = await session();
    if (s === null) {
      showError(OFFLINE);
    } else if (s.setup) {
      // 雲端還沒設定好：留在這頁列出缺什麼（不用登入的網站也是，不然會和首頁來回跳）
      showSetup(s.setup_problems);
      if (s.auth_required === false) {
        $('#loginSub').textContent = '設定好之後，打開網址就能練習';
        form.classList.add('hidden');
        $('#loginFoot').classList.add('hidden');
        return;
      }
    } else if (s.admin_login && s.role !== 'admin') {
      adminOnly();
    } else if (s.logged_in || s.auth_required === false) {
      location.replace('/'); // 已經登入，或這裡不需要登入
      return;
    } else {
      showSetup(s.setup_problems);
    }
    (nameEl.value ? codeEl : nameEl).focus();
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const passcode = codeEl.value;
    const name = nameEl.value.trim().slice(0, 20);
    if (!passcode) {
      showError('請輸入通行碼');
      codeEl.focus();
      return;
    }
    showError('');
    btn.disabled = true;
    btn.textContent = '登入中…';
    try {
      const r = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passcode, name }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok !== false) {
        try { localStorage.setItem(NAME_KEY, name); } catch { /* 存不了就下次再填 */ }
        location.replace('/');
        return;
      }
      if (d.setup) {
        const s = await session();
        if (s) showSetup(s.setup_problems);
      }
      showError(d.error || `登入失敗（代碼 ${r.status}）`);
      codeEl.select();
    } catch {
      showError(OFFLINE);
    } finally {
      btn.disabled = false;
      btn.textContent = '登入';
    }
  });

  init();
})();
