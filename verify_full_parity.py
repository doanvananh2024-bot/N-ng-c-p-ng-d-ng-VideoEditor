"""Evidence-driven reference inventory and isolated parity regressions.

Read only public reference QML/i18n/voice/effect definitions. All runtime tests
use private app data, fake provider keys, mocked HTTP and generated fixtures.
The inventory is not a claim that every listed feature has been reproduced.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import uuid
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent
STAGE = SOURCE.parent
QA = STAGE / "qa"
REFERENCE = Path("C:/Users/doanv/AppData/Local/Programs/VideoEditor/video_editor")
OWN_EXISTING = Path("C:/Users/doanv/AppData/Local/Programs/Media Studio/VideoEditorIndependent")

def require(value, message):
    if not value: raise AssertionError(message)

def flattened(node, prefix=""):
    values = {}
    for key, value in node.items():
        identity = prefix + "." + key if prefix else key
        if isinstance(value, dict): values.update(flattened(value, identity))
        elif isinstance(value, str): values[identity] = value
    return values

FEATURES = [
    ("workspace", "Window/grid/card geometry, sidebar and VI/EN switch", ["Main.qml","VideoGrid.qml","TopBar.qml","RightSidebar.qml"], ["desktop.py"], "existing_source", "Measured geometry and screenshots; exact font/color/pixel identity not established"),
    ("import", "File picker, drag/drop import and thumbnail metadata", ["Main.qml","VideoGrid.qml"], ["desktop.py","engine.py"], "existing_source", "Generated Unicode file ingestion, invalid-file rejection"),
    ("pagination", "Page size 10/20/30/50/100 and 1/2/3/4 columns", ["BottomBar.qml"], ["desktop.py"], "existing_source", "Selection survives page switches; navigation only when multiple pages"),
    ("card_actions", "Select, remove, status/progress, apply all and context menu", ["VideoCard.qml"], ["desktop.py"], "existing_source", "Apply-all preserves per-video asset identities"),
    ("card_transport", "Play/pause, seek, ±5 s, ratio and fullscreen", ["VideoCard.qml","components/CCPlayerControls.qml","components/CCSeekBar.qml"], ["desktop.py"], "existing_source", "Native playback and exact seek with generated fixture"),
    ("pipeline_stt", "STT enable, method, source language and worker count", ["tabs/PipelineTab.qml"], ["desktop.py","media_ai.py"], "partial", "Local Whisper/OCR exist; dynamic remote STT methods and within-video worker coverage differ"),
    ("pipeline_substyle", "Subtitle style, translation-style inheritance and display", ["tabs/PipelineTab.qml","components/CCTextStyleEditor.qml"], ["desktop.py","studio_ui.py"], "partial", "Ten effects and hug/box background fit need exported-pixel tests"),
    ("pipeline_translation", "Enable, target language, scenario, model/provider and batch/workers", ["tabs/PipelineTab.qml"], ["providers.py","desktop.py"], "existing_source", "Exact cue IDs/time/voice metadata, malformed response and key errors"),
    ("translation_context", "Video frame image context for AI translation", ["tabs/PipelineTab.qml"], ["providers.py","workflow.py"], "existing_source", "Bounded image payload, correct MIME/provider fields, missing images"),
    ("subtitle_merge", "Merge subtitles by sentences and length policy", ["tabs/PipelineTab.qml"], ["subtitle_tools.py","workflow.py"], "partial", "Preserve speaker boundaries and cue timing; differing remote capcut behavior"),
    ("voice_groups", "Voice enable/selection/parallel, source separation/speed, dub fit/speed/delay/dictionary", ["tabs/PipelineTab.qml"], ["desktop.py","workflow.py","subtitle_tools.py"], "existing_source", "Preserve 1.2.2 tested audio contract; limits and source timing"),
    ("voice_fit_modes", "Fit voice modes and maximum speed", ["tabs/PipelineTab.qml","modals/SubEditorModal.qml"], ["subtitle_tools.py","desktop.py"], "partial", "Do not silently alias distinct advertised fit modes"),
    ("separation_local", "Local Demucs CPU/GPU and basic/detail four stems", ["tabs/PipelineTab.qml","modules/OrigAudioModule.qml"], ["media_ai.py","gpu_runtime.py"], "existing_source", "Real CUDA, reconstruction, atomic outputs and cancellable GPU queue"),
    ("separation_cloud", "Cloud vocal separation option", ["tabs/PipelineTab.qml"], ["media_ai.py"], "external_integration_missing", "QML uses registry smartVoiceModes; private service is not reconstructed by UI labels"),
    ("pipeline_system", "General/render/TTS concurrency, GPU export and codec/quality/speed", ["tabs/PipelineTab.qml"], ["desktop.py","engine.py","workflow.py"], "existing_source", "Real encode; resource bounded worker concurrency"),
    ("source_composition", "Source scale/position/rotation/canvas blur/color/opacity", ["modules/TransformModule.qml","modules/CanvasModule.qml","modules/BgFillModule.qml","modules/OpacityModule.qml"], ["source_composition.py","desktop.py"], "available_owned_module_not_yet_verified", "Existing independent module available; real pixels and persisted settings"),
    ("layer_management", "Add/select/enable/remove/duplicate/group text/media/blur/filter/audio layers", ["PropertyPanel.qml","modules/LayerGroupSection.qml"], ["desktop.py","studio_ui.py"], "partial", "Filter/audio layer list and detail routing not baseline-equivalent"),
    ("text_basic", "Text/time/font/size/uppercase/color/outline/background/opacity", ["components/CCTextStyleEditor.qml","modules/TextStyleModule.qml"], ["advanced_render.py","preview_canvas.py","desktop.py"], "existing_source", "Preview/export and each selected card retain independently edited style"),
    ("text_effects", "Ten effects: outline, outline_thick, shadow, glow, hollow, soft_shadow, retro_offset, double_border, long_shadow, none", ["components/CCTextStyleEditor.qml"], ["advanced_render.py","preview_canvas.py"], "functional_gap", "Real rendered masks/pixels; outline width alone does not implement shadow/glow"),
    ("text_motion", "Text marquee around/inside/diagonal/auto with px/s speed", ["components/CCTextStyleEditor.qml"], ["advanced_render.py","preview_canvas.py"], "partial", "Frames at different timestamps must change predicted position"),
    ("text_presets", "Selectable style preset grid", ["modules/TextStyleModule.qml"], ["desktop.py","studio_ui.py"], "partial", "Apply full preset fields then customize without global side effects"),
    ("subtitle_position", "Subtitle placement, width and selected cue list", ["modules/SubPositionModule.qml","modules/SubListModule.qml"], ["desktop.py","subtitle_editor.py"], "partial", "Caption current-line highlight and seek routing"),
    ("media_overlay", "Raster/GIF source, file change, scale/rotation/anchor/flip/opacity and loop", ["modules/MediaSourceModule.qml","modules/TransformModule.qml","modules/LoopModule.qml"], ["advanced_render.py","preview_canvas.py","desktop.py"], "existing_source", "Real animated GIF frame timing and exported anchors"),
    ("layer_blend", "Eight blend modes plus opacity", ["modules/BlendModule.qml"], ["layer_blend.py"], "available_owned_module_not_yet_verified", "Independent own module exists; verify numerical blend and clipped export"),
    ("blur", "Editable region, radius/strength/corner/feather/time", ["modules/BlurRegionModule.qml","modules/BlurTuneModule.qml"], ["advanced_render.py","preview_canvas.py"], "existing_source", "Timed region pixels and canvas manipulation"),
    ("filter_layers", "Color filter layer type and intensity; export-only preview note", ["modules/FilterPickModule.qml","modules/IntensityModule.qml"], ["advanced_render.py","desktop.py"], "functional_gap", "Per-layer timing/order/intensity; global filter alone differs"),
    ("a0_audio", "Original audio four stems gain plus independent enable and fades", ["modules/OrigAudioModule.qml","modules/FadeModule.qml"], ["advanced_render.py","audio_layers.py","desktop.py"], "existing_source", "Preserve 1.2.2 spectral gain/fade tests and asset mapping"),
    ("a1_audio", "Narration gain, enable and fades", ["modules/VolumeModule.qml","modules/FadeModule.qml"], ["advanced_render.py","desktop.py"], "existing_source", "A1 fade must not alter A0"),
    ("extra_audio", "Multiple extra audio files, replace/source/loop/gain/fades", ["modules/AudioSourceModule.qml","modules/LoopModule.qml","modules/VolumeModule.qml","modules/FadeModule.qml"], ["audio_layers.py"], "available_owned_module_not_yet_verified", "Real two-frequency independent loop/gain/fade, safe file changes"),
    ("anti_detection", "Auto zoom level/hold/period, mirror, crop/border and metadata/video/audio tweaks", ["tabs/EffectTab.qml"], ["advanced_render.py","desktop.py"], "partial", "Transforms exist; fake metadata and fingerprint toggles may differ and offer no guaranteed detection avoidance"),
    ("subtitle_editor", "Original/translated/bilingual edit, selection, split/merge/search/undo and file save", ["modals/SubEditorModal.qml"], ["subtitle_editor.py","subtitle_tools.py"], "existing_source", "Timings, sidecars, bilingual freshness and cancellation"),
    ("cue_voice", "Per-cue voice picker, preview, timing fit and regenerate", ["modals/SubEditorModal.qml"], ["subtitle_editor.py","subtitle_tools.py"], "partial", "Per-cue voices exist; regeneration/fit status differs"),
    ("subtitle_ai_fix", "AI prompt/scoped picked-cue review and changed-only Apply", ["modals/SubAiFixModal.qml"], ["subtitle_editor.py","providers.py"], "functional_gap", "Review before commit, malformed result rejection, exact indices/times/undo"),
    ("voice_picker", "Engine/language/gender/category/search, readiness badges, preview/confirm", ["modals/VoicePickerModal.qml"], ["dialogs.py","voice_catalog.py"], "existing_source", "Missing packs/credentials cannot preview/confirm; draft state and cancellation"),
    ("edge_tts", "Edge voice catalog and online speech", ["modals/VoicePickerModal.qml"], ["voice_catalog.py","ai_tools.py"], "existing_source", "Staging and mixed dispatch tested; no unrequested live service calls"),
    ("fpt_tts", "FPT voice catalog and speech", ["modals/VoicePickerModal.qml"], ["tts_providers.py"], "existing_source", "Mocked official transport and real decode; user-paid service untested"),
    ("elevenlabs_tts", "ElevenLabs voice catalog and speech", ["modals/VoicePickerModal.qml"], ["tts_providers.py"], "existing_source", "Pagination, auth errors, explicit model/audio format; live account untested"),
    ("additional_tts", "CapCut/gTTS/MiniMax/SiliconFlow/Vbee/Zalo registry engines", ["modals/VoicePickerModal.qml"], ["tts_providers.py"], "functional_gap", "Only public dataset identities visible; backend/auth contract needs independent documentation"),
    ("local_ngochuyen", "Ngọc Huyền/V2 installed models and per-voice catalog/download/remove", ["modals/VoicePickerModal.qml","modals/DownloadsModal.qml"], ["local_tts.py","dialogs.py"], "partial", "Two real local model inferences exist; full remote per-voice downloader is separate gap"),
    ("kokoro", "Offline foreign-language voice model pack", ["modals/DownloadsModal.qml"], ["local_tts.py"], "functional_gap", "Reference labels say 54 voices/9 languages; no public local voice dataset present"),
    ("model_resources", "Installed/missing/downloading/error/cancelled model cards, size/progress, CPU/GPU and uninstall", ["modals/DownloadsModal.qml"], ["reference_dialogs.py","dialogs.py"], "partial", "Current resource inventory/install mechanisms differ; do not claim copied pack management"),
    ("keys", "AI/TTS encrypted keys, multiple keys and status/refresh", ["modals/SettingsModal.qml","modals/KeysModal.qml"], ["providers.py","tts_providers.py","dialogs.py"], "existing_source", "Masked summary, empty key errors, fake isolated DPAPI data only"),
    ("accounts", "Browser account add/rename/enable/login/remove with busy state and proxy", ["modals/SettingsModal.qml","components/CCAccountCard.qml"], ["reference_dialogs.py","dialogs.py"], "external_integration_missing", "Independent credentials/API keys are not browser-session account parity"),
    ("interface_settings", "Language/theme/output folder/cache controls", ["modals/SettingsModal.qml"], ["dialogs.py","desktop.py"], "partial", "Output/cache exist; dynamic full reference light/dark palette not proven identical"),
    ("prompts", "Translation prompt presets/my prompts/editor plus pronunciation dictionaries", ["modals/PromptModal.qml","modals/PromptEditModal.qml","modals/SayEditModal.qml"], ["scenario_store.py","dialogs.py"], "existing_source", "Scoped persistence, valid placeholders, selected dictionary affects speech only"),
    ("split", "Split by duration/count fast-copy/reencode, progress/results/import", ["modals/MiniToolModal.qml"], ["dialogs.py","engine.py"], "existing_source", "Real generated files, no-overwrite, cancellation"),
    ("join", "Ordered join, move/remove/reorder, automatic output name and fast/safe modes", ["modals/MiniToolModal.qml"], ["dialogs.py","engine.py"], "existing_source", "Real order and Unicode/sanitized paths"),
    ("mp3", "Audio extraction quality, progress/open/import", ["modals/MiniToolModal.qml"], ["dialogs.py","engine.py"], "existing_source", "Real output decode and cancel/overwrite"),
    ("video_download", "Video URL download and imported results", ["TopBar.qml"], ["engine.py","dialogs.py"], "existing_source", "Supported yt-dlp public services; reference broader catalogs not established"),
    ("reference_login_terms", "Reference login/license/terms/auth gate", ["modals/LoginModal.qml","modals/TermsModal.qml","Main.qml"], ["desktop.py"], "separate_product", "Independent locally owned application intentionally has its own owner/terms; cannot claim reference license service"),
    ("reference_update", "Reference version/update download/service", ["modals/UpdateModal.qml","TopBar.qml"], ["desktop.py"], "external_integration_missing", "Independent release/package workflow differs from proprietary update service"),
]

def audit():
    translations = {}
    for path in sorted((REFERENCE / "assets/i18n/vi").glob("*.json")):
        translations.update(flattened(json.loads(path.read_text(encoding="utf-8-sig"))))
    surfaces = []
    for path in sorted((REFERENCE / "qml").rglob("*.qml")):
        text = path.read_text(encoding="utf-8-sig")
        keys = sorted(set(re.findall(r'\btr\(["\']([^"\']+)["\']', text)))
        actions = sorted(set(re.findall(r'\b(?:settingsVM|pipelineVM|segments|voicePickerVM|downloadsVM|subAiFixVM|runnerVM|card|gridVM|root)\.([A-Za-z_]\w*)\s*\(', text)))
        options = [json.loads("[" + match + "]") for match in re.findall(r'\b(?:_ids|effectKeys|deviceIds|speedIds)\s*:\s*\[\s*((?:"[^"\n]*"\s*,?\s*)+)\]', text)]
        surfaces.append({"file": path.relative_to(REFERENCE / "qml").as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "translation_labels": {key: translations.get(key, "<dynamic or missing>") for key in keys},
                         "observable_method_names": actions, "static_option_lists": options,
                         "runtime_visibility": "Source inventory includes components; parent flags/models determine actual visibility"})
    datasets = []
    for path in sorted((REFERENCE / "assets/data/voices").glob("*.json")):
        values = json.loads(path.read_text(encoding="utf-8-sig")); rows = values if isinstance(values, list) else values.get("voices", [])
        datasets.append({"dataset": path.stem, "public_catalog_rows": len(rows),
                         "languages": sorted({str(row.get("lang", row.get("Locale", ""))) for row in rows if isinstance(row, dict)}),
                         "claim": "Public asset dataset identity; runtime engine registry may include additional dynamic engines"})
    features = []
    for identity, label, refs, owned, status, validation in FEATURES:
        features.append({"id": identity, "feature": label, "reference_files": refs, "owned_files": owned,
                         "baseline_assessment": status, "verification": "not_verified_by_this_audit",
                         "validation_needed": validation,
                         "owned_source_present": {name: (SOURCE / name).is_file() for name in owned},
                         "existing_owned_module_available": {name: (OWN_EXISTING / name).is_file() for name in owned}})
    matrix = {"created_at": datetime.now(timezone.utc).isoformat(), "scope": "Read-only public QML, i18n, voice/effect data plus independent owned source",
              "no_exact_parity_claim": True, "not_read": "Reference private accounts, cookies, API keys, authentication/license state or model binaries",
              "features": features, "reference_surfaces": surfaces, "public_voice_catalogs": datasets,
              "reference_filter_ids": sorted(path.stem for path in (REFERENCE / "assets/effects").glob("*.json"))}
    (STAGE / "feature-matrix.json").write_text(json.dumps(matrix, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Reference feature audit — 1.3.0 staging", "", "This inventories observable controls and source contracts. It does not certify complete functional or pixel parity. A listed module or label is evidence of source presence; only a separately recorded runtime check establishes tested behavior.", "", "## Priorities", "",
             "1. Preserve 1.2.2 audio gains/fades, source speed/trim, cue timing, GPU voices and cancellation.",
             "2. Implement real ten text effects, per-layer color filters and timed motion; validate exported pixels rather than adding labels alone.",
             "3. Add AI subtitle review that preserves cue timing/metadata, previews changes and commits only accepted changed rows with undo.",
             "4. Treat extra speech engines, Kokoro, downloadable models and remote recognition as separate configured integrations; never show ready after only adding a catalog entry.",
             "5. Merge already independently authored source composition/blend/audio-layer modules with persistence and render tests.", "", "## Feature matrix", "", "| Feature | Baseline audit status | Required evidence |", "|---|---|---|"]
    lines += [f"| {row['feature']} | {row['baseline_assessment']} | {row['validation_needed']} |" for row in features]
    lines += ["", "## Exact public options observed", "", "- Text effects: outline, outline_thick, shadow, glow, hollow, soft_shadow, retro_offset, double_border, long_shadow, none.",
              "- Marquee: empty/off, around, inside, diagonal, auto; speed in pixels per second. Text background fit: hug or box.",
              "- Blend: normal, lighten, screen, darken, overlay, hardlight, softlight, multiply.",
              "- Public voice datasets: " + ", ".join(row["dataset"] for row in datasets) + ". Kokoro is named in downloadable resources but has no voice dataset file in this public folder.",
              "- STT methods, translation engines, paid TTS engines and account providers are runtime model/registry driven; QML labels cannot determine their complete backend contract.",
              "- The reference filter picker explicitly says color filters are visible only in exported video, so a live color-filter preview is not an observable required match.", "", "## AI review contract", "",
              "SubAiFixModal builds picked rows, sends original entries plus selected indices and prompt, and accepts results keyed by index with changed/unchanged/missed status. Apply emits only picked rows that completed, changed, contain nonempty new text and differ from original text. Timing and voice metadata must survive. Failure or cancellation must not commit a partial draft.",
              "", "## Limits", "", "Reference login/license, server accounts/browser profiles and updater are separate external products. The independent application cannot establish those services from public UI definitions. No private credential or authentication state was read. Exact theme tokens and the entire runtime registry were not available in QML. Keep unsupported configurations visible as unavailable or reject them clearly; do not assert all-feature parity from the inventory.",
              "", f"The machine-readable matrix inventories {len(surfaces)} QML files and {len(features)} feature groups, with translated labels, static option lists and method names for every public surface."]
    (STAGE / "parity-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"features": len(features), "qml_surfaces": len(surfaces), "voice_datasets": len(datasets)}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--audit-only", action="store_true")
    options = parser.parse_args()
    result = audit()
    if options.audit_only: print(json.dumps(result)); return 0
    QA.mkdir(exist_ok=True); run = QA / ("parity_" + uuid.uuid4().hex[:8]); run.mkdir()
    original_local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    os.environ.setdefault("MEDIAFUSION_LOCAL_TTS_ROOT", str(original_local / "VideoEditor/tts"))
    os.environ.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"), QT_QPA_PLATFORM="offscreen",
                      QT_QPA_FONTDIR="C:/Windows/Fonts", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    import ai_tools, providers, voice_catalog, tts_providers
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "run_dir": str(run), "audit": result,
              "offline": True, "paid_requests": 0, "network_attempts": 0, "checks": [], "qt_exceptions": []}
    def check(name, fn):
        row = {"name": name}; report["checks"].append(row); start = time.monotonic()
        try: row.update(status="passed", details=fn() or {})
        except Exception: row.update(status="failed", error=traceback.format_exc())
        row["seconds"] = round(time.monotonic()-start, 3)
        print(json.dumps({key: row[key] for key in ("name", "status", "seconds")}, ensure_ascii=True), flush=True)
    def no_http(*args, **kwargs):
        report["network_attempts"] += 1; raise AssertionError("Unrequested external request in offline parity tests")
    class EmptyStore:
        def get_keys(self, provider): return []
        def summary(self, provider): return {"configured": False}
    class FakeStore(EmptyStore):
        def get_keys(self, provider): return ["QA-placeholder-key"]
    rows = [{"start": .1, "end": 1.2, "text": "Xin chào", "voice": "ngochuyen:ngoc_huyen", "qa_tag": "preserve"},
            {"start": 1.3, "end": 2.4, "text": "Hẹn gặp lại", "voice": "ngochuyen_v2:ngoc_huyen_v_2"}]
    def negative_configuration():
        for operation in (providers.translate_rows, providers.rewrite_rows):
            for config in ({"provider": "unknown"}, {"provider": "gemini", "model": "../invalid"}, {"provider": "gemini", "model": "gemini-qa"}):
                try: operation(rows, config, store=EmptyStore())
                except (ValueError, ai_tools.AIError): pass
                else: raise AssertionError("Invalid/credential-free provider config was accepted")
        for identity in ("unsupported:voice", "fpt:banmai", "elevenlabs:voice_qa"):
            try: tts_providers.synthesize("test", identity, str(run / "must-not-exist.mp3"), store=EmptyStore())
            except (ValueError, ai_tools.AIError): pass
            else: raise AssertionError("Unsupported or credential-free TTS was accepted")
        require(not (run / "must-not-exist.mp3").exists(), "Invalid TTS published output")
        return {"unknown_provider_rejected": True, "invalid_model_rejected": True, "missing_keys_rejected": True}
    def rewrite_contract():
        original = copy.deepcopy(rows); observed = []
        def completion(provider, model, system, cues, images, pool, config, progress, cancel):
            observed.extend(cues)
            return json.dumps({"cues": [{"id": row["id"], "text": "Đã sửa: " + row["text"]} for row in reversed(cues)]}, ensure_ascii=False)
        with patch.object(providers, "_completion", completion):
            edited = providers.rewrite_rows(rows, {"provider": "gemini", "model": "gemini-qa", "batch_size": 10,
                                                    "scenario_prompt": "Sửa chính tả, giữ nội dung."}, store=FakeStore())
        require(rows == original, "AI rewrite mutated source rows")
        require([row["text"] for row in edited] == ["Đã sửa: " + row["text"] for row in rows], "Reordered AI response changed cue mapping")
        for before, after in zip(rows, edited):
            require(all(after.get(key) == value for key, value in before.items() if key != "text"), "AI rewrite lost timing or metadata")
        for payload in ({"cues": []}, {"cues": [{"id": "bad", "text": "x"}]},
                        {"cues": [{"id": "cue_000000", "text": "x"}, {"id": "cue_000000", "text": "y"}]}):
            with patch.object(providers, "_completion", return_value=json.dumps(payload)):
                try: providers.rewrite_rows(rows, {"provider": "gemini", "model": "gemini-qa"}, store=FakeStore())
                except providers.ProviderError: pass
                else: raise AssertionError("Malformed AI draft accepted")
        cancel = threading.Event(); cancel.set()
        with patch.object(providers, "_completion") as request:
            try: providers.rewrite_rows(rows, {"provider": "gemini", "model": "gemini-qa"}, cancel=cancel, store=FakeStore())
            except ai_tools.CancelledError: pass
            else: raise AssertionError("Cancelled AI rewrite accepted")
            require(not request.called, "Cancelled AI rewrite reached provider")
        return {"reordered_results_mapped": True, "metadata_and_times_retained": True,
                "draft_source_unchanged": True, "malformed_rejected": True, "cancelled_without_request": True}
    def catalog_validation():
        candidates = [{"ShortName": "unsupported:voice", "Locale": "vi-VN"}, {"ShortName": "bad/path", "Locale": "vi-VN"},
                      {"ShortName": "vi-VN-HoaiMyNeural", "Locale": "en-US"}]
        try: voice_catalog.normalize_catalog(candidates)
        except ValueError: pass
        else: raise AssertionError("Invalid voice catalog rows accepted")
        return {"unsupported_ids_rejected": True, "mismatched_edge_locale_rejected": True}
    import requests
    with patch.object(requests, "request", no_http):
        check("Unsupported providers/models and missing credentials fail without HTTP", negative_configuration)
        check("AI wording rewrite preserves row identity/timing and rejects malformed drafts", rewrite_contract)
        check("Catalog validation rejects unsupported voice IDs and mismatched locales", catalog_validation)
    report["passed"] = sum(row["status"] == "passed" for row in report["checks"])
    report["failed"] = sum(row["status"] == "failed" for row in report["checks"])
    report["status"] = "failed" if report["failed"] or report["network_attempts"] or report["qt_exceptions"] else "passed"
    path = QA / "full-parity-report.json"; path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"report": str(path), "passed": report["passed"], "failed": report["failed"], "status": report["status"]}))
    return 0 if report["status"] == "passed" else 1

if __name__ == "__main__": raise SystemExit(main())
