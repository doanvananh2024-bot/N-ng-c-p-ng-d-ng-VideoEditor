"""Offline integration QA of the owned editor's local voices and GPU runtime.

Real phrase inference runs against installed, read-only models. Mixed cue
dispatch reuses those generated audio fixtures and invokes real FFmpeg timing.
Private app data, outputs, screenshots and reports are confined to stage/qa.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import types
import uuid
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent
QA = SOURCE.parent / "qa"
VOICES = ("ngochuyen:ngoc_huyen", "ngochuyen_v2:ngoc_huyen_v_2")
PHRASE = "Xin chào, đây là giọng đọc Ngọc Huyền."

def require(value, message):
    if not value: raise AssertionError(message)

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--ui-only", action="store_true")
    parser.add_argument("--gpu-only", action="store_true")
    parser.add_argument("--exe")
    options = parser.parse_args()
    QA.mkdir(parents=True, exist_ok=True)
    phase = "packaged" if options.exe else "ui" if options.ui_only else "gpu" if options.gpu_only else "backend" if options.backend_only else "source"
    run = QA / (phase + "_" + uuid.uuid4().hex[:8]); run.mkdir()
    gpu_dependencies = SOURCE.parent.parent / "VideoEditorIndependent-1.2.1-gpu-voices" / "gpu-deps"
    if gpu_dependencies.is_dir() and not options.exe:
        sys.path.insert(0, str(gpu_dependencies))
    original_local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    # Access to shared packs is explicit and read-only, independently of QA appdata.
    os.environ.setdefault("VEI_DEMUCS_PACK", str(original_local / "VideoEditor/voicesep/smartvoice_cuda"))
    os.environ.setdefault("VEI_CUDA_PATH", str(original_local / "VideoEditor/cuda"))
    os.environ.setdefault("MEDIAFUSION_LOCAL_TTS_ROOT", str(original_local / "VideoEditor/tts"))
    os.environ.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"),
                      HF_HOME=str(run / "hf-cache"), TORCH_HOME=str(run / "torch-cache"),
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", QT_QPA_PLATFORM="offscreen",
                      QT_QPA_FONTDIR="C:/Windows/Fonts", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    import ai_tools, engine
    import numpy as np
    import soundfile as sf
    report = {"phase": phase, "created_at": datetime.now(timezone.utc).isoformat(), "run_dir": str(run),
              "offline": True, "checks": [], "artifacts": {}, "network_attempts": 0}
    target_report = QA / ("gpu-voices-" + phase + "-report.json")

    def persist():
        report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
        report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
        report["status"] = "failed" if report["failed"] or report["network_attempts"] else "passed"
        target_report.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        (run / target_report.name).write_text(target_report.read_text(encoding="utf-8"), encoding="utf-8")

    def check(name, fn):
        entry = {"name": name}; start = time.monotonic()
        report["checks"].append(entry)
        try: entry.update(status="passed", details=fn() or {})
        except Exception: entry.update(status="failed", error=traceback.format_exc())
        entry["seconds"] = round(time.monotonic() - start, 3)
        persist()
        print(json.dumps({k: entry[k] for k in ("name", "status", "seconds")}, ensure_ascii=True), flush=True)

    def no_network(*args, **kwargs):
        report["network_attempts"] += 1
        raise AssertionError("Unrequested network access during offline QA")

    def audio(path):
        signal, rate = sf.read(str(path), dtype="float32", always_2d=True)
        duration = len(signal) / rate
        rms = float(np.sqrt(np.mean(np.square(signal)))) if len(signal) else 0
        require(len(signal) > 1000 and duration > .1 and np.isfinite(signal).all() and rms > .0001,
                "Output audio is empty, non-finite, or silent")
        probe = engine.probe(str(path))
        require(float(probe.get("duration", 0)) > .1, "FFprobe cannot decode output duration")
        return {"path": str(path), "bytes": Path(path).stat().st_size, "sample_rate": rate,
                "channels": signal.shape[1], "duration": round(duration, 4), "rms": round(rms, 7), "sha256": digest(path)}

    def source_compile():
        names = ("local_tts", "gpu_runtime", "voice_catalog", "ai_tools", "workflow", "media_ai",
                 "optional_reference_dialogs", "desktop", "dialogs", "main")
        hashes = {}
        for name in names:
            path = SOURCE / (name + ".py")
            if not path.is_file(): continue
            data = path.read_bytes(); compile(data, str(path), "exec"); hashes[name] = hashlib.sha256(data).hexdigest()
        require("local_tts" in hashes and "gpu_runtime" in hashes, "New backend source files are missing")
        report["source_sha256"] = hashes
        return hashes

    generated = {}
    def real_voices():
        import local_tts
        states, result = {}, {}
        for identity in VOICES:
            provider = identity.split(":", 1)[0]
            state = local_tts.availability(provider); states[provider] = state
            require(state.get("ready"), "Local voice model is unavailable: " + str(state))
            require(any(row["ShortName"] == identity for row in local_tts.catalog(provider)), "Canonical local ID missing")
            output = run / (provider + ".wav")
            selected_device = "cuda" if gpu_dependencies.is_dir() else "auto"
            returned = local_tts.synthesize(PHRASE, identity, str(output), device=selected_device)
            require(Path(returned).resolve() == output.resolve(), "Synthesizer returned an unexpected output")
            generated[identity] = output
            result[identity] = audio(output)
            result[identity]["runtime"] = local_tts.last_runtime(identity)
            result[identity]["model_sha256"] = digest(state["model"])
            if selected_device == "cuda": require(result[identity]["runtime"]["device"] == "cuda", "Explicit CUDA used CPU")
            mp3 = run / (provider + ".mp3")
            subprocess.run([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                            "-i", str(output), "-c:a", "libmp3lame", str(mp3)], check=True, capture_output=True,
                           timeout=30, creationflags=engine._creation_flags())
            generated[identity + "-mp3"] = mp3
        require(result[VOICES[0]]["sha256"] != result[VOICES[1]]["sha256"], "Both variants produced identical audio")
        require(result[VOICES[0]]["model_sha256"] != result[VOICES[1]]["model_sha256"], "Both voice IDs used the same ONNX model")
        report["local_model_states"] = states; report["artifacts"]["real_phrases"] = result
        return {"real_model_inference": True, "phrase": PHRASE, "variants": result, "states": states}

    def cancellation_and_overwrite():
        import local_tts
        cancelled = threading.Event(); cancelled.set()
        target = run / "pre-cancelled.wav"
        try: local_tts.synthesize(PHRASE, VOICES[0], target, cancelled)
        except ai_tools.CancelledError: pass
        else: raise AssertionError("Pre-cancelled synthesis was accepted")
        require(not target.exists(), "Cancelled synthesis published output")
        existing = run / "existing.wav"; existing.write_bytes(b"existing-user-audio")
        try: local_tts.synthesize(PHRASE, VOICES[0], existing)
        except FileExistsError: pass
        else: raise AssertionError("Synthesis allowed overwriting existing audio")
        require(existing.read_bytes() == b"existing-user-audio", "Existing audio changed")
        during = threading.Event(); interrupted = run / "mid-cancelled.wav"
        def cancel_after_inference(*args, **kwargs):
            during.set(); return b"\0\1" * 2400
        with patch.object(local_tts, "_run", cancel_after_inference):
            try: local_tts.synthesize(PHRASE, VOICES[0], interrupted, during)
            except ai_tools.CancelledError: pass
            else: raise AssertionError("Cancellation after inference published output")
        require(not interrupted.exists() and not list(run.glob(".local-tts-*")), "Interrupted local staging remains")
        import types
        running = threading.Event(); termination = []
        class SlowSession:
            def get_inputs(self): return [types.SimpleNamespace(name=name) for name in ("input", "input_lengths", "scales")]
            def run(self, outputs, feeds, options):
                limit = time.monotonic() + 2
                while time.monotonic() < limit:
                    if options.terminate:
                        termination.append(True); raise RuntimeError("QA native termination")
                    time.sleep(.005)
                raise AssertionError("ORT cancellation watcher never terminated inference")
        timer = threading.Timer(.025, running.set); timer.start(); started = time.monotonic()
        try:
            try: local_tts._run(SlowSession(), [1, 2, 3], {"inference": {}}, running, "cpu")
            except ai_tools.CancelledError: pass
            else: raise AssertionError("ORT inference cancellation was not propagated")
        finally: timer.cancel()
        require(termination and time.monotonic() - started < .5, "Native termination watcher did not stop promptly")
        return {"pre_cancel_no_output": True, "existing_output_preserved": True, "mid_inference_no_output_or_staging": True,
                "native_run_options_termination": True}

    def model_cache():
        import local_tts
        files, config, mapping, tones = local_tts._validated("ngochuyen")
        before = local_tts.cache_info()
        first, device = local_tts._session(files, "auto", None)
        again, same_device = local_tts._session(files, "auto", None)
        require(first is again and device == same_device, "Identical model/device did not reuse its ORT session")
        require(len(local_tts.cache_info()) <= 2, "Native sessions exceeded the bounded cache")
        # Shared ONNX stays read-only. Only copied small QA metadata is changed.
        folder = run / "cache-metadata"; folder.mkdir()
        fixtures = {}
        for key, path in files.items():
            if key == "model": fixtures[key] = path
            else:
                copied = folder / path.name; shutil.copyfile(path, copied); fixtures[key] = copied
        original_model_hash = digest(files["model"])
        cached, _ = local_tts._session(fixtures, "cpu", None)
        data = json.loads(fixtures["config"].read_text(encoding="utf-8")); data["qa_fingerprint_version"] = 2
        fixtures["config"].write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reloaded, _ = local_tts._session(fixtures, "cpu", None)
        require(reloaded is not cached, "Changed config reused a stale ORT session")
        require(digest(files["model"]) == original_model_hash and len(local_tts.cache_info()) <= 2,
                "Cache check changed shared ONNX or exceeded cache limit")
        return {"reuse_same_session": True, "metadata_change_reloads_session": True, "cache_maximum": 2,
                "shared_model_unchanged": True, "runtime_cache": before}

    def mixed_voice_dispatch():
        import local_tts, subtitle_tools, workflow
        require(all(key in generated for key in VOICES), "Real phrase fixtures are unavailable")
        rows = [{"start": .2, "end": 2.2, "text": "Xin chào.", "voice": VOICES[0]},
                {"start": 2.3, "end": 4.3, "text": "Hẹn gặp lại.", "voice": VOICES[1]}]
        subtitles = run / "mixed-voices.srt"; subtitle_tools.save_subtitles(rows, subtitles)
        require([row["voice"] for row in subtitle_tools.load_subtitles(subtitles)] == list(VOICES), "Sidecar lost per-cue voice IDs")
        calls = []
        def fixture_synth(text, voice, output, cancel=None, progress=None, **kwargs):
            ai_tools._check_cancel(cancel); calls.append({"text": text, "voice": voice, "device": kwargs.get("device")})
            shutil.copyfile(generated[voice + "-mp3"], output); return str(Path(output).resolve())
        output = run / "mixed-timeline.wav"
        with patch.object(local_tts, "synthesize", fixture_synth):
            result = workflow.dub_subtitles(str(subtitles), str(output), VOICES[0])
        require([row["voice"] for row in calls] == list(VOICES), "Mixed cue voices were not dispatched independently")
        require([row["text"] for row in calls] == [row["text"] for row in rows], "Cue text dispatch changed")
        info = audio(result)
        signal, rate = sf.read(result, dtype="float32", always_2d=True)
        require(abs(info["duration"] - 4.3) < .1, "Mixed cue timing duration changed")
        require(float(np.max(np.abs(signal[:int(.15*rate)]))) < .0001, "Speech leaked before first cue")
        for start, end in ((.25, 1.5), (2.35, 3.5)):
            chunk = signal[int(start*rate):int(end*rate)]
            require(float(np.sqrt(np.mean(chunk**2))) > .0001, "A cue is silent after dispatch/mixing")
        report["artifacts"]["mixed_timeline"] = info
        return {"dispatch": calls, "audio": info, "real_ffmpeg_timing": True, "inference_reused_from_real_fixtures": True}

    def gpu_hardware():
        import gpu_runtime
        pack = gpu_runtime.discover_demucs_pack()
        require(pack is not None, "Installed Demucs GPU pack not discovered")
        fingerprint = digest(pack["checkpoint"])
        runtime = gpu_runtime.configure_cuda_runtime()
        require(runtime["dlls_ready"], "Installed CUDA DLL pack not discovered")
        hardware = gpu_runtime.inspect_demucs_hardware()
        require(hardware.get("cuda") is True, "GPU pack cannot execute a CUDA tensor")
        require(digest(pack["checkpoint"]) == fingerprint, "GPU inspection changed model checkpoint")
        return {"runtime": runtime, "hardware": hardware, "checkpoint_unchanged": True}

    def worker_cancellation():
        import gpu_runtime
        cancel = threading.Event(); timer = threading.Timer(.25, cancel.set); timer.start()
        start = time.monotonic()
        try:
            try: gpu_runtime.run_json_worker(sys.executable, "import time;time.sleep(30)", [], cancel=cancel, timeout=5)
            except ai_tools.CancelledError: pass
            else: raise AssertionError("Worker did not propagate cancellation")
        finally: timer.cancel()
        require(time.monotonic() - start < 5, "Cancelled worker did not terminate promptly")
        return {"owned_worker_hard_cancel_seconds": round(time.monotonic()-start, 3)}

    def queued_cancellation():
        import gpu_runtime
        cancel = threading.Event(); timer = threading.Timer(.025, cancel.set)
        gpu_runtime._SEPARATION_LOCK.acquire()
        try:
            timer.start(); started = time.monotonic()
            with patch.object(gpu_runtime, "run_json_worker") as spawn:
                try: gpu_runtime.separate_demucs_gpu("unused.wav", str(run), "vocals", cancel=cancel)
                except ai_tools.CancelledError: pass
                else: raise AssertionError("Queued separation did not cancel")
                require(not spawn.called, "Cancelled queue started an external inference worker")
            elapsed = time.monotonic() - started
            require(elapsed <= .25 and gpu_runtime._SEPARATION_LOCK.locked(), "Queued cancel was slow or released the owner's lock")
        finally:
            timer.cancel(); gpu_runtime._SEPARATION_LOCK.release()
        return {"queue_cancel_seconds": round(elapsed, 3), "no_inference_spawn": True, "owner_lock_preserved": True}

    def separation_selection():
        import gpu_runtime, media_ai
        fixture = run / "separation-routing.wav"
        t = np.arange(8820, dtype="float32") / 44100
        sf.write(fixture, np.column_stack([.1*np.sin(2*np.pi*440*t)]*2), 44100, subtype="FLOAT")
        pack = {"checkpoint": str(run / "shared-models/hub/checkpoints/model.th")}
        calls, messages = [], []
        def fake_cpu(target, args, progress, cancel):
            calls.append({"branch": "cpu", "cache": args[3]})
            paths = {}
            for name in ("vocals", "background"):
                path = Path(args[1]) / (name + ".wav"); shutil.copyfile(args[0], path); paths[name] = str(path)
            return paths
        with patch.object(gpu_runtime, "discover_demucs_pack", return_value=pack), \
                patch.object(media_ai, "_require"), patch.object(media_ai, "_run_worker", fake_cpu), \
                patch.object(gpu_runtime, "separate_demucs_gpu", side_effect=ai_tools.AIError("QA GPU unavailable")):
            output = media_ai.separate_audio(str(fixture), str(run / "auto-fallback"),
                                            progress=lambda p, m: messages.append(m), device="auto")
            require(len(calls) == 1 and "shared-models/hub" in calls[0]["cache"].replace("\\", "/"),
                    "Auto fallback did not reuse the installed model cache")
            require(all(Path(path).is_file() for path in output.values()), "Auto fallback did not publish tracks")
            require(any("CPU" in message for message in messages), "Auto GPU fallback did not report CPU use")
            before = len(calls)
            try: media_ai.separate_audio(str(fixture), str(run / "strict-cuda"), device="cuda")
            except ai_tools.AIError: pass
            else: raise AssertionError("Explicit CUDA silently fell back after GPU error")
            require(len(calls) == before, "Explicit CUDA invoked the CPU worker")
            require(not list((run / "strict-cuda").glob("*.wav")), "Failed CUDA published tracks")
        with patch.object(gpu_runtime, "discover_demucs_pack", return_value=pack), \
                patch.object(media_ai, "_run_worker", fake_cpu), \
                patch.object(gpu_runtime, "separate_demucs_gpu", side_effect=ai_tools.CancelledError("QA cancel")):
            before = len(calls)
            try: media_ai.separate_audio(str(fixture), str(run / "cancel-gpu"), device="auto")
            except ai_tools.CancelledError: pass
            else: raise AssertionError("GPU cancellation was not propagated")
            require(len(calls) == before and not list((run / "cancel-gpu").glob("*.wav")),
                    "GPU cancellation fell back or published tracks")
        return {"automatic_cpu_fallback": True, "shared_checkpoint_reused": True, "explicit_cuda_strict": True,
                "cancel_does_not_fallback_or_publish": True, "inference_mocked": True, "real_ffmpeg_staging": True}

    def ui():
        from PySide6.QtCore import QCoreApplication, QEvent, QSettings
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication, QWidget, QPushButton
        from desktop import IndependentWindow
        from dialogs import VoicePickerDialog, GPUPerformanceDialog
        import gpu_runtime
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(run / "qt-settings"))
        app = QApplication.instance() or QApplication([])
        app.setApplicationName("VideoEditorIndependentGPUVoicesQA"); app.setOrganizationName("VideoEditorIndependentGPUVoicesQA")
        app.setStyle("Fusion")
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguiemj.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file(): QFontDatabase.addApplicationFont(str(path))
        app.setFont(QFont("Segoe UI", 10))
        window = IndependentWindow(testing=True)
        dialogs = []
        def settle(predicate=lambda: True, timeout=10):
            end = time.monotonic() + timeout
            while True:
                app.processEvents(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                if predicate(): return
                require(time.monotonic() < end, "Qt workers did not settle")
                time.sleep(.01)
        try:
            window.resize(1600, 920); window.show(); settle()
            first = window.findChild(QPushButton, "localVoiceNgocHuyen")
            second = window.findChild(QPushButton, "localVoiceNgocHuyenV2")
            require(first is not None and second is not None, "Hidden compatibility voice controls are missing")
            require(first.isEnabled() and second.isEnabled() and not first.isVisible() and not second.isVisible(),
                    "Hidden local controls have incorrect readiness or appear in reference layout")
            network = window._pipeline_uses_network
            require(not network({"dub": True, "voice": VOICES[0], "subtitle_rows": [{"voice": VOICES[1]}]}),
                    "Local-only speech was incorrectly disclosed as a network request")
            require(network({"dub": True, "voice": VOICES[0], "subtitle_rows": [{"voice": "vi-VN-HoaiMyNeural"}]}),
                    "Mixed online cue speech omitted network disclosure")
            require(network({"translate": True, "dub": True, "voice": VOICES[0]}), "Translation omitted network disclosure")
            require(not network({"export": True}), "Local export was incorrectly disclosed as a network request")
            fixture = run / "layout-fixture.mp4"
            subprocess.run([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=12", "-t", "1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fixture)], check=True, capture_output=True,
                           timeout=30, creationflags=engine._creation_flags())
            copy = run / "layout-fixture-2.mp4"; shutil.copyfile(fixture, copy)
            window.ingest([str(fixture), str(copy)])
            settle(lambda: len(window.cards) == 2 and not window.workers, 15)
            window.right_tabs.setCurrentIndex(0); window.pipeline_tabs.setCurrentIndex(2); first.click(); settle()
            require(window.pipe_voice.currentText() == VOICES[0], "Hidden compatibility selection did not retain canonical ID")
            picker = VoicePickerDialog(window, VOICES[0]); dialogs.append(picker)
            picker.voice_selected.connect(window._use_voice); picker.show(); settle()
            require(picker.engine_id == "ngochuyen" and picker.selected_voice == VOICES[0], "Picker did not restore local voice")
            require(picker.confirm_button.isEnabled(), "Installed offline voice cannot be confirmed")
            picker.set_engine("ngochuyen_v2"); picker.select_voice(VOICES[1]); settle()
            require(picker.confirm_button.isEnabled() and picker.cards[VOICES[1]].preview_button.isEnabled(),
                    "V2 picker selection/preview readiness failed")
            picker_image = run / "local-voice-picker.png"; require(picker.grab().save(str(picker_image)), "Voice picker screenshot failed")
            picker.confirm_selection(); settle()
            require(window.pipe_voice.currentText() == VOICES[1], "Voice picker did not route selected V2 to pipeline")
            captures = []
            for width, height in ((1600, 920), (1920, 1080)):
                window.resize(width, height); settle(); time.sleep(.05); app.processEvents()
                require(window.right_tabs.width() == 300, "Sidebar width differs from reference 300 px")
                for card in window.cards:
                    require(card.header.height() == 44 and card.footer.height() == 40 and card.seek.height() == 12,
                            "Card header/seek/footer differs from reference 44/12/40")
                    require(card.ratio.width() == 80 and card.ratio.height() == 26, "Card ratio differs from reference 80x26")
                screenshot = run / ("independent-voice-" + str(width) + ".png")
                require(window.grab().save(str(screenshot)), "Cannot capture owned QWidget UI")
                captures.append({"screenshot": str(screenshot), "size": [window.width(), window.height()],
                                 "sidebar_width": 300, "card_header_seek_footer": [44, 12, 40]})
            performance = GPUPerformanceDialog(window); dialogs.append(performance); performance.show(); settle()
            performance.device.setCurrentIndex(performance.device.findData("cuda")); settle()
            require(window.tts_device.currentData() == "cuda", "Performance device not bound to pipeline settings")
            performance_image = run / "gpu-performance-dialog.png"
            require(performance.grab().save(str(performance_image)), "Performance screenshot failed")
            def cancellable_hardware(progress, cancel):
                cancel.wait(3); ai_tools._check_cancel(cancel); return {"cuda": True, "device": "QA"}
            with patch.object(gpu_runtime, "inspect_demucs_hardware", cancellable_hardware):
                performance.check_hardware(); performance.close()
                settle(lambda: performance.worker is None, 5)
            require(not performance.isVisible(), "Closing GPU check left its dialog open")
            window.active_job = object()
            busy = GPUPerformanceDialog(window); dialogs.append(busy)
            require(not busy.device.isEnabled(), "GPU device can be changed while processing")
            window.active_job = None
            report["artifacts"]["actual_ui"] = captures
            report["artifacts"]["voice_picker"] = str(picker_image)
            report["artifacts"]["gpu_dialog"] = str(performance_image)
            return {"actual_window": type(window).__name__, "captures": captures, "voice_picker_local_confirmation": True,
                    "local_vs_mixed_network_disclosure": True, "gpu_device_binding": True,
                    "gpu_check_close_cancels_worker": True, "compatibility_buttons_hidden": True}
        finally:
            window.active_job = None
            for dialog in dialogs: dialog.close(); dialog.deleteLater()
            settle(lambda: not window.workers, 5)
            window.dirty = False; window.close(); window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()

    def packaged():
        from PyInstaller.archive.readers import CArchiveReader
        executable = Path(options.exe).resolve(); require(executable.is_file(), "Packaged executable missing")
        archive = CArchiveReader(str(executable)); name = next(name for name in archive.toc if name.upper().startswith("PYZ"))
        pyz = archive.open_embedded_archive(name)
        def normalize(code):
            constants = tuple(normalize(value) if isinstance(value, types.CodeType) else value for value in code.co_consts)
            return code.replace(co_filename="owned-source", co_consts=constants)
        modules = {}
        for module in ("local_tts", "gpu_runtime", "voice_catalog", "ai_tools", "workflow", "media_ai", "desktop", "dialogs", "main"):
            if module == "main": continue  # Entry point is an archive script, checked by the normal packaging harness.
            actual = normalize(pyz.extract(module))
            expected = normalize(compile((SOURCE / (module + ".py")).read_bytes(), module + ".py", "exec"))
            modules[module] = actual == expected
        require(all(modules.values()), "Packaged bytecode differs from final source: " + str(modules))
        outputs = {}
        for identity in VOICES:
            provider = identity.split(":", 1)[0]; srt = run / (provider + ".srt")
            ai_tools.save_srt([{"start": .1, "end": 4.1, "text": PHRASE}], srt)
            output = run / (provider + "-packaged.wav")
            job = run / (provider + "-job.json")
            job.write_text(json.dumps({"operation": "dub", "input": str(srt), "output": str(output), "voice": identity, "tts_device": "cuda"}, ensure_ascii=False), encoding="utf-8")
            package_env = os.environ.copy(); package_env.pop("PYTHONPATH", None); package_env.pop("PYTHONHOME", None)
            completed = subprocess.run([str(executable), "--job", str(job)], cwd=run, env=package_env,
                                       stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
                                       creationflags=engine._creation_flags())
            result_path = job.with_suffix(".result.json")
            require(result_path.is_file(), "Packaged local dub wrote no result JSON: " + completed.stderr.decode("utf-8", "replace")[-1000:])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            require(completed.returncode == 0 and result.get("ok"), "Packaged local dub failed: " + str(result))
            require(result.get("runtime", {}).get("device") == "cuda", "Packaged voice did not run on requested CUDA device")
            require("CUDAExecutionProvider" in result["runtime"].get("providers", []), "Packaged CUDA provider missing")
            outputs[identity] = audio(output)
            outputs[identity]["runtime"] = result["runtime"]
        return {"executable": str(executable), "executable_sha256": digest(executable), "module_bytecode": modules,
                "public_job_local_dub": outputs, "packaged_process_source_imports": False}

    import requests
    import urllib.request
    with patch.object(requests, "request", no_network), patch.object(urllib.request, "urlopen", no_network):
        if options.exe: check("Packaged final bytecode and both real local voice jobs", packaged)
        elif options.ui_only: check("Actual reference layout, local voice picker and GPU dialog", ui)
        elif options.gpu_only:
            check("Installed GPU pack executes CUDA without modifying checkpoint", gpu_hardware)
            check("GPU selection, explicit CUDA errors, CPU fallback and cancellation", separation_selection)
            check("GPU runtime worker cancellation terminates owned process", worker_cancellation)
            check("Queued GPU separation cancellation preserves owner lock", queued_cancellation)
        else:
            check("Owned backend/UI source compilation and hashes", source_compile)
            check("Both distinct local models synthesize a real Vietnamese phrase", real_voices)
            check("Local synthesis cancellation and overwrite protection", cancellation_and_overwrite)
            check("Bounded model cache reuse and metadata invalidation", model_cache)
            check("Mixed cue local voice dispatch and real FFmpeg assembly", mixed_voice_dispatch)
            check("Installed GPU pack executes CUDA without modifying checkpoint", gpu_hardware)
            check("GPU selection, explicit CUDA errors, CPU fallback and cancellation", separation_selection)
            check("GPU runtime worker cancellation terminates owned process", worker_cancellation)
            check("Queued GPU separation cancellation preserves owner lock", queued_cancellation)
            if not options.backend_only: check("Actual reference layout, local voice picker and GPU dialog", ui)
    persist()
    print(json.dumps({"report": str(target_report), "passed": report["passed"], "failed": report["failed"],
                      "status": report["status"]}, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1

if __name__ == "__main__": raise SystemExit(main())
