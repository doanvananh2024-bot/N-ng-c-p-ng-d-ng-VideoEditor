"""Local media processing for VideoEditorIndependent, using FFmpeg and yt-dlp.

The module has no GUI dependencies. Call processing functions from a worker
thread; progress callbacks receive ``(percentage, human_readable_status)``.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urlparse
import uuid


Progress = Callable[[int, str], None]


class Cancelled(RuntimeError):
    """The user cancelled an operation; no final output was published."""


def tool_path(name: str) -> str:
    """Find packaged tools, the original installation's tools, or PATH."""
    executable = name if name.endswith(".exe") else name + ".exe"
    roots = [Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
        if getattr(sys, "_MEIPASS", None):
            roots.insert(0, Path(sys._MEIPASS))
    for root in roots:
        for folder in (root / "tools", root / "data" / "tools", root.parent / "data" / "tools"):
            candidate = folder / executable
            if candidate.is_file():
                return str(candidate)
    found = shutil.which(name) or shutil.which(executable)
    if found:
        return found
    raise RuntimeError(f"Không tìm thấy công cụ {name}. Hãy đặt {executable} trong thư mục tools.")


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _source(path: str) -> Path:
    result = Path(path).expanduser().resolve()
    if not result.is_file():
        raise ValueError(f"Không tìm thấy tệp: {result}")
    return result


def probe(path: str) -> dict:
    source = _source(path)
    try:
        result = subprocess.run(
            [tool_path("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            timeout=30, creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Đọc thông tin video quá lâu. Hãy kiểm tra tệp nguồn.") from exc
    if result.returncode:
        raise ValueError(f"Không đọc được tệp {source.name}: {result.stderr.strip()[-700:]}")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Thông tin tệp không hợp lệ: {source.name}") from exc
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if not math.isfinite(duration) or duration < 0:
        duration = 0.0
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    rotation = 0
    for item in video.get("side_data_list", []):
        if "rotation" in item:
            rotation = int(float(item["rotation"]))
    if not rotation:
        rotation = int(float(video.get("tags", {}).get("rotate", 0)))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return {"path": str(source), "name": source.name, "duration": duration,
            "width": width, "height": height,
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def _report(progress: Progress | None, percent: float, message: str) -> None:
    if progress:
        progress(max(0, min(100, int(percent))), message)


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise Cancelled("Đã hủy xử lý.")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # yt-dlp can spawn FFmpeg; stop its child processes as well.
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=_creation_flags())
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is not None:
            return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run(command: list[str], cancel: threading.Event, handler: Callable[[str], None] | None = None,
         cwd: Path | None = None) -> None:
    _check_cancel(cancel)
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1, creationflags=_creation_flags(),
        stdin=subprocess.DEVNULL, cwd=str(cwd) if cwd else None,
    )
    lines: queue.Queue[str | None] = queue.Queue()
    tail: list[str] = []

    def reader() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        while True:
            _check_cancel(cancel)
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                break
            if line:
                tail.append(line)
                if len(tail) > 35:
                    tail.pop(0)
                if handler:
                    handler(line)
        code = process.wait()
        _check_cancel(cancel)
        if code:
            details = [line for line in tail if not re.match(
                r"^(frame|fps|stream_\d+_\d+_q|bitrate|total_size|out_time.*|dup_frames|drop_frames|speed|progress)=", line)]
            raise RuntimeError("Xử lý thất bại: " + "\n".join(details or tail)[-1600:])
    except BaseException:
        _stop(process)
        raise
    finally:
        thread.join(timeout=1)
        if process.stdout:
            process.stdout.close()


def _ffmpeg(args: list[str], duration: float, progress: Progress | None,
            cancel: threading.Event, message: str, cwd: Path | None = None) -> None:
    def handler(line: str) -> None:
        if line.startswith("out_time_us="):
            try:
                seconds = int(line.partition("=")[2]) / 1_000_000
                _report(progress, min(99, seconds / max(duration, 0.001) * 100), message)
            except ValueError:
                pass
    _run([tool_path("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-nostdin", "-n",
          "-progress", "pipe:1", "-nostats", *args], cancel, handler, cwd)
    _report(progress, 100, message)


def _destination(output: str) -> Path:
    result = Path(output).expanduser().resolve()
    if result.exists():
        raise FileExistsError(f"Tệp đã tồn tại, hãy chọn tên khác: {result.name}")
    result.parent.mkdir(parents=True, exist_ok=True)
    return result


def temporary_parent(parent: Path) -> Path:
    """Keep Windows subprocess working directories short on the output volume.

    CreateProcess rejects cwd at MAX_PATH even when FFmpeg and Python accept
    the input/output filenames. Use an existing ancestor for disposable work,
    while keeping final publication on the same filesystem.
    """
    parent = Path(parent).resolve()
    if os.name == "nt":
        while len(str(parent)) > 160 and parent.parent != parent:
            parent = parent.parent
    return parent


def _publish(source: Path, destination: Path) -> None:
    """Publish atomically without overwriting a concurrently created file."""
    try:
        os.link(source, destination)
        source.unlink()
    except FileExistsError:
        raise FileExistsError(f"Tệp đã tồn tại, hãy chọn tên khác: {destination.name}") from None
    except OSError:
        # Both files share the temporary directory's volume. Windows rename
        # refuses an existing destination, so it is a safe filesystem fallback.
        if os.name == "nt":
            os.rename(source, destination)
        else:
            raise


def _number(settings: dict, key: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Thiết lập {key} không hợp lệ.") from exc
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"Thiết lập {key} phải từ {lower:g} đến {upper:g}.")
    return value


def _tempo(speed: float) -> str:
    factors: list[float] = []
    while speed > 2:
        factors.append(2)
        speed /= 2
    while speed < 0.5:
        factors.append(0.5)
        speed /= 0.5
    factors.append(speed)
    return ",".join(f"atempo={factor:.9g}" for factor in factors)


def _subtitle_filter(path: Path, size: int) -> str:
    # The filter is passed as one subprocess argument, without a shell. FFmpeg's
    # filter parser still needs its own escaping for drive colons and apostrophes.
    escaped = str(path).replace("\\", "/").replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:").replace("'", "'\\''").replace("[", "\\[").replace("]", "\\]")
    return f"subtitles=filename='{escaped}':force_style='FontSize={size},Alignment=2,MarginV=24'"


def media_process(clips: list[dict], settings: dict, output: str,
                  progress: Progress, cancel: threading.Event) -> str:
    _check_cancel(cancel)
    if not clips:
        raise ValueError("Hãy thêm ít nhất một video.")
    target = _destination(output)
    speed = _number(settings, "speed", 1, 0.1, 8)
    fps = int(_number(settings, "fps", 30, 1, 120))
    rotation = int(settings.get("rotation", 0))
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Góc xoay phải là 0, 90, 180 hoặc 270 độ.")
    brightness = _number(settings, "brightness", 0, -1, 1)
    contrast = _number(settings, "contrast", 1, 0.1, 3)
    saturation = _number(settings, "saturation", 1, 0, 3)
    volume = 0 if settings.get("mute", False) else _number(settings, "volume", 1, 0, 2)
    music = _source(settings["music_path"]) if settings.get("music_path") else None
    subtitle = _source(settings["subtitle_path"]) if settings.get("subtitle_path") else None
    logo = _source(settings["logo_path"]) if settings.get("logo_path") else None
    music_volume = _number(settings, "music_volume", 0.25, 0, 2)
    subtitle_size = int(_number(settings, "subtitle_size", 24, 8, 120))
    fit = settings.get("fit", "contain")
    if fit not in ("contain", "cover"):
        raise ValueError("Kiểu khung hình phải là contain hoặc cover.")
    from source_composition import needs_composition, ffmpeg_composition
    compose_source = needs_composition(settings)
    sources: list[tuple[dict, float, float, float]] = []
    for clip in clips:
        _check_cancel(cancel)
        info = probe(clip["path"])
        if info["width"] <= 0 or info["height"] <= 0 or info["duration"] <= 0:
            raise ValueError(f"Tệp không phải video có thời lượng hợp lệ: {info['name']}")
        start = float(clip.get("start") or 0)
        end = float(clip.get("end") or info["duration"])
        if end <= 0:
            end = info["duration"]
        end = min(end, info["duration"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"Điểm cắt không hợp lệ: {info['name']}")
        sources.append((info, start, end, (end - start) / speed))
    first = sources[0][0]
    natural_w, natural_h = first["width"], first["height"]
    if rotation in (90, 270):
        natural_w, natural_h = natural_h, natural_w
    width = int(_number(settings, "width", 0, 0, 7680)) or natural_w
    height = int(_number(settings, "height", 0, 0, 7680)) or natural_h
    if width < 2 or height < 2:
        raise ValueError("Kích thước video phải lớn hơn 1 pixel.")
    width, height = width // 2 * 2, height // 2 * 2
    total = sum(item[3] for item in sources)
    blur = settings.get("blur_region")
    if blur:
        if len(blur) != 4:
            raise ValueError("Vùng làm mờ cần x, y, chiều rộng, chiều cao.")
        x, y, bw, bh = (int(value) for value in blur)
        if x < 0 or y < 0 or bw < 2 or bh < 2 or x + bw > width or y + bh > height:
            raise ValueError(f"Vùng làm mờ phải nằm trong khung {width} × {height}.")
    _report(progress, 0, "Chuẩn bị video…")
    with tempfile.TemporaryDirectory(prefix=".vei-", dir=temporary_parent(target.parent)) as temporary:
        folder = Path(temporary)
        normalized: list[Path] = []
        elapsed = 0.0
        for index, (info, start, end, duration) in enumerate(sources):
            _check_cancel(cancel)
            clip_output = folder / f"clip_{index:04d}.mkv"
            filters = ["setpts=PTS-STARTPTS"]
            if rotation == 90:
                filters.append("transpose=1")
            elif rotation == 270:
                filters.append("transpose=2")
            elif rotation == 180:
                filters += ["hflip", "vflip"]
            if settings.get("mirror"):
                filters.append("hflip")
            if fit == "contain":
                filters += [f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"]
            else:
                filters += [f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2",
                            f"crop={width}:{height}"]
            finishing = ["setsar=1", f"eq=brightness={brightness:g}:contrast={contrast:g}:saturation={saturation:g}",
                         f"setpts=PTS/{speed:g}", f"fps={fps}", "format=yuv420p"]
            if compose_source:
                video_filter = ffmpeg_composition(info["width"], info["height"], width, height, settings)
                video_filter += ";[source_composed]" + ",".join(finishing)
            else:
                video_filter = "[0:v:0]" + ",".join(filters + finishing)
            if blur:
                blur_radius = max(0, min(10, bw // 2 - 1, bh // 2 - 1))
                chroma_radius = max(0, min(10, bw // 4 - 1, bh // 4 - 1))
                video_filter += ("[base];[base]split=2[clear][region];"
                                 f"[region]crop={bw}:{bh}:{x}:{y},"
                                 f"boxblur=luma_radius={blur_radius}:luma_power=2:"
                                 f"chroma_radius={chroma_radius}:chroma_power=2[blurred];"
                                 f"[clear][blurred]overlay={x}:{y}[video]")
            else:
                video_filter += "[video]"
            args = ["-ss", f"{start:.9f}", "-t", f"{end-start:.9f}", "-i", info["path"]]
            audio_input = "0:a:0"
            if not info["has_audio"]:
                args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                audio_input = "1:a:0"
            audio_filter = (f"[{audio_input}]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp,{_tempo(speed)},"
                            f"volume={volume:g},aresample=48000,aformat=channel_layouts=stereo,"
                            f"apad,atrim=duration={duration:.9f}[audio]")
            args += ["-filter_complex", video_filter + ";" + audio_filter,
                     "-map", "[video]", "-map", "[audio]", "-t", f"{duration:.9f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2",
                     "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(clip_output)]
            base_percent, share = elapsed / total * 75, duration / total * 75
            _ffmpeg(args, duration,
                    lambda percent, _message, base=base_percent, amount=share, number=index:
                        _report(progress, base + amount * percent / 100,
                                f"Xử lý video {number+1}/{len(sources)}…"), cancel, "Xử lý video…")
            elapsed += duration
            normalized.append(clip_output)
        concat_file = folder / "concat.txt"
        concat_file.write_text("\n".join("file '" + str(path).replace("\\", "/").replace("'", "'\\''") + "'"
                                         for path in normalized), encoding="utf-8")
        joined = folder / "joined.mkv"
        _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_file), "-map", "0:v:0", "-map", "0:a:0",
                 "-c", "copy", str(joined)], total,
                lambda p, m: _report(progress, 75 + p * 0.05, "Ghép video…"), cancel, "Ghép video…")
        # PCM intermediates avoid AAC priming and decoder-state artifacts at
        # clip seams. Encode audio once when making the final MP4.
        args = ["-i", str(joined)]
        logo_index = music_index = None
        next_index = 1
        if logo:
            logo_index = next_index
            next_index += 1
            args += ["-i", str(logo)]
        if music:
            music_index = next_index
            if settings.get("music_loop", True):
                args += ["-stream_loop", "-1"]
            args += ["-i", str(music)]
        graph: list[str] = []
        video_stream = "0:v:0"
        if subtitle:
            # A relative ASCII name avoids FFmpeg's two levels of filter path
            # escaping, including Windows drives and apostrophes in user paths.
            extension = subtitle.suffix.lower()
            if extension not in (".srt", ".ass", ".ssa", ".vtt"):
                extension = ".srt"
            local_subtitle = Path("captions" + extension)
            shutil.copyfile(subtitle, folder / local_subtitle)
            graph.append(f"[{video_stream}]{_subtitle_filter(local_subtitle, subtitle_size)}[captioned]")
            video_stream = "captioned"
        if logo:
            logo_width = int(_number(settings, "logo_width", min(120, width), 8, width))
            margin = max(2, min(24, width // 20, height // 20))
            position = settings.get("logo_position", "top-right")
            positions = {"top-left": (str(margin), str(margin)),
                         "top-right": (f"main_w-overlay_w-{margin}", str(margin)),
                         "bottom-left": (str(margin), f"main_h-overlay_h-{margin}"),
                         "bottom-right": (f"main_w-overlay_w-{margin}", f"main_h-overlay_h-{margin}")}
            if position not in positions:
                raise ValueError("Vị trí logo không hợp lệ.")
            lx, ly = positions[position]
            graph.append(f"[{logo_index}:v:0]scale={logo_width}:-1[logo]")
            graph.append(f"[{video_stream}][logo]overlay={lx}:{ly}:eof_action=repeat:shortest=0[branded]")
            video_stream = "branded"
        audio_stream = "0:a:0"
        if music:
            graph.append(f"[{music_index}:a:0]volume={music_volume:g},aresample=48000,"
                         f"aformat=channel_layouts=stereo,atrim=duration={total:.9f},asetpts=PTS-STARTPTS[music]")
            graph.append("[0:a:0][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                         "alimiter=limit=0.98:level=0[audio]")
            audio_stream = "audio"
        if graph:
            args += ["-filter_complex", ";".join(graph)]
        args += ["-map", f"[{video_stream}]" if ":" not in video_stream else video_stream,
                 "-map", f"[{audio_stream}]" if ":" not in audio_stream else audio_stream,
                 "-t", f"{total:.9f}"]
        if subtitle or logo:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2", "-pix_fmt", "yuv420p"]
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        final = folder / "final.mp4"
        args += ["-movflags", "+faststart", str(final)]
        _ffmpeg(args, total, lambda p, m: _report(progress, 80 + p * 0.19, "Hoàn thiện video…"),
                cancel, "Hoàn thiện video…", cwd=folder)
        _check_cancel(cancel)
        _publish(final, target)
    _report(progress, 100, "Đã xuất video.")
    return str(target)


def split_video(path: str, seconds: float, out_dir: str, progress: Progress,
                cancel: threading.Event) -> list[str]:
    _check_cancel(cancel)
    info = probe(path)
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Độ dài mỗi đoạn phải lớn hơn 0 giây.")
    if info["width"] <= 0 or info["duration"] <= 0:
        raise ValueError("Hãy chọn tệp video có thời lượng hợp lệ.")
    folder = Path(out_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    prefix = Path(info["path"]).stem + "_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    published: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".mediafusion-split-", dir=folder) as temporary:
            pattern = str(Path(temporary) / "part_%04d.mp4")
            _ffmpeg(["-i", info["path"], "-map", "0:v:0", "-map", "0:a:0?",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2",
                     "-pix_fmt", "yuv420p", "-force_key_frames", f"expr:gte(t,n_forced*{seconds:.9f})",
                     "-c:a", "aac", "-b:a", "192k", "-f", "segment", "-segment_time", f"{seconds:.9f}",
                     "-reset_timestamps", "1", pattern], info["duration"],
                    lambda p, m: _report(progress, p * 0.99, "Chia video…"), cancel, "Chia video…")
            for index, part in enumerate(sorted(Path(temporary).glob("part_*.mp4")), 1):
                _check_cancel(cancel)
                target = folder / f"{prefix}_phan_{index:04d}.mp4"
                _publish(part, target)
                published.append(target)
        if not published:
            raise RuntimeError("Không tạo được đoạn video nào.")
    except BaseException:
        for item in published:
            item.unlink(missing_ok=True)
        raise
    _report(progress, 100, f"Đã tạo {len(published)} đoạn video.")
    return [str(item) for item in published]


def extract_mp3(path: str, output: str, progress: Progress,
                cancel: threading.Event) -> str:
    _check_cancel(cancel)
    info = probe(path)
    if not info["has_audio"]:
        raise ValueError("Tệp nguồn không có âm thanh.")
    target = _destination(output)
    with tempfile.TemporaryDirectory(prefix=".mediafusion-audio-", dir=target.parent) as temporary:
        temporary_output = Path(temporary) / "audio.mp3"
        _ffmpeg(["-i", info["path"], "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "2",
                 str(temporary_output)], info["duration"], progress, cancel, "Tách MP3…")
        _check_cancel(cancel)
        _publish(temporary_output, target)
    _report(progress, 100, "Đã tách MP3.")
    return str(target)


def download_video(url: str, out_dir: str, progress: Progress,
                   cancel: threading.Event) -> list[str]:
    _check_cancel(cancel)
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Hãy nhập liên kết video http hoặc https hợp lệ.")
    folder = Path(out_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".mediafusion-download-", dir=folder) as temporary:
            def handler(line: str) -> None:
                match = re.search(r"(?:download:|\[download\]\s+)(\d+(?:\.\d+)?)%", line)
                if match:
                    _report(progress, min(95, float(match[1]) * 0.95), "Đang tải video…")
                elif "Merging formats" in line or "[Merger]" in line:
                    _report(progress, 96, "Ghép hình và âm thanh…")
            _report(progress, 0, "Đọc liên kết video…")
            runtime_args = []
            try:
                runtime_args = ["--js-runtimes", "deno:" + tool_path("deno")]
            except RuntimeError:
                pass
            _run([tool_path("yt-dlp"), "--ignore-config", *runtime_args, "--no-playlist", "--playlist-end", "1", "--no-overwrites",
                  "--newline", "--progress", "--progress-template", "download:%(progress._percent_str)s",
                  "--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3",
                  "--restrict-filenames", "--ffmpeg-location", str(Path(tool_path("ffmpeg")).parent),
                  "-f", "bv*+ba/b", "--merge-output-format", "mp4",
                  "-o", str(Path(temporary) / "%(title).160B [%(id)s].%(ext)s"),
                  "--", url.strip()], cancel, handler)
            files = [item for item in Path(temporary).iterdir() if item.is_file()
                     and item.suffix.lower() not in (".part", ".ytdl", ".json", ".temp")]
            if not files:
                raise RuntimeError("Không tải được video. Trang có thể yêu cầu đăng nhập.")
            for source in files:
                _check_cancel(cancel)
                target = folder / source.name
                if target.exists():
                    target = folder / (source.stem + "_" + uuid.uuid4().hex[:8] + source.suffix)
                _publish(source, target)
                published.append(target)
    except BaseException:
        for item in published:
            item.unlink(missing_ok=True)
        raise
    _report(progress, 100, "Đã tải video.")
    return [str(item) for item in published]
