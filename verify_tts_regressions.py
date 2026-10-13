"""Offline regressions for paid voice selection and safe speech previews.

The UI uses temporary app data and fake keys. Every provider HTTP request is
mocked; MP3 decoding uses a real FFmpeg-generated fixture and real ffprobe.
Only qa/tts-regression-report.json remains after the temporary run is cleaned.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "qa" / "tts-regression-report.json"


def main():
    report = dict(phase="tts-regressions-1.2", created_at=datetime.now(timezone.utc).isoformat(),
                  offline=True, paid_provider_requests=0, actual_network_attempts=0,
                  mocked_http_requests=0, checks=[], qt_exceptions=[])
    with tempfile.TemporaryDirectory(prefix="video-editor-tts-regressions-") as folder:
        root = Path(folder)
        os.environ.update(LOCALAPPDATA=str(root / "local-data"), APPDATA=str(root / "roaming-data"),
                          HF_HOME=str(root / "hf-cache"), TORCH_HOME=str(root / "torch-cache"),
                          HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", QT_QPA_PLATFORM="offscreen")
        from PySide6.QtCore import QCoreApplication, QEvent, QSettings
        from PySide6.QtWidgets import QApplication, QDialog, QWidget
        import requests
        import ai_tools
        import dialogs
        import engine
        import tts_providers
        import voice_catalog

        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root / "qt-settings"))
        app = QApplication.instance() or QApplication([])
        app.setApplicationName("VideoEditorIndependentTTSQA")
        app.setOrganizationName("VideoEditorIndependentTTSQA")
        parent = QWidget()
        previous_hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: report["qt_exceptions"].append(
            "".join(traceback.format_exception(kind, value, trace)))

        class Store:
            ready = False

            def summary(self, provider):
                return dict(configured=self.ready)

            def get_keys(self, provider):
                return ["mock-secret-key"] if self.ready else []

        store = Store()

        def require(value, message):
            if not value:
                raise AssertionError(message)

        def check(name, function):
            entry = dict(name=name)
            try:
                details = function()
                entry.update(status="passed", details=details or {})
            except Exception:
                entry.update(status="failed", error=traceback.format_exc())
            report["checks"].append(entry)

        def settle(predicate=lambda: True, timeout=5):
            limit = time.monotonic() + timeout
            while True:
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                if predicate():
                    return
                if time.monotonic() > limit:
                    raise AssertionError("Voice dialog worker did not settle in time")
                time.sleep(.01)

        def close_dialog(dialog):
            dialog.reject()
            settle(lambda: dialog._worker is None)

        def row(identity):
            return dict(ShortName="elevenlabs:" + identity, Locale="mul", Gender="Female",
                        FriendlyName="Voice", VoiceTag={})

        def no_network(*args, **kwargs):
            report["actual_network_attempts"] += 1
            raise AssertionError("Unmocked provider HTTP request is forbidden in this offline check")

        def response(data, kind="audio/mpeg", status=200):
            value = requests.Response()
            value.status_code = status
            value._content = data
            value._content_consumed = True
            value.headers["Content-Type"] = kind
            return value

        def paid_filters():
            store.ready = False
            dialog = dialogs.VoicePickerDialog(parent, "fpt:banmai", catalog_root=root / "fpt-filter", tts_key_store=store)
            try:
                dialog.search.setText("Mai")
                dialog.apply_filters()
                require(dialog.cards and all(not card.preview_button.isEnabled() for card in dialog.cards.values()),
                        "Filtering enabled paid preview buttons without keys")
                require(not dialog.confirm_button.isEnabled(), "Paid voice could be confirmed without a key")
            finally:
                close_dialog(dialog)

        def busy_preview():
            store.ready = False
            dialog = dialogs.VoicePickerDialog(parent, "fpt:banmai", catalog_root=root / "busy", tts_key_store=store)
            try:
                initial = dialog.selected_voice
                dialog._worker = object()
                dialog._cancelled = False
                dialog.start_preview("fpt:leminh")
                require(dialog.selected_voice == initial and not dialog._cancelled,
                        "Busy preview callback changed selection or cancelled active work")
            finally:
                dialog._worker = None
                close_dialog(dialog)

        def persisted_voice():
            store.ready = True
            dialog = dialogs.VoicePickerDialog(parent, "elevenlabs:saved_voice", catalog_root=root / "eleven", tts_key_store=store)
            try:
                require(not dialog.voices and not dialog.selected_voice, "Missing catalog fixture unexpectedly loaded voices")
                with patch.object(tts_providers, "catalog", return_value=[row("a_first"), row("saved_voice")]):
                    dialog.refresh_catalog()
                    settle(lambda: dialog._worker is None)
                require(dialog.selected_voice == "elevenlabs:saved_voice", "Refresh lost the persisted paid voice ID")
                require((root / "eleven" / "elevenlabs_catalog.json").is_file(), "Refreshed catalog was not saved")
            finally:
                close_dialog(dialog)

        def first_key():
            store.ready = False
            dialog = dialogs.VoicePickerDialog(parent, "elevenlabs:saved_voice", catalog_root=root / "empty", tts_key_store=store)

            class FakeKeys:
                def __init__(self, *args):
                    pass

                def setStyleSheet(self, *args):
                    pass

                def exec(self):
                    store.ready = True
                    return QDialog.DialogCode.Accepted

            try:
                with patch.object(dialogs, "KeysDialog", FakeKeys), patch.object(dialog, "refresh_catalog") as refresh:
                    dialog.edit_tts_keys()
                    refresh.assert_called_once()
            finally:
                close_dialog(dialog)

        def cache_failure():
            dialog = dialogs.VoicePickerDialog(parent, catalog_root=root / "edge-ui", tts_key_store=store)
            try:
                before = dialog.status.text()
                with patch.object(Path, "mkdir", side_effect=OSError("mock permission failure")):
                    dialog.start_preview()
                require(dialog._worker is None and dialog._preview_folder is None,
                        "Cache failure launched preview work")
                require(dialog.status.text() != before and "mock permission" not in dialog.status.text(),
                        "Cache failure did not become a suitable visible error")
            finally:
                close_dialog(dialog)

        def edge_staging():
            target = root / "preview.mp3"
            cancel = threading.Event()

            class FakeEdge:
                mode = "success"

                def __init__(self, *args, **kwargs):
                    pass

                async def save(self, path):
                    Path(path).write_bytes(b"ID3-mocked-audio")
                    if self.mode == "race":
                        target.write_bytes(b"existing-user-file")
                    if self.mode == "cancel":
                        cancel.set()

            class Edge:
                Communicate = FakeEdge

            with patch.object(ai_tools, "_optional", return_value=Edge):
                FakeEdge.mode = "race"
                try:
                    voice_catalog.synthesize_preview("test", "vi-VN-HoaiMyNeural", target)
                except FileExistsError:
                    pass
                else:
                    raise AssertionError("A concurrently created preview output was overwritten")
                require(target.read_bytes() == b"existing-user-file", "Concurrent output was deleted or changed")
                target.unlink()
                FakeEdge.mode = "cancel"
                try:
                    voice_catalog.synthesize_preview("test", "vi-VN-HoaiMyNeural", target, cancel)
                except ai_tools.CancelledError:
                    pass
                else:
                    raise AssertionError("Cancelled preview did not raise cancellation")
                require(not target.exists(), "Cancelled preview published an output")
                FakeEdge.mode = "success"
                result = voice_catalog.synthesize_preview("test", "vi-VN-HoaiMyNeural", target)
                require(result == str(target.resolve()) and target.read_bytes() == b"ID3-mocked-audio",
                        "Successful staged preview was not published")
            return dict(concurrent_output_preserved=True, cancellation_without_output=True, success=True)

        def source_compile():
            names = ("tts_providers.py", "voice_catalog.py", "dialogs.py")
            hashes = {}
            for name in names:
                data = (ROOT / name).read_bytes()
                compile(data.decode("utf-8"), name, "exec")
                hashes[name] = hashlib.sha256(data).hexdigest()
            report["source_sha256"] = hashes
            return dict(files=list(names))

        fixture = root / "fixture.mp3"
        mp3 = None

        def fpt_transport():
            nonlocal mp3
            subprocess.run([engine.tool_path("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=0.2", "-c:a", "libmp3lame", str(fixture)],
                           check=True, capture_output=True, creationflags=engine._creation_flags())
            mp3 = fixture.read_bytes()
            calls = []

            def fake_http(method, url, **kwargs):
                report["mocked_http_requests"] += 1
                calls.append((method, url, kwargs))
                if method == "POST":
                    require(url == "https://api.fpt.ai/hmi/tts/v5", "FPT endpoint changed unexpectedly")
                    require(kwargs["headers"]["api_key"] == "mock-secret-key" and kwargs["headers"]["voice"] == "banmai",
                            "FPT configured key or voice was not routed")
                    return response(json.dumps(dict(error=0, **{"async": "https://s3-ap-southeast-1.amazonaws.com/text2speech-v4/test.mp3"})).encode(), "application/json")
                require(kwargs["headers"] == {"Accept": "audio/mpeg"}, "FPT API key was forwarded to audio download")
                return response(b"", status=404) if len(calls) == 2 else response(mp3)

            with patch.object(requests, "request", fake_http), patch.object(tts_providers, "_POLL_INTERVAL", 0):
                output = tts_providers.synthesize("Test FPT voice", "fpt:banmai", root / "fpt.mp3", store=store)
            require(Path(output).read_bytes() == mp3 and len(calls) == 3, "FPT generation or pending audio polling failed")
            return dict(posts=1, audio_gets=2, real_mp3_validated=True)

        def eleven_transport():
            require(mp3 is not None, "Synthetic MP3 fixture was not generated")
            calls = []

            def fake_http(method, url, **kwargs):
                report["mocked_http_requests"] += 1
                calls.append((method, url, kwargs))
                require(method == "POST" and url == "https://api.elevenlabs.io/v1/text-to-speech/voice_qa", "ElevenLabs endpoint changed")
                require(kwargs["json"] == dict(text="Test ElevenLabs voice", model_id="eleven_flash_v2_5"), "ElevenLabs model or text was not routed")
                require(kwargs["params"] == dict(output_format="mp3_44100_128") and kwargs["headers"]["xi-api-key"] == "mock-secret-key",
                        "ElevenLabs format or configured key was not routed")
                return response(mp3)

            with patch.object(requests, "request", fake_http):
                output = tts_providers.synthesize("Test ElevenLabs voice", "elevenlabs:voice_qa", root / "eleven.mp3", store=store)
            require(Path(output).read_bytes() == mp3 and len(calls) == 1, "ElevenLabs generation failed or was repeated")
            return dict(posts=1, real_mp3_validated=True)

        def paginated_catalog():
            pages = [dict(voices=[dict(voice_id="a_first", name="First", labels=dict(language="vietnamese", gender="female"))],
                          has_more=True, next_page_token="next"),
                     dict(voices=[dict(voice_id="saved_voice", name="Saved", labels={}),
                                  dict(voice_id="a_first", name="Duplicate", labels={})], has_more=False)]
            calls = []

            def fake_http(method, url, **kwargs):
                report["mocked_http_requests"] += 1
                calls.append(kwargs)
                require(method == "GET" and url == "https://api.elevenlabs.io/v2/voices", "ElevenLabs voice catalog endpoint changed")
                return response(json.dumps(pages.pop(0)).encode(), "application/json")

            with patch.object(requests, "request", fake_http):
                rows = tts_providers.catalog("elevenlabs", store=store)
            require(len(rows) == 2 and rows[0]["Locale"] == "vi-VN" and rows[1]["Locale"] == "mul", "Catalog lost voices, duplicates, or locale metadata")
            require(calls[0]["params"] == dict(page_size=100) and calls[1]["params"] == dict(page_size=100, next_page_token="next"),
                    "Catalog failed to use the next-page token")
            require(len(voice_catalog.normalize_catalog(rows)) == 2, "Paid catalog rows failed cache normalization")
            return dict(pages=2, unique_voices=2)

        def authorization_failure():
            def fake_http(*args, **kwargs):
                report["mocked_http_requests"] += 1
                return response(b"", status=401)

            with patch.object(requests, "request", side_effect=fake_http) as transport:
                try:
                    tts_providers.synthesize("Unauthorized test", "elevenlabs:voice_qa", root / "failed.mp3", store=store)
                except tts_providers.TTSError as error:
                    require("401" in str(error) and "mock-secret-key" not in str(error), "Provider error exposed credentials or lost HTTP status")
                else:
                    raise AssertionError("Provider authorization failure was accepted")
                require(transport.call_count == 1 and not (root / "failed.mp3").exists(), "Failed generation was retried or published")
            return dict(posts=1, credentials_masked=True, retries=0)

        def provider_cancel():
            cancel = threading.Event()

            def fake_http(*args, **kwargs):
                report["mocked_http_requests"] += 1
                cancel.set()
                return response(b"")

            with patch.object(requests, "request", fake_http):
                try:
                    tts_providers.synthesize("Cancel request", "fpt:banmai", root / "cancelled.mp3", cancel, store=store)
                except ai_tools.CancelledError:
                    pass
                else:
                    raise AssertionError("Provider request did not honor cancellation")
            require(not (root / "cancelled.mp3").exists(), "Cancelled provider request published an output")
            return dict(cancellation_without_output=True)

        try:
            with patch.object(requests, "request", no_network):
                check("Filtered paid voice cards require keys", paid_filters)
                check("Busy preview preserves active work and selection", busy_preview)
                check("Missing paid catalog refresh restores persisted voice", persisted_voice)
                check("First ElevenLabs key import refreshes empty catalog", first_key)
                check("Preview cache failure is handled visibly", cache_failure)
                check("Edge staging protects concurrent outputs and cancellation", edge_staging)
                check("Three runtime source files compile", source_compile)
                store.ready = True
                check("FPT single POST, pending poll, credential-free audio fetch, real MP3 validation", fpt_transport)
                check("ElevenLabs single POST, official fields, real MP3 validation", eleven_transport)
                check("ElevenLabs pagination, de-duplication, cache normalization", paginated_catalog)
                check("Provider authorization error is masked and generation is not retried", authorization_failure)
                check("Provider cancellation does not publish an output", provider_cancel)
        finally:
            parent.close()
            settle()
            sys.excepthook = previous_hook

    report["temporary_data_cleaned"] = True
    report["passed"] = sum(item["status"] == "passed" for item in report["checks"])
    report["failed"] = sum(item["status"] == "failed" for item in report["checks"])
    report["status"] = "failed" if report["failed"] or report["qt_exceptions"] or report["actual_network_attempts"] else "passed"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(report=str(REPORT), passed=report["passed"], failed=report["failed"],
                          paid_provider_requests=0, status=report["status"]), ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
