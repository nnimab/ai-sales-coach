"""付費語音服務（ElevenLabs、Gemini、雅婷）：合成語音、列出與挑選聲音。只用 Python 標準函式庫。"""

import base64
import datetime
import json
import re
import struct
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

EL_MODELS = [
    ("eleven_v3_conversational", "v3 對話版（最自然，適合即時對話）"),
    ("eleven_multilingual_v2", "Multilingual v2（穩定、高品質）"),
    ("eleven_flash_v2_5", "Flash v2.5（最快、費用減半）"),
    ("eleven_v3", "v3（情緒最豐富，速度較慢）"),
]
EL_DEFAULT_MODEL = "eleven_v3_conversational"
EL_FALLBACK_MODEL = "eleven_multilingual_v2"

YATING_URL = "https://tts.api.yating.tw/v2/speeches/short"
YATING_VOICES = [
    {"voice_id": "zh_en_male_1", "name": "家豪", "gender": "male"},
    {"voice_id": "zh_en_male_2", "name": "志明", "gender": "male"},
    {"voice_id": "zh_en_female_1", "name": "雅婷", "gender": "female"},
    {"voice_id": "zh_en_female_2", "name": "意晴", "gender": "female"},
]
YATING_DEFAULTS = {"male": "zh_en_male_1", "female": "zh_en_female_1"}

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODELS = [
    ("gemini-3.8-flash-lite-tts", "3.8 Flash-Lite（最快，適合即時對話）"),
    ("gemini-3.8-flash-tts", "3.8 Flash（口音、情緒最好，稍慢）"),
]
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"
# 30 個內建聲音（名稱、性別、特色）。講中文不一定是台灣腔，台灣腔要用文字「設計」聲音
GEMINI_VOICES = [
    ("Zephyr", "female", "明亮"), ("Puck", "male", "輕快"), ("Charon", "male", "講解型"),
    ("Kore", "female", "堅定"), ("Fenrir", "male", "容易激動"), ("Leda", "female", "年輕"),
    ("Orus", "male", "堅定"), ("Aoede", "female", "輕鬆"), ("Callirrhoe", "female", "隨和"),
    ("Autonoe", "female", "明亮"), ("Enceladus", "male", "氣音"), ("Iapetus", "male", "清楚"),
    ("Umbriel", "male", "隨和"), ("Algieba", "male", "滑順"), ("Despina", "female", "滑順"),
    ("Erinome", "female", "清楚"), ("Algenib", "male", "沙啞"), ("Rasalgethi", "male", "講解型"),
    ("Laomedeia", "female", "輕快"), ("Achernar", "female", "輕柔"), ("Alnilam", "male", "堅定"),
    ("Schedar", "male", "平穩"), ("Gacrux", "female", "成熟"), ("Pulcherrima", "female", "直率"),
    ("Achird", "male", "親切"), ("Zubenelgenubi", "male", "隨性"), ("Vindemiatrix", "female", "溫柔"),
    ("Sadachbia", "male", "活潑"), ("Sadaltager", "male", "博學"), ("Sulafat", "female", "溫暖"),
]
GEMINI_DEFAULTS = {
    "male": {"young": "Puck", "middle_aged": "Charon", "old": "Algenib"},
    "female": {"young": "Leda", "middle_aged": "Kore", "old": "Gacrux"},
}
# Gemini 會把部分台語詞照國語字音念，換成諧音字才念得對
GEMINI_READINGS = {"歹勢": "拍謝"}


class TTSError(Exception):
    def __init__(self, message, code=0, detail=""):
        super().__init__(message)
        self.code = code
        self.detail = detail


def age_group(age):
    try:
        age = int(age)
    except (TypeError, ValueError):
        return ""
    if age < 35:
        return "young"
    return "middle_aged" if age < 55 else "old"


def pcm_to_wav(pcm, rate, channels=1, width=2):
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * width, channels * width, width * 8)
        + b"data" + struct.pack("<I", len(pcm))
    )
    return header + pcm


def _http(req, timeout, service):
    try:
        with urllib.request.build_opener().open(req, timeout=timeout) as r:
            return r.read(), r.headers.get_content_type()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise TTSError(f"{service} 回應錯誤 HTTP {e.code}：{detail}", e.code, detail) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TTSError(f"連不到 {service}：{getattr(e, 'reason', e)}") from None


def _row(v, source):
    labels = v.get("labels") or {}
    sharing = v.get("sharing") or {}
    return {
        "voice_id": v.get("voice_id") or "",
        "public_owner_id": v.get("public_owner_id") if source == "library" else None,
        "name": v.get("name") or "",
        "gender": str(v.get("gender") or labels.get("gender") or "").lower(),
        "age": str(v.get("age") or labels.get("age") or "").lower(),
        "accent": v.get("accent") or labels.get("accent") or "",
        "description": v.get("description") or v.get("descriptive") or labels.get("description") or "",
        "preview_url": v.get("preview_url") or "",
        "category": v.get("category") or "",
        "source": source,
        "added": bool(v.get("is_added_by_user")),
        "original_voice_id": sharing.get("original_voice_id"),
    }


