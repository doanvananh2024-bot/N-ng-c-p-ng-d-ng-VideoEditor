"""Offline subtitle editing, format conversion and optional cue metadata.

Every editing function returns copied rows. Writers preserve existing files and
SRT/VTT sidecars bind metadata (for example a cue's voice) to the exact text file.
"""
from __future__ import annotations

import copy
import asyncio
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import wave
from array import array
from typing import Iterable

import ai_tools


def _rows(rows: Iterable[dict]) -> list[dict]:
    originals = [copy.deepcopy(row) for row in rows]
    valid = ai_tools._validated_rows(originals)
    return [dict(original, **row) for original, row in zip(originals, valid)]


def shift_timings(rows: Iterable[dict], seconds: float, indices: Iterable[int] | None = None) -> list[dict]:
    """Shift all or selected zero-based cues, clamping each start at zero.

    When a negative shift crosses zero, retain the cue duration. The function
    rejects unknown indices rather than silently changing an unintended row.
    """
    result = _rows(rows)
    amount = float(seconds)
    if not math.isfinite(amount):
        raise ValueError("Độ lệch thời gian phải là số hữu hạn.")
    selected = set(range(len(result))) if indices is None else {int(i) for i in indices}
    if any(i < 0 or i >= len(result) for i in selected):
        raise IndexError("Dòng phụ đề được chọn không còn tồn tại.")
    for index in selected:
        row = result[index]
        duration = row["end"] - row["start"]
        row["start"] = max(0.0, row["start"] + amount)
        row["end"] = row["start"] + duration
    return _rows(result)


def merge_rows(rows: Iterable[dict], indices: Iterable[int], separator: str = "\n") -> list[dict]:
    """Combine selected contiguous cues, retaining text, interval and metadata.

    Combining non-adjacent rows would reorder dialogue, so it is rejected.
    Distinct voice assignments are retained in ``merged_voices``; the first
    cue's voice remains the voice used for the combined spoken cue.
    """
    result = _rows(rows)
    picked = sorted({int(i) for i in indices})
    if not picked:
        return result
    if picked[0] < 0 or picked[-1] >= len(result):
        raise IndexError("Dòng phụ đề được chọn không còn tồn tại.")
    if picked != list(range(picked[0], picked[-1] + 1)):
        raise ValueError("Hãy chọn các dòng liền nhau để ghép đúng thứ tự lời thoại.")
    cues = result[picked[0]:picked[-1] + 1]
    merged = copy.deepcopy(cues[0])
    merged.update(start=min(row["start"] for row in cues), end=max(row["end"] for row in cues),
                  text=str(separator).join(row["text"] for row in cues))
    voices = list(dict.fromkeys(str(row["voice"]) for row in cues if row.get("voice")))
    if len(voices) > 1:
        merged["merged_voices"] = voices
    return _rows(result[:picked[0]] + [merged] + result[picked[-1] + 1:])


def merge_adjacent(rows: Iterable[dict], max_gap: float = .15, max_duration: float = 12.0,
                   separator: str = " ", same_voice: bool = True) -> list[dict]:
    """Merge neighboring cues within the requested gap and maximum duration."""
    source = _rows(rows)
    gap, duration = float(max_gap), float(max_duration)
    if not math.isfinite(gap) or gap < 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Khoảng cách phải không âm và thời lượng ghép phải lớn hơn 0.")
    result = []
    for row in source:
        if result:
            previous = result[-1]
            compatible = not same_voice or previous.get("voice", "") == row.get("voice", "")
            if (compatible and row["start"] >= previous["start"] and
                    row["start"] - previous["end"] <= gap and
                    max(previous["end"], row["end"]) - previous["start"] <= duration):
                result[-1] = merge_rows([previous, row], [0, 1], separator)[0]
                continue
        result.append(row)
    return result


def replace_text(rows: Iterable[dict], search: str, replacement: str,
                 case_sensitive: bool = False, whole_word: bool = False,
                 indices: Iterable[int] | None = None) -> tuple[list[dict], int]:
    """Literal Unicode search/replace; returns (copied_rows, occurrence_count)."""
    result = _rows(rows)
    if not str(search):
        raise ValueError("Nhập nội dung cần tìm.")
    selected = set(range(len(result))) if indices is None else {int(i) for i in indices}
    if any(i < 0 or i >= len(result) for i in selected):
        raise IndexError("Dòng phụ đề được chọn không còn tồn tại.")
    pattern = re.escape(str(search))
    if whole_word:
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    count = 0
    for index in selected:
        result[index]["text"], found = regex.subn(lambda _match: str(replacement), result[index]["text"])
        count += found
    return _rows(result), count


