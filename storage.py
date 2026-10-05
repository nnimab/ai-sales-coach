"""資料存放：本機用檔案（和以前的檔案格式一模一樣），雲端（Vercel）用 Upstash Redis，
還沒接資料庫時暫存在記憶體。只用 Python 標準函式庫；cryptography 只在雲端存登入資料時才載入。"""

import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

# 網頁上可以改、要記住的設定（本機寫進 .env，雲端寫進 Redis）；金鑰不放這裡
RUNTIME_KEYS = ("LLM_PROVIDER", "LIVE_ENABLED", "CHATGPT_MODEL", "CHATGPT_COACH_MODEL", "CLOUD_URL")
_CACHE_EXT = {"audio/wav": ".wav", "audio/mpeg": ".mp3"}
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")  # 登入資料的名稱會變成檔名，不能有路徑字元
_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{16,128}$")
TTS_MAX_BYTES = 4_000_000  # 太大的聲音不放雲端快取（Vercel 回應上限 4.5 MB）
_UNLOCK = 'if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) else return 0 end'


class StoreError(Exception):
    """資料庫連不上或設定錯誤。訊息會給使用者看，不能帶金鑰。"""


def _log(msg):
    print(f"[資料] {msg}", file=sys.stderr)


def atomic_write_text(path, text, private=False):
    """先寫到同資料夾的暫存檔再換名：寫到一半當掉或斷電，原本的檔案也不會壞掉。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + "-", suffix=".tmp")
    try:
        if private:
            try:
                os.chmod(tmp, 0o600)  # 只有自己能讀；Windows 不支援就算了
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows：檔案剛好被別的執行緒、防毒或編輯器開著
                if attempt == 4:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def set_env_value(env_path, name, value):
    env_path = Path(env_path)
    value = str(value)
    if "\n" in value or "\r" in value or "\n" in name or "=" in name:
        raise ValueError("設定值不能有換行")  # 不然會在 .env 多塞出一行設定
    lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
    out, found = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name:
            if not found:
                out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={value}")
    atomic_write_text(env_path, "\n".join(out) + "\n")


def parse_env_text(text):
    """和 server.load_env 讀 .env 的規則一樣。"""
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


def _check_key(key):
    if key not in RUNTIME_KEYS:
        raise ValueError(f"不能在網頁上改的設定：{key}")


def _dump_doc(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _load_doc(text):
    data = json.loads(text)  # 格式壞掉會丟 ValueError
    if not isinstance(data, dict):
        raise ValueError("資料格式不對")
    return data


def _stamp(text):
    return None if text is None else hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class Store:
    kind = ""
    durable = True
    warning = ""


class _Local:
    """短期資料和鎖放在這個程式的記憶體裡（本機和沒接資料庫的雲端都用這個）。"""

    def _init_local(self):
        self._eph = {}
        self._eph_lock = threading.Lock()
        self._locks = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def _now():
        return time.monotonic()

    def _eph_live(self, key):
        item = self._eph.get(key)
        if item is not None and item[0] <= self._now():
            del self._eph[key]
            return None
        return item

    def _eph_sweep(self):
        if len(self._eph) > 2000:
            now = self._now()
            for k in [k for k, v in self._eph.items() if v[0] <= now]:
                del self._eph[k]

    def eph_get(self, key):
        with self._eph_lock:
            item = self._eph_live(key)
        return json.loads(item[1]) if item else None

    def eph_set(self, key, value, ttl):
        text = json.dumps(value, ensure_ascii=False)
        with self._eph_lock:
            self._eph_sweep()
            self._eph[key] = (self._now() + float(ttl), text)

    def eph_pop(self, key):
        with self._eph_lock:
            item = self._eph_live(key)
            self._eph.pop(key, None)
        return json.loads(item[1]) if item else None

    def eph_incr(self, key, ttl):
        with self._eph_lock:
            item = self._eph_live(key)
            if item is None:
                n, expires = 1, self._now() + float(ttl)
            else:
                n, expires = int(json.loads(item[1])) + 1, item[0]
            self._eph[key] = (expires, json.dumps(n))
        return n

    @contextlib.contextmanager
    def lock(self, name, ttl=30.0, wait=15.0):
        with self._locks_guard:
            lk = self._locks.setdefault(name, threading.Lock())
        if not lk.acquire(timeout=max(0.0, float(wait))):
            raise StoreError("系統忙碌中，請稍後再試")
        try:
            yield
        finally:
            lk.release()


# ---------------------------------------------------------------- 本機：檔案


class FileStore(_Local, Store):
    """本機版：路徑和檔案格式都和以前一樣（.env、voices.json、profile.json、scenarios/custom、tts_cache）。"""

    kind = "file"
    durable = True

    def __init__(self, root):
        self.root = Path(root)
        self.env_path = self.root / ".env"
        self.voices_path = self.root / "voices.json"
        self.profile_path = self.root / "profile.json"  # 本機只有一位使用者，uid 不影響檔名
        self.custom_dir = self.root / "scenarios" / "custom"
        self.trash_dir = self.custom_dir / ".trash"
        self.tts_dir = self.root / "tts_cache"
        self.secret_dir = self.root / ".chatgpt"
        self._doc_cache = {}
        self._settings_cache = (None, {})
        self._cache_lock = threading.Lock()
        self._init_local()

    # ---- 設定

    def settings(self):
        try:
            st = self.env_path.stat()
        except OSError:
            return {}
        stamp = (st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))  # 換名寫入會換 inode，時間解析度不夠也分得出來
        with self._cache_lock:
            if self._settings_cache[0] == stamp:
                return dict(self._settings_cache[1])
        try:
            env = parse_env_text(self.env_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            return {}
        out = {k: env[k] for k in RUNTIME_KEYS if k in env}
        with self._cache_lock:
            self._settings_cache = (stamp, out)
        return dict(out)

    def set_setting(self, key, value):
        _check_key(key)
        set_env_value(self.env_path, key, value)
        with self._cache_lock:
            self._settings_cache = (None, {})

    # ---- 文件（voices、profile）

    def _doc_path(self, name):
        if name == "voices":
            return self.voices_path
        if name == "profile" or str(name).startswith("profile:"):
            return self.profile_path
        raise ValueError(f"不認得的資料：{name}")

    def get_doc(self, name):
        path = self._doc_path(name)
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ValueError(str(e)) from None
        stamp = (st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))
        with self._cache_lock:
            hit = self._doc_cache.get(path)
        if hit and hit[0] == stamp:
            return json.loads(hit[1])
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(str(e)) from None
        data = _load_doc(text)
        with self._cache_lock:
            self._doc_cache[path] = (stamp, json.dumps(data, ensure_ascii=False))
        return data

    def doc_stamp(self, name):
        try:
            return self._doc_path(name).stat().st_mtime_ns
        except OSError:
            return None

    def put_doc(self, name, doc):
        path = self._doc_path(name)
        atomic_write_text(path, _dump_doc(doc))
        with self._cache_lock:
            self._doc_cache.pop(path, None)

    def delete_doc(self, name):
        path = self._doc_path(name)
        path.unlink(missing_ok=True)
        with self._cache_lock:
            self._doc_cache.pop(path, None)

    # ---- 講師的情境（scenarios/custom）

    def list_custom(self):
        """[(檔名, 內容, 檔案)]。只讀這一層的 *.json，所以 .trash 和寫到一半的 .tmp 都不會被讀到。"""
        if not self.custom_dir.is_dir():
            return []
        out = []
        for file in sorted(self.custom_dir.glob("*.json")):
            try:
                text = file.read_text(encoding="utf-8-sig")
            except Exception as e:  # 單一情境檔讀不到時不要讓整個系統掛掉
                print(f"[情境] 無法讀取 {file.name}：{e}", file=sys.stderr)
                continue
            out.append((file.name, text, file))
        return out

    def custom_exists(self, sid):
        return bool(_SID_RE.match(str(sid))) and (self.custom_dir / f"{sid}.json").exists()

    def is_custom_ref(self, ref):
        if not isinstance(ref, Path):
            return False
        try:
            return ref.resolve().parent == self.custom_dir.resolve()
        except OSError:
            return False

    def save_custom(self, sid, json_text, ref=None):
        path = ref if self.is_custom_ref(ref) else self.custom_dir / f"{sid}.json"
        if path.resolve().parent != self.custom_dir.resolve():
            raise ValueError("情境檔的位置不對")
        atomic_write_text(path, json_text)
        return path

    def trash_custom(self, ref):
        if not self.is_custom_ref(ref):
            raise ValueError("情境檔的位置不對")
        path = ref
        # 不直接刪掉：先搬到 .trash，救得回來
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.trash_dir / f"{path.stem}-{stamp}.json"
        n = 1
        while dest.exists():
            n += 1
            dest = self.trash_dir / f"{path.stem}-{stamp}-{n}.json"
        for attempt in range(5):
            try:
                os.replace(path, dest)
                break
            except PermissionError:  # Windows：檔案剛好被別的程式開著
                if attempt == 4:
                    raise
                time.sleep(0.1)
        return dest.name

    # ---- 開場白語音快取（tts_cache）

    def tts_get(self, digest):
        if not _DIGEST_RE.match(str(digest)):
            return None
        for ctype, ext in _CACHE_EXT.items():
            path = self.tts_dir / (digest + ext)
            try:
                if path.exists():
                    return path.read_bytes(), ctype
            except OSError:
                return None
        return None

    def tts_put(self, digest, audio, ctype):
        try:
            if not _DIGEST_RE.match(str(digest)):
                return
            self.tts_dir.mkdir(exist_ok=True)
            (self.tts_dir / (digest + _CACHE_EXT.get(ctype, ".bin"))).write_bytes(audio)
        except Exception:
            pass

    # ---- 登入資料（.chatgpt/，只有自己能讀）

    def _secret_path(self, name):
        if not _NAME_RE.match(str(name)):
            raise ValueError("登入資料的名稱不對")
        return self.secret_dir / f"{name}.json"

    def get_secret(self, name):
        path = self._secret_path(name)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as e:
            _log(f".chatgpt/{name}.json 讀不到，先當作沒有：{type(e).__name__}")
            return None
        try:
            return _load_doc(text)
        except ValueError:
            _log(f".chatgpt/{name}.json 格式壞了，先當作沒有")
            return None

    def put_secret(self, name, value):
        path = self._secret_path(name)
        if not isinstance(value, dict):
            raise ValueError("登入資料格式不對")
        try:
            self.secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write_text(path, _dump_doc(value), private=True)
        except OSError as e:
            raise StoreError(f"存不了登入資料（{e.strerror or type(e).__name__}）") from None

    def delete_secret(self, name):
        path = self._secret_path(name)
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            raise StoreError(f"刪不掉登入資料（{e.strerror or type(e).__name__}）") from None


# ---------------------------------------------------------------- 沒接資料庫的雲端：記憶體


class MemoryStore(_Local, Store):
    """Vercel 還沒接 Upstash 時用：重新部署或換一台機器就不見了，畫面會提醒。"""

    kind = "memory"
    durable = False
    warning = "還沒連接資料庫（Upstash）：設定、情境和背景會在重新部署後不見"

    def __init__(self):
        self._data_lock = threading.Lock()
        self._settings = {}
        self._docs = {}
        self._custom = {}
        self._trash = {}
        self._tts = OrderedDict()
        self._secrets = {}
        self._init_local()

    def settings(self):
        with self._data_lock:
            return dict(self._settings)

    def set_setting(self, key, value):
        _check_key(key)
        with self._data_lock:
            self._settings[key] = str(value)

    def get_doc(self, name):
        with self._data_lock:
            text = self._docs.get(name)
        return None if text is None else _load_doc(text)

    def doc_stamp(self, name):
        with self._data_lock:
            return _stamp(self._docs.get(name))

    def put_doc(self, name, doc):
        text = json.dumps(doc, ensure_ascii=False)
        with self._data_lock:
            self._docs[name] = text

    def delete_doc(self, name):
        with self._data_lock:
            self._docs.pop(name, None)

    def list_custom(self):
        with self._data_lock:
            items = list(self._custom.items())
        return sorted(((f"{sid}.json", text, sid) for sid, text in items), key=lambda r: r[0])

    def custom_exists(self, sid):
        with self._data_lock:
            return sid in self._custom

    def is_custom_ref(self, ref):
        return isinstance(ref, str) and bool(_SID_RE.match(ref))

    def save_custom(self, sid, json_text, ref=None):
        field = ref if self.is_custom_ref(ref) else str(sid)
        if not _SID_RE.match(field):
            raise ValueError("情境檔的位置不對")
        with self._data_lock:
            self._custom[field] = json_text
        return field

    def trash_custom(self, ref):
        if not self.is_custom_ref(ref):
            raise ValueError("情境檔的位置不對")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        with self._data_lock:
            if ref not in self._custom:
                raise ValueError("找不到這個情境，可能已經被刪除")
            name, n = f"{ref}-{stamp}", 1
            while name in self._trash:
                n += 1
                name = f"{ref}-{stamp}-{n}"
            self._trash[name] = self._custom.pop(ref)
        return name

    def tts_get(self, digest):
        with self._data_lock:
            hit = self._tts.get(digest)
            if hit:
                self._tts.move_to_end(digest)
        return hit

    def tts_put(self, digest, audio, ctype):
        try:
            if len(audio) > TTS_MAX_BYTES:
                return
            with self._data_lock:
                self._tts[digest] = (bytes(audio), ctype)
                while len(self._tts) > 64:
                    self._tts.popitem(last=False)
        except Exception:
            pass

    def get_secret(self, name):
        with self._data_lock:
            text = self._secrets.get(name)
        return None if text is None else json.loads(text)

    def put_secret(self, name, value):
        if not isinstance(value, dict):
            raise ValueError("登入資料格式不對")
        with self._data_lock:
            self._secrets[name] = json.dumps(value, ensure_ascii=False)

    def delete_secret(self, name):
        with self._data_lock:
            self._secrets.pop(name, None)


# ---------------------------------------------------------------- 雲端：Upstash Redis（REST）


def _opener_for(url):
    host = (urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本機測試不要走代理
    return urllib.request.build_opener()


class RedisStore(Store):
    """Upstash REST API（https://upstash.com/docs/redis/features/restapi），每個 Vercel 執行個體各有一份短暫快取。"""

    kind = "redis"
    durable = True

    def __init__(self, url, token, prefix="aicoach:", cache_ttl=10.0, tts_ttl_days=30, app_secret="", timeout=5.0):
        self.url = url.rstrip("/")
        self._token = token
        self.prefix = prefix
        self.cache_ttl = float(cache_ttl)
        self.profile_ttl = min(3.0, self.cache_ttl)
        self.tts_ttl = max(1, int(float(tts_ttl_days) * 86400))
        self._app_secret = app_secret or ""
        self.timeout = float(timeout)
        self._opener = _opener_for(self.url)
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._tl = threading.local()  # 拿著鎖的時候一律讀最新的，不用快取（不然會蓋掉別台剛寫的）
        self._fernet = None
        self._bad_secret_logged = set()

    # ---- 連線

    def _k(self, name):
        return self.prefix + name

    def _post(self, path, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._token, "Content-Type": "application/json"}
        for attempt in (0, 1):
            req = urllib.request.Request(self.url + path, data=data, headers=headers, method="POST")
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    body = r.read()
                break
            except urllib.error.HTTPError as e:
                try:
                    detail = json.loads(e.read(2000).decode("utf-8", "replace")).get("error") or ""
                except (ValueError, AttributeError, OSError):
                    detail = ""
                if e.code in (401, 403):
                    raise StoreError("資料庫金鑰不對（KV_REST_API_TOKEN）") from None
                raise StoreError(f"資料庫回應錯誤 HTTP {e.code}：{str(detail)[:120]}") from None
            except urllib.error.URLError as e:  # 連不上（還沒送出）：再試一次
                if attempt == 1:
                    raise StoreError(f"連不到資料庫：{getattr(e, 'reason', e)}") from None
            except (TimeoutError, OSError) as e:  # 送出後才逾時：不重送，免得 INCR 或鎖重複執行
                raise StoreError(f"資料庫沒有回應：{type(e).__name__}") from None
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise StoreError("資料庫回傳的資料看不懂") from None

    @staticmethod
    def _args(cmd):
        return [a if isinstance(a, str) else str(a) for a in cmd]

    def cmd(self, *args):
        out = self._post("", self._args(args))
        if not isinstance(out, dict):
            raise StoreError("資料庫回傳的資料看不懂")
        if "error" in out:
            raise StoreError(f"資料庫錯誤：{str(out['error'])[:160]}")
        return out.get("result")

    def _batch(self, path, cmds):
        out = self._post(path, [self._args(c) for c in cmds])
        if isinstance(out, dict) and "error" in out:
            raise StoreError(f"資料庫錯誤：{str(out['error'])[:160]}")
        if not isinstance(out, list) or len(out) != len(cmds):
            raise StoreError("資料庫回傳的資料看不懂")
        results = []
        for item in out:
            if isinstance(item, dict) and "error" in item:
                raise StoreError(f"資料庫錯誤：{str(item['error'])[:160]}")
            results.append(item.get("result") if isinstance(item, dict) else item)
        return results

    def pipeline(self, cmds):
        return self._batch("/pipeline", cmds)

    def multi(self, cmds):
        return self._batch("/multi-exec", cmds)

    # ---- 快取

    def _cached(self, key, ttl, fetch):
        fresh = getattr(self._tl, "fresh", 0)
        now = time.monotonic()
        if not fresh:
            with self._cache_lock:
                hit = self._cache.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        value = fetch()
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + ttl, value)
        return value

    def _remember(self, key, value, ttl):
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + ttl, value)

    def _forget(self, key):
        with self._cache_lock:
            self._cache.pop(key, None)

    # ---- 設定

    def settings(self):
        def fetch():
            flat = self.cmd("HGETALL", self._k("settings")) or []
            return dict(zip(flat[0::2], flat[1::2])) if isinstance(flat, list) else dict(flat)
        data = self._cached("settings", self.cache_ttl, fetch)
        return {k: data[k] for k in RUNTIME_KEYS if k in data}

    def set_setting(self, key, value):
        _check_key(key)
        value = str(value)
        if "\n" in value or "\r" in value:
            raise ValueError("設定值不能有換行")
        self.cmd("HSET", self._k("settings"), key, value)
        self._forget("settings")

    # ---- 文件

    def _doc_ttl(self, name):
        return self.profile_ttl if str(name).startswith("profile:") else self.cache_ttl

    def _doc_raw(self, name):
        return self._cached("doc:" + name, self._doc_ttl(name), lambda: self.cmd("GET", self._k("doc:" + name)))

    def get_doc(self, name):
        raw = self._doc_raw(name)
        return None if raw is None else _load_doc(raw)

    def doc_stamp(self, name):
        return _stamp(self._doc_raw(name))

    def put_doc(self, name, doc):
        text = json.dumps(doc, ensure_ascii=False)
        self.cmd("SET", self._k("doc:" + name), text)
        self._remember("doc:" + name, text, self._doc_ttl(name))

    def delete_doc(self, name):
        self.cmd("DEL", self._k("doc:" + name))
        self._remember("doc:" + name, None, self._doc_ttl(name))

    # ---- 講師的情境：hash scenarios（欄位 = 情境 id）

    def _custom_all(self):
        def fetch():
            flat = self.cmd("HGETALL", self._k("scenarios")) or []
            return dict(zip(flat[0::2], flat[1::2])) if isinstance(flat, list) else dict(flat)
        return self._cached("scenarios", self.cache_ttl, fetch)

    def list_custom(self):
        return sorted(((f"{sid}.json", text, sid) for sid, text in self._custom_all().items()), key=lambda r: r[0])

    def custom_exists(self, sid):
        return bool(_SID_RE.match(str(sid))) and bool(self.cmd("HEXISTS", self._k("scenarios"), sid))

    def is_custom_ref(self, ref):
        return isinstance(ref, str) and bool(_SID_RE.match(ref))

    def save_custom(self, sid, json_text, ref=None):
        field = ref if self.is_custom_ref(ref) else str(sid)
        if not _SID_RE.match(field):
            raise ValueError("情境檔的位置不對")
        self.cmd("HSET", self._k("scenarios"), field, json_text)
        self._forget("scenarios")
        return field

    def trash_custom(self, ref):
        if not self.is_custom_ref(ref):
            raise ValueError("情境檔的位置不對")
        text = self.cmd("HGET", self._k("scenarios"), ref)
        if text is None:
            raise ValueError("找不到這個情境，可能已經被刪除")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name, n = f"{ref}-{stamp}", 1
        while self.cmd("HEXISTS", self._k("scenarios:trash"), name):
            n += 1
            name = f"{ref}-{stamp}-{n}"
        # 搬到垃圾桶和刪除要一起成功：用 transaction
        self.multi([["HSET", self._k("scenarios:trash"), name, text], ["HDEL", self._k("scenarios"), ref]])
        self._forget("scenarios")
        return name

    # ---- 開場白語音快取

    def tts_get(self, digest):
        if not _DIGEST_RE.match(str(digest)):
            return None
        try:
            raw = self.cmd("GET", self._k("tts:" + digest))
            if raw is None:
                return None
            item = json.loads(raw)
            return base64.b64decode(item["a"]), item["c"]
        except Exception:
            return None

    def tts_put(self, digest, audio, ctype):
        try:
            if not _DIGEST_RE.match(str(digest)) or len(audio) > TTS_MAX_BYTES:
                return
            raw = json.dumps({"c": ctype, "a": base64.b64encode(audio).decode("ascii")})
            self.cmd("SET", self._k("tts:" + digest), raw, "EX", self.tts_ttl)
        except Exception:
            pass

    # ---- 登入資料：用 APP_SECRET 加密（Fernet）後才放進 Redis，不快取

    def _cipher(self):
        if not self._app_secret:
            raise StoreError("還沒設定 APP_SECRET，不能存登入資料")
        if self._fernet is None:
            try:
                from cryptography.fernet import Fernet  # 只有雲端會用到，本機不需要裝
            except ImportError:
                raise StoreError("缺少 cryptography 套件（Vercel 會照 pyproject.toml 安裝）") from None
            key = hashlib.sha256(("aicoach-token|" + self._app_secret).encode("utf-8")).digest()
            self._fernet = Fernet(base64.urlsafe_b64encode(key))
        return self._fernet

    def get_secret(self, name):
        if not _NAME_RE.match(str(name)):
            raise ValueError("登入資料的名稱不對")
        raw = self.cmd("GET", self._k("secret:" + name))
        if raw is None or not self._app_secret:
            return None
        try:
            data = json.loads(self._cipher().decrypt(raw.encode("ascii")).decode("utf-8"))
        except StoreError:
            raise
        except Exception:  # APP_SECRET 換過了：舊資料解不開，當作沒有
            if name not in self._bad_secret_logged:
                self._bad_secret_logged.add(name)
                _log(f"{name} 解不開（APP_SECRET 可能換過），當作沒有")
            return None
        return data if isinstance(data, dict) else None

    def put_secret(self, name, value):
        if not _NAME_RE.match(str(name)):
            raise ValueError("登入資料的名稱不對")
        if not isinstance(value, dict):
            raise ValueError("登入資料格式不對")
        token = self._cipher().encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")
        self.cmd("SET", self._k("secret:" + name), token)

    def delete_secret(self, name):
        if not _NAME_RE.match(str(name)):
            raise ValueError("登入資料的名稱不對")
        self.cmd("DEL", self._k("secret:" + name))

    # ---- 短期資料

    def eph_get(self, key):
        raw = self.cmd("GET", self._k("eph:" + key))
        return None if raw is None else json.loads(raw)

    def eph_set(self, key, value, ttl):
        self.cmd("SET", self._k("eph:" + key), json.dumps(value, ensure_ascii=False), "EX", max(1, int(-(-float(ttl) // 1))))

    def eph_pop(self, key):
        raw = self.multi([["GET", self._k("eph:" + key)], ["DEL", self._k("eph:" + key)]])[0]
        return None if raw is None else json.loads(raw)

    def eph_incr(self, key, ttl):
        k = self._k("eph:" + key)
        n = int(self.cmd("INCR", k))
        if n == 1:  # 第一次才設期限：固定的時間窗，不會越試越久
            self.cmd("EXPIRE", k, max(1, int(-(-float(ttl) // 1))))
        return n

    # ---- 跨執行個體的鎖

    @contextlib.contextmanager
    def lock(self, name, ttl=30.0, wait=15.0):
        key = self._k("lock:" + name)
        token = secrets.token_hex(12)
        deadline = time.monotonic() + max(0.0, float(wait))
        while self.cmd("SET", key, token, "NX", "PX", max(1, int(float(ttl) * 1000))) != "OK":
            if time.monotonic() >= deadline:
                raise StoreError("系統忙碌中，請稍後再試")
            time.sleep(0.15)
        self._tl.fresh = getattr(self._tl, "fresh", 0) + 1
        try:
            yield
        finally:
            self._tl.fresh -= 1
            self._unlock(key, token)

    def _unlock(self, key, token):
        try:
            self.cmd("EVAL", _UNLOCK, 1, key, token)
            return
        except StoreError:
            pass
        try:  # 不支援 EVAL：確認還是自己的鎖再刪
            if self.cmd("GET", key) == token:
                self.cmd("DEL", key)
        except StoreError:
            pass  # 刪不掉也沒關係，時間到會自己解開


# ---------------------------------------------------------------- 選擇


def _num(value, default):
    try:
        return float(value) if str(value or "").strip() else default
    except ValueError:
        return default


def open_store(root, env, on_cloud):
    """有 Upstash 變數就用 Redis；本機用檔案；雲端沒接資料庫就先放記憶體（會提醒）。"""
    url = env.get("KV_REST_API_URL") or env.get("UPSTASH_REDIS_REST_URL") or ""
    token = env.get("KV_REST_API_TOKEN") or env.get("UPSTASH_REDIS_REST_TOKEN") or ""
    if url and token:
        if urlparse(url).scheme in ("https", "http") and urlparse(url).hostname:
            return RedisStore(
                url, token,
                prefix=env.get("STORE_PREFIX") or "aicoach:",
                cache_ttl=_num(env.get("STORE_CACHE_TTL"), 10.0),
                tts_ttl_days=_num(env.get("TTS_CACHE_TTL_DAYS"), 30.0),
                app_secret=env.get("APP_SECRET") or "",
            )
        _log("KV_REST_API_URL 格式不對，先不用資料庫")
    if not on_cloud:
        return FileStore(root)
    store = MemoryStore()
    if url and token:
        store.warning = "資料庫網址（KV_REST_API_URL）格式不對：" + MemoryStore.warning
    return store
