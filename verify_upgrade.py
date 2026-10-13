"""Offline upgrade integration checks for the independently authored desktop.

This harness creates its own media and data directories, never loads the
reference application's code/assets/accounts, and never downloads AI models.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid


ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-voice", action="store_true")
    options = parser.parse_args()
    run = ROOT / "qa" / ("upgrade_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    run.mkdir(parents=True, exist_ok=False)
    os.environ.update(LOCALAPPDATA=str(run / "local-data"), APPDATA=str(run / "roaming-data"),
                      HF_HOME=str(run / "hf-cache"), TORCH_HOME=str(run / "torch-cache"),
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", QT_QPA_PLATFORM="offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QSettings, Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QSignalSpy, QTest
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QLineEdit, QSlider, QWidget
    import engine
    import render_pipeline
    from desktop import IndependentWindow

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(run / "qt-settings"))
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("VideoEditorIndependentUpgradeQA")
    app.setOrganizationName("VideoEditorIndependentUpgradeQA")
    app.setStyle("Fusion")
    for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "seguisb.ttf", "seguisym.ttf", "seguiemj.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI", 10))
    report = dict(application="VideoEditorIndependent", phase="upgrade", created_at=datetime.now(timezone.utc).isoformat(),
                  run_dir=str(run), offline=True, checks=[], artifacts={})
    errors = []
    notifications = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, trace: errors.append("".join(traceback.format_exception(kind, value, trace)))
    window = None
    source = run / "Nguồn có dấu – thử nâng cấp.mp4"
    second = run / "Nguồn thứ hai.mp4"

    def persist():
        report["passed"] = sum(item["status"] == "passed" for item in report["checks"])
        report["failed"] = sum(item["status"] == "failed" for item in report["checks"])
        report["status"] = "failed" if report["failed"] else "passed"
        text = json.dumps(report, ensure_ascii=False, indent=2)
        (ROOT / "qa" / "upgrade-verification-report.json").write_text(text, encoding="utf-8")
        (run / "upgrade-verification-report.json").write_text(text, encoding="utf-8")

    def settle():
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        QTest.qWait(20)

    def wait_for(predicate, title, timeout=30):
        limit = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= limit:
                raise AssertionError("Timed out: " + title)
            app.processEvents()
            QTest.qWait(15)
        settle()
        require(not errors, "Qt slot exception: " + "\n".join(errors))

    def check(name, function):
        began = time.monotonic()
        item = dict(name=name)
        report["checks"].append(item)
        try:
            details = function()
            require(not errors, "Qt slot exception: " + "\n".join(errors))
            item.update(status="passed", details=details or {})
        except Exception as exc:
            item.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        item["seconds"] = round(time.monotonic() - began, 3)
        print(json.dumps({key: item[key] for key in ("name", "status", "seconds")}, ensure_ascii=False), flush=True)
        persist()

    def capture(name):
        settle()
        path = run / (name + ".png")
        window.grab().save(str(path))
        report["artifacts"][name] = str(path)
        return str(path)

    def fixture():
        command = [engine.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                   "-f", "lavfi", "-i", "color=c=0x101010:size=320x180:rate=15:duration=1.5",
                   "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.5",
                   "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, creationflags=engine._creation_flags())
        require(result.returncode == 0, result.stderr.decode("utf-8", "replace"))
        os.link(source, second)
        info = engine.probe(str(source))
        require(info["duration"] > 1.4, "Generated source is not playable")
        report["artifacts"].update(source=str(source), second=str(second))
        return info

    def construct_and_ingest():
        nonlocal window
        window = IndependentWindow(testing=True)
        window.info = lambda title, message: notifications.append(dict(title=title, message=message))
        window.resize(1600, 920)
        window.show()
        wait_for(lambda: not window.workers, "startup")
        require(window.page_size.currentText() == "20", "Page size must default to 20")
        require([window.page_size.itemText(i) for i in range(window.page_size.count())] == ["10", "20", "30", "50", "100"],
                "Reference page-size choices differ")
        require(window.columns.currentIndex() == 1, "Grid must default to two columns")
        require(window.right_tabs.width() == 300 and window.pipeline_tabs.count() == 4, "Sidebar/tab geometry differs")
        footer = window.findChild(QFrame, "batchBar")
        require(footer is not None and footer.isAncestorOf(window.page_size) and footer.isAncestorOf(window.columns),
                "Page size/layout are not in the bottom toolbar")
        require(not window.run_all_button.isEnabled(), "Export should be disabled without selected media")
        window.ingest([str(source), str(second)])
        wait_for(lambda: len(window.library) == 2 and not window.workers, "two source imports")
        require(len(window.cards) == 2, "Imported sources do not create two cards")
        return dict(default_page=20, columns=2, sidebar_width=window.right_tabs.width(), screenshot=capture("upgrade-shell"))

    def footer_and_paging():
        original = copy.deepcopy(window.library)
        base = engine.probe(str(source))
        generated = []
        for index in range(21):
            path = run / (f"Trang thử {index+1:02}.mp4")
            os.link(source, path)
            media = dict(base, path=str(path), name=path.name)
            generated.append(media)
            # Sharing a generated poster avoids launching 21 FFmpeg jobs.
            poster = window.thumbnail_path(str(path))
            poster.parent.mkdir(parents=True, exist_ok=True)
            from PIL import Image
            Image.new("RGB", (320, 180), "#101010").save(poster)
        window.library = generated
        window._selected_paths.clear()
        window.render_library()
        settle()
        require(len(window.cards) == 20 and window.page_next.isVisible(), "Page 1 does not show 20 items with next navigation")
        window.cards[0].check.setChecked(True)
        picked = window.cards[0].path
        window._change_page(1)
        settle()
        require(len(window.cards) == 1 and window._page_number == 1 and picked in window._selected_paths,
                "Pagination loses processing selection or last-page cardinality")
        window._change_page(-1)
        settle()
        require(window.cards[0].check.isChecked(), "Returning to page 1 loses its checkbox")
        window.library = original
        window._selected_paths.clear()
        window._page_number = 0
        window.render_library()
        settle()
        require(not window.page_next.isVisible() and not window.page_back.isVisible(), "Single-page navigation should be hidden")
        require(not window.run_all_button.isEnabled(), "Footer enabled export with no selection")
        return dict(first_page=20, second_page=1, selection_survives=True, single_page_navigation_hidden=True)

    def resources_and_apply_all():
        identities = [str(source), str(second)]
        asset_state = {identities[0]: dict(original_subtitle="first.srt", translated_subtitle="first-vi.srt", narration="first.wav"),
                       identities[1]: dict(original_subtitle="second.srt", translated_subtitle="second-vi.srt", narration="second.wav")}
        window.assets = copy.deepcopy(asset_state)
        first_settings = copy.deepcopy(window.settings())
        second_settings = copy.deepcopy(window.settings())
        first_settings.update(subtitle_path="first-vi.srt", voice_path="first.wav", background_path="first-bgm.wav")
        second_settings.update(subtitle_path="second-vi.srt", voice_path="second.wav", background_path="second-bgm.wav")
        window.video_settings = {identities[0]: first_settings, identities[1]: second_settings}
        window._activate_media(identities[0])
        window.controls["brightness"].setValue(.15)
        window.apply_all()
        require(window.assets == asset_state, "Apply-all changed another video's subtitle or generated voice asset")
        for identity, original in ((identities[0], first_settings), (identities[1], second_settings)):
            copied = window.video_settings[identity]
            require(abs(copied["brightness"] - .15) < .001, "Apply-all did not copy layer adjustment")
            for key in ("subtitle_path", "voice_path", "background_path"):
                require(copied[key] == original[key], "Apply-all overwrote per-video " + key)
        # Restore actual resources before a real export.
        window.assets = {identity: {} for identity in identities}
        for settings in window.video_settings.values():
            settings.update(subtitle_path="", voice_path="", background_path="")
        window.apply_all_settings(window.video_settings[identities[0]])
        window._activate_media(identities[1])
        window._activate_media(identities[0])
        return dict(asset_identity_preserved=True, per_video_resources_preserved=True)

    def slider_bindings():
        spin = window.findChild(QDoubleSpinBox, "videoSpeed")
        require(spin is not None, "Precise video speed entry unavailable")
        slider = window.findChild(QSlider, "videoSpeedSlider")
        require(slider is not None, "Video speed row has no named slider")
        spin.setValue(1.25)
        settle()
        require(abs(window.settings()["speed"] - 1.25) < .001, "Precise entry did not update renderer speed")
        changed = slider.value()
        slider.setValue(min(slider.maximum(), changed + 5))
        settle()
        require(spin.value() != 1.25 and abs(window.settings()["speed"] - spin.value()) < .001,
                "Slider does not synchronize precise entry and render config")
        window.controls["speed"].setValue(1)
        return dict(two_way_binding=True, slider_range=[slider.minimum(), slider.maximum()])

    def card_ratios():
        window._activate_media(str(source))
        card = next(item for item in window.cards if item.path == str(source))
        choices = [card.ratio.itemText(i) for i in range(card.ratio.count())]
        require(choices == ["Gốc", "9:16", "16:9", "1:1", "4:5", "3:4", "4:3"], "Per-card ratio choices differ")
        card.ratio.setCurrentText("1:1")
        settle()
        settings = copy.deepcopy(window.settings())
        require((settings["width"], settings["height"]) == (1080, 1080), "Card ratio does not change actual renderer canvas")
        window._activate_media(str(second))
        require((window.settings()["width"], window.settings()["height"]) == (0, 0), "Changing one card ratio modified the second card")
        window._activate_media(str(source))
        require((window.settings()["width"], window.settings()["height"]) == (1080, 1080), "Card ratio was lost when changing active card")
        settings.update(speed=1, brightness=0, subtitle_path="", voice_path="", background_path="", encoder=dict(codec="h264",device="cpu",preset="fast",format="mp4"))
        target = run / "Tỷ lệ vuông thực.mp4"
        render_pipeline.render_project([dict(path=str(source))], settings, str(target), lambda p,m:None, threading.Event())
        info = engine.probe(str(target))
        require((info["width"],info["height"]) == (1080,1080), "Real exported ratio does not match selected card ratio")
        next(item for item in window.cards if item.path == str(source)).ratio.setCurrentText("Gốc")
        settle()
        report["artifacts"]["square_ratio_export"] = str(target)
        return dict(choices=choices, exported_dimensions=[info["width"],info["height"]], per_video_ratio_preserved=True)

    def interactive_layers():
        from PIL import Image
        from io import BytesIO
        window._activate_media(str(source))
        logo = run / "Logo đỏ thử.png"
        Image.new("RGBA", (30,20), (255,0,0,255)).save(logo)
        window.advanced.update(image_layers=[dict(path=str(logo),x=20,y=20,width=30,height=20,opacity=1,enabled=True)],
                               text_layers=[],blur_zones=[dict(x=220,y=70,width=60,height=50,strength=8,shape="rect",enabled=True)],
                               filter_preset="none",zoom_enabled=False,frame_border=0,fade_in=0,fade_out=0)
        window.controls["brightness"].setValue(0)
        window.controls["speed"].setValue(1)
        window.mirror.setChecked(False)
        window._canvas_size=(0,0)
        window.refresh_canvas()
        settle()
        card = next(item for item in window.cards if item.path == str(source))
        poster = card.poster
        require(poster is not None, "Editable card preview is unavailable")
        poster.set_position(.5)
        def drag(start_world,end_world):
            start = poster.world_to_view(QPointF(*start_world)).toPoint()
            end = poster.world_to_view(QPointF(*end_world)).toPoint()
            QTest.mousePress(poster,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
            QTest.mouseMove(poster,end,30)
            QTest.mouseRelease(poster,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end)
            settle()
        drag((35,30),(75,50))
        row=window.video_settings[str(source)]["image_layers"][0]
        require(abs(row["x"]-60)<=1 and abs(row["y"]-40)<=1,f"Inline image drag did not persist expected coordinates: {row}")
        poster.set_selected_layer("image_layers",0)
        # Move the selected bottom-right corner outward, making the image 50x30.
        drag((row["x"]+row["width"],row["y"]+row["height"]),(row["x"]+row["width"]+20,row["y"]+row["height"]+10))
        row=copy.deepcopy(window.video_settings[str(source)]["image_layers"][0])
        require(abs(row["width"]-50)<=1 and abs(row["height"]-30)<=1,f"Inline image resize did not persist: {row}")
        drag((250,95),(270,115))
        blur=copy.deepcopy(window.video_settings[str(source)]["blur_zones"][0])
        require(abs(blur["x"]-240)<=1 and abs(blur["y"]-90)<=1,f"Inline blur mask drag did not persist coordinates: {blur}")
        window._activate_media(str(second))
        window._activate_media(str(source))
        require(window.settings()["image_layers"][0]==row and window.settings()["blur_zones"][0]==blur,
                "Layer coordinates were lost after changing active cards")
        settings=copy.deepcopy(window.settings())
        settings.update(subtitle_path="",voice_path="",background_path="",encoder=dict(codec="h264",device="cpu",preset="fast",format="mp4"))
        target=run / "Xuất vị trí lớp đã kéo.mp4"
        render_pipeline.render_project([dict(path=str(source))],settings,str(target),lambda p,m:None,threading.Event())
        result=subprocess.run([engine.tool_path("ffmpeg"),"-v","error","-ss","0.5","-i",str(target),"-frames:v","1","-f","image2pipe","-vcodec","png","pipe:1"],
                              stdin=subprocess.DEVNULL,capture_output=True,timeout=30,creationflags=engine._creation_flags())
        require(result.returncode==0,"Could not inspect real layer output: "+result.stderr.decode("utf-8","replace")[-1000:])
        frame=Image.open(BytesIO(result.stdout)).convert("RGB")
        red=frame.getpixel((int(row["x"])+10,int(row["y"])+10))
        old=frame.getpixel((25,25))
        require(red[0]>180 and red[1]<70 and red[2]<70,f"Exported image is missing at dragged position: {red}")
        require(max(old)<60,f"Image remained at its old position in export: {old}")
        frame_path=run / "layer-export-frame.png"
        frame.save(frame_path)
        report["artifacts"].update(layer_export=str(target),layer_export_frame=str(frame_path))
        return dict(image_layer=row,blur_layer=blur,export_pixel=list(red),old_position_pixel=list(old),
                    screenshot=capture("upgrade-inline-layers"),per_video_persistence=True)

    def voice_picker():
        from dialogs import VoicePickerDialog
        catalog = [dict(ShortName="vi-VN-HoaiMyNeural", Locale="vi-VN", Gender="Female", FriendlyName="Hoài My", VoiceTag={}),
                   dict(ShortName="vi-VN-NamMinhNeural", Locale="vi-VN", Gender="Male", FriendlyName="Nam Minh", VoiceTag={}),
                   dict(ShortName="en-US-AriaNeural", Locale="en-US", Gender="Female", FriendlyName="Aria", VoiceTag={})]
        dialog = VoicePickerDialog(window, "vi-VN-HoaiMyNeural", catalog=catalog, catalog_root=run / "voice-catalog")
        spy = QSignalSpy(dialog.voice_selected)
        dialog.show()
        settle()
        language = dialog.findChild(QComboBox, "voiceLanguageFilter")
        gender = dialog.findChild(QComboBox, "voiceGenderFilter")
        search = dialog.findChild(QLineEdit, "voiceSearch")
        require(language is not None and gender is not None and search is not None, "Voice picker filters/search controls unavailable")
        require(language.findData("vi")>=0,"Vietnamese language filter unavailable")
        language.setCurrentIndex(language.findData("vi"))
        dialog.apply_filters()
        require(len(dialog.filtered_voices)==2,"Language filter failed to isolate two Vietnamese voices")
        gender.setCurrentIndex(gender.findData("Female"))
        dialog.apply_filters()
        require(len(dialog.filtered_voices)==1 and dialog.filtered_voices[0]["ShortName"]=="vi-VN-HoaiMyNeural",
                "Gender filter failed to isolate the female Vietnamese voice")
        gender.setCurrentIndex(0)
        search.setText("NamMinh")
        dialog.apply_filters()
        settle()
        require(len(dialog.filtered_voices) == 1 and dialog.filtered_voices[0]["ShortName"] == "vi-VN-NamMinhNeural",
                "Offline voice search does not filter its catalog")
        dialog.select_voice("vi-VN-NamMinhNeural")
        dialog.confirm_selection()
        settle()
        require(dialog.selected_voice == "vi-VN-NamMinhNeural" and spy.count() == 1,
                "Voice confirmation did not emit the selected ID")
        require(not dialog.isVisible(), "Voice confirm did not close dialog")
        dialog.stop_preview()
        dialog.close()
        return dict(offline_catalog=len(catalog), selected_voice=dialog.selected_voice, language_gender_search_and_confirm=True, network_calls=0)

    def voice_close_cancellation():
        import dialogs
        import ai_tools
        from unittest.mock import patch
        started=threading.Event()
        stopped=threading.Event()
        def slow_local_preview(text,voice,output,cancel,progress):
            started.set()
            while not cancel.wait(.02):
                pass
            stopped.set()
            raise ai_tools.CancelledError("QA preview cancelled")
        catalog=[dict(ShortName="vi-VN-HoaiMyNeural",Locale="vi-VN",Gender="Female",FriendlyName="Hoài My",VoiceTag={})]
        dialog=dialogs.VoicePickerDialog(window,"vi-VN-HoaiMyNeural",catalog=catalog,catalog_root=run / "close-preview")
        with patch.object(dialogs,"synthesize_preview",slow_local_preview):
            dialog.show()
            dialog.start_preview()
            wait_for(lambda: started.is_set() and dialog._worker is not None,"preview worker starts")
            dialog.close()
            wait_for(lambda: dialog._worker is None and not dialog.isVisible(),"preview worker cancels before dialog close")
            require(stopped.is_set() and not window.workers,"Voice close left a running worker registered in parent")
        return dict(active_preview_cancelled=True,clean_dialog_close=True,network_calls=0)

    try:
        check("Generated Unicode fixtures", fixture)
        check("Reference defaults, footer geometry and source import", construct_and_ingest)
        if window is not None:
            check("Page cardinality, selection persistence and export state", footer_and_paging)
            check("Apply-all retains each video's subtitles/voice resources", resources_and_apply_all)
            check("Two-way slider/precise entry/renderer binding", slider_bindings)
            check("Per-card ratios persist and determine real render dimensions", card_ratios)
            check("Inline image move/resize and blur-mask coordinates persist into real export", interactive_layers)
            if not options.skip_voice:
                check("Offline voice search and confirmed selection", voice_picker)
                check("Voice dialog cancels its active worker before closing", voice_close_cancellation)
    finally:
        if window is not None:
            wait_for(lambda: not window.workers, "pending workers before close")
            window.dirty = False
            window.close()
            settle()
            require(not window.isVisible(), "Application close failed")
        sys.excepthook = previous_hook
        report["notifications"] = notifications
        report["qt_exceptions"] = errors
        persist()
    print(json.dumps(dict(report=str(ROOT / "qa" / "upgrade-verification-report.json"), passed=report["passed"], failed=report["failed"]), ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
