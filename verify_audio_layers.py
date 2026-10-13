"""Offline, real-FFmpeg checks for canvas audio-layer timing and mixing."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import traceback
import wave
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "qa" / "audio-layers-report.json"


def main():
    report = dict(phase="canvas-audio-layers", created_at=datetime.now(timezone.utc).isoformat(),
                  offline=True, network_attempts=0, checks=[], helper_api={
                      "signature": "append_audio_layers(args, graph, layers, duration, input_index, *, cancel=None) -> (labels, next_index)",
                      "output": "Separate stereo 48 kHz labels audioLayerN; caller performs final mixing and limiting",
                      "defaults": dict(volume=1, loop=True, enabled=True, start=0, end=0, fade_in=0, fade_out=0),
                      "semantics": ["Source playback begins at source time zero at the configured canvas start",
                                    "End zero uses canvas duration; timing must remain within the canvas",
                                    "Loop repeats the source; once pads silence after the source ends",
                                    "Fades use local configured interval time and clamp to its length",
                                    "Start/end are rounded to the nearest 48 kHz sample",
                                    "Disabled rows skip file and parameter validation",
                                    "Invalid enabled rows or cancellation leave args and graph unchanged"]})
    with tempfile.TemporaryDirectory(prefix="video-editor-audio-layers-") as folder:
        root = Path(folder)
        os.environ.update(LOCALAPPDATA=str(root / "local-data"), APPDATA=str(root / "roaming-data"))
        import numpy as np
        import engine
        import audio_layers

        sample_rate = audio_layers.SAMPLE_RATE
        short = root / "Nhạc 440 Hz ngắn có dấu.wav"
        long = root / "Nhạc 880 Hz dài có dấu.wav"
        no_audio = root / "Video không có âm thanh.mp4"

        def require(value, message):
            if not value:
                raise AssertionError(message)

        def check(name, function):
            start = time.monotonic()
            entry = dict(name=name)
            try:
                entry.update(status="passed", details=function() or {})
            except Exception:
                entry.update(status="failed", error=traceback.format_exc())
            entry["seconds"] = round(time.monotonic() - start, 3)
            report["checks"].append(entry)

        def ffmpeg(arguments):
            result = subprocess.run([engine.tool_path("ffmpeg"), "-v", "error", "-nostdin", "-n", *arguments],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                                    creationflags=engine._creation_flags())
            require(result.returncode == 0, result.stderr.decode("utf-8", "replace")[-2000:])

        def fixture():
            ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.4",
                    "-c:a", "pcm_s16le", str(short)])
            ffmpeg(["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:duration=2",
                    "-ac", "2", "-c:a", "pcm_s16le", str(long)])
            ffmpeg(["-f", "lavfi", "-i", "color=black:size=16x16:rate=10:duration=0.2",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(no_audio)])
            require(engine.probe(str(short))["has_audio"] and not engine.probe(str(no_audio))["has_audio"],
                    "Fixture streams are incorrect")
            return dict(frequencies=[440, 880], short_seconds=.4, source_sample_rates=[48000, 44100], unicode_paths=True)

        rendered = 0

        def render(layers, duration=3, *, batches=None):
            nonlocal rendered
            args, graph = [], []
            index, labels = 0, []
            untouched = copy.deepcopy(layers)
            for batch in batches if batches is not None else [layers]:
                added, index = audio_layers.append_audio_layers(args, graph, batch, duration, index, cancel=threading.Event())
                labels.extend(added)
            require(layers == untouched, "Audio helper mutated the project layer dictionaries")
            require(labels and index == len(labels), "Audio helper returned incorrect input indexing")
            require(len(set(labels)) == len(labels), "Audio filter labels collided")
            if len(labels) == 1:
                graph.append(f"[{labels[0]}]anull[result]")
            else:
                graph.append("".join(f"[{label}]" for label in labels) +
                             f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0[result]")
            target = root / f"Âm thanh kết quả {rendered}.wav"
            rendered += 1
            ffmpeg([*args, "-filter_complex_threads", "1", "-filter_complex", ";".join(graph),
                    "-map", "[result]", "-ar", str(sample_rate), "-ac", "2", "-c:a", "pcm_s16le", str(target)])
            with wave.open(str(target), "rb") as reader:
                require(reader.getnchannels() == 2 and reader.getsampwidth() == 2 and reader.getframerate() == sample_rate,
                        "Audio overlay output is not stereo 48 kHz PCM")
                values = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").reshape(-1, 2).astype(float)
            require(len(values) == round(duration * sample_rate), "Audio layer output does not span exactly the canvas")
            return values[:, 0], labels

        def window(values, start, end):
            return values[round(start * sample_rate):round(end * sample_rate)]

        def rms(values, start, end):
            segment = window(values, start, end)
            return float(np.sqrt(np.mean(segment ** 2)))

        def spectral(values, start, end, frequency):
            segment = window(values, start, end)
            spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
            bins = np.fft.rfftfreq(len(segment), 1 / sample_rate)
            return float(spectrum[np.argmin(np.abs(bins - frequency))] / max(1, len(segment)))

        def silence(values, start, end):
            require(rms(values, start, end) < 1, f"Unexpected audio outside layer interval {start}–{end}")

        def timed_loop():
            values, labels = render([dict(path=str(short), start=.6, end=2.4)])
            silence(values, 0, .59)
            silence(values, 2.4, 3)
            for start, end in ((.7, .9), (1.3, 1.5), (2.15, 2.35)):
                require(spectral(values, start, end, 440) > 200, "Looped source did not fill the enabled interval")
            return dict(labels=labels, before_rms=rms(values, 0, .5), after_rms=rms(values, 2.5, 3),
                        late_440_fft=spectral(values, 2.15, 2.35, 440))

        def two_layers():
            layers = [dict(path=str(short), start=.5, end=2, volume=.5),
                      dict(path=str(long), start=1.25, end=2.75, loop=False, volume=.5)]
            values, labels = render(layers, batches=[[layers[0]], [layers[1]]])
            silence(values, 0, .49)
            silence(values, 2.75, 3)
            first_440, first_880 = spectral(values, .75, 1, 440), spectral(values, .75, 1, 880)
            both_440, both_880 = spectral(values, 1.4, 1.65, 440), spectral(values, 1.4, 1.65, 880)
            last_440, last_880 = spectral(values, 2.2, 2.45, 440), spectral(values, 2.2, 2.45, 880)
            require(first_440 > 200 and first_880 < 2 and both_440 > 200 and both_880 > 200 and last_440 < 2 and last_880 > 200,
                    "Mixed layers lost frequencies or leaked across their individual timing")
            return dict(labels=labels, first_only=[first_440, first_880], overlap=[both_440, both_880], last_only=[last_440, last_880])

        def once_mode():
            values, _ = render([dict(path=str(short), start=.4, end=2.8, loop=False)])
            silence(values, 0, .39)
            require(spectral(values, .5, .7, 440) > 200, "Once-mode source did not start at source zero")
            silence(values, .8, 3)
            return dict(source_ends_at=.8, subsequent_rms=rms(values, 1, 2.7))

        def local_fades():
            values, _ = render([dict(path=str(short), start=.5, end=2.5, fade_in=.4, fade_out=.4)])
            early, middle, late = rms(values, .52, .58), rms(values, 1, 1.2), rms(values, 2.42, 2.48)
            require(middle > 1000 and early < middle * .25 and late < middle * .25,
                    "Fades were not relative to the local layer interval")
            silence(values, 0, .5)
            silence(values, 2.5, 3)
            return dict(early_rms=early, middle_rms=middle, late_rms=late)

        def clamped_fades():
            values, _ = render([dict(path=str(short), start=.5, end=1.5, fade_in=5, fade_out=5)], duration=2)
            edge, middle = rms(values, .51, .56), rms(values, .95, 1.05)
            require(middle > 300 and edge < middle * .25, "Overlong fades did not clamp to the local interval")
            silence(values, 0, .5)
            silence(values, 1.5, 2)
            return dict(clamped_seconds=1, overlapping_fades="multiplicative", edge_rms=edge, middle_rms=middle)

        def canvas_end():
            values, _ = render([dict(path=str(short), start=1.25, end=0)], duration=2.8)
            silence(values, 0, 1.25)
            require(spectral(values, 2.55, 2.75, 440) > 200, "End-zero/default loop failed to reach canvas end")
            return dict(canvas_samples=len(values), end_zero_seconds=2.8)

        def disabled_rows():
            args, graph = ["prefix"], ["existing"]
            labels, index = audio_layers.append_audio_layers(args, graph,
                [dict(path=str(root / "missing.wav"), enabled=False, volume=float("nan"), start=-2)], 3, 7)
            require(labels == [] and index == 7 and args == ["prefix"] and graph == ["existing"],
                    "Disabled layer was validated or added as an input")
            values, labels = render([dict(path=str(short), enabled=False),
                                    dict(path=str(root / "missing.wav"), enabled=False), dict(path=str(long))])
            require(spectral(values, .5, .75, 440) < 2 and spectral(values, .5, .75, 880) > 200,
                    "Disabled audio was included in the actual output")
            return dict(enabled_labels=labels, missing_disabled_source_ignored=True)

        def volumes():
            levels = {}
            for volume in (0, .5, 1, 2):
                values, _ = render([dict(path=str(short), volume=volume)], duration=1)
                levels[str(volume)] = rms(values, .1, .3)
            require(levels["0"] == 0 and abs(levels["0.5"] / levels["1"] - .5) < .01 and abs(levels["2"] / levels["1"] - 2) < .01,
                    "Layer volume does not honor zero, half, and double gain")
            return dict(rms_by_gain=levels)

        def invalid_rows():
            fixtures = [dict(path=str(root / "missing.wav")), dict(path=str(no_audio)),
                        dict(path=str(short), volume=2.1), dict(path=str(short), volume=float("nan")),
                        dict(path=str(short), start=-1), dict(path=str(short), start=2, end=1),
                        dict(path=str(short), end=4), dict(path=str(short), fade_out=5.1)]
            for bad in fixtures:
                args, graph = ["prefix"], ["existing"]
                try:
                    audio_layers.append_audio_layers(args, graph, [dict(path=str(short)), bad], 3, 4)
                except (ValueError, TypeError):
                    pass
                else:
                    raise AssertionError("Invalid enabled audio row was accepted")
                require(args == ["prefix"] and graph == ["existing"], "Failed validation partially mutated arguments or graph")
            for duration in (0, -1, float("nan"), float("inf")):
                try:
                    audio_layers.append_audio_layers([], [], [], duration, 0)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Invalid canvas duration was accepted")
            return dict(enabled_invalid_cases=len(fixtures), invalid_canvas_cases=4, no_partial_mutation=True)

        def cancellation():
            cancel = threading.Event()
            cancel.set()
            args, graph = ["prefix"], ["existing"]
            with patch.object(engine, "probe", side_effect=AssertionError("Cancelled helper must not probe")):
                try:
                    audio_layers.append_audio_layers(args, graph, [dict(path=str(short))], 3, 0, cancel=cancel)
                except engine.Cancelled:
                    pass
                else:
                    raise AssertionError("Preset cancellation was ignored")
            require(args == ["prefix"] and graph == ["existing"], "Preset cancellation mutated caller data")
            cancel.clear()
            original_probe = engine.probe

            def cancelling_probe(path):
                value = original_probe(path)
                cancel.set()
                return value

            with patch.object(engine, "probe", cancelling_probe):
                try:
                    audio_layers.append_audio_layers(args, graph, [dict(path=str(short))], 3, 0, cancel=cancel)
                except engine.Cancelled:
                    pass
                else:
                    raise AssertionError("Cancellation during probe was ignored")
            require(args == ["prefix"] and graph == ["existing"], "Probe cancellation partially mutated caller data")
            return dict(before_probe=True, after_probe=True, no_partial_mutation=True)

        def forbid_network(*args, **kwargs):
            report["network_attempts"] += 1
            raise AssertionError("Network is forbidden in the local audio-layer check")

        with patch.object(socket, "create_connection", forbid_network):
            check("Real Unicode 440/880 Hz audio and video-only fixtures", fixture)
            check("Looped source starts locally and stays inside its enabled interval", timed_loop)
            check("Two separate audio labels mix correctly across overlap and gaps", two_layers)
            check("Once mode plays the source once then remains silent", once_mode)
            check("Fade-in/out use the local enabled interval", local_fades)
            check("Overlong fades clamp to interval duration", clamped_fades)
            check("End zero and default loop reach the canvas end", canvas_end)
            check("Disabled audio and missing disabled paths are ignored", disabled_rows)
            check("Volume zero, half, default, and double gain affect real output", volumes)
            check("Missing/audio-free sources and invalid fields fail before mutation", invalid_rows)
            check("Cancellation before and after probe prevents caller mutation", cancellation)
        report["real_audio_renders"] = rendered
        report["source_sha256"] = hashlib.sha256((ROOT / "audio_layers.py").read_bytes()).hexdigest()

    report["temporary_data_cleaned"] = True
    report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
    report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
    report["status"] = "failed" if report["failed"] or report["network_attempts"] else "passed"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(report=str(REPORT), passed=report["passed"], failed=report["failed"],
                          network_attempts=report["network_attempts"], status=report["status"]), ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
