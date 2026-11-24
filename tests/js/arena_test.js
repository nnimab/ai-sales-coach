'use strict';

// 「新增情境」三步驟的純邏輯（web/arena.js）：三種人設 × 三種商品 × 三種刁難度轉出來的情境，
// 每個欄位都要在 server.py（sanitize_scenario）收得下的範圍裡。
// 執行：node tests/js/arena_test.js（python -m unittest 會自動跑）；加 --dump 會印出全部情境的 JSON，給 tests/test_arena.py 送去伺服器存。

const assert = require('node:assert/strict');
const path = require('node:path');
const Arena = require(path.join(__dirname, '..', '..', 'web', 'arena.js'));

const LONG = '這是一段故意寫得很長的內容用來測試截斷'.repeat(20);
const KEYS = new Set(['業務姓', '業務全名', '業務稱呼', '職稱', '職稱簡稱', '公司', '分公司', '年資']); // server.py 的 PLACEHOLDER_KEYS

function cases() {
  const out = [];
  for (const pid of ['owner', 'eng', 'senior']) {
    for (const type of ['bond', 'fund', 'custom']) {
      for (const d of [1, 2, 3]) {
        // 一半用正常的內容，一半每一格都塞到最長、還帶大括號
        const long = (out.length % 2) === 1;
        out.push({
          type, pid, d,
          p: long
            ? { name: `{測試}${LONG}`, issuer: LONG, code: 'XS3034046279', ccy: LONG, risk: 'RR3', coupon: LONG, tenor: LONG, rating: LONG, call: LONG, extra: LONG, risks: `1. ${LONG}\n2. 信用風險\n3. {怪}風險` }
            : { name: '高盛 20 年期步階票息美元債', issuer: '高盛金融國際有限公司', code: 'XS3034046279', ccy: '美元 USD', risk: 'RR3', coupon: '前二年 9%、第三年起 4%，每季付息', tenor: '20 年', rating: 'S&P A+', call: '第 2 年起發行機構有權提前買回', extra: '', risks: '' },
          c: long ? { name: LONG, age: '999', job: LONG, asset: LONG } : { name: '', age: '', job: '', asset: '' },
          ph: long ? [LONG, '你講白話一點', '每個月要讓我看到錢', '這跟ETF比差在哪？', '定存至少不會少', 'a', 'b', 'c', 'd', 'e'] : ['你講白話一點'],
          w: { unit: '財經部', name: long ? LONG : '王小明' },
        });
      }
    }
  }
  return out;
}

function build(raw) {
  const ctx = Arena.normalize(raw);
  const ol = Arena.cleanOutline(null, ctx);
  const rows = Arena.buildChat(ctx);
  return { ctx, ol, rows, sc: Arena.toScenario(ctx, ol, rows) };
}

