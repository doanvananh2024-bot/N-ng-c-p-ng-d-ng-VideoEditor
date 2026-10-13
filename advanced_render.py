"""Composable local video layers and export encoders for VideoEditorIndependent Studio.

Coordinates are pixels in the final canvas. A layer ending at 0 lasts until the
end of the video. Subprocesses use argument arrays; user text is rendered to
transparent PNGs and never interpolated into FFmpeg expressions.
"""
from __future__ import annotations

from functools import lru_cache
import copy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

import engine

_ENCODERS = {
    "cpu": {"h264": "libx264", "hevc": "libx265", "vp9": "libvpx-vp9"},
    "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc"},
    "qsv": {"h264": "h264_qsv", "hevc": "hevc_qsv", "vp9": "vp9_qsv"},
    "amf": {"h264": "h264_amf", "hevc": "hevc_amf"},
}
_ENCODER_CACHE: tuple[float, list[dict]] = (0, [])
_ENCODER_LOCK = threading.Lock()
_ADVANCED_KEYS = {"text_layers", "image_layers", "blur_zones", "filter", "filter_preset",
                  "zoom_enabled", "frame_border", "fade_in", "fade_out", "subtitle_style", "encoder",
                  "voice_path", "background_path", "audio_filters", "audio_effect", "audio_layers", "filter_layers"}
_AUDIO_REFERENCE_KEYS = {"source_stem_paths", "source_stem_volumes", "source_fade_in", "source_fade_out",
                         "voice_fade_in", "voice_fade_out", "source_audio_enabled", "voice_audio_enabled"}


def has_advanced(settings: dict) -> bool:
    """Whether the project requests layers, styling, or export controls."""
    merged = {**settings, **(settings.get("advanced") or {})}
    return (any(bool(merged.get(key)) for key in _ADVANCED_KEYS)
            or bool(_AUDIO_REFERENCE_KEYS.intersection(merged)) or "subtitle_tracks" in merged)


def resolved_subtitle_tracks(settings: dict, assets: dict | None = None) -> list[dict] | None:
    """None preserves legacy captions; an explicit empty list draws no captions.

    At most one original and one translated track are accepted. A missing path
    represents a role not generated/imported yet and is skipped, never replaced
    with the other role or the legacy single-caption path.
    """
    settings = {**settings, **(settings.get("advanced") or {})}
    if "subtitle_tracks" not in settings:
        return None
    tracks = settings["subtitle_tracks"]
    if not isinstance(tracks, list):
        raise ValueError("Danh sách lớp phụ đề chưa hợp lệ.")
    result, roles = [], set()
    global_style = {"font": "Arial", "size": settings.get("subtitle_size", 24), **(settings.get("subtitle_style") or {})}
    for track in tracks:
        if not isinstance(track, dict) or track.get("role") not in ("original", "translated"):
            raise ValueError("Lớp phụ đề cần chọn original hoặc translated.")
        role = track["role"]
        if role in roles:
            raise ValueError("Mỗi loại phụ đề chỉ được có một lớp.")
        roles.add(role)
        style = track.get("style") or {}
        if not isinstance(style, dict):
            raise ValueError("Kiểu chữ của lớp phụ đề chưa hợp lệ.")
        path = (assets or {}).get(role + "_subtitle") or track.get("path") or ""
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError("Đường dẫn lớp phụ đề chưa hợp lệ.")
        result.append(dict(track, role=role, path=str(path), enabled=bool(track.get("enabled", True)),
                           style=copy.deepcopy({**global_style, **style})))
    return result


