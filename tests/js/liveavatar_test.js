'use strict';

// 真人模式網頁那邊的邏輯（web/liveavatar.js）：用假的 WebSocket 和假的 LiveKit 測，不連任何網路。
// 執行：node tests/js/liveavatar_test.js（python -m unittest 會自動跑）

const assert = require('node:assert/strict');
const path = require('node:path');
const { FaceAudio, FaceLink } = require(path.join(__dirname, '..', '..', 'web', 'liveavatar.js'));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const pcm = (bytes, fill = 0) => btoa(String.fromCharCode(...new Uint8Array(bytes).fill(fill)));
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
console.warn = () => {}; // 「真人畫面出錯」的提醒不用印出來
console.debug = () => {};

// ------------------------------------------------------------------ 純邏輯

test('b64Bytes', () => {
  assert.equal(FaceAudio.b64Bytes(''), 0);
  assert.equal(FaceAudio.b64Bytes(btoa('abc')), 3);
  assert.equal(FaceAudio.b64Bytes(btoa('ab')), 2);
  assert.equal(FaceAudio.b64Bytes(btoa('a')), 1);
  assert.equal(FaceAudio.b64Bytes(pcm(4800)), 4800);
});

test('PcmBatcher：第一包小一點、之後固定大小，太大就切', () => {
  const b = new FaceAudio.PcmBatcher(4, 6);
  assert.deepEqual(b.push(btoa('ab')), []);
  assert.equal(b.size, 2);
  assert.deepEqual(b.push(btoa('cd')), [btoa('abcd')]); // 第一包 4
  assert.deepEqual(b.push(btoa('efghijklmnopqrst')), [btoa('efghij'), btoa('klmnop')]); // 之後每包 6
  assert.equal(b.size, 4);
  assert.deepEqual(b.flush(), [btoa('qrst')]);
  assert.deepEqual(b.flush(), []);
  b.reset(); // 下一句：第一包重新算
  assert.deepEqual(b.push(btoa('uvwx')), [btoa('uvwx')]);
  b.push(btoa('zz'));
  b.reset();
  assert.deepEqual(b.flush(), []);
  assert.equal(new FaceAudio.PcmBatcher(5, 7).first, 4); // 一包一定是完整的 16-bit 樣本
  const d = new FaceAudio.PcmBatcher();
  assert.equal(d.first, 9600); // 第一包約 0.2 秒：越小客戶越早開口
  assert.equal(d.next, 24000); // 之後約 0.5 秒
  assert.ok(d.next * (4 / 3) < 1024 * 1024); // base64 後也遠低於 1 MB
});

test('heardShare 和原本自己播時的算法一樣', () => {
  assert.equal(FaceAudio.heardShare(0, 0, 5, 10), 0); // 還沒出聲
  assert.equal(FaceAudio.heardShare(10, 12, 11, 6), 0.5);
  assert.equal(FaceAudio.heardShare(10, 12, 20, 6), 1);
  assert.equal(FaceAudio.heardShare(10, 12, 9, 6), 0);
  assert.equal(FaceAudio.heardShare(10, 11, 11, 60), 0.1); // 字幕很長、聲音還沒到齊：用每秒 6 個字估
  assert.equal(FaceAudio.heardShare(10.6, 12, 10.2, 6), 0); // 開口通知到了、聲音還在路上：還沒聽到
  assert.ok(FaceAudio.HEARD_LAG_SEC > 0.3 && FaceAudio.HEARD_LAG_SEC < 1); // 實測約 0.6～0.7 秒
});

test('pcmTail 跳過講過的、只留完整樣本', () => {
  const chunks = [btoa('abcd'), btoa('ef')];
  assert.equal(FaceAudio.pcmTail(chunks, 0), btoa('abcdef'));
  assert.equal(FaceAudio.pcmTail(chunks, 3), btoa('cdef'));
  assert.equal(FaceAudio.pcmTail(chunks, 6), '');
  assert.equal(FaceAudio.pcmTail(chunks, 100), '');
});

// ------------------------------------------------------------------ 假的 WebSocket、LiveKit、伺服器