def bilingual_rows(original: Iterable[dict], translated: Iterable[dict],
                    original_first: bool = True) -> list[dict]:
    """Compose aligned bilingual cues without discarding either subtitle file."""
    originals, translations = _rows(original), _rows(translated)
    if len(originals) != len(translations):
        raise ValueError("Hai bản phụ đề phải có cùng số dòng để tạo phụ đề song ngữ.")
    result = []
    for number, (source, target) in enumerate(zip(originals, translations), 1):
        if abs(source["start"] - target["start"]) > .01 or abs(source["end"] - target["end"]) > .01:
            raise ValueError(f"Thời gian hai bản phụ đề chưa khớp ở dòng {number}.")
        texts = [source["text"], target["text"]] if original_first else [target["text"], source["text"]]
        result.append(dict(target, text="\n".join(texts), original_text=source["text"],
                           translated_text=target["text"]))
    return result


def _write_text(path: str | os.PathLike, text: str) -> str:
    target = ai_tools._output_target(path)
    descriptor, name = tempfile.mkstemp(prefix=".mediafusion-subtitle-", suffix=".tmp", dir=target.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
        return ai_tools._publish(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def _vtt_time(seconds: float) -> str:
    return ai_tools.format_timestamp(seconds).replace(",", ".")


def _parse_vtt_time(value: str) -> float:
    value = value.strip()
    if value.count(":") == 1:
        value = "00:" + value
    return ai_tools.parse_timestamp(value)


def load_vtt(path: str | os.PathLike) -> list[dict]:
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if not re.match(r"^WEBVTT(?:[ \t].*)?(?:\n|$)", raw):
        raise ValueError("Tệp chưa có tiêu đề WEBVTT hợp lệ.")
    result = []
    for block in re.split(r"\n[ \t]*\n+", raw.strip()):
        lines = block.strip().splitlines()
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        if "-->" not in lines[0]:
            lines = lines[1:]
        if not lines or "-->" not in lines[0] or len(lines) < 2:
            raise ValueError("Một khối phụ đề VTT thiếu thời gian hoặc nội dung.")
        times = re.fullmatch(r"\s*(\S+)\s*-->\s*(\S+)(?:\s+.*)?", lines[0])
        if not times:
            raise ValueError("Mốc thời gian VTT không hợp lệ.")
        result.append(dict(start=_parse_vtt_time(times.group(1)), end=_parse_vtt_time(times.group(2)),
                           text="\n".join(lines[1:])))
    return _rows(result)


def save_vtt(rows: Iterable[dict], path: str | os.PathLike) -> str:
    body = "\n\n".join(f"{index}\n{_vtt_time(row['start'])} --> {_vtt_time(row['end'])}\n{row['text']}"
                       for index, row in enumerate(_rows(rows), 1))
    return _write_text(path, "WEBVTT\n\n" + body + ("\n" if body else ""))


def _ass_time(seconds: float) -> str:
    value = int(round(seconds * 100))
    total_seconds, hundredths = divmod(value, 100)
    total_minutes, second = divmod(total_seconds, 60)
    hour, minute = divmod(total_minutes, 60)
    return f"{hour}:{minute:02d}:{second:02d}.{hundredths:02d}"


def _ass_color(value: str, default: str) -> str:
    value = str(value or default).strip()
    if re.fullmatch(r"&H[0-9a-fA-F]{6,8}&?", value):
        return value.rstrip("&").upper()
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", value)
    if not match:
        raise ValueError("Màu phụ đề phải theo dạng #RRGGBB.")
    rgb = match.group(1)
    return "&H00" + rgb[4:6] + rgb[2:4] + rgb[0:2]


def save_ass(rows: Iterable[dict], path: str | os.PathLike, style: dict | None = None,
             width: int = 1920, height: int = 1080) -> str:
    """Create a styled ASS file usable by FFmpeg/libass and subtitle editors."""
    rows = _rows(rows)
    style = dict(style or {})
    width, height = int(width), int(height)
    if width < 2 or height < 2 or width > 16384 or height > 16384:
        raise ValueError("Kích thước khung phụ đề không hợp lệ.")
    font = str(style.get("font_name", style.get("font", "Arial"))).strip()
    if not font or any(character in font for character in ",\r\n"):
        raise ValueError("Tên phông chữ không hợp lệ.")
    size = float(style.get("font_size", style.get("size", 48)))
    outline, shadow = float(style.get("outline", 2)), float(style.get("shadow", 1))
    margin = int(style.get("margin_v", 35))
    alignment = int(style.get("alignment", 2))
    if not math.isfinite(size) or not 4 <= size <= 400 or alignment not in range(1, 10):
        raise ValueError("Cỡ chữ hoặc vị trí phụ đề không hợp lệ.")
    if any(not math.isfinite(n) or not 0 <= n <= 40 for n in (outline, shadow)) or not 0 <= margin <= height:
        raise ValueError("Viền, bóng hoặc lề phụ đề không hợp lệ.")
    primary = _ass_color(style.get("color", style.get("primary_color", "#FFFFFF")), "#FFFFFF")
    edge = _ass_color(style.get("outline_color", "#000000"), "#000000")
    back = _ass_color(style.get("back_color", "#000000"), "#000000")
    border = 3 if style.get("background", False) else 1
    bold, italic = (-1 if style.get(name, False) else 0 for name in ("bold", "italic"))
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n"
              "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
              "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
              "Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Default,{font},{size:g},{primary},{primary},{edge},{back},{bold},{italic},0,0,100,100,0,0,"
              f"{border},{outline:g},{shadow:g},{alignment},25,25,{margin},1\n\n[Events]\n"
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events = []
    for row in rows:
        # Backslashes and override braces in plain dialogue must not become
        # user supplied style commands. The full-width braces remain readable.
        text = row["text"].replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", r"\N")
        events.append(f"Dialogue: 0,{_ass_time(row['start'])},{_ass_time(row['end'])},Default,,0,0,0,,{text}")
    return _write_text(path, header + "\n".join(events) + ("\n" if events else ""))


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".metadata.json")


def save_subtitles(rows: Iterable[dict], path: str | os.PathLike,
                   preserve_metadata: bool = True, style: dict | None = None) -> str:
    """Write SRT/VTT/ASS, keeping optional voice assignments in a sidecar."""
    rows = _rows(rows)
    target = Path(path).expanduser().absolute()
    metadata = [{key: value for key, value in row.items() if key not in {"start", "end", "text"}} for row in rows]
    needs_metadata = preserve_metadata and any(metadata)
    sidecar = _sidecar(target)
    if needs_metadata and os.path.lexists(sidecar):
        raise FileExistsError(f"Metadata đã tồn tại: {sidecar.name}")
    suffix = target.suffix.lower()
    if suffix == ".srt":
        saved = ai_tools.save_srt(rows, target)
    elif suffix == ".vtt":
        saved = save_vtt(rows, target)
    elif suffix in {".ass", ".ssa"}:
        saved = save_ass(rows, target, style)
    else:
        raise ValueError("Chọn tệp phụ đề SRT, VTT hoặc ASS.")
    if needs_metadata:
        try:
            payload = dict(version=1, sha256=hashlib.sha256(target.read_bytes()).hexdigest(), cues=metadata)
            _write_text(sidecar, json.dumps(payload, ensure_ascii=False, indent=2))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return saved


def load_subtitles(path: str | os.PathLike, preserve_metadata: bool = True) -> list[dict]:
    """Read SRT/VTT and accept cue metadata only if its SHA-256 still matches."""
    source = Path(path).expanduser()
    if source.suffix.lower() == ".vtt":
        rows = load_vtt(source)
    elif source.suffix.lower() == ".srt":
        rows = ai_tools.load_srt(source)
    else:
        raise ValueError("Nhập phụ đề SRT hoặc VTT.")
    sidecar = _sidecar(source)
    if preserve_metadata and sidecar.is_file():
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if (payload.get("version") == 1 and payload.get("sha256") == hashlib.sha256(source.read_bytes()).hexdigest()
                    and isinstance(payload.get("cues"), list) and len(payload["cues"]) == len(rows)):
                for row, metadata in zip(rows, payload["cues"]):
                    if isinstance(metadata, dict):
                        row.update({key: value for key, value in metadata.items() if key not in {"start", "end", "text"}})
        except (ValueError, OSError, TypeError):
            pass  # Stale/broken metadata must not make valid subtitles unreadable.
    return rows


def dub_rows(rows: Iterable[dict], output: str, default_voice: str = "vi-VN-HoaiMyNeural",
             progress=None, cancel: threading.Event | None = None, *, voice_speed: float = 1,
             voice_delay: float = 0, voice_fit: str = "fit", pronunciation: dict | None = None,
             voice_parallel: int = 1, voice_max_speed: float | None = None) -> str:
    """Speak timed cues with their own voice, or the selected default voice.

    Each distinct voice is synthesized as a sparse timed WAV; tracks are mixed
    in bounded chunks on disk. Text is sent to the explicitly selected Edge,
    FPT or ElevenLabs engine. No request occurs until this function is called.
    """
    rows = sorted(_rows(rows), key=lambda row: (row["start"], row["end"]))
    # A bilingual cue displays both languages while its speech follows the
    # translated role. Only accept metadata that still describes this text;
    # later edits must not silently speak an older translated sentence.
    for row in rows:
        original, translated = row.get("original_text"), row.get("translated_text")
        if isinstance(original, str) and isinstance(translated, str) and translated.strip():
            displayed = row["text"].strip()
            variants = (translated.strip(), (original + "\n" + translated).strip(),
                        (translated + "\n" + original).strip())
            if displayed in variants:
                row["text"] = translated
    ai_tools._check_cancel(cancel)
    if not rows:
        raise ValueError("Phụ đề chưa có dòng nào để lồng tiếng.")
    target = ai_tools._output_target(output)
    if target.suffix.lower() != ".wav":
        raise ValueError("Lồng tiếng nhiều giọng cần chọn đầu ra WAV.")
    speed, delay = float(voice_speed), float(voice_delay)
    if not math.isfinite(speed) or not .25 <= speed <= 4:
        raise ValueError("Tốc độ giọng cần trong khoảng 0.25 đến 4 lần.")
    if not math.isfinite(delay) or abs(delay) > 60000:
        raise ValueError("Trễ giọng cần trong khoảng -60000 đến 60000 mili giây.")
    if voice_fit=="off":
        voice_fit="natural"
        speed=1
    if voice_fit not in {"fit", "natural"}:
        raise ValueError("Khớp giọng cần chọn fit hoặc natural.")
    if pronunciation is not None and not isinstance(pronunciation, dict):
        raise ValueError("Bảng sửa cách đọc cần gồm từ gốc và cách đọc thay thế.")
    if type(voice_parallel) is not int or not 1 <= voice_parallel <= 8:
        raise ValueError("Luồng lồng tiếng song song cần trong khoảng 1 đến 8.")
    if voice_max_speed is not None:
        voice_max_speed=float(voice_max_speed)
        if not math.isfinite(voice_max_speed) or not 1<=voice_max_speed<=4:
            raise ValueError("Tốc độ giọng tối đa cần từ 1 đến 4 lần.")
    if voice_fit == "fit" and any(round(after["start"] * 24000) <= round(before["start"] * 24000)
                                   for before, after in zip(rows, rows[1:])):
        raise ValueError("Có nhiều dòng phụ đề bắt đầu cùng lúc. Hãy chỉnh thời gian trước khi lồng tiếng.")
    if speed != 1 or delay or voice_fit != "fit" or pronunciation or voice_parallel > 1 or voice_max_speed is not None:
        return _dub_configured(rows, str(target), default_voice, progress, cancel,
                               speed, delay / 1000, voice_fit, pronunciation or {}, voice_parallel,voice_max_speed)
    groups = {}
    for row in rows:
        voice = str(row.get("voice") or default_voice)
        voice = ai_tools.VIETNAMESE_VOICES.get(voice, voice)
        groups.setdefault(voice, []).append(row)
    if len(groups) == 1:
        with tempfile.TemporaryDirectory(prefix=".mediafusion-cue-voices-", dir=target.parent) as temporary:
            source = Path(temporary) / "captions.srt"
            ai_tools.save_srt(rows, source)
            return ai_tools.dub_srt(str(source), str(target), next(iter(groups)), progress, cancel)
    # Fitting must use the next cue from the whole timeline, even when its
    # speaker changes. Grouping by voice allowed speech to run over that cue.
    return _dub_configured(rows, str(target), default_voice, progress, cancel,
                           speed, delay / 1000, voice_fit, pronunciation or {})


def _dub_configured(rows, output, default_voice, progress, cancel, speed, delay, fit, pronunciation,
                    voice_parallel=1,voice_max_speed=None):
    """Render pitch-preserving speed and delay; natural speech may overlap.

    Individual cue WAVs stay on disk. Chunked mixing bounds memory even for
    long projects and preserves every cue's selected voice and start time.
    Pronunciation substitutions affect speech only, never subtitle text.
    """
    target = ai_tools._output_target(output)
    edge = ai_tools._optional("edge_tts", "edge-tts")
    sample_rate = 24000
    maximum_speed = 1.0
    with tempfile.TemporaryDirectory(prefix=".mediafusion-voice-timing-", dir=target.parent) as temporary:
        folder = Path(temporary)
        tracks = [None] * len(rows)
        stopped = threading.Event()

        class CueCancel:
            def is_set(self):
                return stopped.is_set() or (cancel is not None and cancel.is_set())

            def wait(self, timeout=None):
                deadline = None if timeout is None else time.monotonic() + max(0, timeout)
                while not self.is_set():
                    remaining = .05 if deadline is None else min(.05, deadline - time.monotonic())
                    if remaining <= 0:
                        return self.is_set()
                    stopped.wait(remaining)
                return True

        cue_cancel = CueCancel()

        def render_cue(index):
            row = rows[index]
            ai_tools._check_cancel(cue_cancel)
            voice = str(row.get("voice") or default_voice)
            voice = ai_tools.VIETNAMESE_VOICES.get(voice, voice)
            text = row["text"]
            # Longest literal replacements first avoid nested term collisions.
            for old in sorted(pronunciation, key=lambda value: len(str(value)), reverse=True):
                if str(old):
                    text = text.replace(str(old), str(pronunciation[old]))
            if not text.strip():
                raise ValueError(f"Bảng cách đọc làm câu {index + 1} thành rỗng.")
            speech = folder / f"cue_{index:05d}.mp3"
            natural = folder / f"natural_{index:05d}.wav"
            fitted = folder / f"fitted_{index:05d}.wav"
            try:
                asyncio.run(ai_tools._synthesize(edge, text, voice, speech, cue_cancel))
            except ai_tools.CancelledError:
                raise
            except Exception as exc:
                raise ai_tools.AIError(f"Không tạo được giọng ở câu {index + 1} "
                                       f"({row['start']:.2f}–{row['end']:.2f}s), giọng {voice}. {exc}") from exc
            ai_tools._run_ffmpeg(["-i", str(speech), "-vn", "-ar", str(sample_rate), "-ac", "1",
                                  "-c:a", "pcm_s16le", str(natural)], cue_cancel)
            with wave.open(str(natural), "rb") as reader:
                natural_frames = reader.getnframes()
                if not natural_frames:
                    raise ValueError("Giọng đọc trả về tệp âm thanh rỗng.")
            start = max(0, row["start"] + delay)
            end = max(start + .001, row["end"] + delay)
            if index + 1 < len(rows):
                end = max(start + .001, min(end, rows[index + 1]["start"] + delay))
            frames = max(1, round((end - start) * sample_rate))
            tempo = speed
            if fit == "fit" and natural_frames / tempo > frames:
                slack = min(round(.04 * sample_rate), max(1, round(frames * .05)))
                tempo = max(tempo, natural_frames / max(1, frames - slack))
            if fit=="fit" and voice_max_speed is not None and tempo>voice_max_speed:
                raise ValueError(f"Câu {index+1} cần tốc độ {tempo:.2f}x, vượt mức {voice_max_speed:.2f}x. Kéo dài thời gian, rút ngắn câu hoặc chọn Giọng đều.")
            # ai_tools' helper validates speeding-up factors only; the engine
            # builds legal pitch-preserving factors in both directions.
            filters = _engine_tempo(tempo).split(",") if tempo < 1 else ai_tools._tempo_filters(tempo)
            if fit == "fit":
                filters.extend(["apad", f"atrim=duration={frames / sample_rate:.9f}"])
            arguments = ["-i", str(natural), "-vn"]
            if filters:
                arguments += ["-af", ",".join(filters)]
            arguments += ["-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le", str(fitted)]
            ai_tools._run_ffmpeg(arguments, cue_cancel)
            with wave.open(str(fitted), "rb") as reader:
                length = reader.getnframes()
            speech.unlink(missing_ok=True)
            natural.unlink(missing_ok=True)
            return (fitted, round(start * sample_rate), length), tempo

        # Only one window of cues is in flight. Each worker receives its own
        # context, preserving the chosen local CPU/CUDA device in every cue.
        # The GPU runtime serializes model execution to avoid VRAM exhaustion;
        # phonemization, conversion and online engines may run concurrently.
        workers = min(voice_parallel, len(rows))
        ai_tools._progress(progress, 0, f"Tạo giọng với {workers} luồng song song…")
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vei-cue") as pool:
            pending = {}
            next_index = 0
            completed = 0

            def submit_next():
                nonlocal next_index
                ai_tools._check_cancel(cue_cancel)
                future = pool.submit(copy_context().run, render_cue, next_index)
                pending[future] = next_index
                next_index += 1

            try:
                for _ in range(workers):
                    submit_next()
                while pending:
                    ai_tools._check_cancel(cue_cancel)
                    ready, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                    for future in ready:
                        index = pending.pop(future)
                        track, tempo = future.result()
                        tracks[index] = track
                        maximum_speed = max(maximum_speed, tempo)
                        completed += 1
                        message = f"Tạo giọng {completed}/{len(rows)} · {workers} luồng…"
                        if fit == "fit" and tempo > 2:
                            message += f" Câu {index + 1} cần đọc nhanh {tempo:.1f} lần."
                        ai_tools._progress(progress, int(85 * completed / len(rows)), message)
                        if next_index < len(rows):
                            submit_next()
            except BaseException:
                stopped.set()
                for future in pending:
                    future.cancel()
                raise
        staged = folder / "narration.wav"
        total = max(start + length for _, start, length in tracks)
        if fit == "fit":
            total = max(total, round(max(0, max(row["end"] for row in rows) + delay) * sample_rate))
        readers = [wave.open(str(path), "rb") for path, _, _ in tracks]
        try:
            with wave.open(str(staged), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(sample_rate)
                position = 0
                while position < total:
                    ai_tools._check_cancel(cancel)
                    count = min(32768, total - position)
                    mixed = [0] * count
                    for reader, (_, start, length) in zip(readers, tracks):
                        first, end = max(position, start), min(position + count, start + length)
                        if first >= end:
                            continue
                        reader.setpos(first - start)
                        samples = array("h", reader.readframes(end - first))
                        for offset, value in enumerate(samples, first - position):
                            mixed[offset] += value
                    writer.writeframesraw(array("h", (max(-32768, min(32767, value)) for value in mixed)).tobytes())
                    position += count
                    ai_tools._progress(progress, 85 + int(14 * position / total), "Trộn giọng theo thời gian…")
        finally:
            for reader in readers:
                reader.close()
        result = ai_tools._publish(staged, target, cancel)
    finished = "Đã lưu giọng đọc theo tốc độ và độ trễ đã chọn."
    if fit == "fit" and maximum_speed > 2:
        finished += f" Có câu đọc nhanh {maximum_speed:.1f} lần; hãy kiểm tra và kéo dài thời gian hoặc rút ngắn câu nếu khó nghe."
    ai_tools._progress(progress, 100, finished)
    return result


def _engine_tempo(speed):
    from engine import _tempo
    return _tempo(speed)


def dub_subtitles(input_srt: str, output_audio: str, default_voice: str = "vi-VN-HoaiMyNeural",
                  progress=None, cancel: threading.Event | None = None) -> str:
    """Speak SRT/VTT using its optional per-cue voice metadata."""
    rows = load_subtitles(input_srt)
    target = ai_tools._output_target(output_audio)
    if target.suffix.lower() == ".wav":
        return dub_rows(rows, str(target), default_voice, progress, cancel)
    codecs = {
        ".mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
        ".m4a": ["-c:a", "aac", "-b:a", "128k"],
        ".aac": ["-c:a", "aac", "-b:a", "128k"],
        ".flac": ["-c:a", "flac"],
    }
    if target.suffix.lower() not in codecs:
        raise ValueError("Chọn file lồng tiếng WAV, MP3, M4A, AAC hoặc FLAC.")
    with tempfile.TemporaryDirectory(prefix=".mediafusion-voice-export-", dir=target.parent) as temporary:
        audio = Path(temporary) / "narration.wav"
        callback = lambda value, message: ai_tools._progress(progress, int(value * .9), message)
        dub_rows(rows, str(audio), default_voice, callback, cancel)
        converted = Path(temporary) / ("narration" + target.suffix.lower())
        ai_tools._run_ffmpeg(["-i", str(audio), "-vn", *codecs[target.suffix.lower()], str(converted)], cancel)
        result = ai_tools._publish(converted, target, cancel)
    ai_tools._progress(progress, 100, "Đã lưu bản lồng tiếng theo giọng từng dòng.")
    return result