def _taiwan_score(v):
    text = f"{v['accent']} {v['name']} {v['description']}".lower()
    return 0 if ("taiwan" in text or "台灣" in text or "臺灣" in text) else 1


class ElevenLabs:
    service = "ElevenLabs"

    def __init__(self, key, base="https://api.elevenlabs.io"):
        self.key = key
        self.base = base.rstrip("/")

    def _req(self, method, path, body=None, timeout=30):
        headers = {"xi-api-key": self.key}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        return _http(req, timeout, self.service)

    def _json(self, method, path, body=None):
        payload, _ = self._req(method, path, body)
        return json.loads(payload.decode("utf-8") or "{}")

    def my_voices(self):
        try:
            data = self._json("GET", "/v2/voices?page_size=100")
        except TTSError as e:
            if e.code in (401, 403):
                raise
            data = self._json("GET", "/v1/voices")
        return [_row(v, "mine") for v in data.get("voices", [])]

    def library(self, gender="", age=""):
        """從公開聲音庫找台灣口音的中文聲音；查詢條件猜不中時會換下一組。"""
        queries = [
            {"language": "zh", "locale": "zh-TW"},
            {"language": "zh", "accent": "taiwanese"},
            {"language": "zh", "search": "taiwan"},
        ]
        rows, seen, last_error = [], set(), None
        for q in queries:
            params = {"page_size": 30, "sort": "cloned_by_count", **q}
            if gender:
                params["gender"] = gender
            try:
                data = self._json("GET", "/v1/shared-voices?" + urlencode(params))
            except TTSError as e:
                if e.code in (401, 403):
                    raise
                last_error = e
                continue
            for v in data.get("voices", []):
                row = _row(v, "library")
                if row["voice_id"] and row["voice_id"] not in seen:
                    seen.add(row["voice_id"])
                    rows.append(row)
        if not rows and last_error:
            raise last_error
        rows.sort(key=lambda r: (_taiwan_score(r), 0 if age and r["age"] == age else 1))
        return rows[:24]

    def add_shared(self, public_owner_id, voice_id, name):
        """把公開聲音加進自己的帳號，回傳帳號內可用的 voice_id。"""
        try:
            data = self._json("POST", f"/v1/voices/add/{quote(public_owner_id)}/{quote(voice_id)}", {"new_name": name})
            return data.get("voice_id") or voice_id
        except TTSError:
            # 已經加過時會報錯：改到自己的聲音清單裡找那一份
            for v in self.my_voices():
                if voice_id in (v["voice_id"], v["original_voice_id"]):
                    return v["voice_id"]
            raise

    def synthesize(self, voice_id, text, model):
        """回傳 (音訊, content-type, 實際使用的模型)。模型不能用時自動退回穩定版。"""
        path = f"/v1/text-to-speech/{quote(voice_id)}?output_format=mp3_44100_128"
        try:
            audio, ctype = self._req("POST", path, {"text": text, "model_id": model}, timeout=60)
            return audio, ctype, model
        except TTSError as e:
            if e.code in (400, 404, 422) and "model" in e.detail.lower() and model != EL_FALLBACK_MODEL:
                audio, ctype = self._req("POST", path, {"text": text, "model_id": EL_FALLBACK_MODEL}, timeout=60)
                return audio, ctype, EL_FALLBACK_MODEL
            raise


class Yating:
    service = "雅婷語音"

    def __init__(self, key, url=YATING_URL):
        self.key = key
        self.url = url

    def voices(self):
        return [{**v, "age": "", "accent": "台灣", "description": "", "preview_url": "", "source": "mine",
                 "public_owner_id": None, "added": True, "original_voice_id": None, "category": "premade"}
                for v in YATING_VOICES]

    def synthesize(self, voice_id, text):
        body = {
            "input": {"text": text[:600], "type": "text"},
            "voice": {"model": voice_id, "speed": 1.0, "pitch": 1.0, "energy": 1.0},
            "audioConfig": {"encoding": "LINEAR16", "sampleRate": "22K"},
        }
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"),
                                     headers={"key": self.key, "Content-Type": "application/json"}, method="POST")
        payload, _ = _http(req, 120, self.service)
        try:
            data = json.loads(payload.decode("utf-8"))
            audio = base64.b64decode(data.get("audioContent") or "")
        except (ValueError, TypeError):
            raise TTSError("雅婷語音回傳的資料看不懂") from None
        if not audio:
            raise TTSError("雅婷語音沒有回傳聲音")
        if audio[:4] != b"RIFF":
            audio = pcm_to_wav(audio, 22050)
        return audio, "audio/wav"


def _gemini_row(voice_id, name, gender="", description="", source="library"):
    return {"voice_id": voice_id, "public_owner_id": None, "name": name, "gender": gender, "age": "",
            "accent": "", "description": description, "preview_url": "", "category": "premade",
            "source": source, "added": True, "original_voice_id": None}


def _last_audio(node):
    """回應裡最後一段音訊。REST 回應把 base64 音訊放在 steps[].content[]，type 是 audio。"""
    found = None
    if isinstance(node, dict):
        if node.get("type") == "audio" and node.get("data"):
            found = node
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        hit = _last_audio(child)
        if hit is not None:
            found = hit
    return found