function harness({ connectDelay = 0, failConnect = false, video = true, api, livekitFails = false, ...extra } = {}) {
  const h = { sockets: [], rooms: [], stops: [], calls: [], started: [], ended: [], failed: [], blocked: 0 };
  h.session = {
    session_id: '11111111-2222-4333-8444-555555555555', session_token: 'st', livekit_url: 'wss://lk.test',
    livekit_client_token: 'ct', ws_url: 'wss://ws.test/v1/ws/1', max_session_duration: 1200, sandbox: false, name: 'Ann',
  };
  class FakeWS {
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; h.sockets.push(this); }
    send(s) { this.sent.push(JSON.parse(s)); }
    close() { this.readyState = 3; }
    emit(m) { this.onmessage?.({ data: JSON.stringify(m) }); }
    connected() { this.readyState = 1; this.emit({ type: 'session.state_updated', state: 'connected', event_id: 'x', source_event_id: null }); }
    drop() { this.readyState = 3; this.onclose?.({ code: 1006 }); }
    types() { return this.sent.map((m) => m.type); }
  }
  const track = (kind) => ({ kind, attach(el) { el.attached = kind; return el; } });
  class Room {
    constructor(opts) { this.opts = opts; this.handlers = {}; h.rooms.push(this); }
    on(ev, fn) { (this.handlers[ev] ||= []).push(fn); return this; }
    emit(ev, ...a) { (this.handlers[ev] || []).forEach((f) => f(...a)); }
    removeAllListeners() { this.handlers = {}; }
    async connect(url, token) {
      this.url = url;
      this.token = token;
      await sleep(connectDelay);
      if (failConnect) throw new Error('could not establish signal connection');
      if (video) {
        setTimeout(() => {
          this.emit('trackSubscribed', track('video'), {}, { identity: 'avatar' });
          this.emit('trackSubscribed', track('audio'), {}, { identity: 'avatar' });
        }, 5);
      }
    }
    async disconnect() { this.disconnected = true; }
    async startAudio() { this.audioStarted = true; }
  }
  const lib = {
    Room,
    RoomEvent: {
      TrackSubscribed: 'trackSubscribed', Disconnected: 'disconnected', ParticipantDisconnected: 'participantDisconnected',
      AudioPlaybackStatusChanged: 'audioPlaybackChanged',
    },
    Track: { Kind: { Video: 'video', Audio: 'audio' } },
  };
  h.video = {};
  h.audio = { isConnected: true, removed: 0, remove() { this.removed += 1; this.isConnected = false; } };
  h.link = FaceLink.open({
    avatar: 'chen',
    api: api || (async (p, body) => { h.calls.push([p, body]); return h.session; }),
    video: h.video,
    audio: h.audio,
    livekit: async () => { if (livekitFails) throw new Error('載入不了'); return lib; },
    WebSocket: FakeWS,
    stopSession: (id, beacon) => h.stops.push([id, beacon]),
    onStart: (id) => h.started.push(id),
    onEnd: (id) => h.ended.push(id),
    onFail: (why) => h.failed.push(why),
    onAudioBlocked: () => { h.blocked += 1; },
    connectMs: 150, readyMs: 600, keepAliveMs: 40, flushMs: 20, errorWindowMs: 1000,
    ...extra,
  });
  h.ws = () => h.sockets[0];
  h.speaks = () => h.ws().sent.filter((m) => m.type === 'agent.speak');
  return h;
}

async function connected(opts) {
  const h = harness(opts);
  await sleep(5);
  h.ws().connected();
  await h.link.ready;
  return h;
}

// ------------------------------------------------------------------ 連線

test('接通：要了工作階段、進了房間、控制連線說 connected、影像也到了', async () => {
  const h = await connected();
  assert.deepEqual(h.calls, [['/api/avatar/session', { avatar: 'chen' }]]);
  assert.equal(h.ws().url, h.session.ws_url);
  assert.equal(h.rooms[0].url, 'wss://lk.test');
  assert.equal(h.rooms[0].token, 'ct');
  assert.equal(h.video.attached, 'video');
  assert.equal(h.audio.attached, 'audio'); // 真人的聲音接在 audio 上，不在本機播 Gemini 的聲音
  assert.equal(h.link.name, 'Ann');
  assert.equal(h.link.sessionId, h.session.session_id);
  h.link.close();
  assert.deepEqual(h.stops, [[h.session.session_id, false]]);
  assert.ok(h.rooms[0].disconnected);
  assert.equal(h.ws().readyState, 3);
  h.link.close(); // 重複關沒關係
  assert.equal(h.stops.length, 1);
});

