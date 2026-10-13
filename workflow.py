"""Sequential, resumable video workflows for VideoEditorIndependent Studio.

No model or network request starts at import. Every run has its own directory
and atomic manifest. Finished stages survive failure/cancellation and can be
resumed by passing ``config['resume_manifest']``. Source and settings changes
are rejected on resume so stale outputs cannot silently enter a new render.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Callable
import uuid

import ai_tools
import engine
import subtitle_tools

Progress = Callable[[int, str], None]
_OVERRIDES = {
    "recognize", "source_language", "target_language", "translate", "dub", "voice", "export",
    "recognition_method", "ocr_region", "ocr_language", "ocr_interval", "split_voice", "bilingual",
    "subtitle_path", "subtitle_rows", "subtitle_tracks", "separation_mode", "separation_device",
    "original_srt", "translated_srt", "voice_path", "background_path", "model",
    "recognition_model", "whisper_model", "provider", "translation_model", "scenario_prompt", "image_context",
    "recognition_device", "recognition_batch_size",
    "batch_size", "translate_workers", "voice_speed", "voice_delay", "voice_fit", "voice_parallel", "voice_max_speed", "pronunciation", "sub_lead", "tts_device",
}
_RESOURCES = ContextVar("mediafusion_workflow_resources", default=None)
_STAGE_LABELS = dict(subtitles="Tạo phụ đề gốc", translate="Dịch phụ đề", bilingual="Phụ đề song ngữ",
                     separate="Tách giọng và nhạc nền", dub="Lồng tiếng", export="Xuất video")


def _report(progress: Progress | None, percent: float, message: str) -> None:
    if progress:
        progress(max(0, min(100, int(percent))), message)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _fingerprint(path: str) -> dict:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy tệp nguồn: {source.name}")
    info = source.stat()
    return dict(path=str(source), size=info.st_size, mtime_ns=info.st_mtime_ns)


def _optional_fingerprint(path: str | None) -> dict | None:
    if not path:
        return None
    return _fingerprint(path)


def _snapshot(videos: list[str | dict], config: dict) -> list[dict]:
    inputs = []
    for index, item in enumerate(videos):
        video = {"path": item} if isinstance(item, (str, os.PathLike)) else copy.deepcopy(item)
        if not isinstance(video, dict) or not video.get("path"):
            raise ValueError(f"Video {index + 1} chưa có đường dẫn nguồn.")
        local = copy.deepcopy({key: value for key, value in config.items() if key != "resume_manifest"})
        local.update(video.get("config") or {})
        local.pop("resume_manifest", None)
        for key in _OVERRIDES:
            if key in video:
                local[key] = copy.deepcopy(video[key])
        local["settings"] = dict(config.get("settings") or {})
        local["settings"].update((video.get("config") or {}).get("settings") or {})
        local["settings"].update(video.get("settings") or {})
        local.update({key: value for key, value in (local["settings"].get("voice_pipeline") or {}).items()
                      if key in {"voice", "voice_speed", "voice_delay", "voice_fit", "voice_parallel", "voice_max_speed", "separation_mode"}})
        local.update({key:value for key,value in (local["settings"].get("recognition_pipeline") or {}).items() if key in ("recognition_device","recognition_batch_size")})
        path = str(Path(video["path"]).expanduser().resolve())
        clip = dict(path=path, start=video.get("start", 0), end=video.get("end", 0))
        error = ""
        try:
            fingerprint = _fingerprint(path)
        except (OSError, ValueError) as exc:
            fingerprint, error = None, str(exc)
        dependencies = {}
        # Include user-provided subtitles/music/logo in resume validation.
        for key in ("subtitle_path", "original_srt", "translated_srt", "music_path", "logo_path", "voice_path", "background_path"):
            candidate = local.get(key)
            candidate = candidate or local["settings"].get(key)
            if candidate:
                try:
                    dependencies[key] = _optional_fingerprint(candidate)
                    metadata = Path(str(candidate) + ".metadata.json")
                    if metadata.is_file():
                        dependencies[key + "_metadata"] = _fingerprint(str(metadata))
                except (OSError, ValueError) as exc:
                    dependencies[key] = dict(path=str(candidate), unavailable=True)
                    # A requested recognition stage generates its own captions.
                    if key not in {"subtitle_path", "original_srt", "translated_srt"} or not local.get("recognize"):
                        error = error or str(exc)
        from advanced_render import resolved_subtitle_tracks
        track_settings = copy.deepcopy(local["settings"])
        if "subtitle_tracks" in local:
            track_settings["advanced"] = dict(track_settings.get("advanced") or {})
            track_settings["advanced"]["subtitle_tracks"] = local["subtitle_tracks"]
        for track in resolved_subtitle_tracks(track_settings) or []:
            if track["enabled"] and track["path"]:
                key = "subtitle_track_" + track["role"]
                try:
                    dependencies[key] = _fingerprint(track["path"])
                except (OSError, ValueError) as exc:
                    dependencies[key] = dict(path=track["path"], unavailable=True)
                    regenerating = local.get("recognize") if track["role"] == "original" else local.get("translate")
                    if not regenerating:
                        error = error or str(exc)
        inputs.append(dict(source=path, clip=clip, config=local, fingerprint=fingerprint,
                           dependencies=dependencies, initial_error=error))
    return inputs


def _write_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now().astimezone().isoformat()
    descriptor, name = tempfile.mkstemp(prefix=".workflow-manifest-", suffix=".tmp", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            json.dump(manifest, destination, ensure_ascii=False, indent=2, allow_nan=False)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


@contextmanager
def _manifest_lock(folder: Path):
    """OS releases the advisory lock after a process crash; resume stays usable."""
    handle = (folder / ".workflow.lock").open("a+b")
    locked = False
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError("Quy trình này đang chạy ở một cửa sổ khác. Hãy chờ hoàn thành rồi tiếp tục.") from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _stages(config: dict) -> list[str]:
    stages = []
    if (config.get("recognize") or config.get("translate") or config.get("dub") or config.get("bilingual")
            or config.get("subtitle_rows") is not None or config.get("subtitle_path")
            or config.get("original_srt") or config.get("translated_srt")):
        stages.append("subtitles")
    if config.get("translate"):
        stages.append("translate")
    if config.get("bilingual"):
        stages.append("bilingual")
    if config.get("split_voice"):
        stages.append("separate")
    if config.get("dub"):
        stages.append("dub")
    if config.get("export", True):
        stages.append("export")
    if not stages:
        raise ValueError("Hãy bật ít nhất một bước trong quy trình.")
    return stages


def _available(entry: dict, stage: str) -> bool:
    if stage not in entry.get("completed_stages", []):
        return False
    stage_outputs = entry.get("stage_outputs", {}).get(stage, [])
    if not stage_outputs or not all(Path(path).is_file() and Path(path).stat().st_size > 0 for path in stage_outputs):
        return False
    fingerprints = entry.get("stage_fingerprints", {}).get(stage)
    if fingerprints:
        try:
            return all(_fingerprint(item["path"]) == item for item in fingerprints)
        except (OSError, ValueError):
            return False
    return True  # Legacy version-one manifests remain resumable.


def _new_output(folder: Path, name: str) -> Path:
    """Preserve any result left by a crash before its manifest was committed."""
    target = folder / name
    if os.path.lexists(target) or os.path.lexists(target.with_name(target.name + ".metadata.json")):
        target = target.with_name(target.stem + "_" + uuid.uuid4().hex[:8] + target.suffix)
    return target


def _copy_source_subtitles(config: dict, source: str, folder: Path, callback, cancel) -> str:
    output = _new_output(folder, "original.srt")
    ai_tools._check_cancel(cancel)
    if config.get("subtitle_rows") is not None:
        callback(0, "Đang lưu phụ đề đã chỉnh…")
        result = subtitle_tools.save_subtitles(config["subtitle_rows"], output)
        ai_tools._check_cancel(cancel)
        callback(100, "Đã lưu phụ đề gốc.")
        return result
    supplied = config.get("original_srt") or config.get("subtitle_path") or config.get("settings", {}).get("subtitle_path")
    if config.get("reuse_artifacts") and supplied:
        try:
            existing = subtitle_tools.load_subtitles(supplied)
            if existing:
                callback(100, "Dùng phụ đề gốc đã có.")
                return subtitle_tools.save_subtitles(existing, output)
        except (OSError, ValueError):
            if not config.get("recognize"):
                raise
    if config.get("recognize"):
        method = config.get("recognition_method", "speech")
        if method == "speech":
            model = config.get("recognition_model") or config.get("whisper_model")
            if not model and config.get("provider", "google").lower() in {"google", "google_translate"}:
                model = config.get("model")
            if model:
                import media_ai
                return media_ai.transcribe_model(source, str(output), config.get("source_language", "auto"),
                                                 model, callback, cancel,device=config.get("recognition_device","cpu"),batch_size=config.get("recognition_batch_size",1))
            return ai_tools.transcribe(source, str(output), config.get("source_language", "auto"), callback, cancel)
        if method == "ocr":
            import media_ai
            region = config.get("ocr_region")
            if isinstance(region, (list, tuple)):
                if len(region) != 4:
                    raise ValueError("Vùng OCR cần x, y, chiều rộng, chiều cao.")
                region = dict(zip(("x", "y", "width", "height"), region))
            return media_ai.extract_ocr(source, str(output), region,
                                        config.get("ocr_language", "ch"), config.get("ocr_interval", .5), callback, cancel)
        raise ValueError("Phương thức nhận dạng cần chọn speech hoặc ocr.")
    input = (config.get("original_srt") or config.get("subtitle_path")
             or config.get("settings", {}).get("subtitle_path") or config.get("translated_srt"))
    if not input and Path(source).with_suffix(".srt").is_file():
        input = str(Path(source).with_suffix(".srt"))
    if not input:
        raise ValueError("Hãy nhập phụ đề SRT/VTT hoặc bật nhận dạng giọng nói/OCR trước khi dịch hay lồng tiếng.")
    callback(0, "Đang nhập phụ đề…")
    result = subtitle_tools.save_subtitles(subtitle_tools.load_subtitles(input), output)
    ai_tools._check_cancel(cancel)
    callback(100, "Đã giữ bản phụ đề gốc.")
    return result


def _translate(original: str, config: dict, folder: Path, callback, cancel) -> str:
    language = re.sub(r"[^a-zA-Z0-9_-]", "_", str(config.get("target_language", "vi")))[:24] or "vi"
    target = _new_output(folder, f"translated.{language}.srt")
    rows = subtitle_tools.load_subtitles(original)
    if config.get("reuse_artifacts") and not config.get("force_translate") and config.get("translated_srt"):
        try:
            translated = subtitle_tools.load_subtitles(config["translated_srt"])
            if (len(translated) == len(rows) and translated and all(
                    abs(a["start"] - b["start"]) < .01 and abs(a["end"] - b["end"]) < .01
                    for a, b in zip(rows, translated))):
                callback(100, "Dùng bản dịch đã chỉnh.")
                return subtitle_tools.save_subtitles([{**a, **b, "original_text": a["text"]}
                                                      for a, b in zip(rows, translated)], target)
        except (OSError, ValueError):
            pass
    provider = str(config.get("provider", "google")).lower()
    if provider not in {"google", "google_translate"}:
        import providers
        translated = providers.translate_rows(rows, config, callback, cancel)
        if len(translated) != len(rows):
            raise ValueError("Bản dịch có số dòng khác bản gốc.")
        result = []
        for source_row, target_row in zip(rows, translated):
            text = target_row.get("text") if isinstance(target_row, dict) else str(target_row)
            if not str(text or "").strip():
                raise ValueError("Nhà cung cấp trả về một câu dịch rỗng.")
            result.append(dict(source_row, text=text, original_text=source_row["text"]))
        ai_tools._check_cancel(cancel)
        return subtitle_tools.save_subtitles(result, target)
    # ai_tools writes plain SRT. Enrich its result with original cue metadata
    # before publishing the final translated file in our owned run folder.
    with tempfile.TemporaryDirectory(prefix=".translation-", dir=folder) as temporary:
        staged = Path(temporary) / "translated.srt"
        ai_tools.translate_srt(original, str(staged), config.get("target_language", "vi"), callback, cancel)
        translated = ai_tools.load_srt(staged)
        if len(translated) != len(rows):
            raise ValueError("Bản dịch có số dòng khác bản gốc; chưa thể lồng tiếng an toàn.")
        result = [{**source, **target_row, "original_text": source["text"]} for source, target_row in zip(rows, translated)]
        ai_tools._check_cancel(cancel)
        return subtitle_tools.save_subtitles(result, target)


def _execute(stage: str, input: dict, entry: dict, folder: Path, callback, cancel) -> dict[str, str]:
    resources = _RESOURCES.get() or {}
    resource = resources.get(stage)
    if resource is None:
        return _execute_stage(stage, input, entry, folder, callback, cancel)
    while not resource.acquire(timeout=.1):
        ai_tools._check_cancel(cancel)
    try:
        ai_tools._check_cancel(cancel)
        return _execute_stage(stage, input, entry, folder, callback, cancel)
    finally:
        resource.release()


def _execute_stage(stage: str, input: dict, entry: dict, folder: Path, callback, cancel) -> dict[str, str]:
    config, source, paths = input["config"], input["source"], entry["paths"]
    if stage == "subtitles":
        path = _copy_source_subtitles(config, source, folder, callback, cancel)
        outputs = dict(original_subtitle=path, subtitle=path)
        if config.get("translated_srt") and not config.get("translate"):
            rows = subtitle_tools.load_subtitles(config["translated_srt"])
            originals = subtitle_tools.load_subtitles(path)
            # An external translated SRT may omit the original's voice sidecar.
            # Copy those assignments only for matching, aligned cues.
            if len(rows) == len(originals):
                for row, original in zip(rows, originals):
                    if (not row.get("voice") and original.get("voice")
                            and abs(row["start"] - original["start"]) < .01
                            and abs(row["end"] - original["end"]) < .01):
                        row["voice"] = original["voice"]
            translated = subtitle_tools.save_subtitles(rows, _new_output(folder, "translated.imported.srt"))
            outputs.update(translated_subtitle=translated, subtitle=translated)
        return outputs
    if stage == "translate":
        path = _translate(paths["original_subtitle"], config, folder, callback, cancel)
        return dict(translated_subtitle=path, subtitle=path)
    if stage == "bilingual":
        if not paths.get("translated_subtitle"):
            raise ValueError("Hãy bật bước dịch trước khi tạo phụ đề song ngữ.")
        rows = subtitle_tools.bilingual_rows(subtitle_tools.load_subtitles(paths["original_subtitle"]),
                                             subtitle_tools.load_subtitles(paths["translated_subtitle"]))
        ai_tools._check_cancel(cancel)
        path = subtitle_tools.save_subtitles(rows, _new_output(folder, "bilingual.srt"))
        callback(100, "Đã tạo phụ đề song ngữ.")
        return dict(bilingual_subtitle=path, subtitle=path)
    if stage == "separate":
        existing = (config.get("settings") or {}).get("source_stem_paths") or ((config.get("settings") or {}).get("advanced") or {}).get("source_stem_paths") or {}
        required = ("vocals", "drums", "bass", "other") if config.get("separation_mode") == "stems" else ("vocals", "background")
        if config.get("reuse_artifacts") and all(_valid_audio(existing.get(key)) for key in required):
            callback(100, "Dùng các lớp âm gốc đã tách.")
            return {key: existing[key] for key in required}
        if config.get("separation_mode") != "stems" and config.get("reuse_artifacts") and _valid_audio(config.get("background_path")):
            callback(100, "Dùng nhạc nền đã tách.")
            return dict(background=config["background_path"])
        import media_ai
        # A new stage folder preserves stems left by a interrupted earlier run.
        destination = folder / ("stems_" + uuid.uuid4().hex[:8])
        destination.mkdir()
        return {key: str(value) for key, value in media_ai.separate_audio(
            source, str(destination), config.get("separation_mode", "vocals"), callback, cancel,
            device=config.get("separation_device", "auto")).items()}
    if stage == "dub":
        timeline = _audio_timeline(input)
        metadata = Path(str(config.get("voice_path", "")) + ".timeline.json")
        saved_timeline = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else None
        if config.get("reuse_artifacts") and not config.get("force_dub") and _valid_audio(config.get("voice_path")) and (saved_timeline is None or saved_timeline == timeline):
            callback(100, "Dùng bản lồng tiếng đã có.")
            return dict(narration=config["voice_path"])
        # Speak translated dialogue only when rendering bilingual captions.
        subtitle = paths.get("translated_subtitle") or paths["original_subtitle"]
        rows = subtitle_tools.load_subtitles(subtitle)
        rows = _retime_rows(rows, timeline)
        options = {key: config[key] for key in ("voice_speed", "voice_delay", "voice_fit", "voice_parallel", "voice_max_speed", "pronunciation") if key in config}
        import local_tts
        with local_tts.device_scope(config.get("tts_device", "auto")):
            path = subtitle_tools.dub_rows(rows, str(_new_output(folder, "narration.wav")),
                                           config.get("voice", "vi-VN-HoaiMyNeural"), callback, cancel, **options)
        Path(path + ".timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
        return dict(narration=path)
    if stage == "export":
        from render_pipeline import render_project
        settings = copy.deepcopy(config.get("settings") or {})
        timeline = _audio_timeline(input)
        from advanced_render import resolved_subtitle_tracks
        if "subtitle_tracks" in config:
            settings.setdefault("advanced", {})["subtitle_tracks"] = copy.deepcopy(config["subtitle_tracks"])
        tracks = resolved_subtitle_tracks(settings)
        if tracks is not None:
            lead = float(config.get("sub_lead", 0))
            if not math.isfinite(lead) or abs(lead) > 60:
                raise ValueError("Phụ đề hiện sớm phải trong khoảng -60 đến 60 giây.")
            for track in tracks:
                role = track["role"]
                track["path"] = (paths.get(role + "_subtitle")
                                 or config.get("original_srt" if role == "original" else "translated_srt")
                                 or track["path"])
                if not track["enabled"] or not track["path"]:
                    continue
                if lead or timeline != {"speed": 1.0, "start": 0.0, "end": 0.0}:
                    rows = _retime_rows(subtitle_tools.load_subtitles(track["path"]), timeline)
                    shifted = [dict(row, start=max(0, row["start"] - lead), end=max(.001, row["end"] - lead))
                               for row in rows if row["end"] - lead > 0]
                    track["path"] = subtitle_tools.save_subtitles(shifted, _new_output(folder, "rendered-" + role + ".srt"))
            settings.setdefault("advanced", {})["subtitle_tracks"] = tracks
            # The explicit list exclusively controls captions, including [] or
            # both disabled. A legacy caption path must not burn a third copy.
            settings["subtitle_path"] = ""
            settings["advanced"]["subtitle_path"] = ""
        elif paths.get("subtitle"):
            settings["subtitle_path"] = paths["subtitle"]
            lead = float(config.get("sub_lead", 0))
            if not math.isfinite(lead) or abs(lead) > 60:
                raise ValueError("Phụ đề hiện sớm phải trong khoảng -60 đến 60 giây.")
            if lead or timeline != {"speed": 1.0, "start": 0.0, "end": 0.0}:
                rows = subtitle_tools.load_subtitles(paths["subtitle"])
                rows = _retime_rows(rows, timeline)
                # Only the rendered caption file shifts; narration keeps the
                # source cue times and pronunciation edits never change captions.
                shifted = [dict(row, start=max(0, row["start"] - lead), end=max(.001, row["end"] - lead))
                           for row in rows if row["end"] - lead > 0]
                settings["subtitle_path"] = subtitle_tools.save_subtitles(shifted, _new_output(folder, "rendered.srt"))
        narration = paths.get("narration") or (config.get("voice_path") if not config.get("dub") else None)
        background = paths.get("background") or config.get("background_path")
        stem_keys = ("vocals", "drums", "bass", "other") if config.get("separation_mode") == "stems" else ("vocals", "background")
        stems = {key: paths[key] for key in stem_keys if paths.get(key)}
        if stems and "vocals" in stems:
            settings.setdefault("advanced", {})["source_stem_paths"] = stems
            if "background" in stems:
                gains = settings["advanced"].get("source_stem_volumes") or settings.get("source_stem_volumes") or {}
                settings["advanced"]["source_stem_volumes"] = dict(gains, background=gains.get("other",settings.get("background_volume",1)))
        if narration:
            settings["voice_path"] = narration
            settings.setdefault("advanced", {})
            settings["advanced"]["voice_path"] = narration
            # Without separate stems, silence the original dialogue by default.
            if not background and not stems and "source_stem_volumes" not in {**settings, **(settings.get("advanced") or {})}:
                settings["mute"] = True
            voice_metadata = Path(narration + ".timeline.json")
            if voice_metadata.is_file():
                if json.loads(voice_metadata.read_text(encoding="utf-8")) != timeline:
                    raise ValueError("Tốc độ hoặc đoạn video đã đổi. Hãy chạy lại bước Lồng tiếng.")
                settings["advanced"].update(voice_audio_speed=1, voice_audio_start=0)
        if background:
            settings["background_path"] = background
        encoder = settings.get("encoder") or (settings.get("advanced") or {}).get("encoder") or {}
        extension = str(encoder.get("format", "mp4")).lower().lstrip(".")
        if extension not in {"mp4", "mkv", "mov", "webm"}:
            raise ValueError("Định dạng xuất cần chọn MP4, MKV, MOV hoặc WEBM.")
        result = render_project([copy.deepcopy(input["clip"])], settings,
                                str(_new_output(folder, "video." + extension)), callback, cancel)
        return dict(video=str(result))
    raise ValueError(f"Bước quy trình không hợp lệ: {stage}")


def _audio_timeline(input):
    settings = input["config"].get("settings") or {}
    speed = float(settings.get("speed", 1))
    start, end = float(input["clip"].get("start", 0)), float(input["clip"].get("end", 0))
    if not all(math.isfinite(value) for value in (speed, start, end)) or not .25 <= speed <= 4 or start < 0 or (end and end <= start):
        raise ValueError("Tốc độ hoặc đoạn video chưa hợp lệ.")
    return dict(speed=speed, start=start, end=end)


def _retime_rows(rows, timeline):
    result = []
    for row in rows:
        start = max(row["start"], timeline["start"])
        end = min(row["end"], timeline["end"]) if timeline["end"] else row["end"]
        if end > start:
            result.append(dict(row, start=(start-timeline["start"])/timeline["speed"],
                               end=(end-timeline["start"])/timeline["speed"]))
    return result


def run_workflow(videos: list[str | dict], config: dict, out_dir: str, progress: Progress | None = None,
                 cancel: threading.Event | None = None, *, _run_name: str | None = None) -> list[dict]:
    """Run enabled stages sequentially; return one result per input video.

    Video dictionaries accept ``path``, cut ``start/end``, per-video ``settings``
    and ``config`` overrides. Results contain ``source``, ``status`` (done,
    failed, cancelled, pending), ``paths``, ``error``, ``run_dir`` and ``manifest``.
    A failed file does not prevent independent files from running. Cancellation
    marks the active file cancelled and leaves following files pending. To
    resume, pass the same videos/config plus ``resume_manifest``; completed
    stages are skipped only when their files still exist.
    """
    if not isinstance(videos, list) or not videos:
        raise ValueError("Hãy thêm ít nhất một video vào hàng đợi.")
    if not isinstance(config, dict):
        raise ValueError("Cấu hình quy trình chưa hợp lệ.")
    cancel = cancel or threading.Event()
    inputs = _snapshot(videos, config)
    stages = [_stages(input["config"]) for input in inputs]
    digest = _digest(inputs)
    output = Path(out_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    resume = config.get("resume_manifest")
    if resume:
        manifest_path = Path(resume).expanduser().resolve()
        folder = manifest_path.parent
        if not manifest_path.is_file():
            raise FileNotFoundError("Không tìm thấy manifest để tiếp tục quy trình.")
    else:
        if _run_name and (Path(_run_name).name != _run_name or len(_run_name) > 32):
            raise ValueError("Tên thư mục chạy nội bộ chưa hợp lệ.")
        folder = output / (_run_name or ("VideoEditorIndependent_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]))
        folder.mkdir()
        manifest_path = folder / "manifest.json"
    with _manifest_lock(folder):
        if resume:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (manifest.get("version") != 1 or manifest.get("inputs_digest") != digest
                    or len(manifest.get("results", [])) != len(inputs)):
                raise ValueError("Video hoặc cấu hình đã thay đổi. Hãy tạo lần chạy mới để tránh dùng kết quả cũ.")
            if Path(manifest.get("run_dir", "")).resolve() != folder:
                raise ValueError("Thư mục của manifest đã thay đổi; hãy tạo lần chạy mới.")
        else:
            manifest = dict(version=1, created_at=datetime.now().astimezone().isoformat(), run_dir=str(folder),
                            inputs_digest=digest, inputs=inputs, status="running", results=[])
            for index, input in enumerate(inputs):
                safe_name = re.sub(r"[^\w.-]", "_", Path(input["source"]).stem, flags=re.UNICODE)[:70] or "video"
                video_folder = folder / f"{index + 1:03d}_{safe_name}_{_digest(input['source'])[:6]}"
                video_folder.mkdir()
                paths = {}
                if input["config"].get("voice_path") and not input["config"].get("dub"):
                    paths["narration"] = input["config"]["voice_path"]
                if input["config"].get("background_path") and not input["config"].get("split_voice"):
                    paths["background"] = input["config"]["background_path"]
                manifest["results"].append(dict(source=input["source"], status="pending", paths=paths, error="",
                                                folder=str(video_folder), run_dir=str(folder), manifest=str(manifest_path),
                                                completed_stages=[], stage_outputs={}))
        manifest["status"] = "running"
        _write_manifest(manifest_path, manifest)
        total_stages = sum(len(plan) for plan in stages)
        completed_units = 0
        for index, (input, plan, entry) in enumerate(zip(inputs, stages, manifest["results"])):
            if cancel.is_set():
                break
            entry.update(status="running", error="")
            if input["initial_error"]:
                entry.update(status="failed", error=input["initial_error"])
                completed_units += len(plan)
                _write_manifest(manifest_path, manifest)
                _report(progress, 100 * completed_units / total_stages,
                        f"Video {index + 1}/{len(inputs)} · Lỗi: {entry['error']}")
                continue
            destination = Path(entry["folder"]).resolve()
            if folder != destination and folder not in destination.parents:
                raise ValueError("Manifest chứa thư mục kết quả nằm ngoài lần chạy này.")
            destination.mkdir(parents=True, exist_ok=True)
            unit_at_start = completed_units
            try:
                for stage_number, stage in enumerate(plan):
                    ai_tools._check_cancel(cancel)
                    callback = lambda percent, message, base=completed_units, step=stage: _report(
                        progress, 100 * (base + max(0, min(100, percent)) / 100) / total_stages,
                        f"Video {index + 1}/{len(inputs)} · {_STAGE_LABELS[step]} · {message}")
                    if _available(entry, stage):
                        callback(100, "Dùng kết quả đã hoàn thành.")
                    else:
                        # If an earlier output vanished, subsequent cached
                        # stages depend on it and must be regenerated too.
                        downstream = set(plan[stage_number:])
                        entry["completed_stages"] = [s for s in entry.get("completed_stages", []) if s not in downstream]
                        for old_stage in downstream:
                            entry.get("stage_outputs", {}).pop(old_stage, None)
                            entry.get("stage_fingerprints", {}).pop(old_stage, None)
                        entry["active_stage"] = stage
                        _write_manifest(manifest_path, manifest)
                        callback(0, "Bắt đầu…")
                        outputs = _execute(stage, input, entry, destination, callback, cancel)
                        ai_tools._check_cancel(cancel)
                        if not outputs or any(not Path(path).is_file() for path in outputs.values()):
                            raise RuntimeError("Bước xử lý chưa tạo đủ tệp đầu ra.")
                        entry["paths"].update(outputs)
                        entry["stage_outputs"][stage] = list(dict.fromkeys(outputs.values()))
                        tracked = list(entry["stage_outputs"][stage])
                        tracked += [path + ".metadata.json" for path in tracked if Path(path + ".metadata.json").is_file()]
                        entry.setdefault("stage_fingerprints", {})[stage] = [_fingerprint(path) for path in tracked]
                        entry["completed_stages"].append(stage)
                        _write_manifest(manifest_path, manifest)
                        callback(100, "Hoàn thành.")
                    completed_units += 1
                entry.update(status="done", error="", active_stage="")
            except (ai_tools.CancelledError, engine.Cancelled) as exc:
                entry.update(status="cancelled", error=str(exc))
            except Exception as exc:
                if cancel.is_set():
                    entry.update(status="cancelled", error="Đã dừng theo yêu cầu.")
                else:
                    entry.update(status="failed", error=str(exc))
                    completed_units = unit_at_start + len(plan)
                    _report(progress, 100 * completed_units / total_stages,
                            f"Video {index + 1}/{len(inputs)} · Lỗi: {exc}")
            _write_manifest(manifest_path, manifest)
            if cancel.is_set() or entry["status"] == "cancelled":
                break
        results = manifest["results"]
        manifest["status"] = ("cancelled" if cancel.is_set() or any(row["status"] == "cancelled" for row in results)
                              else "finished_with_errors" if any(row["status"] == "failed" for row in results)
                              else "done")
        _write_manifest(manifest_path, manifest)
        if manifest["status"] != "cancelled":
            done = sum(row["status"] == "done" for row in results)
            failed = sum(row["status"] == "failed" for row in results)
            _report(progress, 100, f"Quy trình hoàn tất: {done} video thành công, {failed} lỗi.")
        else:
            _report(progress, 100 * completed_units / max(total_stages, 1), "Đã dừng. Các bước hoàn thành được giữ để tiếp tục.")
        return copy.deepcopy(results)


def resume_workflow(manifest_path: str, progress: Progress | None = None,
                    cancel: threading.Event | None = None) -> list[dict]:
    """Resume a saved run from its normalized inputs without GUI reconstruction.

    The manifest stores each video's complete effective configuration. Passing
    it back as a per-video override recreates the exact input digest, including
    trims, subtitle paths and settings. run_workflow still validates current
    source/dependency fingerprints before it reuses any finished output.
    """
    path = Path(manifest_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("inputs"), list) or not payload["inputs"]:
        raise ValueError("Manifest chưa có cấu hình đầu vào hợp lệ để tiếp tục.")
    videos = []
    for item in payload["inputs"]:
        if not isinstance(item, dict) or not item.get("source") or not isinstance(item.get("config"), dict):
            raise ValueError("Manifest thiếu video nguồn hoặc cấu hình riêng của video.")
        clip = item.get("clip") or {}
        videos.append(dict(path=item["source"], start=clip.get("start", 0), end=clip.get("end", 0),
                           config=copy.deepcopy(item["config"])))
    return run_workflow(videos, dict(resume_manifest=str(path)), str(path.parent.parent), progress, cancel)


def dub_subtitles(input_srt: str, output_audio: str, default_voice: str = "vi-VN-HoaiMyNeural",
                  progress: Progress | None = None, cancel: threading.Event | None = None) -> str:
    """Public GUI convenience wrapper for per-cue subtitle voices."""
    return subtitle_tools.dub_subtitles(input_srt, output_audio, default_voice, progress, cancel)


def _valid_audio(path: str | None) -> bool:
    if not path:
        return False
    try:
        info = engine.probe(path)
        return info["has_audio"] and info["duration"] > 0
    except (OSError, ValueError, RuntimeError):
        return False


def _worker_count(config: dict, key: str, default: int, maximum: int = 8) -> int:
    value = int(config.get(key, default))
    if not 1 <= value <= maximum:
        raise ValueError(f"{key} phải từ 1 đến {maximum}.")
    return value


def _credential_free(value):
    """Credential identifiers are allowed; secret material stays in ProvidersStore."""
    if isinstance(value, dict):
        return {key: _credential_free(item) for key, item in value.items()
                if not any(secret in str(key).lower() for secret in ("api_key", "secret", "password", "authorization"))
                and str(key).lower() not in {"keys", "token", "access_token", "credential", "credentials"}}
    if isinstance(value, list):
        return [_credential_free(item) for item in value]
    return copy.deepcopy(value)


def _export_inputs(videos: list[str | dict], config: dict) -> tuple[list[dict], dict]:
    settings = _credential_free(config)
    for key, value in dict(recognize=True, translate=True, dub=True, export=True,
                           join_enabled=True, reuse_artifacts=True).items():
        settings.setdefault(key, value)
    settings["export"] = True
    # Settings are explicit rather than mutating MEDIAFUSION_WHISPER_MODEL.
    settings.setdefault("recognition_model", settings.get("whisper_model", "base"))
    settings.pop("resume_manifest", None)
    clean = _credential_free(videos)
    snapshots = _snapshot(clean, settings)
    return snapshots, settings


def export_all(videos: list[str | dict], config: dict, out_dir: str,
               progress: Progress | None = None, cancel: threading.Event | None = None) -> dict:
    """One click: enabled prerequisites → individual exports → ordered join.

    Missing toggles default to recognition, translation, dubbing and joining.
    Valid supplied subtitles/narration are reused; explicit False disables that
    prerequisite. A joined result is published only if every clip succeeds.
    Outputs and stage manifests survive cancellation/errors, and the returned
    master ``manifest`` accepts ``config['resume_manifest']`` for a later retry.
    General workers schedule files; TTS/render permits bound those expensive
    stages independently. Recognition/separation use one shared permit by
    default, with an explicit expert maximum of two model workers.
    """
    if not isinstance(videos, list) or not videos:
        raise ValueError("Hãy thêm ít nhất một video trước khi xuất.")
    if not isinstance(config, dict):
        raise ValueError("Cấu hình xuất chưa hợp lệ.")
    cancel = cancel or threading.Event()
    inputs, normalized = _export_inputs(videos, config)
    workers = _worker_count(normalized, "general_workers", 2)
    tts_workers = _worker_count(normalized, "tts_workers", 2)
    render_workers = _worker_count(normalized, "render_workers", 2)
    model_workers = _worker_count(normalized, "recognition_workers", 1, 2)
    digest = _digest(inputs)
    output = Path(out_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    resume = config.get("resume_manifest")
    if resume:
        manifest_path = Path(resume).expanduser().resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError("Không tìm thấy manifest xuất để tiếp tục.")
        folder = manifest_path.parent
    else:
        folder = output / ("MF_Export_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
        folder.mkdir()
        manifest_path = folder / "export-manifest.json"
    model_permit = threading.BoundedSemaphore(model_workers)
    resources = dict(subtitles=model_permit, separate=model_permit,
                     dub=threading.BoundedSemaphore(tts_workers), export=threading.BoundedSemaphore(render_workers))
    mutex = threading.RLock()
    percentages = [0.0] * len(inputs)
    last_percent = [0]
    join_enabled = bool(normalized.get("join_enabled")) and len(inputs) > 1
    share = 90 if join_enabled else 100

    def report_file(index, percent, message):
        with mutex:
            percentages[index] = max(percentages[index], max(0, min(100, float(percent))))
            total = int(share * sum(percentages) / (100 * len(inputs)))
            last_percent[0] = max(last_percent[0], total)
            _report(progress, last_percent[0], f"Video {index + 1}/{len(inputs)} · {message}")

    with _manifest_lock(folder):
        if resume:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (manifest.get("kind") != "oneclick_export" or manifest.get("version") != 1
                    or manifest.get("inputs_digest") != digest or len(manifest.get("results", [])) != len(inputs)):
                raise ValueError("Video hoặc thiết lập xuất đã thay đổi. Hãy xuất thành lần chạy mới.")
            if Path(manifest.get("run_dir", "")).resolve() != folder:
                raise ValueError("Thư mục manifest đã thay đổi.")
        else:
            manifest = dict(version=1, kind="oneclick_export", inputs_digest=digest, inputs=inputs,
                            created_at=datetime.now().astimezone().isoformat(), run_dir=str(folder),
                            status="running", joined_video="", join_error="", results=[])
            for index, item in enumerate(inputs):
                manifest["results"].append(dict(source=item["source"], status="pending", paths={}, error="",
                                                manifest="", folder=str(folder / f"clip_{index + 1:03d}")))
        manifest.update(status="running", join_error="")
        _write_manifest(manifest_path, manifest)

        def run_file(index):
            if cancel.is_set():
                return
            item, previous = inputs[index], manifest["results"][index]
            destination = Path(previous.get("folder") or (folder / f"clip_{index + 1:03d}")).resolve()
            if folder not in destination.parents:
                raise ValueError("Thư mục video trong manifest nằm ngoài lần chạy.")
            clip = dict(item["clip"], config=copy.deepcopy(item["config"]))
            local = {}
            if previous.get("manifest"):
                local["resume_manifest"] = previous["manifest"]
            token = _RESOURCES.set(resources)
            try:
                result = run_workflow([clip], local, str(destination),
                                      lambda p, m: report_file(index, p, m), cancel, _run_name="run")[0]
            except (ai_tools.CancelledError, engine.Cancelled) as exc:
                result = dict(previous, status="cancelled", error=str(exc))
            except Exception as exc:
                result = dict(previous, status="cancelled" if cancel.is_set() else "failed", error=str(exc))
            finally:
                _RESOURCES.reset(token)
            with mutex:
                # Keep the queue container stable; per-video result folder is
                # separately tracked by its stage manifest.
                result["queue_folder"] = str(destination)
                manifest["results"][index] = result
                _write_manifest(manifest_path, manifest)
            if result["status"] != "cancelled":
                report_file(index, 100, "Hoàn thành." if result["status"] == "done" else "Lỗi: " + result["error"])

        # Preserve queue folders when resuming; run_workflow's folder is one
        # level deeper and must not become the destination on every retry.
        for result in manifest["results"]:
            if result.get("queue_folder"):
                result["folder"] = result["queue_folder"]
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mediafusion-export") as executor:
            futures = [executor.submit(run_file, index) for index in range(len(inputs))]
            for future in as_completed(futures):
                future.result()
        results = manifest["results"]
        errors = [dict(source=row["source"], stage=row.get("active_stage", ""), error=row.get("error", ""))
                  for row in results if row["status"] in {"failed", "cancelled"}]
        done = sum(row["status"] == "done" for row in results)
        cancelled = cancel.is_set() or any(row["status"] == "cancelled" for row in results)
        status = "cancelled" if cancelled else "done" if done == len(results) else "partial" if done else "failed"
        joined = ""
        if status == "done" and join_enabled:
            current = manifest.get("joined_video")
            # If any render changed on resume, stale joined media must not be
            # mistaken for the current pipeline. Fingerprint every final clip.
            join_digest = _digest([_fingerprint(row["paths"]["video"]) for row in results])
            if current and _valid_joined(current) and manifest.get("join_inputs_digest") == join_digest:
                joined = current
            else:
                try:
                    ai_tools._check_cancel(cancel)
                    requested = str(normalized.get("join_filename") or "video_ghep.mp4")
                    basename = Path(requested).name
                    if Path(basename).suffix.lower() != ".mp4":
                        basename = Path(basename).stem + ".mp4"
                    target = _new_output(folder, basename)
                    settings = dict(normalized.get("join_settings") or {})
                    settings.setdefault("fps", (normalized.get("settings") or {}).get("fps", 30))
                    joined = engine.media_process([dict(path=row["paths"]["video"]) for row in results],
                                                  settings, str(target),
                                                  lambda p, m: _report(progress, share + p * (100 - share) / 100,
                                                                       "Ghép video · " + m), cancel)
                    manifest.update(joined_video=joined, join_inputs_digest=join_digest)
                except (ai_tools.CancelledError, engine.Cancelled) as exc:
                    status = "cancelled"
                    manifest["join_error"] = str(exc)
                    errors.append(dict(source="", stage="join", error=str(exc)))
                except Exception as exc:
                    status = "cancelled" if cancel.is_set() else "partial"
                    manifest["join_error"] = str(exc)
                    errors.append(dict(source="", stage="join", error=str(exc)))
        elif status == "done" and len(results) == 1:
            joined = ""  # Individual output is available through results[0].
        manifest["status"] = status
        _write_manifest(manifest_path, manifest)
        if status == "done":
            _report(progress, 100, f"Đã xuất {done} video" + (" và ghép theo thứ tự." if joined else "."))
        else:
            _report(progress, min(last_percent[0], 99), f"Đã giữ {done}/{len(results)} video thành công; " +
                    ("quy trình đã dừng." if status == "cancelled" else "có lỗi cần xử lý trước khi ghép."))
        return dict(results=copy.deepcopy(results), joined_video=joined, manifest=str(manifest_path),
                    status=status, errors=errors)


def _valid_joined(path: str) -> bool:
    try:
        info = engine.probe(path)
        return info["duration"] > 0 and info["width"] > 0 and info["height"] > 0
    except (OSError, RuntimeError, ValueError):
        return False


def resume_export(manifest_path: str, progress: Progress | None = None,
                  cancel: threading.Event | None = None) -> dict:
    """Reconstruct and resume a one-click export, including its final join."""
    path = Path(manifest_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("kind") != "oneclick_export" or not payload.get("inputs"):
        raise ValueError("Đây chưa phải manifest của quy trình xuất một lần.")
    videos = [dict(item["clip"], config=copy.deepcopy(item["config"])) for item in payload["inputs"]]
    config = dict(payload["inputs"][0]["config"], resume_manifest=str(path))
    return export_all(videos, config, str(path.parent.parent), progress, cancel)
