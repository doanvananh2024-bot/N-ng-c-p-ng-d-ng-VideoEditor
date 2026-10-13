"""Edge TTS voice metadata and short previews in this application's own cache.

No network activity occurs at import or while reading cached metadata. The three
seed entries are an independently authored convenience list, not a claim that a
live service is available. Only an explicit catalog refresh or preview calls
the installed edge_tts package.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading

import ai_tools
import engine
from providers import app_data_root


SEED_VOICES = (
    dict(ShortName="vi-VN-HoaiMyNeural", FriendlyName="Hoài My", Gender="Female",
         Locale="vi-VN", VoiceTag=dict(ContentCategories=[], VoicePersonalities=[])),
    dict(ShortName="vi-VN-NamMinhNeural", FriendlyName="Nam Minh", Gender="Male",
         Locale="vi-VN", VoiceTag=dict(ContentCategories=[], VoicePersonalities=[])),
    dict(ShortName="en-US-JennyNeural", FriendlyName="Jenny", Gender="Female",
         Locale="en-US", VoiceTag=dict(ContentCategories=[], VoicePersonalities=[])),
)
LANGUAGE_NAMES = {
    "mul": "Đa ngôn ngữ",
    "vi": "Tiếng Việt", "en": "Tiếng Anh", "zh": "Tiếng Trung", "ja": "Tiếng Nhật",
    "ko": "Tiếng Hàn", "fr": "Tiếng Pháp", "de": "Tiếng Đức", "es": "Tiếng Tây Ban Nha",
    "pt": "Tiếng Bồ Đào Nha", "ru": "Tiếng Nga", "th": "Tiếng Thái", "id": "Tiếng Indonesia",
    "ms": "Tiếng Malaysia", "ar": "Tiếng Ả Rập", "hi": "Tiếng Hindi", "it": "Tiếng Ý",
    "nl": "Tiếng Hà Lan", "tr": "Tiếng Thổ Nhĩ Kỳ", "uk": "Tiếng Ukraina",
}
_SHORT_NAME = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){1,3}-[A-Za-z0-9]{2,100}$")
_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){1,3}$")
_MAX_CACHE = 8 * 1024 * 1024
_MAX_VOICES = 2000


def catalog_root() -> Path:
    return app_data_root() / "voices"


def language_name(locale: str) -> str:
    language = str(locale).split("-")[0].lower()
    return LANGUAGE_NAMES.get(language, language.upper())


def display_name(voice: dict) -> str:
    friendly = str(voice.get("FriendlyName", ""))
    if friendly and len(friendly) <= 80 and "Microsoft" not in friendly:
        return friendly
    name = str(voice.get("ShortName", ""))
    locale = str(voice.get("Locale", ""))
    if name.startswith(locale + "-"):
        name = name[len(locale) + 1:]
    return name.removesuffix("Neural") or str(voice.get("FriendlyName", ""))


def normalize_catalog(values) -> list[dict]:
    """Keep validated fields only, limiting untrusted network/cache payloads."""
    if not isinstance(values, (list, tuple)) or len(values) > _MAX_VOICES:
        raise ValueError("Danh sách giọng đọc không đúng định dạng hoặc quá lớn.")
    voices, seen = [], set()
    for row in values:
        if not isinstance(row, dict):
            continue
        name, locale = str(row.get("ShortName", "")), str(row.get("Locale", ""))
        provider = "edge"
        if ":" in name:
            try:
                import local_tts
                if local_tts.is_local_voice(name):
                    provider, _identity = local_tts.split_voice_id(name)
                else:
                    from tts_providers import split_voice_id
                    provider, _identity = split_voice_id(name)
            except (ValueError, ai_tools.AIError):
                continue
            valid = locale == "mul" or bool(_LOCALE.fullmatch(locale))
        else:
            valid = bool(_SHORT_NAME.fullmatch(name) and _LOCALE.fullmatch(locale) and name.startswith(locale + "-"))
        if not valid or name in seen:
            continue
        gender = str(row.get("Gender", ""))
        if gender not in {"Female", "Male"}:
            gender = "Unknown"
        tags = row.get("VoiceTag", {})
        tags = tags if isinstance(tags, dict) else {}
        def tag_values(key):
            values = tags.get(key, [])
            if not isinstance(values, list):
                return []
            return [str(value)[:80] for value in values[:12] if isinstance(value, str)]
        voices.append(dict(ShortName=name, Locale=locale, Gender=gender,
                           FriendlyName=str(row.get("FriendlyName", ""))[:240],
                           Provider=provider,
                           VoiceTag=dict(ContentCategories=tag_values("ContentCategories"),
                                         VoicePersonalities=tag_values("VoicePersonalities"))))
        seen.add(name)
    if not voices:
        raise ValueError("Dịch vụ chưa trả về giọng đọc hợp lệ.")
    return sorted(voices, key=lambda voice: (voice["Locale"] != "vi-VN", voice["Locale"], voice["Gender"], voice["ShortName"]))


def seed_catalog() -> list[dict]:
    return normalize_catalog(copy.deepcopy(SEED_VOICES))


def load_catalog(root: Path | None = None, *, provider="edge") -> tuple[list[dict], dict]:
    import local_tts
    if provider in local_tts.SUPPORTED_IDS:
        readiness = local_tts.availability(provider)
        return normalize_catalog(local_tts.catalog(provider)), dict(source="local", updated_at="",
                                                                    error="" if readiness["ready"] else readiness["hint"])
    from tts_providers import supported_ids
    if provider not in {"edge", *supported_ids}:
        raise ValueError("Nhà cung cấp giọng đọc không hợp lệ.")
    root = Path(root) if root is not None else catalog_root()
    path = root / (provider + "_catalog.json")
    metadata = dict(source="seed", updated_at="", error="")
    def fallback():
        if provider == "edge":
            return seed_catalog(), metadata
        if provider == "fpt":
            from tts_providers import fpt_catalog
            metadata["source"] = "documented"
            return normalize_catalog(fpt_catalog()), metadata
        if provider not in ("edge","elevenlabs"):
            from online_speech import seed_catalog as online_seed_catalog
            seed=online_seed_catalog(provider)
            if seed: return normalize_catalog(seed),metadata
        metadata["source"] = "missing"
        return [], metadata
    if not path.is_file():
        return fallback()
    try:
        if path.stat().st_size > _MAX_CACHE:
            raise ValueError("oversized cache")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("invalid cache")
        voices = normalize_catalog(payload.get("voices"))
        if any(voice.get("Provider") != provider for voice in voices):
            raise ValueError("wrong provider cache")
        updated_at = str(payload.get("updated_at", ""))[:64]
        if updated_at:
            datetime.fromisoformat(updated_at)
        return voices, dict(source="cache", updated_at=updated_at, error="")
    except (OSError, ValueError, UnicodeError, TypeError):
        metadata["error"] = "Không đọc được danh sách giọng đã lưu. Làm mới để tải lại."
        return fallback()


def save_catalog(voices, root: Path | None = None, *, cancel=None, provider="edge") -> dict:
    from tts_providers import supported_ids
    if provider not in {"edge", *supported_ids}:
        raise ValueError("Nhà cung cấp giọng đọc không hợp lệ.")
    rows = normalize_catalog(voices)
    if any(row.get("Provider") != provider for row in rows):
        raise ValueError("Danh sách giọng không thuộc nhà cung cấp được chọn.")
    ai_tools._check_cancel(cancel)
    destination = Path(root) if root is not None else catalog_root()
    destination.mkdir(parents=True, exist_ok=True)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = dict(version=1, updated_at=updated_at, voices=rows)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text.encode("utf-8")) > _MAX_CACHE:
        raise ValueError("Danh sách giọng đọc vượt quá 8 MB.")
    descriptor, name = tempfile.mkstemp(prefix=".edge-voices-", suffix=".tmp", dir=destination)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        ai_tools._check_cancel(cancel)
        os.replace(temporary, destination / (provider + "_catalog.json"))
    finally:
        temporary.unlink(missing_ok=True)
    return dict(source="live", updated_at=updated_at, error="")


async def _await_request(awaitable, cancel, *, timeout=35):
    """Bound online work and cancel it while waiting on sockets or response I/O."""
    task = asyncio.create_task(awaitable)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while not task.done():
            ai_tools._check_cancel(cancel)
            if loop.time() >= deadline:
                raise ai_tools.AIError(f"Dịch vụ giọng đọc chưa phản hồi trong {timeout} giây. Thử lại sau.")
            await asyncio.wait({task}, timeout=.1)
        ai_tools._check_cancel(cancel)
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _service_error(exc, action):
    status = getattr(exc, "status", None)
    suffix = f" (HTTP {status})" if isinstance(status, int) else ""
    return ai_tools.AIError(f"Không {action} được từ Edge TTS{suffix}. Kiểm tra Internet hoặc thử lại sau.")


def fetch_catalog(cancel: threading.Event | None = None, root: Path | None = None, progress=None):
    ai_tools._check_cancel(cancel)
    edge = ai_tools._optional("edge_tts", "edge-tts")
    if progress:
        progress(0, "Đang tải danh sách giọng Edge TTS…")
    try:
        rows = asyncio.run(_await_request(edge.list_voices(), cancel))
        voices = normalize_catalog(rows)
        metadata = save_catalog(voices, root, cancel=cancel)
    except ai_tools.CancelledError:
        raise
    except ai_tools.AIError:
        raise
    except Exception as exc:
        raise _service_error(exc, "tải danh sách giọng") from None
    if progress:
        progress(100, f"Đã tải {len(voices)} giọng đọc.")
    return voices, metadata


def sample_text(locale: str) -> str:
    language = str(locale).split("-")[0]
    samples = {
        "vi": "Xin chào, đây là giọng đọc thử cho video của bạn.",
        "en": "Hello, this is a voice preview for your video.",
        "zh": "你好，这是你的视频配音试听。",
        "ja": "こんにちは。動画の音声プレビューです。",
        "ko": "안녕하세요. 동영상 음성 미리 듣기입니다.",
        "fr": "Bonjour, voici un aperçu de la voix pour votre vidéo.",
        "de": "Hallo, dies ist eine Stimmvorschau für dein Video.",
        "es": "Hola, esta es una muestra de voz para tu vídeo.",
    }
    return samples.get(language, samples["en"])


def synthesize_preview(text, voice, output, cancel=None, progress=None, *, store=None):
    """Create a short MP3 with Edge or the explicitly selected speech provider."""
    ai_tools._check_cancel(cancel)
    voice = str(voice)
    if voice.startswith(("ngochuyen:", "ngochuyen_v2:", "kokoro:")):
        if not 1 <= len(str(text).strip()) <= 500:
            raise ValueError("Nội dung nghe thử cần từ 1 đến 500 ký tự.")
        import local_tts
        return local_tts.synthesize(text, voice, output, cancel, progress,
                                    device=local_tts.current_device())
    if voice.partition(":")[0] in ("fpt","elevenlabs","gtts","minimax","siliconflow","vbee","zalo","capcut") and ":" in voice:
        if not 1 <= len(str(text).strip()) <= 500:
            raise ValueError("Nội dung nghe thử cần từ 1 đến 500 ký tự.")
        from tts_providers import synthesize
        if progress:
            progress(0, "Đang tạo âm thanh nghe thử qua nhà cung cấp…")
        result = synthesize(text, voice, output, cancel, store=store)
        if progress:
            progress(100, "Âm thanh nghe thử đã sẵn sàng.")
        return result
    if not _SHORT_NAME.fullmatch(voice):
        raise ValueError("ID giọng đọc không hợp lệ.")
    text = str(text).strip()
    if not 1 <= len(text) <= 500:
        raise ValueError("Nội dung nghe thử cần từ 1 đến 500 ký tự.")
    target = Path(output).expanduser().resolve()
    if target.exists():
        raise FileExistsError("Tệp nghe thử đã tồn tại.")
    target.parent.mkdir(parents=True, exist_ok=True)
    edge = ai_tools._optional("edge_tts", "edge-tts")
    if progress:
        progress(0, "Đang tạo âm thanh nghe thử…")
    try:
        with tempfile.TemporaryDirectory(prefix=".edge-preview-", dir=target.parent) as temporary:
            staged = Path(temporary) / "speech.mp3"
            asyncio.run(_await_request(edge.Communicate(text, voice, connect_timeout=10, receive_timeout=20).save(str(staged)), cancel))
            ai_tools._check_cancel(cancel)
            if not staged.is_file() or staged.stat().st_size <= 0:
                raise ai_tools.AIError("Edge TTS chưa trả về âm thanh nghe thử.")
            engine._publish(staged, target)
    except BaseException as exc:
        if isinstance(exc, (ai_tools.AIError, ai_tools.CancelledError, FileExistsError, KeyboardInterrupt, SystemExit)):
            raise
        raise _service_error(exc, "tạo âm thanh nghe thử") from None
    if progress:
        progress(100, "Âm thanh nghe thử đã sẵn sàng.")
    return str(target)
