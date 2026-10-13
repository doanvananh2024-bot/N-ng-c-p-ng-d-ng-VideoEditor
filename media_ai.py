"""Local OCR, Whisper model selection and neural stem separation.

Imports never download models. OCR Chinese/English models are bundled in the
RapidOCR wheel; Vietnamese uses EasyOCR's Latin recognizer on first use. Demucs
downloads its official htdemucs weights on first use. Video/audio stay local.
Call these functions in a background thread. Frozen apps need freeze_support().
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import importlib.util
import math
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unicodedata
import uuid
import sys

import ai_tools
import engine
import gpu_runtime

Progress = ai_tools.Progress
AIError = ai_tools.AIError
CancelledError = ai_tools.CancelledError

WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")
OCR_LANGUAGES = ("ch", "en", "vi")


def _cache_folder(kind: str) -> Path:
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VideoEditorIndependent" / "models" / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _require(module: str, label: str) -> None:
    # Heavy native imports belong in the cancellable worker, not the GUI thread.
    if importlib.util.find_spec(module) is None:
        raise AIError(f"Chưa cài {label}. Chạy Install Advanced AI.cmd rồi mở lại ứng dụng.")


def _run_worker(target, args: tuple, progress: Progress | None, cancel: threading.Event | None):
    ai_tools._check_cancel(cancel)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=target, args=(sender, *args), daemon=True)
    try:
        worker.start()
        sender.close()
        while True:
            ai_tools._check_cancel(cancel)
            if receiver.poll(.1):
                try:
                    message = receiver.recv()
                except EOFError as exc:
                    raise AIError("Tiến trình AI đã đóng trước khi trả kết quả.") from exc
                if message[0] == "progress":
                    ai_tools._progress(progress, message[1], message[2])
                elif message[0] == "result":
                    ai_tools._check_cancel(cancel)
                    return message[1]
                elif message[0] == "error":
                    raise AIError(message[1])
                else:
                    raise AIError("Tiến trình AI trả thông tin không hợp lệ.")
            elif not worker.is_alive():
                raise AIError("Tiến trình AI bị gián đoạn. Kiểm tra dung lượng RAM và các thành phần AI.")
    finally:
        sender.close()
        receiver.close()
        ai_tools._stop_worker(worker)


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if not ch.isspace() and not unicodedata.category(ch).startswith("P"))


def _same_text(a: str, b: str) -> bool:
    a, b = _normalized(a), _normalized(b)
    if a == b:
        return True
    # Short text must match exactly so adjacent short dialogue is not swallowed.
    if min(len(a), len(b)) < 6 or abs(len(a) - len(b)) > max(2, .12 * max(len(a), len(b))):
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= .9


def _group_samples(samples: list[dict], duration: float, interval: float) -> list[dict]:
    """Merge repeated text; place boundaries halfway between adjacent samples.

    Empty OCR frames close the current cue. Most common spelling wins when
    transient OCR errors vary across frames. Timing accuracy is ±interval/2.
    """
    rows: list[dict] = []
    active: dict | None = None
    previous_t = 0.0
    for sample in samples:
        t = float(sample["time"])
        text = str(sample.get("text", "")).strip()
        boundary = max(0.0, (previous_t + t) / 2) if active is not None else max(0.0, t - interval / 2)
        if active is not None and text and _same_text(active["reference"], text):
            active["versions"][text] += 1
            active["last"] = t
        else:
            if active is not None:
                best = max(active["versions"], key=lambda item: (active["versions"][item], len(item)))
                end = min(duration, max(boundary, active["start"] + .001))
                rows.append({"start": active["start"], "end": end, "text": best})
                active = None
            if text:
                # A changed cue starts at the exact boundary where its predecessor ends.
                start = rows[-1]["end"] if rows and rows[-1]["end"] >= t - interval else max(0, t - interval / 2)
                active = {"start": start, "last": t, "reference": text, "versions": Counter({text: 1})}
        previous_t = t
    if active is not None:
        best = max(active["versions"], key=lambda item: (active["versions"][item], len(item)))
        rows.append({"start": active["start"], "end": min(duration, active["last"] + interval / 2), "text": best})
    return [row for row in rows if row["end"] - row["start"] >= .001]


def _ocr_text(result) -> str:
    if not result:
        return ""
    pieces = []
    for item in result:
        if len(item) < 3 or float(item[2]) < .55:
            continue
        text = unicodedata.normalize("NFC", str(item[1])).strip()
        if not text or not any(ch.isalnum() for ch in text):
            continue
        box = item[0]
        left = min(float(point[0]) for point in box)
        top = min(float(point[1]) for point in box)
        height = max(float(point[1]) for point in box) - top
        pieces.append((top, left, height, text))
    pieces.sort(key=lambda item: (item[0], item[1]))
    lines = []
    line_top = 0.0
    line_height = 0.0
    for top, left, height, text in pieces:
        if not lines or abs(top - line_top) > max(8, .55 * max(height, line_height)):
            lines.append([(left, text)])
            line_top, line_height = top, height
        else:
            lines[-1].append((left, text))
    return "\n".join(" ".join(text for _, text in sorted(line)) for line in lines)


def _load_runtime():
    """Load the MS OpenMP DLL included in this application's runtime."""
    if os.name != "nt":
        return None
    import ctypes
    for candidate in (Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "vcomp140.dll",):
        if candidate.is_file():
            return ctypes.WinDLL(str(candidate))
    return None