def _gemini_quota_message(detail):
    """把 Google 的額度錯誤（例如 limit: 10 requests per day on Free Tier）翻成看得懂的話。"""
    m = re.search(r"limit:\s*(\d+)\s*requests?\s*per\s*(day|minute)", detail, re.I)
    if m and m.group(2).lower() == "day":
        return f"Gemini 免費版每天只能念 {m.group(1)} 次（每句話算一次），今天的用完了。到 AI Studio 開啟付費就能繼續用"
    if m:
        return f"Gemini 每分鐘只能念 {m.group(1)} 次，請等一分鐘再試，或到 AI Studio 開啟付費"
    return "Gemini 用量到上限了，請等一下再試，或到 AI Studio 開啟付費"


class Gemini:
    service = "Gemini"

    def __init__(self, key, base=GEMINI_BASE):
        self.key = key
        self.base = base.rstrip("/")

    def _json(self, method, path, body=None, timeout=60):
        headers = {"x-goog-api-key": self.key}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            payload, _ = _http(req, timeout, self.service)
        except TTSError as e:
            detail = e.detail.lower()
            if "api key" in detail or "api_key" in detail:
                raise TTSError("Gemini 金鑰不對，請到「聲音設定」重新貼一次", 401, e.detail) from None
            if e.code == 429:
                raise TTSError(_gemini_quota_message(e.detail), 429, e.detail) from None
            m = re.search(r'"message":\s*"((?:[^"\\]|\\.)*)"', e.detail)
            if m:
                raise TTSError(f"Gemini 回應錯誤 HTTP {e.code}：{m.group(1)}", e.code, e.detail) from None
            raise
        try:
            return json.loads(payload.decode("utf-8") or "{}")
        except ValueError:
            raise TTSError("Gemini 回傳的資料看不懂") from None

    def prebuilt(self, gender=""):
        return [_gemini_row(n, n, g, d) for n, g, d in GEMINI_VOICES if not gender or g == gender]

    def designed(self):
        """帳號裡用文字設計的聲音，最新的在前面。"""
        data = self._json("GET", "/voices?" + urlencode({"type": "prompted", "page_size": 100}))
        rows = []
        for v in data.get("voices") or []:
            vid = v.get("id") or (v.get("name") or "").rsplit("/", 1)[-1]
            if vid and v.get("type", "prompted") == "prompted":
                brief = (v.get("prompted") or {}).get("input") or v.get("description") or ""
                rows.append(_gemini_row(vid, v.get("display_name") or vid, str(v.get("gender") or "").lower(), brief, "mine"))
        return rows

    def design(self, brief, gender, name):
        """依文字描述設計新聲音，回傳 voice_id（存在帳號裡一年）。
        不指定模型：Flash-Lite 不支援設計，用預設的設計模型做出來的聲音，兩個 3.8 模型都能用。"""
        voice = {"type": "prompted", "display_name": name, "language_code": "zh-TW", "prompted": {"input": brief}}
        if gender in ("male", "female"):
            voice["gender"] = gender
        try:
            data = self._json("POST", "/voices", {"store": True, "voice": voice}, timeout=120)
        except TTSError as e:
            if e.code != 400 or "language" not in e.detail.lower():
                raise
            voice.pop("language_code")  # 不接受 zh-TW 時，改由描述裡的「台灣腔」決定口音
            data = self._json("POST", "/voices", {"store": True, "voice": voice}, timeout=120)
        vid = data.get("id") or (data.get("name") or "").rsplit("/", 1)[-1]
        if not vid:
            raise TTSError("Gemini 沒有回傳新聲音的代號")
        return vid

    def _speak(self, voice_id, text, model):
        body = {
            "model": model,
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text}]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice_id}]},
            "store": False,  # 不讓 Google 保存請求內容（預設會存 1～55 天）
        }
        part = _last_audio(self._json("POST", "/interactions", body))
        try:
            audio = base64.b64decode((part or {}).get("data") or "")
        except (ValueError, TypeError):
            audio = b""
        if not audio:
            raise TTSError("Gemini 沒有回傳聲音")
        if audio[:4] != b"RIFF":  # 預設是 WAV；萬一回的是沒有檔頭的 PCM 就補上
            audio = pcm_to_wav(audio, 24000)
        return audio

    def synthesize(self, voice_id, text, model):
        for word, reading in GEMINI_READINGS.items():
            text = text.replace(word, reading)
        return self._speak(voice_id, text, model), "audio/wav"

    def live_token(self, setup, minutes=30):
        """Gemini Live 的短效通行證：瀏覽器拿它直接連線。設定整份鎖在通行證裡（瀏覽器送的設定會被忽略），
        真正的金鑰也不會外流。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        stamp = lambda m: (now + datetime.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"uses": 1, "expireTime": stamp(minutes), "newSessionExpireTime": stamp(2), "bidiGenerateContentSetup": setup}
        token = self._json("POST", "/auth_tokens", body).get("name")
        if not token:
            raise TTSError("Gemini 沒有回傳通行證")
        return token
