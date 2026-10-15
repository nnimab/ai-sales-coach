'use strict';

// 真人模式（HeyGen LiveAvatar，LITE「Avatar Only」）：客戶的聲音還是 Gemini Live 的，
// 這裡把它轉給 LiveAvatar 對嘴，真人影像和聲音再從 LiveKit 房間傳回來。只有開了真人模式的通話才會載入 LiveKit。

// ------------------------------------------------------------------ 聲音的小工具（不碰畫面，Node 也能測）

const FaceAudio = (() => {
  const BYTES_PER_SEC = 48000; // 24kHz、16-bit、單聲道（Gemini Live 給的就是這個格式，原樣轉過去，不能重新取樣）
  // 官方建議第一包 0.6 秒、之後 1 秒，但要先湊滿才送，客戶開口會再晚 0.2～0.4 秒。
  // 實測（沙盒）第一包 0.1～0.6 秒、之後 0.5 秒，真人開口的時間都差不多，也沒有 video_starvation，所以送小包
  const FIRST_BYTES = 9600; // 每句第一包約 0.2 秒
  const NEXT_BYTES = 24000; // 之後每包約 0.5 秒（base64 後約 32 KB，遠低於 1 MB 的上限）
  // 實測（沙盒）：收到 speak_started 之後大約 0.6～0.7 秒，聲音才經過 LiveKit 傳到業務耳朵
  const HEARD_LAG_SEC = 0.6;

  function b64Bytes(b64) {
    const n = String(b64 || '').length;
    if (!n) return 0;
    return Math.floor((n * 3) / 4) - (b64.endsWith('==') ? 2 : b64.endsWith('=') ? 1 : 0);
  }

  // Gemini 一次給的聲音很短（幾十毫秒），湊成一包再送：每句第一包 0.2 秒、之後 0.5 秒
  class PcmBatcher {
    constructor(first = FIRST_BYTES, next = NEXT_BYTES) {
      this.first = first - (first % 2); // 一包一定是完整的 16-bit 樣本
      this.next = next - (next % 2);
      this.buf = ''; // 位元組字串（atob 的結果），接起來最省事
      this.started = false; // 這句已經送過第一包
    }

    get size() { return this.buf.length; }

    get target() { return this.started ? this.next : this.first; }

    push(b64) {
      this.buf += atob(b64);
      const out = [];
      while (this.buf.length >= this.target) {
        const n = this.target;
        out.push(btoa(this.buf.slice(0, n)));
        this.buf = this.buf.slice(n);
        this.started = true;
      }
      return out;
    }

    // 不夠一包也先送（等太久或這句講完了）
    flush() {
      if (!this.buf.length) return [];
      const out = [btoa(this.buf)];
      this.buf = '';
      this.started = true;
      return out;
    }

    // 換下一句（或被打斷）：還沒送的丟掉，第一包重新算
    reset() {
      this.buf = '';
      this.started = false;
    }
  }

  // 客戶被打斷時，業務大概聽到這句的幾成。字幕比聲音早到很多：用大約每秒 6 個字估整句要講多久。
  // start 是開始出聲的時間（秒），還沒出聲就是 0（一個字都沒聽到）
  function heardShare(start, end, now, chars) {
    if (!start) return 0;
    const total = Math.max(end - start, chars / 6);
    if (!(total > 0)) return 1;
    return Math.min(1, Math.max(0, (now - start) / total));
  }

  // 真人畫面中途斷了：這句已經講過的部分跳過，剩下的改在本機播
  function pcmTail(chunks, skipBytes) {
    const all = chunks.map((c) => atob(c)).join('');
    const skip = Math.max(0, Math.floor(skipBytes)) & ~1;
    return skip >= all.length ? '' : btoa(all.slice(skip));
  }

  return { BYTES_PER_SEC, FIRST_BYTES, NEXT_BYTES, HEARD_LAG_SEC, b64Bytes, PcmBatcher, heardShare, pcmTail };
})();

// ------------------------------------------------------------------ 連線：LiveKit 房間（看和聽）＋ WebSocket（叫它講話）