def _ocr_worker(connection, source: str, region: tuple, language: str, interval: float, duration: float) -> None:
    capture = None
    try:
        cuda_runtime = gpu_runtime.configure_cuda_runtime()
        import cv2
        import onnxruntime as ort
        from rapidocr_onnxruntime import RapidOCR
        use_cuda = cuda_runtime["dlls_ready"] and "CUDAExecutionProvider" in ort.get_available_providers()
        options = {"intra_op_num_threads": max(1, min(4, os.cpu_count() or 1)), "inter_op_num_threads": 1,
                   "use_cls": False, "det_limit_side_len": 480, "det_limit_type": "max", "text_score": .55,
                   "det_use_cuda": use_cuda, "rec_use_cuda": use_cuda}
        connection.send(("progress", 3, "Đang nạp mô hình OCR trên máy…"))
        try:
            model = RapidOCR(**options)
        except Exception:
            if not use_cuda:
                raise
            connection.send(("progress", 3, "OCR không khởi tạo được GPU; chuyển sang CPU."))
            options.update(det_use_cuda=False, rec_use_cuda=False)
            model = RapidOCR(**options)
        providers = (model.text_det.infer.session.get_providers(), model.text_rec.session.session.get_providers())
        actual_cuda = all("CUDAExecutionProvider" in available for available in providers)
        connection.send(("progress", 4, "OCR dùng GPU NVIDIA." if actual_cuda else "OCR dùng CPU; CUDA chưa khả dụng cho mô hình."))
        vietnamese = None
        if language == "vi":
            runtime = _load_runtime()
            import torch
            import easyocr
            torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
            connection.send(("progress", 4, "Nạp OCR tiếng Việt; lần đầu tải mô hình chữ Latin (~15 MB), video xử lý trên máy…"))
            vietnamese = easyocr.Reader(["vi", "en"], gpu=False, detector=False, recognizer=True,
                                       model_storage_directory=str(_cache_folder("easyocr")),
                                       user_network_directory=str(_cache_folder("easyocr-user")), verbose=False)
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            raise AIError("Không mở được video để đọc phụ đề OCR.")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise AIError("Video không có tốc độ khung hình hợp lệ.")
        x, y, width, height = region
        count = max(1, int(math.ceil(duration / interval)))
        samples = []
        for number in range(count):
            timestamp = min(duration - min(.001, duration / 2), (number + .5) * interval)
            # Seek by timestamp instead of decoding all frames for long footage.
            # OpenCV's FFmpeg backend seeks to the preceding keyframe and decodes
            # to the requested presentation timestamp.
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                raise AIError(f"Không đọc được khung hình video ở {timestamp:.2f} giây.")
            if x + width > frame.shape[1] or y + height > frame.shape[0]:
                raise AIError("Kích thước video đọc được khác vùng OCR. Kiểm tra xoay video và chọn lại vùng.")
            crop = frame[y:y + height, x:x + width]
            if crop.shape[0] < 80:
                crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            if vietnamese is None:
                result, _ = model(crop, use_cls=False)
            else:
                boxes, _ = model(crop, use_cls=False, use_rec=False)
                if boxes:
                    horizontal = []
                    for box in boxes:
                        left = max(0, int(min(point[0] for point in box)) - 5)
                        right = min(crop.shape[1], int(max(point[0] for point in box)) + 6)
                        top = max(0, int(min(point[1] for point in box)) - 5)
                        bottom = min(crop.shape[0], int(max(point[1] for point in box)) + 6)
                        horizontal.append([left, right, top, bottom])
                    result = vietnamese.recognize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
                                                   horizontal_list=horizontal, free_list=[],
                                                   decoder="beamsearch", beamWidth=5, batch_size=1,
                                                   workers=0, detail=1, paragraph=False)
                else:
                    result = None
            samples.append({"time": timestamp, "text": _ocr_text(result)})
            connection.send(("progress", 5 + int(90 * (number + 1) / count),
                             f"OCR khung hình {number + 1}/{count} · {timestamp:.1f}/{duration:.1f} giây"))
        rows = _group_samples(samples, duration, interval)
        if not rows:
            raise AIError("Không tìm thấy chữ phụ đề trong vùng đã chọn. Chọn đúng vùng và ngôn ngữ, hoặc giảm khoảng lấy mẫu.")
        connection.send(("result", rows))
    except Exception as exc:
        connection.send(("error", f"Không trích xuất được OCR: {exc}"))
    finally:
        if capture is not None:
            capture.release()
        connection.close()


