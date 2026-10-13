"""Independent desktop dialogs built on this application's own media engines.

The public mixin requires only the StudioWindow interface. No code, resources,
accounts or credentials are loaded from the reference VideoEditor installation.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import uuid

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal, QObject, QEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

import engine
import providers
import ai_tools
from reference_dialogs import (
    DIALOG_STYLE, KeysDialog, PronunciationEditorDialog, ScenariosDialog as BaseScenariosDialog,
    SettingsDialog as BaseSettingsDialog, TemplateEditorDialog,
    model_root, remove_own_cache,
)
from scenario_store import ScenarioStore
from voice_catalog import (
    catalog_root as voice_catalog_root, display_name as voice_display_name,
    fetch_catalog, language_name, load_catalog, normalize_catalog, sample_text, save_catalog,
    synthesize_preview,
)
import tts_providers
import local_tts


def _voice_engine_label(identity):
    if identity == "edge":
        return "Edge TTS"
    if identity in local_tts.SUPPORTED_IDS:
        return local_tts.PROVIDER_INFO[identity]["label"]
    return tts_providers.PROVIDER_INFO[identity]["label"]


class _ModalChrome(QObject):
    """Centered dialog chrome with the reference's dimmed workspace backdrop."""
    def __init__(self, dialog):
        # Qt can dispatch Python event filters while constructing/destroying
        # parented wrappers. Establish state before giving Qt ownership.
        self.overlay = None
        self.surface = None
        self.dialog = dialog
        super().__init__(dialog)
        parent = dialog.parentWidget()
        self.surface = parent.window() if parent is not None else None
        if self.surface is not None:
            self.overlay = QWidget(self.surface)
            self.overlay.setObjectName("referenceModalBackdrop")
            self.overlay.setStyleSheet("background:rgba(0,0,0,179);")
            self.overlay.hide()
        dialog.installEventFilter(self)
        dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

    def eventFilter(self, watched, event):
        from shiboken6 import isValid
        overlay = getattr(self, "overlay", None)
        surface = getattr(self, "surface", None)
        dialog = getattr(self, "dialog", None)
        if overlay is None or surface is None or not isValid(overlay) or not isValid(surface):
            return False
        if watched is surface and event.type() == QEvent.Type.Resize:
            overlay.setGeometry(surface.rect())
        elif watched is dialog and event.type() == QEvent.Type.Show:
            surface.installEventFilter(self)
            overlay.setGeometry(surface.rect())
            overlay.show()
            overlay.raise_()
        elif watched is dialog and event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            # Hidden dialogs must not remain filters on the live workspace.
            surface.removeEventFilter(self)
            overlay.hide()
        return False


def _reference_modal(dialog):
    dialog._reference_chrome = _ModalChrome(dialog)


THEME = DIALOG_STYLE + """
QDialog {background:#161618;color:#ededed;font-family:'VE UI';font-size:13px;}
QWidget {color:#ededed;}
QLabel#dialogTitle {font-size:17px;font-weight:700;}
QLabel#cardTitle {font-size:14px;font-weight:700;}
QLabel#sectionTitle {font-size:13px;color:#00b4d8;}
QLabel#muted,QLabel#description {color:#969696;}
QFrame#resourceCard,QFrame#keyCard,QFrame#scenarioCard {
 background:#252528;border:1px solid #3d3d3d;border-radius:9px;
}
QFrame#sectionCard {background:#303030;border:0;border-radius:8px;}
QPushButton {background:#292929;color:#dedede;border:1px solid #454545;
 border-radius:5px;padding:6px 12px;min-height:18px;}
QPushButton:hover {background:#363636;border-color:#00b4d8;}
QPushButton#primary {background:#00b4d8;color:#111111;border-color:#00b4d8;font-weight:600;}
QPushButton#primary:hover {background:#20c5df;}
QPushButton:disabled {background:#262626;color:#6f6f6f;border-color:#363636;}
QLineEdit,QPlainTextEdit,QComboBox,QSpinBox,QTableWidget,QListWidget {
 background:#252525;color:#e5e5e5;border:1px solid #444444;border-radius:5px;
 padding:6px;selection-background-color:#006f80;
}
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus {border-color:#00b4d8;}
QTabWidget::pane {background:#161618;border:0;border-top:1px solid #3a3a3a;}
QTabBar::tab {background:#363636;color:#9c9c9c;border-bottom:2px solid transparent;
 padding:10px 13px;min-height:16px;}
QTabBar::tab:selected {color:#00b4d8;border-bottom:2px solid #00b4d8;background:#363636;}
QTabBar::tab:hover {color:#d1d1d1;}
QScrollArea,QScrollArea>QWidget>QWidget {background:transparent;border:0;}
QProgressBar {background:#303030;border:0;border-radius:4px;height:7px;color:#dddddd;}
QProgressBar::chunk {background:#00b4d8;border-radius:4px;}
QCheckBox {spacing:8px;}
QCheckBox::indicator {width:16px;height:16px;border:1px solid #585858;border-radius:4px;background:#282828;}
QCheckBox::indicator:checked {background:#00b4d8;border-color:#00b4d8;}
QHeaderView::section {background:#333333;color:#cccccc;border:0;padding:6px;}
QScrollBar:vertical {background:#242424;width:7px;margin:1px;}
QScrollBar::handle:vertical {background:#505050;border-radius:3px;min-height:24px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QFrame#voiceCard {background:#252528;border:1px solid #3b3b3d;border-radius:8px;}
QFrame#voiceCard[selected="true"] {border:1px solid #00b4d8;background:#22343b;}
QFrame#voiceEngine {background:#1c1c1e;border:1px solid #373737;border-radius:7px;}
QPushButton#voiceEngineSelected {color:#00b4d8;border-color:#00b4d8;background:#252528;text-align:left;}
QLabel#voiceName {font-size:14px;font-weight:600;color:#ededed;}
QLabel#voiceBadge {color:#22c55e;font-size:11px;}
QLabel#voiceError {color:#ef7777;}
QPushButton#voicePreview {min-height:14px;padding:4px 9px;font-size:12px;}
"""


def _button(text, callback, *, primary=False):
    button = QPushButton(text)
    if primary:
        button.setObjectName("primary")
    button.clicked.connect(callback)
    return button


def _label(text, *, muted=False, title=False):
    result = QLabel(text)
    result.setWordWrap(True)
    if muted:
        result.setObjectName("muted")
    if title:
        result.setObjectName("dialogTitle")
    return result


def _scroll(body):
    result = QScrollArea()
    result.setWidgetResizable(True)
    result.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    result.setWidget(body)
    return result


def _open_folder(path):
    folder = Path(path).expanduser().resolve()
    if folder.is_file():
        folder = folder.parent
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _size_text(path):
    folder = Path(path)
    size = 0
    if folder.is_dir():
        for item in folder.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    size += item.stat().st_size
            except OSError:
                continue
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} GB"
    return f"{size / 1024 ** 2:.1f} MB"


