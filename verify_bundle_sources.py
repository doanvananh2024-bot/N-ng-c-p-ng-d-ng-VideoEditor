"""Check that the packaged module bytecode matches the final owned sources."""
import json
from pathlib import Path
import sys
import types
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent


def normalize(code):
    constants = tuple(normalize(item) if isinstance(item, types.CodeType) else item for item in code.co_consts)
    return code.replace(co_filename="independent-source", co_consts=constants)


def main():
    executable = Path(sys.argv[1]).resolve()
    archive = CArchiveReader(str(executable))
    name = next(name for name in archive.toc if name.upper().startswith("PYZ"))
    pyz = archive.open_embedded_archive(name)
    results = {}
    for name in ["desktop", "dialogs", "preview_canvas", "voice_catalog", "subtitle_editor", "tts_providers", "local_tts", "gpu_runtime", "app", "studio_ui", "engine", "advanced_render", "ai_tools",
                 "media_ai", "workflow", "subtitle_tools", "render_pipeline", "providers", "scenario_store",
                 "reference_dialogs","source_composition","layer_blend","audio_layers","text_effects","color_filters","foreign_tts","online_speech","caption_raster","layer_groups","subtitle_detail","ui_theme"]:
        actual = normalize(pyz.extract(name))
        expected = normalize(compile((ROOT / (name + ".py")).read_bytes(), str(ROOT / (name + ".py")), "exec"))
        results[name] = actual == expected
    report = dict(executable=str(executable), modules=results, all_final_sources=all(results.values()))
    (ROOT / "qa" / "bundle-source-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["all_final_sources"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