test('connected 之前不送任何指令', async () => {
  const h = harness();
  await sleep(20);
  assert.equal(h.link.speak(pcm(4800)), ''); // 還沒好：不收
  assert.deepEqual(h.ws().sent, []);
  h.ws().connected();
  await h.link.ready;
  h.link.close();
});

// ------------------------------------------------------------------ 講話、封口、插話

test('第一包 0.2 秒、之後 0.5 秒，同一句用同一個 event_id，講完一定封口', async () => {
  const h = await connected();
  const id = h.link.speak(pcm(4800));
  assert.match(id, UUID);
  assert.equal(h.speaks().length, 0); // 0.1 秒：還不夠
  assert.equal(h.link.speak(pcm(4800)), id);
  assert.equal(h.speaks().length, 1);
  assert.equal(FaceAudio.b64Bytes(h.speaks()[0].audio), 9600);
  h.link.speak(pcm(24000 + 2400)); // Gemini 一次給很多：切成 0.5 秒一包，剩下的等一下
  assert.equal(h.speaks().length, 2);
  assert.equal(FaceAudio.b64Bytes(h.speaks()[1].audio), 24000);
  await sleep(40); // 等不到新的聲音：剩下的先送
  assert.equal(h.speaks().length, 3);
  assert.equal(FaceAudio.b64Bytes(h.speaks()[2].audio), 2400);
  h.link.speak(pcm(1000));
  h.link.endTurn();
  const sent = h.ws().sent.filter((m) => m.type.startsWith('agent.'));
  assert.deepEqual(sent.map((m) => m.type), ['agent.speak', 'agent.speak', 'agent.speak', 'agent.speak', 'agent.speak_end']);
  assert.ok(sent.every((m) => m.event_id === id));
  assert.ok(h.link.busy); // 封口了，但真人還沒講完
  const next = h.link.speak(pcm(9600));
  assert.notEqual(next, id); // 下一句換新的
  assert.equal(FaceAudio.b64Bytes(h.speaks().at(-1).audio), 9600); // 第一包重新算
  h.link.close();
});

test('聲音來得慢：最多等 flushMs 就送，不會一直往後延', async () => {
  const h = await connected({ flushMs: 60 });
  h.link.speak(pcm(2400));
  await sleep(35);
  h.link.speak(pcm(2400)); // 不重新計時
  await sleep(40);
  assert.equal(h.speaks().length, 1);
  assert.equal(FaceAudio.b64Bytes(h.speaks()[0].audio), 4800);
  h.link.close();
});

test('開始、講完的通知對得上這句才算；講完就換成在聽', async () => {
  const h = await connected();
  const id = h.link.speak(pcm(19200));
  h.link.endTurn();
  h.ws().emit({ type: 'agent.speak_started', event_id: 'e1', source_event_id: 'someone-else' });
  h.ws().emit({ type: 'agent.speak_started', event_id: 'e2', source_event_id: id });
  assert.deepEqual(h.started, [id]);
  assert.ok(!h.ws().types().includes('agent.start_listening'));
  h.ws().emit({ type: 'agent.speak_ended', event_id: 'e3', source_event_id: id });
  h.ws().emit({ type: 'agent.speak_ended', event_id: 'e4', source_event_id: id });
  assert.deepEqual(h.ended, [id]);
  assert.equal(h.link.busy, false);
  assert.equal(h.ws().types().filter((t) => t === 'agent.start_listening').length, 1);
  // 整合指南的範例把這句的代號放在 event_id：也要認得
  const id2 = h.link.speak(pcm(19200));
  h.link.endTurn();
  h.ws().emit({ type: 'agent.speak_started', event_id: id2, task: { id: 't' } });
  h.ws().emit({ type: 'agent.speak_ended', event_id: id2, task: { id: 't' } });
  assert.deepEqual(h.started, [id, id2]);
  assert.deepEqual(h.ended, [id, id2]);
  h.link.close();
});

test('插話：先停送、丟掉還沒送的，再送 agent.interrupt，然後換成在聽', async () => {
  const h = await connected();
  const id = h.link.speak(pcm(9600));
  h.link.speak(pcm(4800)); // 這段還在湊
  h.link.interrupt();
  await sleep(40);
  const types = h.ws().types();
  assert.deepEqual(types.filter((t) => t.startsWith('agent.')), ['agent.speak', 'agent.interrupt', 'agent.start_listening']);
  assert.equal(h.link.busy, false);
  h.ws().emit({ type: 'agent.speak_interrupted', event_id: 'e', source_event_id: id });
  assert.deepEqual(h.ended, []); // 已經當作停了，不再通知
  h.link.interrupt(); // 沒在講話就不送
  assert.equal(h.ws().types().filter((t) => t === 'agent.interrupt').length, 1);
  assert.notEqual(h.link.speak(pcm(4800)), id);
  h.link.close();
});

