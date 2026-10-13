"""Offline UI regressions for the supplied October 2026 reference screenshots.

Run with the GUI environment's Python. Uses isolated settings, opens real Qt
widgets and never calls a speech/translation service. Optional screenshots:
QT_SCALE_FACTOR=1.25 python verify_reference_layout.py --screenshots /tmp/ui-review
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="videoeditor-reference-") as temporary:
        root = Path(temporary)
        os.environ.update(
            QT_QPA_PLATFORM="offscreen", LOCALAPPDATA=str(root / "data"),
            APPDATA=str(root / "roaming"), XDG_CONFIG_HOME=str(root / "config"),
            XDG_CACHE_HOME=str(root / "cache"), HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )
        from PySide6.QtCore import QPoint, QSettings, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QFrame, QPushButton, QWidget
        from desktop import IndependentWindow, ReferenceSwitch

        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setOrganizationName("ReferenceLayoutQA")
        app.setApplicationName("ReferenceLayoutQA")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root / "settings"))
        callback_errors = []
        previous_hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: callback_errors.append(str(value))

        class LayoutTests(unittest.TestCase):
            def setUp(self):
                QSettings().clear()
                callback_errors.clear()
                self.window = IndependentWindow(testing=True)
                self.window.resize(1536, 800)
                self.window.show()
                app.processEvents()

            def tearDown(self):
                self.window.dirty = False
                self.window.close()
                app.processEvents()
                self.assertEqual(callback_errors, [], "Exception in a Qt callback")
                self.window.deleteLater()
                app.processEvents()

            def widget(self, kind, name):
                control = self.window.findChild(kind, name)
                self.assertIsNotNone(control, name)
                return control

            def test_switch_hit_area_and_collapse_preserve_workflow(self):
                for tab, name, source in (
                    (0, "pipelineRecognize", self.window.pipe_recognize),
                    (1, "pipelineTranslate", self.window.pipe_translate),
                    (2, "pipelineDub", self.window.pipe_dub),
                ):
                    self.window.pipeline_tabs.setCurrentIndex(tab)
                    app.processEvents()
                    switch = self.widget(ReferenceSwitch, name)
                    before = source.isChecked()
                    QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(33, 10))
                    self.assertEqual(source.isChecked(), not before)
                    source.setChecked(before)
                    self.assertEqual(switch.isChecked(), before)
                section = self.widget(QFrame, "translationSection")
                heading = section.findChild(QPushButton, "pipelineHeading")
                self.window.pipeline_tabs.setCurrentIndex(1)
                config = self.window.pipeline_config()
                heading.click()
                self.assertTrue(section.findChild(QWidget, "audioSectionBody").isHidden())
                self.assertEqual(config, self.window.pipeline_config())
                heading.click()
                self.assertFalse(section.findChild(QWidget, "audioSectionBody").isHidden())

            def test_localized_choices_keep_encoder_ids_and_item_flags(self):
                window = self.window
                formats = self.widget(QComboBox, "exportFormat")
                codec = self.widget(QComboBox, "exportCodec")
                formats.setCurrentIndex(formats.findText("WebM"))
                self.assertEqual(window.settings()["encoder"]["format"], "webm")
                self.assertEqual(window.settings()["encoder"]["codec"], "vp9")
                formats.setCurrentIndex(formats.findText("MOV"))
                self.assertEqual(window.enc_format.currentText(), "mov")
                vp9 = codec.findText("VP9")
                self.assertFalse(codec.model().flags(codec.model().index(vp9, 0)) & Qt.ItemFlag.ItemIsEnabled)
                window.enc_format.setCurrentText("mp4")
                self.assertEqual(formats.currentText(), "MP4")
                window.enc_device.addItem("nvidia")
                devices = self.widget(QComboBox, "exportDevice")
                devices.setCurrentIndex(devices.findText("NVIDIA"))
                self.assertEqual(window.settings()["encoder"]["device"], "nvidia")
                self.widget(QComboBox, "targetLanguage").setCurrentIndex(1)
                self.assertEqual(window.pipeline_config()["target_language"], "en")

            def test_sliders_update_real_configuration_and_reverse_sync(self):
                window = self.window
                batch = self.widget(QDoubleSpinBox, "translationBatchSize")
                batch._field_slider.setValue(135)
                self.assertEqual(window.pipeline_config()["batch_size"], 135)
                self.assertEqual(batch.value(), 135)
                workers = self.widget(QDoubleSpinBox, "translationWorkers")
                workers.setValue(3)
                self.assertEqual(window.pipeline_config()["translate_workers"], 3)
                window.request_workers.setValue(4)
                self.assertEqual(workers.value(), 4)
                self.assertEqual(workers._field_slider.value(), 4)
                speed = self.widget(QDoubleSpinBox, "sourceVideoSpeed")
                speed._field_slider.setValue(125)
                self.assertAlmostEqual(window.settings()["speed"], 1.25)
                delay = self.widget(QDoubleSpinBox, "voiceDelayMs")
                delay.setValue(-500)
                self.assertEqual(window.tts_delay.value(), -500)

            def test_saved_translation_settings_restore_visible_controls(self):
                window = self.window
                window.translation_config.update(provider="gemini", model="test-model",
                                                 image_context=True, batch_size=135, translate_workers=3)
                window._restore_translation_controls()
                switch = self.widget(ReferenceSwitch, "translationImageContextSwitch")
                self.assertTrue(switch.isChecked())
                self.assertTrue(switch.isEnabled())
                self.assertEqual(self.widget(QDoubleSpinBox, "translationBatchSize").value(), 135)
                self.assertEqual(self.widget(QDoubleSpinBox, "translationWorkers")._field_slider.value(), 3)
                window.pipe_provider.setCurrentIndex(window.pipe_provider.findData("google"))
                self.assertFalse(switch.isEnabled())
                self.assertFalse(switch.isChecked())
                QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
                self.assertFalse(window.pipeline_config()["image_context"])

            def test_quality_presets_and_conditional_voice_controls(self):
                window = self.window
                preset = self.widget(QComboBox, "exportQualityPreset")
                preset.setCurrentIndex(preset.findText("Cao"))
                self.assertEqual(window.settings()["encoder"]["quality"], 18)
                window.enc_quality.setValue(26)
                self.assertEqual(preset.currentText(), "Tùy chỉnh")
                self.assertEqual(self.widget(QDoubleSpinBox, "exportQuality").value(), 26)
                window.pipeline_tabs.setCurrentIndex(2)
                maximum = self.widget(QDoubleSpinBox, "voiceMaxSpeed")
                self.assertTrue(maximum.isHidden())
                window.tts_fit.setCurrentIndex(window.tts_fit.findData("fit"))
                self.assertFalse(maximum.isHidden())
                maximum.setValue(2.5)
                window.tts_fit.setCurrentIndex(window.tts_fit.findData("natural"))
                self.assertTrue(maximum.isHidden())
                self.assertEqual(window.voice_max_speed.value(), 2.5)

            def test_shell_at_reference_and_smaller_sizes(self):
                window = self.window
                for width, height in ((1536, 800), (1120, 690)):
                    window.resize(width, height)
                    app.processEvents()
                    self.assertEqual(window.width(), width)
                    self.assertEqual(window.right_tabs.width(), 300)
                    footer = self.widget(QFrame, "batchBar")
                    for control in (window.add_video_button, window.run_all_button,
                                    window.stop_batch_button, window.page_size, window.columns):
                        right = control.mapTo(footer, control.rect().topRight()).x()
                        self.assertLess(right, footer.width(), control.objectName())
                    self.assertFalse(window.run_all_button.isEnabled())
                    self.assertFalse(self.widget(QPushButton, "runRecognize").isEnabled())
                    for tab in range(4):
                        window.pipeline_tabs.setCurrentIndex(tab)
                        app.processEvents()
                        scroll = window.pipeline_tabs.widget(tab)
                        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

        try:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LayoutTests))
            if options.screenshots:
                options.screenshots.mkdir(parents=True, exist_ok=True)
                QSettings().clear()
                window = IndependentWindow(testing=True)
                window.resize(1536, 800)
                window.show()
                for index, name in enumerate(("subtitles", "translation", "dubbing", "system")):
                    window.pipeline_tabs.setCurrentIndex(index)
                    app.processEvents()
                    if not window.grab().save(str(options.screenshots / (name + ".png"))):
                        raise RuntimeError("Could not save screenshot: " + name)
                window.dirty = False
                window.close()
                (options.screenshots / "result.json").write_text(json.dumps({
                    "tests": result.testsRun, "failures": len(result.failures),
                    "errors": len(result.errors), "skipped": len(result.skipped),
                    "scale_factor": os.environ.get("QT_SCALE_FACTOR", "1"),
                    "logical_size": [1536, 800],
                }, indent=2), encoding="utf-8")
            return 0 if result.wasSuccessful() else 1
        finally:
            sys.excepthook = previous_hook


if __name__ == "__main__":
    raise SystemExit(main())
