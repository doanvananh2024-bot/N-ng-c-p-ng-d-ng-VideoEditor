"""Offline speech from the user's installed Ngọc Huyền model packs.

Imports and catalogs never load a model or contact a service. V1 uses Piper's
bundled Vietnamese eSpeak frontend. V2 uses the pack's syllable/tone vocabulary,
not its legacy English eSpeak config. Models remain in their original folder.

Piper tensor and padding contract:
https://github.com/OHF-Voice/piper1-gpl/blob/v1.4.2/src/piper/voice.py
https://github.com/OHF-Voice/piper1-gpl/blob/v1.4.2/src/piper/phoneme_ids.py
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import unicodedata
import wave

import ai_tools

SUPPORTED_IDS = ("ngochuyen", "ngochuyen_v2", "kokoro")
PROVIDER_INFO = {
    "ngochuyen": {"label": "Ngọc Huyền (offline)", "voice": "ngoc_huyen", "name": "Ngọc Huyền"},
    "ngochuyen_v2": {"label": "Ngọc Huyền V2 (offline)", "voice": "ngoc_huyen_v_2", "name": "Ngọc Huyền V2"},
    "kokoro": {"label":"Kokoro (offline)","voice":"af_heart","name":"Kokoro"},
}
MODEL_ROOT_ENV = "MEDIAFUSION_LOCAL_TTS_ROOT"
_DEVICE = ContextVar("mediafusion_local_tts_device", default="auto")
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_ESPEAK_LOCK = threading.RLock()
_GPU_LOCK = threading.Lock()
_PHONEMIZER = None
_LAST_RUNTIME = {}
_PUNCT = ".,!?;:"
_ONSETS = tuple(sorted(("b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "q",
                       "r", "s", "t", "v", "x", "ch", "gh", "gi", "kh", "nh", "ng", "ngh",
                       "ph", "qu", "th", "tr"), key=len, reverse=True))
_NUMBER_NAMES = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_COMMON_WORDS = {"video": "vi đê ô", "audio": "au đi ô", "gpu": "gi pi iu", "cpu": "xi pi iu",
                 "cuda": "cu đa", "online": "on lai", "offline": "óp lai"}


def is_local_voice(voice) -> bool:
    return str(voice).partition(":")[0] in SUPPORTED_IDS and ":" in str(voice)


def split_voice_id(voice) -> tuple[str, str]:
    provider, separator, identity = str(voice).partition(":")
    if provider=="kokoro" and separator and re.fullmatch(r"[abefhijpz][fm]_[a-z0-9_]{1,80}",identity):
        return provider,identity
    installed=provider in ("ngochuyen","ngochuyen_v2") and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}",identity)) and (model_root()/provider/(identity+'.onnx')).is_file()
    if provider not in SUPPORTED_IDS or not separator or (identity != PROVIDER_INFO[provider]["voice"] and not installed):
        raise ai_tools.AIError("Giọng offline cần chọn Ngọc Huyền hoặc Ngọc Huyền V2 đã cài.")
    return provider, identity


def model_root() -> Path:
    override = os.environ.get(MODEL_ROOT_ENV)
    if override:
        return Path(override).expanduser().absolute()
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "VideoEditor" / "tts"


def _files(provider: str, identity=None) -> dict[str, Path]:
    if provider not in SUPPORTED_IDS:
        raise ValueError("Nhà cung cấp offline không hợp lệ.")
    folder = model_root() / provider
    identity = identity or PROVIDER_INFO[provider]["voice"]
    if provider=='ngochuyen' and identity=='ngoc_huyen' and not (folder/(identity+'.onnx')).is_file():
        # Keep saved legacy voice IDs usable with the installed replacement.
        # The selected model/config/meta come from the same actual voice.
        if (folder/'ngoc_huyen_premium.onnx').is_file(): identity='ngoc_huyen_premium'
    files = dict(model=folder / (identity + ".onnx"), config=folder / (identity + ".onnx.json"),
                 metadata=folder / (identity + ".meta.json"))
    if provider == "ngochuyen_v2":
        files.update(phonemes=folder / "phoneme_map.json", tones=folder / "tone_map.json")
    return files


def _read_json(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Dữ liệu model offline bị thiếu hoặc quá lớn: " + path.name)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Dữ liệu model offline không hợp lệ: " + path.name)
    return value


def _validated(provider,identity=None):
    files = _files(provider,identity)
    model = files["model"]
    if not model.is_file() or model.stat().st_size < 1024:
        raise ValueError("Chưa tìm thấy model " + PROVIDER_INFO[provider]["name"] + " trong " + str(model.parent))
    config, metadata = _read_json(files["config"]), _read_json(files["metadata"])
    expected = "syllable" if provider == "ngochuyen_v2" else "espeak"
    if metadata.get("phoneme") != expected:
        raise ValueError("Frontend của model không khớp giọng đã chọn; cần bản " + expected + ".")
    mapping = _read_json(files["phonemes"]) if provider == "ngochuyen_v2" else config.get("phoneme_id_map")
    symbols = int(config.get("num_symbols", 0))
    if not isinstance(mapping, dict) or symbols < 1:
        raise ValueError("Model chưa có bảng âm vị hợp lệ.")
    for token, values in mapping.items():
        if not isinstance(token, str) or not isinstance(values, list) or not values or any(
                not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < symbols for value in values):
            raise ValueError("Bảng âm vị có ID nằm ngoài model.")
    if not all(token in mapping for token in ("^", "_", "$", " ")):
        raise ValueError("Bảng âm vị thiếu ký hiệu đầu/cuối câu.")
    rate = int((config.get("audio") or {}).get("sample_rate", 0))
    if not 8000 <= rate <= 96000 or int(config.get("num_speakers", 1)) != 1:
        raise ValueError("Chưa hỗ trợ định dạng âm thanh hoặc model nhiều người đọc này.")
    tones = _read_json(files["tones"]) if provider == "ngochuyen_v2" else {}
    if tones and (not all(name in mapping for name in ("ngang", "sac", "huyen", "hoi", "nga", "nang"))
                  or any(not isinstance(value, list) or len(value) != 2 or value[1] not in mapping
                         for value in tones.values())):
        raise ValueError("Bảng thanh điệu của giọng V2 không hợp lệ.")
    return files, config, mapping, tones


def availability(provider) -> dict:
    """Model/dependency readiness only; does not claim the GPU is available."""
    identity=None
    if is_local_voice(provider):
        provider, identity = split_voice_id(provider)
    if provider=="kokoro":
        from foreign_tts import availability as state
        return state()
    try:
        files, _config, _map, _tones = _validated(provider,identity)
        for dependency in ("numpy", "onnxruntime") + (("piper",) if provider == "ngochuyen" else ()):
            if importlib.util.find_spec(dependency) is None:
                raise ValueError("Thiếu thư viện " + dependency + " cho giọng offline.")
        name=_read_json(files['metadata']).get('name',PROVIDER_INFO[provider]['name'])
        return dict(ready=True, hint="Giọng offline đã sẵn sàng: "+str(name)+"; không cần Internet.", model=str(files["model"]))
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        return dict(ready=False, hint=str(exc), model="")


readiness = availability


def catalog(provider) -> list[dict]:
    if provider=="kokoro":
        from foreign_tts import catalog as voices
        return voices()
    if provider not in SUPPORTED_IDS:
        raise ValueError("Nhà cung cấp giọng offline không hợp lệ.")
    info, ready = PROVIDER_INFO[provider], availability(provider)
    default_name=info['name']
    if ready['ready']:
        default_name=str(_read_json(_files(provider)['metadata']).get('name',default_name))[:240]
    result=[dict(ShortName=provider + ":" + info["voice"], FriendlyName=default_name,
                 Gender="Female", Locale="vi-VN", Provider=provider, Installed=ready["ready"],
                 VoiceTag=dict(ContentCategories=["Offline"], VoicePersonalities=[]))]
    for model in sorted((model_root()/provider).glob('*.onnx')):
        identity=model.stem
        if identity==info['voice'] or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identity): continue
        try:
            files,_config,_map,_tones=_validated(provider,identity)
            metadata=_read_json(files['metadata'])
        except (OSError,ValueError,TypeError,KeyError): continue
        result.append(dict(ShortName=provider+':'+identity,FriendlyName=str(metadata.get('name',identity))[:240],
            Gender={'female':'Female','male':'Male'}.get(str(metadata.get('gender','')).lower(),'Unknown'),
            Locale='vi-VN',Provider=provider,Installed=True,
            VoiceTag=dict(ContentCategories=[str(metadata.get('category','Offline'))[:80]],VoicePersonalities=[])))
    return result


@contextmanager
def device_scope(device="auto"):
    device = str(device).lower()
    if device not in {"auto", "cpu", "cuda"}:
        raise ValueError("Thiết bị lồng tiếng cần chọn auto, cpu hoặc cuda.")
    token = _DEVICE.set(device)
    try:
        yield
    finally:
        _DEVICE.reset(token)


def current_device() -> str:
    return _DEVICE.get()


def _integer_words(value: str) -> str:
    # Vietnamese cardinal numbers; long identifiers are read digit by digit.
    if len(value) > 15 or len(value) > 1 and value.startswith("0"):
        return " ".join(_NUMBER_NAMES[int(char)] for char in value)
    number = int(value)
    if number == 0:
        return _NUMBER_NAMES[0]
    def triple(number, full=False):
        hundreds, rest = divmod(number, 100)
        tens, units = divmod(rest, 10)
        words = []
        if hundreds or full:
            words += [_NUMBER_NAMES[hundreds], "trăm"]
        if tens > 1:
            words += [_NUMBER_NAMES[tens], "mươi"]
        elif tens == 1:
            words += ["mười"]
        elif units and (hundreds or full):
            words += ["lẻ"]
        if units:
            words += ["mốt" if units == 1 and tens > 1 else "lăm" if units == 5 and tens else _NUMBER_NAMES[units]]
        return words
    groups, words = [], []
    while number:
        number, part = divmod(number, 1000)
        groups.append(part)
    names = ("", "nghìn", "triệu", "tỷ", "nghìn tỷ")
    for index in range(len(groups) - 1, -1, -1):
        part = groups[index]
        if part:
            words += triple(part, full=bool(words) and part < 100)
            if index:
                words += names[index].split()
    return " ".join(words)


def normalize_vietnamese(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text)).lower()
    text = text.replace("…", "...")
    text = text.replace("%", " phần trăm ").replace("&", " và ")
    for source, replacement in _COMMON_WORDS.items():
        text = re.sub(r"(?<!\w)" + source + r"(?!\w)", replacement, text)
    text = re.sub(r"\d+(?:[.,]\d+)?", lambda match: " phẩy ".join(
        _integer_words(part) for part in re.split(r"[.,]", match.group())), text)
    text = re.sub(r"[\"“”‘’()\[\]{}—–/\\]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class SyllableFrontend:
    """Orthographic onset/rime/tone tokens defined by the installed V2 pack."""
    def __init__(self, mapping, tones):
        self.mapping, self.tones = mapping, tones

    def syllable(self, value: str) -> list[str]:
        tone, letters = "ngang", []
        for char in value:
            if char in self.tones:
                base, tone = self.tones[char]
                letters.append(base)
            else:
                letters.append(char)
        word = "".join(letters)
        for onset in _ONSETS:
            if not word.startswith(onset):
                continue
            rime = word[len(onset):]
            # In gì/gìn/giếng, the i belongs to both the gi onset and the rime.
            if onset == "gi" and (not rime or rime[0] not in "aăâeêioôơuưy"):
                rime = "i" + rime
            if rime in self.mapping:
                return [onset, rime, tone]
        if word in self.mapping:
            return [word, tone]
        raise ai_tools.AIError("Giọng V2 chưa đọc được từ “" + value[:60]
                               + "”. Hãy sửa từ này trong bảng cách đọc hoặc dùng Ngọc Huyền bản thường.")

    def phonemes(self, text: str) -> list[list[str]]:
        normalized = normalize_vietnamese(text)
        tokens = re.findall(r"[^\W\d_]+|[.,!?;:]|\S", normalized, flags=re.UNICODE)
        sentences, sentence = [], []
        for token in tokens:
            if token in _PUNCT:
                # A boundary already ended the previous spoken sentence.
                # Repeated dots in an ellipsis must not become a new empty
                # inference run: that discarded the entire valid cue.
                if not sentence:
                    continue
                sentence.append(token)
                if token in ".!?" and sentence:
                    sentences.append(sentence)
                    sentence = []
                continue
            if not token.isalpha():
                raise ai_tools.AIError("Giọng V2 chưa hỗ trợ ký hiệu “" + token[:20] + "”.")
            if sentence and sentence[-1] != " ":
                sentence.append(" ")
            sentence.extend(self.syllable(token))
        if sentence:
            sentences.append(sentence)
        if not sentences:
            raise ValueError("Nội dung cần ít nhất một từ để tạo giọng.")
        return sentences


def _phonemes(text, provider, mapping, tones):
    global _PHONEMIZER
    if provider == "ngochuyen_v2":
        return SyllableFrontend(mapping, tones).phonemes(text)
    with _ESPEAK_LOCK:
        if _PHONEMIZER is None:
            from piper.phonemize_espeak import EspeakPhonemizer
            _PHONEMIZER = EspeakPhonemizer()
        return _PHONEMIZER.phonemize("vi", unicodedata.normalize("NFC", text))


def _ids(phonemes, mapping):
    result = list(mapping["^"]) + list(mapping["_"])
    spoken = 0
    for phoneme in phonemes:
        values = mapping.get(phoneme)
        if values is None:
            raise ai_tools.AIError("Model thiếu âm vị “" + phoneme + "”; không thể tạo giọng đúng nội dung.")
        result.extend(values)
        result.extend(mapping["_"])
        if phoneme not in _PUNCT + " ":
            spoken += 1
    if not spoken:
        raise ValueError("Nội dung chưa có âm để đọc.")
    result.extend(mapping["$"])
    return result


def _fingerprint(files):
    return tuple((name, str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
                 for name, path in sorted(files.items()))


def _session(files, device, progress):
    from gpu_runtime import configure_cuda_runtime
    configure_cuda_runtime()
    import onnxruntime as ort
    available = ort.get_available_providers()
    requested = "cuda" if device != "cpu" and "CUDAExecutionProvider" in available else "cpu"
    if device == "cuda" and requested != "cuda":
        raise ai_tools.AIError("Chưa có ONNX Runtime GPU hoạt động. Chọn Tự động/CPU hoặc cài bộ GPU của ứng dụng.")
    key = (_fingerprint(files), requested)
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            session = _CACHE[key]
        else:
            options = ort.SessionOptions()
            options.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 2) // 2))
            options.inter_op_num_threads = 1
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if requested == "cuda" else ["CPUExecutionProvider"]
            try:
                session = ort.InferenceSession(str(files["model"]), sess_options=options, providers=providers)
            except Exception as exc:
                if device == "cuda" or requested == "cpu":
                    raise ai_tools.AIError("Không nạp được model giọng " + ("trên GPU." if device == "cuda" else "offline.")) from exc
                requested = "cpu"
                session = ort.InferenceSession(str(files["model"]), sess_options=options, providers=["CPUExecutionProvider"])
                key = (_fingerprint(files), requested)
            if requested == "cuda" and "CUDAExecutionProvider" not in session.get_providers():
                if device == "cuda":
                    raise ai_tools.AIError("CUDA không khởi tạo được; chưa tạo giọng bằng CPU khi bạn chọn GPU.")
                requested = "cpu"
                key = (_fingerprint(files), requested)
            _CACHE[key] = session
            while len(_CACHE) > 2:
                _CACHE.popitem(last=False)
    if progress:
        progress(0, "Tạo giọng offline trên GPU NVIDIA…" if requested == "cuda" else
                 "GPU giọng đọc chưa sẵn sàng; đang dùng CPU." if device == "auto" else "Tạo giọng offline trên CPU…")
    return session, requested


def cache_info() -> list[dict]:
    with _CACHE_LOCK:
        return [dict(model=next(item[1] for item in key[0] if item[0] == "model"), device=key[1],
                     fingerprint=list(key[0]), providers=session.get_providers())
                for key, session in _CACHE.items()]


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def last_runtime(voice) -> dict:
    return dict(_LAST_RUNTIME.get(str(voice), {}))


def _run(session, ids, config, cancel, device):
    import numpy as np
    import onnxruntime as ort
    ai_tools._check_cancel(cancel)
    inference = config.get("inference") or {}
    scales = np.array([float(inference.get("noise_scale", .667)),
                       float(inference.get("length_scale", 1.0)),
                       float(inference.get("noise_w", .8))], dtype=np.float32)
    if not np.isfinite(scales).all() or (scales < 0).any() or scales[1] <= 0:
        raise ValueError("Tham số suy luận của model không hợp lệ.")
    names = {value.name for value in session.get_inputs()}
    if not {"input", "input_lengths", "scales"}.issubset(names) or names - {"input", "input_lengths", "scales", "sid"}:
        raise ValueError("Model chưa dùng định dạng tensor Piper được hỗ trợ.")
    feeds = dict(input=np.array([ids], dtype=np.int64), input_lengths=np.array([len(ids)], dtype=np.int64), scales=scales)
    if "sid" in names:
        feeds["sid"] = np.array([0], dtype=np.int64)
    run_options, finished = ort.RunOptions(), threading.Event()
    def monitor():
        while not finished.wait(.05):
            if cancel is not None and cancel.is_set():
                run_options.terminate = True
                return
    observer = threading.Thread(target=monitor, name="local-tts-cancel", daemon=True) if cancel is not None else None
    if observer:
        observer.start()
    lock = _GPU_LOCK if device == "cuda" else threading.Lock()
    try:
        while not lock.acquire(timeout=.1):
            ai_tools._check_cancel(cancel)
        try:
            ai_tools._check_cancel(cancel)
            audio = np.asarray(session.run(None, feeds, run_options)[0], dtype=np.float32).reshape(-1)
        finally:
            lock.release()
    except Exception:
        ai_tools._check_cancel(cancel)
        raise
    finally:
        finished.set()
        if observer:
            observer.join(timeout=.2)
    ai_tools._check_cancel(cancel)
    if not audio.size or not np.isfinite(audio).all() or not np.any(np.abs(audio) > 1e-6):
        raise ai_tools.AIError("Model offline chưa tạo âm thanh hợp lệ.")
    # Piper produces float audio; normalize only when clipping would occur.
    peak = max(1.0, float(np.max(np.abs(audio))))
    return np.clip(audio / peak * 32767, -32768, 32767).astype("<i2").tobytes()


def synthesize(text, voice, output, cancel=None, progress=None, *, device="auto") -> str:
    """Generate WAV/MP3 atomically, using installed models and no network calls."""
    provider, identity = split_voice_id(voice)
    if provider=="kokoro":
        from foreign_tts import synthesize as create
        return create(text,voice,output,cancel,progress,device)
    ai_tools._check_cancel(cancel)
    text = str(text).strip()
    if not 1 <= len(text) <= 5000:
        raise ValueError("Nội dung giọng offline cần từ 1 đến 5000 ký tự.")
    device = str(device).lower()
    if device not in {"auto", "cpu", "cuda"}:
        raise ValueError("Thiết bị giọng offline cần chọn auto, cpu hoặc cuda.")
    target = ai_tools._output_target(output)
    if target.suffix.lower() not in {".wav", ".mp3"}:
        raise ValueError("Giọng offline cần đầu ra WAV hoặc MP3.")
    files, config, mapping, tones = _validated(provider,identity)
    # Bound each run for memory/cancellation without dropping words.
    chunks = re.findall(r".{1,240}(?:\s+|$)|\S{1,240}", text, flags=re.DOTALL)
    batches = [phones for chunk in chunks for phones in _phonemes(chunk.strip(), provider, mapping, tones)]
    if not batches:
        raise ValueError("Nội dung chưa có câu để đọc.")
    session, actual_device = _session(files, device, progress)
    ai_tools._check_cancel(cancel)
    sample_rate = int(config["audio"]["sample_rate"])
    with tempfile.TemporaryDirectory(prefix=".local-tts-", dir=target.parent) as temporary:
        folder = Path(temporary)
        staged = folder / "speech.wav"
        with wave.open(str(staged), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            for index, phonemes in enumerate(batches):
                ai_tools._check_cancel(cancel)
                try:
                    pcm = _run(session, _ids(phonemes, mapping), config, cancel, actual_device)
                except (ai_tools.CancelledError, ValueError, ai_tools.AIError):
                    raise
                except Exception as exc:
                    if device != "auto" or actual_device != "cuda":
                        raise ai_tools.AIError("Không tạo được giọng offline trên thiết bị đã chọn.") from exc
                    if progress:
                        progress(0, "GPU gặp lỗi; đang tạo lại câu bằng CPU.")
                    session, actual_device = _session(files, "cpu", progress)
                    pcm = _run(session, _ids(phonemes, mapping), config, cancel, actual_device)
                writer.writeframesraw(pcm)
                if index + 1 < len(batches):
                    writer.writeframesraw(b"\0\0" * round(sample_rate * .12))
                if progress:
                    progress(int(90 * (index + 1) / len(batches)), "Đã tạo câu " + str(index + 1) + "/" + str(len(batches)))
        if target.suffix.lower() == ".mp3":
            encoded = folder / "speech.mp3"
            ai_tools._run_ffmpeg(["-i", str(staged), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(encoded)], cancel)
            staged = encoded
        ai_tools._check_cancel(cancel)
        result = ai_tools._publish(staged, target, cancel)
    _LAST_RUNTIME[str(voice)] = dict(device=actual_device, providers=session.get_providers(), model=str(files["model"]),
                                    sample_rate=sample_rate, sentences=len(batches))
    if progress:
        progress(100, "Đã tạo giọng offline " + PROVIDER_INFO[provider]["name"] + ".")
    return result
