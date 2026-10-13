"""Shared source-to-canvas geometry for FFmpeg export and editable previews.

Source offsets are fractions of canvas dimensions. Backgrounds cover the
original oriented source; foreground rotation/flips, fit, scale, opacity and
offsets are independent of that background.
"""
from __future__ import annotations

import math
import re


DEFAULTS = dict(background_mode="black", background_color="#000000", background_blur=12,
                source_x=0, source_y=0, source_scale=1, source_scale_x=1, source_scale_y=1, source_rotation=0,
                source_opacity=1, source_flip_vertical=False)


def composition_settings(settings: dict) -> dict:
    result = {**DEFAULTS, **{key: settings[key] for key in DEFAULTS if key in settings}}
    if result["background_mode"] not in {"black", "color", "blur"}:
        raise ValueError("Nền khung cần chọn black, color hoặc blur.")
    color = str(result["background_color"]).strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Màu nền cần có dạng #RRGGBB.")
    result["background_color"] = color.lower()
    for key, low, high in (("background_blur", 1, 30), ("source_x", -1, 1), ("source_y", -1, 1),
                           ("source_scale", .05, 5), ("source_scale_x", .05, 5), ("source_scale_y", .05, 5),
                           ("source_rotation", -180, 180),
                           ("source_opacity", 0, 1)):
        try:
            value = float(result[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Thiết lập {key} không hợp lệ.") from exc
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Thiết lập {key} phải từ {low:g} đến {high:g}.")
        result[key] = value
    result["source_flip_vertical"] = bool(result["source_flip_vertical"])
    return result


def canvas_size(media: dict, settings: dict) -> tuple[int, int]:
    width, height = int(media.get("width", 1280)), int(media.get("height", 720))
    if int(settings.get("rotation", 0)) % 180 == 90:
        width, height = height, width
    width = int(settings.get("width", 0)) or width
    height = int(settings.get("height", 0)) or height
    return max(2, width // 2 * 2), max(2, height // 2 * 2)


def _nearest(value: float) -> int:
    return math.floor(value + .5)


def fitted_size(source_width: int, source_height: int, width: int, height: int, fit: str) -> tuple[int, int]:
    """Match FFmpeg aspect-fit dimensions with even-pixel normalization."""
    if min(source_width, source_height, width, height) <= 0:
        raise ValueError("Nguồn và khung phải có kích thước hợp lệ.")
    if fit not in {"contain", "cover"}:
        raise ValueError("Kiểu khung hình phải là contain hoặc cover.")
    factor = (min if fit == "contain" else max)(width / source_width, height / source_height)
    fitted = (_nearest(source_width * factor), _nearest(source_height * factor))
    if fit == "contain":
        return tuple(max(2, value // 2 * 2) for value in fitted)
    return tuple(max(2, (value + 1) // 2 * 2) for value in fitted)


def source_layout(source_width: int, source_height: int, width: int, height: int, settings: dict) -> dict:
    options = composition_settings(settings)
    rotation = int(settings.get("rotation", 0))
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Góc xoay phải là 0, 90, 180 hoặc 270 độ.")
    rotated_w, rotated_h = (source_height, source_width) if rotation in (90, 270) else (source_width, source_height)
    fitted_w, fitted_h = fitted_size(rotated_w, rotated_h, width, height, settings.get("fit", "contain"))
    pre_w = max(2, _nearest(fitted_w * options["source_scale"] * options["source_scale_x"] / 2) * 2)
    pre_h = max(2, _nearest(fitted_h * options["source_scale"] * options["source_scale_y"] / 2) * 2)
    angle = math.radians(options["source_rotation"])
    def even_extent(value):
        return max(2, (math.ceil(value-1e-8)+1)//2*2)
    foreground_w = even_extent(abs(pre_w*math.cos(angle))+abs(pre_h*math.sin(angle)))
    foreground_h = even_extent(abs(pre_w*math.sin(angle))+abs(pre_h*math.cos(angle)))
    if max(pre_w*pre_h,foreground_w*foreground_h) > 40_000_000:
        raise ValueError("Nguồn phóng to vượt giới hạn 40 triệu pixel. Hãy giảm tỉ lệ hoặc kích thước khung.")
    x = _nearest((width - foreground_w) / 2 + options["source_x"] * width)
    y = _nearest((height - foreground_h) / 2 + options["source_y"] * height)
    background_w, background_h = fitted_size(source_width, source_height, width, height, "cover")
    return dict(foreground=(x, y, foreground_w, foreground_h), pre_rotation=(pre_w,pre_h),
                background=((width - background_w) // 2, (height - background_h) // 2, background_w, background_h),
                options=options, rotation=rotation)


def needs_composition(settings: dict) -> bool:
    options = composition_settings(settings)
    return (options["background_mode"] != "black" or options["source_flip_vertical"]
            or any(options[key] != DEFAULTS[key] for key in ("source_x", "source_y", "source_scale", "source_scale_x", "source_scale_y", "source_rotation", "source_opacity")))


def ffmpeg_composition(source_width: int, source_height: int, width: int, height: int, settings: dict) -> str:
    """Produce a bounded graph from [0:v:0] to [source_composed]."""
    layout = source_layout(source_width, source_height, width, height, settings)
    options = layout["options"]
    x, y, foreground_w, foreground_h = layout["foreground"]
    pre_w, pre_h = layout["pre_rotation"]
    bx, by, background_w, background_h = layout["background"]
    graph = ["[0:v:0]setpts=PTS-STARTPTS,split=2[source_foreground][source_background]"]
    if options["background_mode"] == "blur":
        background = (f"scale={background_w}:{background_h},crop={width}:{height}:{-bx}:{-by},"
                      f"setsar=1,format=gbrp,gblur=sigma={options['background_blur']:g}:steps=3")
    else:
        color = options["background_color"] if options["background_mode"] == "color" else "#000000"
        background = f"scale={width}:{height},setsar=1,format=rgb24,drawbox=color=0x{color[1:]}:t=fill"
    graph.append(f"[source_background]{background}[source_canvas]")
    foreground = []
    if layout["rotation"] == 90:
        foreground.append("transpose=1")
    elif layout["rotation"] == 270:
        foreground.append("transpose=2")
    elif layout["rotation"] == 180:
        foreground.extend(["hflip", "vflip"])
    if settings.get("mirror"):
        foreground.append("hflip")
    if options["source_flip_vertical"]:
        foreground.append("vflip")
    foreground.extend([f"scale={pre_w}:{pre_h}", "setsar=1", "format=rgba"])
    if options["source_rotation"]:
        foreground.append(f"rotate={math.radians(options['source_rotation']):.12g}:ow={foreground_w}:oh={foreground_h}:c=none")
    foreground.append(f"colorchannelmixer=aa={options['source_opacity']:g}")
    graph.append("[source_foreground]" + ",".join(foreground) + "[source_picture]")
    graph.append(f"[source_canvas][source_picture]overlay=x={x}:y={y}:format=rgb:shortest=1:eof_action=pass[source_composed]")
    return ";".join(graph)