def extract_ocr(video: str, output_srt: str, region: dict | None = None, lang: str = "ch",
                sample_interval: float = .5, progress: Progress | None = None,
                cancel: threading.Event | None = None) -> str:
    """OCR burned-in subtitles into SRT locally; default ROI is bottom third.

    region is pixel coordinates {x,y,width,height} of the display-oriented video.
    Short cues can fall between samples; use .1–.25 s for rapidly changing text.
    All output paths must be new files; source/output collisions are rejected.
    """
    ai_tools._check_cancel(cancel)
    info = engine.probe(video)
    ai_tools._output_target(output_srt, info["path"])
    if info["width"] < 2 or info["height"] < 2 or info["duration"] <= 0:
        raise AIError("Hãy chọn video có hình ảnh và thời lượng hợp lệ để OCR.")
    if lang not in OCR_LANGUAGES:
        raise ValueError("Ngôn ngữ OCR phải là ch (Trung), en (Anh) hoặc vi (Việt).")
    interval = float(sample_interval)
    if not math.isfinite(interval) or not .1 <= interval <= 5:
        raise ValueError("Khoảng lấy mẫu OCR phải từ 0,1 đến 5 giây.")
    if region is None:
        top = int(info["height"] * .67)
        coordinates = (0, top, info["width"], info["height"] - top)
    else:
        try:
            values = [float(region[key]) for key in ("x", "y", "width", "height")]
            if any(not math.isfinite(value) or value != int(value) for value in values):
                raise ValueError
            coordinates = tuple(int(value) for value in values)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Vùng OCR cần x, y, width, height là số pixel nguyên.") from exc
    x, y, width, height = coordinates
    if x < 0 or y < 0 or width < 2 or height < 2 or x + width > info["width"] or y + height > info["height"]:
        raise ValueError(f"Vùng OCR phải nằm trong khung {info['width']} × {info['height']} pixel.")
    _require("rapidocr_onnxruntime", "RapidOCR")
    if lang == "vi":
        _require("easyocr", "EasyOCR tiếng Việt")
    rows = _run_worker(_ocr_worker, (info["path"], coordinates, lang, interval, info["duration"]), progress, cancel)
    result = ai_tools._save_srt(rows, output_srt, cancel)
    ai_tools._progress(progress, 100, f"Đã tạo {len(rows)} dòng phụ đề OCR.")
    return result


def transcribe_model(video: str, output_srt: str, language: str = "auto", model: str = "base",
                     progress: Progress | None = None, cancel: threading.Event | None = None,
                     device: str = "cpu", batch_size: int = 1) -> str:
    """Whisper transcription with explicit model choice, no global env mutation."""
    ai_tools._check_cancel(cancel)
    source = Path(video).expanduser().resolve()
    if not source.is_file():
        raise AIError("Không tìm thấy video hoặc âm thanh nguồn.")
    ai_tools._output_target(output_srt, source)
    if model not in WHISPER_MODELS:
        raise ValueError("Mô hình Whisper không hợp lệ.")
    if device not in ("auto","cpu","cuda") or type(batch_size) is not int or not 1<=batch_size<=12:
        raise ValueError("Thiết bị Whisper hoặc số đoạn song song chưa hợp lệ.")
    shared=Path(os.environ.get("VEI_WHISPER_ROOT",str(Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"VideoEditor/stt/faster-whisper")))/model
    model_source=str(shared) if all((shared/name).is_file() for name in ("model.bin","config.json","tokenizer.json")) else model
    _require("faster_whisper", "faster-whisper")
    rows = _run_worker(ai_tools._transcription_worker,
                       (str(source), model_source, str(_cache_folder("whisper")), ai_tools._language(language, whisper=True),device,batch_size),
                       progress, cancel)
    result = ai_tools._save_srt(rows, output_srt, cancel)
    ai_tools._progress(progress, 100, f"Đã tạo {len(rows)} dòng phụ đề bằng Whisper {model}.")
    return result


