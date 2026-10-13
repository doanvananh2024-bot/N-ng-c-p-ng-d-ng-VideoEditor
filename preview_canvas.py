"""Editable card preview using the independent renderer's actual text raster.

Coordinates are pixels in the exported canvas. Letterboxing, source rotation,
mirroring, media anchors and overlay timing are shared with export semantics.
Color/zoom/fade filters still require the rendered preview action.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget
from source_composition import canvas_size, source_layout, composition_settings


def _qimage(pillow):
    pillow = pillow.convert("RGBA")
    return QImage(pillow.tobytes(), pillow.width, pillow.height,
                  pillow.width * 4, QImage.Format.Format_RGBA8888).copy()


def _number(row, key, default):
    try:
        value = float(row.get(key, default))
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


class EditablePreview(QWidget):
    changed = Signal(str, int, object)
    chosen = Signal(str, int)
    activated = Signal()

    def __init__(self, media, thumbnail="", settings=None, assets=None, parent=None):
        super().__init__(parent)
        self.media = dict(media)
        self.settings = {}
        self.assets = {}
        self.position = 0.0
        self.selected_layer = None
        self._drag = None
        self._live_frame = QImage()
        self._poster = QImage()
        self._images = OrderedDict()
        self._layer_images = OrderedDict()
        self._animations = OrderedDict()
        self._source_canvases = OrderedDict()
        self._text = OrderedDict()
        self._subtitle_key = None
        self._subtitles = []
        self._caption_tracks = []
        self._subtitle_tracks_key = None
        self.setObjectName("editablePreview")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(60, 45)
        self.setToolTip("Nhấp lớp để chọn; kéo để di chuyển; kéo góc để đổi cỡ. "
                        "Giữ Shift để giữ tỉ lệ. Dùng Xem thử 5 giây cho bộ lọc màu, zoom và fade.")
        self.set_content(thumbnail, settings or {}, assets or {})

    def set_content(self, thumbnail, settings, assets=None):
        thumbnail = str(thumbnail or "")
        if thumbnail != getattr(self, "thumbnail", None) or self._poster.isNull():
            self.thumbnail = thumbnail
            self._poster = QImage(thumbnail) if Path(thumbnail).is_file() else QImage()
        self.settings = copy.deepcopy({**settings, **(settings.get("advanced") or {})})
        self.assets = dict(assets or {})
        from advanced_render import resolved_subtitle_tracks
        tracks = resolved_subtitle_tracks(self.settings, self.assets)
        if tracks is not None:
            self.settings["subtitle_tracks"] = tracks
            track_key = []
            for track in tracks:
                try:
                    stamp = Path(track["path"]).stat().st_mtime_ns if track["enabled"] and track["path"] else 0
                except OSError:
                    stamp = 0
                track_key.append((track["role"], track["path"], track["enabled"], stamp,
                                  json.dumps(track["style"], sort_keys=True, ensure_ascii=False, default=str)))
            if track_key != self._subtitle_tracks_key:
                self._subtitle_tracks_key = track_key
                self._caption_tracks = []
                for index, track in enumerate(tracks):
                    rows = []
                    if track["enabled"] and track["path"]:
                        try:
                            from subtitle_tools import load_subtitles
                            rows = load_subtitles(track["path"])
                        except (ValueError, OSError):
                            pass
                    self._caption_tracks.append(dict(track, index=index, rows=rows))
            self._subtitles = []
            self._subtitle_key = None
        else:
            self._caption_tracks = []
            self._subtitle_tracks_key = None
            key = (self.settings.get("subtitle_path") or self.assets.get("translated_subtitle")
                   or self.assets.get("original_subtitle") or "")
            try:
                stamp = Path(key).stat().st_mtime_ns if key else 0
            except OSError:
                stamp = 0
            if (key, stamp) != self._subtitle_key:
                self._subtitle_key = (key, stamp)
                self._subtitles = []
                if stamp:
                    try:
                        from subtitle_tools import load_subtitles
                        self._subtitles = load_subtitles(key)
                    except (ValueError, OSError):
                        pass
        if self.selected_layer:
            kind, index = self.selected_layer
            if index >= len(self.settings.get(kind, [])):
                self.selected_layer = None
        self.update()

    def set_frame(self, frame):
        if not frame.isNull():
            self._live_frame = frame.copy()
            self.update()

    def clear_frame(self):
        self._live_frame = QImage()
        self.update()

    def set_position(self, seconds):
        self.position = max(0, float(seconds))
        self.update()

    def set_subtitle_rows(self, rows, role=None):
        """Use unsaved editor cues for immediate timing/text preview."""
        if role is None:
            self._subtitles = copy.deepcopy(rows)
        else:
            for track in self._caption_tracks:
                if track["role"] == role:
                    track["rows"] = copy.deepcopy(rows)
        self.update()

    def set_selected_layer(self, kind, index):
        self.selected_layer = (kind, index) if 0 <= index < len(self.settings.get(kind, [])) else None
        self.update()

    def canvas_rect(self):
        width, height = canvas_size(self.media, self.settings)
        scale = min(self.width() / width, self.height() / height)
        return QRectF((self.width() - width * scale) / 2,
                      (self.height() - height * scale) / 2, width * scale, height * scale)

    def world_to_view(self, point):
        rect = self.canvas_rect()
        width, height = canvas_size(self.media, self.settings)
        return QPointF(rect.left() + point.x() * rect.width() / width,
                       rect.top() + point.y() * rect.height() / height)

    def view_to_world(self, point):
        rect = self.canvas_rect()
        width, height = canvas_size(self.media, self.settings)
        return QPointF((point.x() - rect.left()) * width / max(1, rect.width()),
                       (point.y() - rect.top()) * height / max(1, rect.height()))

    def _visible(self, row):
        return (row.get("enabled", True) and self.position >= _number(row, "start", 0)
                and (not row.get("end") or self.position <= _number(row, "end", 0))
                and self._animation_frame(row) is not None)

    def _animation_frame(self, row):
        """Use the same cue-relative clock and explicit loop policy as export."""
        path = Path(str(row.get("path") or ""))
        if path.suffix.lower() not in (".gif", ".webp"):
            return 0
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return 0
        if key not in self._animations:
            durations = []
            try:
                from advanced_render import _animation_durations
                durations = _animation_durations(path)
            except (ValueError, OSError, EOFError):
                durations = []
            self._animations[key] = durations
            while len(self._animations) > 12:
                self._animations.popitem(last=False)
        durations = self._animations[key]
        if not durations:
            return 0
        elapsed = max(0, self.position - _number(row, "start", 0))
        duration = sum(durations)
        if row.get("loop", True):
            elapsed %= duration
        elif elapsed >= duration:
            return None
        boundary = 0.0
        for index, length in enumerate(durations):
            boundary += length
            if elapsed < boundary:
                return index
        return len(durations) - 1

    def _image(self, path):
        path = str(path)
        try:
            key = (path, Path(path).stat().st_mtime_ns)
        except OSError:
            return QImage()
        if key not in self._images:
            image = QImage(path)
            if image.width() * image.height() > 16_000_000:
                image = image.scaled(4096, 4096, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            self._images[key] = image
            while len(self._images) > 12:
                self._images.popitem(last=False)
        return self._images[key]

    def _text_image(self, row):
        # Exactly the same font, wrapping, padding and outline as exported text.
        key = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        if key not in self._text:
            try:
                from advanced_render import _text_image
                image = _qimage(_text_image(row, canvas_size(self.media, self.settings)[0]))
            except (ValueError, OSError):
                image = QImage()
            self._text[key] = image
            while len(self._text) > 16:
                self._text.popitem(last=False)
        return self._text[key]

    def _layer_image(self, row):
        try:
            stamp = Path(row.get("path", "")).stat().st_mtime_ns
        except OSError:
            return QImage()
        frame_index = self._animation_frame(row) or 0
        key = (stamp, frame_index, json.dumps(row, sort_keys=True, ensure_ascii=False, default=str))
        if key not in self._layer_images:
            try:
                from advanced_render import _image_layer_image
                image = _qimage(_image_layer_image(row, frame_index))
            except (ValueError, OSError):
                image = QImage()
            self._layer_images[key] = image
            while len(self._layer_images) > 12:
                self._layer_images.popitem(last=False)
        return self._layer_images[key]

    def _world_rect(self, kind, row):
        canvas_w, canvas_h = canvas_size(self.media, self.settings)
        if kind == "text_layers":
            image = self._text_image(row)
            width, height = max(2, image.width()), max(2, image.height())
        elif kind == "image_layers":
            image = self._layer_image(row)
            width, height = max(2, image.width()), max(2, image.height())
        else:
            width = max(2, _number(row, "width", 160))
            height = max(2, _number(row, "height", 90))
        offset_default = 0 if kind == "blur_zones" else 16
        x, y = _number(row, "x", offset_default), _number(row, "y", offset_default)
        anchor = row.get("anchor", "top-left")
        if kind != "blur_zones":
            anchor = {"tl": "top-left", "t": "top", "tr": "top-right", "l": "left", "c": "center",
                      "r": "right", "bl": "bottom-left", "b": "bottom", "br": "bottom-right"}.get(anchor, anchor)
            if anchor in ("top", "center", "bottom"):
                x += (canvas_w - width) / 2
            elif anchor in ("top-right", "right", "bottom-right"):
                x = canvas_w - width - x
            if anchor in ("left", "center", "right"):
                y += (canvas_h - height) / 2
            elif anchor in ("bottom-left", "bottom", "bottom-right"):
                y = canvas_h - height - y
            if kind == "text_layers":
                elapsed = max(0, self.position - _number(row, "start", 0))
                speed = _number(row, "speed", 80)
                mode = row.get("marquee","none")
                if mode in ("auto","left","right","up","down") or ("text_effect" in row and mode != "horizontal" and mode != "none"):
                    from text_effects import position_motion
                    x,y = position_motion(mode,elapsed,canvas_w,canvas_h,width,height,speed)
                    return QRectF(x,y,width,height)
                if row.get("marquee") in ("horizontal", "inside"):
                    x = canvas_w - elapsed * speed % (canvas_w + width)
                elif row.get("marquee") == "diagonal":
                    span_x, span_y = max(1, canvas_w - width), max(1, canvas_h - height)
                    x = abs((elapsed * speed + _number(row, "x", offset_default)) % (2 * span_x) - span_x)
                    y = abs((elapsed * speed * .7 + _number(row, "y", offset_default)) % (2 * span_y) - span_y)
                elif row.get("marquee") == "around":
                    span_x, span_y = max(1, canvas_w - width), max(1, canvas_h - height)
                    travel = elapsed * speed % (2 * (span_x + span_y))
                    if travel < span_x:
                        x, y = travel, 0
                    elif travel < span_x + span_y:
                        x, y = span_x, travel - span_x
                    elif travel < 2 * span_x + span_y:
                        x, y = 2 * span_x + span_y - travel, span_y
                    else:
                        x, y = 0, 2 * (span_x + span_y) - travel
        return QRectF(x, y, width, height)

    def layer_rect(self, kind, index):
        if kind == "subtitle_tracks":
            track = next((track for track in self._caption_tracks if track["index"] == index and track["enabled"]), None)
            cue = self._active_caption(track["rows"]) if track else None
            if cue is None:
                return QRectF()
            image, world = self._caption_geometry(cue, track["style"], raster=True)
            return QRectF(self.world_to_view(world.topLeft()), self.world_to_view(world.bottomRight()))
        row = self.settings.get(kind, [])[index]
        world = self._world_rect(kind, row)
        return QRectF(self.world_to_view(world.topLeft()), self.world_to_view(world.bottomRight()))

    def _layers(self):
        return [(kind, index, row) for kind in ("blur_zones", "image_layers", "text_layers")
                for index, row in enumerate(self.settings.get(kind, [])) if self._visible(row)]

    def _active_caption(self, rows):
        return next((row for row in rows if row["start"] <= self.position < row["end"]), None)

    def _caption_geometry(self, cue, style, raster=False):
        width, height = canvas_size(self.media, self.settings)
        row = dict(style, text=cue["text"], font=style.get("font", "Arial"),
                   font_size=style.get("size", self.settings.get("subtitle_size", 24)),
                   wrap_width=max(2,width-48), width=max(2,width-48), opacity=style.get("opacity",1))
        if raster:
            from text_effects import render_text
            key = "caption-role:" + json.dumps(row,sort_keys=True,ensure_ascii=False,default=str)
            if key not in self._text:
                self._text[key] = _qimage(render_text(row,width))
                while len(self._text) > 16:
                    self._text.popitem(last=False)
            image = self._text[key]
        else:
            image = self._text_image(row)
        alignment = int(style.get("alignment",2))
        margin = 24 if raster or "text_effect" in style or "bg_fit" in style else 16
        column, vertical = (alignment-1)%3, (alignment-1)//3
        x = margin if column == 0 else (width-image.width())/2 if column == 1 else width-image.width()-margin
        y = height-image.height()-margin if vertical == 0 else (height-image.height())/2 if vertical == 1 else margin
        if style.get("x") is not None:
            x = float(style["x"])-image.width()*(0 if column == 0 else .5 if column == 1 else 1)
        if style.get("y") is not None:
            y = float(style["y"])-image.height()*(1 if vertical == 0 else .5 if vertical == 1 else 0)
        return image, QRectF(x,y,image.width(),image.height())

    def _paint_caption(self,painter,cue,style,raster=False):
        image, world = self._caption_geometry(cue,style,raster)
        painter.drawImage(QRectF(self.world_to_view(world.topLeft()),self.world_to_view(world.bottomRight())),image)

    def _background(self):
        image = self._live_frame if not self._live_frame.isNull() else self._poster
        if image.isNull():
            return image
        rotation = int(self.settings.get("rotation", 0)) % 360
        if rotation:
            image = image.transformed(QTransform().rotate(rotation))
        if self.settings.get("mirror"):
            image = image.mirrored(True, False)
        if self.settings.get("source_flip_vertical"):
            image = image.mirrored(False, True)
        return image

    def _source_canvas(self):
        """Compose at display resolution using export's canvas-pixel geometry."""
        original = self._live_frame if not self._live_frame.isNull() else self._poster
        world_w, world_h = canvas_size(self.media, self.settings)
        canvas = self.canvas_rect()
        width, height = max(2, round(canvas.width())), max(2, round(canvas.height()))
        options = composition_settings(self.settings)
        key = (original.cacheKey(), width, height, world_w, world_h,
               json.dumps({**options, **{name: self.settings.get(name) for name in ("rotation", "mirror", "fit")}}, sort_keys=True))
        if key in self._source_canvases:
            return self._source_canvases[key]
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(QColor(options["background_color"] if options["background_mode"] == "color" else "black"))
        if not original.isNull():
            layout = source_layout(original.width(), original.height(), world_w, world_h, self.settings)
            def rectangle(values):
                x, y, w, h = values
                return QRectF(x * width / world_w, y * height / world_h, w * width / world_w, h * height / world_h)
            if options["background_mode"] == "blur":
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(rectangle(layout["background"]), original)
                painter.end()
                from PIL import Image, ImageFilter
                raster = Image.frombytes("RGBA", (width, height), bytes(image.constBits()))
                raster = raster.filter(ImageFilter.GaussianBlur(options["background_blur"] * width / world_w))
                image = _qimage(raster)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setOpacity(options["source_opacity"])
            if options.get("source_rotation"):
                target = rectangle(layout["foreground"])
                pre_w, pre_h = layout["pre_rotation"]
                painter.translate(target.center())
                painter.rotate(options["source_rotation"])
                painter.drawImage(QRectF(-pre_w*width/world_w/2,-pre_h*height/world_h/2,
                                        pre_w*width/world_w,pre_h*height/world_h),self._background())
            else:
                painter.drawImage(rectangle(layout["foreground"]), self._background())
            painter.end()
        self._source_canvases[key] = image
        while len(self._source_canvases) > 2:
            self._source_canvases.popitem(last=False)
        return image

    def _blur_patch(self, rect, source, target, row):
        """Preview Gaussian blur at display size; export keeps full resolution."""
        if source.isNull():
            return QImage()
        from PIL import Image, ImageDraw, ImageFilter
        width, height = max(2, round(rect.width())), max(2, round(rect.height()))
        patch = QImage(width, height, QImage.Format.Format_RGBA8888)
        patch.fill(QColor("black"))
        painter = QPainter(patch)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target.translated(-rect.left(), -rect.top()), source)
        painter.end()
        original = Image.frombytes("RGBA", (width, height), bytes(patch.constBits()))
        scale = self.canvas_rect().width() / canvas_size(self.media, self.settings)[0]
        blurred = original.filter(ImageFilter.GaussianBlur(max(.1, _number(row, "strength", 12) * scale)))
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        feather = min(min(width, height) / 2 - 1, max(0, _number(row, "feather", 0) * scale))
        box = (feather, feather, width - 1 - feather, height - 1 - feather)
        if row.get("shape") == "ellipse":
            draw.ellipse(box, fill=255)
        else:
            draw.rounded_rectangle(box, radius=max(0, _number(row, "corner", 0) * scale), fill=255)
        if feather:
            mask = mask.filter(ImageFilter.GaussianBlur(feather))
        blurred.putalpha(mask)
        return _qimage(blurred)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#2a2a2a"))
        canvas = self.canvas_rect()
        painter.fillRect(canvas, QColor("black"))
        painter.setClipRect(canvas)
        image = self._source_canvas()
        target = canvas
        painter.drawImage(canvas, image)
        width, height = canvas_size(self.media, self.settings)
        for kind, index, row in self._layers():
            rect = self.layer_rect(kind, index)
            painter.save()
            if kind == "blur_zones":
                patch = self._blur_patch(rect, image, target, row)
                if not patch.isNull():
                    painter.drawImage(rect, patch)
                painter.setPen(QPen(QColor("#00b4d8"), 1, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if row.get("shape") == "ellipse":
                    painter.drawEllipse(rect)
                else:
                    painter.drawRoundedRect(rect, 2, 2)
            elif kind == "image_layers":
                from layer_blend import qt_composition_mode
                painter.setCompositionMode(qt_composition_mode(row.get("blend_mode", "normal")))
                painter.setOpacity(min(1, max(0, _number(row, "opacity", 1))))
                overlay = self._layer_image(row)
                if not overlay.isNull():
                    painter.drawImage(rect, overlay)
            else:
                from layer_blend import qt_composition_mode
                painter.setCompositionMode(qt_composition_mode(row.get("blend_mode", "normal")))
                overlay = self._text_image(row)
                if not overlay.isNull():
                    painter.drawImage(rect, overlay)
            painter.restore()
        # Captions follow the active cue, with export style and ASS alignment.
        if "subtitle_tracks" in self.settings:
            for track in self._caption_tracks:
                cue = self._active_caption(track["rows"]) if track["enabled"] else None
                if cue:
                    self._paint_caption(painter,cue,track["style"],raster=True)
        else:
            cue = self._active_caption(self._subtitles)
            if cue:
                self._paint_caption(painter,cue,self.settings.get("subtitle_style",{}))
        if self.selected_layer:
            kind, index = self.selected_layer
            if 0 <= index < len(self.settings.get(kind, [])):
                rect = self.layer_rect(kind, index)
                painter.setPen(QPen(QColor("#00b4d8"), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
                painter.setBrush(QColor("#ffffff"))
                if kind != "subtitle_tracks":
                    for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
                        painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))
        border = _number(self.settings, "frame_border", 0) * canvas.width() / width
        if border:
            painter.setPen(QPen(QColor(self.settings.get("frame_border_color", "#ffffff")), border))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(canvas.adjusted(border / 2, border / 2, -border / 2, -border / 2))
        painter.end()

    def _hit(self, point):
        if self.selected_layer:
            kind, index = self.selected_layer
            if kind != "subtitle_tracks" and index < len(self.settings.get(kind, [])):
                rect = self.layer_rect(kind, index)
                for mode, corner in (("tl", rect.topLeft()), ("tr", rect.topRight()),
                                     ("bl", rect.bottomLeft()), ("br", rect.bottomRight())):
                    if QRectF(corner.x() - 7, corner.y() - 7, 14, 14).contains(point):
                        return kind, index, mode
        for track in reversed(self._caption_tracks):
            if track["enabled"] and self.layer_rect("subtitle_tracks",track["index"]).contains(point):
                return "subtitle_tracks",track["index"],"select"
        for kind, index, row in reversed(self._layers()):
            if self.layer_rect(kind, index).contains(point):
                return kind, index, "move"
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.activated.emit()
        self.setFocus()
        hit = self._hit(event.position())
        if hit:
            kind, index, mode = hit
            self.selected_layer = (kind, index)
            if kind == "subtitle_tracks":
                self._drag = None
                self.chosen.emit(kind,index)
                self.update()
                event.accept()
                return
            row = copy.deepcopy(self.settings[kind][index])
            self._drag = (kind, index, mode, self.view_to_world(event.position()),
                          row, self._world_rect(kind, row))
            self.chosen.emit(kind, index)
        else:
            self.selected_layer = None
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag:
            kind, index, mode, start, original, bounds = self._drag
            delta = self.view_to_world(event.position()) - start
            row = copy.deepcopy(original)
            canvas_w, canvas_h = canvas_size(self.media, self.settings)
            if mode == "move":
                x, y = bounds.x() + delta.x(), bounds.y() + delta.y()
                # Convert anchors to the resulting top-left position on a drag.
                row["anchor"] = "top-left"
                row["marquee"] = "none" if kind == "text_layers" else row.get("marquee", "none")
                row["x"] = round(min(max(0, x), max(0, canvas_w - bounds.width())))
                row["y"] = round(min(max(0, y), max(0, canvas_h - bounds.height())))
            else:
                x = bounds.x() + delta.x() if "l" in mode else bounds.x()
                y = bounds.y() + delta.y() if "t" in mode else bounds.y()
                w = bounds.width() + (-delta.x() if "l" in mode else delta.x())
                h = bounds.height() + (-delta.y() if "t" in mode else delta.y())
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    h = w * bounds.height() / max(1, bounds.width())
                x, y = min(canvas_w - 4, max(0, x)), min(canvas_h - 4, max(0, y))
                row.update(x=round(x), y=round(y), anchor="top-left",
                           width=round(max(4, min(w, canvas_w - x))),
                           height=round(max(4, min(h, canvas_h - y))))
                if kind == "image_layers" and _number(original, "rotation", 0):
                    # Keep transformed resize handles stable; numeric fields
                    # still allow separate changes to the unrotated dimensions.
                    factor = max(.02, min(w / max(1, bounds.width()), h / max(1, bounds.height())))
                    original_width = _number(original, "width", 160)
                    original_height = _number(original, "height", 0)
                    if not original_height:
                        image = self._image(original.get("path", ""))
                        original_height = original_width * image.height() / max(1, image.width())
                    row["width"] = round(max(4, original_width * factor))
                    row["height"] = round(max(4, original_height * factor))
                if kind == "text_layers":
                    row["font_size"] = round(min(512, max(6, _number(original, "font_size", 36) * h / max(1, bounds.height()))))
                    row["wrap_width"] = row["width"]
                    row["marquee"] = "none"
            self.settings[kind][index] = row
            self.changed.emit(kind, index, copy.deepcopy(row))
            self.update()
        else:
            hit = self._hit(event.position())
            self.setCursor(Qt.CursorShape.SizeAllCursor if hit and hit[2] == "move"
                           else Qt.CursorShape.SizeFDiagCursor if hit else Qt.CursorShape.ArrowCursor)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.selected_layer and self.selected_layer[0] == "subtitle_tracks":
            return super().keyPressEvent(event)
        if self.selected_layer and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            kind, index = self.selected_layer
            row = copy.deepcopy(self.settings[kind][index])
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            xstep = -step if event.key() == Qt.Key.Key_Left else step if event.key() == Qt.Key.Key_Right else 0
            ystep = -step if event.key() == Qt.Key.Key_Up else step if event.key() == Qt.Key.Key_Down else 0
            bounds = self._world_rect(kind, row)
            width, height = canvas_size(self.media, self.settings)
            row.update(anchor="top-left", x=round(min(max(0, bounds.x() + xstep), max(0, width - bounds.width()))),
                       y=round(min(max(0, bounds.y() + ystep), max(0, height - bounds.height()))))
            if kind == "text_layers":
                row["marquee"] = "none"
            self.settings[kind][index] = row
            self.changed.emit(kind, index, copy.deepcopy(row))
            self.update()
            event.accept()
        else:
            super().keyPressEvent(event)
