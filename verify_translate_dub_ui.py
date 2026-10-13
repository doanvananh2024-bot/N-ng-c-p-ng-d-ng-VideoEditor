"""Private offscreen regression QA for manual translation/dubbing and modal chrome.

Google HTTP and speech generation are substituted with known fixtures. The
actual window, worker threads, workflow, manifests, subtitles, timing and audio
assembly run normally. No user keys, project data, installed application or
network service is accessed. The generated audio is a test tone, not a claim of
real provider availability or voice quality.
"""
from __future__ import annotations

from array import array
from contextlib import ExitStack
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
import wave
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parent
QA = SOURCE.parent / "qa"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    QA.mkdir(exist_ok=True)
    run = QA / ("translate-dub-ui-" + uuid.uuid4().hex[:8])
    run.mkdir()
    os.environ.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"),
                      QT_QPA_PLATFORM="offscreen", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout, QWidget
    import shiboken6
    import ai_tools, desktop, dialogs, engine, local_tts, media_ai, providers, subtitle_tools, workflow
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(run / "qt-settings"))
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("IndependentTranslateDubPrivateQA")
    app.setApplicationName("IndependentTranslateDubPrivateQA")
    app.setStyle("Fusion")
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "run_dir": str(run),
              "offline": True, "checks": [], "qt_exceptions": [], "network_attempts": 0,
              "artifacts": {}, "mocked": ["Google HTTP transport", "speech synthesis", "missing-caption transcription"]}
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, trace: report["qt_exceptions"].append("".join(traceback.format_exception(kind, value, trace)))
    report_path = QA / "translate-dub-ui-report.json"
    def persist():
        report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
        report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
        report["status"] = "failed" if report["failed"] or report["qt_exceptions"] or report["network_attempts"] else "passed"
        data = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        report_path.write_text(data, encoding="utf-8")
        (run / report_path.name).write_text(data, encoding="utf-8")
    def check(name, function):
        start = time.monotonic()
        row = {"name": name}
        report["checks"].append(row)
        try:
            row.update(status="passed", details=function() or {})
            require(not report["qt_exceptions"], "Qt callbacks raised: " + "\n".join(report["qt_exceptions"])[-2500:])
        except Exception:
            row.update(status="failed", error=traceback.format_exc())
        row["seconds"] = round(time.monotonic()-start, 3)
        persist()
        print(json.dumps({key: row[key] for key in ("name", "status", "seconds")}), flush=True)
    def settle(predicate=lambda: True, timeout=15):
        deadline = time.monotonic() + timeout
        while True:
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if predicate():
                return
            require(time.monotonic() < deadline, "Qt worker/event did not settle")
            time.sleep(.01)
    def ffmpeg(arguments):
        result = subprocess.run([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", *arguments],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=40, creationflags=engine._creation_flags())
        require(result.returncode == 0, result.stderr.decode("utf-8", "replace")[-1500:])
    source = run / "with-captions.mp4"
    fresh = run / "needs-recognition.mp4"
    original = run / "edited-original.srt"
    stale_translation = run / "stale-translation.srt"
    stale_voice = run / "stale-voice.wav"
    rows = [{"start": .1, "end": .65, "text": "Hello editor", "voice": "ngochuyen:ngoc_huyen"},
            {"start": .8, "end": 1.4, "text": "Another sentence", "voice": "ngochuyen_v2:ngoc_huyen"}]
    translated_texts = {"Hello editor": "Xin chào biên tập viên", "Another sentence": "Một câu nữa"}
    request_calls, synth_calls, recognitions = [], [], []
    window = None

    def write_tone(path, duration=.3, frequency=660):
        samples = array("h", (int(4000 * math.sin(2*math.pi*frequency*i/24000)) for i in range(round(duration*24000))))
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(24000); writer.writeframes(samples.tobytes())
        return str(path)
    def fixtures():
        for target, color in ((source, "0x242424"), (fresh, "0x393939")):
            ffmpeg(["-n", "-f", "lavfi", "-i", f"color=c={color}:size=320x180:rate=12:duration=1.6",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=1.6", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(target)])
        subtitle_tools.save_subtitles(rows, original)
        subtitle_tools.save_subtitles([dict(row, text="OLD TRANSLATION") for row in rows], stale_translation)
        write_tone(stale_voice, 1.6, 220)
        report["artifacts"].update(source=str(source), fresh_source=str(fresh), original=str(original))
        return {"media_generated": 2, "private_settings": str(run / "qt-settings"), "original_sha256": hashlib.sha256(original.read_bytes()).hexdigest()}

    def early_filter():
        # Qt can reenter a Python virtual while QObject.__init__ dispatches to
        # its parent. Exercise the exact missing-overlay condition from logs.
        partial = dialogs._ModalChrome.__new__(dialogs._ModalChrome)
        QObject.__init__(partial)
        owner = QWidget()
        try:
            for kind in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Close):
                require(partial.eventFilter(owner, QEvent(kind)) is False, "Partly initialized chrome consumed event")
        finally:
            partial.deleteLater(); owner.deleteLater(); settle()
        return {"uninitialized_overlay_regression": "No AttributeError for Qt virtual callback before Python attributes"}

    def chrome_lifecycle():
        owner = QWidget(); owner.resize(900, 700); owner.show(); settle()
        instances = []
        try:
            for index in range(12):
                dialog = QDialog(owner)
                dialogs._reference_modal(dialog)
                chrome = dialog._reference_chrome
                instances.append(chrome)
                dialog.resize(400, 300); dialog.show(); settle()
                require(chrome.overlay is not None and chrome.overlay.isVisible(), "Shown modal did not dim its owner")
                owner.resize(900+index, 700+index); settle()
                require(chrome.overlay.geometry() == owner.rect(), "Backdrop failed to follow owner resize")
                dialog.hide(); settle()
                require(not chrome.overlay.isVisible(), "Hidden dialog left owner blocked")
                dialog.show(); settle(); dialog.reject(); settle()
                require(not chrome.overlay.isVisible(), "Rejected dialog left backdrop visible")
                dialog.close(); dialog.deleteLater(); settle()
                owner.resize(940+index, 720+index); settle()
            # Owner teardown may delete the sibling overlay before the dialog
            # event filter. No invalid-C++-wrapper exception may escape.
            dialog = QDialog(owner); dialogs._reference_modal(dialog); dialog.show(); settle()
            owner.deleteLater(); settle()
        finally:
            if shiboken6.isValid(owner): owner.close(); owner.deleteLater(); settle()
        return {"open_hide_reopen_reject_delete_cycles": 12, "owner_deleted_with_open_dialog": True}

    def window_start():
        nonlocal window
        window = desktop.IndependentWindow(testing=True)
        window.info = lambda title, text: messages.append([title, text])
        window.ai_confirm = lambda text: True
        window.out_dir = str(run / "output")
        window.pipe_folder.setText(window.out_dir)
        window.export_folder.setText(window.out_dir)
        window.resize(1600, 920); window.show(); settle()
        window.ingest([str(source), str(fresh)])
        settle(lambda: len(window.library) == 2 and not window.workers, 20)
        window.assets[str(source)] = {"original_subtitle": str(original), "translated_subtitle": str(stale_translation), "narration": str(stale_voice)}
        window._selected_paths = {str(source)}
        window._activate_media(str(source))
        window.pipe_voice.setCurrentText("ngochuyen:ngoc_huyen")
        window.tts_fit.setCurrentIndex(window.tts_fit.findData("natural"))
        window.voice_parallel.setValue(2)
        window.pipe_separate.setChecked(False); window.pipe_bilingual.setChecked(False)
        window.pipe_recognize.setChecked(True)
        window.pipe_source.setCurrentText("en"); window.pipe_target.setCurrentText("vi")
        window.translation_config.update(provider="google", image_context=False, scenario_prompt="")
        deadline = time.monotonic()+.8
        while time.monotonic() < deadline:
            settle(); time.sleep(.01)
        window._refresh_job_status()
        require(window._job_timer.isActive(), "Job status timer stopped before any job")
        require(window.provider_display.text() == "Google Translate", "Provider status did not refresh")
        require(window.run_all_button.isEnabled(), "Ready controls disabled after startup")
        return {"timer_interval_ms": window._job_timer.interval(), "provider_status": window.provider_display.text(), "selected": list(window._selected_paths)}

    messages = []
    def actual_dialogs():
        definitions = [lambda: dialogs.SettingsDialog(window, window.provider_store, window.scenario_store, window.prefs),
                       lambda: dialogs.DownloadsDialog(window, window.provider_store, window.scenario_store, window.prefs),
                       lambda: dialogs.ScenariosDialog(window, window.scenario_store),
                       lambda: dialogs.VoicePickerDialog(window, "vi-VN-HoaiMyNeural", catalog=[{
                           "ShortName": "vi-VN-HoaiMyNeural", "FriendlyName": "QA Voice", "Locale": "vi-VN", "Gender": "Female"}],
                           catalog_root=run / "private-voice-catalog")]
        observed = []
        for create in definitions:
            dialog = create()
            try:
                for _ in range(2):
                    dialog.show(); settle(); window.resize(window.width()+1, window.height()); settle()
                    require(dialog._reference_chrome.overlay.isVisible(), "Application modal lacks backdrop")
                    dialog.hide(); settle(); require(not dialog._reference_chrome.overlay.isVisible(), "Application backdrop remained visible")
                dialog.show(); settle(); dialog.reject(); settle()
                observed.append(type(dialog).__name__)
            finally:
                dialog.close(); dialog.deleteLater(); settle()
        # A task failure uses a nested informational modal. Execute its event
        # loop briefly and close it without substituting the dialog itself.
        error = QDialog(window); dialogs._reference_modal(error)
        layout = QVBoxLayout(error); layout.addWidget(QLabel("QA task failure"))
        button = QPushButton("Đóng"); button.clicked.connect(error.accept); layout.addWidget(button)
        QTimer.singleShot(20, button.click)
        error.exec(); require(not error._reference_chrome.overlay.isVisible(), "Error dialog blocked owner after accept")
        error.deleteLater(); settle(); window._refresh_job_status()
        return {"real_dialog_classes": observed, "error_modal_exec_recovered": True}

    class GoogleResponse:
        status_code = 200
        content = b"generated Google QA fixture"
        def __init__(self, text): self.text = text
        def json(self): return [[[translated_texts.get(self.text, "Dịch mới: " + self.text), self.text, None, None]]]
        def close(self): pass
    def google_request(method, url, **kwargs):
        require(method == "GET" and url == "https://translate.googleapis.com/translate_a/single", "Unexpected service request")
        text = kwargs["params"]["q"]; request_calls.append({"text": text, "target": kwargs["params"]["tl"]})
        return GoogleResponse(text)
    def google_batch(texts, target, cancel, timeout=90):
        # The old ai_tools Google path spawns a request process. Substitute its
        # transport boundary; parsing/metadata/publication stay real.
        ai_tools._check_cancel(cancel)
        request_calls.extend({"text": text, "target": target} for text in texts)
        return [translated_texts.get(text, "Dịch mới: " + text) for text in texts]
    async def synthesize(_edge, text, voice, path, cancel):
        ai_tools._check_cancel(cancel)
        synth_calls.append({"text": text, "voice": voice, "path": str(path), "device": local_tts.current_device()})
        write_tone(path, .25, 660 if "v2" in voice else 550)
    def transcription(video, output, *args, **kwargs):
        recognitions.append(str(video))
        return subtitle_tools.save_subtitles(rows, output)
    def stop_network(*args, **kwargs):
        report["network_attempts"] += 1
        raise AssertionError("Unexpected network attempt in isolated UI QA")
    def run_manual(stage):
        before = window.job_list.count()
        window.run_pipeline(stage)
        require(window.active_job is not None, "Manual " + stage + " did not start")
        window._refresh_job_status()
        require(window.stop_batch_button.isEnabled() and not window.run_all_button.isEnabled(), "Busy controls did not reflect running job")
        settle(lambda: window.active_job is None and not window.workers, 40)
        window._refresh_job_status()
        require(window.job_list.count() == before+1, "Manual stage did not create one job record")
        require(not window._batch_errors, "Manual " + stage + " failed: " + str(window._batch_errors))
        require(window.run_all_button.isEnabled() and not window.stop_batch_button.isEnabled(), "Idle controls did not recover")
        return dict(window.assets[str(source)])

    def manual_translation():
        before = hashlib.sha256(original.read_bytes()).hexdigest()
        old = stale_translation.read_bytes()
        clip = copy.deepcopy(window.clips)
        result = run_manual("translate")
        translated = subtitle_tools.load_subtitles(result["translated_subtitle"])
        require([row["text"] for row in translated] == list(translated_texts.values()), "Manual Translate reused stale translation or wrong source")
        require(request_calls and not recognitions, "Edited original was not reused or translation transport was not called")
        require([row.get("voice") for row in translated] == [row["voice"] for row in rows], "Translation lost per-cue voice assignments")
        require([(row["start"], row["end"]) for row in translated] == [(row["start"], row["end"]) for row in rows], "Translation changed cue timing")
        require(hashlib.sha256(original.read_bytes()).hexdigest() == before and stale_translation.read_bytes() == old, "Manual run overwrote a user caption file")
        require(window.clips == clip, "Translation reset source cut ranges")
        report["artifacts"]["manual_translation"] = result["translated_subtitle"]
        return {"new_texts": [row["text"] for row in translated], "transports_called": len(request_calls), "source_and_old_translation_preserved": True, "recognition_skipped_for_existing_captions": True}

    def manual_dubbing():
        old = hashlib.sha256(stale_voice.read_bytes()).hexdigest()
        clips = copy.deepcopy(window.clips)
        result = run_manual("dub")
        narration = Path(result["narration"])
        require(narration != stale_voice and narration.is_file(), "Manual Dub reused stale narration")
        require({item["text"] for item in synth_calls} == set(translated_texts.values()), "Dubbing did not synthesize translated dialogue")
        require({item["voice"] for item in synth_calls} == {row["voice"] for row in rows}, "Dubbing ignored per-cue local voice selection")
        with wave.open(str(narration), "rb") as reader:
            samples = array("h", reader.readframes(reader.getnframes()))
            duration = reader.getnframes()/reader.getframerate()
            require(reader.getnchannels() == 1 and reader.getframerate() == 24000 and max(abs(sample) for sample in samples) > 1000, "Narration invalid or silent")
        require(.9 < duration < 1.6, "Dubbing cue timeline was not assembled")
        require(hashlib.sha256(stale_voice.read_bytes()).hexdigest() == old and window.clips == clips, "Dubbing overwrote old voice or reset cuts")
        report["artifacts"]["manual_narration_test_tone"] = str(narration)
        return {"duration": duration, "real_ffmpeg_and_cue_mixing": True, "voices": sorted({item["voice"] for item in synth_calls}), "synthesized_texts": [item["text"] for item in synth_calls], "old_narration_preserved": True}

    def repeated_translation_sidecar():
        # The user's failed manifest contained translated rows that already
        # had original_text. Reusing that file used to expand **row and pass
        # original_text again, raising a duplicate-keyword TypeError.
        folder = run / "translation-sidecar-reuse"; folder.mkdir()
        input_path = window.assets[str(source)]["translated_subtitle"]
        loaded = subtitle_tools.load_subtitles(input_path)
        require(all(row.get("original_text") for row in loaded), "Regression fixture lacks original_text sidecar")
        before = len(request_calls)
        reused = workflow._translate(str(original), dict(provider="google", target_language="vi", reuse_artifacts=True,
                                     translated_srt=input_path), folder, lambda *_: None, threading.Event())
        roundtrip = subtitle_tools.load_subtitles(reused)
        require(roundtrip == loaded, "Reusing translated captions changed text/timing/voice metadata")
        require(len(request_calls) == before, "Cached translation unexpectedly called its transport")
        metadata_original = run / "original-already-has-original-text.srt"
        subtitle_tools.save_subtitles([dict(row, original_text="A PREVIOUS SOURCE TEXT", speaker="QA") for row in rows], metadata_original)
        forced = workflow._translate(str(metadata_original), dict(provider="google", target_language="vi", reuse_artifacts=True,
                                      force_translate=True, translated_srt=reused), folder, lambda *_: None, threading.Event())
        forced_rows = subtitle_tools.load_subtitles(forced)
        require([row["original_text"] for row in forced_rows] == [row["text"] for row in rows], "Fresh translation retained stale original_text metadata")
        require([row["voice"] for row in forced_rows] == [row["voice"] for row in rows] and all(row["speaker"] == "QA" for row in forced_rows), "Fresh translation lost source speaker/voice metadata")
        report["artifacts"]["reused_translation_with_metadata"] = reused
        return {"existing_original_text_reused_without_TypeError": True, "cached_transport_calls": 0,
                "fresh_translation_rebases_original_text": True, "speaker_and_voice_metadata_preserved": True}

    def selective_recognition():
        window._selected_paths = {str(source), str(fresh)}
        window._activate_media(str(source))
        before = window.job_list.count()
        window.run_pipeline("translate")
        require(window.active_job is not None, "Mixed manual translation did not start")
        settle(lambda: window.active_job is None and not window.workers, 40)
        require(window.job_list.count() == before+1 and not window._batch_errors, "Mixed manual translation failed: " + str(window._batch_errors))
        require(recognitions == [str(fresh)], "Recognition was not limited to the card without usable captions: " + str(recognitions))
        for target in (source, fresh):
            require([row["text"] for row in subtitle_tools.load_subtitles(window.assets[str(target)]["translated_subtitle"])] == list(translated_texts.values()), "Mixed batch result incorrect")
        return {"recognized_only": recognitions, "both_cards_translated": True}

    def failure_recovery():
        window._selected_paths = {str(source)}
        window._activate_media(str(source))
        old_paths = copy.deepcopy(window.assets[str(source)])
        before = len(messages)
        def fail(progress, cancel):
            progress(25, "QA failure")
            raise ValueError("QA translation failed safely")
        window.start_job("QA expected failure", fail)
        settle(lambda: window.active_job is None and not window.workers)
        window._refresh_job_status()
        require(len(messages) == before+1 and "QA translation failed safely" in messages[-1][1], "Worker failure did not reach user-visible error")
        require(window.assets[str(source)] == old_paths, "Failed worker damaged previous output assets")
        require(window.run_all_button.isEnabled() and not window.stop_batch_button.isEnabled(), "Failed worker left processing controls blocked")
        return {"error_presented": messages[-1], "previous_results_preserved": True, "controls_reenabled": True}

    def cancellation_recovery():
        entered = threading.Event()
        def cancellable(progress, cancel):
            progress(15, "QA cancellable")
            entered.set()
            while not cancel.wait(.01): pass
            ai_tools._check_cancel(cancel)
        window.start_job("QA cancellation", cancellable)
        settle(lambda: entered.is_set())
        window.cancel_job()
        settle(lambda: window.active_job is None and not window.workers)
        window._refresh_job_status()
        require(window.run_all_button.isEnabled() and not window.stop_batch_button.isEnabled(), "Cancelled worker left controls blocked")
        require(window._job_timer.isActive(), "Cancellation stopped status refresh timer")
        return {"cancel_event_observed": True, "no_thread_left_running": True, "timer_alive": True}

    def editor_selected_translator():
        target = run / "editor-gemini.srt"
        calls = []
        saved_config = copy.deepcopy(window.translation_config)
        window.translation_config.update(provider="gemini", model="gemini-qa-selected-model", image_context=False,
                                         scenario_prompt="Use concise dialogue.", batch_size=40, translate_workers=1)
        window.language.setCurrentText("vi")
        def response(method, url, provider, _pool, **kwargs):
            require(method == "POST" and provider == "gemini", "Subtitle editor ignored selected Gemini provider")
            require(url.endswith("/models/gemini-qa-selected-model:generateContent"), "Subtitle editor ignored selected model")
            payload = kwargs["payload"]
            prompt = payload["systemInstruction"]["parts"][0]["text"]
            require("Use concise dialogue." in prompt and "to vi" in prompt, "Editor lost selected prompt/target language")
            incoming = json.loads(payload["contents"][0]["parts"][0]["text"])["cues"]
            calls.append({"provider": provider, "model": "gemini-qa-selected-model", "source_texts": [cue["text"] for cue in incoming]})
            output = {"cues": [{"id": cue["id"], "text": translated_texts[cue["text"]]} for cue in incoming]}
            return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(output, ensure_ascii=False)}]}}]}
        try:
            with patch.object(window, "current_srt", return_value=str(original)), \
                 patch.object(window, "output_path", return_value=str(target)), \
                 patch.object(providers.ProviderStore, "get_keys", return_value=["generated-qa-fixture-not-a-real-key"]), \
                 patch.object(providers, "_request_json", side_effect=response):
                window.translate()
                require(window.active_job is not None, "Subtitle editor Translate did not start")
                settle(lambda: window.active_job is None and not window.workers)
            require(calls and target.is_file(), "Configured Gemini translator was not used by editor action")
            actual = subtitle_tools.load_subtitles(target)
            require([row["text"] for row in actual] == list(translated_texts.values()), "Editor translation file incorrect")
            require([row["voice"] for row in actual] == [row["voice"] for row in rows] and
                    [row["original_text"] for row in actual] == [row["text"] for row in rows], "Editor translation lost voice/original-text metadata")
            require(window.original_srt == str(original) and window._subtitle_role == "translated", "Editor did not activate translated result")
            report["artifacts"]["editor_selected_gemini_translation"] = str(target)
            return {"selected_transport": calls, "real_provider_json_decode_and_subtitle_publication": True,
                    "subtitle_metadata_preserved": True, "fixture_key_generated_in_memory": True}
        finally:
            window.translation_config = saved_config

    def editor_local_voice_options():
        source_srt = run / "editor-local-voices.srt"
        target = run / "editor-local-voice-test-tone.wav"
        editor_rows = [dict(rows[0], text=translated_texts[rows[0]["text"]]),
                       {"start": rows[1]["start"], "end": rows[1]["end"], "text": translated_texts[rows[1]["text"]]}]
        subtitle_tools.save_subtitles(editor_rows, source_srt)
        v2_voice = "ngochuyen_v2:ngoc_huyen_v_2"
        if window.pipe_voice.findText(v2_voice) < 0: window.pipe_voice.addItem(v2_voice)
        window.pipe_voice.setCurrentText(v2_voice)
        window.tts_device.setCurrentIndex(window.tts_device.findData("cuda"))
        window.tts_speed.setValue(1.25); window.tts_delay.setValue(120)
        window.tts_fit.setCurrentIndex(window.tts_fit.findData("natural"))
        window.voice_parallel.setValue(2); window.voice_max_speed.setValue(1.7)
        pronunciation = window.scenario_store.save_pronunciation("Private QA pronunciation", {"biên tập viên": "người dựng phim"})
        window.prefs.setValue("pronunciation_id", pronunciation["id"])
        window.refresh_scenarios()
        require(window.pipe_pronunciation.currentData() == pronunciation["id"], "Could not select private pronunciation fixture")
        before = len(synth_calls)
        options = []
        original_dub_rows = subtitle_tools.dub_rows
        def dub_with_capture(input_rows, output, default_voice, progress, cancel, **kwargs):
            options.append({"default_voice": default_voice, "device": local_tts.current_device(), **kwargs})
            return original_dub_rows(input_rows, output, default_voice, progress, cancel, **kwargs)
        with patch.object(window, "current_srt", return_value=str(source_srt)), \
             patch.object(window, "output_path", return_value=str(target)), \
             patch.object(subtitle_tools, "dub_rows", side_effect=dub_with_capture), \
             patch.object(window, "ai_confirm", side_effect=AssertionError("Local editor voice unexpectedly asks for online speech")):
            window.dub()
            require(window.active_job is not None, "Subtitle editor Dub did not start")
            settle(lambda: window.active_job is None and not window.workers, 40)
        require(target.is_file() and len(options) == 1, "Subtitle editor did not invoke configured local dubbing")
        require(options[0]["default_voice"] == v2_voice and options[0]["device"] == "cuda", "Editor ignored selected V2 voice or GPU device")
        for key, value in (("voice_speed", 1.25), ("voice_delay", 120), ("voice_fit", "natural"), ("voice_parallel", 2), ("voice_max_speed", 1.7)):
            require(options[0][key] == value, "Subtitle editor lost voice option " + key)
        calls = synth_calls[before:]
        require({row["voice"] for row in calls} == {rows[0]["voice"], v2_voice}, "Editor lost per-cue override/default local V2 voice")
        require(all(row["device"] == "cuda" for row in calls), "GPU preference did not propagate into cue worker contexts")
        require({row["text"] for row in calls} == {"Xin chào người dựng phim", "Một câu nữa"}, "Editor did not apply selected pronunciation dictionary")
        with wave.open(str(target), "rb") as reader:
            samples = array("h", reader.readframes(reader.getnframes()))
            rate = reader.getframerate(); duration = len(samples)/rate
            require(all(value == 0 for value in samples[:round(.22*rate)]), "Configured editor delay not applied to real narration")
            require(max(abs(value) for value in samples[round(.23*rate):round(.4*rate)]) > 1000, "Editor narration missing delayed first cue")
        require(window.voice_path.text() == str(target) and window.mute.isChecked(), "Editor did not attach narration/mute original voice")
        require(subtitle_tools.load_subtitles(source_srt) == editor_rows, "Pronunciation modified subtitle text")
        report["artifacts"]["editor_local_narration_test_tone"] = str(target)
        return {"selected_options": options[0], "cue_devices": [row["device"] for row in calls],
                "cue_voices": [row["voice"] for row in calls], "actual_audio_duration": duration,
                "real_timeline_delay_and_pronunciation": True, "original_subtitle_unchanged": True}

    try:
        with ExitStack() as mocks:
            mocks.enter_context(patch("requests.sessions.Session.request", side_effect=stop_network))
            mocks.enter_context(patch.object(providers.requests, "request", side_effect=google_request))
            mocks.enter_context(patch.object(ai_tools, "_translate_batch", side_effect=google_batch))
            mocks.enter_context(patch.object(ai_tools, "_synthesize", side_effect=synthesize))
            mocks.enter_context(patch.object(media_ai, "transcribe_model", side_effect=transcription))
            mocks.enter_context(patch.object(ai_tools, "transcribe", side_effect=transcription))
            check("private-synthetic-fixtures", fixtures)
            check("partly-initialized-modal-event-filter", early_filter)
            check("modal-open-hide-reopen-delete-lifecycle", chrome_lifecycle)
            check("window-job-timer-ready-state", window_start)
            check("actual-dialogs-and-error-modal-return", actual_dialogs)
            check("manual-translate-regenerates-stale-output-keeps-edited-captions", manual_translation)
            check("translation-sidecar-original-text-duplicate-keyword-regression", repeated_translation_sidecar)
            check("manual-dub-regenerates-stale-output-keeps-cue-voices", manual_dubbing)
            check("manual-translate-selectively-recognizes-missing-caption-card", selective_recognition)
            check("worker-failure-preserves-results-recovers-controls", failure_recovery)
            check("worker-cancellation-recovers-controls-and-timer", cancellation_recovery)
            check("subtitle-editor-translation-honors-configured-gemini-model", editor_selected_translator)
            check("subtitle-editor-local-v2-honors-gpu-cue-options-and-pronunciation", editor_local_voice_options)
    finally:
        if window is not None:
            if window.active_job: window.cancel_job(); settle(lambda: window.active_job is None, 40)
            window.dirty = False; window.close(); window.deleteLater(); settle()
        sys.excepthook = previous_hook
        persist()
    print(json.dumps({"status": report["status"], "passed": report["passed"], "failed": report["failed"], "report": str(report_path)}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
