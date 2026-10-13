"""Signal-level and QWidget QA for the three supplied audio reference panels.

Uses private synthetic sine stems and real FFmpeg renders. No neural models,
provider keys, network requests, existing datasets, or installed apps are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import copy
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
import uuid
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent
QA = SOURCE.parent / "qa"
FREQUENCIES = {"vocals": 220, "drums": 440, "bass": 880, "other": 1760, "voice": 660}
AMPLITUDE = .06

def require(value, message):
    if not value: raise AssertionError(message)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--ui-only", action="store_true")
    parser.add_argument("--parallel-only", action="store_true")
    parser.add_argument("--exe")
    options = parser.parse_args()
    phase = "packaged" if options.exe else "ui" if options.ui_only else "parallel" if options.parallel_only else "render" if options.render_only else "source"
    QA.mkdir(exist_ok=True)
    run = QA / ("audio_" + phase + "_" + uuid.uuid4().hex[:8]); run.mkdir()
    original_local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    os.environ.setdefault("MEDIAFUSION_LOCAL_TTS_ROOT", str(original_local / "VideoEditor/tts"))
    os.environ.setdefault("VEI_DEMUCS_PACK", str(original_local / "VideoEditor/voicesep/smartvoice_cuda"))
    os.environ.setdefault("VEI_CUDA_PATH", str(original_local / "VideoEditor/cuda"))
    os.environ.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"),
                      QT_QPA_PLATFORM="offscreen", QT_QPA_FONTDIR="C:/Windows/Fonts", HF_HUB_OFFLINE="1",
                      TRANSFORMERS_OFFLINE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    import numpy as np
    import soundfile as sf
    import ai_tools, engine, render_pipeline, subtitle_tools, workflow
    report = {"phase": phase, "created_at": datetime.now(timezone.utc).isoformat(), "run_dir": str(run),
              "offline": True, "synthesis": "synthetic local audio; real FFmpeg processing", "checks": [], "artifacts": {},
              "network_attempts": 0, "qt_exceptions": []}
    report_path = QA / ("audio-reference-" + phase + "-report.json")
    def persist():
        report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
        report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
        report["status"] = "failed" if report["failed"] or report["network_attempts"] or report["qt_exceptions"] else "passed"
        text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        report_path.write_text(text, encoding="utf-8"); (run / report_path.name).write_text(text, encoding="utf-8")
    def check(name, fn):
        entry = {"name": name}; report["checks"].append(entry); start = time.monotonic()
        try:
            entry.update(status="passed", details=fn() or {})
            require(not report["qt_exceptions"], "Qt callback exceptions occurred")
        except Exception: entry.update(status="failed", error=traceback.format_exc())
        entry["seconds"] = round(time.monotonic()-start, 3); persist()
        print(json.dumps({key: entry[key] for key in ("name", "status", "seconds")}, ensure_ascii=True), flush=True)
    def ffmpeg(arguments, timeout=40):
        result = subprocess.run([engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", *arguments],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, creationflags=engine._creation_flags())
        require(result.returncode == 0, result.stderr.decode("utf-8", "replace")[-1800:]); return result.stdout
    def decoded(path):
        return np.frombuffer(ffmpeg(["-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"]), dtype="<f4")
    def level(signal, frequency, start, length=.2):
        chunk = signal[round(start*48000):round((start+length)*48000)]
        require(len(chunk) >= length*48000-1, "Audio shorter than measurement window")
        phase = np.arange(len(chunk))/48000
        return float(abs(2*np.mean(chunk*np.exp(-2j*np.pi*frequency*phase))))
    def close(actual, expected, message, relative=.12, absolute=.0012):
        require(abs(actual-expected) <= max(absolute, abs(expected)*relative), f"{message}: {actual:.6f}, expected {expected:.6f}")
    files = {}; video = run / "source.mp4"
    def fixtures():
        times = np.arange(4*48000, dtype=np.float32)/48000
        for key, frequency in FREQUENCIES.items():
            path = run / (key + ".wav"); sf.write(path, AMPLITUDE*np.sin(2*np.pi*frequency*times), 48000, subtype="FLOAT"); files[key] = path
        ffmpeg(["-n", "-f", "lavfi", "-i", "color=c=0x303030:size=320x180:rate=12:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=1200:sample_rate=48000:duration=4",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)])
        report["artifacts"]["fixtures"] = {key: str(path) for key, path in files.items()}
        return {"source": str(video), "frequencies": FREQUENCIES, "stem_peak": AMPLITUDE, "seconds": 4}
    def settings(**changes):
        result = {"width": 320, "height": 180, "fps": 12, "speed": 1, "volume": 1,
                  "encoder": {"format": "mp4", "codec": "h264", "device": "cpu", "preset": "fast", "quality": 18},
                  "source_stem_paths": {key: str(files[key]) for key in ("vocals", "drums", "bass", "other")},
                  "source_stem_volumes": {"vocals": .24, "drums": 1, "bass": 1, "other": 0},
                  "source_audio_enabled": True, "voice_audio_enabled": True, "voice_path": str(files["voice"]),
                  "voice_volume": 1.26, "source_fade_in": 0, "source_fade_out": 0, "voice_fade_in": 0, "voice_fade_out": 0}
        result.update(changes); return result
    def render(name, config):
        path = run / (name + ".mp4")
        render_pipeline.render_project([{"path": str(video), "start": 0, "end": 4}], config, str(path), lambda *_: None, threading.Event())
        signal = decoded(path); report["artifacts"][name] = str(path)
        return signal, engine.probe(str(path))
    def four_stem_gains():
        signal, info = render("four-stem-gains", settings())
        observed = {key: level(signal, frequency, 1.5) for key, frequency in FREQUENCIES.items()}
        for key in ("vocals", "drums", "bass", "other"):
            close(observed[key], AMPLITUDE*settings()["source_stem_volumes"][key], key + " gain")
        close(observed["voice"], AMPLITUDE*1.26, "A1 narration gain")
        require(level(signal, 1200, 1.5) < .002, "Raw source audio was mixed again alongside stems")
        return {"spectral_amplitudes": observed, "duration": info["duration"], "raw_source_not_double_mixed": True}
    def source_fades():
        signal, _ = render("a0-independent-fades", settings(source_fade_in=1, source_fade_out=1))
        measured = {}
        for key in ("vocals", "drums", "bass"):
            frequency = FREQUENCIES[key]; middle = level(signal, frequency, 1.5)
            first = level(signal, frequency, .1); last = level(signal, frequency, 3.7)
            close(first/middle, .2, key + " A0 fade in", relative=.2, absolute=.06)
            close(last/middle, .2, key + " A0 fade out", relative=.2, absolute=.06)
            measured[key] = {"early_middle": first/middle, "late_middle": last/middle}
        for start in (.1, 1.5, 3.7): close(level(signal, 660, start), AMPLITUDE*1.26, "A1 unaffected by A0 fade")
        return {"a0_ratios": measured, "a1_stays_constant": True}
    def narration_fades():
        signal, _ = render("a1-independent-fades", settings(voice_fade_in=1, voice_fade_out=1))
        middle = level(signal, 660, 1.5)
        early, late = level(signal, 660, .1)/middle, level(signal, 660, 3.7)/middle
        close(early, .2, "A1 fade in", relative=.2, absolute=.06); close(late, .2, "A1 fade out", relative=.2, absolute=.06)
        for start in (.1, 1.5, 3.7): close(level(signal, 220, start), AMPLITUDE*.24, "A0 unaffected by A1 fade")
        return {"a1_early_middle": early, "a1_late_middle": late, "a0_stays_constant": True}
    def enables():
        a0off, _ = render("a0-disabled", settings(source_audio_enabled=False))
        for key in ("vocals", "drums", "bass", "other"): require(level(a0off, FREQUENCIES[key], 1.5) < .002, "Disabled A0 remains audible")
        close(level(a0off, 660, 1.5), AMPLITUDE*1.26, "A1 survives A0 disabling")
        a1off, _ = render("a1-disabled", settings(voice_audio_enabled=False))
        require(level(a1off, 660, 1.5) < .002, "Disabled A1 remains audible")
        close(level(a1off, 220, 1.5), AMPLITUDE*.24, "A0 survives A1 disabling")
        return {"a0_and_a1_independent": True}
    def source_speed():
        signal, info = render("source-speed-half", settings(speed=.5))
        require(abs(float(info["duration"])-8) < .12, "Source speed did not double duration")
        for start in (1.5, 5.5):
            close(level(signal, 220, start), AMPLITUDE*.24, "Source stem tempo/time or pitch changed")
            close(level(signal, 660, start), AMPLITUDE*1.26, "Reused narration did not follow source speed")
        trimmed = run / "trimmed-speed.mp4"
        render_pipeline.render_project([{"path": str(video), "start": 1, "end": 3}], settings(speed=2), str(trimmed), lambda *_: None, threading.Event())
        require(abs(float(engine.probe(str(trimmed))["duration"])-1) < .12, "Trim and speed were not applied together")
        return {"slow_duration": info["duration"], "trimmed_fast_duration": engine.probe(str(trimmed))["duration"], "pitch_preserved": True}

    def parallel():
        import asyncio, local_tts
        rows = [{"start": i*.8, "end": i*.8+.7, "text": str(i), "voice": "ngochuyen:ngoc_huyen" if i%2 == 0 else "ngochuyen_v2:ngoc_huyen_v_2"} for i in range(6)]
        fixture = run / "parallel-fixture.mp3"
        ffmpeg(["-n", "-f", "lavfi", "-i", "sine=frequency=660:duration=0.25", "-c:a", "libmp3lame", str(fixture)])
        lock, active, maximum = threading.Lock(), 0, 0
        barrier = threading.Barrier(3); calls = []
        async def fake_synth(edge, text, voice, path, cancel):
            nonlocal active, maximum
            with lock: active += 1; maximum = max(maximum, active)
            try:
                barrier.wait(timeout=3); await asyncio.sleep(.04)
                ai_tools._check_cancel(cancel); shutil.copyfile(fixture, path)
                with lock: calls.append({"text": text, "voice": voice, "device": local_tts.current_device()})
            finally:
                with lock: active -= 1
        target = run / "parallel-voices.wav"
        with local_tts.device_scope("cuda"), patch.object(ai_tools, "_synthesize", fake_synth):
            result = subtitle_tools.dub_rows(rows, str(target), voice_parallel=3)
        require(maximum == 3 and active == 0 and len(calls) == 6, "Voice worker limit is not honored")
        require(all(call["device"] == "cuda" for call in calls), "Worker context lost CUDA selection")
        signal = decoded(result)
        require(abs(len(signal)/48000-4.7) < .03, "Parallel synthesis changed the cue timeline duration")
        for row in rows: require(level(signal, 660, row["start"]+.04, .12) > .02, "A parallel cue is missing")
        require(local_tts.current_device() == "auto", "Device context leaked to the caller")
        return {"maximum_inflight": maximum, "voices": calls, "timed_audio_seconds": len(signal)/48000,
                "provider_requests_mocked": True, "ffmpeg_processing_real": True}

    def parallel_cancel():
        import asyncio, local_tts
        event = threading.Event(); started = threading.Event(); active, lock = 0, threading.Lock()
        async def fake_synth(edge, text, voice, path, cancel):
            nonlocal active
            with lock: active += 1
            started.set()
            try:
                for _ in range(100): ai_tools._check_cancel(cancel); await asyncio.sleep(.01)
                raise AssertionError("Cancelled provider never stopped")
            finally:
                with lock: active -= 1
        rows = [{"start": i, "end": i+.8, "text": "cancel", "voice": "ngochuyen:ngoc_huyen"} for i in range(6)]
        target = run / "cancel-parallel.wav"
        timer = threading.Timer(.1, event.set); timer.start()
        began = time.monotonic()
        try:
            with local_tts.device_scope("cuda"), patch.object(ai_tools, "_synthesize", fake_synth):
                try: subtitle_tools.dub_rows(rows, str(target), cancel=event, voice_parallel=3)
                except ai_tools.CancelledError: pass
                else: raise AssertionError("Parallel cancellation was swallowed")
        finally: timer.cancel()
        require(started.is_set() and active == 0 and not target.exists(), "Cancelled cue workers remain active or published output")
        require(not list(run.glob(".mediafusion-voice-*")) and time.monotonic()-began < 4, "Cancelled cue staging was not cleaned promptly")
        require(local_tts.current_device() == "auto", "Cancelled worker context leaked")
        return {"all_workers_settled": True, "no_published_audio": True, "seconds": round(time.monotonic()-began, 3)}

    def workflow_timing():
        caption=run/"workflow-captions.srt"
        subtitle_tools.save_subtitles([{"start":1,"end":2,"text":"Giọng đầu"},{"start":2.2,"end":2.8,"text":"Giọng sau"}],caption)
        captured={}
        def fake_dub(rows, output, voice, progress, cancel, **options):
            captured.update(rows=rows,options=options)
            samples=AMPLITUDE*np.sin(2*np.pi*660*np.arange(2*48000)/48000)
            sf.write(output,samples,48000,subtype="FLOAT")
            return str(output)
        config=dict(recognize=False,translate=False,dub=True,export=True,original_srt=str(caption),voice_parallel=3,
                    voice_speed=1.2,voice_fit="natural",settings={"speed":.5,"width":320,"height":180,
                    "source_audio_enabled":False,"voice_volume":1.26,"encoder":{"device":"cpu","codec":"h264","format":"mp4"}})
        with patch.object(subtitle_tools,"dub_rows",fake_dub):
            results=workflow.run_workflow([dict(path=str(video),start=1,end=3)],config,str(run/"workflow"))
        require(results[0]["status"]=="done","Retime workflow failed: "+str(results))
        require(captured["rows"][0]["start"]==0 and captured["rows"][0]["end"]==2,"Generated cue times did not follow trim/speed")
        require(captured["options"]["voice_parallel"]==3 and captured["options"]["voice_speed"]==1.2,"Voice controls did not reach synthesis")
        signal=decoded(results[0]["paths"]["video"])
        close(level(signal,660,.3),AMPLITUDE*1.26,"Generated narration starts on output timeline")
        require(level(signal,660,2.5)<.002,"Generated narration was stretched a second time")
        return {"generated_cues":captured,"generated_audio_not_retimed_twice":True,"export":results[0]["paths"]["video"]}

    def compile_sources():
        hashes = {}
        for name in ("advanced_render", "render_pipeline", "workflow", "subtitle_tools", "desktop"):
            path = SOURCE / (name + ".py"); data = path.read_bytes(); compile(data, str(path), "exec"); hashes[name] = hashlib.sha256(data).hexdigest()
        report["source_sha256"] = hashes; return hashes
    def ui():
        from PySide6.QtCore import QSettings, QCoreApplication, QEvent, Qt
        from PySide6.QtWidgets import QApplication, QDoubleSpinBox
        from PySide6.QtGui import QFontDatabase, QFont
        from desktop import IndependentWindow
        app = QApplication.instance() or QApplication([])
        app.setOrganizationName("AudioReferenceQA"); app.setApplicationName("AudioReferenceQA")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(run/"settings"))
        app.setStyle("Fusion")
        for filename in ("segoeui.ttf","segoeuib.ttf","seguisb.ttf"):
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/"+filename)
        app.setFont(QFont("Segoe UI",10))
        previous_hook = sys.excepthook
        sys.excepthook = lambda kind,value,trace: report["qt_exceptions"].append(str(value))
        window = IndependentWindow(testing=True)
        def settle():
            app.processEvents(); QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        try:
            window.resize(1600,920); window.show(); settle()
            expected = {"vocals":.24,"drums":1,"bass":1,"other":0}
            require(window.settings()["source_stem_volumes"] == expected, "Screenshot stem defaults were not retained")
            require(window.pipeline_config()["voice_parallel"] == 3, "Within-video parallel default differs")
            require(window.pipeline_config()["voice_fit"] == "natural", "Giọng đều does not select natural constant speed")
            ffmpeg(["-n","-f","lavfi","-i","color=size=320x180:rate=12:duration=1","-c:v","libx264",str(run/"ui.mp4")])
            window.ingest([str(run/"ui.mp4")]); window._activate_media(str(run/"ui.mp4")); settle()
            limit=time.monotonic()+15
            while window.workers and time.monotonic()<limit:
                settle(); time.sleep(.01)
            require(not window.workers,"Thumbnail worker did not settle")
            window.right_tabs.setCurrentIndex(1)
            captures=[]
            for identity in ("A0","A1"):
                item=next(window.inline_layers.item(i) for i in range(window.inline_layers.count()) if tuple(window.inline_layers.item(i).data(Qt.ItemDataRole.UserRole))==("audio",identity))
                window.inline_layers.setCurrentItem(item); settle()
                require(window.audio_badge.text()==identity and window.audio_detail_frames[identity].isVisible(),"Audio detail selection failed")
                image=run/(identity+"-sidebar.png"); require(window.right_tabs.grab().save(str(image)),"Sidebar capture failed"); captures.append(str(image))
                if identity=="A0":
                    body=window.source_stem_rows[0].parentWidget()
                    report["artifacts"]["a0_geometry"]=[{"type":child.metaObject().className(),"name":child.objectName(),"rect":[child.x(),child.y(),child.width(),child.height()]} for child in body.children() if hasattr(child,"x")]
            window.findChild(QDoubleSpinBox,"a0Vocals").setValue(37)
            window.findChild(QDoubleSpinBox,"a1Volume").setValue(142)
            window.findChild(QDoubleSpinBox,"a0FadeIn").setValue(.65)
            window.findChild(QDoubleSpinBox,"a1FadeOut").setValue(.85)
            window.voice_parallel.setValue(4)
            saved=copy.deepcopy(window.settings())
            window.audio_fields["vocals"].setValue(.11); window.voice_volume.setValue(.5)
            window.apply_all_settings(saved); settle()
            require(window.audio_fields["vocals"].value()==.37 and window.voice_volume.value()==1.42,"Audio gain persistence failed")
            require(window.audio_fields["source_fade_in"].value()==.65 and window.audio_fields["voice_fade_out"].value()==.85,"Audio fade persistence failed")
            require(window.pipeline_config()["voice_parallel"]==4,"Parallel persistence failed")
            window.right_tabs.setCurrentIndex(0); window.pipeline_tabs.setCurrentIndex(2); settle()
            image=run/"voice-pipeline-sidebar.png"; window.right_tabs.grab().save(str(image)); captures.append(str(image))
            window.apply_all_settings({"speed":1,"volume":1,"voice_volume":1}); settle()
            require("source_stem_volumes" not in window.settings(),"Loading legacy project adds unintended stem gains")
            require(window.controls["speed"].value()==1 and window.tts_speed.value()==1,"Legacy project timing was replaced")
            report["artifacts"]["sidebar_captures"]=captures
            return {"screenshots":captures,"gain_fade_and_parallel_persist":True,"legacy_defaults_preserved":True}
        finally:
            window.dirty=False; window.subtitle_dirty=False; window.close(); settle(); sys.excepthook=previous_hook
    def packaged():
        from PyInstaller.archive.readers import CArchiveReader
        from verify_bundle_sources import normalize
        executable=Path(options.exe).resolve()
        archive=CArchiveReader(str(executable)); pyz=archive.open_embedded_archive(next(name for name in archive.toc if name.upper().startswith("PYZ")))
        for name in ("desktop","advanced_render","render_pipeline","workflow","subtitle_tools"):
            require(normalize(pyz.extract(name))==normalize(compile((SOURCE/(name+".py")).read_bytes(),str(SOURCE/(name+".py")),"exec")),"Package source differs: "+name)
        fixtures()
        target=run/"packaged-audio.mp4"
        job=run/"export-job.json"
        job.write_text(json.dumps({"operation":"export","clips":[{"path":str(video),"start":0,"end":4}],"settings":settings(source_fade_in=1,voice_fade_out=1),"output":str(target)}),encoding="utf-8")
        completed=subprocess.run([str(executable),"--job",str(job)],capture_output=True,timeout=90,creationflags=engine._creation_flags())
        payload=json.loads(job.with_suffix(".result.json").read_text(encoding="utf-8")); require(completed.returncode==0 and payload.get("ok"),"Packaged export failed: "+str(payload))
        signal=decoded(target)
        close(level(signal,220,1.5),AMPLITUDE*.24,"Packaged A0 gain")
        close(level(signal,660,1.5),AMPLITUDE*1.26,"Packaged A1 gain")
        close(level(signal,220,.1)/level(signal,220,1.5),.2,"Packaged A0 fade",relative=.2,absolute=.06)
        close(level(signal,660,3.7)/level(signal,660,1.5),.2,"Packaged A1 fade",relative=.2,absolute=.06)
        report["executable_sha256"]=hashlib.sha256(executable.read_bytes()).hexdigest()
        return {"final_modules":5,"actual_exe_export":str(target),"signal_gains_and_fades_passed":True}
    import requests
    import urllib.request
    def no_network(*args, **kwargs):
        report["network_attempts"] += 1; raise AssertionError("Network is forbidden in synthetic audio QA")
    with patch.object(requests, "request", no_network), patch.object(urllib.request, "urlopen", no_network):
        if options.exe: check("Packaged audio source bytecode and signal render", packaged)
        elif options.ui_only: check("Reference A0/A1/Pipeline widget persistence", ui)
        elif options.parallel_only:
            check("Bounded parallel voice synthesis preserves device context and timing", parallel)
            check("Parallel voice cancellation joins workers and cleans staging", parallel_cancel)
        else:
            check("Final audio source syntax and hashes", compile_sources)
            check("Private four-frequency stem fixtures", fixtures)
            check("Four A0 gains and A1 gain are audible in real render", four_stem_gains)
            check("A0 fades independently of A1", source_fades)
            check("A1 fades independently of A0", narration_fades)
            check("A0/A1 enable switches remain independent", enables)
            check("Source speed and trim affect stems and reused narration", source_speed)
            check("Workflow retimes captions and generated narration exactly once", workflow_timing)
            check("Bounded parallel voice synthesis preserves device context and timing", parallel)
            check("Parallel voice cancellation joins workers and cleans staging", parallel_cancel)
            if not options.render_only: check("Reference A0/A1/Pipeline widget persistence", ui)
    persist()
    print(json.dumps({"report": str(report_path), "passed": report["passed"], "failed": report["failed"], "status": report["status"]}, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1

if __name__ == "__main__": raise SystemExit(main())

