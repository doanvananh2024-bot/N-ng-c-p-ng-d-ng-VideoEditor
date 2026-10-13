"""Real offline FFmpeg/Qt checks for source-canvas composition version 1.3."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid


ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    run = ROOT / "qa" / ("source_composition_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    run.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PIL import Image, ImageDraw
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    import engine
    from preview_canvas import EditablePreview
    from source_composition import DEFAULTS

    app = QApplication.instance() or QApplication([])
    report_path = ROOT / "qa" / "source-composition-report.json"
    report = dict(phase="source-composition-1.3", created_at=datetime.now(timezone.utc).isoformat(), run_dir=str(run),
                  offline=True, paid_provider_requests=0, checks=[], artifacts={})
    qt_errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, trace: qt_errors.append("".join(traceback.format_exception(kind, value, trace)))

    def persist():
        report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
        report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
        report["status"] = "failed" if report["failed"] or qt_errors else "passed"
        report["qt_exceptions"] = qt_errors
        value = json.dumps(report, ensure_ascii=False, indent=2)
        report_path.write_text(value, encoding="utf-8")
        (run / "source-composition-report.json").write_text(value, encoding="utf-8")

    def check(name, fn):
        began = time.monotonic()
        row = dict(name=name)
        report["checks"].append(row)
        try:
            row.update(status="passed", details=fn())
            require(not qt_errors, "Qt slot exceptions: " + "\n".join(qt_errors))
        except Exception as exc:
            row.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        row["seconds"] = round(time.monotonic() - began, 3)
        print(json.dumps({key: row[key] for key in ("name", "status", "seconds")}), flush=True)
        persist()

    def ffmpeg(args):
        process = subprocess.run([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", *args],
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=45, creationflags=engine._creation_flags())
        require(process.returncode == 0, process.stderr.decode("utf-8", "replace")[-1500:])
        return process.stdout

    def frame(path, seconds=.3):
        data = ffmpeg(["-ss", str(seconds), "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        return Image.open(BytesIO(data)).convert("RGB")

    fixtures = {}
    base = dict(width=320, height=180, fps=10)

    def make_fixtures():
        for name, size in (("portrait", (90, 160)), ("square", (120, 120)), ("landscape", (160, 90))):
            width, height = size
            image = Image.new("RGB", size)
            draw = ImageDraw.Draw(image)
            for box, color in (((0, 0, width // 2 - 1, height // 2 - 1), "red"),
                               ((width // 2, 0, width - 1, height // 2 - 1), "lime"),
                               ((0, height // 2, width // 2 - 1, height - 1), "blue"),
                               ((width // 2, height // 2, width - 1, height - 1), "yellow")):
                draw.rectangle(box, fill=color)
            original, source, poster = (run / f"{name}.{extension}" for extension in ("png", "mp4", "decoded.png"))
            image.save(original)
            ffmpeg(["-n", "-loop", "1", "-i", str(original), "-t", "1.2", "-r", "10", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "0", "-pix_fmt", "yuv420p", str(source)])
            frame(source, 0).save(poster)
            fixtures[name] = dict(source=source, poster=poster, info=engine.probe(str(source)))
            report["artifacts"][name + "_source"] = str(source)
        return {name: fixture["info"] for name, fixture in fixtures.items()}

    def export(name, shape, settings, clips=None):
        target = run / (name + ".mp4")
        engine.media_process(clips or [dict(path=str(fixtures[shape]["source"]))], {**base, **settings},
                             str(target), lambda p, m: None, threading.Event())
        report["artifacts"][name] = str(target)
        return target

    def parity(name, shape, settings, points, expected=None, tolerance=18, seconds=.3):
        output = export(name, shape, settings)
        exported = frame(output, seconds)
        fixture = fixtures[shape]
        preview = EditablePreview(fixture["info"], str(fixture["poster"]), {**base, **settings}, {})
        rows = []
        try:
            preview.resize(640, 360)
            preview.show()
            preview.set_position(seconds)
            app.processEvents()
            captured = preview.grab()
            captured.save(str(run / (name + "-preview.png")))
            exported.save(run / (name + "-export.png"))
            for index, point in enumerate(points):
                actual = exported.getpixel(point)
                mapped = preview.world_to_view(QPointF(*point))
                color = captured.toImage().pixelColor(round(mapped.x()), round(mapped.y()))
                visible = (color.red(), color.green(), color.blue())
                require(max(abs(a - b) for a, b in zip(actual, visible)) <= tolerance,
                        f"Preview/export mismatch at {point}: exported={actual}, preview={visible}")
                if expected:
                    require(max(abs(a - b) for a, b in zip(actual, expected[index])) <= tolerance,
                            f"Incorrect composition at {point}: exported={actual}, expected={expected[index]}")
                rows.append(dict(point=list(point), export_pixel=list(actual), preview_pixel=list(visible),
                                 expected=list(expected[index]) if expected else None))
        finally:
            preview.close()
        return dict(output=str(output), observations=rows)

    def default_compatibility():
        evidence = []
        for shape in fixtures:
            output = export("default-" + shape, shape, {})
            reference = run / ("legacy-normalization-" + shape + ".mkv")
            old_filter = ("setpts=PTS-STARTPTS,scale=320:180:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                          "pad=320:180:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,eq=brightness=0:contrast=1:saturation=1,"
                          "setpts=PTS/1,fps=10,format=yuv420p")
            ffmpeg(["-n", "-i", str(fixtures[shape]["source"]), "-vf", old_filter, "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "18", "-threads", "2", str(reference)])
            actual, legacy = np.asarray(frame(output)), np.asarray(frame(reference))
            difference = int(np.max(np.abs(actual.astype(int) - legacy.astype(int))))
            require(difference == 0, f"Default source composition changed old normalization pixels: {shape}, delta={difference}")
            require((engine.probe(str(output))["width"], engine.probe(str(output))["height"]) == (320, 180), "Output dimensions changed")
            evidence.append(dict(shape=shape, legacy_reference=str(reference), max_pixel_difference=difference))
        explicit = export("explicit-defaults", "portrait", DEFAULTS)
        require(np.array_equal(np.asarray(frame(explicit)), np.asarray(frame(run / "default-portrait.mp4"))),
                "Explicit source defaults differ from an old project without composition keys")
        return dict(legacy_normalization_comparisons=evidence, explicit_defaults_identical=True)

    def color_opacity_offsets():
        return parity("color-opacity-offset", "square",
                      dict(background_mode="color", background_color="#0000FF", source_scale=.5,
                           source_x=.25, source_y=-.1, source_opacity=.5),
                      [(10, 10), (205, 40), (270, 40), (205, 100), (270, 100)],
                      [(0, 0, 255), (128, 0, 128), (0, 128, 128), (0, 0, 255), (128, 128, 128)])

    def hidden_foreground():
        return parity("zero-opacity", "portrait", dict(background_mode="color", background_color="#224466",
                      source_opacity=0, source_scale=3, source_x=-1, source_y=1),
                      [(10, 10), (160, 90), (300, 160)], [(34, 68, 102)] * 3)

    def blurred_background():
        points = [(10, 30), (10, 80), (10, 100), (10, 150), (300, 30), (300, 150)]
        visible = parity("blur-letterbox", "portrait", dict(background_mode="blur", background_blur=12), points, tolerance=30)
        top, near_top, near_bottom, bottom = [row["export_pixel"] for row in visible["observations"][:4]]
        require(top[0] > 180 and bottom[2] > 180, "Blur background did not fill portrait letterboxes with source content")
        require(near_top[0] > 40 and near_top[2] > 10 and near_bottom[0] > 10 and near_bottom[2] > 40,
                "Background only covers the frame without blurring its color transition")
        hidden = parity("blur-background-independent", "portrait", dict(background_mode="blur", background_blur=12,
                        source_opacity=0, source_scale=.1, source_x=1, source_y=-1), points, tolerance=30)
        for first, second in zip(visible["observations"], hidden["observations"]):
            require(max(abs(a - b) for a, b in zip(first["export_pixel"], second["export_pixel"])) < 12,
                    "Foreground transforms changed the independent blurred background")
        return dict(visible_foreground=visible, hidden_and_shifted_foreground=hidden)

    def fit_and_transform():
        evidence = []
        for shape in fixtures:
            evidence.append(parity("cover-" + shape, shape, dict(fit="cover", background_mode="color", background_color="#FF00FF"),
                                   [(40, 30), (280, 30), (40, 150), (280, 150)],
                                   [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]))
        evidence.append(parity("rotated-and-flipped", "portrait", dict(rotation=90, mirror=True, source_flip_vertical=True,
                               background_mode="color", background_color="#FF00FF"),
                               [(80, 45), (240, 45), (80, 135), (240, 135)],
                               [(0, 255, 0), (255, 255, 0), (255, 0, 0), (0, 0, 255)]))
        return dict(cases=evidence)

    def trim_concat():
        clips = [dict(path=str(fixtures["portrait"]["source"]), start=.2, end=.6),
                 dict(path=str(fixtures["landscape"]["source"]), start=.1, end=.7)]
        output = export("trimmed-composed-concat", "portrait", dict(background_mode="color", background_color="#112233", source_scale=.5), clips)
        info = engine.probe(str(output))
        require(abs(info["duration"] - 1.0) < .12 and (info["width"], info["height"]) == (320, 180),
                f"Composed clips were not normalized/trimmed before concat: {info}")
        before, after = frame(output, .2).getpixel((90, 60)), frame(output, .65).getpixel((90, 60))
        require(max(abs(a - b) for a, b in zip(before, (17, 34, 51))) < 12 and after[0] > 180 and after[1] < 60,
                f"Each clip did not preserve its own source-to-canvas placement: {before}, {after}")
        return dict(output=str(output), duration=info["duration"], portrait_letterbox_pixel=list(before), landscape_source_pixel=list(after))

    def cancellation_validation():
        cancelled = threading.Event()
        target = run / "cancelled-composition.mp4"
        progress = []
        def cancel_during_render(percent, message):
            progress.append(percent)
            if percent >= 5:
                cancelled.set()
        stopped = False
        try:
            engine.media_process([dict(path=str(fixtures["portrait"]["source"]))],
                                 {**base, "background_mode": "blur", "source_scale": .5}, str(target), cancel_during_render, cancelled)
        except engine.Cancelled:
            stopped = True
        require(stopped and not target.exists(), "Cancelling an actual composed render published an output")
        invalid_cases = [dict(source_x=1.1), dict(source_y=float("nan")), dict(source_scale=.01), dict(source_opacity=1.1),
                         dict(background_blur=31), dict(background_mode="invalid"), dict(background_color="red;null")]
        for index, invalid in enumerate(invalid_cases):
            target = run / f"invalid-settings-{index}.mp4"
            blocked = False
            try:
                engine.media_process([dict(path=str(fixtures["portrait"]["source"]))], {**base, **invalid}, str(target), lambda p, m: None, threading.Event())
            except ValueError:
                blocked = True
            require(blocked and not target.exists(), f"Invalid composition settings were accepted/published: {invalid}")
        return dict(cancelled_during_real_render=True, published_after_cancel=False, progress_before_cancel=progress,
                    invalid_composition_inputs_rejected=len(invalid_cases))

    try:
        check("Local portrait, square and landscape video fixtures", make_fixtures)
        check("Default output remains pixel-identical to legacy normalization for three source shapes", default_compatibility)
        check("Color background, half-opacity source, scale and normalized offsets agree in FFmpeg and Qt", color_opacity_offsets)
        check("Zero opacity hides the foreground at maximum scale and offsets", hidden_foreground)
        check("Blur fills source letterboxes and stays independent of foreground transforms", blurred_background)
        check("Cover fit and combined rotation, mirror and vertical flip agree in output and preview", fit_and_transform)
        check("Composition precedes normalization and preserves trimmed multi-shape concat", trim_concat)
        check("Actual composed-render cancellation and invalid-input output protection", cancellation_validation)
    finally:
        app.processEvents()
        sys.excepthook = previous_hook
        persist()
    print(json.dumps(dict(report=str(report_path), passed=report["passed"], failed=report["failed"], status=report["status"])))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
