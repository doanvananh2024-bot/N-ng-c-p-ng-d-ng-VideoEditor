"""Independent Pillow text treatments and matching preview/export motion.

Raster effects operate on bounded grayscale glyph masks. The renderer applies
layer flip/rotation after this helper; it must not transform the image twice.
"""
from __future__ import annotations

from functools import lru_cache
import math
import os
from pathlib import Path
import re

EFFECTS = ("outline", "outline_thick", "shadow", "glow", "hollow", "soft_shadow",
           "retro_offset", "double_border", "long_shadow", "none")
MOTIONS = ("none", "around", "inside", "diagonal", "auto", "horizontal", "left", "right", "up", "down")
BACKGROUND_FITS = ("hug", "box")
_MAX_PIXELS = 40_000_000


def _number(item, key, default, low, high):
    try:
        value = float(item.get(key, default))
    except (ValueError, TypeError) as exc:
        raise ValueError("Thông số chữ không hợp lệ: " + key) from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Thông số chữ nằm ngoài giới hạn: " + key)
    return value


def _color(value, default="#ffffff"):
    from PIL import ImageColor
    try:
        color = ImageColor.getrgb(str(value or default))
        return color[:3]
    except (ValueError, TypeError) as exc:
        raise ValueError("Màu chữ không hợp lệ.") from exc


@lru_cache(maxsize=64)
def _font_path(name, bold=False):
    from ui_theme import font_path
    bundled=font_path(name,bold)
    if bundled: return bundled
    candidate = Path(name)
    if candidate.is_file() and candidate.suffix.lower() in (".ttf", ".otf", ".ttc"):
        return str(candidate)
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    wanted = str(name).casefold().strip()
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                    candidates = []
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        family, path, _ = winreg.EnumValue(key, index)
                        family = re.sub(r"\s*\(.*?\)", "", family).casefold()
                        if family not in {wanted, wanted + (" bold" if bold else " regular")}:
                            continue
                        file = Path(path)
                        if not file.is_absolute():
                            file = fonts / file
                        if file.is_file():
                            candidates.append((0 if ("bold" in family) == bool(bold) else 1, str(file)))
                    if candidates:
                        return min(candidates)[1]
            except OSError:
                pass
    known = {"arial": "arialbd.ttf" if bold else "arial.ttf",
             "segoe ui": "segoeuib.ttf" if bold else "segoeui.ttf",
             "times new roman": "timesbd.ttf" if bold else "times.ttf",
             "tahoma": "tahomabd.ttf" if bold else "tahoma.ttf",
             "calibri": "calibrib.ttf" if bold else "calibri.ttf"}
    file = fonts / known.get(wanted, "arialbd.ttf" if bold else "arial.ttf")
    return str(file) if file.is_file() else "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"


def _wrap(text, font, width):
    from PIL import ImageDraw, Image
    if not width:
        return text
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            proposed = line + (" " if line else "") + word
            if line and draw.textlength(proposed, font=font) > width:
                lines.append(line)
                line = ""
            # Break an overlong token instead of allocating a huge single row.
            if draw.textlength(word, font=font) > width:
                for letter in word:
                    if line and draw.textlength(line + letter, font=font) > width:
                        lines.append(line)
                        line = ""
                    line += letter
            else:
                line += (" " if line else "") + word
        lines.append(line)
    return "\n".join(lines)


