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
    for (const type of ['fcn', 'bond', 'fund', 'custom']) {
      for (const d of [1, 2, 3]) {
        // 一半用正常的內容，一半每一格都塞到最長、還帶大括號
        const long = (out.length % 2) === 1;
        out.push({
          type, pid, d,
          p: long
            ? { name: `{測試}${LONG}`, issuer: LONG, code: 'XS3034046279', ccy: LONG, risk: 'RR3', under: LONG, coupon: LONG, tenor: LONG, strike: LONG, rating: LONG, call: LONG, ki: LONG, extra: LONG, risks: `1. ${LONG}\n2. 信用風險\n3. {怪}風險` }
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

test('36 種組合（三種人設 × 四種商品 × 三種刁難度）：每個欄位都在伺服器收得下的範圍', () => {
  const all = cases();
  assert.equal(all.length, 36);
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

test('商品類型：FCN 排第一個；每種類型用自己的欄位，基金不出現債券的說法', () => {
  assert.deepEqual(Object.keys(Arena.TYPES), ['fcn', 'bond', 'fund', 'custom']);
  const labels = (t) => Arena.fieldsOf(t).map((f) => f[1]).join('、');
  assert.ok(!/債券|票息|提前贖回|發行機構/.test(labels('fund')), labels('fund'));
  assert.ok(/基金公司/.test(labels('fund')) && /配息機制/.test(labels('fund')));
  assert.ok(/連結標的/.test(labels('fcn')) && /履約價/.test(labels('fcn')) && /下限價/.test(labels('fcn')));
  assert.ok(/債券信用評等/.test(labels('bond')) && /提前贖回條件/.test(labels('bond')));
  // 換過類型留下來的舊內容不會混進去：基金沒有信用評等、履約價、連結標的
  const ctx = Arena.normalize({ type: 'fund', pid: 'owner', p: { name: '某某基金', rating: 'S&P A', strike: '85%', under: 'NVDA' } });
  assert.equal(ctx.p.rating + ctx.p.strike + ctx.p.under, '');
});

test('基金：三種人設都不會出現發行機構倒閉、信用評等、20 年期、提前買回這些債券才有的話', () => {
  for (const pid of ['owner', 'eng', 'senior']) {
    const { ctx, sc, rows, ol } = build({ type: 'fund', pid, d: 3, p: { name: '某某收益基金', issuer: '某某投信', coupon: '每月配息', rating: '不該出現' } });
    const said = [rows.map((r) => r.t || '').join(' '), sc.server_only.persona, sc.server_only.behavior, sc.server_only.coach_notes, sc.server_only.product_facts,
      ol.prod.join(' '), ol.cust.join(' '), ol.atk.join(' '), sc.customer.summary, sc.learner_task, sc.checklist.map((c) => c.label + c.hint).join(' '),
      Arena.sec3(ctx, false)].join(' ');
    const bad = said.match(/.{0,12}(信用評等|發行機構|違約|不該出現|債券|20\s*年|提前買回|直債).{0,12}/);
    assert.equal(bad, null, `${pid}：${bad && bad[0]}`);
    assert.equal(sc.rubric_type, 'fund');
  }
  // 預設口頭禪：基金不列「20年期太長」「提前買回」；FCN 只少「20年期太長」
  assert.equal(Arena.PHRASES.filter((t) => Arena.fitsType('fund', t)).length, Arena.PHRASES.length - 2);
  assert.equal(Arena.PHRASES.filter((t) => Arena.fitsType('fcn', t)).length, Arena.PHRASES.length - 1);
  assert.equal(Arena.PHRASES.filter((t) => Arena.fitsType('bond', t)).length, Arena.PHRASES.length);
});

test('FCN：講得到連結標的、履約價、承接風險，評分標準用一般', () => {
  const { sc, rows, ol } = build({ type: 'fcn', pid: 'eng', d: 2, p: { name: '美元 6 個月期 FCN', issuer: '某某銀行', under: 'NVDA、TSLA', coupon: '年化 12%，每月配息', tenor: '6 個月', strike: '期初價格的 85%', call: '每月觀察，全部標的高於期初價就提前出場', ki: '期初價格的 65%' } });
  const said = rows.map((r) => r.t || '').join(' ');
  for (const w of ['NVDA、TSLA', '期初價格的 85%', '期初價格的 65%', '承接', '不保本', '提前出場']) assert.ok(said.includes(w), w);
  assert.ok(!said.includes('調整債券部位'));
  assert.equal(sc.rubric_type, 'general');
  assert.ok(sc.server_only.product_facts.includes('連結標的：NVDA、TSLA') && sc.server_only.product_facts.includes('履約價：'));
  assert.ok(sc.server_only.product_facts.includes('非保本'));
  assert.ok(ol.prod.some((x) => x.startsWith('下限價（KI）：')));
  assert.ok(sc.checklist.some((c) => c.label === '講清跌破時的承接風險'));
  const parsed = Arena.parseProduct(['商品名稱：美元 FCN', '連結標的：NVDA、TSLA', '履約價：85%', '下限價(KI)：65%', '提前出場(KO)：100%'].join(String.fromCharCode(10)), 'FCN.docx');
  assert.equal(parsed.type, 'fcn');
  assert.equal(parsed.under, 'NVDA、TSLA');
  assert.equal(parsed.strike, '85%');
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
});

test('上傳商品檔：文件裡沒寫名稱時，用檔名當商品名稱', () => {
  assert.equal(Arena.parseProduct('', 'x.pdf').name, 'x'); // 讀不到字（例如掃描的 PDF）也至少有名稱
  assert.equal(Arena.parseProduct('', '永豐_FCN_NVDA_TSLA_6M (1).pdf').name, '永豐 FCN NVDA TSLA 6M');
  assert.equal(Arena.parseProduct('', '永豐_FCN_NVDA_TSLA_6M (1).pdf').type, 'fcn');
  assert.equal(Arena.parseProduct('這是一檔債券型基金的月報，內容沒有欄位名稱', '00985B 月報.pdf').name, '00985B 月報');
  assert.equal(Arena.parseProduct('基金名稱：某某全球債券基金', '月報.pdf').name, '某某全球債券基金'); // 文件有寫就用文件的
  // PDF 抽出來的「部首字」換回一般的字（⼆→二、⾏→行、⽤→用），全形標點不動
  assert.equal(Arena.fixCjk('⼆、發⾏機構信⽤風險：（測試），１２％'), '二、發行機構信用風險：（測試），１２％');
  // 規則抓風險：標題和「風險等級」那一行不算，前面的編號拿掉
  const risks = Arena.parseProduct('商品風險等級 RR4\n主要風險\n一、本商品非保本，有本金損失風險。\n2. 發行機構信用風險。\n匯率風險', 'x.pdf').risks;
  assert.equal(risks, '本商品非保本，有本金損失風險。\n發行機構信用風險。\n匯率風險');
  // 表格式的文件（沒有冒號）：一行開頭是欄位名稱、空白後面是內容
  const tbl = Arena.parseProduct(['美元計價 6 個月期固定配息股權連結商品（FCN）主要條件', '發行機構 測試國際金融股份有限公司', '商品代號 XS9900112233',
    '商品風險等級 RR4', '連結標的 NVDA、TSLA', '配息 年化 12.00%，每月配息一次', '天期 6 個月', '履約價 期初價格的 85%', '提前出場 每月觀察一次', '下限價 期初價格的 65%',
    '說明：本商品之發行機構信用評等可能調整。'].join('\n'), 'x.pdf');
  assert.equal(tbl.type, 'fcn');
  assert.equal(tbl.issuer, '測試國際金融股份有限公司');
  assert.equal(tbl.code, 'XS9900112233');
  assert.equal(tbl.risk, 'RR4');
  assert.equal(tbl.under, 'NVDA、TSLA');
  assert.equal(tbl.coupon, '年化 12.00%，每月配息一次');
  assert.equal(tbl.tenor, '6 個月');
  assert.equal(tbl.strike, '期初價格的 85%');
  assert.equal(tbl.call, '每月觀察一次');
  assert.equal(tbl.ki, '期初價格的 65%');
  assert.equal(tbl.rating, ''); // 句子中間提到「信用評等」不算
  const fund = Arena.parseProduct('測試全球債券基金\n基金公司　測試投信\n基金類型　海外債券型\n配息政策　每月配息\n買回費用　持有未滿 7 日收 0.01%', '基金月報.pdf');
  assert.equal(fund.type, 'fund');
  assert.equal(fund.issuer, '測試投信');
  assert.equal(fund.tenor, '海外債券型');
  assert.equal(fund.coupon, '每月配息');
  assert.equal(fund.call, '持有未滿 7 日收 0.01%');
  assert.equal(Arena.nameFromFile('美元債DM - 複製 (2).docx'), '美元債DM');
  assert.equal(Arena.nameFromFile('a'.repeat(80) + '.txt').length, 60);
  assert.equal(Arena.nameFromFile(''), '');
});

test('上傳商品檔：幣別不用猜，風險只收一項一項的短句', () => {
  // 投資美元債的台幣計價基金：以前看到「美元」就填美元 USD，現在沒明寫就留空
  const deck = Arena.parseProduct('富邦優選收益傘型基金\n主要投資於投資等級美元公司債\n投資人應謹慎考量本身之需求與投資風險，本公司恕不負任何可能之損害責任\n級之高風險債券且配息來源可能為收\n動態調整 管控風險 理出最適', '傘型基金.pdf');
  assert.equal(deck.ccy, '');
  assert.equal(deck.risks, ''); // 句子中間剛好有「風險」兩個字的不算
  assert.equal(Arena.parseProduct('計價幣別：新台幣', 'x.pdf').ccy, '新台幣');
  assert.equal(Arena.parseProduct('基金小檔案\n計價幣別 新台幣\n風險收益等級 RR2', 'x.pdf').ccy, '新台幣');
  // 表格一行有兩組「欄位 內容」：只取到下一個欄位名稱前面
  const two = Arena.parseProduct('基金小檔案\n基金類型 主動式交易所交易基金 追蹤指標 無參考指標\n計價幣別 新台幣 風險收益等級 RR2(註)\n收益分配 月配息', '某某基金.pdf');
  assert.equal(two.ccy, '新台幣');
  assert.equal(two.tenor, '主動式交易所交易基金');
  assert.equal(two.risk, 'RR2');
  // 債券 ETF：檔名有「債」字，但開頭提到 ETF 或投信，算基金，不算海外債
  assert.equal(Arena.parseProduct('證券代號：00985B\n資料來源：群益投信整理', '群益ESG投等債0-5年期00985B.pdf').type, 'fund');
  assert.equal(Arena.parseProduct('富邦環球動態入息投資級債主動式ETF', '傘型.pdf').type, 'fund');
  assert.equal(Arena.parseProduct('票面利率：5.5%', '某某公司美元債.pdf').type, 'bond');
  // 頁數多的文件：規則只看第一頁和規格最密的頁，圖表頁的「平均票息率：7.1%」「發行人 債券名稱 到期」不會被當成欄位
  const pages = ['某某傘型基金\n2025年8月', '發行人 債券名稱 到期 殖利率%\n甲骨文 ORCL 5.55 02/06/53', '基金模擬投資組合\n平均票息率：7.1%', '市場展望', '基金小檔案\n計價幣別 新台幣 風險收益等級 RR2\n收益分配 月配息\n經理費 0.55%'];
  const r = Arena.parseProduct(Arena.ruleText(pages), '某某傘型基金.pdf');
  assert.equal(r.issuer, '');
  assert.equal(r.coupon, '月配息'); // 規格頁上的「收益分配 月配息」，不是圖表頁的 7.1%
  assert.equal(r.ccy, '新台幣');
  // 封面寫「預計第一次配息:11月底」、規格頁寫「收益分配 …每月配息」：用規格頁那個
  assert.equal(Arena.parseProduct('預計第一次配息:11月底\nETF小檔案\n收益分配 本基金成立日起滿45日(含)後，採每月配息', 'x.pdf').coupon, '本基金成立日起滿45日(含)後，採每月配息');
  assert.equal(Arena.ruleText(['一頁', '兩頁']), '一頁\n兩頁'); // 頁數少：整份都看
});

test('上傳商品檔：文件很長時，挑有規格的頁面給 AI', () => {
  const head = '僅供內部教育訓練使用、不得作為其他用途或對外公開';
  const slide = (n) => `${head}\n${n}\n市場展望第 ${n} 頁\n${`第 ${n} 頁的景氣循環與利率走勢說明文字。`.repeat(36)}`;
  const spec = `${head}\n24\n基金小檔案\n證券簡稱 主動測試動態入息\n計價幣別 新台幣\n風險收益等級 RR2\n經理費 0.55%\n收益分配 月配息`;
  const legal = (name) => `${head}\n【${name}】\n${'本基金經金管會同意生效，惟不表示本基金絕無風險。基金經理公司除盡善良管理人之注意義務外，不負責本基金之盈虧，亦不保證最低之收益，投資人申購前應詳閱基金公開說明書。其風險報酬等級為RR3。'.repeat(30)}`;
  const pages = ['測試優選收益傘型基金\n2025年8月\n主動測試動態入息\n主動測試複合收益'].concat(Array.from({ length: 22 }, (_, i) => slide(i + 2)), [spec], [legal('測試優選收益傘型基金'), legal('別的基金 A'), legal('別的基金 B')]);
  assert.equal(pages.length, 27);
  const text = Arena.pickText(pages, 20000);
  assert.ok(text.length <= 20000, String(text.length));
  assert.ok(text.startsWith('【第 1 頁】\n測試優選收益傘型基金')); // 第一頁一定在
  assert.ok(text.includes('【第 24 頁】\n基金小檔案') && text.includes('風險收益等級 RR2') && text.includes('計價幣別 新台幣')); // 規格頁在後面也挑得到
  assert.ok(!text.includes(head)); // 每頁都有的頁首拿掉
  assert.ok(!/\n24\n/.test(text)); // 單獨一行的頁碼拿掉
  assert.ok(text.includes('【測試優選收益傘型基金】')); // 法律聲明只放第一頁
  assert.ok(!text.includes('別的基金 A') && !text.includes('別的基金 B'));
  assert.ok(text.indexOf('【第 2 頁】') < text.indexOf('【第 24 頁】') && text.indexOf('【第 24 頁】') < text.indexOf('【第 25 頁】')); // 照原本的頁次排
  // 放得下就整份都送；空白頁不佔位置
  const short = Arena.pickText(['第一頁', '', '第三頁\n計價幣別 美元'], 20000);
  assert.equal(short, '【第 1 頁】\n第一頁\n【第 3 頁】\n第三頁\n計價幣別 美元');
  // 只有一頁（Word、txt）：照原樣，太長就截掉
  assert.equal(Arena.pickText(['商品名稱：某某'], 20000), '商品名稱：某某');
  assert.equal(Arena.pickText(['字'.repeat(30000)], 20000).length, 20000);
  assert.equal(Arena.pickText([], 20000), '');
  // 很擠的時候：位置先給規格頁
  const tight = Arena.pickText(pages, 1500);
  assert.ok(tight.length <= 1500 && tight.includes('基金小檔案') && tight.includes('【第 1 頁】'));
});

test('上傳商品檔：文件裡有好幾檔商品', () => {
  const blank = () => Object.fromEntries(Arena.FK.map((k) => [k, '']));
  const d = { products: [
    { type: 'fund', name: '主動測試動態入息', issuer: '測試投信', ccy: '新台幣', risk: 'RR2', coupon: '月配息', risks: '匯率風險' },
    { type: 'fund', name: '主動測試複合收益', issuer: '測試投信', ccy: '新台幣', risk: 'RR2', coupon: '月配息', risks: '非投資等級債券風險' },
    { type: 'fund', name: '主動測試動態入息', issuer: '重複的' },
    { type: 'fund' }, 'x', null,
  ] };
  const list = Arena.aiProducts(d);
  assert.deepEqual(list.map((c) => c.name), ['主動測試動態入息', '主動測試複合收益']); // 同名的、什麼都沒有的不算
  assert.equal(Arena.aiProducts({ product: { name: '舊格式一檔' } }).length, 1); // 伺服器還是舊版時
  assert.equal(Arena.aiProducts({ products: Array.from({ length: 9 }, (_, i) => ({ name: `第 ${i} 檔` })) }).length, 6);
  assert.deepEqual(Arena.aiProducts(null), []);
  // AI 有讀完就以 AI 為準：規則抓的不留（AI 沒寫的就空著）
  const snap = { type: 'fund', p: { ...blank(), name: '傘型基金', code: 'LU1234567891', coupon: '規則抓的配息', risks: '規則抓的風險' } };
  const second = Arena.productTarget(snap, list[1]);
  assert.equal(second.p.name, '主動測試複合收益');
  assert.equal(second.p.code, '');
  assert.equal(second.p.ccy, '新台幣');
  assert.equal(second.p.risks, '非投資等級債券風險');
  // AI 連名稱都沒寫：留著原本的名稱（檔名）
  assert.equal(Arena.productTarget(snap, { coupon: '月配息' }).p.name, '傘型基金');
  // 併進表單：規則抓的被清掉也算改到；使用者改過的不動
  const raw = { type: 'fund', ph: [], p: { ...snap.p, coupon: '我自己改的' } };
  assert.equal(Arena.mergeProduct(raw, snap, list[0]), 6); // name、issuer、code（清空）、ccy、risk、risks
  assert.equal(raw.p.coupon, '我自己改的');
  assert.equal(raw.p.code, '');
});

test('上傳商品檔：AI 讀到的蓋過檔名和規則抓的，使用者自己改過的不動', () => {
  const blank = () => Object.fromEntries(Arena.FK.map((k) => [k, '']));
  const snap = { type: 'fcn', p: { ...blank(), name: '月報', coupon: '舊的配息' } };
  const raw = { type: 'fcn', ph: ['20年期太長，跌下來誰賠？', '我自己的口頭禪'], p: { ...snap.p, coupon: '我自己改的' } };
  const n = Arena.mergeProduct(raw, snap, {
    type: 'fund', name: '  某某全球債券基金 ', issuer: '某某投信', coupon: 'AI 的配息', risk: 'RR9', code: '', risks: '匯率風險\n\n\n\n流動性風險', bogus: 'x', call: { a: 1 },
  });
  assert.equal(raw.type, 'fund'); // 類型沒被使用者改過：照 AI 的
  assert.deepEqual(raw.ph, ['我自己的口頭禪']); // 換成基金：只有債券才講的預設口頭禪取消
  assert.equal(raw.p.name, '某某全球債券基金'); // AI 的名稱蓋過檔名
  assert.equal(raw.p.issuer, '某某投信');
  assert.equal(raw.p.coupon, '我自己改的'); // 等 AI 的時候自己改過：不蓋
  assert.equal(raw.p.risk, ''); // 不是 RR1～RR5：不收
  assert.equal(raw.p.risks, '匯率風險\n\n流動性風險');
  assert.ok(!('bogus' in raw.p));
  assert.equal(n, 3);
  // 使用者等的時候自己換了類型：AI 的類型不蓋，欄位照樣換成 AI 的（AI 沒寫配息，規則抓的那個清掉）
  const raw2 = { type: 'bond', ph: [], p: { ...snap.p } };
  assert.equal(Arena.mergeProduct(raw2, snap, { type: 'fund', name: '某某基金' }), 2);
  assert.equal(raw2.type, 'bond');
  assert.equal(raw2.p.coupon, '');
  // AI 什麼都沒讀到，或回的東西不對：表單原樣（不會把規則抓的清光）
  const raw3 = { type: 'fcn', ph: [], p: { ...snap.p } };
  assert.equal(Arena.mergeProduct(raw3, snap, Object.fromEntries(Arena.FK.map((k) => [k, '']))), 0);
  assert.equal(Arena.mergeProduct(raw3, snap, null), 0);
  assert.equal(Arena.mergeProduct(raw3, snap, { type: 'fund' }), 0);
  assert.equal(raw3.p.coupon, '舊的配息');
  assert.equal(raw3.type, 'fcn');
  assert.equal(Arena.mergeProduct(raw3, snap, { type: 'nope', name: 123 }), 2); // 數字當成字收下，類型不認得就不換
  assert.equal(raw3.type, 'fcn');
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

test('下載的 Word 檔名：Role play腳本_[標的]_[客戶態樣]_[日期]', () => {
  const day = new Date(2026, 10, 5); // 使用者電腦的 2026/11/5
  assert.equal(Arena.wordName('00985B', '精明上班族', day), 'Role play腳本_00985B_精明上班族_20261105.doc');
  // 底線只當分隔：名稱裡的底線、空白和不能當檔名的字換成一個空白
  assert.equal(Arena.wordName(' 高盛 20 年期_步階票息/美元債? ', '保守賠過錢企業主', day), 'Role play腳本_高盛 20 年期 步階票息 美元債_保守賠過錢企業主_20261105.doc');
  assert.equal(Arena.wordName('', null, day), 'Role play腳本_商品_客戶_20261105.doc');
  assert.equal(Arena.wordName('長'.repeat(80), '態'.repeat(40), day), `Role play腳本_${'長'.repeat(60)}_${'態'.repeat(20)}_20261105.doc`);
  assert.match(Arena.wordName('A', 'B'), /^Role play腳本_A_B_\d{8}\.doc$/);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok - ${name}`); } catch (e) { failed += 1; console.log(`not ok - ${name}\n${e.stack || e}`); }
}
console.log(failed ? `${failed} failed` : `all ${tests.length} ok`);
process.exit(failed ? 1 : 0);
