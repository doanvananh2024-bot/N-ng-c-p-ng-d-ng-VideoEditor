"""Subtitle helpers and optional AI jobs for VideoEditorIndependent.

Importing this module never starts a network request or downloads a model.
The GUI must explain the following before the user starts an AI action:
* transcribe: downloads a Whisper model on first use; audio is processed locally.
* translate_srt: sends subtitle text to Google Translate over the Internet.
* dub_srt: sends subtitle text to Microsoft's online Edge speech service.

All public job functions are synchronous and intended for a background thread.
If the app is frozen, call multiprocessing.freeze_support() in its main guard.
"""
from __future__ import annotations

import asyncio
import errno
import importlib
import math
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable, Iterable
import wave


Progress = Callable[[int, str], None]
VIETNAMESE_VOICES = {
    "Nữ · Hoài My": "vi-VN-HoaiMyNeural",
    "Nam · Nam Minh": "vi-VN-NamMinhNeural",
}
_TIMESTAMP = re.compile(r"^(\d+):(\d{2}):(\d{2})[,.](\d{1,3})$")
_CUE_TIMING = re.compile(
    r"^\s*(\d+:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(\d+:\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?$"
)
_LANGUAGES = {
    "tiếng việt": "vi", "vietnamese": "vi",
    "tiếng anh": "en", "english": "en",
    "tiếng trung": "zh-CN", "chinese": "zh-CN",
    "tiếng nhật": "ja", "japanese": "ja",
    "tiếng hàn": "ko", "korean": "ko",
    "tiếng pháp": "fr", "french": "fr",
    "tiếng đức": "de", "german": "de",
    "tiếng thái": "th", "thai": "th",
    "tự động": "auto", "automatic": "auto",
}


class AIError(RuntimeError):
    """An error with a message suitable for showing directly in the UI."""


class CancelledError(AIError):
    """A job was cancelled; no finished output is published."""


class _TranslationRateLimited(AIError):
    """A service limit must stop the job rather than cause immediate retries."""


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError("Đã dừng theo yêu cầu.")


def _progress(callback: Progress | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(percent))), message)


def _wait(seconds: float, cancel: threading.Event | None) -> None:
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        _check_cancel(cancel)


def parse_timestamp(value: str) -> float:
    """Parse an SRT timestamp; commas and decimal points are supported."""
    match = _TIMESTAMP.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"Mốc thời gian SRT không hợp lệ: {value}")
    hour, minute, second, fraction = match.groups()
    if int(minute) >= 60 or int(second) >= 60:
        raise ValueError(f"Mốc thời gian SRT không hợp lệ: {value}")
    return int(hour) * 3600 + int(minute) * 60 + int(second) + int(fraction.ljust(3, "0")) / 1000


def format_timestamp(seconds: float) -> str:
    """Round seconds to milliseconds without producing 60-second fields."""
    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Mốc thời gian phải là số giây không âm.")
    milliseconds = int(round(value * 1000))
    total_seconds, fraction = divmod(milliseconds, 1000)
    total_minutes, second = divmod(total_seconds, 60)
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d},{fraction:03d}"


def _validated_rows(rows: Iterable[dict]) -> list[dict]:
    result = []
    for index, row in enumerate(rows, 1):
        try:
            start, end = float(row["start"]), float(row["end"])
            text = str(row["text"]).replace("\r\n", "\n").replace("\r", "\n").strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Dòng phụ đề {index} thiếu thời gian hoặc nội dung hợp lệ.") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"Dòng phụ đề {index}: thời gian kết thúc phải lớn hơn bắt đầu.")
        if int(round(end * 1000)) <= int(round(start * 1000)):
            raise ValueError(f"Dòng phụ đề {index}: thời lượng phải ít nhất 1 mili giây.")
        if not text:
            raise ValueError(f"Dòng phụ đề {index} chưa có nội dung.")
        # Blank lines delimit SRT cues and cannot occur inside a cue.
        text = "\n".join(line for line in text.split("\n") if line.strip())
        result.append({"start": start, "end": end, "text": text})
    return result


