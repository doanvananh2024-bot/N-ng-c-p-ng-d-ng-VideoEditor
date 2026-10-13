"""Exercise real local and free Edge narration without user project data."""
from __future__ import annotations

import asyncio
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--subtitle", type=Path)
parser.add_argument("--no-edge", action="store_true")
options = parser.parse_args()
GPU = Path(r"C:\Users\doanv\Documents\Codex\2026-10-12\VideoEditorIndependent-1.2.1-gpu-voices\gpu-deps")
if not GPU.is_dir():
    GPU = Path(r"C:\Users\doanv\AppData\Local\Programs\Video Editor Independent\versions\1.2.0\_internal")
if GPU.is_dir(): sys.path.insert(0, str(GPU))
QA = SOURCE.parent / "qa"
QA.mkdir(exist_ok=True)
RUN = QA / ("dubbing-live-" + uuid.uuid4().hex[:8])
RUN.mkdir()
original_local = Path(os.environ["LOCALAPPDATA"])
os.environ.update(VEI_CUDA_PATH=str(original_local / "VideoEditor/cuda"),
                  MEDIAFUSION_LOCAL_TTS_ROOT=str(original_local / "VideoEditor/tts"),
                  LOCALAPPDATA=str(RUN / "local-data"), APPDATA=str(RUN / "roaming-data"),
                  PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
import ai_tools
import edge_tts
import engine
import local_tts
import numpy as np
import soundfile as sf
import subtitle_tools

report = dict(created_at=datetime.now(timezone.utc).isoformat(), run_dir=str(RUN),
              synthetic_text_only=True, paid_requests=0, checks=[])

def save():
    report["passed"] = sum(r["status"] == "passed" for r in report["checks"])
    report["failed"] = sum(r["status"] == "failed" for r in report["checks"])
    report["status"] = "passed" if not report["failed"] else "failed"
    path = QA / "dubbing-live-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

def check(name, action):
    start = time.monotonic()
    row = dict(name=name)
    report["checks"].append(row)
    try:
        row.update(status="passed", details=action())
    except Exception as exc:
        row.update(status="failed", error=str(exc), error_type=type(exc).__name__)
    row["seconds"] = round(time.monotonic()-start, 3)
    save()
    print(json.dumps(row, ensure_ascii=True), flush=True)

def audio(path):
    signal, rate = sf.read(str(path), dtype="float32", always_2d=True)
    rms = float(np.sqrt(np.mean(signal**2)))
    assert len(signal) > 1000 and np.isfinite(signal).all() and rms > .0001, "Silent or invalid narration"
    return dict(path=str(path), seconds=round(len(signal)/rate, 3), rate=rate,
                rms=round(rms, 6), bytes=Path(path).stat().st_size)

ROWS = [dict(start=0., end=4., text="Xin chào, đây là giọng đọc thử cho video của bạn."),
        dict(start=4.5, end=8., text="Phần lồng tiếng đang được kiểm tra trên máy này.")]

def local_dub(voice, device="cuda"):
    path = RUN / (voice.replace(":", "_") + ".wav")
    with local_tts.device_scope(device):
        returned = subtitle_tools.dub_rows(ROWS, str(path), voice, voice_speed=1.2,
                                          voice_fit="natural", voice_parallel=3)
    assert Path(returned).resolve() == path.resolve()
    return dict(audio=audio(path), runtime=local_tts.last_runtime(voice))

def edge_dub():
    path = RUN / "edge-hoaimy.mp3"
    async def speak():
        await asyncio.wait_for(edge_tts.Communicate(ROWS[0]["text"], "vi-VN-HoaiMyNeural", connect_timeout=10,
                               receive_timeout=25).save(str(path)), timeout=40)
    asyncio.run(speak())
    return dict(audio=audio(path), edge_version=getattr(edge_tts, "__version__", "unknown"))

def v2_boundaries():
    _, _, mapping, tones = local_tts._validated("ngochuyen_v2")
    front = local_tts.SyllableFrontend(mapping, tones)
    canonical = front.phonemes("Cuối cùng. Ta đến rồi.")
    for text in ("Cuối cùng... Ta đến rồi…", "... Cuối cùng!!! Ta đến rồi...", "Cuối cùng… Ta đến rồi."):
        phones = front.phonemes(text)
        assert len(phones) == 2
        assert [p for row in phones for p in row if p not in local_tts._PUNCT] == [p for row in canonical for p in row if p not in local_tts._PUNCT]
        for sentence in phones: local_tts._ids(sentence, mapping)
    for text in ("...", "…", "!!!"):
        try: front.phonemes(text)
        except ValueError: pass
        else: raise AssertionError("Punctuation-only text was accepted as speech")
    try: front.phonemes("先喝")
    except ai_tools.AIError: pass
    else: raise AssertionError("Unsupported Chinese was accepted as Vietnamese phonemes")
    return dict(ellipsis_words_preserved=True, unicode_ellipsis=True, empty_speech_rejected=True,
                unsupported_language_rejected=True)

def bilingual_dispatch():
    seen = []
    original = dict(start=0., end=4., text="先喝\nUống trước đi", original_text="先喝", translated_text="Uống trước đi")
    rows = [original, dict(start=4.5, end=8., text="Câu đã được sửa.", original_text="先喝", translated_text="Uống trước đi")]
    async def capture(edge, text, voice, path, cancel):
        seen.append(text)
        signal = np.sin(np.arange(4800) * .11).astype(np.float32) * .1
        sf.write(str(path), signal, 24000, format="WAV")
    with patch.object(ai_tools, "_synthesize", capture):
        subtitle_tools.dub_rows(rows, str(RUN / "bilingual-routing.wav"), "ngochuyen_v2:ngoc_huyen_v_2",
                               voice_fit="natural", voice_parallel=1)
    assert seen == ["Uống trước đi", "Câu đã được sửa."]
    assert original["text"] == "先喝\nUống trước đi"
    return dict(translated_role_only=True, stale_metadata_keeps_edited_text=True, caller_unchanged=True)

def real_bilingual():
    rows = [dict(start=0., end=3., text="先喝\nUống trước đi", original_text="先喝", translated_text="Uống trước đi"),
            dict(start=3.5, end=6., text="Cuối cùng...\n终于", original_text="终于", translated_text="Cuối cùng...")]
    path = RUN / "bilingual-v2-actual-cuda.wav"
    with local_tts.device_scope("cuda"):
        subtitle_tools.dub_rows(rows, str(path), "ngochuyen_v2:ngoc_huyen_v_2", voice_fit="natural", voice_parallel=3)
    return dict(audio=audio(path), runtime=local_tts.last_runtime("ngochuyen_v2:ngoc_huyen_v_2"))

def saved_subtitles():
    rows = subtitle_tools.load_subtitles(options.subtitle)
    if not rows: raise AssertionError("Fixture is empty")
    before = options.subtitle.read_bytes()
    output = RUN / "saved-subtitles-v2-cuda.wav"
    with local_tts.device_scope("cuda"):
        subtitle_tools.dub_rows(rows, str(output), "ngochuyen_v2:ngoc_huyen_v_2", voice_fit="natural", voice_speed=1.2, voice_parallel=3)
    assert options.subtitle.read_bytes() == before, "User subtitle changed during QA"
    return dict(cues=len(rows), audio=audio(output), source_unchanged=True,
                runtime=local_tts.last_runtime("ngochuyen_v2:ngoc_huyen_v_2"))

check("v2-ellipsis-boundaries-preserve-words", v2_boundaries)
check("bilingual-target-role-and-edited-text-routing", bilingual_dispatch)
check("bilingual-v2-actual-cuda", real_bilingual)
check("local-legacy-ngoc-huyen-real-cuda-timed-dub", lambda: local_dub("ngochuyen:ngoc_huyen"))
check("local-premium-real-cuda-timed-dub", lambda: local_dub("ngochuyen:ngoc_huyen_premium"))
check("local-v2-real-cuda-timed-dub", lambda: local_dub("ngochuyen_v2:ngoc_huyen_v_2"))
if options.subtitle: check("user-saved-subtitles-v2-real-cuda-all-cues", saved_subtitles)
if not options.no_edge: check("edge-hoaimy-real-free-service", edge_dub)
print(str(QA / "dubbing-live-report.json"), flush=True)
raise SystemExit(1 if report["failed"] else 0)
