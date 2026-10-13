"""User-configured FPT.AI and ElevenLabs speech through official HTTP APIs.

No key discovery or network activity occurs on import. Keys are separately
protected with Windows DPAPI inside this application's ``tts`` data directory.
Generation posts are never automatically retried: a retry could consume credits
twice. FPT asynchronous audio is polled without forwarding provider credentials.

Official contracts checked 2026-10-12:
https://docs.fpt.ai/docs/en/speech/api/text-to-speech/
https://elevenlabs.io/docs/api-reference/voices/search
https://elevenlabs.io/docs/api-reference/text-to-speech/convert
https://elevenlabs.io/docs/overview/models
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse

import requests

import ai_tools
import engine
from providers import app_data_root, _dpapi


supported_ids = ("fpt", "elevenlabs", "gtts", "minimax", "siliconflow", "vbee", "zalo", "capcut")
SUPPORTED_IDS = supported_ids
PROVIDER_INFO = {
    "fpt": dict(label="FPT.AI", keys_url="https://console.fpt.ai", docs_url="https://docs.fpt.ai/docs/en/speech/api/text-to-speech/"),
    "elevenlabs": dict(label="ElevenLabs", keys_url="https://elevenlabs.io/app/settings/api-keys", docs_url="https://elevenlabs.io/docs/api-reference/text-to-speech/convert"),
}
PROVIDER_INFO.update({
    "gtts":dict(label="Google TTS",keys_url="https://translate.google.com",docs_url="https://gtts.readthedocs.io"),
    "minimax":dict(label="MiniMax",keys_url="https://platform.minimax.io",docs_url="https://platform.minimax.io/docs/api-reference/speech-t2a-http"),
    "siliconflow":dict(label="SiliconFlow",keys_url="https://cloud.siliconflow.cn",docs_url="https://docs.siliconflow.cn/docs/api/audio-speech-post"),
    "vbee":dict(label="Vbee",keys_url="https://api.vbee.vn",docs_url="https://api-docs.vbee.vn"),
    "zalo":dict(label="Zalo AI",keys_url="https://ai.zalo.solutions/account/manage-keys",docs_url="https://ai.zalo.solutions/docs/api/text-to-audio-converter"),
    "capcut":dict(label="CapCut (cầu nối của bạn)",keys_url="https://www.capcut.com",docs_url="https://www.capcut.com/help/text-to-speech-feature")})
FPT_VOICES = (
    ("banmai", "Ban Mai", "Female", "Miền Bắc"),
    ("lannhi", "Lan Nhi", "Female", "Miền Nam"),
    ("leminh", "Lê Minh", "Male", "Miền Bắc"),
    ("myan", "Mỹ An", "Female", "Miền Trung"),
    ("thuminh", "Thu Minh", "Female", "Miền Bắc"),
    ("giahuy", "Gia Huy", "Male", "Miền Trung"),
    ("linhsan", "Linh San", "Female", "Miền Nam"),
)
_VOICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_JSON = 8 * 1024 * 1024
_MAX_AUDIO = 64 * 1024 * 1024
_POLL_TIMEOUT = 120
_POLL_INTERVAL = 1
_HTTP_TIMEOUT = (5, 10)


class TTSError(ai_tools.AIError):
    """Provider failure with no submitted key, response body or signed URL."""


def provider_id(provider):
    value = str(provider).strip().lower()
    if value not in supported_ids:
        raise TTSError("Chọn nhà cung cấp FPT.AI hoặc ElevenLabs.")
    return value


def split_voice_id(voice):
    provider, separator, identity = str(voice).partition(":")
    provider = provider_id(provider)
    if not separator or not _VOICE_ID.fullmatch(identity):
        raise TTSError("ID giọng đọc của nhà cung cấp không hợp lệ.")
    if provider == "fpt" and identity not in {row[0] for row in FPT_VOICES}:
        raise TTSError("Giọng FPT.AI không có trong danh sách được hỗ trợ.")
    return provider, identity


class TTSKeyStore:
    """Own encrypted speech keys, compatible with the desktop key editor."""
    def __init__(self, root=None, *, allow_plaintext_test=False):
        if allow_plaintext_test and root is None:
            raise ValueError("Khóa kiểm tra cần thư mục riêng.")
        self.root = Path(root).expanduser().absolute() if root is not None else app_data_root() / "tts"
        self._plaintext_test = bool(allow_plaintext_test and os.name != "nt")
        self._lock = threading.RLock()

    def _path(self, provider):
        return self.root / (provider_id(provider) + (".fixture.json" if self._plaintext_test else ".keys.dpapi"))

    def get_options(self,provider):
        path=self.root/(provider_id(provider)+".connection.json")
        if not path.is_file(): return {}
        if path.stat().st_size>8192: raise TTSError("Cấu hình kết nối quá lớn.")
        value=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value,dict): raise TTSError("Cấu hình kết nối chưa hợp lệ.")
        return value

    def save_options(self,provider,values):
        provider=provider_id(provider)
        allowed={"app_id","model","endpoint"}
        data={key:str(value).strip() for key,value in values.items() if key in allowed}
        if any(len(value)>1000 or any(c in value for c in "\r\n") for value in data.values()):
            raise TTSError("Cấu hình kết nối chưa hợp lệ.")
        if data.get("endpoint"):
            from online_speech import bridge_url
            data["endpoint"]=bridge_url(data["endpoint"])
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/(provider+".connection.json")
        temporary=path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data),encoding="utf-8")
        os.replace(temporary,path)

    @staticmethod
    def _clean(keys):
        if not isinstance(keys, (list, tuple)):
            raise ValueError("Mỗi khóa API cần một dòng riêng.")
        values = []
        for key in keys:
            if not isinstance(key, str):
                raise ValueError("Khóa API không hợp lệ.")
            value = key.strip()
            if not value:
                continue
            if not 8 <= len(value) <= 1024 or any(character.isspace() for character in value):
                raise ValueError("Khóa cần ít nhất 8 ký tự và không chứa khoảng trắng.")
            if value not in values:
                values.append(value)
        if len(values) > 32:
            raise ValueError("Mỗi nhà cung cấp hỗ trợ tối đa 32 khóa.")
        return values

    def save_keys(self, provider, keys):
        provider = provider_id(provider)
        values = self._clean(keys)
        path = self._path(provider)
        with self._lock:
            if not values:
                path.unlink(missing_ok=True)
                return
            payload = json.dumps(dict(version=1, provider=provider, keys=values), ensure_ascii=False).encode("utf-8")
            try:
                protected = payload if self._plaintext_test else _dpapi(payload)
                self.root.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix=".tts-keys-", suffix=".tmp", dir=self.root)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "wb") as destination:
                        destination.write(protected)
                        destination.flush()
                        os.fsync(destination.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            except Exception:
                raise TTSError("Không bảo vệ được khóa lồng tiếng. Kiểm tra quyền ghi dữ liệu và Windows DPAPI.") from None

    def get_keys(self, provider):
        provider = provider_id(provider)
        path = self._path(provider)
        with self._lock:
            if not path.exists():
                return []
            try:
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError("oversized store")
                raw = path.read_bytes()
                payload = json.loads((raw if self._plaintext_test else _dpapi(raw, decrypt=True)).decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("provider") != provider:
                    raise ValueError("invalid store")
                return self._clean(payload.get("keys"))
            except Exception:
                raise TTSError("Kho khóa lồng tiếng không đọc được. Nhập lại khóa trong Thiết Lập.") from None

    def summary(self, provider):
        keys = self.get_keys(provider)
        return dict(count=len(keys), last4=keys[0][-4:] if keys else "", configured=bool(keys))


def fpt_catalog():
    """Documented voice choices; availability and quota are checked on use."""
    return [dict(ShortName="fpt:" + identity, Locale="vi-VN", Gender=gender, FriendlyName=name,
                 Provider="fpt", VoiceTag=dict(ContentCategories=[region], VoicePersonalities=[]))
            for identity, name, gender, region in FPT_VOICES]


def _key(provider, store):
    keys = (store or TTSKeyStore()).get_keys(provider)
    if not keys:
        raise TTSError("Chưa có khóa " + PROVIDER_INFO[provider]["label"] + ". Nhập khóa trong Thiết Lập → Lồng tiếng.")
    # The first explicitly configured key is used. No silent account rotation.
    return keys[0]


def _status_error(provider, status):
    label = PROVIDER_INFO[provider]["label"]
    if status in {401, 403}:
        detail = "Khóa hoặc quyền sử dụng chưa hợp lệ. Kiểm tra khóa và hạn mức tài khoản."
    elif status == 429:
        detail = "Đã đạt giới hạn dịch vụ hoặc hạn mức tài khoản. Thử lại sau."
    elif status == 404:
        detail = "Không tìm thấy giọng hoặc API được yêu cầu. Làm mới danh sách giọng."
    elif status in {400, 422}:
        detail = "Dịch vụ không chấp nhận nội dung hoặc cấu hình giọng này."
    else:
        detail = "Dịch vụ chưa hoàn tất yêu cầu. Thử lại sau."
    return TTSError(f"{label}: HTTP {int(status)}. {detail}")


def _request(method, url, provider, cancel, **kwargs):
    ai_tools._check_cancel(cancel)
    try:
        response = requests.request(method, url, timeout=_HTTP_TIMEOUT, allow_redirects=False, stream=True, **kwargs)
    except requests.RequestException:
        ai_tools._check_cancel(cancel)
        raise TTSError("Không kết nối được " + PROVIDER_INFO[provider]["label"] + ". Kiểm tra Internet rồi thử lại.") from None
    try:
        ai_tools._check_cancel(cancel)
    except BaseException:
        response.close()
        raise
    return response


def _read_json(response, provider, cancel):
    if not 200 <= response.status_code < 300:
        raise _status_error(provider, response.status_code)
    data = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=65536):
            ai_tools._check_cancel(cancel)
            if chunk:
                data.extend(chunk)
                if len(data) > _MAX_JSON:
                    raise TTSError("Danh sách hoặc phản hồi lồng tiếng quá lớn.")
        payload = json.loads(bytes(data).decode("utf-8"))
    except ai_tools.CancelledError:
        raise
    except TTSError:
        raise
    except requests.RequestException:
        raise TTSError("Kết nối lồng tiếng bị gián đoạn. Thử lại sau.") from None
    except (ValueError, UnicodeError, TypeError):
        raise TTSError("Dịch vụ lồng tiếng trả về JSON không hợp lệ.") from None
    if not isinstance(payload, dict):
        raise TTSError("Dịch vụ lồng tiếng trả về cấu trúc không hợp lệ.")
    return payload


def catalog(provider, cancel=None, *, store=None):
    provider = provider_id(provider)
    ai_tools._check_cancel(cancel)
    if provider not in ("fpt","elevenlabs"):
        from online_speech import catalog as fetch
        return fetch(provider,cancel,store or TTSKeyStore())
    if provider == "fpt":
        return fpt_catalog()
    key = _key(provider, store)
    rows, seen_ids, seen_tokens = [], set(), set()
    token = None
    for _page in range(20):
        params = dict(page_size=100)
        if token:
            params["next_page_token"] = token
        response = _request("GET", "https://api.elevenlabs.io/v2/voices", provider, cancel,
                            headers={"xi-api-key": key, "Accept": "application/json"}, params=params)
        try:
            payload = _read_json(response, provider, cancel)
        finally:
            response.close()
        voices = payload.get("voices")
        if not isinstance(voices, list):
            raise TTSError("ElevenLabs chưa trả danh sách giọng hợp lệ.")
        for voice in voices:
            if not isinstance(voice, dict):
                continue
            identity = str(voice.get("voice_id", ""))
            if not _VOICE_ID.fullmatch(identity) or identity in seen_ids:
                continue
            labels = voice.get("labels", {})
            labels = labels if isinstance(labels, dict) else {}
            gender = {"female": "Female", "male": "Male"}.get(str(labels.get("gender", "")).lower(), "Unknown")
            # A voice label describes its original accent, not all supported
            # languages. Flash v2.5 infers speech language from the input text.
            language = str(labels.get("language", "")).lower()
            language = {"english": "en", "vietnamese": "vi", "french": "fr", "german": "de", "spanish": "es"}.get(language, language)
            locale = {"en": "en-US", "vi": "vi-VN", "fr": "fr-FR", "de": "de-DE", "es": "es-ES",
                      "ja": "ja-JP", "ko": "ko-KR", "zh": "zh-CN"}.get(language, "mul")
            rows.append(dict(ShortName="elevenlabs:" + identity, FriendlyName=str(voice.get("name", identity))[:240],
                             Locale=locale, Gender=gender, Provider="elevenlabs",
                             VoiceTag=dict(ContentCategories=[str(labels.get("use_case", ""))[:80]] if labels.get("use_case") else [],
                                           VoicePersonalities=[str(labels.get("accent", ""))[:80]] if labels.get("accent") else [])))
            seen_ids.add(identity)
        if len(rows) > 2000:
            raise TTSError("ElevenLabs trả quá nhiều giọng. Giảm danh sách đã lưu trong tài khoản.")
        if not payload.get("has_more", False):
            break
        token = payload.get("next_page_token")
        if not isinstance(token, str) or not token or len(token) > 2048 or token in seen_tokens:
            raise TTSError("ElevenLabs trả thông tin phân trang không hợp lệ.")
        seen_tokens.add(token)
    else:
        raise TTSError("Danh sách ElevenLabs có quá nhiều trang.")
    if not rows:
        raise TTSError("Tài khoản ElevenLabs chưa có giọng đọc khả dụng.")
    return rows


def _validated_fpt_url(value):
    try:
        parsed = urlparse(str(value))
        host = (parsed.hostname or "").lower()
        documented_s3 = host == "s3-ap-southeast-1.amazonaws.com" and parsed.path.startswith(("/text2speech-v4/", "/text2speech-v5/"))
        official_host = host == "fpt.ai" or host.endswith(".fpt.ai")
        bucket_host = host in {"text2speech-v4.s3.ap-southeast-1.amazonaws.com", "text2speech-v5.s3.ap-southeast-1.amazonaws.com"}
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not (documented_s3 or official_host or bucket_host) or not parsed.path.lower().endswith(".mp3")
                or parsed.fragment or len(str(value)) > 4096 or any(character.isspace() for character in str(value))):
            raise ValueError("untrusted async URL")
        return str(value)
    except (ValueError, TypeError):
        raise TTSError("FPT.AI trả đường dẫn âm thanh không hợp lệ hoặc ngoài máy chủ được hỗ trợ.") from None


def _audio_response(response, provider, destination, cancel):
    if response.status_code != 200:
        raise _status_error(provider, response.status_code)
    content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
    if content_type not in {"audio/mpeg", "audio/mp3", "audio/x-mpeg", "application/octet-stream", "binary/octet-stream"}:
        raise TTSError(PROVIDER_INFO[provider]["label"] + " chưa trả về tệp MP3 hợp lệ.")
    total, prefix = 0, b""
    try:
        with Path(destination).open("wb") as output:
            for chunk in response.iter_content(chunk_size=65536):
                ai_tools._check_cancel(cancel)
                if not chunk:
                    continue
                total += len(chunk)
                if total > _MAX_AUDIO:
                    raise TTSError("Âm thanh lồng tiếng vượt quá 64 MB.")
                if len(prefix) < 16:
                    prefix = (prefix + chunk)[:16]
                output.write(chunk)
        ai_tools._check_cancel(cancel)
    except requests.RequestException:
        raise TTSError("Tải âm thanh lồng tiếng bị gián đoạn. Thử lại sau.") from None
    if total < 16 or not (prefix.startswith(b"ID3") or len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0):
        raise TTSError(PROVIDER_INFO[provider]["label"] + " trả về âm thanh rỗng hoặc không phải MP3.")


def _verify_mp3(path, cancel):
    process = subprocess.Popen([engine.tool_path("ffprobe"), "-v", "error", "-select_streams", "a:0",
                                "-show_entries", "stream=codec_name,codec_type:format=format_name,duration", "-of", "json", str(path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=engine._creation_flags())
    deadline = time.monotonic() + 15
    try:
        while process.poll() is None:
            ai_tools._check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise TTSError("Kiểm tra âm thanh lồng tiếng quá lâu.")
            time.sleep(.05)
        output, _error = process.communicate()
        if process.returncode or len(output) > 65536:
            raise TTSError("Tệp lồng tiếng tải về không đọc được.")
        try:
            payload = json.loads(output)
            streams = payload.get("streams", [])
            duration = float(payload.get("format", {}).get("duration", 0))
            if not streams or streams[0].get("codec_name") != "mp3" or not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid audio")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise TTSError("Tệp lồng tiếng tải về không phải âm thanh MP3 hợp lệ.") from None
    finally:
        if process.poll() is None:
            engine._stop(process)
        for pipe in (process.stdout, process.stderr):
            if pipe:
                pipe.close()


def synthesize(text: str, voice: str, output: str, cancel=None, *, store=None) -> str:
    """Generate a validated MP3, using only user-supplied provider credentials."""
    ai_tools._check_cancel(cancel)
    provider, identity = split_voice_id(voice)
    if provider not in ("fpt","elevenlabs"):
        from online_speech import synthesize as create
        return create(text,voice,output,cancel,store)
    text = str(text).strip()
    if not text or len(text) > 5000 or provider == "fpt" and len(text) < 3:
        raise TTSError("FPT.AI cần 3–5.000 ký tự; ElevenLabs cần 1–5.000 ký tự cho mỗi lần tạo giọng.")
    target = Path(output).expanduser().resolve()
    if target.suffix.lower() != ".mp3" or target.exists():
        raise TTSError("Chọn tệp MP3 mới để lưu âm thanh lồng tiếng.")
    key = _key(provider, store)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tts-provider-", dir=target.parent) as temporary:
        staged = Path(temporary) / "speech.mp3"
        if provider == "fpt":
            response = _request("POST", "https://api.fpt.ai/hmi/tts/v5", provider, cancel,
                                headers={"api_key": key, "voice": identity, "speed": "0", "format": "mp3",
                                         "Content-Type": "text/plain; charset=utf-8", "Accept": "application/json"}, data=text.encode("utf-8"))
            try:
                payload = _read_json(response, provider, cancel)
            finally:
                response.close()
            if type(payload.get("error")) is not int or payload["error"] != 0:
                raise TTSError("FPT.AI không chấp nhận yêu cầu tạo giọng. Kiểm tra khóa, hạn mức và nội dung.")
            url = _validated_fpt_url(payload.get("async"))
            deadline = time.monotonic() + _POLL_TIMEOUT
            while True:
                ai_tools._check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise TTSError("FPT.AI chưa tạo xong âm thanh trong 120 giây. Thử lại sau.")
                # The async URL is public or signed. Never forward the API key.
                audio = _request("GET", url, provider, cancel, headers={"Accept": "audio/mpeg"})
                try:
                    if audio.status_code == 200:
                        _audio_response(audio, provider, staged, cancel)
                        break
                    if audio.status_code not in {202, 204, 403, 404, 503}:
                        raise _status_error(provider, audio.status_code)
                finally:
                    audio.close()
                if cancel is not None:
                    if cancel.wait(_POLL_INTERVAL):
                        ai_tools._check_cancel(cancel)
                else:
                    time.sleep(_POLL_INTERVAL)
        else:
            response = _request("POST", "https://api.elevenlabs.io/v1/text-to-speech/" + identity, provider, cancel,
                                headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                                params={"output_format": "mp3_44100_128"}, json={"text": text, "model_id": "eleven_flash_v2_5"})
            try:
                _audio_response(response, provider, staged, cancel)
            finally:
                response.close()
        _verify_mp3(staged, cancel)
        ai_tools._check_cancel(cancel)
        engine._publish(staged, target)
    return str(target)
