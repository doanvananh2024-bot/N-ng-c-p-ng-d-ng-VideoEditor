"""Offline regressions for voice fitting and image/GIF preview-export parity.

Speech synthesis is replaced with local sine-wave fixtures. Conversion,
fitting, mixing, GIF export and Qt preview painting use the actual app code.
"""
from __future__ import annotations

from array import array
from datetime import datetime, timezone
from io import BytesIO
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from unittest.mock import patch
import uuid
import wave


ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    run_dir = ROOT / "qa" / ("renderer_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    run_dir.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PIL import Image
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    import ai_tools
    import engine
    import render_pipeline
    import subtitle_tools
    from preview_canvas import EditablePreview

    app = QApplication.instance() or QApplication([])
    report = dict(
        phase="renderer-regressions-1.2",
        created_at=datetime.now(timezone.utc).isoformat(),
        run_dir=str(run_dir), offline=True, paid_provider_requests=0,
        synthesis="local mocked sine-wave fixtures", checks=[], artifacts={},
    )
    qt_errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, trace: qt_errors.append("".join(traceback.format_exception(kind, value, trace)))
    report_path = ROOT / "qa" / "renderer-regression-report.json"

    def persist():
        report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
        report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
        report["status"] = "failed" if report["failed"] or qt_errors else "passed"
        report["qt_exceptions"] = qt_errors
        value = json.dumps(report, ensure_ascii=False, indent=2)
        report_path.write_text(value, encoding="utf-8")
        (run_dir / "renderer-regression-report.json").write_text(value, encoding="utf-8")

    def check(name, fn):
        began = time.monotonic()
        row = dict(name=name)
        report["checks"].append(row)
        try:
            details = fn()
            require(not qt_errors, "Qt exceptions: " + "\n".join(qt_errors))
            row.update(status="passed", details=details or {})
        except Exception as exc:
            row.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        row["seconds"] = round(time.monotonic() - began, 3)
        print(json.dumps({key: row[key] for key in ("name", "status", "seconds")}, ensure_ascii=False), flush=True)
        persist()

    def ffmpeg(args):
        process = subprocess.run(
            [engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", *args],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30, creationflags=engine._creation_flags(),
        )
        require(process.returncode == 0, process.stderr.decode("utf-8", "replace")[-1500:])
        return process.stdout

    def frame(path, seconds):
        data = ffmpeg(["-ss", str(seconds), "-i", str(path), "-frames:v", "1",
                       "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        return Image.open(BytesIO(data)).convert("RGB")

    source, poster, gif = (run_dir / name for name in ("source.mp4", "poster.png", "two-frame.gif"))

    def fixtures():
        ffmpeg(["-n", "-f", "lavfi", "-i", "color=c=0x101010:size=320x180:rate=20:duration=1.5",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)])
        Image.new("RGB", (320, 180), (16, 16, 16)).save(poster)
        images = [Image.new("RGBA", (40, 20), color) for color in ("red", "lime")]
        images[0].save(gif, save_all=True, append_images=images[1:], duration=[200, 200], loop=0, disposal=2)
        report["artifacts"].update(source=str(source), poster=str(poster), two_frame_gif=str(gif))
        return dict(video=engine.probe(str(source)), gif_frame_durations_ms=[200, 200])

    def speaker_fitting():
        calls = []
        async def synthesize(edge, text, voice, path, cancel):
            calls.append(dict(text=text, voice=voice))
            rate = 24000
            frequency = 440 if voice == "speakerA" else 880
            seconds = 1.5 if voice == "speakerA" else .2
            with wave.open(str(path), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(rate)
                writer.writeframes(array("h", (
                    round(3000 * math.sin(2 * math.pi * frequency * index / rate))
                    for index in range(round(seconds * rate))
                )).tobytes())

        rows = [dict(start=0, end=1.2, text="Long first cue", voice="speakerA"),
                dict(start=.5, end=.9, text="Second speaker", voice="speakerB")]
        messages = []
        target = run_dir / "two-speaker-fit.wav"
        with patch.object(ai_tools, "_synthesize", synthesize):
            subtitle_tools.dub_rows(rows, str(target), progress=lambda p, message: messages.append(message))
        with wave.open(str(target), "rb") as reader:
            require((reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, 24000),
                    "Voice mix must be 24 kHz mono PCM")
            samples = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(float)
        require(len(samples) == round(1.2 * 24000), "Fit mode lost the final subtitle's trailing timeline")
        segment = samples[round(.52 * 24000):round(.64 * 24000)]
        spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
        frequencies = np.fft.rfftfreq(len(segment), 1 / 24000)
        previous_voice = float(max(spectrum[np.abs(frequencies - 440) < 25]))
        current_voice = float(max(spectrum[np.abs(frequencies - 880) < 25]))
        require(current_voice > 50 * previous_voice,
                f"Previous speaker overlaps the next cue: 440 Hz={previous_voice}, 880 Hz={current_voice}")
        require(float(np.max(np.abs(samples[round(1 * 24000):]))) == 0, "Trailing timeline must remain silent")
        require(any("nhanh" in message and "lần" in message for message in messages), "Fast fitted speech must show a warning")
        require(rows[0]["text"] == "Long first cue" and rows[0]["end"] == 1.2, "Fitting mutated subtitle content/timing")
        with patch.object(ai_tools, "_synthesize", side_effect=AssertionError("Validation must precede synthesis")):
            blocked = False
            invalid = run_dir / "invalid-coincident.wav"
            try:
                subtitle_tools.dub_rows([dict(start=0, end=1, text="A", voice="a"),
                                        dict(start=0, end=2, text="B", voice="b")], str(invalid))
            except ValueError:
                blocked = True
            require(blocked and not invalid.exists(), "Coincident fit cues were accepted or published")
        report["artifacts"]["two_speaker_fit"] = str(target)
        return dict(duration=len(samples) / 24000, synthesis_calls=calls, previous_voice_magnitude=previous_voice,
                    current_voice_magnitude=current_voice, minimum_dominance_ratio=50, trailing_silence=True,
                    speed_warning=True, coincident_cues_rejected_before_synthesis=True, subtitle_rows_preserved=True)

    def animation_parity():
        evidence = []
        for looping in (False, True):
            row = dict(path=str(gif), width=40, height=20, anchor="br", x=10, y=10,
                       start=.2, end=1.4, loop=looping, rotation=90, flip_horizontal=True)
            settings = dict(width=320, height=180, fps=20,
                            encoder=dict(format="mp4", codec="h264", device="cpu", preset="fast", quality=18),
                            image_layers=[row])
            output = run_dir / ("gif-loop.mp4" if looping else "gif-once.mp4")
            render_pipeline.render_project([dict(path=str(source))], settings, str(output), lambda p, m: None, threading.Event())
            preview = EditablePreview(engine.probe(str(source)), str(poster), settings, {})
            try:
                preview.resize(640, 360)
                preview.show()
                app.processEvents()
                for seconds, expected in ((.25, "red"), (.45, "green"), (.65, "red" if looping else "dark")):
                    exported = frame(output, seconds)
                    pixel = exported.getpixel((300, 145))
                    preview.set_position(seconds)
                    app.processEvents()
                    snapshot = preview.grab()
                    point = preview.world_to_view(QPointF(300, 145))
                    color = snapshot.toImage().pixelColor(round(point.x()), round(point.y()))
                    visible = (color.red(), color.green(), color.blue())
                    def matches(value):
                        if expected == "red":
                            return value[0] > 180 and value[1] < 80
                        if expected == "green":
                            return value[1] > 180 and value[0] < 80
                        return max(value) < 60
                    require(matches(pixel), f"GIF export mismatch: loop={looping}, t={seconds}, expected={expected}, pixel={pixel}")
                    require(matches(visible), f"GIF preview mismatch: loop={looping}, t={seconds}, expected={expected}, pixel={visible}")
                    capture = run_dir / f"gif-{'loop' if looping else 'once'}-{seconds:.2f}-preview.png"
                    snapshot.save(str(capture))
                    exported.save(run_dir / f"gif-{'loop' if looping else 'once'}-{seconds:.2f}-export.png")
                    evidence.append(dict(loop=looping, seconds=seconds, expected=expected,
                                         export_pixel=list(pixel), preview_pixel=list(visible), preview_capture=str(capture)))
            finally:
                preview.close()
            report["artifacts"]["gif_loop_export" if looping else "gif_once_export"] = str(output)
        return dict(transforms=dict(rotation=90, flip_horizontal=True, anchor="br"), observations=evidence,
                    last_frame_retained_for_full_duration=True, no_loop_lifetime_respected=True)

    def static_geometry():
        image = run_dir / "odd-dimensions.png"
        Image.new("RGBA", (7, 3), "lime").save(image)
        row = dict(path=str(image), width=11, anchor="br")
        preview = EditablePreview(dict(width=320, height=180), str(poster), dict(image_layers=[row]), {})
        try:
            bounds = preview._world_rect("image_layers", row)
            values = (bounds.x(), bounds.y(), bounds.width(), bounds.height())
            require(values == (293, 159, 11, 5), f"Static automatic height/anchor/default offsets mismatch: {values}")
        finally:
            preview.close()
        report["artifacts"]["odd_dimension_image"] = str(image)
        return dict(source_dimensions=[7, 3], scaled_dimensions=[11, 5], bounds=list(values), default_offsets=[16, 16])

    try:
        check("Generated isolated local video and two-frame GIF fixtures", fixtures)
        check("Real multi-speaker fitting, warnings, trailing silence and pre-synthesis timing validation", speaker_fitting)
        check("Real transformed GIF export and Qt preview agree with looping and one-shot lifetime", animation_parity)
        check("Odd static image dimensions, short anchor alias and default offsets agree", static_geometry)
    finally:
        app.processEvents()
        sys.excepthook = previous_hook
        persist()
    print(json.dumps(dict(report=str(report_path), passed=report["passed"], failed=report["failed"], status=report["status"]), ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