def _separation_worker(connection, source_wav: str, folder: str, mode: str, cache: str) -> None:
    outputs = []
    silent_streams = []
    try:
        # Windowed PyInstaller apps expose sys.stderr/stdout as None. Torch Hub
        # writes download status directly to stderr; give it a real safe sink.
        for stream_name in ("stdout", "stderr"):
            if getattr(sys, stream_name) is None:
                sink = open(os.devnull, "w", encoding="utf-8")
                silent_streams.append(sink)
                setattr(sys, stream_name, sink)
        # TorchAudio's Windows CPU wheel requires Microsoft's OpenMP runtime.
        # PyInstaller packages this DLL beside its Python modules; source runs
        # can use the same runtime already supplied by the installed editor.
        runtime = _load_runtime()
        import numpy as np
        import soundfile as sf
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        torch.hub.set_dir(cache)
        checkpoint = Path(cache) / "checkpoints" / gpu_runtime.CHECKPOINT
        if checkpoint.is_file() and checkpoint.stat().st_size > 0:
            connection.send(("progress", 12, "Nạp mô hình Demucs đã cài trên CPU…"))
            model = get_model("955717e8", repo=checkpoint.parent).cpu().eval()
        else:
            connection.send(("progress", 12, "Nạp Demucs htdemucs; lần đầu tải mô hình (~80 MB), âm thanh xử lý trên máy…"))
            model = get_model("htdemucs").cpu().eval()
        sample_rate = int(model.samplerate)
        if sample_rate != 44100:
            raise AIError("Mô hình Demucs có tần số lấy mẫu ngoài cấu hình 44100 Hz.")
        source = sf.SoundFile(source_wav)
        if source.samplerate != sample_rate or source.channels != model.audio_channels:
            source.close()
            raise AIError("Âm thanh trung gian không đúng tần số/kênh của Demucs.")
        names = ["vocals", "background"] + (["drums", "bass", "other"] if mode == "stems" else [])
        paths = {name: str(Path(folder) / f"{name}.wav") for name in names}
        outputs = [sf.SoundFile(paths[name], mode="w", samplerate=sample_rate, channels=2,
                                format="WAV", subtype="FLOAT") for name in names]
        handles = dict(zip(names, outputs))
        total = source.frames
        block_length = sample_rate * 30
        context = sample_rate * 2
        cursor = 0
        with source, torch.inference_mode():
            while cursor < total:
                # Two-second overlap/context prevents artificial seams between
                # bounded-memory 30 s blocks; only each block's centre is saved.
                left = max(0, cursor - context)
                right = min(total, cursor + block_length + context)
                source.seek(left)
                array = source.read(right - left, dtype="float32", always_2d=True)
                mix = torch.from_numpy(np.ascontiguousarray(array.T))
                reference = mix.mean(0)
                mean = reference.mean()
                std = reference.std()
                if float(std) < 1e-6:
                    stems = torch.zeros((len(model.sources), *mix.shape), dtype=mix.dtype)
                    # Silence/DC are not neural inference inputs; preserve DC as background.
                    stems[model.sources.index("other")] = mix
                else:
                    normalized = (mix - mean) / std
                    estimates = apply_model(model, normalized[None], device="cpu", shifts=0,
                                            split=True, overlap=.25, segment=7, progress=False, num_workers=0)[0]
                    stems = estimates * std + mean / len(model.sources)
                start = cursor - left
                keep = min(block_length, total - cursor)
                stems = stems[:, :, start:start + keep]
                vocal = stems[model.sources.index("vocals")]
                # Complement guarantees that vocals + background reconstruct
                # the original mixture, despite separation model residuals.
                background = mix[:, start:start + keep] - vocal
                handles["vocals"].write(vocal.T.numpy())
                handles["background"].write(background.T.numpy())
                if mode == "stems":
                    for name in ("drums", "bass", "other"):
                        handles[name].write(stems[model.sources.index(name)].T.numpy())
                cursor += keep
                connection.send(("progress", 15 + int(80 * cursor / max(1, total)),
                                 f"Tách giọng/nhạc bằng Demucs: {cursor / sample_rate:.1f}/{total / sample_rate:.1f} giây"))
        for item in outputs:
            item.close()
        outputs = []
        connection.send(("result", paths))
    except Exception as exc:
        connection.send(("error", f"Không tách được giọng/nhạc: {exc}"))
    finally:
        for item in outputs:
            item.close()
        for item in silent_streams:
            item.close()
        connection.close()