def load_srt(path: str | os.PathLike) -> list[dict]:
    """Read UTF-8/UTF-16 SRT, retaining Vietnamese and multiline cue text."""
    source = Path(path).expanduser()
    raw = source.read_bytes()
    try:
        text = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Không đọc được phụ đề. Hãy lưu file SRT bằng UTF-8 hoặc UTF-16.") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    rows = []
    for block_number, block in enumerate(re.split(r"\n[ \t]*\n+", text), 1):
        lines = block.strip().split("\n")
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        match = _CUE_TIMING.fullmatch(lines[0]) if lines else None
        if not match or len(lines) < 2:
            raise ValueError(f"Khối phụ đề {block_number} không đúng định dạng SRT.")
        rows.append({
            "start": parse_timestamp(match.group(1)),
            "end": parse_timestamp(match.group(2)),
            "text": "\n".join(lines[1:]),
        })
    return _validated_rows(rows)


def _output_target(path: str | os.PathLike, source: str | os.PathLike | None = None) -> Path:
    target = Path(path).expanduser().absolute()
    if source is not None and target.resolve() == Path(source).expanduser().resolve():
        raise FileExistsError("Hãy chọn tên file đầu ra khác file nguồn.")
    if os.path.lexists(target):
        raise FileExistsError(f"File đầu ra đã tồn tại. Hãy chọn tên khác: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _publish(staged: Path, target: Path, cancel: threading.Event | None = None) -> str:
    """Publish without replacing any file, including one created during a job."""
    _check_cancel(cancel)
    try:
        os.link(staged, target)
    except FileExistsError as exc:
        raise FileExistsError(f"File đầu ra đã tồn tại. Hãy chọn tên khác: {target.name}") from exc
    except OSError as exc:
        if exc.errno == errno.EEXIST or os.path.lexists(target):
            raise FileExistsError(f"File đầu ra đã tồn tại. Hãy chọn tên khác: {target.name}") from exc
        created = False
        try:
            with target.open("xb") as destination:
                created = True
                with staged.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        _check_cancel(cancel)
                        destination.write(chunk)
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
    return str(target)


def _save_srt(rows: Iterable[dict], path: str | os.PathLike,
              cancel: threading.Event | None = None) -> str:
    _check_cancel(cancel)
    validated = _validated_rows(rows)
    target = _output_target(path)
    body = "\n\n".join(
        f"{number}\n{format_timestamp(row['start'])} --> {format_timestamp(row['end'])}\n{row['text']}"
        for number, row in enumerate(validated, 1)
    )
    handle, name = tempfile.mkstemp(prefix=".mediafusion-srt-", suffix=".tmp", dir=target.parent)
    staged = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(body + ("\n" if body else ""))
        return _publish(staged, target, cancel)
    finally:
        staged.unlink(missing_ok=True)


def save_srt(rows: Iterable[dict], path: str | os.PathLike) -> str:
    """Save Unicode SRT without overwriting an existing file."""
    return _save_srt(rows, path)


def _optional(module: str, package: str):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise AIError(
            f"Chưa cài thành phần AI '{package}'. Chạy Install AI.cmd trong thư mục VideoEditorIndependent, "
            "hoặc cài các gói trong requirements-ai.txt rồi mở lại ứng dụng."
        ) from exc


def _language(value: str | None, *, whisper: bool = False) -> str:
    value = (value or "auto").strip()
    normalized = _LANGUAGES.get(value.casefold(), value)
    if whisper and normalized.casefold() in {"zh-cn", "zh-tw"}:
        return "zh"
    return normalized


def _transcription_worker(connection, source: str, model_name: str, model_dir: str,
                          language_code: str, device: str = "cpu", batch_size: int = 1) -> None:
    """Keep model download/inference stoppable even inside a long native call."""
    model = None
    try:
        from gpu_runtime import configure_cuda_runtime
        if device!="cpu": configure_cuda_runtime()
        from faster_whisper import WhisperModel, BatchedInferencePipeline
        import ctranslate2
        actual_device="cuda" if device!="cpu" and ctranslate2.get_cuda_device_count()>0 else "cpu"
        if device=="cuda" and actual_device!="cuda":
            raise AIError("Đã chọn GPU nhưng CTranslate2 chưa nhận được CUDA.")
        connection.send(("progress", 2, "Đang nạp Whisper; lần đầu có thể cần tải mô hình qua mạng…"))
        try:
            model = WhisperModel(
                model_name, device=actual_device, compute_type="int8_float16" if actual_device=="cuda" else "int8", download_root=model_dir,
                cpu_threads=max(1, min(4, os.cpu_count() or 1)), num_workers=1,
            )
        except Exception:
            if device!="auto" or actual_device!="cuda": raise
            actual_device="cpu"
            model=WhisperModel(model_name,device="cpu",compute_type="int8",download_root=model_dir,cpu_threads=4,num_workers=1)
        connection.send(("progress", 15, "Whisper trên "+("GPU NVIDIA" if actual_device=="cuda" else "CPU")+"…"))
        recognizer=BatchedInferencePipeline(model=model) if batch_size>1 else model
        extra={"batch_size":batch_size} if batch_size>1 else {}
        segments, info = recognizer.transcribe(
            source, language=None if language_code == "auto" else language_code,
            beam_size=3, vad_filter=True, **extra,
        )
        duration = max(float(getattr(info, "duration", 0) or 0), 0.01)
        rows = []
        for segment in segments:
            text = str(segment.text).strip()
            if text:
                rows.append({"start": float(segment.start), "end": max(float(segment.end), float(segment.start) + .01), "text": text})
            percent = min(95, 15 + int(80 * float(segment.end) / duration))
            connection.send(("progress", percent, f"Đang tạo phụ đề: {len(rows)} dòng"))
        if not rows:
            raise AIError("Không nhận diện được giọng nói. Hãy kiểm tra âm thanh và ngôn ngữ nguồn.")
        connection.send(("result", rows))
    except Exception as exc:
        connection.send(("error", f"Không tạo được phụ đề: {exc}"))
    finally:
        del model
        connection.close()


def _stop_worker(worker) -> None:
    if worker.pid is not None:
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=3)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=3)
        worker.close()