if (process.argv.includes('--dump')) {
  process.stdout.write(JSON.stringify(cases().map((c) => build(c).sc)));
  process.exit(0);
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const len = (s, lo, hi, what) => assert.ok(typeof s === 'string' && s.length >= lo && s.length <= hi, `${what} 長度 ${String(s).length}，要在 ${lo}～${hi}`);

test('27 種組合：每個欄位都在伺服器收得下的範圍', () => {
  const all = cases();
  assert.equal(all.length, 27);
  for (const raw of all) {
    const { sc } = build(raw);
    const tag = `${raw.pid}/${raw.type}/${raw.d}`;
    len(sc.title, 2, 30, `${tag} title`);
    len(sc.product, 2, 30, `${tag} product`);
    assert.ok(['general', 'fund', 'bond'].includes(sc.rubric_type), tag);
    assert.ok([1, 2, 3].includes(sc.difficulty_level), tag);
    assert.equal(sc.initial_trust, { 1: 60, 2: 45, 3: 25 }[raw.d], tag);
    assert.equal(sc.call_direction, 'outgoing');
    len(sc.customer.name, 1, 12, `${tag} name`);
    assert.ok(Number.isInteger(sc.customer.age) && sc.customer.age >= 18 && sc.customer.age <= 95, `${tag} age`);
    len(sc.customer.job, 1, 30, `${tag} job`);
    assert.ok(['chen', 'lin', 'wang'].includes(sc.customer.avatar), tag);
    len(sc.customer.summary, 5, 200, `${tag} summary`);
    len(sc.opening_line, 2, 150, `${tag} opening`);
    len(sc.learner_task, 5, 120, `${tag} task`);
    len(sc.first_tip, 1, 40, `${tag} tip`);
    assert.ok(sc.checklist.length >= 1 && sc.checklist.length <= 10, tag);
    const ids = new Set();
    for (const c of sc.checklist) {
      assert.match(c.id, /^[a-z][a-z0-9_]{0,23}$/);
      ids.add(c.id);
      len(c.label, 1, 20, `${tag} checklist label`);
      len(c.hint, 0, 60, `${tag} checklist hint`);
    }
    assert.equal(ids.size, sc.checklist.length, `${tag} checklist id 重複`);
    assert.ok(sc.demo_lines.length <= 10, tag);
    sc.demo_lines.forEach((l) => len(l, 1, 80, `${tag} demo`));
    const so = sc.server_only;
    len(so.persona, 20, 1500, `${tag} persona`);
    len(so.hidden_info, 5, 1000, `${tag} hidden`);
    len(so.reveal_rules, 5, 1500, `${tag} reveal`);
    len(so.behavior, 5, 1500, `${tag} behavior`);
    len(so.coach_notes, 5, 1000, `${tag} coach`);
    len(so.product_facts, 1, 3000, `${tag} facts`);
    // 大括號只能是伺服器認得的代稱；使用者填的大括號要被拿掉
    const texts = [sc.title, sc.product, sc.customer.name, sc.customer.job, sc.customer.summary, sc.opening_line, sc.learner_task, sc.first_tip,
      ...sc.checklist.flatMap((c) => [c.label, c.hint]), ...sc.demo_lines, ...Object.values(so)];
    for (const t of texts) {
      for (const m of t.matchAll(/\{([^{}\n]{1,20})\}/g)) assert.ok(KEYS.has(m[1]), `${tag} 不認得的代稱 ${m[0]}`);
      assert.ok(!/[{}]/.test(t.replace(/\{([^{}\n]{1,20})\}/g, '')), `${tag} 多出來的大括號：${t.slice(0, 40)}`);
    }
    const size = JSON.stringify(sc.playbook).length;
    assert.ok(size <= 24000, `${tag} 劇本 ${size} 字，超過 24000`);
    for (const r of sc.playbook.rows) assert.ok(r.m ? r.l.length <= 40 : r.t.length <= 1500, `${tag} 對話太長`);
  }
});

test('刁難度：信任度 60／45／25，犀利挑剔多一輪質疑', () => {
  const base = { type: 'bond', pid: 'owner', p: { name: '某某美元債' }, w: {} };
  const n = (d) => build({ ...base, d }).rows.filter((r) => !r.m).length;
  assert.equal(n(3), n(2) + 2);
  assert.equal(build({ ...base, d: 1 }).rows[2].t.startsWith('喔，好啊，你說說看。'), true);
});

test('口頭禪會進到客戶的話和人設裡', () => {
  const { sc, rows } = build({ type: 'fund', pid: 'senior', d: 2, p: { name: '某某基金' }, ph: ['我自己的口頭禪'] });
  assert.ok(rows.some((r) => r.w === 'C' && r.t.includes('我自己的口頭禪')));
  assert.ok(sc.server_only.persona.includes('我自己的口頭禪'));
});

test('沒填必揭露風險：套用這類商品通用的；有填就用填的', () => {
  const a = build({ type: 'fund', pid: 'eng', p: { name: '某某基金' } });
  assert.ok(a.sc.server_only.product_facts.includes('配息可能由本金支付'));
  const b = build({ type: 'fund', pid: 'eng', p: { name: '某某基金', risks: '1. 自己寫的風險\n2、第二項' } });
  assert.deepEqual(Arena.riskList(b.ctx), ['自己寫的風險', '第二項']);
});

test('情境檔：匯出再讀回來，內容一樣；不是情境檔會被擋', () => {
  const { ctx, ol, rows, sc } = build({ type: 'bond', pid: 'eng', d: 3, p: { name: '某某美元債', coupon: '5%' }, ph: ['這跟ETF比差在哪？'], w: { unit: '金商部', name: '窗口' } });
  rows[1].t = '手動改過的一句話';
  const file = JSON.parse(JSON.stringify(Arena.exportData(Arena.toScenario(ctx, ol, rows).playbook, sc.title, '2026-11-24T00:00:00Z')));
  const back = Arena.readExport(file);
  assert.equal(Arena.recipeKey(back.ctx), Arena.recipeKey(ctx));
  assert.deepEqual(back.rows, rows);
  assert.deepEqual(back.ol, ol);
  assert.deepEqual(Arena.toScenario(back.ctx, back.ol, back.rows), Arena.toScenario(ctx, ol, rows));
  assert.throws(() => Arena.readExport({ kind: 'other' }), /不是/);
  assert.throws(() => Arena.readExport({ kind: 'ai-arena-scenario', v: 2, playbook: {} }), /版本/);
  assert.throws(() => Arena.readExport({ kind: 'ai-arena-scenario', v: 1, playbook: { p: {} } }), /商品名稱/);
});

test('商品檔解析：抓得到名稱、發行機構、ISIN、評等、票息', () => {
  const o = Arena.parseProduct('商品名稱：某某公司 20 年期美元債\n發行機構：某某金融\nISIN XS3034046279\n票面利率：5.5%，每半年付息\n信用評等：S&P A+\n風險等級 RR3', '美元債DM.docx');
  assert.equal(o.name, '某某公司 20 年期美元債');
  assert.equal(o.issuer, '某某金融');
  assert.equal(o.code, 'XS3034046279');
  assert.equal(o.risk, 'RR3');
  assert.equal(o.type, 'bond');
  assert.ok(o.coupon.includes('5.5%'));
  assert.equal(Arena.parseProduct('', 'x.pdf').name, '');
});

test('Word 文件：四個章節都在，使用者填的字有跳脫', () => {
  const { ctx, ol, rows } = build({ type: 'bond', pid: 'owner', d: 2, p: { name: '<b>壞</b>美元債' }, w: { unit: '財經部', name: '<i>x</i>' } });
  const html = Arena.docHtml(ctx, ol, rows, '2026/11/24');
  for (const [, title] of Arena.SECTIONS) assert.ok(html.includes(title), title);
  assert.ok(!html.includes('<b>壞</b>') && html.includes('&lt;b&gt;壞&lt;/b&gt;'));
  assert.ok(!html.includes('<i>x</i>'));
  const plain = Arena.plainDocHtml({ title: '情境<x>', product: '商品', customer: { name: '陳先生', age: 38, job: '工程師', summary: '背景' }, checklist: [{ label: '項目', hint: '' }], demo_lines: [] }, '2026/11/24');
  assert.ok(plain.includes('情境&lt;x&gt;') && plain.includes('沒有超業範本'));
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok - ${name}`); } catch (e) { failed += 1; console.log(`not ok - ${name}\n${e.stack || e}`); }
}
console.log(failed ? `${failed} failed` : `all ${tests.length} ok`);
process.exit(failed ? 1 : 0);