test('業務開口換成在聽、講完回到平常；真人在講話時不換', async () => {
  const h = await connected();
  const count = (t) => h.ws().types().filter((x) => x === t).length;
  h.link.listen(true);
  h.link.listen(true);
  assert.equal(count('agent.start_listening'), 1);
  h.link.listen(false);
  h.link.listen(false);
  assert.equal(count('agent.stop_listening'), 1);
  h.link.speak(pcm(4800));
  h.link.listen(true); // 正在講：不換
  assert.equal(count('agent.start_listening'), 1);
  assert.ok(h.ws().sent.filter((m) => m.type.endsWith('_listening')).every((m) => UUID.test(m.event_id)));
  h.link.close();
});

test('報平安（這裡縮短成 40ms）', async () => {
  const h = await connected();
  await sleep(110);
  const keep = h.ws().sent.filter((m) => m.type === 'session.keep_alive');
  assert.ok(keep.length >= 2);
  assert.ok(keep.every((m) => UUID.test(m.event_id)));
  assert.equal(new Set(keep.map((m) => m.event_id)).size, keep.length);
  const n = h.ws().sent.length;
  h.link.close();
  await sleep(60);
  assert.equal(h.ws().sent.length, n); // 關了就不再送
});

// 實測（沙盒）的事件順序：打斷後先 speak_interrupted、audio_buffer_cleared，接著還會再來一次 speak_ended
test('實測順序：打斷後 interrupted 和 ended 都來，不重複通知、不多送「在聽」', async () => {
  const h = await connected();
  const ev = (type, extra = {}) => h.ws().emit({ type, event_id: `srv-${Math.random()}`, ...extra });
  ev('agent.state_updated', { previous_state: 'idle', new_state: 'listening', source_event_id: null });
  const id = h.link.speak(pcm(28800));
  ev('agent.audio_buffer_appended', { source_event_id: id });
  ev('agent.speak_started', { source_event_id: id });
  ev('agent.state_updated', { previous_state: 'listening', new_state: 'talking', source_event_id: id });
  assert.deepEqual(h.started, [id]);
  h.link.interrupt();
  ev('agent.speak_interrupted', { source_event_id: id });
  ev('agent.audio_buffer_cleared', { source_event_id: 'x' });
  ev('agent.speak_ended', { source_event_id: id });
  ev('agent.state_updated', { previous_state: 'talking', new_state: 'idle', source_event_id: null });
  assert.deepEqual(h.ended, []);
  const types = h.ws().types();
  assert.equal(types.filter((t) => t === 'agent.interrupt').length, 1);
  assert.equal(types.filter((t) => t === 'agent.start_listening').length, 1); // 只有打斷時那一次
  // 正常講完：committed、started、ended、talking→idle
  const id2 = h.link.speak(pcm(28800));
  h.link.endTurn();
  ev('agent.audio_buffer_committed', { source_event_id: id2 });
  ev('agent.speak_started', { source_event_id: id2 });
  ev('agent.speak_ended', { source_event_id: id2 });
  ev('agent.state_updated', { previous_state: 'talking', new_state: 'idle', source_event_id: null });
  assert.deepEqual(h.started, [id, id2]);
  assert.deepEqual(h.ended, [id2]);
  assert.equal(h.ws().types().filter((t) => t === 'agent.start_listening').length, 2);
  h.link.close();
});

test('瀏覽器擋住自動播放：通知呼叫端；關掉時把聲音元素拿掉', async () => {
  const h = await connected();
  h.rooms[0].canPlaybackAudio = true;
  h.rooms[0].emit('audioPlaybackChanged');
  assert.equal(h.blocked, 0);
  h.rooms[0].canPlaybackAudio = false;
  h.rooms[0].emit('audioPlaybackChanged');
  assert.equal(h.blocked, 1);
  h.link.close();
  assert.equal(h.audio.removed, 1);
  assert.equal(h.audio.srcObject, null);
});

// ------------------------------------------------------------------ 連不上、中途斷線