def transcribe(path: str, output: str, language: str = "auto", progress: Progress | None = None,
               cancel: threading.Event | None = None) -> str:
    """Generate SRT on CPU. First use downloads base/tiny Whisper; audio stays local.

    A separate worker makes download/inference cancellable. Interrupted model
    downloads may leave a resumable cache; a finished SRT is never published.
    MEDIAFUSION_WHISPER_MODEL may select 'tiny' or 'base' (default: 'base').
    """
    _check_cancel(cancel)
    source = Path(path).expanduser()
    if not source.is_file():
        raise AIError("Không tìm thấy file video hoặc âm thanh nguồn.")
    _output_target(output, source)
    _optional("faster_whisper", "faster-whisper")
    model_name = os.environ.get("MEDIAFUSION_WHISPER_MODEL", "base").strip().lower()
    if model_name not in {"tiny", "base"}:
        raise AIError("MEDIAFUSION_WHISPER_MODEL chỉ hỗ trợ 'tiny' hoặc 'base'.")
    model_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VideoEditorIndependent" / "models" / "whisper"
    _progress(progress, 2, "Đang nạp Whisper; lần đầu có thể cần tải mô hình qua mạng…")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(
        target=_transcription_worker,
        args=(sender, str(source), model_name, str(model_dir), _language(language, whisper=True)),
        daemon=True,
    )
    try:
        worker.start()
        sender.close()
        while True:
            _check_cancel(cancel)
            if receiver.poll(.1):
                try:
                    message = receiver.recv()
                except EOFError as exc:
                    raise AIError("Tiến trình tạo phụ đề đã đóng trước khi trả kết quả.") from exc
                if message[0] == "progress":
                    _progress(progress, message[1], message[2])
                elif message[0] == "result":
                    rows = message[1]
                    break
                elif message[0] == "error":
                    raise AIError(message[1])
            elif not worker.is_alive():
                raise AIError("Tiến trình tạo phụ đề bị gián đoạn.")
        _check_cancel(cancel)
        result = _save_srt(rows, output, cancel)
        _progress(progress, 100, "Đã tạo phụ đề SRT.")
        return result
    except (CancelledError, AIError, FileExistsError):
        raise
    except Exception as exc:
        raise AIError(f"Không tạo được phụ đề: {exc}") from exc
    finally:
        sender.close()
        receiver.close()
        _stop_worker(worker)