def render_text(item: dict, max_width: int):
    """Return a raw RGBA text raster for preview/export, without transforms."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
    if not isinstance(item, dict) or not isinstance(max_width, int) or isinstance(max_width, bool) or not 1 <= max_width <= 32768:
        raise ValueError("Khung chữ cần chiều rộng từ 1 đến 32768 pixel.")
    text = str(item.get("text", ""))
    if len(text) > 20000:
        raise ValueError("Một lớp chữ chỉ được dài tối đa 20.000 ký tự.")
    if item.get("uppercase"):
        text = text.upper()
    effect = str(item.get("text_effect", "outline"))
    fit = str(item.get("bg_fit", "hug"))
    if effect not in EFFECTS or fit not in BACKGROUND_FITS:
        raise ValueError("Hiệu ứng chữ hoặc kiểu nền không được hỗ trợ.")
    size = round(_number(item, "font_size", 36, 6, 512))
    outline = round(_number(item, "outline", 1, 0, 32))
    opacity = _number(item, "opacity", 1, 0, 1)
    bg_opacity = _number(item, "bg_opacity", 0, 0, 1)
    radius = _number(item, "bg_radius", 0, 0, 200)
    padding = round(_number(item, "padding", size // 5, 0, 200))
    wrap_width = round(_number(item, "wrap_width", 0, 0, max(16384, max_width)))
    font = ImageFont.truetype(_font_path(str(item.get("font") or "Arial"), bool(item.get("bold"))), size)
    text = _wrap(text, font, wrap_width)
    spacing = max(2, size // 6)
    alignment = str(item.get("text_align", "left"))
    if alignment not in ("left", "center", "right"):
        raise ValueError("Căn chữ cần chọn trái, giữa hoặc phải.")
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    bbox = draw.multiline_textbbox((0, 0), text or " ", font=font, spacing=spacing, align=alignment)
    ink_width, ink_height = max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])
    stroke = max(1, outline)
    depth = max(3, min(32, round(size * .3)))
    margin = max(stroke * 3 + 2, depth + 2) if effect != "none" else 0
    width, height = ink_width + 2 * (padding + margin), ink_height + 2 * (padding + margin)
    if fit == "box":
        box_width = round(_number(item, "width", width, 2, 16384))
        box_height = round(_number(item, "height", height, 2, 16384))
        width, height = max(width, box_width), max(height, box_height)
    if width > 32768 or height > 32768 or width * height > _MAX_PIXELS:
        raise ValueError("Lớp chữ quá lớn. Giảm cỡ chữ hoặc chia thành nhiều lớp.")
    # Effects use fixed canvas bounds so their coordinates match the fill.
    mask = Image.new("L", (width, height), 0)
    md = ImageDraw.Draw(mask)
    x = padding + margin - bbox[0]
    if alignment == "center":
        x = (width - ink_width) // 2 - bbox[0]
    elif alignment == "right":
        x = width - padding - margin - ink_width - bbox[0]
    y = padding + margin - bbox[1]
    md.multiline_text((x, y), text, font=font, spacing=spacing, fill=255, align=alignment)
    foreground = _color(item.get("color"))
    edge = _color(item.get("outline_color"), "#000000")
    shadow = _color(item.get("shadow_color"), item.get("outline_color") or "#000000")
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if bg_opacity:
        ImageDraw.Draw(image).rounded_rectangle((0, 0, width - 1, height - 1), radius=min(radius, width / 2, height / 2),
                                                fill=(*_color(item.get("bg_color"), "#000000"), round(bg_opacity * 255)))
    def composite(layer_mask, color, alpha=1):
        tint = Image.new("RGBA", (width, height), (*color, 0))
        tint.putalpha(layer_mask.point(lambda value: round(value * alpha)))
        image.alpha_composite(tint)
    def finished():
        image.putalpha(image.getchannel("A").point(lambda value:round(value*opacity)))
        return image
    def dilate(amount):
        outlined = Image.new("L", (width, height), 0)
        ImageDraw.Draw(outlined).multiline_text((x, y), text, font=font, spacing=spacing,
                                               fill=255, stroke_width=amount, stroke_fill=255, align=alignment)
        return outlined
    def offset(layer_mask, dx, dy):
        shifted = Image.new("L", (width, height), 0)
        shifted.paste(layer_mask, (dx, dy))
        return shifted
    if effect == "outline":
        if outline:
            composite(dilate(outline), edge)
    elif effect == "outline_thick":
        composite(dilate(max(2, min(64, stroke * 3))), edge)
    elif effect == "shadow":
        composite(offset(mask, stroke + 2, stroke + 2), shadow, .85)
    elif effect == "glow":
        glow = dilate(max(1, stroke)).filter(ImageFilter.GaussianBlur(max(2, size * .10)))
        composite(glow, foreground, .65)
        composite(dilate(1), foreground, .35)
    elif effect == "hollow":
        composite(ImageChops.subtract(dilate(stroke), mask), foreground)
        return finished()
    elif effect == "soft_shadow":
        composite(offset(dilate(stroke), stroke + 3, stroke + 3).filter(ImageFilter.GaussianBlur(max(2, size * .08))), shadow, .8)
    elif effect == "retro_offset":
        composite(offset(dilate(stroke), max(4, size // 8), max(2, size // 12)), edge)
        composite(dilate(stroke), foreground)
        composite(mask, foreground)
        return finished()
    elif effect == "double_border":
        composite(dilate(min(64, stroke * 3 + 2)), edge)
        composite(dilate(stroke * 2 + 1), foreground)
        composite(dilate(stroke), edge)
    elif effect == "long_shadow":
        trail = Image.new("L", (width, height), 0)
        for step in range(1, depth + 1):
            trail = ImageChops.lighter(trail, offset(mask, step, step))
        composite(trail, shadow, .8)
    composite(mask, foreground)
    return finished()


def _motion_parameters(mode, width, height, speed):
    if mode not in MOTIONS:
        raise ValueError("Kiểu chữ chạy không được hỗ trợ.")
    for label, value in (("width", width), ("height", height), ("speed", speed)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Thông số chữ chạy phải lớn hơn 0: " + label)
    if width > 32768 or height > 32768 or not 1 <= speed <= 2000:
        raise ValueError("Kích thước hoặc tốc độ chữ chạy vượt giới hạn.")


def position_motion(mode, time, width, height, iw, ih, speed=80):
    """Numeric motion coordinates; caller adds anchor offsets for static text."""
    _motion_parameters(mode, width, height, speed)
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
               for value in (time, iw, ih)) or iw <= 0 or ih <= 0 or time < 0:
        raise ValueError("Thời gian hoặc kích thước lớp chữ không hợp lệ.")
    if mode == "none":
        return 0.0, 0.0
    if mode == "auto":
        cycle = time % 45
        mode = "around" if cycle < 15 else "inside" if cycle < 30 else "diagonal"
        time = cycle % 15
    a, b = max(0, width - iw), max(0, height - ih)
    def bounce(elapsed, span):
        return 0.0 if not span else abs((elapsed % (2 * span)) - span)
    if mode in ("horizontal", "left"):
        return width - (time * speed) % (width + iw), b / 2
    if mode == "right":
        return -iw + (time * speed) % (width + iw), b / 2
    if mode in ("up", "down"):
        return a / 2, height - (time * speed) % (height + ih) if mode == "up" else -ih + (time * speed) % (height + ih)
    if mode == "inside":
        return bounce(time * speed, a), b / 2
    if mode == "diagonal":
        return bounce(time * speed, a), bounce(time * speed * .7, b)
    perimeter = 2 * (a + b)
    if not perimeter:
        return 0.0, 0.0
    distance = time * speed % perimeter
    if distance < a:
        return distance, 0.0
    if distance < a + b:
        return a, distance - a
    if distance < 2 * a + b:
        return 2 * a + b - distance, b
    return 0.0, perimeter - distance


def motion_expressions(mode, width, height, iw="overlay_w", ih="overlay_h", speed=80, time="t"):
    """FFmpeg overlay coordinates implementing the same numeric trajectories."""
    _motion_parameters(mode, width, height, speed)
    for value in (iw, ih, time):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("Biểu thức chuyển động không hợp lệ.")
        if isinstance(value, str) and not re.fullmatch(r"[A-Za-z0-9_+*/(),. -]{1,240}", value):
            raise ValueError("Biểu thức chuyển động chứa ký tự không hỗ trợ.")
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise ValueError("Biểu thức chuyển động cần số hữu hạn.")
    a, b = f"max(0,{width:g}-({iw}))", f"max(0,{height:g}-({ih}))"
    elapsed = f"({time})"
    if mode == "none":
        return "0", "0"
    if mode == "auto":
        cycle = f"mod({elapsed},45)"
        local = f"mod({elapsed},15)"
        pairs = [motion_expressions(value, width, height, iw, ih, speed, local) for value in ("around", "inside", "diagonal")]
        return tuple(f"if(lt({cycle},15),{pairs[0][i]},if(lt({cycle},30),{pairs[1][i]},{pairs[2][i]}))" for i in (0, 1))
    if mode in ("horizontal", "left"):
        return f"{width:g}-mod({elapsed}*{speed:g},{width:g}+({iw}))", f"({b})/2"
    if mode == "right":
        return f"-({iw})+mod({elapsed}*{speed:g},{width:g}+({iw}))", f"({b})/2"
    if mode in ("up", "down"):
        return f"({a})/2", f"{height:g}-mod({elapsed}*{speed:g},{height:g}+({ih}))" if mode == "up" else f"-({ih})+mod({elapsed}*{speed:g},{height:g}+({ih}))"
    def bounce(span, velocity):
        return f"if(lte({span},0),0,abs(mod({elapsed}*{velocity:g},2*max(0.000001,{span}))-({span})))"
    if mode == "inside":
        return bounce(a, speed), f"({b})/2"
    if mode == "diagonal":
        return bounce(a, speed), bounce(b, speed * .7)
    perimeter = f"2*({a}+{b})"
    distance = f"mod({elapsed}*{speed:g},max(0.000001,{perimeter}))"
    x = f"if(lte({perimeter},0),0,if(lt({distance},{a}),{distance},if(lt({distance},{a}+{b}),{a},if(lt({distance},2*{a}+{b}),2*{a}+{b}-{distance},0))))"
    y = f"if(lte({perimeter},0),0,if(lt({distance},{a}),0,if(lt({distance},{a}+{b}),{distance}-{a},if(lt({distance},2*{a}+{b}),{b},{perimeter}-{distance}))))"
    return x, y
