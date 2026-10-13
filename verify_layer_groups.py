"""Offline grouped-layer and independent S0/S1 subtitle integration QA.

All media, subtitle files, private settings, projects and screenshots are
generated under this stage's qa directory. No installed app or keys are used.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent
QA = SOURCE.parent / "qa"
REFERENCE_IMAGE = Path("C:/Users/doanv/AppData/Local/Temp/codex-clipboard-11f7fa2f-6aa4-481c-a87d-3d503c811582.png")

def require(value, message):
    if not value: raise AssertionError(message)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--render-only", action="store_true"); parser.add_argument("--ui-only", action="store_true")
    options = parser.parse_args()
    QA.mkdir(exist_ok=True); run = QA / ("layer_groups_" + uuid.uuid4().hex[:8]); run.mkdir()
    original_local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    os.environ.setdefault("MEDIAFUSION_LOCAL_TTS_ROOT", str(original_local / "VideoEditor/tts"))
    os.environ.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"), QT_QPA_PLATFORM="offscreen",
                      QT_QPA_FONTDIR="C:/Windows/Fonts", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    from PIL import Image
    import numpy as np
    import ai_tools, engine, render_pipeline, subtitle_tools
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.IniFormat); QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(run / "qt-settings"))
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("IndependentLayerGroupsQA"); app.setOrganizationName("IndependentLayerGroupsQA"); app.setStyle("Fusion")
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguiemj.ttf", "arial.ttf", "arialbd.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.is_file(): QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI", 10))
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "run_dir": str(run), "offline": True,
              "checks": [], "artifacts": {}, "network_attempts": 0, "qt_exceptions": [],
              "reference": {"image": str(REFERENCE_IMAGE), "logical_sidebar": 300, "comparison_scale": 1.25,
                            "claim": "Measured grouping/layout and behavior; no pixel-perfect or all-feature parity claim"}}
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, trace: report["qt_exceptions"].append("".join(traceback.format_exception(kind, value, trace)))
    report_path = QA / ("layer-groups-" + ("render" if options.render_only else "ui" if options.ui_only else "source") + "-report.json")
    def persist():
        report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
        report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
        report["status"] = "failed" if report["failed"] or report["qt_exceptions"] or report["network_attempts"] else "passed"
        data = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        report_path.write_text(data, encoding="utf-8"); (run / report_path.name).write_text(data, encoding="utf-8")
    def check(name, function):
        start = time.monotonic(); row = {"name": name}; report["checks"].append(row)
        try:
            row.update(status="passed", details=function() or {})
            require(not report["qt_exceptions"], "Qt slot exceptions occurred")
        except Exception: row.update(status="failed", error=traceback.format_exc())
        row["seconds"] = round(time.monotonic()-start, 3); persist()
        print(json.dumps({key: row[key] for key in ("name", "status", "seconds")}, ensure_ascii=True), flush=True)
    def settle(predicate=lambda: True, timeout=10):
        end = time.monotonic() + timeout
        while True:
            app.processEvents(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if predicate(): return
            require(time.monotonic() < end, "Qt worker did not settle")
            time.sleep(.01)
    def ffmpeg(arguments):
        result = subprocess.run([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", *arguments],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=40, creationflags=engine._creation_flags())
        require(result.returncode == 0, result.stderr.decode("utf-8", "replace")[-1500:]); return result.stdout
    source = run / "source.mp4"; original = run / "original.srt"; translated = run / "translated.srt"; poster = run / "poster.png"
    def fixtures():
        ffmpeg(["-n", "-f", "lavfi", "-i", "color=c=0x202020:size=320x180:rate=12:duration=2", "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "0", "-pix_fmt", "yuv420p", str(source)])
        ai_tools.save_srt([{"start": .2, "end": 1.2, "text": "ORIGINAL ALPHA"}], original)
        ai_tools.save_srt([{"start": .5, "end": 1.5, "text": "TRANSLATED BETA"}], translated)
        Image.open(BytesIO(ffmpeg(["-i", str(source), "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"]))).save(poster)
        report["artifacts"].update(source=str(source), original=str(original), translated=str(translated))
        return {"private_media": str(source), "cue_texts_differ": True, "cue_times_differ": True}
    def tracks(s0=True, s1=True):
        return [{"role": "original", "path": str(original), "enabled": s0,
                 "style": {"font": "Arial", "size": 22, "bold": True, "color": "#ffff00", "outline": 0, "alignment": 8, "text_effect": "none"}},
                {"role": "translated", "path": str(translated), "enabled": s1,
                 "style": {"font": "Arial", "size": 22, "bold": True, "color": "#00ffff", "outline": 0, "alignment": 2, "text_effect": "none"}}]
    def configuration(s0=True, s1=True):
        return {"width": 320, "height": 180, "fps": 12, "subtitle_tracks": tracks(s0, s1),
                "subtitle_path": str(original), "subtitle_style": {"color": "#ff00ff", "size": 30},
                "encoder": {"format": "mp4", "codec": "h264", "device": "cpu", "preset": "fast", "quality": 16}}
    def frame(path, seconds=.75):
        return np.asarray(Image.open(BytesIO(ffmpeg(["-ss", str(seconds), "-i", str(path), "-frames:v", "1",
                     "-f", "image2pipe", "-c:v", "png", "pipe:1"]))).convert("RGB"))
    def colors(image):
        r, g, b = image[..., 0].astype(int), image[..., 1].astype(int), image[..., 2].astype(int)
        top, bottom = slice(0, 90), slice(90, 180)
        yellow = (r > 140) & (g > 140) & (b < 100)
        cyan = (g > 140) & (b > 140) & (r < 100)
        magenta = (r > 140) & (b > 140) & (g < 100)
        return {"yellow_top": int(yellow[top].sum()), "cyan_bottom": int(cyan[bottom].sum()), "magenta": int(magenta.sum())}
    outputs = {}
    def per_role_render():
        expected = {"both": (True, True), "original_off": (False, True), "translated_off": (True, False), "both_off": (False, False)}
        observed = {}
        for name, flags in expected.items():
            target = run / (name + ".mp4")
            render_pipeline.render_project([{"path": str(source)}], configuration(*flags), str(target), lambda *_: None, threading.Event())
            outputs[name] = target; image = frame(target); info = colors(image); observed[name] = info
            Image.fromarray(image).save(run / (name + ".png")); report["artifacts"][name] = str(target)
            require((info["yellow_top"] > 100) == flags[0] and (info["cyan_bottom"] > 100) == flags[1], "Role rendering does not honor independent flags: " + str(info))
            require(info["magenta"] < 10, "Legacy subtitle rendered in addition to explicit tracks")
        require(abs(observed["both"]["cyan_bottom"]-observed["original_off"]["cyan_bottom"]) < 60, "S0 flag changed S1 style/output")
        require(abs(observed["both"]["yellow_top"]-observed["translated_off"]["yellow_top"]) < 60, "S1 flag changed S0 style/output")
        return {"decoded_pixel_counts": observed, "legacy_not_double_rendered": True}
    def role_timing():
        observed = {}
        for timepoint, expected in ((.3, (True, False)), (.75, (True, True)), (1.3, (False, True)), (1.75, (False, False))):
            info = colors(frame(outputs["both"], timepoint)); observed[str(timepoint)] = info
            require((info["yellow_top"] > 100, info["cyan_bottom"] > 100) == expected, "Caption tracks share incorrect timeline at " + str(timepoint))
        return {"distinct_cue_boundaries": observed}
    def role_preview():
        from preview_canvas import EditablePreview
        observed = {}
        for name, flags in (("both", (True, True)), ("original_off", (False, True)), ("translated_off", (True, False)), ("both_off", (False, False))):
            preview = EditablePreview(engine.probe(str(source)), str(poster), configuration(*flags),
                                      {"original_subtitle": str(original), "translated_subtitle": str(translated)})
            try:
                preview.resize(320, 180); preview.show(); preview.set_position(.75); settle()
                image_path = run / ("preview-" + name + ".png"); require(preview.grab().save(str(image_path)), "Preview capture failed")
                info = colors(np.asarray(Image.open(image_path).convert("RGB"))); observed[name] = info
                require((info["yellow_top"] > 100, info["cyan_bottom"] > 100) == flags, "Preview ignores independent role flags/styles: " + str(info))
                require(info["magenta"] < 10, "Legacy preview caption is double-rendered")
            finally: preview.close(); preview.deleteLater(); settle()
        return {"actual_preview_pixels": observed}
    def negative_tracks():
        for malformed in ([{"role": "original", "path": str(original)}, {"role": "original", "path": str(translated)}],
                          [{"role": "unsupported", "path": str(original)}]):
            target = run / ("invalid-" + uuid.uuid4().hex[:5] + ".mp4")
            config = configuration(); config["subtitle_tracks"] = malformed
            try: render_pipeline.render_project([{"path": str(source)}], config, str(target), lambda *_: None, threading.Event())
            except (ValueError, ai_tools.AIError): pass
            else: raise AssertionError("Malformed/duplicate subtitle role configuration accepted")
            require(not target.exists(), "Invalid subtitle tracks published a video")
        return {"invalid_roles_rejected": True, "duplicate_roles_rejected": True, "no_invalid_publication": True}
    def workflow_roles():
        import workflow
        config = configuration()
        config["speed"] = 2
        for track in config["subtitle_tracks"]: track["path"] = str(run / "stale-role-path.srt")
        input = {"source": str(source), "clip": {"path": str(source), "start": .4, "end": 1.6},
                 "config": {"settings": config, "sub_lead": .1}}
        initial = copy.deepcopy(input)
        entry = {"paths": {"original_subtitle": str(original), "translated_subtitle": str(translated), "subtitle": str(translated)}}
        folder = run / "workflow-role-export"; folder.mkdir()
        result = workflow._execute_stage("export", input, entry, folder, lambda *_: None, threading.Event())
        require(input == initial, "Workflow export mutated the input settings snapshot")
        target = result["video"]; duration = float(engine.probe(target)["duration"])
        require(abs(duration-.6) < .12, "Workflow trim/speed duration incorrect")
        observations = {}
        # Sample before the final PTS (0.5005 s at 12 fps), so seek can return
        # an actual frame even though the encoded container lasts ~0.584 s.
        for seconds, expected in ((.1, (True, True)), (.35, (False, True)), (.49, (False, False))):
            info = colors(frame(target, seconds)); observations[str(seconds)] = info
            require((info["yellow_top"] > 100, info["cyan_bottom"] > 100) == expected,
                    "Workflow roles were not rebound/retimed exactly once: " + str(observations))
        report["artifacts"]["workflow_roles"] = target
        return {"duration": duration, "trim": [.4, 1.6], "speed": 2, "subtitle_lead": .1,
                "entry_paths_override_stale_role_paths": True, "timing_pixels": observations, "input_snapshot_unchanged": True}
    def grouped_ui():
        from PySide6.QtWidgets import QPushButton, QFileDialog, QInputDialog
        from desktop import IndependentWindow
        import soundfile as sf
        second = run / "second-card.mp4"; shutil.copyfile(source, second)
        second_original = run / "second-original.srt"; second_translated = run / "second-translated.srt"
        ai_tools.save_srt([{"start": .1, "end": 1.8, "text": "SECOND ORIGINAL"}], second_original)
        ai_tools.save_srt([{"start": .1, "end": 1.8, "text": "SECOND TRANSLATED"}], second_translated)
        image_file = run / "overlay.png"; Image.new("RGB", (32, 32), "red").save(image_file)
        audio_file = run / "extra-audio.wav"
        sf.write(audio_file, .05*np.sin(2*np.pi*440*np.arange(48000)/48000), 48000)
        windows = []; window = IndependentWindow(testing=True); windows.append(window)
        def merged(settings): return {**settings, **(settings.get("advanced") or {})}
        def by_role(settings): return {row["role"]: row for row in merged(settings).get("subtitle_tracks", [])}
        def click(name, owner=window):
            button = owner.findChild(QPushButton, name)
            require(button is not None, "Missing action " + name); button.click(); settle()
        messages = []; window.info = lambda title, text: messages.append([title, text])
        try:
            window.resize(1600, 920); window.show(); settle()
            require(all(not button.isEnabled() for button in window.layer_add_buttons), "Add controls enabled without a card")
            window.ingest([str(source), str(second)])
            settle(lambda: len(window.cards) == 2 and not window.workers, 15)
            window.assets[str(source)] = {"original_subtitle": str(original), "translated_subtitle": str(translated)}
            window.assets[str(second)] = {"original_subtitle": str(second_original), "translated_subtitle": str(second_translated)}
            window._activate_media(str(source)); window.right_tabs.setCurrentIndex(1); window.component_tabs.setCurrentIndex(0); settle()
            require(not window.inline_layers.isVisible(), "Legacy flat list is visible")
            require("subtitle_tracks" not in merged(window.settings()), "Loading legacy assets materialized explicit subtitle tracks")
            tags = {row["tag"] for row in window.layer_groups.rows()}
            require({"V", "S0", "S1", "A0", "A1"}.issubset(tags), "Base grouped roles are missing")
            for tag in ("V", "S0", "S1", "A0", "A1"):
                row = window.findChild(__import__("layer_groups").LayerRow, "layerRow_" + tag)
                require(row is not None and row.delete_button is None, "Base role can be deleted: " + tag)
            baseline_style = copy.deepcopy(window.advanced.get("subtitle_style"))
            click("layerSelect_S0")
            require(window._subtitle_selection == "original" and window.component_tabs.currentIndex() == 1,
                    "S0 selection did not route to its detail panel")
            require(window.subtitle_file_label.text() == original.name, "S0 detail used the wrong subtitle path")
            require("subtitle_tracks" not in merged(window.settings()), "Viewing S0 changed legacy behavior")
            window.subtitle_detail_controls["size"].setValue(28)
            window.subtitle_detail_controls["color"].setText("#ffff00")
            window.subtitle_detail_controls["alignment"].setCurrentIndex(window.subtitle_detail_controls["alignment"].findData(8))
            window.subtitle_detail_enabled.setChecked(True); settle()
            s0 = copy.deepcopy(by_role(window.settings())["original"])
            s1_before = copy.deepcopy(by_role(window.settings())["translated"])
            click("layerSelect_S1")
            window.subtitle_detail_controls["size"].setValue(19)
            window.subtitle_detail_controls["color"].setText("#00ffff")
            window.subtitle_detail_controls["alignment"].setCurrentIndex(window.subtitle_detail_controls["alignment"].findData(2))
            window.subtitle_detail_enabled.setChecked(True); settle()
            roles = by_role(window.settings())
            require(roles["original"] == s0 and roles["translated"]["style"]["size"] == 19,
                    "Editing S1 altered S0 or failed to store its own style")
            require(window.advanced.get("subtitle_style") == baseline_style, "Per-role style overwrote legacy global style")
            require(roles["original"]["path"] == str(original) and roles["translated"]["path"] == str(translated), "Role paths crossed")
            window.component_tabs.setCurrentIndex(0); settle()
            action_counts = {}
            expected_keys = {"text": "text_layers", "media": "image_layers", "blur": "blur_zones", "filter": "filter_layers", "audio": "audio_layers"}
            for identity, key in expected_keys.items():
                before = {name: len(window.advanced.get(name, [])) for name in expected_keys.values()}
                with patch.object(QInputDialog, "getMultiLineText", return_value=("First title", True)), \
                     patch.object(QFileDialog, "getOpenFileName", return_value=(str(audio_file if identity == "audio" else image_file), "")):
                    click("layerAdd_" + identity)
                after = {name: len(window.advanced.get(name, [])) for name in expected_keys.values()}
                require(after[key] == before[key]+1 and all(after[name] == before[name] for name in after if name != key),
                        "Add button changed the wrong type: " + identity)
                action_counts[identity] = {"before": before[key], "after": after[key]}
                window.component_tabs.setCurrentIndex(0); settle()
            require(window.advanced["image_layers"][0]["path"] == str(image_file), "Media action did not retain selected file")
            require(window.advanced["audio_layers"][0]["path"] == str(audio_file), "Audio action did not retain selected file")
            with patch.object(QInputDialog, "getMultiLineText", return_value=("Second title", True)): click("layerAdd_text")
            window.component_tabs.setCurrentIndex(0); settle()
            for tag in ("I0", "F0", "A2"):
                row = window.findChild(__import__("layer_groups").LayerRow, "layerRow_" + tag)
                require(row is not None and row.delete_button is not None, "Added row lacks deletion action: " + tag)
                row.delete_button.click(); settle(); window.component_tabs.setCurrentIndex(0); settle()
            click("layerSelect_T0")
            require(window.layer_selection == ("text_layers", 0), "Title select button did not target its actual layer")
            window.component_tabs.setCurrentIndex(0); settle()
            window.layer_groups.row_for(("blur_zones", 0)).switch.click(); settle()
            require(window.advanced["blur_zones"][0]["enabled"] is False, "Blur switch did not change render settings")
            window.layer_groups.row_for(("subtitle", "original")).switch.click(); settle()
            require(by_role(window.settings())["original"]["enabled"] is False and by_role(window.settings())["translated"]["enabled"] is True,
                    "Subtitle row switch changed the other role")
            require(window.right_tabs.width() == 300 and set(window.layer_groups.groups) == {"media", "text", "blur", "audio"},
                    "Screenshot fixture groups do not match expected nonempty groups")
            counts = {key: len(group.rows) for key, group in window.layer_groups.groups.items()}
            require(counts == {"media": 1, "text": 4, "blur": 1, "audio": 2}, "Group counts do not match row identities")
            click("layerSelect_V"); window.component_tabs.setCurrentIndex(0); settle()
            screenshot = run / "grouped-sidebar.png"; require(window.right_tabs.grab().save(str(screenshot)), "Sidebar screenshot failed")
            scaled = run / "grouped-sidebar-125pct.png"
            image = Image.open(screenshot); image.resize((375, round(image.height*1.25)), Image.Resampling.LANCZOS).save(scaled)
            report["artifacts"].update(grouped_sidebar=str(screenshot), grouped_sidebar_scaled=str(scaled))
            metrics = {key: {"row_height": group.rows[0].height(), "count": len(group.rows), "radius_logical": 8} for key, group in window.layer_groups.groups.items()}
            require(all(value["row_height"] == 38 for value in metrics.values()), "Group row height differs from designed 38 logical px")
            window.layer_groups.groups["text"].header.click(); settle()
            require(window.layer_groups.groups["text"].body.isHidden(), "Group header did not collapse rows")
            source_snapshot = copy.deepcopy(merged(window.settings()))
            window._activate_media(str(second)); settle()
            require(window.layer_groups.groups["text"].body.isHidden(), "Card change reset collapsed group state")
            require("subtitle_tracks" not in merged(window.settings()), "S0/S1 explicit state leaked into untouched card")
            window._choose_subtitle_detail("original"); settle()
            require(window.subtitle_file_label.text() == second_original.name, "New card detail retained prior card SRT")
            window._activate_media(str(source)); settle()
            require(by_role(window.settings()) == {row["role"]: row for row in source_snapshot["subtitle_tracks"]},
                    "Returning to card lost S0/S1 style or enable state")
            window.component_tabs.setCurrentIndex(0); window.layer_groups.groups["text"].header.click(); settle()
            window.layer_groups.row_for(("text_layers", 0)).delete_button.click(); settle()
            require([row["text"] for row in window.advanced["text_layers"]] == ["Second title"], "Delete targeted wrong title")
            require(window.layer_groups.row_for(("subtitle", "original")) is not None and window.layer_groups.row_for(("subtitle", "translated")) is not None,
                    "Deleting title removed protected subtitle roles")
            window.component_tabs.setCurrentIndex(0); settle()
            window.layer_groups.groups["text"].header.click(); settle()
            require(window.layer_groups.groups["text"].body.isHidden(), "Cannot persist a collapsed state before reopening project")
            project = run / "grouped-project.mfs"; window.project_path = str(project)
            require(window.save_project(), "Cannot save grouped project")
            saved_settings = copy.deepcopy(window.video_settings[str(source)])
            # Testing windows clear their private QSettings at construction.
            # Preserve this isolated test store when simulating an app reopen.
            window.prefs.sync()
            with patch.object(QSettings,"clear",lambda owner:None):
                restored = IndependentWindow(testing=True)
            windows.append(restored); restored.confirm_new = lambda: True
            restored.info = lambda title, text: messages.append([title, text])
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(project), "")): restored.open_project()
            settle(lambda: len(restored.library) == 2 and not restored.workers, 15)
            restored._activate_media(str(source)); settle()
            require(restored.layer_groups.groups["text"].body.isHidden(), "New window/project load ignored saved group collapse preference")
            require(by_role(restored.settings()) == by_role(saved_settings), "Project roundtrip lost per-role styles or enabled flags")
            require(restored.advanced["blur_zones"][0]["enabled"] is False and restored.advanced["text_layers"][0]["text"] == "Second title",
                    "Project roundtrip lost deletion/toggle result")
            require(not messages, "Unexpected application messages: " + str(messages))
            window.apply_all(); settle()
            copied=by_role(window.video_settings[str(second)])
            require(copied['original']['path']==str(second_original) and copied['translated']['path']==str(second_translated),
                    "Apply all copied another video's caption contents")
            require(copied['original']['style']==by_role(window.settings())['original']['style'],
                    "Apply all did not copy selected role style")
            return {"five_add_actions": action_counts, "group_counts": counts, "geometry": metrics, "legacy_flat_list_hidden": True,
                    "protected_base_rows": True, "subtitle_style_isolation": True, "card_style_and_path_isolation": True,
                    "collapse_survives_card_change": True, "collapse_preference_restored_new_window": True,
                    "project_roundtrip": True, "apply_all_keeps_role_paths":True,"screenshot": str(scaled)}
        finally:
            for owner in windows:
                settle(lambda: not owner.workers, 5); owner.dirty = False; owner.close(); owner.deleteLater(); settle()
    def compile_sources():
        result = {}
        for name in ("layer_groups", "subtitle_detail", "desktop", "preview_canvas", "advanced_render", "workflow", "render_pipeline"):
            path = SOURCE / (name + ".py")
            require(path.exists(), "Required source is missing: " + name)
            data = path.read_bytes(); compile(data, str(path), "exec"); result[name] = hashlib.sha256(data).hexdigest()
        report["source_sha256"] = result; return result
    import requests
    def no_http(*args, **kwargs):
        report["network_attempts"] += 1; raise AssertionError("Unexpected network request in layer-group QA")
    try:
        with patch.object(requests, "request", no_http):
            check("Grouped layer and per-role subtitle sources compile", compile_sources)
            check("Private independent subtitle fixtures", fixtures)
            if not options.ui_only:
                check("S0/S1 independent enable/style export pixels and legacy override", per_role_render)
                check("S0/S1 cue times stay independent", role_timing)
                check("Actual preview paints each enabled subtitle role independently", role_preview)
                check("Invalid subtitle roles cannot publish a render", negative_tracks)
                check("Workflow binds S0/S1 assets and applies trim/speed/lead once", workflow_roles)
            if not options.render_only: check("Grouped selection, toggles, deletion and persistence", grouped_ui)
    finally: sys.excepthook = previous_hook
    persist(); print(json.dumps({"report": str(report_path), "passed": report["passed"], "failed": report["failed"], "status": report["status"]}))
    return 0 if report["status"] == "passed" else 1

if __name__ == "__main__": raise SystemExit(main())