def _translation_worker(connection, texts: list[str], target: str) -> None:
    """Use Google's public translation interface with bounded, paced requests.

    The mobile HTML interface used by deep-translator can reject automation.
    This public JSON interface is separate from the paid Cloud Translation API
    and has no guaranteed availability. Do not switch endpoints on HTTP 429.
    """
    try:
        import requests
        result = []
        with requests.Session() as session:
            for index, text in enumerate(texts):
                if index:
                    time.sleep(.35)
                with session.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={"client": "gtx", "dt": "t", "sl": "auto", "tl": target, "q": text},
                    timeout=(10, 20),
                ) as response:
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After", "")
                        wait_hint = f" Thử lại sau {retry_after} giây." if retry_after.isdigit() else " Hãy thử lại sau."
                        connection.send((False, {
                            "kind": "rate_limit",
                            "message": "Google đang giới hạn số lượt dịch trên kết nối này." + wait_hint,
                        }))
                        return
                    if not response.ok:
                        raise AIError(f"Dịch vụ Google trả lỗi HTTP {response.status_code}.")
                    data = response.json()
                if not isinstance(data, list) or not data or not isinstance(data[0], list):
                    raise AIError("Dịch vụ Google trả kết quả không đúng định dạng.")
                translated = "".join(
                    piece[0] for piece in data[0]
                    if isinstance(piece, list) and piece and isinstance(piece[0], str)
                ).strip()
                if not translated:
                    raise AIError("Dịch vụ Google không trả nội dung bản dịch.")
                result.append(translated)
        connection.send((True, result))
    except Exception as exc:
        # Request exception URLs can contain cue text; keep those out of logs/UI.
        detail = str(exc) if isinstance(exc, AIError) else type(exc).__name__
        connection.send((False, detail))
    finally:
        connection.close()


def _translate_batch(texts: list[str], target: str, cancel: threading.Event | None,
                     timeout: float = 90) -> list[str]:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_translation_worker, args=(sender, texts, target), daemon=True)
    try:
        _check_cancel(cancel)
        worker.start()
        sender.close()
        started = time.monotonic()
        while True:
            _check_cancel(cancel)
            if receiver.poll(.1):
                try:
                    ok, result = receiver.recv()
                except EOFError as exc:
                    raise AIError("Tiến trình dịch đã đóng trước khi trả kết quả.") from exc
                if not ok:
                    if isinstance(result, dict) and result.get("kind") == "rate_limit":
                        raise _TranslationRateLimited(result["message"])
                    raise AIError(str(result))
                if not isinstance(result, list) or len(result) != len(texts) or any(not isinstance(x, str) or not x.strip() for x in result):
                    raise AIError("Dịch vụ dịch trả về số dòng hoặc nội dung không hợp lệ.")
                return result
            if not worker.is_alive():
                raise AIError("Tiến trình dịch bị gián đoạn.")
            if time.monotonic() - started > timeout:
                raise AIError("Dịch vụ dịch không phản hồi trong 90 giây.")
    finally:
        sender.close()
        receiver.close()
        _stop_worker(worker)