def hardware_encoders() -> list[dict]:
    """Return only advertised encoders that successfully encode real frames.

    The result also includes CPU encoders. Availability is cached for five
    minutes. A compiled-in NVENC/QSV/AMF entry alone never counts as support.
    """
    global _ENCODER_CACHE
    with _ENCODER_LOCK:
        if time.monotonic() - _ENCODER_CACHE[0] < 300 and _ENCODER_CACHE[1]:
            return [dict(item) for item in _ENCODER_CACHE[1]]
        binary = engine.tool_path("ffmpeg")
        response = subprocess.run([binary, "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=engine._creation_flags())
        advertised = set(re.findall(r"^\s*V\S*\s+(\S+)", response.stdout, re.MULTILINE))
        working = []
        for device in ("nvenc", "qsv", "amf", "cpu"):
            for codec, encoder in _ENCODERS[device].items():
                if encoder not in advertised:
                    continue
                command = [binary, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=black:s=128x128:r=10:d=0.2",
                    "-frames:v", "2", "-an", "-c:v", encoder, "-threads", "1",
                    "-pix_fmt", "yuv420p", "-f", "null", "-"]
                if encoder == "libx265":
                    command[command.index("-pix_fmt"):command.index("-pix_fmt")] = ["-x265-params", "pools=1:frame-threads=1"]
                try:
                    tested = subprocess.run(command, capture_output=True, timeout=12,
                                             creationflags=engine._creation_flags())
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if tested.returncode == 0:
                    working.append({"device": device, "codec": codec, "encoder": encoder,
                        "label": {"nvenc": "NVIDIA NVENC", "qsv": "Intel Quick Sync",
                                  "amf": "AMD AMF", "cpu": "CPU"}[device] + " · " + codec.upper()})
        _ENCODER_CACHE = (time.monotonic(), working)
        return [dict(item) for item in working]


def _num(item: dict, key: str, default: float, low: float, high: float) -> float:
    return engine._number(item, key, default, low, high)


def _source_fps(path: str) -> float:
    """Read source cadence so zoom never speeds up a non-30-FPS source."""
    result = subprocess.run([engine.tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        creationflags=engine._creation_flags())
    try:
        stream = json.loads(result.stdout)["streams"][0]
        for key in ("avg_frame_rate", "r_frame_rate"):
            try:
                value = float(Fraction(stream.get(key, "0")))
            except (ValueError, ZeroDivisionError):
                continue
            if math.isfinite(value) and 1 <= value <= 240:
                return value
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    return 30.0


def _color(value, default: str = "#ffffff") -> str:
    value = str(value or default).strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:]).lower()
    from PIL import ImageColor
    try:
        rgb = ImageColor.getrgb(value)
        return "#%02x%02x%02x" % rgb[:3]
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Màu không hợp lệ: {value}") from exc


def _rgba(value, opacity: float) -> tuple[int, int, int, int]:
    color = _color(value)
    return tuple(int(color[p:p+2], 16) for p in (1, 3, 5)) + (round(opacity * 255),)


@lru_cache(maxsize=64)
def _font_path(font: str, bold: bool = False) -> str:
    from ui_theme import font_path
    bundled=font_path(font,bold)
    if bundled: return bundled
    candidate = Path(font)
    if candidate.is_file() and candidate.suffix.lower() in (".ttf", ".otf", ".ttc"):
        return str(candidate)
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    wanted = font.casefold().strip()
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                    candidates = []
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, path, _ = winreg.EnumValue(key, index)
                        normalized = re.sub(r"\s*\(.*?\)", "", name).casefold()
                        if normalized == wanted or normalized == wanted + (" bold" if bold else " regular"):
                            file = Path(path)
                            if not file.is_absolute():
                                file = windows_fonts / file
                            if file.is_file():
                                candidates.append((0 if ("bold" in normalized) == bold else 1, str(file)))
                    if candidates:
                        return min(candidates)[1]
            except OSError:
                pass
    known = {"arial": "arialbd.ttf" if bold else "arial.ttf",
             "segoe ui": "segoeuib.ttf" if bold else "segoeui.ttf",
             "times new roman": "timesbd.ttf" if bold else "times.ttf",
             "tahoma": "tahomabd.ttf" if bold else "tahoma.ttf",
             "calibri": "calibrib.ttf" if bold else "calibri.ttf"}
    file = windows_fonts / known.get(wanted, "arialbd.ttf" if bold else "arial.ttf")
    if file.is_file():
        return str(file)
    return "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"


def _text_image(item: dict, max_width: int):
    if "text_effect" in item or "bg_fit" in item:
        from text_effects import render_text
        return _overlay_transform(render_text(item,max_width),item)
    from PIL import Image, ImageDraw, ImageFont
    text = str(item.get("text", ""))
    if item.get("uppercase"):
        text = text.upper()
    if len(text) > 20000:
        raise ValueError("Một lớp văn bản chỉ được dài tối đa 20.000 ký tự.")
    size = int(_num(item, "font_size", 36, 6, 512))
    outline = int(_num(item, "outline", 1, 0, 32))
    opacity = _num(item, "opacity", 1, 0, 1)
    font = ImageFont.truetype(_font_path(str(item.get("font") or "Arial"), bool(item.get("bold"))), size)
    spacing = max(2, size // 6)
    probe_image = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(probe_image)
    # Optional wrapping avoids titles that extend beyond the canvas.
    wrap_width = int(_num(item, "wrap_width", 0, 0, max(16384, max_width)))
    if wrap_width:
        wrapped = []
        for paragraph in text.splitlines() or [""]:
            line = ""
            for word in paragraph.split(" "):
                candidate = line + (" " if line else "") + word
                if line and draw.textlength(candidate, font=font) > wrap_width:
                    wrapped.append(line)
                    line = word
                else:
                    line = candidate
            wrapped.append(line)
        text = "\n".join(wrapped)
    bbox = draw.multiline_textbbox((0, 0), text or " ", font=font,
                                   spacing=spacing, stroke_width=outline)
    pad = max(4, int(_num(item, "padding", size // 5, 0, 200)))
    width, height = max(2, bbox[2] - bbox[0] + pad * 2), max(2, bbox[3] - bbox[1] + pad * 2)
    if width > 32768 or height > 32768 or width * height > 40_000_000:
        raise ValueError("Lớp văn bản quá lớn. Hãy giảm cỡ chữ hoặc chia thành nhiều lớp.")
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    background_opacity = _num(item, "bg_opacity", 0, 0, 1)
    if background_opacity:
        draw.rounded_rectangle((0, 0, width-1, height-1), radius=int(item.get("bg_radius") or 0),
                               fill=_rgba(item.get("bg_color", "#000000"), background_opacity * opacity))
    draw.multiline_text((pad - bbox[0], pad - bbox[1]), text, font=font, spacing=spacing,
        fill=_rgba(item.get("color", "#ffffff"), opacity), stroke_width=outline,
        stroke_fill=_rgba(item.get("outline_color", "#000000"), opacity),
        align=str(item.get("text_align", "left")) if item.get("text_align") in ("left", "center", "right") else "left")
    return _overlay_transform(image, item)


def _overlay_transform(image, item: dict):
    """Transform an RGBA overlay; coordinates refer to the resulting bounds."""
    from PIL import Image
    if item.get("flip_horizontal"):
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if item.get("flip_vertical"):
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    rotation = _num(item, "rotation", 0, -180, 180)
    if rotation:
        image = image.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
    return image


def _image_layer_image(item: dict, frame_index: int = 0):
    """Image raster shared by export and the editable preview."""
    from PIL import Image
    with Image.open(item["path"]) as source:
        if frame_index:
            source.seek(frame_index)
        image = source.convert("RGBA")
    width = int(_num(item, "width", 160, 2, 16384))
    height = int(_num(item, "height", 0, 0, 16384)) or max(2, round(width * image.height / image.width))
    if width * height > 40_000_000:
        raise ValueError("Lớp ảnh quá lớn. Hãy giảm kích thước.")
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    return _overlay_transform(image, item)


def _animation_durations(path) -> list[float]:
    """Frame durations in seconds, matching FFmpeg's GIF delay defaults."""
    from PIL import Image
    durations = []
    with Image.open(path) as image:
        if getattr(image, "is_animated", False):
            for index in range(image.n_frames):
                image.seek(index)
                milliseconds = float(image.info.get("duration") or 100)
                if Path(path).suffix.lower() == ".gif" and milliseconds < 20:
                    milliseconds = 100
                durations.append(max(1, milliseconds) / 1000)
    return durations


def _text_png(item: dict, output: Path, max_width: int) -> None:
    _text_image(item, max_width).save(output)


def _timing(item: dict, duration: float) -> tuple[float, float, str]:
    start = _num(item, "start", 0, 0, duration)
    end = _num(item, "end", 0, 0, duration) or duration
    if end <= start:
        raise ValueError("Mốc kết thúc của lớp phải sau mốc bắt đầu.")
    return start, end, f"between(t,{start:.9g},{end:.9g})"


def _position(item: dict, canvas_width: int, canvas_height: int, moving: bool = False) -> tuple[str, str]:
    x = _num(item, "x", 16, -32768, 32768)
    y = _num(item, "y", 16, -32768, 32768)
    anchor = str(item.get("anchor") or "top-left")
    positions = {
        "top-left": (f"{x:g}", f"{y:g}"), "top": (f"(W-w)/2+{x:g}", f"{y:g}"),
        "top-right": (f"W-w-{x:g}", f"{y:g}"), "left": (f"{x:g}", f"(H-h)/2+{y:g}"),
        "center": (f"(W-w)/2+{x:g}", f"(H-h)/2+{y:g}"),
        "right": (f"W-w-{x:g}", f"(H-h)/2+{y:g}"),
        "bottom-left": (f"{x:g}", f"H-h-{y:g}"), "bottom": (f"(W-w)/2+{x:g}", f"H-h-{y:g}"),
        "bottom-right": (f"W-w-{x:g}", f"H-h-{y:g}")}
    aliases = {"tl": "top-left", "t": "top", "tr": "top-right", "l": "left", "c": "center",
               "r": "right", "bl": "bottom-left", "b": "bottom", "br": "bottom-right"}
    anchor = aliases.get(anchor, anchor)
    if anchor not in positions:
        raise ValueError("Điểm bám của lớp không hợp lệ.")
    px, py = positions[anchor]
    if not moving or str(item.get("marquee", "none")) == "none":
        return px, py
    mode = str(item.get("marquee"))
    speed = _num(item, "speed", 80, 1, 2000)
    start = _num(item, "start", 0, 0, 864000)
    elapsed = f"(t-{start:.9g})"
    if mode in ("auto","left","right","up","down") or ("text_effect" in item and mode != "horizontal"):
        from text_effects import motion_expressions
        return motion_expressions(mode,canvas_width,canvas_height,iw="overlay_w",ih="overlay_h",speed=speed,time=elapsed)
    if mode in ("horizontal", "inside"):
        return f"W-mod({elapsed}*{speed:g},W+w)", py
    if mode == "diagonal":
        return (f"abs(mod({elapsed}*{speed:g}+{x:g},2*max(1,W-w))-max(1,W-w))",
                f"abs(mod({elapsed}*{speed*0.7:g}+{y:g},2*max(1,H-h))-max(1,H-h))")
    if mode == "around":
        # Travel clockwise along the four edges, preserving layer dimensions.
        a, b = "max(1,W-w)", "max(1,H-h)"
        s = f"mod({elapsed}*{speed:g},2*({a}+{b}))"
        px = f"if(lt({s},{a}),{s},if(lt({s},{a}+{b}),{a},if(lt({s},2*{a}+{b}),2*{a}+{b}-{s},0)))"
        py = f"if(lt({s},{a}),0,if(lt({s},{a}+{b}),{s}-{a},if(lt({s},2*{a}+{b}),{b},2*({a}+{b})-{s})))"
        return px, py
    raise ValueError("Kiểu chạy chữ không hợp lệ.")


def _ass_color(value, opacity: float = 1) -> str:
    color = _color(value)
    red, green, blue = (int(color[p:p+2], 16) for p in (1, 3, 5))
    return f"&H{round((1-opacity)*255):02X}{blue:02X}{green:02X}{red:02X}"


def _caption_ass(path: Path, style: dict, folder: Path, width: int, height: int,
                 cancel: threading.Event) -> Path:
    converted = folder / "captions_raw.ass"
    engine._run([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-n", "-i", str(path), str(converted)], cancel)
    lines = converted.read_text(encoding="utf-8-sig").splitlines()
    size = int(_num(style, "size", style.get("font_size", 32), 6, 512))
    outline = _num(style, "outline", 2, 0, 24)
    background = _num(style, "bg_opacity", 0, 0, 1)
    alignment = int(_num(style, "alignment", 2, 1, 9))
    font = re.sub(r"[,\r\n{}\\]", "", str(style.get("font", "Arial")))[:100] or "Arial"
    primary = _ass_color(style.get("color", "#ffffff"), _num(style, "opacity", 1, 0, 1))
    border = _ass_color(style.get("bg_color" if background else "outline_color", "#000000"),
                        background if background else 1)
    # ASS BorderStyle=3 provides a box behind each subtitle line.
    default = (f"Style: Fusion,{font},{size},{primary},{primary},{border},{_ass_color('#000000',0)},"
               f"{-1 if style.get('bold') else 0},0,0,0,100,100,0,0,{3 if background else 1},"
               f"{max(outline,4) if background else outline:g},0,{alignment},24,24,24,1")
    header = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
              "ScaledBorderAndShadow: yes", "WrapStyle: 0", "", "[V4+ Styles]",
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
              default, "", "[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            continue
        fields[3] = "Fusion"
        # Source ASS formatting must not silently override the chosen style.
        text = re.sub(r"\{[^}]*\}", "", fields[9])
        if style.get("uppercase"):
            text = text.upper()
        if style.get("x") is not None or style.get("y") is not None:
            px = _num(style, "x", width / 2, -32768, 32768)
            py = _num(style, "y", height - 24, -32768, 32768)
            text = f"{{\\an{alignment}\\pos({px:g},{py:g})}}" + text
        fields[9] = text
        header.append(",".join(fields))
    result = folder / "captions.ass"
    result.write_text("\n".join(header) + "\n", encoding="utf-8-sig")
    return result


def _rate(value, default: str = "5M") -> str:
    if value is None or value == "" or value == 0:
        return default
    if isinstance(value, (float, int)):
        number = float(value)
        if not math.isfinite(number) or number <= 0 or number > 1000:
            raise ValueError("Bitrate phải từ 0 đến 1000 Mbps.")
        return f"{number:g}M"
    value = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?[kKmM]?", value) or float(value.rstrip("kKmM")) <= 0:
        raise ValueError("Bitrate không hợp lệ. Ví dụ: 5M hoặc 2500k.")
    return value


def _encoding(settings: dict, extension: str, progress) -> tuple[list[str], str]:
    item = dict(settings.get("encoder") or {})
    for key in ("format", "codec", "device", "quality", "preset", "bitrate_mode", "bitrate", "maxrate"):
        if "encoder_" + key in settings:
            item[key] = settings["encoder_" + key]
    fmt = str(item.get("format") or extension.lstrip(".") or "mp4").lower()
    if fmt not in ("mp4", "mkv", "mov", "webm"):
        raise ValueError("Định dạng xuất phải là MP4, MKV, MOV hoặc WebM.")
    codec = str(item.get("codec") or "h264").lower()
    codec = {"h265": "hevc", "h.264": "h264", "h.265": "hevc", "auto": "h264"}.get(codec, codec)
    if fmt == "webm":
        codec = "vp9"
    if codec not in ("h264", "hevc", "vp9"):
        raise ValueError("Codec phải là H.264, H.265 hoặc VP9.")
    device = str(item.get("device") or "cpu").lower()
    if device not in ("auto", "cpu", "nvenc", "qsv", "amf"):
        raise ValueError("Thiết bị mã hóa không hợp lệ.")
    if device == "cpu":
        encoder = _ENCODERS[device][codec]
    else:
        engine._report(progress, 0, "Kiểm tra thiết bị mã hóa…")
        available = hardware_encoders()
        matches = [row for row in available if row["codec"] == codec and (device == "auto" or row["device"] == device)]
        if not matches:
            raise ValueError(f"Thiết bị {device.upper()} không mã hóa được {codec.upper()} trên máy này. Hãy chọn CPU hoặc Tự động.")
        encoder = matches[0]["encoder"]
        device = matches[0]["device"]
    quality = int(_num(item, "quality", 30 if codec == "vp9" else 22, 0, 51))
    mode = str(item.get("bitrate_mode") or "crf").lower()
    if mode not in ("crf", "vbr", "cbr"):
        raise ValueError("Kiểu kiểm soát bitrate không hợp lệ.")
    preset = str(item.get("preset") or "balanced").lower()
    presets = {"fast": "veryfast", "balanced": "medium", "high": "slow"}
    speed = presets.get(preset, preset)
    if speed not in ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"):
        speed = "medium"
    args = ["-c:v", encoder, "-pix_fmt", "yuv420p", "-threads", "2"]
    if codec in ("h264", "hevc") and item.get("bframes") is False:
        args += ["-bf", "0"]
    if device == "cpu" and codec in ("h264", "hevc"):
        args += ["-preset", speed]
        if codec == "hevc":
            args += ["-x265-params", "pools=2:frame-threads=2"]
    elif encoder == "libvpx-vp9":
        args += ["-deadline", "good", "-cpu-used", "4" if speed in ("ultrafast", "superfast", "veryfast", "fast") else "2",
                 "-row-mt", "1"]
    elif device == "nvenc":
        args += ["-preset", "p2" if speed in ("ultrafast", "superfast", "veryfast", "fast") else "p6" if speed.startswith("slow") else "p4"]
    elif device == "qsv":
        args += ["-preset", speed if speed in ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow") else "medium"]
    elif device == "amf":
        args += ["-quality", "speed" if speed in ("ultrafast", "superfast", "veryfast", "fast") else "quality" if speed.startswith("slow") else "balanced"]
    if mode == "crf":
        if device == "cpu":
            args += ["-crf", str(quality)]
            if codec == "vp9":
                args += ["-b:v", "0"]
        elif device == "nvenc":
            args += ["-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
        elif device == "qsv":
            args += ["-global_quality", str(quality)]
        else:
            args += ["-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality)]
    else:
        rate = _rate(item.get("bitrate"))
        args += ["-b:v", rate]
        if mode == "cbr":
            args += ["-minrate", rate, "-maxrate", rate, "-bufsize", rate]
            if device in ("nvenc", "amf"):
                args += ["-rc", "cbr"]
            elif device == "cpu" and codec == "h264":
                args += ["-x264-params", "nal-hrd=cbr:force-cfr=1"]
        else:
            args += ["-maxrate", _rate(item.get("maxrate"), rate), "-bufsize", rate]
            if device in ("nvenc", "amf"):
                args += ["-rc", "vbr" if device == "nvenc" else "vbr_peak"]
    if mode == "crf" and item.get("maxrate"):
        args += ["-maxrate", _rate(item["maxrate"]), "-bufsize", _rate(item["maxrate"])]
    if fmt == "webm":
        args += ["-c:a", "libopus", "-b:a", "160k"]
    else:
        args += ["-c:a", "aac", "-b:a", "192k"]
        if fmt in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]
            if codec == "hevc":
                args += ["-tag:v", "hvc1"]
    args += ["-ar", "48000", "-ac", "2", "-f", "matroska" if fmt == "mkv" else fmt]
    return args, fmt


def advanced_render(base_video: str, settings: dict, output: str, progress,
                    cancel: threading.Event) -> str:
    """Apply project layers and export atomically without replacing files."""
    engine._check_cancel(cancel)
    settings = {**settings, **(settings.get("advanced") or {})}
    info = engine.probe(base_video)
    width, height, duration = info["width"], info["height"], info["duration"]
    if min(width, height) < 2 or duration <= 0:
        raise ValueError("Nguồn xuất phải là video có thời lượng hợp lệ.")
    target = engine._destination(output)
    encoding, fmt = _encoding(settings, target.suffix, progress)
    with tempfile.TemporaryDirectory(prefix=".vei-layers-", dir=engine.temporary_parent(target.parent)) as temporary:
        folder = Path(temporary)
        args = ["-i", info["path"]]
        graph = []
        current = "0:v:0"
        serial, input_index = 0, 1

        def apply(filters: str):
            nonlocal current, serial
            serial += 1
            label = f"v{serial}"
            graph.append(f"[{current}]{filters}[{label}]")
            current = label

        def static_input(path: Path) -> int:
            nonlocal input_index
            index = input_index
            input_index += 1
            args.extend(["-loop", "1", "-i", str(path)])
            return index

        apply("setpts=PTS-STARTPTS,setsar=1")
        selected = str(settings.get("filter_preset") or settings.get("filter") or "none")
        intensity = float(settings.get("filter_intensity", 0.6))
        if intensity > 1:
            intensity /= 100
        if not math.isfinite(intensity) or not 0 <= intensity <= 1:
            raise ValueError("Độ mạnh hiệu ứng phải từ 0 đến 100%.")
        amount = intensity
        selected = {"tv": "crt", "vintage_film": "vintage"}.get(selected, selected)
        simple_filters = {
            "none": "null", "vignette": f"vignette=angle={amount*math.pi/4:.9g}",
            "vintage": f"eq=saturation={1-0.6*amount:g}:contrast={1+0.1*amount:g},colorbalance=rs={0.14*amount:g}:bs={-0.12*amount:g}",
            "grain": f"noise=alls={int(28*amount)}:allf=t+u:all_seed=7",
            "crt": f"noise=alls={int(12*amount)}:allf=t,eq=saturation={1-0.25*amount:g},drawgrid=w=iw:h=4:t=1:c=black@{amount*0.35:g}",
            "stripe": f"drawgrid=w=iw:h=6:t=1:c=black@{amount*0.35:g}",
            "sharpen": f"unsharp=5:5:{amount*1.6:g}:5:5:0",
            "white_balance": f"colorbalance=rs={0.1*amount:g}:bs={-0.1*amount:g}",
            "grayscale": "hue=s=0", "saturation": f"eq=saturation={1+amount:g}",
            "bright_contrast": f"eq=brightness={amount*0.1:g}:contrast={1+amount*0.25:g}",
            "lift_gamma_gain": f"eq=gamma={1+amount*0.15:g}:brightness={amount*0.03:g}:contrast={1+amount*0.1:g}",
            "lightleak": f"colorbalance=rs={amount*0.2:g}:gs={amount*0.06:g}:bs={-amount*0.03:g}"}
        if selected == "glow" and amount:
            serial += 1
            graph.extend([f"[{current}]split=2[glowbase{serial}][glowsource{serial}]",
                          f"[glowsource{serial}]gblur=sigma={2+amount*6:g},eq=brightness={amount*0.12:g}[glowblur{serial}]",
                          f"[glowbase{serial}][glowblur{serial}]blend=all_expr='A*{1-amount*0.45:g}+B*{amount*0.45:g}'[v{serial}]"])
            current = f"v{serial}"
        elif selected in simple_filters:
            if selected != "none" and amount:
                apply(simple_filters[selected])
        else:
            raise ValueError(f"Bộ lọc không hợp lệ: {selected}")

        if settings.get("filter_layers"):
            from color_filters import append_layers
            current = append_layers(graph,current,settings["filter_layers"],duration)

        if settings.get("zoom_enabled"):
            amount = _num(settings, "zoom_amount", 0.12, 0, 3)
            if amount >= 1:
                amount = amount - 1
            period = _num(settings, "zoom_period", 6, 0.2, 3600)
            hold = _num(settings, "zoom_hold", 0, 0, 3)
            if hold >= period:
                raise ValueError("Chu kỳ zoom cần dài hơn thời gian giữ zoom.")
            fps = _source_fps(info["path"])
            phase = f"mod(on/{fps:g},{period:g})"
            ramp = (period - hold) / 2
            curve = (f"if(lt({phase},{ramp:g}),(1-cos(PI*{phase}/{ramp:g}))/2,"
                     f"if(lt({phase},{ramp+hold:g}),1,(1-cos(PI*({period:g}-{phase})/{ramp:g}))/2))")
            apply(f"zoompan=z='1+{amount:g}*({curve})':"
                  f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps={fps:g}")

        for number, zone in enumerate(settings.get("blur_zones") or []):
            engine._check_cancel(cancel)
            if not zone.get("enabled", True):
                continue
            start, end, enable = _timing(zone, duration)
            x, y = int(_num(zone, "x", 0, 0, width-2)), int(_num(zone, "y", 0, 0, height-2))
            bw, bh = int(_num(zone, "width", width//4, 2, width)), int(_num(zone, "height", height//4, 2, height))
            if x + bw > width or y + bh > height:
                raise ValueError(f"Vùng mờ {number+1} nằm ngoài khung {width} × {height}.")
            strength = _num(zone, "strength", 12, 0.1, 100)
            feather = _num(zone, "feather", 0, 0, min(bw, bh)//2)
            shape = str(zone.get("shape") or "rect")
            if shape not in ("rect", "ellipse"):
                raise ValueError("Vùng mờ chỉ hỗ trợ chữ nhật và bầu dục.")
            serial += 1
            graph.append(f"[{current}]split=2[clear{serial}][region{serial}]")
            graph.append(f"[region{serial}]crop={bw}:{bh}:{x}:{y}:exact=1,gblur=sigma={strength:g}[blur{serial}]")
            patch = f"blur{serial}"
            if shape == "ellipse" or feather or zone.get("corner"):
                from PIL import Image, ImageDraw, ImageFilter
                mask = Image.new("L", (bw*2, bh*2), 0)
                painter = ImageDraw.Draw(mask)
                inset = min(max(0, round(feather*2)), max(0, min(bw,bh)-2))
                box = (inset, inset, bw*2-1-inset, bh*2-1-inset)
                if shape == "ellipse":
                    painter.ellipse(box, fill=255)
                else:
                    painter.rounded_rectangle(box, radius=max(0, float(zone.get("corner") or 0))*2, fill=255)
                if feather:
                    mask = mask.filter(ImageFilter.GaussianBlur(feather))
                mask = mask.resize((bw, bh), Image.Resampling.LANCZOS)
                mask_path = folder / f"mask_{number}.png"
                mask.save(mask_path)
                mask_index = static_input(mask_path)
                graph.append(f"[{mask_index}:v:0]format=gray[mask{serial}]")
                graph.append(f"[{patch}]format=rgba[blurcolor{serial}]")
                graph.append(f"[blurcolor{serial}][mask{serial}]alphamerge[blurpatch{serial}]")
                patch = f"blurpatch{serial}"
            graph.append(f"[clear{serial}][{patch}]overlay={x}:{y}:enable='{enable}':eof_action=repeat:shortest=0[v{serial}]")
            current = f"v{serial}"

        for number, item in enumerate(settings.get("image_layers") or []):
            engine._check_cancel(cancel)
            if not item.get("enabled", True):
                continue
            source = engine._source(item.get("path") or "")
            start, end, enable = _timing(item, duration)
            animated = source.suffix.lower() in (".gif", ".webp")
            transformed = bool(item.get("rotation") or item.get("flip_horizontal") or item.get("flip_vertical"))
            if not animated:
                # Share resize rounding and rotation bounds with the GUI.
                transformed_path = folder / f"image_transformed_{number}.png"
                _image_layer_image(dict(item, path=str(source))).save(transformed_path)
                source = transformed_path
            index = input_index
            input_index += 1
            looping = bool(item.get("loop", True))
            animation_duration = 0
            if animated:
                if not looping:
                    animation_duration = sum(_animation_durations(source))
                    if animation_duration:
                        # The demuxer's EOF follows the last frame timestamp;
                        # retain that frame for its duration, then hide it.
                        enable += f"*lt(t,{start + animation_duration:.9g})"
                args += ["-stream_loop", "-1" if looping else "0", "-i", str(source)]
            else:
                args += ["-loop", "1", "-i", str(source)]
            iw = int(_num(item, "width", min(160, width), 2, 16384))
            ih = int(_num(item, "height", 0, 0, 16384))
            opacity = _num(item, "opacity", 1, 0, 1)
            serial += 1
            geometry = "null" if not animated else f"scale={iw}:{ih if ih else -1}"
            if transformed and animated:
                if item.get("flip_horizontal"):
                    geometry += ",hflip"
                if item.get("flip_vertical"):
                    geometry += ",vflip"
                angle = _num(item, "rotation", 0, -180, 180) * math.pi / 180
                if angle:
                    geometry += f",format=rgba,rotate={angle:.10g}:ow=rotw({angle:.10g}):oh=roth({angle:.10g}):c=none"
            graph.append(f"[{index}:v:0]{geometry},format=rgba,"
                         f"colorchannelmixer=aa={opacity:g},setpts=PTS-STARTPTS+{start:.9g}/TB[image{serial}]")
            px, py = _position(item, width, height)
            from layer_blend import append_blend_overlay
            append_blend_overlay(graph, current, f"image{serial}", width=width, height=height,
                                 fps=_source_fps(info["path"]), duration=duration, x=px, y=py, enable=enable,
                                 eof_action='repeat' if looping or not animated or animation_duration else 'pass',
                                 mode=item.get('blend_mode','normal'), output=f"v{serial}", prefix=f"iblend{serial}")
            current = f"v{serial}"

        for number, item in enumerate(settings.get("text_layers") or []):
            engine._check_cancel(cancel)
            if not item.get("enabled", True) or not str(item.get("text") or "").strip():
                continue
            start, end, enable = _timing(item, duration)
            image_path = folder / f"text_{number}.png"
            _text_png(item, image_path, width)
            index = static_input(image_path)
            px, py = _position(item, width, height, moving=True)
            serial += 1
            from layer_blend import append_blend_overlay
            append_blend_overlay(graph, current, f"{index}:v:0", width=width, height=height,
                                 fps=_source_fps(info["path"]), duration=duration, x=px, y=py, enable=enable,
                                 eof_action='repeat', mode=item.get('blend_mode','normal'),
                                 output=f"v{serial}", prefix=f"tblend{serial}")
            current = f"v{serial}"

        tracks = resolved_subtitle_tracks(settings)
        if tracks is not None:
            from caption_raster import caption_stream
            for track in tracks:
                engine._check_cancel(cancel)
                if not track["enabled"] or not track["path"]:
                    continue
                source = engine._source(track["path"])
                role_folder = folder / ("captions-" + track["role"])
                role_folder.mkdir()
                stream = caption_stream(source, track["style"], role_folder, width, height, duration, cancel)
                index = input_index
                input_index += 1
                args.extend(["-f", "concat", "-safe", "0", "-i", str(stream)])
                serial += 1
                graph.append(f"[{current}][{index}:v:0]overlay=x=0:y=0:format=auto:eof_action=repeat:shortest=0[v{serial}]")
                current = f"v{serial}"
        elif settings.get("subtitle_path"):
            source = engine._source(settings["subtitle_path"])
            style=settings.get("subtitle_style") or {}
            if "text_effect" in style or "bg_fit" in style:
                from caption_raster import caption_stream
                stream=caption_stream(source,style,folder,width,height,duration,cancel)
                index=input_index
                input_index+=1
                args.extend(["-f","concat","-safe","0","-i",str(stream)])
                serial+=1
                graph.append(f"[{current}][{index}:v:0]overlay=x=0:y=0:format=auto:eof_action=repeat:shortest=0[v{serial}]")
                current=f"v{serial}"
            else:
                _caption_ass(source, style, folder, width, height, cancel)
                from ui_theme import font_path, font_directory
                if font_path(style.get('font','')):
                    target_fonts=folder/'fonts'; target_fonts.mkdir(exist_ok=True)
                    for path in font_directory().glob('*.ttf'): shutil.copy2(path,target_fonts/path.name)
                    apply("ass=filename='captions.ass':fontsdir='fonts'")
                else:
                    apply("ass=filename='captions.ass'")
        border = int(_num(settings, "frame_border", 0, 0, max(0, min(width, height)//2-1)))
        if border:
            color = _color(settings.get("frame_border_color", "#ffffff"))
            apply(f"drawbox=x=0:y=0:w=iw:h=ih:color={color}:t={border}")
        fade_in = _num(settings, "fade_in", 0, 0, duration)
        fade_out = _num(settings, "fade_out", 0, 0, duration)
        if fade_in:
            apply(f"fade=t=in:st=0:d={fade_in:g}")
        if fade_out:
            apply(f"fade=t=out:st={duration-fade_out:g}:d={fade_out:g}")
        apply("format=yuv420p")
        audio = "0:a:0?"
        audio_sources: list[str] = []
        source_speed = _num(settings, "_source_audio_speed", 1, .25, 4)
        source_start = _num(settings, "_source_audio_start", 0, 0, 86400)
        source_end = _num(settings, "_source_audio_end", 0, 0, 86400)
        def external_timeline(speed, start, end=0):
            filters = [f"atrim=start={start:g}" + (f":end={end:g}" if end else ""), "asetpts=PTS-STARTPTS"]
            if speed != 1:
                filters.append(engine._tempo(speed))
            return filters
        def audio_track(label, gain, timeline=()):
            return ["aresample=48000", "aformat=channel_layouts=stereo", *timeline,
                    "asetpts=PTS-STARTPTS", f"volume={gain:g}", "apad", f"atrim=duration={duration:.9g}"]
        def track_fades(prefix):
            fades = []
            for direction in ("in", "out"):
                seconds = min(duration, _num(settings, prefix + "_fade_" + direction, 0, 0, 30))
                if seconds:
                    fades.append(f"afade=t={direction}:st={0 if direction=='in' else duration-seconds:g}:d={seconds:g}")
            return fades
        stems = settings.get("source_stem_paths") or {}
        gains = settings.get("source_stem_volumes") or {}
        if not isinstance(stems, dict) or not isinstance(gains, dict):
            raise ValueError("Các lớp âm gốc và âm lượng chưa hợp lệ.")
        stem_labels = []
        if stems and settings.get("source_audio_enabled", True) and not settings.get("mute"):
            for role in ("vocals", "drums", "bass", "other", "background"):
                path = stems.get(role)
                if not path:
                    continue
                stem = engine._source(path)
                if not engine.probe(str(stem))["has_audio"]:
                    raise ValueError(f"Lớp âm gốc {role} không có âm thanh.")
                gain = _num(gains, role, 1, 0, 4)
                index = input_index
                input_index += 1
                args += ["-i", str(stem)]
                label = "stem" + role
                filters = audio_track(label, gain, external_timeline(source_speed, source_start, source_end))
                graph.append(f"[{index}:a:0]" + ",".join(filters) + f"[{label}]")
                stem_labels.append(label)
            if not stem_labels:
                raise ValueError("Không tìm thấy lớp âm gốc đã tách.")
            graph.append("".join(f"[{label}]" for label in stem_labels) +
                         f"amix=inputs={len(stem_labels)}:duration=first:dropout_transition=0:normalize=0[sourceMix]")
            graph.append("[sourceMix]" + ",".join(["anull", *track_fades("source")]) + "[sourceAudio]")
            audio_sources.append("sourceAudio")
        background_path = settings.get("background_path")
        if stems or not settings.get("source_audio_enabled", True):
            base_audio = None
        elif background_path:
            background = engine._source(background_path)
            if not engine.probe(str(background))["has_audio"]:
                raise ValueError("Tệp âm nền không có âm thanh.")
            background_index = input_index
            input_index += 1
            args += ["-i", str(background)]
            base_audio = f"{background_index}:a:0"
        else:
            base_audio = "0:a:0" if info["has_audio"] else None
        if base_audio:
            if background_path:
                original_volume = _num(settings, "background_volume", 1, 0, 4)
            elif settings.get("mute"):
                original_volume = 0
            else:
                original_volume = _num(settings, "original_volume", settings.get("volume", 1), 0, 4)
            timeline = external_timeline(source_speed, source_start, source_end) if background_path else []
            filters = audio_track("mixbase", original_volume, timeline) + track_fades("source")
            graph.append(f"[{base_audio}]" + ",".join(filters) + "[mixbase]")
            audio_sources.append("mixbase")
        for kind in ("voice", "music"):
            if kind == "voice" and not settings.get("voice_audio_enabled", True):
                continue
            path = settings.get(kind + "_path")
            if not path:
                continue
            source = engine._source(path)
            if not engine.probe(str(source))["has_audio"]:
                raise ValueError(f"Tệp {'giọng đọc' if kind == 'voice' else 'nhạc nền'} không có âm thanh.")
            index = input_index
            input_index += 1
            if kind == "music" and settings.get("music_loop", True):
                args += ["-stream_loop", "-1"]
            args += ["-i", str(source)]
            volume = _num(settings, kind + "_volume", 1 if kind == "voice" else 0.25, 0, 4)
            delay = _num(settings, "voice_delay", 0, 0, 3600) if kind == "voice" else 0
            timeline = []
            if kind == "voice":
                voice_speed = _num(settings, "voice_audio_speed", source_speed, .25, 4)
                voice_start = _num(settings, "voice_audio_start", source_start, 0, 86400)
                timeline = external_timeline(voice_speed, voice_start, source_end if "voice_audio_start" not in settings else 0)
            filters = audio_track("mix" + kind, volume, timeline)
            filters += [f"adelay={round(delay*1000)}:all=1"]
            if kind == "voice":
                filters += track_fades("voice")
            graph.append(f"[{index}:a:0]" + ",".join(filters) + f"[mix{kind}]")
            audio_sources.append("mix" + kind)
        from audio_layers import append_audio_layers
        layer_audio, input_index = append_audio_layers(args, graph, settings.get('audio_layers') or [],
                                                       duration, input_index, cancel=cancel)
        audio_sources.extend(layer_audio)
        if audio_sources:
            effect_options = dict(settings.get("audio_filters") or {})
            preset = str(settings.get("audio_effect") or "none")
            if preset in ("denoise", "noise_reduction"):
                effect_options["noise_reduction"] = True
            elif preset in ("compressor", "reverb", "normalize"):
                effect_options[preset] = True
            elif preset in ("bass", "treble"):
                effect_options[preset] = 6
            elif preset != "none":
                raise ValueError("Hiệu ứng âm thanh không hợp lệ.")
            filters = ["anull"]
            if len(audio_sources) > 1:
                graph.append("".join(f"[{label}]" for label in audio_sources) +
                             f"amix=inputs={len(audio_sources)}:duration=first:dropout_transition=0:normalize=0[mixed]")
                audio_input = "mixed"
            else:
                audio_input = audio_sources[0]
            if effect_options.get("noise_reduction"):
                filters.append("afftdn=nf=-25")
            if effect_options.get("compressor"):
                filters.append("acompressor=threshold=0.125:ratio=3:attack=20:release=250:makeup=1.5")
            if effect_options.get("equalizer"):
                gain = _num(effect_options, "equalizer", 0, -20, 20)
                filters.append(f"equalizer=f=1000:t=o:w=2:g={gain:g}")
            if effect_options.get("bass"):
                filters.append(f"bass=g={_num(effect_options,'bass',0,-20,20):g}")
            if effect_options.get("treble"):
                filters.append(f"treble=g={_num(effect_options,'treble',0,-20,20):g}")
            if effect_options.get("reverb"):
                filters.append("aecho=0.8:0.9:80|160:0.18|0.1")
            if effect_options.get("normalize"):
                filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")
            filters.append("alimiter=limit=0.98:level=0")
            if fade_in:
                filters.append(f"afade=t=in:st=0:d={fade_in:g}")
            if fade_out:
                filters.append(f"afade=t=out:st={duration-fade_out:g}:d={fade_out:g}")
            filters += ["apad", f"atrim=duration={duration:.9g}"]
            graph.append(f"[{audio_input}]" + ",".join(filters) + "[audio]")
            audio = "[audio]"
        elif _AUDIO_REFERENCE_KEYS.intersection(settings):
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.9g}[audio]")
            audio = "[audio]"
        # Limiting filter threads prevents excessive resource use with many PNG
        # inputs and avoids filter graph stalls on machines with many cores.
        args += ["-filter_complex_threads", "2", "-filter_complex", ";".join(graph),
                 "-map", f"[{current}]", "-map", audio, "-t", f"{duration:.9f}", *encoding]
        temporary_output = folder / ("render." + fmt)
        args.append(str(temporary_output))
        engine._ffmpeg(args, duration, lambda percent, message: engine._report(progress, percent*0.99,
                       "Đang kết xuất các lớp và hiệu ứng…"), cancel, "Kết xuất…", cwd=folder)
        engine._check_cancel(cancel)
        engine._publish(temporary_output, target)
    engine._report(progress, 100, "Đã xuất video với các lớp và hiệu ứng.")
    return str(target)
