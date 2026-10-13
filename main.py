"""Entry point for the independent, locally owned video workspace."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys


def data_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VideoEditorIndependent"
    root.mkdir(parents=True, exist_ok=True)
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--check-output")
    parser.add_argument("--testing", action="store_true")
    parser.add_argument("--job")
    parser.add_argument("--screenshot")
    parser.add_argument("--icon")
    options = parser.parse_args()
    logging.basicConfig(filename=str(data_root() / "application.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    if options.job:
        from app import main as run_backend_job
        run_backend_job()
        return 0
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QFontDatabase
    from app import make_icon
    from desktop import IndependentWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Video Editor Independent")
    app.setOrganizationName("VideoEditorIndependent")
    app.setApplicationVersion("1.3.2")
    app.setStyle("Fusion")
    app.setWindowIcon(make_icon())
    for font_name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "seguisb.ttf"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_name
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    def report_exception(kind, error, trace):
        logging.error("Unhandled application error", exc_info=(kind, error, trace))
        if options.self_check or options.testing:
            sys.__excepthook__(kind, error, trace)
        else:
            QMessageBox.critical(None, "Không hoàn thành thao tác", str(error))
    sys.excepthook = report_exception
    if options.icon:
        make_icon().pixmap(256, 256).save(options.icon)
        return 0
    if options.self_check:
        import engine
        import providers
        import reference_dialogs
        import importlib
        import media_ai
        media_ai._load_runtime()
        modules = {}
        for module_name in ["faster_whisper", "rapidocr_onnxruntime", "easyocr", "demucs",
                            "edge_tts", "deep_translator", "av", "torch",
                            "preview_canvas", "voice_catalog", "subtitle_editor", "tts_providers",
                            "local_tts", "gpu_runtime","source_composition","layer_blend","audio_layers","text_effects","color_filters","foreign_tts","online_speech","gtts"]:
            try:
                importlib.import_module(module_name)
                modules[module_name] = "OK"
            except Exception as error:
                modules[module_name] = str(error)
        window = IndependentWindow(testing=True)
        app.processEvents()
        result = {
            "application": app.applicationName(),
            "version": app.applicationVersion(),
            "data_root": str(providers.app_data_root()),
            "model_root": str(reference_dialogs.model_root()),
            "ffmpeg": engine.tool_path("ffmpeg"),
            "ffprobe": engine.tool_path("ffprobe"),
            "yt_dlp": engine.tool_path("yt-dlp"),
            "local_owner": True,
            "license_server": None,
            "window_title": window.windowTitle(),
            "modules": modules,
        }
        if options.check_output:
            Path(options.check_output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        window.dirty = False
        window.close()
        return 0 if all(value == "OK" for value in modules.values()) else 2
    try:
        window = IndependentWindow(testing=options.testing)
        window.showMaximized()
        if options.screenshot:
            def capture():
                window.grab().save(options.screenshot)
                window.dirty = False
                window.close()
                app.quit()
            QTimer.singleShot(1500, capture)
        return app.exec()
    except Exception:
        logging.exception("Application startup failed")
        QMessageBox.critical(None, "Không mở được Video Editor Independent",
                             "Đã ghi chi tiết lỗi tại:\n" + str(data_root() / "application.log"))
        return 1


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