const FaceLink = (() => {
  // 固定版本加上完整性檢查：CDN 上的檔案被換掉，瀏覽器就不會執行
  const LIVEKIT_SRC = 'https://cdn.jsdelivr.net/npm/livekit-client@2.22.3/dist/livekit-client.umd.js';
  const LIVEKIT_SRI = 'sha384-G/xxtkVytOx/ia9Q8MXxM+V0ohsaY1fZAgVP3iSGTPz4wJ0s3+ulJNKXh/gnzEDZ';
  const DEFAULTS = {
    connectMs: 8000, // LiveKit 房間、控制連線各等 8 秒
    readyMs: 15000, // 從響鈴算起，15 秒內真人畫面沒出來就改用語音
    keepAliveMs: 120000, // 閒置 5 分鐘會被收掉：官方建議每 2～3 分鐘報一次平安
    flushMs: 100, // 不夠一包時最多等這麼久就先送：等越久，客戶開口越晚
    errorWindowMs: 30000, // 30 秒內出錯 3 次就放棄
    maxErrors: 3,
  };
  const END_STATES = new Set(['disconnected', 'closed', 'closing']); // 兩份官方文件寫法不同，都當作結束
  const open = new Set(); // 還開著的真人畫面：關分頁時通知伺服器收掉

  let loading = null;
  function loadLivekit() {
    if (globalThis.LivekitClient) return Promise.resolve(globalThis.LivekitClient);
    if (!loading) {
      loading = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = LIVEKIT_SRC;
        s.integrity = LIVEKIT_SRI;
        s.crossOrigin = 'anonymous';
        s.async = true;
        s.onload = () => (globalThis.LivekitClient ? resolve(globalThis.LivekitClient) : s.onerror());
        s.onerror = () => {
          loading = null; // 下一通再試
          s.remove();
          reject(new Error('載入不了真人畫面的元件（cdn.jsdelivr.net），請檢查網路'));
        };
        document.head.appendChild(s);
      });
    }
    return loading;
  }

  // 伺服器用金鑰收掉工作階段。關分頁時用 sendBeacon（頁面關了也送得出去），不行再用 keepalive 的 fetch
  function stopRemote(id, beacon) {
    const body = JSON.stringify({ session_id: id });
    if (beacon && navigator.sendBeacon) {
      try {
        if (navigator.sendBeacon('/api/avatar/stop', new Blob([body], { type: 'application/json' }))) return;
      } catch { /* 有的瀏覽器不收 JSON 的 beacon */ }
    }
    fetch('/api/avatar/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true })
      .catch(() => {});
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', () => [...open].forEach((link) => link.close('pagehide')));
  }

  function uuid() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  function withTimeout(promise, ms, message) {
    let t = 0;
    const timer = new Promise((_, reject) => { t = setTimeout(() => reject(new Error(message)), ms); });
    return Promise.race([promise, timer]).finally(() => clearTimeout(t));
  }

  // opts：avatar（我們的頭像代號）、api(path, body)、video／audio（要接上影像和聲音的元素）、
  // onStart(id)／onEnd(id) 真人開始、講完一句，onFail(原因) 接通後才斷掉，onAudioBlocked() 瀏覽器擋住了聲音。
  // 打斷之後 LiveAvatar 會送 speak_interrupted，接著還會再送一次 speak_ended（實測）：句子在打斷時就拿掉了，兩個都不再通知。
  // 測試可以換掉 livekit、WebSocket、stopSession 和各種等待時間
  function connect(opts) {
    const o = { livekit: loadLivekit, WebSocket: globalThis.WebSocket, stopSession: stopRemote, ...DEFAULTS, ...opts };
    const batcher = new FaceAudio.PcmBatcher();
    const playing = new Set(); // 送出去、還沒講完（或被打斷）的句子
    let sess = null;
    let room = null;
    let ws = null;
    let utt = ''; // 正在送的這一句的 event_id；speak_end 或 interrupt 之後換新的
    let agent = 'idle';
    let speaker = ''; // 真人在 LiveKit 房間裡的代號
    let errors = [];
    let closed = false;
    let isReady = false;
    let flushTimer = 0;
    let keepTimer = 0;
    let readyTimer = 0;
    let settle = null;
    const ready = new Promise((resolve, reject) => { settle = { resolve, reject }; });
    ready.catch(() => {}); // 呼叫端自己決定要不要等

    const endText = () => (sess?.sandbox ? '測試模式的真人影像時間到了' : '真人畫面斷線了');

    function send(obj) {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
    }

    function shutdown(beacon) {
      closed = true;
      open.delete(link);
      clearTimeout(flushTimer);
      clearInterval(keepTimer);
      clearTimeout(readyTimer);
      batcher.reset();
      playing.clear();
      utt = '';
      const sock = ws;
      ws = null;
      if (sock) {
        sock.onmessage = null;
        sock.onclose = null;
        sock.onerror = null;
        try { sock.close(1000); } catch { /* 已經關了 */ }
      }
      const r = room;
      room = null;
      if (r) {
        try {
          r.removeAllListeners?.();
          Promise.resolve(r.disconnect()).catch(() => {}); // 馬上斷：關分頁時也來得及
        } catch { /* 已經斷了 */ }
      }
      [o.video, o.audio].forEach((el) => {
        try {
          if (el) el.srcObject = null;
        } catch { /* 沒關係 */ }
      });
      if (o.audio?.isConnected) o.audio.remove(); // 呼叫端放進頁面的聲音元素，一起收掉
      if (sess) o.stopSession(sess.session_id, beacon);
    }

    // 連不上或中途斷了：收乾淨；接通前就讓 ready 失敗，接通後通知呼叫端
    function fail(reason) {
      if (closed) return;
      shutdown(false);
      if (isReady) o.onFail?.(reason);
      else settle.reject(new Error(reason));
    }

    function close(why) {
      if (closed) return;
      shutdown(why === 'pagehide');
      if (!isReady) settle.reject(new Error('cancelled'));
    }

    function onEvent(m) {
      // 這句的代號：事件文件寫在 source_event_id，整合指南的範例放在 event_id，兩個都認
      const id = playing.has(m.source_event_id) ? m.source_event_id : playing.has(m.event_id) ? m.event_id : '';
      switch (m.type) {
        case 'session.state_updated':
          if (END_STATES.has(m.state)) fail(endText());
          break;
        case 'agent.state_updated':
          agent = m.new_state || agent;
          break;
        case 'agent.speak_started':
          if (id) o.onStart?.(id);
          break;
        case 'agent.speak_ended':
        case 'agent.speak_interrupted':
          if (id && playing.delete(id)) {
            o.onEnd?.(id);
            if (!utt && !playing.size) listen(true); // 講完了：換成在聽，等業務開口
          }
          break;
        case 'error': {
          const now = Date.now();
          errors = errors.filter((t) => now - t < o.errorWindowMs).concat(now);
          console.warn('[真人模式]', m.error?.type || '', m.error?.message || '');
          if (errors.length >= o.maxErrors) fail('真人畫面一直出錯');
          break;
        }
        case 'warning':
          console.debug('[真人模式]', m.warning?.type || '', m.warning?.message || '');
          break;
        default:
      }
    }

    function openSocket() {
      return new Promise((resolve, reject) => {
        let sock;
        try {
          sock = new o.WebSocket(sess.ws_url);
        } catch {
          reject(new Error('真人畫面的控制連線開不起來'));
          return;
        }
        ws = sock;
        const t = setTimeout(() => reject(new Error('真人畫面的控制連線沒有回應')), o.connectMs);
        sock.onmessage = (e) => {
          let m;
          try { m = JSON.parse(typeof e.data === 'string' ? e.data : new TextDecoder().decode(e.data)); } catch { return; }
          if (!m || typeof m !== 'object') return;
          // 要等它說 connected 才能下指令
          if (m.type === 'session.state_updated' && m.state === 'connected') { clearTimeout(t); resolve(); }
          onEvent(m);
        };
        sock.onclose = () => {
          clearTimeout(t);
          const why = isReady ? endText() : '真人畫面的控制連線開不起來';
          reject(new Error(why));
          fail(why);
        };
      });
    }

    // 瀏覽器預設會多緩衝一點讓播放更順；真人模式本來就比語音慢，請它盡量少緩衝、收到就播（不支援的瀏覽器就照舊）
    function lowLatency(track) {
      const rx = track?.receiver;
      if (!rx) return;
      try {
        if ('jitterBufferTarget' in rx) rx.jitterBufferTarget = 0;
        else if ('playoutDelayHint' in rx) rx.playoutDelayHint = 0;
      } catch { /* 不支援就算了 */ }
    }

    async function joinRoom(lk, gotVideo) {
      const { Room, RoomEvent, Track } = lk;
      room = new Room({ adaptiveStream: false, dynacast: false });
      room.on(RoomEvent.TrackSubscribed, (track, pub, participant) => {
        lowLatency(track);
        if (track.kind === Track.Kind.Video) {
          speaker = participant?.identity || speaker;
          track.attach(o.video);
          gotVideo();
        } else if (track.kind === Track.Kind.Audio) {
          track.attach(o.audio); // 真人的聲音：這通電話就聽這個，不在本機播 Gemini 的聲音
        }
      });
      room.on(RoomEvent.ParticipantDisconnected, (p) => { if (speaker && p?.identity === speaker) fail(endText()); });
      room.on(RoomEvent.Disconnected, () => fail(endText()));
      // 瀏覽器擋住自動播放：真人的聲音只從 LiveKit 來，等使用者點一下或按個鍵就接著播
      if (RoomEvent.AudioPlaybackStatusChanged) {
        room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
          if (!room || room.canPlaybackAudio !== false) return;
          o.onAudioBlocked?.();
          if (typeof document === 'undefined') return;
          const resume = () => {
            document.removeEventListener('pointerdown', resume, true);
            document.removeEventListener('keydown', resume, true);
            unlock();
          };
          document.addEventListener('pointerdown', resume, true);
          document.addEventListener('keydown', resume, true);
        });
      }
      try {
        await withTimeout(room.connect(sess.livekit_url, sess.livekit_client_token, { autoSubscribe: true }), o.connectMs, '');
      } catch {
        throw new Error('連不上真人畫面（LiveKit），可能是網路擋住了');
      }
      Promise.resolve().then(() => room?.startAudio?.()).catch(() => {});
    }

    async function run() {
      readyTimer = setTimeout(() => fail('真人畫面太久沒出來'), o.readyMs);
      const lkLoad = o.livekit();
      lkLoad.catch(() => {});
      const s = await o.api('/api/avatar/session', { avatar: o.avatar });
      if (!s?.session_id || !s.livekit_url || !s.ws_url) throw new Error('伺服器沒有給真人畫面的連線資料');
      if (closed) { o.stopSession(s.session_id, false); return; } // 已經掛斷：剛開好的馬上收掉
      sess = s;
      open.add(link);
      const lk = await lkLoad;
      if (closed) return;
      let gotVideo = null;
      const video = new Promise((resolve) => { gotVideo = resolve; });
      await Promise.all([joinRoom(lk, gotVideo), openSocket()]);
      await video;
      if (closed) return;
      isReady = true;
      clearTimeout(readyTimer);
      keepTimer = setInterval(() => send({ type: 'session.keep_alive', event_id: uuid() }), o.keepAliveMs);
      settle.resolve(link);
    }

    function sendAudio(audio) {
      send({ type: 'agent.speak', event_id: utt, audio });
    }

    function flushLater() {
      clearTimeout(flushTimer);
      flushTimer = 0;
      if (!batcher.size) return;
      flushTimer = setTimeout(() => {
        flushTimer = 0;
        batcher.flush().forEach(sendAudio);
      }, o.flushMs);
    }

    // Gemini 給一段聲音就丟進來；回傳這句的 event_id（同一句的每一包都用它，真人開始、講完時也用它對）
    function speak(b64) {
      if (closed || !isReady || !b64) return '';
      if (!utt) {
        utt = uuid();
        playing.add(utt);
        batcher.reset();
      }
      const frames = batcher.push(b64);
      frames.forEach(sendAudio);
      // 剩下不夠一包的：送出一包後重新計時；還在等的就不延後，最多等 flushMs
      if (frames.length || !flushTimer) flushLater();
      return utt;
    }

    // Gemini 這一輪講完：剩下的聲音送出去，一定要封口（不封口真人不會回到平常的樣子）
    function endTurn() {
      if (closed || !utt) return;
      clearTimeout(flushTimer);
      flushTimer = 0;
      batcher.flush().forEach(sendAudio);
      send({ type: 'agent.speak_end', event_id: utt });
      utt = '';
    }

    // 業務插話：先停止送聲音、丟掉還沒送的，再叫真人閉嘴，然後換成在聽
    function interrupt() {
      clearTimeout(flushTimer);
      flushTimer = 0;
      batcher.reset();
      const busy = !!utt || playing.size > 0;
      utt = '';
      playing.clear();
      if (!busy || closed) return;
      send({ type: 'agent.interrupt', event_id: uuid() });
      agent = 'idle';
      listen(true);
    }

    // 業務開口（true）、講完（false）：真人沒在講話時，換成在聽／回到平常的樣子
    function listen(on) {
      if (closed || !isReady || utt || playing.size) return;
      if (on === (agent === 'listening')) return;
      agent = on ? 'listening' : 'idle';
      send({ type: on ? 'agent.start_listening' : 'agent.stop_listening', event_id: uuid() });
    }

    // 在使用者的點擊裡呼叫：讓瀏覽器允許真人的聲音播出來
    function unlock() {
      Promise.resolve().then(() => room?.startAudio?.()).catch(() => {});
      Promise.resolve().then(() => (o.audio?.srcObject ? o.audio.play() : null)).catch(() => {});
    }

    const link = {
      ready, speak, endTurn, interrupt, listen, unlock, close,
      get busy() { return !!utt || playing.size > 0; },
      get name() { return sess?.name || ''; },
      get sandbox() { return !!sess?.sandbox; },
      get sessionId() { return sess?.session_id || ''; },
    };
    run().catch((e) => fail(e?.message || '真人模式連不上'));
    return link;
  }

  return { open: connect, loadLivekit };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = { FaceAudio, FaceLink };