def _text_chunks(text: str, limit: int = 4500) -> list[str]:
    chunks = []
    remainder = text
    while len(remainder) > limit:
        split = max(remainder.rfind("\n", 0, limit), remainder.rfind(" ", 0, limit))
        if split < limit // 2:
            split = limit
        chunks.append(remainder[:split].strip())
        remainder = remainder[split:].strip()
    if remainder:
        chunks.append(remainder)
    return chunks


def translate_srt(input: str, output: str, target: str = "vi", progress: Progress | None = None,
                  cancel: threading.Event | None = None) -> str:
    """Translate SRT via Google's public JSON interface; sends cue text online.

    Work is batched with three attempts and a cancellable 90-second limit per
    batch. HTTP 429 stops immediately. Timing is kept; a failed or cancelled job
    never publishes an incomplete SRT file. This is not a paid Cloud API client.
    """
    _check_cancel(cancel)
    rows = load_srt(input)
    if not rows:
        raise AIError("File phụ đề chưa có nội dung để dịch.")
    _output_target(output, input)
    _optional("requests", "requests")
    target_code = _language(target)
    if target_code == "auto":
        raise AIError("Hãy chọn ngôn ngữ đích để dịch.")
    pieces = [(index, chunk) for index, row in enumerate(rows) for chunk in _text_chunks(row["text"])]
    translated = [[] for _ in rows]
    for first in range(0, len(pieces), 6):
        _check_cancel(cancel)
        batch = pieces[first:first + 6]
        texts = [text for _, text in batch]
        result = None
        for attempt in range(3):
            _check_cancel(cancel)
            try:
                result = _translate_batch(texts, target_code, cancel)
                break
            except (CancelledError, _TranslationRateLimited):
                raise
            except Exception as exc:
                if attempt == 2:
                    raise AIError(f"Không dịch được phụ đề. Kiểm tra mạng hoặc thử lại sau. Chi tiết: {exc}") from exc
                _progress(progress, int(first * 95 / len(pieces)), f"Dịch vụ đang bận, thử lại lần {attempt + 2}/3…")
                _wait(2 * 2 ** attempt, cancel)
        for (index, _), text in zip(batch, result):
            translated[index].append(text.strip())
        _progress(progress, int(min(first + len(batch), len(pieces)) * 95 / len(pieces)), "Đang dịch phụ đề…")
    _check_cancel(cancel)
    for index, row in enumerate(rows):
        row["text"] = "\n".join(translated[index])
    result = _save_srt(rows, output, cancel)
    _progress(progress, 100, "Đã lưu phụ đề dịch.")
    return result


