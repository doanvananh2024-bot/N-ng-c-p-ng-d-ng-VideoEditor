"""Offline integration verification for the independently implemented editor.

Runs against a generated, two-second Unicode-path media fixture. All persistent
data and outputs are confined to this application's QA folder. No AI network
calls or model downloads are made. The JSON report retains useful artifacts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
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
QA = ROOT / "qa"
REPORT_NAME = "ui-verification-report.json" if "--ui-only" in sys.argv else "verification-report.json"
RUN = QA / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
RUN.mkdir(parents=True, exist_ok=True)
os.environ["LOCALAPPDATA"] = str(RUN / "local-data")
os.environ["APPDATA"] = str(RUN / "roaming-data")
os.environ["HF_HOME"] = str(RUN / "hf-cache")
os.environ["TORCH_HOME"] = str(RUN / "torch-cache")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import ai_tools
import engine
import providers
import render_pipeline
import subtitle_tools
import workflow


REPORT = dict(created_at=datetime.now(timezone.utc).isoformat(), run_dir=str(RUN),
              application="VideoEditorIndependent", offline=True, checks=[], artifacts={})
FIXTURE = RUN / "Đoạn thử tiếng Việt – xin chào.mp4"
SUBTITLES = RUN / "Phụ đề tiếng Việt.srt"
LOGO = RUN / "Ảnh thử.png"
ROWS = [dict(start=0.05, end=0.85, text="Xin chào Việt Nam!", voice="vi-VN-HoaiMyNeural"),
        dict(start=0.9, end=1.85, text="Đây là phim thử có dấu tiếng Việt.", voice="vi-VN-NamMinhNeural")]


class DeferredCheck(Exception):
    """Implementation is not ready yet; do not count as a passing check."""


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def progress(_percent, _message):
    pass


def invoke(arguments, **kwargs):
    return subprocess.run(arguments, check=True, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=kwargs.pop("timeout", 90),
                          creationflags=engine._creation_flags(), **kwargs)


def stream_info(path):
    result = invoke([engine.tool_path("ffprobe"), "-v", "error", "-show_streams",
                     "-show_format", "-of", "json", str(path)])
    return json.loads(result.stdout)


def frame(path, at=0.5):
    from PIL import Image
    from io import BytesIO
    result = invoke([engine.tool_path("ffmpeg"), "-v", "error", "-ss", str(at), "-i", str(path),
                     "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
    return Image.open(BytesIO(result.stdout)).convert("RGB")


def check(name, function):
    began = time.monotonic()
    row = dict(name=name, status="running")
    REPORT["checks"].append(row)
    try:
        details = function()
        row.update(status="passed", details=details or {})
    except DeferredCheck as exc:
        row.update(status="deferred", reason=str(exc))
    except Exception as exc:
        row.update(status="failed", error=str(exc), traceback=traceback.format_exc())
    row["seconds"] = round(time.monotonic() - began, 3)
    print(json.dumps({key: row[key] for key in ("name", "status", "seconds")}, ensure_ascii=False), flush=True)
    persist()


def persist():
    REPORT["passed"] = sum(item["status"] == "passed" for item in REPORT["checks"])
    REPORT["failed"] = sum(item["status"] == "failed" for item in REPORT["checks"])
    REPORT["deferred"] = sum(item["status"] == "deferred" for item in REPORT["checks"])
    REPORT["status"] = "failed" if REPORT["failed"] else "passed"
    content = json.dumps(REPORT, ensure_ascii=False, indent=2)
    (QA / REPORT_NAME).write_text(content, encoding="utf-8")
    (RUN / "verification-report.json").write_text(content, encoding="utf-8")


def generate_fixture():
    from PIL import Image, ImageDraw
    invoke([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(FIXTURE)])
    logo = Image.new("RGBA", (36, 30), (255, 0, 0, 255))
    ImageDraw.Draw(logo).rectangle((10, 8, 25, 22), fill=(255, 255, 255, 255))
    logo.save(LOGO)
    subtitle_tools.save_subtitles(ROWS, SUBTITLES)
    info = engine.probe(str(FIXTURE))
    require(info["width"] == 320 and info["height"] == 180 and info["has_audio"], "Incorrect Unicode-path source metadata")
    require(abs(info["duration"] - 2) < .15, "Unexpected fixture duration")
    require(providers.app_data_root().is_relative_to(RUN), "Provider data root is not QA-isolated")
    REPORT["artifacts"].update(source=str(FIXTURE), subtitles=str(SUBTITLES), logo=str(LOGO))
    return dict(metadata=info, data_root=str(providers.app_data_root()), ffmpeg=engine.tool_path("ffmpeg"))


def subtitle_roundtrip():
    rows = subtitle_tools.load_subtitles(SUBTITLES)
    require(rows == ROWS, "Unicode text, timings or per-cue voice metadata did not round-trip")
    replaced, count = subtitle_tools.replace_text(rows, "phim", "video", whole_word=True)
    require(count == 1 and "video thử" in replaced[1]["text"], "Unicode editing failed")
    shifted = subtitle_tools.shift_timings(replaced, -.2, indices=[0])
    require(shifted[0]["start"] == 0 and abs(shifted[0]["end"] - .8) < .001, "Clamped shift lost cue duration")
    merged = subtitle_tools.merge_rows(shifted, [0, 1])
    require(len(merged) == 1 and len(merged[0]["merged_voices"]) == 2, "Merge lost cue text or voice assignments")
    target = RUN / "Đã sửa.vtt"
    subtitle_tools.save_subtitles(merged, target)
    loaded = subtitle_tools.load_subtitles(target)
    require(loaded == merged, "Edited VTT failed metadata/text/timing round-trip")
    require(rows == ROWS, "Editing unexpectedly mutated the original cues")
    stale = RUN / "Thay văn bản.srt"
    subtitle_tools.save_subtitles(ROWS, stale)
    stale.write_text(stale.read_text(encoding="utf-8").replace("Xin chào", "Chào nhé"), encoding="utf-8")
    require("voice" not in subtitle_tools.load_subtitles(stale)[0], "Stale sidecar metadata was incorrectly reused")
    return dict(cues=len(rows), replacements=count, edited_vtt=str(target), stale_metadata_rejected=True)


def settings_advanced():
    return dict(width=320, height=180, fps=15, subtitle_path=str(SUBTITLES),
                subtitle_style=dict(size=15, color="#FFFF00", outline=1, bg_opacity=.6, alignment=2),
                encoder=dict(format="mp4", codec="h264", device="cpu", preset="fast", quality=20),
                text_layers=[dict(text="Tiếng Việt: Chào bạn!", x=50, y=10, font_size=15, color="#FFFFFF",
                                  outline=1, bg_opacity=.5, start=.1, end=1.9)],
                image_layers=[dict(path=str(LOGO), x=8, y=12, width=36, height=30, opacity=1)],
                blur_zones=[dict(x=240, y=70, width=64, height=70, shape="ellipse", strength=8, feather=2)],
                filter_preset="vintage", filter_intensity=.4, frame_border=4, frame_border_color="#00FF00",
                audio_filters=dict(compressor=True))


def advanced_export():
    target = RUN / "Xuất lớp nâng cao.mp4"
    values = []
    render_pipeline.render_project([dict(path=str(FIXTURE))], settings_advanced(), str(target),
                                   lambda p, m: values.append(p), threading.Event())
    info = stream_info(target)
    video = next(item for item in info["streams"] if item["codec_type"] == "video")
    require(video["codec_name"] == "h264" and (video["width"], video["height"]) == (320, 180), "Wrong advanced export format")
    require(any(item["codec_type"] == "audio" for item in info["streams"]), "Advanced export lost audio")
    require(values and values[-1] == 100, "Advanced export did not finish progress")
    picture = frame(target)
    green = picture.getpixel((1, 1))
    red = picture.getpixel((10, 15))
    white = picture.getpixel((24, 27))
    require(green[1] > 170 and green[0] < 90 and green[2] < 90, f"Green frame border missing: {green}")
    require(red[0] > 160 and red[1] < 80, f"Image overlay red pixel missing: {red}")
    require(min(white) > 150, f"Image overlay white interior missing: {white}")
    poster = RUN / "advanced-output-frame.png"
    picture.save(poster)
    REPORT["artifacts"].update(advanced_export=str(target), advanced_frame=str(poster))
    return dict(path=str(target), codec=video["codec_name"], frame_border_pixel=green,
                image_red_pixel=red, image_white_pixel=white, duration=info["format"]["duration"])


def hevc_codec():
    target = RUN / "Kiểm tra H265.mov"
    settings = dict(width=320, height=180, fps=15,
                    encoder=dict(format="mov", codec="hevc", device="cpu", preset="fast", quality=26))
    render_pipeline.render_project([dict(path=str(FIXTURE), start=.2, end=1.2)], settings,
                                   str(target), progress, threading.Event())
    info = stream_info(target)
    video = next(item for item in info["streams"] if item["codec_type"] == "video")
    require(video["codec_name"] == "hevc" and video.get("codec_tag_string") == "hvc1", "H.265/MOV codec choice ignored")
    require(abs(float(info["format"]["duration"]) - 1) < .15, "H.265 export trim incorrect")
    return dict(path=str(target), codec=video["codec_name"], tag=video.get("codec_tag_string"))


def workflow_resume_and_cancel():
    config = dict(recognize=False, translate=False, dub=False, export=True, subtitle_path=str(SUBTITLES),
                  settings=dict(width=320, height=180, fps=15, encoder=dict(codec="h264", device="cpu", preset="fast")))
    cancel = threading.Event()

    def stop_at_export(percent, message):
        if "Xuất video" in message and "Bắt đầu" in message:
            cancel.set()

    rows = workflow.run_workflow([str(FIXTURE)], config, str(RUN / "workflow-cancel"), stop_at_export, cancel)
    require(rows[0]["status"] == "cancelled", f"Cancellation was not recorded: {rows[0]}")
    require(rows[0]["completed_stages"] == ["subtitles"] and "video" not in rows[0]["paths"], "Cancellation published video or lost completed subtitle stage")
    resumed = workflow.resume_workflow(rows[0]["manifest"], progress, threading.Event())
    require(resumed[0]["status"] == "done" and Path(resumed[0]["paths"]["video"]).is_file(), f"Resume failed: {resumed}")
    output = Path(resumed[0]["paths"]["video"])
    original_stamp = output.stat().st_mtime_ns
    original_digest = digest(output)
    cached = workflow.resume_workflow(rows[0]["manifest"], progress, threading.Event())
    require(cached[0]["paths"]["video"] == str(output) and output.stat().st_mtime_ns == original_stamp,
            "Completed resumed export was unnecessarily re-encoded")
    require(digest(output) == original_digest, "Completed resumed export changed media")
    manifest = json.loads(Path(rows[0]["manifest"]).read_text(encoding="utf-8"))
    require(manifest["status"] == "done" and manifest["results"][0]["completed_stages"] == ["subtitles", "export"],
            "Final workflow manifest has incorrect status/stages")
    REPORT["artifacts"]["workflow_manifest"] = rows[0]["manifest"]
    return dict(cancel_status=rows[0]["status"], resumed_status=resumed[0]["status"], manifest=rows[0]["manifest"],
                completed_output_reused=True)


def oneclick_join():
    config = dict(recognize=False, translate=False, dub=False, join_enabled=True, general_workers=2, render_workers=1,
                  settings=dict(width=320, height=180, fps=15), join_settings=dict(width=320, height=180, fps=15))
    source = [dict(path=str(FIXTURE), start=0, end=.8), dict(path=str(FIXTURE), start=.8, end=1.6)]
    result = workflow.export_all(source, config, str(RUN / "oneclick"), progress, threading.Event())
    require(result["status"] == "done" and len(result["results"]) == 2 and result["joined_video"], f"One-click join failed: {result}")
    info = engine.probe(result["joined_video"])
    require(abs(info["duration"] - 1.6) < .2, "Ordered joined output has wrong duration")
    stamp = Path(result["joined_video"]).stat().st_mtime_ns
    resumed = workflow.resume_export(result["manifest"], progress, threading.Event())
    require(resumed["status"] == "done" and resumed["joined_video"] == result["joined_video"], "One-click export failed resume")
    require(Path(result["joined_video"]).stat().st_mtime_ns == stamp, "Resumed joined output was unexpectedly regenerated")
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    require(manifest["kind"] == "oneclick_export" and manifest["status"] == "done", "Incorrect master export manifest")
    REPORT["artifacts"].update(export_manifest=result["manifest"], joined_output=result["joined_video"])
    return dict(manifest=result["manifest"], joined=result["joined_video"], duration=info["duration"], outputs=len(result["results"]))


def mini_tools():
    audio = RUN / "Tiếng thử.mp3"
    engine.extract_mp3(str(FIXTURE), str(audio), progress, threading.Event())
    require(engine.probe(str(audio))["has_audio"], "MP3 extraction failed")
    parts = engine.split_video(str(FIXTURE), 1, str(RUN / "split"), progress, threading.Event())
    require(len(parts) == 2 and all(engine.probe(path)["duration"] > .7 for path in parts), "Split output did not create two playable parts")
    before = digest(audio)
    rejected = False
    try:
        engine.extract_mp3(str(FIXTURE), str(audio), progress, threading.Event())
    except FileExistsError:
        rejected = True
    require(rejected and digest(audio) == before, "Export overwrote an existing output")
    return dict(audio=str(audio), parts=parts, overwrite_rejected=True)


def ui_smoke():
    if not (ROOT / "desktop.py").is_file():
        raise DeferredCheck("desktop.py is still being implemented")
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDoubleSpinBox
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(RUN / "qt-settings"))
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("VideoEditorIndependentQA")
    app.setOrganizationName("VideoEditorIndependentQA")
    app.setStyle("Fusion")
    # The offscreen Windows Qt plugin does not populate the native font list.
    # Load a system-owned font for the saved QA render; no font is redistributed.
    for family_file in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "seguisb.ttf", "seguisym.ttf", "seguiemj.ttf"):
        system_font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / family_file
        if system_font.is_file():
            QFontDatabase.addApplicationFont(str(system_font))
    app.setFont(QFont("Segoe UI", 10))
    from desktop import IndependentWindow
    window = IndependentWindow(testing=True)
    messages = []
    exceptions = []
    previous_exception_handler = sys.excepthook
    sys.excepthook = lambda kind, value, trace: exceptions.append("".join(traceback.format_exception(kind, value, trace)))
    window.info = lambda title, message: messages.append(dict(title=title, message=message))
    window.resize(1600, 920)
    window.show()
    app.processEvents()
    try:
        require(window.windowTitle(), "Desktop window has no title")
        loader = getattr(window, "ingest", None) or getattr(window, "add_paths", None) or getattr(window, "add_videos", None)
        if loader is None:
            raise AssertionError("Desktop smoke needs its ingest/add_paths/add_videos entry point")
        loader([str(FIXTURE)])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.03)
            if getattr(window, "library", []) and not window.workers:
                break
        require(len(window.library) == 1 and len(window.cards) == 1, "Desktop ingest did not create its library/card")
        require(window.grid_views.currentIndex() == 1 and window.right_tabs.width() == 300, "Desktop card workspace or sidebar geometry incorrect")
        card = window.cards[0]
        require(card.path == str(FIXTURE) and not card.check.isChecked(), "Imported card has incorrect path/selection")
        card.check.setChecked(True)
        app.processEvents()
        require(len(window.selected_videos()) == 1 and str(FIXTURE) in window._selected_paths, "Card checkbox is not bound to the processing selection")
        speed = window.findChild(QDoubleSpinBox, "videoSpeed")
        mirror = window.findChild(QCheckBox, "mirrorVideo")
        require(speed is not None and mirror is not None, "Sidebar edit controls unavailable")
        speed.setValue(1.25)
        mirror.setChecked(True)
        require(abs(window.settings()["speed"] - 1.25) < .001 and window.settings()["mirror"], "Sidebar controls do not change renderer settings")
        window.controls["speed"].setValue(.9)
        require(abs(speed.value() - .9) < .001, "Sidebar speed does not synchronize from editor control")
        for name, key in (("pipelineRecognize", "recognize"), ("pipelineTranslate", "translate"), ("pipelineDub", "dub"), ("pipelineExport", "export")):
            toggle = window.findChild(QCheckBox, name)
            require(toggle is not None, f"Missing {name} toggle")
            toggle.setChecked(False)
            require(window.pipeline_config()[key] is False, f"{name} does not change workflow config")
        codec = window.findChild(QComboBox, "exportCodec")
        require(codec is not None and codec.count() >= 2, "Codec selector unavailable")
        codec.setCurrentIndex(1)
        require(window.enc_codec.currentIndex() == 1, "Sidebar codec selector is not bound to encoder")
        window.search_box.setText("không có kết quả")
        app.processEvents()
        require(not window.cards and window.grid_views.currentIndex() == 0, "Search did not filter the grid")
        window.search_box.clear()
        app.processEvents()
        require(len(window.cards) == 1 and window.cards[0].check.isChecked(), "Grid rebuild lost checkbox selection")
        window.columns.setCurrentIndex(0)
        app.processEvents()
        require(len(window.cards) == 1 and window.columns.currentIndex() == 0,
                "Grid layout selector failed")
        window.set_language("en")
        require("Components" in window.right_tabs.tabText(1), "UI language switch did not translate sidebar")
        window.set_language("vi")
        # Run the same selected-card export path used by the desktop button.
        # No recognition/translation/voice stage is enabled.
        window.pipe_recognize.setChecked(False)
        window.pipe_translate.setChecked(False)
        window.pipe_dub.setChecked(False)
        window.pipe_separate.setChecked(False)
        window.pipe_bilingual.setChecked(False)
        window.pipe_export.setChecked(True)
        window.pipe_folder.setText(str(RUN / "gui-export"))
        window.enc_device.setCurrentText("cpu")
        window.enc_codec.setCurrentText("h264")
        window.enc_format.setCurrentText("mp4")
        window.controls["speed"].setValue(1)
        window.mirror.setChecked(False)
        window.clips = [dict(path=str(FIXTURE), start=.2, end=1.2, duration=2)]
        window.run_pipeline("export")
        require(window.active_job is not None, "Desktop export button did not dispatch a worker")
        deadline = time.monotonic() + 30
        while (window.active_job is not None or window.workers) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.03)
        require(window.active_job is None and not window.workers, "Desktop export worker did not finish")
        require(not exceptions and not messages, f"Desktop worker errors: {exceptions or messages}")
        output = window.assets.get(str(FIXTURE), {}).get("video")
        require(output and Path(output).is_file(), "Desktop export did not attach the source card's rendered output")
        require(abs(engine.probe(output)["duration"] - 1) < .15, "Desktop export did not pass the clip trim to its renderer")
        require(window.resume_manifest and Path(window.resume_manifest).is_file(), "Desktop export did not retain a resumable master manifest")
        payload = json.loads(Path(window.resume_manifest).read_text(encoding="utf-8"))
        require(payload.get("kind") == "oneclick_export", "Desktop export saved a stage manifest in place of its master manifest")
        window.navigate(0)
        REPORT["artifacts"].update(gui_export=output, gui_export_manifest=window.resume_manifest)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        screenshot = RUN / "independent-ui-smoke.png"
        window.grab().save(str(screenshot))
        REPORT["artifacts"]["ui_screenshot"] = str(screenshot)
        return dict(title=window.windowTitle(), cards=len(window.cards), selected=len(window.selected_videos()),
                    bindings_verified=["speed", "mirror", "pipeline toggles", "codec", "search", "layout", "language"],
                    actual_gui_export=output, trim_verified=True, manifest=window.resume_manifest,
                    screenshot=str(screenshot))
    finally:
        deadline = time.monotonic() + 10
        while window.workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.03)
        window.dirty = False
        window.close()
        app.processEvents()
        sys.excepthook = previous_exception_handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui-only", action="store_true")
    arguments = parser.parse_args()
    check("Unicode sample import/probe and isolated data roots", generate_fixture)
    if not arguments.ui_only:
        check("Subtitle editing and SRT/VTT metadata round-trip", subtitle_roundtrip)
        check("Real export with text, subtitles, image, blur, filter and frame border", advanced_export)
        check("Real H.265/MOV encoding and trim", hevc_codec)
        check("Workflow manifest, active cancellation and cached resume", workflow_resume_and_cancel)
        check("Concurrent per-video export, ordered join and master resume", oneclick_join)
        check("MP3, split and existing-output protection", mini_tools)
    check("Independent desktop construction and ingestion smoke", ui_smoke)
    persist()
    print(json.dumps(dict(report=str(QA / REPORT_NAME), passed=REPORT["passed"], failed=REPORT["failed"]), ensure_ascii=False))
    return 1 if REPORT["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