class TaskThread(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.result.emit(self.function(self.progress.emit, self.cancel_event))
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class VoiceCard(QFrame):
    selected = Signal(str)
    preview_requested = Signal(str)

    def __init__(self, voice, parent=None):
        super().__init__(parent)
        self.voice = dict(voice)
        self.setObjectName("voiceCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(200, 62)
        self.setMaximumHeight(62)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(voice["ShortName"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(4)
        glyph = QLabel("♀" if voice["Gender"] == "Female" else "♂" if voice["Gender"] == "Male" else "◆")
        glyph.setStyleSheet("color:#ef4444;" if voice["Gender"] == "Female" else "color:#60a5fa;")
        top.addWidget(glyph)
        title = _label(voice_display_name(voice))
        title.setObjectName("voiceName")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setStyleSheet("font-size:12px;font-weight:600;")
        top.addWidget(title, 1)
        self.preview_button = _button("▶", lambda: self.preview_requested.emit(voice["ShortName"]))
        self.preview_button.setObjectName("voicePreview")
        self.preview_button.setFixedSize(24, 24)
        self.preview_button.setToolTip("Nghe thử")
        top.addWidget(self.preview_button)
        layout.addLayout(top)
        gender = {"Female": "Nữ", "Male": "Nam"}.get(voice["Gender"], "Khác")
        info = _label(voice["Locale"] + " · " + gender, muted=True)
        info.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(info)
        personalities = voice.get("VoiceTag", {}).get("VoicePersonalities", [])
        description = _label(", ".join(personalities[:2]) if personalities else language_name(voice["Locale"]), muted=True)
        description.setStyleSheet("font-size:11px;")
        description.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        description.setVisible(False)
        actions = QHBoxLayout()
        self.choice = _label("", muted=True)
        self.choice.setStyleSheet("font-size:11px;color:#00b4d8;")
        self.choice.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        actions.addWidget(self.choice, 1)
        layout.addLayout(actions)

    def set_selected(self, selected):
        self.setProperty("selected", bool(selected))
        self.choice.setText("Đã chọn" if selected else "")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.voice["ShortName"])
        super().mousePressEvent(event)


class VoicePickerDialog(QDialog):
    """Draft selection, catalog filtering, real Edge TTS preview and playback."""
    voice_selected = Signal(str)

    def __init__(self, parent, selected_voice=None, *, catalog=None, catalog_root=None, tts_key_store=None):
        super().__init__(parent)
        _reference_modal(self)
        self.setObjectName("voicePickerDialog")
        self.setWindowTitle("Chọn Giọng Đọc · Video Editor Independent")
        self.setMinimumSize(820, 540)
        self.resize(980, 640)
        self.setStyleSheet(THEME)
        self.catalog_root = Path(catalog_root) if catalog_root is not None else voice_catalog_root()
        self.tts_key_store = tts_key_store or getattr(parent, "tts_key_store", None) or tts_providers.TTSKeyStore()
        current = selected_voice
        if current is None:
            current = getattr(parent, "pipe_voice", None)
            current = current.currentText() if isinstance(current, QComboBox) else "vi-VN-HoaiMyNeural"
        prefix = str(current).partition(":")[0]
        self.engine_id = prefix if prefix in tts_providers.supported_ids + local_tts.SUPPORTED_IDS else "edge"
        self._catalog_overrides = {}
        if catalog is not None:
            supplied = normalize_catalog(catalog)
            for engine_id in ("edge",) + tts_providers.supported_ids + local_tts.SUPPORTED_IDS:
                self._catalog_overrides[engine_id] = [voice for voice in supplied if voice.get("Provider", "edge") == engine_id]
        self.voices, self.catalog_metadata = self._load_engine_catalog()
        self._selected_by_engine = {self.engine_id: str(current)}
        self.filtered_voices = []
        self.cards = {}
        self.selected_voice = ""
        self._worker = None
        self._operation = ""
        self._cancelled = False
        self._pending_close = None
        self._preview_folder = None
        self._preview_path = None
        self._preview_voice = ""
        self._sample_locale = ""
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(.85)
        self.player.setAudioOutput(self.audio_output)
        self.player.errorOccurred.connect(self._playback_error)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.playbackStateChanged.connect(self._playback_state)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(_label("Chọn Giọng Đọc", title=True), 1)
        self.refresh_button = _button("⟳ Làm mới danh sách", self.refresh_catalog)
        self.refresh_button.setObjectName("voiceRefresh")
        heading.addWidget(_button("✕", self.reject))
        outer.addLayout(heading)
        content = QHBoxLayout()
        content.setSpacing(0)
        engine_card = QFrame()
        engine_card.setObjectName("voiceEngine")
        engine_card.setFixedWidth(184)
        engine_layout = QVBoxLayout(engine_card)
        engine_layout.setContentsMargins(4, 4, 4, 4)
        engine_layout.setSpacing(1)
        self.engine_buttons = {}
        for engine_id in ("edge",) + tts_providers.supported_ids + local_tts.SUPPORTED_IDS:
            label = _voice_engine_label(engine_id)
            button = _button(label, lambda checked=False, identity=engine_id: self.set_engine(identity))
            button.setObjectName("voiceEngine_" + engine_id)
            button.setFixedHeight(30)
            engine_layout.addWidget(button)
            self.engine_buttons[engine_id] = button
        self.engine_badge = _label("")
        self.engine_badge.setObjectName("voiceBadge")
        engine_layout.addWidget(self.engine_badge)
        self.engine_hint = _label("", muted=True)
        engine_layout.addWidget(self.engine_hint)
        self.keys_button = _button("Nhập / sửa khóa API", self.edit_tts_keys)
        self.keys_button.setObjectName("voiceKeys")
        engine_layout.addWidget(self.keys_button)
        engine_layout.addStretch()
        self.catalog_status = _label("", muted=True)
        self.catalog_status.setStyleSheet("font-size:11px;")
        engine_layout.addWidget(self.catalog_status)
        content.addWidget(_scroll(engine_card))
        body = QVBoxLayout()
        body.setContentsMargins(10, 0, 10, 0)
        filters = QHBoxLayout()
        self.language_filter = QComboBox()
        self.language_filter.setObjectName("voiceLanguageFilter")
        self.language_filter.setMinimumWidth(138)
        self.language_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.language_filter.setMinimumContentsLength(12)
        self.gender_filter = QComboBox()
        self.gender_filter.setObjectName("voiceGenderFilter")
        self.gender_filter.addItem("Tất cả giới tính", "")
        self.gender_filter.addItem("Nữ", "Female")
        self.gender_filter.addItem("Nam", "Male")
        self.search = QLineEdit()
        self.search.setObjectName("voiceSearch")
        self.search.setPlaceholderText("Tìm giọng đọc hoặc ngôn ngữ…")
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.language_filter)
        self.gender_filter.setParent(self)
        self.gender_filter.hide()
        filters.addWidget(self.search, 1)
        self.refresh_button.setText("⟳")
        self.refresh_button.setFixedSize(28, 28)
        self.refresh_button.setToolTip("Tải lại danh sách giọng")
        filters.addWidget(self.refresh_button)
        body.addLayout(filters)
        self.card_scroll = QScrollArea()
        self.card_scroll.setObjectName("voiceCards")
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_body = QWidget()
        self.card_layout = QVBoxLayout(self.card_body)
        self.card_layout.setContentsMargins(0, 3, 4, 8)
        self.card_layout.setSpacing(10)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.card_scroll.setWidget(self.card_body)
        body.addWidget(self.card_scroll, 1)
        self.filtered_count = _label("", muted=True)
        self.filtered_count.setStyleSheet("font-size:11px;")
        self.filtered_count.hide()
        content.addLayout(body, 1)
        outer.addLayout(content, 1)
        selected = QFrame()
        selected.setObjectName("sectionCard")
        selected_layout = QVBoxLayout(selected)
        selected_layout.setContentsMargins(12, 9, 12, 10)
        selected_layout.setSpacing(6)
        self.selected_details = _label("Chọn một giọng để tiếp tục.")
        self.selected_details.setObjectName("voiceSelectedDetails")
        selected_layout.addWidget(self.selected_details)
        preview = QHBoxLayout()
        self.preview_text = QLineEdit()
        self.preview_text.setObjectName("voicePreviewText")
        self.preview_text.setMaxLength(500)
        self.preview_text.setPlaceholderText("Nội dung nghe thử")
        preview.addWidget(self.preview_text, 1)
        self.preview_button = _button("▷ Nghe thử", self.start_preview)
        self.preview_button.setObjectName("voicePreviewSelected")
        preview.addWidget(self.preview_button)
        self.stop_button = _button("■ Dừng", self.stop_preview)
        self.stop_button.setObjectName("voicePreviewStop")
        self.stop_button.setEnabled(False)
        preview.addWidget(self.stop_button)
        selected_layout.addLayout(preview)
        selected.setParent(self)
        selected.hide()
        footer = QHBoxLayout()
        self.status = _label("", muted=True)
        self.status.setObjectName("voiceStatus")
        footer.addWidget(self.status, 1)
        self.confirm_button = _button("Xác nhận", self.confirm_selection, primary=True)
        self.confirm_button.setObjectName("voiceConfirm")
        self.confirm_button.setEnabled(False)
        footer.addWidget(self.confirm_button)
        outer.addLayout(footer)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(100)
        self.filter_timer.timeout.connect(self.apply_filters)
        self.language_filter.currentIndexChanged.connect(self.apply_filters)
        self.gender_filter.currentIndexChanged.connect(self.apply_filters)
        self.search.textChanged.connect(lambda: self.filter_timer.start())
        self._populate_languages()
        self.apply_filters()
        if not self.select_voice(str(current)) and self.voices:
            self.select_voice(self.voices[0]["ShortName"])
        self._show_catalog_status()
        self._update_engine_state()

    def _load_engine_catalog(self):
        if self.engine_id in self._catalog_overrides:
            return self._catalog_overrides[self.engine_id], dict(source="provided", updated_at="", error="")
        return load_catalog(self.catalog_root, provider=self.engine_id)

    def _engine_ready(self):
        if self.engine_id in ("edge","gtts"):
            return True
        if self.engine_id in local_tts.SUPPORTED_IDS:
            return bool(local_tts.availability(self.engine_id)["ready"])
        try:
            config=self.tts_key_store.get_options(self.engine_id) if hasattr(self.tts_key_store,"get_options") else {}
            if self.engine_id=="capcut":
                from online_speech import bridge_url
                return bool(bridge_url(config.get("endpoint","")))
            if self.engine_id=="vbee" and not config.get("app_id"):
                return False
            return bool(self.tts_key_store.summary(self.engine_id).get("configured"))
        except ai_tools.AIError:
            return False

    def _update_engine_state(self):
        ready = self._engine_ready()
        for identity, button in self.engine_buttons.items():
            button.setStyleSheet("color:#00b4d8;border-color:#00b4d8;background:#252528;text-align:left;" if identity == self.engine_id else "text-align:left;")
            button.setEnabled(self._worker is None)
        self.keys_button.setVisible(self.engine_id in tts_providers.supported_ids and self.engine_id!="gtts")
        self.keys_button.setText("Cấu hình kết nối / khóa" if self.engine_id in ("vbee","minimax","siliconflow","capcut") else "Nhập / sửa khóa API")
        self.keys_button.setEnabled(self._worker is None)
        if self.engine_id in ("edge","gtts"):
            self.engine_badge.setText("Không cần khóa API")
            self.engine_badge.setStyleSheet("color:#22c55e;")
            self.engine_hint.setText("Nghe thử và lồng tiếng cần Internet.")
        elif self.engine_id in local_tts.SUPPORTED_IDS:
            info = local_tts.availability(self.engine_id)
            self.engine_badge.setText("ĐÃ CÀI" if ready else "CHƯA CÀI")
            self.engine_badge.setStyleSheet("color:#22c55e;" if ready else "color:#eab44b;")
            self.engine_hint.setText(info.get("hint", "Giọng ngoại tuyến trên máy."))
        else:
            self.engine_badge.setText("ĐÃ NHẬP KHÓA" if ready else "CẦN KHÓA API")
            self.engine_badge.setStyleSheet("color:#22c55e;" if ready else "color:#eab44b;")
            self.engine_hint.setText("Nghe thử và lồng tiếng dùng hạn mức của tài khoản bạn."
                                     + ("\nEleven Flash v2.5 hỗ trợ tiếng Việt; giọng đọc suy ra ngôn ngữ từ nội dung." if self.engine_id == "elevenlabs" else ""))
            if self.engine_id=="capcut":
                self.engine_badge.setText("ĐÃ CẤU HÌNH" if ready else "CẦN CẦU NỐI")
                self.engine_hint.setText("Dùng cầu nối CapCut do bạn vận hành với tài khoản của bạn. Nhập endpoint trong Cấu hình kết nối.")
        self.refresh_button.setEnabled(self._worker is None and (ready or self.engine_id == "fpt"))
        self.preview_button.setEnabled(self._worker is None and ready and bool(self.selected_voice))
        self.confirm_button.setEnabled(ready and bool(self.selected_voice))
        for card in self.cards.values():
            card.preview_button.setEnabled(self._worker is None and ready)
        if not ready and not self._worker:
            if self.engine_id in local_tts.SUPPORTED_IDS:
                self.status.setText(local_tts.availability(self.engine_id).get("hint", "Chưa có mô hình giọng trên máy."))
            else:
                self.status.setText("Nhập khóa " + _voice_engine_label(self.engine_id) + " để nghe thử hoặc dùng giọng đọc.")

    def set_engine(self, identity):
        if identity not in {"edge", *tts_providers.supported_ids, *local_tts.SUPPORTED_IDS} or self._worker:
            return False
        if self.selected_voice:
            self._selected_by_engine[self.engine_id] = self.selected_voice
        self.stop_preview()
        self.engine_id = identity
        self.voices, self.catalog_metadata = self._load_engine_catalog()
        self.selected_voice = ""
        self.selected_details.setText("Chọn một giọng để tiếp tục.")
        self._populate_languages()
        self.search.clear()
        self.apply_filters()
        previous = self._selected_by_engine.get(identity, "")
        if not self.select_voice(previous) and self.voices:
            self.select_voice(self.voices[0]["ShortName"])
        self._show_catalog_status()
        self._update_engine_state()
        return True

    def edit_tts_keys(self):
        if self.engine_id not in tts_providers.supported_ids or self._worker:
            return
        dialog = SpeechConnectionDialog(self,self.tts_key_store,self.engine_id) if self.engine_id in ("vbee","minimax","siliconflow","capcut") else KeysDialog(self, self.tts_key_store, self.engine_id, tts_providers.PROVIDER_INFO[self.engine_id]["label"])
        dialog.setStyleSheet(THEME)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._update_engine_state()
            if self.engine_id == "elevenlabs" and not self.voices and self._engine_ready():
                self.refresh_catalog()

    def _populate_languages(self):
        current = self.language_filter.currentData() or ""
        self.language_filter.blockSignals(True)
        self.language_filter.clear()
        self.language_filter.addItem("Tất cả ngôn ngữ", "")
        languages = {voice["Locale"].split("-")[0] for voice in self.voices}
        for language in sorted(languages, key=lambda value: (value != "vi", language_name(value))):
            self.language_filter.addItem(language_name(language), language)
        self.language_filter.setCurrentIndex(max(0, self.language_filter.findData(current)))
        self.language_filter.blockSignals(False)

    def _show_catalog_status(self):
        source = self.catalog_metadata.get("source")
        if source == "seed":
            text = "3 giọng mặc định.\nLàm mới để tải danh sách hiện tại."
        elif source == "local":
            text = f"{len(self.voices)} giọng ngoại tuyến đã cài trên máy."
        elif source == "documented":
            text = f"{len(self.voices)} giọng FPT.AI được công bố trong tài liệu API.\nQuyền dùng và hạn mức được kiểm tra khi tạo giọng."
        elif source == "missing":
            text = "Chưa tải danh sách giọng của tài khoản.\nNhập khóa rồi bấm Làm mới."
        elif source in {"live", "cache"}:
            text = f"{len(self.voices)} giọng · {'vừa làm mới' if source == 'live' else 'danh sách đã lưu'}"
            updated = self.catalog_metadata.get("updated_at", "")
            if updated:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                try:
                    text += "\n" + datetime.fromisoformat(updated).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M")
                except (ValueError, KeyError):
                    pass
        else:
            text = f"{len(self.voices)} giọng trong danh sách"
        self.catalog_status.setText(text)
        error = self.catalog_metadata.get("error")
        if error:
            self.status.setText(error)

    def apply_filters(self, *_):
        from unicodedata import normalize, combining
        def plain(value):
            return "".join(character for character in normalize("NFKD", str(value).casefold()) if not combining(character)).replace("đ", "d")
        query = plain(self.search.text().strip())
        language = self.language_filter.currentData() or ""
        gender = self.gender_filter.currentData() or ""
        filtered = []
        for voice in self.voices:
            if language and voice["Locale"].split("-")[0] != language:
                continue
            if gender and voice["Gender"] != gender:
                continue
            searchable = " ".join((voice["ShortName"], voice.get("FriendlyName", ""), voice["Locale"], language_name(voice["Locale"]), voice_display_name(voice)))
            if query and query not in plain(searchable):
                continue
            filtered.append(voice)
        self.filtered_voices = filtered
        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.cards = {}
        if not filtered:
            self.card_layout.addWidget(_label("Không có giọng phù hợp. Thử đổi ngôn ngữ, giới tính hoặc từ tìm kiếm.", muted=True))
        for group, title in (("Female", "Giọng nữ"), ("Male", "Giọng nam"), ("Unknown", "Giọng khác")):
            rows = [voice for voice in filtered if voice["Gender"] == group]
            if not rows:
                continue
            group_widget = QWidget()
            group_layout = QVBoxLayout(group_widget)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(7)
            group_layout.addWidget(_label(title + f" · {len(rows)}", muted=True))
            grid = QGridLayout()
            grid.setHorizontalSpacing(9)
            grid.setVerticalSpacing(9)
            for column in range(3):
                grid.setColumnStretch(column, 1)
            for index, voice in enumerate(rows):
                card = VoiceCard(voice)
                card.selected.connect(self.select_voice)
                card.preview_requested.connect(self.start_preview)
                card.set_selected(voice["ShortName"] == self.selected_voice)
                card.preview_button.setEnabled(self._worker is None and self._engine_ready())
                self.cards[voice["ShortName"]] = card
                grid.addWidget(card, index // 3, index % 3)
            group_layout.addLayout(grid)
            self.card_layout.addWidget(group_widget)
        self.card_layout.addStretch()
        self.filtered_count.setText(f"Hiển thị {len(filtered)} / {len(self.voices)} giọng")

    def select_voice(self, short_name):
        voice = next((row for row in self.voices if row["ShortName"] == str(short_name)), None)
        if voice is None:
            return False
        if self._preview_voice and self._preview_voice != voice["ShortName"]:
            self.stop_preview()
        self.selected_voice = voice["ShortName"]
        self._selected_by_engine[self.engine_id] = self.selected_voice
        for identity, card in self.cards.items():
            card.set_selected(identity == self.selected_voice)
        gender = {"Female": "Nữ", "Male": "Nam"}.get(voice["Gender"], "Khác")
        engine_label = _voice_engine_label(self.engine_id)
        self.selected_details.setText(voice_display_name(voice) + " · " + language_name(voice["Locale"]) + " · " + gender + "\n" + self.selected_voice + " · " + engine_label)
        if self._sample_locale != voice["Locale"] or not self.preview_text.text().strip():
            self.preview_text.setText(sample_text(voice["Locale"]))
            self._sample_locale = voice["Locale"]
        self.confirm_button.setEnabled(self._engine_ready())
        self.preview_button.setEnabled(self._worker is None and self._engine_ready())
        if not self._worker:
            suffix = ". Nghe thử trên máy." if self.engine_id in local_tts.SUPPORTED_IDS else ". Nghe thử cần Internet."
            self.status.setText("Đã chọn " + voice_display_name(voice) + suffix)
        if self.engine_id != "edge":
            self._update_engine_state()
        return True

    def refresh_catalog(self):
        if self._worker:
            return
        if self.engine_id in local_tts.SUPPORTED_IDS:
            requested = self.selected_voice
            self.voices, self.catalog_metadata = self._load_engine_catalog()
            self._populate_languages()
            self.apply_filters()
            if not self.select_voice(requested) and self.voices:
                self.select_voice(self.voices[0]["ShortName"])
            self._show_catalog_status()
            self._update_engine_state()
            return
        if self.engine_id == "fpt":
            self.voices = normalize_catalog(tts_providers.catalog("fpt"))
            self.catalog_metadata = dict(source="documented", updated_at="", error="")
            self._populate_languages()
            self.apply_filters()
            self._show_catalog_status()
            self._update_engine_state()
            return
        if not self._engine_ready():
            self._update_engine_state()
            return
        self.stop_preview()
        def done(result):
            requested = self.selected_voice or self._selected_by_engine.get(self.engine_id, "")
            self.voices, self.catalog_metadata = result
            self._populate_languages()
            self.apply_filters()
            if not self.select_voice(requested) and self.voices:
                self.select_voice(self.voices[0]["ShortName"])
            self._show_catalog_status()
            engine_label = _voice_engine_label(self.engine_id)
            self.status.setText(f"Đã tải {len(self.voices)} giọng {engine_label}.")
        if self.engine_id == "edge":
            self._start_worker("catalog", lambda p, c: fetch_catalog(c, self.catalog_root, p), done)
        else:
            def load_provider(progress, cancel):
                voices = normalize_catalog(tts_providers.catalog(self.engine_id, cancel, store=self.tts_key_store))
                metadata = save_catalog(voices, self.catalog_root, cancel=cancel, provider=self.engine_id)
                return voices, metadata
            self._start_worker("catalog", load_provider, done)

    def start_preview(self, voice=None):
        # clicked(bool) supplies False; it is not a ShortName.
        if self._worker:
            return
        if isinstance(voice, str):
            if not self.select_voice(voice):
                return
        if not self.selected_voice or not self._engine_ready():
            self._update_engine_state()
            return
        text = self.preview_text.text().strip()
        if not text:
            self.status.setText("Nhập nội dung nghe thử.")
            return
        self.stop_preview()
        if self._preview_folder is None:
            destination = self.catalog_root / "previews"
            try:
                destination.mkdir(parents=True, exist_ok=True)
                self._preview_folder = tempfile.TemporaryDirectory(prefix="preview-", dir=destination)
            except OSError:
                self.status.setText("Không tạo được thư mục nghe thử. Kiểm tra quyền ghi dữ liệu của ứng dụng.")
                self.status.setStyleSheet("color:#ef7777;")
                return
        output = Path(self._preview_folder.name) / (uuid.uuid4().hex + ".mp3")
        self._preview_voice = self.selected_voice
        short_name = self.selected_voice
        def done(path):
            self._preview_path = Path(path)
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.play()
            self.status.setText("Đang phát " + self._preview_voice + "…")
            self.stop_button.setEnabled(True)
        if self.engine_id == "edge":
            self._start_worker("preview", lambda p, c: synthesize_preview(text, short_name, output, c, p), done)
        else:
            self._start_worker("preview", lambda p, c: synthesize_preview(text, short_name, output, c, p, store=self.tts_key_store), done)

    def _start_worker(self, operation, function, done):
        if self._worker:
            return
        self._operation, self._cancelled = operation, False
        self.refresh_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        for card in self.cards.values():
            card.preview_button.setEnabled(False)
        self.status.setText("Đang tải danh sách giọng…" if operation == "catalog" else "Đang tạo âm thanh nghe thử…")
        worker = TaskThread(function, self)
        self._worker = worker
        self._update_engine_state()
        parent = self.parentWidget()
        if parent and hasattr(parent, "workers"):
            parent.workers.add(worker)
        worker.progress.connect(lambda _percent, message: self.status.setText(message) if not self._cancelled else None)
        worker.result.connect(lambda result: done(result) if not self._cancelled and self._pending_close is None else None)
        def failed(message):
            if self._cancelled:
                self.status.setText("Đã dừng." if self._pending_close is None else "Đang đóng…")
            else:
                self.status.setText(message)
                self.status.setStyleSheet("color:#ef7777;")
            self._preview_voice = ""
        worker.failed.connect(failed)
        def finished():
            if parent and hasattr(parent, "workers"):
                parent.workers.discard(worker)
            self._worker = None
            self._operation = ""
            worker.deleteLater()
            self.refresh_button.setEnabled(True)
            self.preview_button.setEnabled(bool(self.selected_voice))
            self.stop_button.setEnabled(self.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState)
            for card in self.cards.values():
                card.preview_button.setEnabled(True)
            self._update_engine_state()
            if self._pending_close is not None:
                self._finish_close(self._pending_close)
        worker.finished.connect(finished)
        self.status.setStyleSheet("")
        worker.start()

    def stop_preview(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self._preview_voice = ""
        if self._preview_path:
            try:
                self._preview_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._preview_path = None
        if self._worker:
            self._cancelled = True
            self._worker.cancel_event.set()
            self.status.setText("Đang dừng…")
        self.stop_button.setEnabled(False)

    def _playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.stop_button.setEnabled(True)
        elif state == QMediaPlayer.PlaybackState.StoppedState and not self._worker:
            self.stop_button.setEnabled(False)

    def _media_status(self, state):
        if state == QMediaPlayer.MediaStatus.EndOfMedia:
            self.status.setText("Đã phát xong. Có thể nghe lại hoặc xác nhận giọng đọc.")

    def _playback_error(self, *_):
        self.status.setText("Không phát được âm thanh nghe thử. " + self.player.errorString())
        self.status.setStyleSheet("color:#ef7777;")
        self.stop_button.setEnabled(False)

    def confirm_selection(self):
        if self._engine_ready() and self.selected_voice and any(row["ShortName"] == self.selected_voice for row in self.voices):
            self.done(QDialog.DialogCode.Accepted)

    def _finish_close(self, code):
        self.stop_preview()
        if self._preview_folder:
            try:
                self._preview_folder.cleanup()
            except OSError:
                pass
            self._preview_folder = None
        if code == QDialog.DialogCode.Accepted:
            self.voice_selected.emit(self.selected_voice)
        self._pending_close = None
        super().done(code)

    def done(self, code):
        if self._worker:
            self._pending_close = code
            self.confirm_button.setEnabled(False)
            self.stop_preview()
            self.status.setText("Đang dừng kết nối trước khi đóng…")
            return
        self._finish_close(code)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        if self._worker:
            self.reject()
            event.ignore()
            return
        self.stop_preview()
        super().closeEvent(event)


class SpeechConnectionDialog(QDialog):
    def __init__(self,parent,store,provider):
        super().__init__(parent)
        _reference_modal(self)
        self.store,self.provider=store,provider
        self.setWindowTitle("Kết nối · "+tts_providers.PROVIDER_INFO[provider]["label"])
        self.resize(540,330)
        self.setStyleSheet(THEME)
        layout=QVBoxLayout(self)
        layout.addWidget(_label(self.windowTitle(),title=True))
        values=store.get_options(provider)
        form=QFormLayout()
        self.fields={}
        keys=("app_id",) if provider=="vbee" else ("endpoint",) if provider=="capcut" else ("model",)
        for key in keys:
            control=QLineEdit(values.get(key,""))
            control.setObjectName("speechConnection_"+key)
            control.setPlaceholderText({"app_id":"App ID của Vbee","endpoint":"http://localhost:8080","model":"Mô hình mặc định của nhà cung cấp"}[key])
            form.addRow({"app_id":"App ID","endpoint":"Địa chỉ cầu nối","model":"Mô hình"}[key],control)
            self.fields[key]=control
        layout.addLayout(form)
        layout.addWidget(_button("Nhập / sửa khóa API",self.edit_keys))
        hint="Cầu nối cần cung cấp GET /v2/speakers và POST /v2/synthesize trả MP3. Đăng nhập tài khoản của bạn tại dịch vụ cầu nối." if provider=="capcut" else "Nhập khóa riêng của bạn. Vbee cần App ID và access token; tài khoản dịch vụ phải còn hạn mức."
        layout.addWidget(_label(hint,muted=True))
        self.status=_label("")
        layout.addWidget(self.status)
        actions=QHBoxLayout()
        actions.addWidget(_button("Lưu",self.save,primary=True))
        actions.addWidget(_button("Đóng",self.reject))
        layout.addLayout(actions)

    def edit_keys(self):
        KeysDialog(self,self.store,self.provider,tts_providers.PROVIDER_INFO[self.provider]["label"]).exec()

    def save(self):
        try:
            self.store.save_options(self.provider,{key:value.text() for key,value in self.fields.items()})
        except (ValueError,ai_tools.AIError) as error:
            self.status.setText(str(error))
            return
        self.accept()


class SettingsDialog(BaseSettingsDialog):
    """API key management and local workspace settings, with real persistence."""
    def __init__(self, parent, provider_store, scenario_store, prefs):
        self.tts_key_store = getattr(parent, "tts_key_store", None) or tts_providers.TTSKeyStore()
        self.tts_key_cards = {}
        super().__init__(parent, provider_store, scenario_store, prefs, resources_root=model_root())
        self.setWindowTitle("Thiết Lập · Video Editor Independent")
        self.setMinimumSize(620, 460)
        self.resize(680, 520)
        _reference_modal(self)
        self.setStyleSheet(THEME)
        self.tabs.setTabText(1, "Dịch vụ")
        self.tabs.setTabText(2, "Cài đặt chung")
        for frame in self.findChildren(QFrame):
            if frame.objectName() == "keyCard":
                frame.setMinimumHeight(108)
        for scroll in self.findChildren(QScrollArea):
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def build_keys(self):
        page = super().build_keys()
        body = page.widget()
        layout = body.layout()
        section = QFrame()
        section.setObjectName("sectionCard")
        section_layout = QVBoxLayout(section)
        section_layout.addWidget(_label("Lồng tiếng · Khóa của bạn", title=True))
        section_layout.addWidget(_label("Các dịch vụ dùng khóa và hạn mức tài khoản của bạn. Khóa được mã hóa bằng Windows DPAPI.", muted=True))
        grid = QGridLayout()
        for index, (provider, metadata) in enumerate(tts_providers.PROVIDER_INFO.items()):
            card = QFrame()
            card.setObjectName("keyCard")
            card.setMinimumHeight(108)
            column = QVBoxLayout(card)
            top = QHBoxLayout()
            title = _label(metadata["label"])
            title.setObjectName("cardTitle")
            top.addWidget(title, 1)
            badge = _label("CHƯA CÓ")
            badge.setObjectName("missingBadge")
            top.addWidget(badge)
            column.addLayout(top)
            summary = _label("(Chưa có)", muted=True)
            column.addWidget(summary)
            column.addWidget(_button("Quản lý khóa", lambda checked=False, identity=provider: self.edit_tts_keys(identity)))
            self.tts_key_cards[provider] = dict(badge=badge, masked=summary)
            grid.addWidget(card, index//2, index%2)
        section_layout.addLayout(grid)
        layout.insertWidget(max(0, layout.count() - 2), section)
        return page

    def refresh_keys(self):
        super().refresh_keys()
        for provider, card in self.tts_key_cards.items():
            try:
                data = self.tts_key_store.summary(provider)
                count, last4 = int(data.get("count", 0)), str(data.get("last4", ""))[-4:]
            except ai_tools.AIError:
                count, last4 = 0, ""
            badge = card["badge"]
            badge.setText(f"{count} KEY" if count else "CHƯA CÓ")
            badge.setObjectName("readyBadge" if count else "missingBadge")
            badge.style().unpolish(badge)
            badge.style().polish(badge)
            card["masked"].setText("••••••" + last4 if count else "(Chưa có)")

    def edit_tts_keys(self, provider):
        if provider=="gtts":
            QMessageBox.information(self,"Google TTS","Google TTS dùng Internet và không cần khóa API.")
            return
        dialog = SpeechConnectionDialog(self,self.tts_key_store,provider) if provider in ("vbee","minimax","siliconflow","capcut") else KeysDialog(self, self.tts_key_store, provider, tts_providers.PROVIDER_INFO[provider]["label"])
        dialog.setStyleSheet(THEME)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_keys()
            self.settings_changed.emit()

    def refresh_resources(self):
        # Model cards are intentionally presented in their Downloads dialog.
        pass

    def build_accounts(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(_label("Dịch vụ của bạn", title=True))
        layout.addWidget(_label("Google Translate và Edge TTS sử dụng Internet. Với dịch AI, nhập khóa API của bạn trong tab Khóa API.", muted=True))
        for metadata in list(providers.PROVIDERS.values()) + list(tts_providers.PROVIDER_INFO.values()):
            card = QFrame()
            card.setObjectName("resourceCard")
            line = QHBoxLayout(card)
            line.addWidget(_label(metadata["label"]), 1)
            url = metadata.get("keys_url") or metadata.get("docs_url")
            line.addWidget(_button("Mở trang cấp khóa", lambda checked=False, u=url: QDesktopServices.openUrl(QUrl(u))))
            layout.addWidget(card)
        layout.addStretch()
        return _scroll(body)

    def build_common(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        card = QFrame()
        card.setObjectName("sectionCard")
        form = QFormLayout(card)
        form.setSpacing(13)
        parent = self.parentWidget()
        default_output = getattr(parent, "out_dir", "")
        self.output_directory = QLineEdit(str(self.prefs.value("output_dir", default_output) or default_output))
        line = QHBoxLayout()
        line.addWidget(self.output_directory, 1)
        line.addWidget(_button("Chọn", self.choose_output))
        line.addWidget(_button("Mở", lambda: _open_folder(self.output_directory.text()) if self.output_directory.text().strip() else None))
        form.addRow("Thư mục xuất", line)
        self.interface_language = QComboBox()
        self.interface_language.addItem("Tiếng Việt", "vi")
        self.interface_language.addItem("English", "en")
        self.interface_language.setCurrentIndex(max(0, self.interface_language.findData(self.prefs.value("ui_language", "vi"))))
        form.addRow("Ngôn ngữ", self.interface_language)
        self.storage_label = _label(str(self.resources_root) + "\n" + _size_text(self.resources_root), muted=True)
        form.addRow("Dữ liệu mô hình", self.storage_label)
        actions = QHBoxLayout()
        actions.addWidget(_button("Mở thư mục mô hình", lambda: _open_folder(self.resources_root)))
        actions.addWidget(_button("Quản lý tải xuống", self.open_downloads))
        actions.addStretch()
        form.addRow(actions)
        self.cache_locations = [providers.app_data_root() / "thumbnails", voice_catalog_root() / "previews"]
        self.cache_size_label = _label(self.cache_summary(), muted=True)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.cache_size_label, 1)
        cache_row.addWidget(_button("Dọn bộ nhớ tạm", self.clear_cache))
        form.addRow("Bộ nhớ tạm", cache_row)
        layout.addWidget(card)
        self.model_status = _label("Các thiết lập được lưu trên máy của bạn.", muted=True)
        layout.addWidget(self.model_status)
        layout.addStretch()
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(_button("Lưu cài đặt", self.save_common, primary=True))
        layout.addLayout(bottom)
        return _scroll(body)

    def cache_summary(self):
        return " · ".join(_size_text(folder) for folder in self.cache_locations) + " (ảnh xem trước / âm thanh nghe thử)"

    def clear_cache(self):
        parent = self.parentWidget()
        if parent and getattr(parent, "workers", None):
            QMessageBox.information(self, "Đang xử lý", "Chờ các tác vụ dừng trước khi dọn bộ nhớ tạm.")
            return
        if QMessageBox.question(self, "Dọn bộ nhớ tạm", "Xóa ảnh xem trước và âm thanh nghe thử của ứng dụng? Ảnh xem trước sẽ được tạo lại khi mở video.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            for folder in self.cache_locations:
                if folder.exists():
                    remove_own_cache(providers.app_data_root(), folder)
            self.cache_size_label.setText(self.cache_summary())
            self.model_status.setText("Đã dọn bộ nhớ tạm.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Chưa dọn được bộ nhớ tạm", str(exc))

    def open_downloads(self):
        parent = self.parentWidget()
        DownloadsDialog(parent, self.provider_store, self.scenario_store, self.prefs).exec()
        self.storage_label.setText(str(self.resources_root) + "\n" + _size_text(self.resources_root))

    def save_common(self):
        path = self.output_directory.text().strip()
        if not path:
            QMessageBox.warning(self, "Thiếu thư mục", "Chọn thư mục xuất video.")
            return False
        try:
            destination = Path(path).expanduser().resolve()
            destination.mkdir(parents=True, exist_ok=True)
            if not destination.is_dir():
                raise ValueError("Đường dẫn không phải thư mục.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Không lưu được thư mục", str(exc))
            return False
        self.output_directory.setText(str(destination))
        self.prefs.setValue("output_dir", str(destination))
        self.prefs.setValue("output", str(destination))
        self.prefs.setValue("ui_language", self.interface_language.currentData())
        self.prefs.sync()
        self.model_status.setText("Đã lưu cài đặt.")
        self.settings_changed.emit()
        return True


class DownloadsDialog(BaseSettingsDialog):
    """Real model readiness, official downloads, cancellation and own-cache removal."""
    def __init__(self, parent, provider_store, scenario_store, prefs):
        # Reuse this application's resource page and resource lifecycle only.
        super().__init__(parent, provider_store, scenario_store, prefs, resources_root=model_root())
        self.setWindowTitle("Tải Xuống · Video Editor Independent")
        self.setMinimumSize(740, 540)
        self.resize(820, 620)
        _reference_modal(self)
        self.setStyleSheet(THEME)
        self.tabs.removeTab(0)
        self.tabs.removeTab(0)
        self.tabs.setTabText(0, "Thành phần & mô hình")
        self.tabs.tabBar().hide()
        titles = self.findChildren(QLabel, "dialogTitle")
        if titles:
            titles[0].setText("Tải Xuống")
        for scroll in self.findChildren(QScrollArea):
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for frame in self.findChildren(QFrame, "resourceCard"):
            frame.setMinimumHeight(210)
        self.settings_changed.connect(getattr(parent, "_apply_dialog_settings", lambda: None))

    def build_keys(self):
        return QWidget()

    def build_accounts(self):
        return QWidget()

    def build_common(self):
        page = super().build_common()
        body = page.widget()
        layout = body.layout()
        # The first card is general preferences. Downloads needs model cards.
        item = layout.takeAt(0)
        if item and item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        return page


class GPUPerformanceDialog(QDialog):
    """Controls for this editor's real local speech and separation engines."""
    def __init__(self, parent):
        super().__init__(parent)
        import gpu_runtime
        self.setObjectName("gpuPerformanceDialog")
        self.setWindowTitle("Hiệu năng · Video Editor Independent")
        self.resize(620, 440)
        _reference_modal(self)
        self.setStyleSheet(THEME)
        self.worker = None
        self._closing = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        outer.addWidget(_label("Hiệu năng", title=True))
        outer.addWidget(_label("Thiết bị dùng cho hai giọng ngoại tuyến và tách giọng trên máy.", muted=True))
        form = QFormLayout()
        self.device = QComboBox()
        self.device.setObjectName("gpuInferenceDevice")
        for text, value in (("Tự động", "auto"), ("GPU (NVIDIA)", "cuda"), ("CPU", "cpu")):
            self.device.addItem(text, value)
        control = getattr(parent, "tts_device", None)
        if control is not None:
            self.device.setCurrentIndex(max(0, self.device.findData(control.currentData())))
            self.device.currentIndexChanged.connect(lambda: control.setCurrentIndex(max(0, control.findData(self.device.currentData()))))
        self.device.setEnabled(getattr(parent, "active_job", None) is None)
        form.addRow("Thiết bị xử lý", self.device)
        outer.addLayout(form)
        pack = gpu_runtime.discover_demucs_pack()
        self.pack_status = _label("Tách giọng GPU: " + ("Đã cài" if pack else "Chưa cài"))
        self.pack_status.setObjectName("gpuPackStatus")
        outer.addWidget(self.pack_status)
        for provider in local_tts.SUPPORTED_IDS:
            availability = local_tts.availability(provider)
            outer.addWidget(_label(_voice_engine_label(provider) + ": "
                                   + ("Đã cài" if availability["ready"] else availability.get("hint", "Chưa cài"))))
        self.hardware_status = _label("Bấm Kiểm tra GPU để xác minh thiết bị thực tế.", muted=True)
        self.hardware_status.setObjectName("gpuHardwareStatus")
        outer.addWidget(self.hardware_status)
        actions = QHBoxLayout()
        self.check_button = _button("Kiểm tra GPU", self.check_hardware)
        self.check_button.setObjectName("checkGpuHardware")
        actions.addWidget(self.check_button)
        encoder_button = _button("Thiết bị xuất video…", lambda: parent.detect_hardware())
        encoder_button.setEnabled(getattr(parent, "active_job", None) is None)
        actions.addWidget(encoder_button)
        outer.addLayout(actions)
        outer.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(_button("Đóng", self.close, primary=True))
        outer.addLayout(footer)

    def check_hardware(self):
        if self.worker is not None:
            return
        import gpu_runtime
        self.check_button.setEnabled(False)
        self.hardware_status.setText("Đang kiểm tra NVIDIA và runtime tách giọng…")
        self.worker = TaskThread(lambda progress, cancel: gpu_runtime.inspect_demucs_hardware(progress, cancel), self)
        self.worker.result.connect(self._checked)
        self.worker.failed.connect(lambda message: self.hardware_status.setText(message))
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _checked(self, result):
        if self._closing:
            return
        if result.get("cuda_available") or result.get("cuda"):
            self.hardware_status.setText("GPU sẵn sàng: " + str(result.get("device", "NVIDIA")))
        else:
            self.hardware_status.setText(str(result.get("reason", result.get("error", "Runtime chưa xác nhận CUDA; chọn CPU hoặc kiểm tra gói GPU."))))

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        self.check_button.setEnabled(True)
        if self._closing:
            self.close()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self._closing = True
            self.worker.cancel_event.set()
            self.hardware_status.setText("Đang dừng kiểm tra…")
            event.ignore()
            return
        super().closeEvent(event)


class ScenariosDialog(BaseScenariosDialog):
    pronunciation_selected = Signal(str)

    def __init__(self, parent, store):
        super().__init__(parent, store)
        self.setWindowTitle("Kịch Bản Dịch · Video Editor Independent")
        self.setMinimumSize(680, 500)
        self.resize(720, 560)
        _reference_modal(self)
        self.setStyleSheet(THEME)
        for index, title in enumerate(("Dịch mặc định", "Dịch của tôi", "Sửa mặc định", "Sửa của tôi", "Cách đọc")):
            self.tabs.setTabText(index, title)
        for scroll in self.findChildren(QScrollArea):
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def refresh(self):
        super().refresh()
        for frame in self.findChildren(QFrame, "scenarioCard"):
            frame.setMinimumSize(180, 128)
            for title in frame.findChildren(QLabel, "cardTitle"):
                title.setStyleSheet("font-size:13px;font-weight:600;")
                title.setText(title.text().replace("★ ★ ", "★ "))
        for index, dictionary in enumerate(self.store.list_pronunciation()):
            item = self.dictionary_layout.itemAt(index)
            if item and item.widget():
                item.widget().layout().addWidget(_button("Chọn dùng", lambda checked=False, identity=dictionary["id"]: self.use_pronunciation(identity)))

    def use_pronunciation(self, identity):
        self.pronunciation_selected.emit(identity)
        self.accept()

    def edit_template(self, template):
        dialog = TemplateEditorDialog(self, self.store, template)
        dialog.setStyleSheet(THEME)
        dialog.resize(680, 520)
        dialog.selected.connect(self.template_selected.emit)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.store_changed.emit()

    def new_template(self, kind):
        self.edit_template(dict(id="", name="Kịch bản mới", prompt="", builtin=False, kind=kind))

    def edit_dictionary(self, dictionary):
        dialog = PronunciationEditorDialog(self, self.store, dictionary)
        dialog.setStyleSheet(THEME)
        dialog.resize(700, 520)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.store_changed.emit()


def split_by_parts(source, parts, folder, fast, progress, cancel):
    """Create exactly N equal time intervals; fast mode cuts near keyframes."""
    engine._check_cancel(cancel)
    info = engine.probe(source)
    count = int(parts)
    if not 2 <= count <= 99 or info["duration"] <= 0 or info["width"] <= 0:
        raise ValueError("Chọn video hợp lệ và số phần từ 2 đến 99.")
    if info["duration"] / count < 0.05:
        raise ValueError("Video quá ngắn cho số phần đã chọn.")
    destination = Path(folder).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    prefix = Path(source).stem + "_chia_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    published = []
    try:
        with tempfile.TemporaryDirectory(prefix=".independent-split-", dir=destination) as temporary:
            for index in range(count):
                engine._check_cancel(cancel)
                start = info["duration"] * index / count
                length = info["duration"] * (index + 1) / count - start
                staged = Path(temporary) / f"part_{index + 1:03d}.mp4"
                args = ["-ss", f"{start:.9f}", "-i", info["path"], "-t", f"{length:.9f}", "-map", "0:v:0", "-map", "0:a:0?"]
                if fast:
                    args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
                else:
                    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
                args += ["-movflags", "+faststart", str(staged)]
                engine._ffmpeg(args, length,
                               lambda value, message, i=index: progress(min(99, int((i + value / 100) * 100 / count)), f"Chia phần {i + 1}/{count}"),
                               cancel, "Chia video…")
                engine._check_cancel(cancel)
                if not staged.is_file() or staged.stat().st_size == 0:
                    raise RuntimeError(f"Không tạo được phần {index + 1}.")
                target = destination / f"{prefix}_{index + 1:03d}.mp4"
                engine._publish(staged, target)
                published.append(target)
    except BaseException:
        for output in published:
            output.unlink(missing_ok=True)
        raise
    progress(100, f"Đã tạo {len(published)} phần.")
    return [str(path) for path in published]


def extract_mp3(source, output, quality, progress, cancel):
    engine._check_cancel(cancel)
    info = engine.probe(source)
    if not info["has_audio"]:
        raise ValueError("Video nguồn không có âm thanh.")
    target = engine._destination(output)
    with tempfile.TemporaryDirectory(prefix=".independent-mp3-", dir=target.parent) as temporary:
        staged = Path(temporary) / "audio.mp3"
        engine._ffmpeg(["-i", info["path"], "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", str(int(quality)), str(staged)],
                       info["duration"], progress, cancel, "Tách MP3…")
        engine._check_cancel(cancel)
        engine._publish(staged, target)
    progress(100, "Đã tách MP3.")
    return str(target)


class MiniToolsDialog(QDialog):
    """Split/join/extract tools; all action buttons connect to actual processing."""
    VIDEO_FILTER = "Video (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.ts);;Tất cả (*)"

    def __init__(self, parent):
        super().__init__(parent)
        self.owner = parent
        self.worker = None
        self._closing = False
        self.result_files = []
        self.setWindowTitle("Công Cụ Cắt Ghép · Video Editor Independent")
        self.setStyleSheet(THEME)
        self.resize(760, 600)
        _reference_modal(self)
        self.setMinimumSize(680, 490)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.addWidget(_label("Công Cụ Cắt Ghép", title=True))
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self.build_split(), "Chia video")
        self.tabs.addTab(self.build_join(), "Ghép video")
        self.tabs.addTab(self.build_mp3(), "Tách MP3")
        self.status = _label("Sẵn sàng", muted=True)
        outer.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        outer.addWidget(self.progress)
        actions = QHBoxLayout()
        self.open_result_button = _button("Mở thư mục", self.open_results)
        self.open_result_button.setEnabled(False)
        self.import_button = _button("Nhập vào kho", self.import_results)
        self.import_button.setEnabled(False)
        self.cancel_button = _button("Dừng", self.cancel_task)
        self.cancel_button.setEnabled(False)
        actions.addWidget(self.open_result_button)
        actions.addWidget(self.import_button)
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(_button("Đóng", self.reject))
        outer.addLayout(actions)
        source = getattr(parent, "current_media", "") or ""
        if source and Path(source).is_file():
            self.set_source(self.split_source, source)
            self.set_source(self.mp3_source, source)

    def source_row(self, edit):
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(_button("Chọn video", lambda: self.choose_source(edit)))
        return row

    def directory_row(self, edit):
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(_button("Chọn", lambda: self.choose_directory(edit)))
        return row

    def output_row(self, edit, suffix):
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(_button("Lưu tại…", lambda: self.choose_output(edit, suffix)))
        return row

    def build_split(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 12)
        layout.addWidget(_label("Chia video thành các phần bằng nhau", title=True))
        form = QFormLayout()
        form.setSpacing(16)
        self.split_source = QLineEdit()
        self.split_source.setPlaceholderText("Chọn video nguồn")
        form.addRow("Video nguồn", self.source_row(self.split_source))
        self.duration = _label("", muted=True)
        form.addRow("Thời lượng", self.duration)
        self.parts = QSpinBox()
        self.parts.setRange(2, 99)
        self.parts.setValue(2)
        form.addRow("Số phần", self.parts)
        self.split_directory = QLineEdit(getattr(self.owner, "out_dir", ""))
        form.addRow("Thư mục xuất", self.directory_row(self.split_directory))
        self.fast_split = QCheckBox("Chia nhanh (giữ nguyên codec)")
        form.addRow("Chế độ", self.fast_split)
        form.addRow(_label("Chia nhanh cắt gần khung hình khóa, độ dài mỗi phần có thể lệch. Tắt để chia chính xác theo thời gian.", muted=True))
        layout.addLayout(form)
        layout.addStretch()
        self.split_button = _button("Chia video", self.start_split, primary=True)
        layout.addWidget(self.split_button, 0, Qt.AlignmentFlag.AlignRight)
        return page

    def build_join(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 12)
        heading = QHBoxLayout()
        heading.addWidget(_label("Ghép video", title=True), 1)
        heading.addWidget(_button("Thêm video", self.add_join_files, primary=True))
        heading.addWidget(_button("Xóa hết", self.clear_join_files))
        layout.addLayout(heading)
        self.join_list = QListWidget()
        self.join_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.join_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.join_list.setToolTip("Kéo video để đổi thứ tự ghép.")
        layout.addWidget(self.join_list, 1)
        reorder = QHBoxLayout()
        reorder.addWidget(_button("↑ Lên", lambda: self.move_join(-1)))
        reorder.addWidget(_button("↓ Xuống", lambda: self.move_join(1)))
        reorder.addWidget(_button("Gỡ video", self.remove_join_files))
        reorder.addStretch()
        layout.addLayout(reorder)
        self.join_output = QLineEdit()
        form = QFormLayout()
        form.addRow("Tệp xuất", self.output_row(self.join_output, ".mp4"))
        layout.addLayout(form)
        layout.addWidget(_label("Các video được ghép theo thứ tự trong danh sách và đồng bộ với khung hình của video đầu tiên.", muted=True))
        self.join_button = _button("Ghép video", self.start_join, primary=True)
        layout.addWidget(self.join_button, 0, Qt.AlignmentFlag.AlignRight)
        return page

    def build_mp3(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 12)
        layout.addWidget(_label("Tách âm thanh MP3", title=True))
        form = QFormLayout()
        form.setSpacing(16)
        self.mp3_source = QLineEdit()
        self.mp3_source.setPlaceholderText("Chọn video có âm thanh")
        form.addRow("Video nguồn", self.source_row(self.mp3_source))
        self.mp3_output = QLineEdit()
        form.addRow("Tệp xuất", self.output_row(self.mp3_output, ".mp3"))
        self.mp3_quality = QSpinBox()
        self.mp3_quality.setRange(0, 9)
        self.mp3_quality.setValue(0)
        form.addRow("Chất lượng", self.mp3_quality)
        form.addRow(_label("0 = chất lượng cao nhất; 9 = tệp nhỏ nhất. MP3 sử dụng bitrate biến đổi.", muted=True))
        layout.addLayout(form)
        layout.addStretch()
        self.mp3_button = _button("Tách MP3", self.start_mp3, primary=True)
        layout.addWidget(self.mp3_button, 0, Qt.AlignmentFlag.AlignRight)
        return page

    def choose_source(self, edit):
        path = QFileDialog.getOpenFileName(self, "Chọn video", edit.text() or getattr(self.owner, "out_dir", ""), self.VIDEO_FILTER)[0]
        if path:
            self.set_source(edit, path)

    def set_source(self, edit, path):
        edit.setText(path)
        if edit is self.split_source:
            try:
                info = engine.probe(path)
                seconds = info["duration"]
                self.duration.setText(f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}:{int(seconds % 60):02d} · {info['width']} × {info['height']}")
            except Exception as exc:
                self.duration.setText(str(exc))
        if edit is self.mp3_source and not self.mp3_output.text().strip():
            self.mp3_output.setText(str(Path(getattr(self.owner, "out_dir", "") or Path(path).parent) / (Path(path).stem + ".mp3")))

    def choose_directory(self, edit):
        path = QFileDialog.getExistingDirectory(self, "Thư mục xuất", edit.text())
        if path:
            edit.setText(path)

    def choose_output(self, edit, suffix):
        path = QFileDialog.getSaveFileName(self, "Tệp xuất", edit.text() or getattr(self.owner, "out_dir", ""), f"{'MP3' if suffix == '.mp3' else 'Video MP4'} (*{suffix})")[0]
        if path:
            if Path(path).suffix.casefold() != suffix:
                path += suffix
            edit.setText(path)

    def add_join_files(self):
        paths = QFileDialog.getOpenFileNames(self, "Thêm video để ghép", getattr(self.owner, "out_dir", ""), self.VIDEO_FILTER)[0]
        self.add_join_paths(paths)

    def add_join_paths(self, paths):
        for path in paths:
            item = QListWidgetItem(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.join_list.addItem(item)
        if self.join_list.count() and not self.join_output.text().strip():
            first = Path(self.join_list.item(0).data(Qt.ItemDataRole.UserRole))
            folder = Path(getattr(self.owner, "out_dir", "") or first.parent)
            self.join_output.setText(str(folder / (first.stem + "_ghép.mp4")))

    def clear_join_files(self):
        self.join_list.clear()
        self.join_output.clear()

    def remove_join_files(self):
        for item in self.join_list.selectedItems():
            self.join_list.takeItem(self.join_list.row(item))

    def move_join(self, delta):
        selected = sorted([self.join_list.row(item) for item in self.join_list.selectedItems()], reverse=delta > 0)
        for row in selected:
            destination = row + delta
            if 0 <= destination < self.join_list.count():
                item = self.join_list.takeItem(row)
                self.join_list.insertItem(destination, item)
                item.setSelected(True)

    def valid_source(self, edit):
        path = edit.text().strip()
        if not path or not Path(path).is_file():
            QMessageBox.warning(self, "Thiếu video", "Chọn tệp video nguồn có trên máy.")
            return None
        return str(Path(path).resolve())

    def valid_output(self, edit, suffix):
        path = edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Thiếu tệp xuất", "Chọn nơi lưu tệp xuất.")
            return None
        if Path(path).suffix.casefold() != suffix:
            path += suffix
            edit.setText(path)
        path = str(Path(path).expanduser().resolve())
        if Path(path).exists():
            QMessageBox.warning(self, "Tệp đã tồn tại", "Chọn tên mới để giữ tệp đã có.")
            return None
        return path

    def start_split(self):
        source = self.valid_source(self.split_source)
        folder = self.split_directory.text().strip()
        if not source:
            return
        if not folder:
            folder = str(Path(source).parent)
        count, fast = self.parts.value(), self.fast_split.isChecked()
        self.start_task("Chia video", lambda p, c: split_by_parts(source, count, folder, fast, p, c), importable=True)

    def start_join(self):
        paths = [self.join_list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.join_list.count())]
        if len(paths) < 2:
            QMessageBox.warning(self, "Thiếu video", "Thêm ít nhất hai video vào danh sách ghép.")
            return
        output = self.valid_output(self.join_output, ".mp4")
        if not output:
            return
        def run(progress, cancel):
            clips = []
            for path in paths:
                engine._check_cancel(cancel)
                info = engine.probe(path)
                clips.append(dict(path=info["path"], start=0, end=info["duration"]))
            return engine.media_process(clips, {}, output, progress, cancel)
        self.start_task("Ghép video", run, importable=True)

    def start_mp3(self):
        source = self.valid_source(self.mp3_source)
        output = self.valid_output(self.mp3_output, ".mp3") if source else None
        if not source or not output:
            return
        quality = self.mp3_quality.value()
        self.start_task("Tách MP3", lambda p, c: extract_mp3(source, output, quality, p, c), importable=False)

    def start_task(self, title, function, *, importable):
        if self.worker or getattr(self.owner, "active_job", None):
            QMessageBox.information(self, "Đang xử lý", "Chờ tác vụ hiện tại hoàn thành hoặc dừng trước khi chạy tác vụ khác.")
            return
        self.result_files = []
        self.open_result_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText(title + "…")
        self.tabs.setEnabled(False)
        self.cancel_button.setEnabled(True)
        worker = TaskThread(function, self)
        self.worker = worker
        self.owner.active_job = worker
        if hasattr(self.owner, "workers"):
            self.owner.workers.add(worker)
        job_item = QListWidgetItem(title + " · Đang xử lý")
        if hasattr(self.owner, "job_list"):
            self.owner.job_list.insertItem(0, job_item)
        if hasattr(self.owner, "cancel_button"):
            self.owner.cancel_button.setEnabled(True)
        if hasattr(self.owner, "export_button"):
            self.owner.export_button.setEnabled(False)
        def update(percent, message):
            self.progress.setValue(percent)
            self.status.setText(f"{percent}% · {message}")
            if hasattr(self.owner, "progress"):
                self.owner.progress.setValue(percent)
            self.owner.statusBar().showMessage(title + " · " + message)
        def completed(result):
            self.result_files = list(result) if isinstance(result, (tuple, list)) else [str(result)]
            self.progress.setValue(100)
            self.status.setText(f"Hoàn tất · {len(self.result_files)} tệp")
            self.open_result_button.setEnabled(True)
            self.import_button.setEnabled(importable)
            job_item.setText(title + " · Hoàn thành")
        def failed(message):
            self.status.setText(message)
            job_item.setText(title + " · " + message)
        def finished():
            self.tabs.setEnabled(True)
            self.cancel_button.setEnabled(False)
            if getattr(self.owner, "active_job", None) is worker:
                self.owner.active_job = None
            if hasattr(self.owner, "workers"):
                self.owner.workers.discard(worker)
            if hasattr(self.owner, "cancel_button"):
                self.owner.cancel_button.setEnabled(False)
            if hasattr(self.owner, "export_button"):
                self.owner.export_button.setEnabled(True)
            self.worker = None
            worker.deleteLater()
            if self._closing:
                super(MiniToolsDialog, self).reject()
        worker.progress.connect(update)
        worker.result.connect(completed)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def cancel_task(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("Đang dừng…")

    def open_results(self):
        if self.result_files:
            _open_folder(Path(self.result_files[0]).parent)

    def import_results(self):
        if self.result_files:
            self.owner.ingest(self.result_files)
            self.status.setText(f"Đã nhập {len(self.result_files)} video vào kho.")
            self.import_button.setEnabled(False)

    def reject(self):
        if self.worker:
            self._closing = True
            self.cancel_task()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self._closing = True
            self.cancel_task()
            event.ignore()
            return
        super().closeEvent(event)


class DialogMixin:
    """Place before StudioWindow in the desktop shell's method resolution order."""
    def _dialog_stores(self):
        if not hasattr(self, "provider_store"):
            self.provider_store = providers.ProviderStore()
        if not hasattr(self, "scenario_store"):
            self.scenario_store = ScenarioStore()
        if not hasattr(self, "tts_key_store"):
            self.tts_key_store = tts_providers.TTSKeyStore()
        if not hasattr(self, "translation_config"):
            try:
                saved = self.prefs.value("translation_config", "{}")
                data = json.loads(saved) if isinstance(saved, str) else saved
                self.translation_config = dict(data) if isinstance(data, dict) else {}
            except (ValueError, TypeError):
                self.translation_config = {}
        self.translation_config.setdefault("provider", "google")
        self.translation_config.setdefault("batch_size", 40)
        self.translation_config.setdefault("translate_workers", 1)
        self.translation_config.setdefault("image_context", False)
        if not getattr(self, "_voice_prefs_loaded", False):
            self._voice_prefs_loaded = True
            voice = str(self.prefs.value("tts_voice", "") or "")
            if voice:
                for attribute in ("pipe_voice", "voice"):
                    combo = getattr(self, attribute, None)
                    if isinstance(combo, QComboBox):
                        index = combo.findText(voice)
                        if index < 0:
                            combo.addItem(voice)
                            index = combo.findText(voice)
                        combo.setCurrentIndex(index)
        return self.provider_store, self.scenario_store

    def _apply_dialog_settings(self):
        folder = str(self.prefs.value("output_dir", self.out_dir) or self.out_dir)
        self.out_dir = folder
        for attribute in ("pipe_folder", "export_folder", "download_dir", "output_directory"):
            edit = getattr(self, attribute, None)
            if isinstance(edit, QLineEdit):
                edit.setText(folder)
        model = self.prefs.value("whisper_model", "base")
        for attribute in ("pipe_model", "rec_model"):
            combo = getattr(self, attribute, None)
            if isinstance(combo, QComboBox):
                index = combo.findData(model)
                if index < 0:
                    index = combo.findText(str(model))
                if index >= 0:
                    combo.setCurrentIndex(index)
        language = str(self.prefs.value("ui_language", "vi"))
        setter = getattr(self, "set_ui_language", None) or getattr(self, "set_language", None)
        if callable(setter):
            setter(language)
        self.statusBar().showMessage("Đã cập nhật thiết lập.", 5000)

    def open_settings(self):
        provider_store, scenario_store = self._dialog_stores()
        dialog = SettingsDialog(self, provider_store, scenario_store, self.prefs)
        dialog.settings_changed.connect(self._apply_dialog_settings)
        dialog.exec()

    def open_scenarios(self):
        _provider_store, scenario_store = self._dialog_stores()
        dialog = ScenariosDialog(self, scenario_store)
        dialog.template_selected.connect(self._use_scenario)
        dialog.pronunciation_selected.connect(self._use_pronunciation)
        dialog.store_changed.connect(self._refresh_scenario_controls)
        dialog.exec()

    def open_tools(self):
        MiniToolsDialog(self).exec()

    def open_voice_picker(self):
        self._dialog_stores()
        current = getattr(self, "pipe_voice", None)
        selected = current.currentText() if isinstance(current, QComboBox) else str(self.prefs.value("tts_voice", "vi-VN-HoaiMyNeural"))
        dialog = VoicePickerDialog(self, selected)
        dialog.voice_selected.connect(self._use_voice)
        dialog.exec()

    def open_gpu_performance(self):
        GPUPerformanceDialog(self).exec()

    def _use_voice(self, short_name):
        # Processing expects the raw ID, including paid-provider prefixes.
        for attribute in ("pipe_voice", "voice"):
            combo = getattr(self, attribute, None)
            if isinstance(combo, QComboBox):
                index = combo.findText(short_name)
                if index < 0:
                    combo.addItem(short_name)
                    index = combo.findText(short_name)
                combo.setCurrentIndex(index)
        self.prefs.setValue("tts_voice", short_name)
        self.prefs.sync()
        callback = getattr(self, "refresh_voice_display", None)
        if callable(callback):
            callback()
        self.statusBar().showMessage("Đã chọn giọng: " + short_name, 7000)

    def open_downloads(self):
        provider_store, scenario_store = self._dialog_stores()
        dialog = DownloadsDialog(self, provider_store, scenario_store, self.prefs)
        dialog.settings_changed.connect(self._apply_dialog_settings)
        dialog.exec()

    def _use_scenario(self, identity, kind):
        self._dialog_stores()
        try:
            template = self.scenario_store.get_template(identity, kind)
        except (KeyError, ValueError) as exc:
            self.info("Không chọn được kịch bản", str(exc))
            return
        self.prefs.setValue("scenario_" + kind, identity)
        if kind == "translate":
            self.translation_config["scenario_prompt"] = template["prompt"]
            self.prefs.setValue("translation_config", json.dumps(self.translation_config, ensure_ascii=False))
        else:
            self.rewrite_scenario_prompt = template["prompt"]
        self._refresh_scenario_controls()
        self.statusBar().showMessage("Đã chọn kịch bản: " + template["name"], 8000)

    def _use_pronunciation(self, identity):
        self._dialog_stores()
        self.prefs.setValue("pronunciation_id", identity)
        self._refresh_scenario_controls()
        self.statusBar().showMessage("Đã chọn bảng sửa cách đọc.", 6000)

    def _refresh_scenario_controls(self):
        callback = getattr(self, "refresh_scenarios", None)
        if callable(callback):
            callback()
        for attribute, setting in (("pipe_scenario", "scenario_translate"), ("pipe_pronunciation", "pronunciation_id")):
            combo = getattr(self, attribute, None)
            if isinstance(combo, QComboBox):
                identity = self.prefs.value(setting, "")
                index = combo.findData(identity)
                if index >= 0:
                    combo.setCurrentIndex(index)