def _ffmpeg_path() -> str:
    try:
        from engine import tool_path
        found = tool_path("ffmpeg")
        if found:
            return str(found)
    except (ImportError, FileNotFoundError, RuntimeError, TypeError):
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (
        Path(__file__).resolve().parent / "tools" / "ffmpeg.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    raise AIError("Không tìm thấy FFmpeg. Hãy cài FFmpeg hoặc đặt ffmpeg.exe vào thư mục tools.")


def _run_ffmpeg(arguments: list[str], cancel: threading.Event | None) -> None:
    _check_cancel(cancel)
    process = subprocess.Popen(
        [_ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        while True:
            _check_cancel(cancel)
            try:
                _, error = process.communicate(timeout=.15)
                break
            except subprocess.TimeoutExpired:
                pass
        _check_cancel(cancel)
        if process.returncode:
            detail = (error or b"").decode("utf-8", errors="replace").strip()[-1500:]
            raise AIError(f"FFmpeg không xử lý được âm thanh: {detail}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stderr:
            process.stderr.close()


async def _synthesize(edge, text: str, voice: str, path: Path, cancel: threading.Event | None) -> None:
    if voice.startswith(("ngochuyen:", "ngochuyen_v2:", "kokoro:")):
        import local_tts
        _check_cancel(cancel)
        await asyncio.to_thread(local_tts.synthesize, text, voice, str(path), cancel,
                                device=local_tts.current_device())
        _check_cancel(cancel)
        if not path.is_file() or not path.stat().st_size:
            raise AIError("Giọng offline chưa tạo được tệp âm thanh.")
        return
    if voice.partition(":")[0] in ("fpt","elevenlabs","gtts","minimax","siliconflow","vbee","zalo","capcut") and ":" in voice:
        from tts_providers import synthesize
        _check_cancel(cancel)
        await asyncio.to_thread(synthesize, text, voice, str(path), cancel)
        _check_cancel(cancel)
        if not path.is_file() or not path.stat().st_size:
            raise AIError("Dịch vụ giọng đọc trả về tệp âm thanh rỗng.")
        return
    for attempt in range(3):
        _check_cancel(cancel)
        task = asyncio.create_task(edge.Communicate(text, voice, connect_timeout=10, receive_timeout=30).save(str(path)))
        try:
            started = time.monotonic()
            while not task.done():
                _check_cancel(cancel)
                if time.monotonic() - started > 90:
                    raise AIError("Dịch vụ giọng đọc không phản hồi trong 90 giây.")
                await asyncio.wait({task}, timeout=.1)
            await task
            if not path.is_file() or not path.stat().st_size:
                raise AIError("Dịch vụ giọng đọc trả về file âm thanh rỗng.")
            return
        except CancelledError:
            raise
        except Exception as exc:
            if attempt == 2:
                raise AIError(f"Không tạo được giọng đọc. Kiểm tra mạng hoặc giọng đã chọn. Chi tiết: {exc}") from exc
            for _ in range(10 * (attempt + 1)):
                _check_cancel(cancel)
                await asyncio.sleep(.1)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _write_silence(writer, frames: int, cancel: threading.Event | None) -> None:
    zeros = b"\0" * 65536
    while frames > 0:
        _check_cancel(cancel)
        chunk_frames = min(frames, len(zeros) // 2)
        writer.writeframesraw(zeros[:chunk_frames * 2])
        frames -= chunk_frames


def _tempo_filters(speed: float) -> list[str]:
    """Chain factors at or below 2x so FFmpeg retains the full speech signal."""
    if not math.isfinite(speed) or speed < 1:
        raise ValueError("Tốc độ khớp giọng phải là số hữu hạn từ 1 trở lên.")
    filters = []
    while speed > 2:
        filters.append("atempo=2")
        speed /= 2
    if speed > 1.0000001:
        filters.append(f"atempo={speed:.10f}")
    return filters


def dub_srt(input: str, output: str, voice: str = "vi-VN-HoaiMyNeural", progress: Progress | None = None,
            cancel: threading.Event | None = None) -> str:
    """Create timed narration through the explicitly selected online TTS engine.

    FFmpeg converts each cue to 24 kHz mono PCM. Longer speech is sped up with
    pitch-preserving atempo chains to fit before its end/the next cue's start;
    shorter speech is padded. Each sentence starts at its subtitle time and
    keeps its full content without overlapping speech. Speeds above 2x generate
    a progress warning: shorten the text or lengthen its timing for clearer speech.
    Supports WAV, MP3, M4A, AAC and FLAC. No video is sent to the speech service.
    """
    _check_cancel(cancel)
    rows = sorted(load_srt(input), key=lambda row: row["start"])
    if not rows:
        raise AIError("File phụ đề chưa có nội dung để lồng tiếng.")
    target = _output_target(output, input)
    extension = target.suffix.casefold()
    codecs = {
        ".wav": ["-c:a", "pcm_s16le"],
        ".mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
        ".m4a": ["-c:a", "aac", "-b:a", "128k"],
        ".aac": ["-c:a", "aac", "-b:a", "128k"],
        ".flac": ["-c:a", "flac"],
    }
    if extension not in codecs:
        raise AIError("Chọn file lồng tiếng WAV, MP3, M4A, AAC hoặc FLAC.")
    for index in range(len(rows) - 1):
        if round(rows[index + 1]["start"] * 24000) <= round(rows[index]["start"] * 24000):
            raise AIError("Có nhiều dòng phụ đề bắt đầu cùng lúc. Hãy chỉnh thời gian trước khi lồng tiếng.")
    edge = _optional("edge_tts", "edge-tts")
    _ffmpeg_path()
    voice = VIETNAMESE_VOICES.get(voice, voice) or "vi-VN-HoaiMyNeural"
    sample_rate = 24000
    maximum_speed = 1.0
    with tempfile.TemporaryDirectory(prefix=".mediafusion-dub-", dir=target.parent) as temporary:
        work = Path(temporary)
        assembled = work / "assembled.wav"
        position = 0
        with wave.open(str(assembled), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            for index, row in enumerate(rows):
                _check_cancel(cancel)
                start_frame = round(row["start"] * sample_rate)
                end = min(row["end"], rows[index + 1]["start"]) if index + 1 < len(rows) else row["end"]
                end_frame = max(start_frame + 1, round(end * sample_rate))
                frames = end_frame - start_frame
                _progress(progress, int(index * 85 / len(rows)), f"Đang tạo giọng đọc: {index + 1}/{len(rows)}")
                speech, natural, fitted = work / "cue.mp3", work / "cue-natural.wav", work / "cue.wav"
                asyncio.run(_synthesize(edge, row["text"], voice, speech, cancel))
                _run_ffmpeg([
                    "-i", str(speech), "-vn", "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le", str(natural),
                ], cancel)
                with wave.open(str(natural), "rb") as reader:
                    natural_frames = reader.getnframes()
                    if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, sample_rate) or not natural_frames:
                        raise AIError("Dịch vụ giọng đọc trả về âm thanh không hợp lệ.")
                filters = []
                if natural_frames > frames:
                    # Small slack absorbs atempo block rounding, retaining the final
                    # sound before the hard timing boundary even for short cues.
                    slack = min(round(.04 * sample_rate), max(1, round(frames * .05)))
                    speed = natural_frames / max(1, frames - slack)
                    maximum_speed = max(maximum_speed, speed)
                    filters.extend(_tempo_filters(speed))
                    if speed > 2:
                        _progress(progress, int(index * 85 / len(rows)),
                                  f"Câu {index + 1} cần đọc nhanh {speed:.1f} lần. Nên kéo dài thời gian hoặc rút ngắn nội dung để dễ nghe.")
                filters.extend(["apad", f"atrim=duration={frames / sample_rate:.8f}"])
                _run_ffmpeg([
                    "-i", str(natural), "-vn", "-af", ",".join(filters),
                    "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le", str(fitted),
                ], cancel)
                _write_silence(writer, max(0, start_frame - position), cancel)
                written = 0
                with wave.open(str(fitted), "rb") as reader:
                    if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, sample_rate):
                        raise AIError("Âm thanh của câu chưa được chuẩn hoá đúng định dạng.")
                    while written < frames:
                        _check_cancel(cancel)
                        data = reader.readframes(min(32768, frames - written))
                        if not data:
                            break
                        writer.writeframesraw(data)
                        written += len(data) // 2
                _write_silence(writer, frames - written, cancel)
                position = end_frame
                speech.unlink(missing_ok=True)
                natural.unlink(missing_ok=True)
                fitted.unlink(missing_ok=True)
            total_frames = round(max(row["end"] for row in rows) * sample_rate)
            _write_silence(writer, max(0, total_frames - position), cancel)
        _check_cancel(cancel)
        _progress(progress, 90, "Đang lưu bản lồng tiếng…")
        if extension == ".wav":
            staged = assembled
        else:
            staged = work / f"narration{extension}"
            _run_ffmpeg(["-i", str(assembled), "-vn", *codecs[extension], str(staged)], cancel)
        result = _publish(staged, target, cancel)
    finished = "Đã lưu âm thanh lồng tiếng theo thời gian phụ đề."
    if maximum_speed > 2:
        finished += f" Có câu đọc nhanh {maximum_speed:.1f} lần; hãy kiểm tra và kéo dài thời gian hoặc rút ngắn câu nếu khó nghe."
    _progress(progress, 100, finished)
    return result