test('伺服器開不起來：ready 失敗、帶著伺服器的原因，沒有工作階段要收', async () => {
  const h = harness({ api: async () => { throw new Error('還沒幫陳先生挑真人臉孔'); } });
  await assert.rejects(h.link.ready, /挑真人臉孔/);
  assert.deepEqual(h.stops, []);
});

test('控制連線一直沒說 connected：逾時、收掉工作階段', async () => {
  const h = harness();
  await assert.rejects(h.link.ready, /控制連線沒有回應/);
  assert.equal(h.stops.length, 1);
  assert.ok(h.rooms[0].disconnected);
});

test('控制連線一開就斷', async () => {
  const h = harness();
  await sleep(5);
  h.ws().drop();
  await assert.rejects(h.link.ready, /控制連線開不起來/);
  assert.equal(h.stops.length, 1);
});

test('LiveKit 連不上（擋住或逾時）', async () => {
  let h = harness({ failConnect: true });
  await sleep(5);
  h.ws().connected();
  await assert.rejects(h.link.ready, /LiveKit/);
  assert.equal(h.stops.length, 1);
  h = harness({ connectDelay: 400 });
  await sleep(5);
  h.ws().connected();
  await assert.rejects(h.link.ready, /LiveKit/);
});

test('影像一直沒出來：響鈴後一段時間就放棄', async () => {
  const h = harness({ video: false, readyMs: 120 });
  await sleep(5);
  h.ws().connected();
  await assert.rejects(h.link.ready, /太久沒出來/);
  assert.equal(h.stops.length, 1);
});

test('LiveKit 元件載入失敗：剛開的工作階段也要收掉', async () => {
  const h = harness({ livekitFails: true });
  await assert.rejects(h.link.ready, /載入不了/);
  assert.equal(h.stops.length, 1);
});

test('響鈴時就掛斷：伺服器晚到的工作階段馬上收掉', async () => {
  const h = harness({ api: async () => { await sleep(40); return h.session; } });
  h.link.close();
  await assert.rejects(h.link.ready, /cancelled/);
  await sleep(60);
  assert.deepEqual(h.stops, [[h.session.session_id, false]]);
  assert.equal(h.sockets.length, 0);
});

test('關分頁：用 beacon 收', async () => {
  const h = await connected();
  h.link.close('pagehide');
  assert.deepEqual(h.stops, [[h.session.session_id, true]]);
});

test('接通後斷線（例如測試模式 1 分鐘到了）：通知一次、收掉、之後不再送', async () => {
  const h = await connected({});
  h.session.sandbox = true;
  h.ws().drop();
  h.ws().drop();
  assert.deepEqual(h.failed, ['測試模式的真人影像時間到了']);
  assert.equal(h.stops.length, 1);
  assert.equal(h.link.speak(pcm(4800)), '');
});

test('控制連線說 closed／closing／disconnected 都算結束', async () => {
  for (const s of ['closed', 'closing', 'disconnected']) {
    const h = await connected();
    h.ws().emit({ type: 'session.state_updated', state: s });
    assert.deepEqual(h.failed, ['真人畫面斷線了'], s);
  }
});

test('房間斷了、真人離開房間也算斷線', async () => {
  let h = await connected();
  h.rooms[0].emit('participantDisconnected', { identity: 'someone' });
  assert.deepEqual(h.failed, []);
  h.rooms[0].emit('participantDisconnected', { identity: 'avatar' });
  assert.deepEqual(h.failed, ['真人畫面斷線了']);
  h = await connected();
  h.rooms[0].emit('disconnected');
  assert.deepEqual(h.failed, ['真人畫面斷線了']);
});

test('一直出錯：30 秒內 3 次就放棄', async () => {
  const h = await connected();
  const err = { type: 'error', error: { type: 'server_error', message: 'boom' }, source_event_id: null };
  h.ws().emit(err);
  h.ws().emit({ type: 'warning', warning: { type: 'video_starvation', message: 'gap' } });
  h.ws().emit(err);
  assert.deepEqual(h.failed, []);
  h.ws().emit(err);
  assert.deepEqual(h.failed, ['真人畫面一直出錯']);
  assert.equal(h.stops.length, 1);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (e) {
      failed += 1;
      console.log(`FAIL - ${name}\n${e?.stack || e}`);
    }
  }
  console.log(failed ? `${failed} of ${tests.length} failed` : `all ${tests.length} ok`);
  process.exit(failed ? 1 : 0);
})();