def separate_audio(video: str, out_dir: str, mode: str = "vocals", progress: Progress | None = None,
                   cancel: threading.Event | None = None, device: str = "auto") -> dict[str, str]:
    """Separate vocals/background or four music stems using real local Demucs.

    Outputs are 44.1 kHz stereo float WAV, preserving length and headroom.
    Music models can leave dialogue/sound-effect artifacts; review before dubbing.
    Auto reuses an installed NVIDIA pack; explicit cuda never silently uses CPU.
    Cancel terminates inference, including the separate GPU Python process.
    """
    ai_tools._check_cancel(cancel)
    if mode not in ("vocals", "stems"):
        raise ValueError("Chế độ tách phải là vocals hoặc stems.")
    if device not in ("auto", "cuda", "cpu"):
        raise ValueError("Thiết bị tách giọng phải là auto, cuda hoặc cpu.")
    info = engine.probe(video)
    if not info["has_audio"] or info["duration"] <= 0:
        raise AIError("Tệp nguồn không có âm thanh hoặc thời lượng hợp lệ.")
    installed_pack = gpu_runtime.discover_demucs_pack()
    gpu_pack = installed_pack if device != "cpu" else None
    if device == "cuda" and gpu_pack is None:
        raise AIError("Đã chọn GPU NVIDIA nhưng chưa có bộ smartvoice_cuda hoặc mô hình đã cài.")
    if gpu_pack is None:
        for module, label in (("demucs", "Demucs"), ("torch", "PyTorch"), ("soundfile", "SoundFile")):
            _require(module, label)
        if device == "auto":
            ai_tools._progress(progress, 0, "Không có bộ Demucs GPU đã cài; đang sử dụng CPU.")
    folder = Path(out_dir).expanduser().resolve()
    if folder.is_file():
        raise ValueError("Đầu ra tách giọng phải là một thư mục.")
    folder.mkdir(parents=True, exist_ok=True)
    event = cancel if cancel is not None else threading.Event()
    prefix = Path(info["path"]).stem + "_" + uuid.uuid4().hex[:8]
    published = []
    try:
        with tempfile.TemporaryDirectory(prefix=".mediafusion-demucs-", dir=folder) as temporary:
            staging = Path(temporary)
            source_wav = staging / "input.wav"
            engine._ffmpeg(["-i", info["path"], "-vn", "-map", "0:a:0", "-ar", "44100", "-ac", "2",
                            "-c:a", "pcm_f32le", str(source_wav)], info["duration"],
                           lambda p, m: ai_tools._progress(progress, int(p * .1), "Chuẩn bị âm thanh cho Demucs…"),
                           event, "Chuẩn bị âm thanh…")
            used_device = "CPU"
            cpu_cache = str(Path(installed_pack["checkpoint"]).parent.parent) if installed_pack else str(_cache_folder("demucs"))
            if gpu_pack:
                try:
                    paths = gpu_runtime.separate_demucs_gpu(str(source_wav), str(staging), mode, progress, cancel)
                    used_device = "GPU NVIDIA"
                except CancelledError:
                    raise
                except AIError as exc:
                    if device == "cuda":
                        raise
                    ai_tools._progress(progress, 10, f"Không dùng được GPU ({exc}); chuyển sang CPU.")
                    for module, label in (("demucs", "Demucs"), ("torch", "PyTorch"), ("soundfile", "SoundFile")):
                        _require(module, label)
                    paths = _run_worker(_separation_worker, (str(source_wav), str(staging), mode, cpu_cache),
                                        progress, cancel)
            else:
                paths = _run_worker(_separation_worker, (str(source_wav), str(staging), mode, cpu_cache),
                                    progress, cancel)
            result = {}
            for name, value in paths.items():
                ai_tools._check_cancel(cancel)
                target = ai_tools._output_target(folder / f"{prefix}_{name}.wav", info["path"])
                result[name] = ai_tools._publish(Path(value), target, cancel)
                published.append(target)
        ai_tools._progress(progress, 100, f"Đã tách {len(result)} tệp âm thanh bằng Demucs · {used_device}.")
        return result
    except engine.Cancelled as exc:
        for item in published:
            item.unlink(missing_ok=True)
        raise CancelledError(str(exc)) from exc
    except BaseException:
        for item in published:
            item.unlink(missing_ok=True)
        raise

