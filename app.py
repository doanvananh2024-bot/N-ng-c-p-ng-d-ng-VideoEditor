"""VideoEditorIndependent Studio: one Windows workflow for downloading and editing media."""
from __future__ import annotations
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, QUrl, QThread, Signal, QStandardPaths, QTimer, QSettings
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QFont, QAction, QPolygonF, QPen
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QStackedWidget, QFileDialog,
    QMessageBox, QFrame, QSplitter, QTabWidget, QFormLayout, QDoubleSpinBox,
    QSpinBox, QComboBox, QCheckBox, QLineEdit, QScrollArea, QSlider,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QAbstractItemView, QInputDialog,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
import engine
import ai_tools

APP_DIR = Path(__file__).resolve().parent
STYLE = """
QWidget {background:#11131c;color:#e9ebf5;font-family:'Segoe UI';font-size:13px;}
QMainWindow {background:#11131c;}
QFrame#sidebar {background:#171a27;border-right:1px solid #2b2e40;}
QFrame#panel {background:#191c2a;border:1px solid #2c3043;border-radius:10px;}
QLabel {background:transparent;}
QLabel#title {font-size:26px;font-weight:700;}
QLabel#muted {color:#929ab6;}
QLabel#brand {font-size:22px;font-weight:700;color:#00b6d6;}
QPushButton {background:#252a3e;border:1px solid #363c55;border-radius:7px;padding:9px 14px;}
QPushButton:hover {background:#333953;border-color:#8e74ec;}
QPushButton:pressed {background:#454263;}
QPushButton:disabled {color:#616b85;background:#1c2030;border-color:#252b3d;}
QPushButton#primary {background:#00afce;border-color:#9474fd;color:white;font-weight:600;}
QPushButton#primary:hover {background:#8d6cf5;}
QPushButton#nav {text-align:left;border:0;background:transparent;padding:13px 16px;}
QPushButton#nav:checked {background:#332b52;color:#cbb8ff;}
QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox,QTextEdit {background:#121622;border:1px solid #343b53;border-radius:5px;padding:7px;selection-background-color:#7654df;}
QListWidget,QTableWidget {background:#151925;border:1px solid #2c334b;border-radius:8px;alternate-background-color:#1b2031;}
QListWidget::item {padding:12px;border-bottom:1px solid #252b3d;}
QListWidget::item:selected {background:#352d55;color:#eee7ff;border-left:3px solid #9b79ff;}
QTabWidget::pane {border:0;}
QTabBar::tab {padding:9px 11px;background:#1b2030;color:#939db8;}
QTabBar::tab:selected {color:#d5c3ff;background:#332b52;}
QProgressBar {background:#222839;border:0;border-radius:4px;text-align:center;height:19px;}
QProgressBar::chunk {background:#805fe8;border-radius:4px;}
QSlider::groove:horizontal {height:4px;background:#2e354d;border-radius:2px;}
QSlider::handle:horizontal {width:12px;margin:-4px 0;background:#a38aff;border-radius:6px;}
QHeaderView::section {background:#22283c;padding:8px;border:0;}
QScrollBar:vertical {width:9px;background:#161a28;}
QScrollBar::handle:vertical {background:#3e4561;border-radius:4px;min-height:25px;}
QStatusBar {color:#a5afc8;background:#171b29;}
QMenu {background:#202539;border:1px solid #424963;}
QMenu::item {padding:7px 24px;}
QMenu::item:selected {background:#66509b;}
"""


def time_label(seconds):
    seconds = max(0, float(seconds))
    return f'{int(seconds // 3600):02}:{int(seconds // 60) % 60:02}:{seconds % 60:06.3f}'


def make_icon():
    pm = QPixmap(128, 128)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#00afce'))
    p.drawRoundedRect(6, 6, 116, 116, 26, 26)
    p.setBrush(QColor('#151929'))
    p.drawRoundedRect(21, 20, 86, 86, 10, 10)
    p.setBrush(QColor('#54b5ff'))
    for y in [30,46,62,78]:
        p.drawRoundedRect(27,y,9,9,2,2); p.drawRoundedRect(92,y,9,9,2,2)
    p.setBrush(QColor('#00b6d6'))
    p.drawPolygon(QPolygonF([QPointF(48,36),QPointF(48,80),QPointF(80,58)]))
    p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor('#f0d7ff'),4))
    p.drawEllipse(QPointF(78,94),7,7); p.drawEllipse(QPointF(95,105),7,7)
    p.drawLine(QPointF(83,89),QPointF(107,68)); p.drawLine(QPointF(90,99),QPointF(105,89))
    p.end()
    return QIcon(pm)


class Worker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    failed = Signal(str)
    def __init__(self, func):
        super().__init__()
        self.func = func
        self.cancel_event = threading.Event()
    def run(self):
        try:
            self.result.emit(self.func(self.progress.emit, self.cancel_event))
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class Window(QMainWindow):
    def __init__(self, testing=False):
        super().__init__()
        self.setWindowTitle('VideoEditorIndependent Studio')
        self.setWindowIcon(make_icon())
        self.resize(1400, 900)
        self.setMinimumSize(1080, 720)
        self.prefs = QSettings('VideoEditorIndependent-QA' if testing else 'VideoEditorIndependent', 'Studio')
        if testing: self.prefs.clear()
        self.library = []
        self.clips = []
        self.workers = set()
        self.active_job = None
        self.current_clip = -1
        self.syncing = False
        self.project_path = None
        self.dirty = False
        self.subtitle_dirty = False
        self.out_dir = str(self.prefs.value('output', str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)) / 'VideoEditorIndependent')))
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.7)
        self.player.setAudioOutput(self.audio)
        self.build()
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda v: self.seek.setMaximum(v))
        self.player.errorOccurred.connect(lambda *_: self.statusBar().showMessage('Không xem được tệp này. Bạn vẫn có thể thử xuất qua FFmpeg.', 8000))
        self.player.playbackStateChanged.connect(lambda s: self.play_button.setText('Tạm dừng' if s == QMediaPlayer.PlaybackState.PlayingState else 'Phát'))
        if not testing: self.restore_library()
        self.statusBar().showMessage('Sẵn sàng · Nhập video để bắt đầu')

    def button(self, text, slot, primary=False):
        b = QPushButton(text)
        if primary: b.setObjectName('primary')
        b.clicked.connect(slot)
        return b

    def heading(self, title, subtitle):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 12)
        a = QLabel(title); a.setObjectName('title'); lay.addWidget(a)
        a = QLabel(subtitle); a.setObjectName('muted'); a.setWordWrap(True); lay.addWidget(a)
        return box

    def page(self, title, subtitle):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(24, 24, 24, 20)
        lay.addWidget(self.heading(title, subtitle))
        self.pages.addWidget(w)
        return lay

    def build(self):
        main = QWidget(); h = QHBoxLayout(main); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        self.setCentralWidget(main)
        side = QFrame(); side.setObjectName('sidebar'); side.setFixedWidth(210)
        nav = QVBoxLayout(side); nav.setContentsMargins(16, 26, 16, 20)
        brand = QLabel('VideoEditorIndependent'); brand.setObjectName('brand'); nav.addWidget(brand)
        tag = QLabel('STUDIO  /  1.0'); tag.setObjectName('muted'); nav.addWidget(tag); nav.addSpacing(32)
        self.nav_buttons = []
        for i, text in enumerate(['Kho video', 'Tải video', 'Chỉnh sửa', 'Phụ đề & giọng đọc', 'Tác vụ', 'Hướng dẫn']):
            b = self.button(text, lambda _=False, ix=i: self.navigate(ix)); b.setObjectName('nav'); b.setCheckable(True)
            self.nav_buttons.append(b); nav.addWidget(b)
        nav.addStretch()
        foot = QLabel('Một kho media\nMột quy trình làm việc'); foot.setObjectName('muted'); nav.addWidget(foot)
        h.addWidget(side)
        self.pages = QStackedWidget(); h.addWidget(self.pages, 1)
        self.build_library(); self.build_download(); self.build_editor(); self.build_subtitles(); self.build_jobs(); self.build_help()
        self.navigate(0)
        menu = self.menuBar().addMenu('Dự án')
        for text, key, func in [('Dự án mới', 'Ctrl+N', self.new_project), ('Mở dự án…', 'Ctrl+O', self.open_project), ('Lưu dự án', 'Ctrl+S', self.save_project), ('Nhập video…', 'Ctrl+I', self.import_files)]:
            a = QAction(text, self); a.setShortcut(key); a.triggered.connect(func); menu.addAction(a)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, b in enumerate(self.nav_buttons): b.setChecked(i == index)

    def build_library(self):
        lay = self.page('Kho video', 'Video tải về và video đã xuất cùng nằm trong một kho. Chọn tệp để đưa vào dự án.')
        bar = QHBoxLayout()
        bar.addWidget(self.button('+ Nhập video', self.import_files, True))
        bar.addWidget(self.button('Đưa vào chỉnh sửa', self.add_selected))
        bar.addWidget(self.button('Mở thư mục', self.open_selected_folder))
        bar.addWidget(self.button('Bỏ khỏi kho', self.remove_library)); bar.addStretch()
        self.search = QLineEdit(); self.search.setPlaceholderText('Tìm tên video…'); self.search.textChanged.connect(self.render_library); bar.addWidget(self.search)
        lay.addLayout(bar)
        self.lib_list = QListWidget(); self.lib_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lib_list.itemDoubleClicked.connect(lambda _: self.add_selected()); lay.addWidget(self.lib_list, 1)
        self.lib_info = QLabel('Chưa có video'); self.lib_info.setObjectName('muted'); lay.addWidget(self.lib_info)

    def build_download(self):
        lay = self.page('Tải video', 'Dán liên kết video được yt-dlp hỗ trợ. Video tải xong sẽ tự chuyển vào kho chung.')
        f = QFormLayout()
        self.url_edit = QLineEdit(); self.url_edit.setPlaceholderText('https://…'); f.addRow('Liên kết video', self.url_edit)
        self.download_dir = QLineEdit(self.out_dir); f.addRow('Thư mục lưu', self.file_row(self.download_dir, folder=True))
        lay.addLayout(f)
        b = self.button('Tải vào kho video', self.download, True); lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        hint = QLabel('Mỗi lần tải một video. Liên kết cần đăng nhập hoặc bị chặn có thể không tải được.'); hint.setObjectName('muted'); hint.setWordWrap(True); lay.addWidget(hint)
        lay.addStretch()

    def build_editor(self):
        lay = self.page('Chỉnh sửa video', 'Cắt từng đoạn, sắp xếp thứ tự, thêm nhạc và phụ đề rồi xuất thành một video.')
        bar = QHBoxLayout()
        bar.addWidget(self.button('+ Thêm video', self.import_to_editor))
        bar.addWidget(self.button('Lưu dự án', self.save_project)); bar.addStretch()
        bar.addWidget(self.button('Tách MP3', self.extract_audio)); bar.addWidget(self.button('Chia nhiều phần', self.split_selected))
        self.export_button = self.button('Xuất video', self.export, True); bar.addWidget(self.export_button); lay.addLayout(bar)
        split = QSplitter()
        center = QWidget(); col = QVBoxLayout(center); col.setContentsMargins(0, 0, 10, 0)
        preview_tabs = QTabWidget()
        preview = QWidget(); pvl = QVBoxLayout(preview); pvl.setContentsMargins(0, 0, 0, 0)
        self.video = QVideoWidget(); self.video.setMinimumHeight(230); self.player.setVideoOutput(self.video); pvl.addWidget(self.video, 1)
        transport = QHBoxLayout()
        self.play_button = self.button('Phát', self.toggle_play); transport.addWidget(self.play_button)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.sliderMoved.connect(self.player.setPosition); transport.addWidget(self.seek, 1)
        self.time = QLabel('00:00:00.000'); transport.addWidget(self.time); pvl.addLayout(transport)
        preview_tabs.addTab(preview, 'Video nguồn')
        self.output_video = QVideoWidget(); self.output_player = QMediaPlayer(self); self.output_audio = QAudioOutput(self); self.output_player.setAudioOutput(self.output_audio); self.output_player.setVideoOutput(self.output_video)
        outw = QWidget(); ov = QVBoxLayout(outw); ov.addWidget(self.output_video, 1)
        outbar = QHBoxLayout(); outbar.addWidget(self.button('Phát / Tạm dừng', lambda: self.output_player.pause() if self.output_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState else self.output_player.play())); outbar.addWidget(self.button('Xem từ đầu', lambda: (self.output_player.setPosition(0), self.output_player.play()))); ov.addLayout(outbar)
        self.output_hint = QLabel('Xuất video để xem kết quả có đầy đủ hiệu ứng.'); self.output_hint.setObjectName('muted'); self.output_hint.setWordWrap(True); ov.addWidget(self.output_hint)
        preview_tabs.addTab(outw, 'Kết quả xuất'); self.preview_tabs = preview_tabs; col.addWidget(preview_tabs, 2)
        col.addWidget(QLabel('CÁC ĐOẠN VIDEO  ·  ghép theo thứ tự bên dưới'))
        self.clip_list = QListWidget(); self.clip_list.setMinimumHeight(130); self.clip_list.currentRowChanged.connect(self.select_clip); col.addWidget(self.clip_list, 1)
        tools = QHBoxLayout()
        tools.addWidget(self.button('Lên', lambda: self.move_clip(-1))); tools.addWidget(self.button('Xuống', lambda: self.move_clip(1))); tools.addWidget(self.button('Bỏ đoạn', self.remove_clip)); tools.addStretch(); col.addLayout(tools)
        trim = QHBoxLayout()
        self.trim_start = self.number(0, 0, 864000, 3)
        self.trim_end = self.number(0, 0, 864000, 3)
        trim.addWidget(QLabel('Bắt đầu (giây)')); trim.addWidget(self.trim_start); trim.addWidget(self.button('Lấy vị trí', lambda: self.trim_start.setValue(self.player.position()/1000)))
        trim.addWidget(QLabel('Kết thúc (giây)')); trim.addWidget(self.trim_end); trim.addWidget(self.button('Lấy vị trí', lambda: self.trim_end.setValue(self.player.position()/1000))); col.addLayout(trim)
        self.trim_start.valueChanged.connect(self.trim_changed); self.trim_end.valueChanged.connect(self.trim_changed)
        split.addWidget(center)
        options = QWidget(); options.setMinimumWidth(310); right = QVBoxLayout(options); right.setContentsMargins(8, 0, 0, 0)
        tabs = QTabWidget(); right.addWidget(tabs, 1)
        self.controls = {}
        image_form = self.option_form(tabs, 'Hình ảnh')
        self.resolution = self.combo(['Theo video đầu', '1920 × 1080', '1080 × 1920', '1280 × 720', '720 × 1280', '1080 × 1080', '854 × 480'])
        image_form.addRow('Khung hình', self.resolution)
        self.fit = self.combo(['Thêm viền', 'Cắt đầy khung']); image_form.addRow('Cách vừa khung', self.fit)
        self.fps = QSpinBox(); self.fps.setRange(15, 60); self.fps.setValue(30); image_form.addRow('Khung/giây', self.fps)
        self.add_number(image_form, 'speed', 'Tốc độ', 1, .25, 4)
        self.rotation = self.combo(['0°', '90°', '180°', '270°']); image_form.addRow('Xoay', self.rotation)
        self.mirror = QCheckBox('Lật ngang'); image_form.addRow(self.mirror)
        self.add_number(image_form, 'brightness', 'Độ sáng', 0, -1, 1)
        self.add_number(image_form, 'contrast', 'Tương phản', 1, .1, 3)
        self.add_number(image_form, 'saturation', 'Độ bão hòa', 1, 0, 3)
        image_form.addRow(self.button('Đặt lại hiệu chỉnh', self.reset_adjustments))
        audio_form = self.option_form(tabs, 'Âm thanh')
        self.mute = QCheckBox('Tắt tiếng video gốc'); audio_form.addRow(self.mute)
        self.add_number(audio_form, 'volume', 'Âm lượng gốc', 1, 0, 2)
        self.music = QLineEdit(); audio_form.addRow('Nhạc / giọng đọc', self.file_row(self.music, filter='Âm thanh (*.mp3 *.wav *.m4a *.aac *.ogg);;Tất cả (*)'))
        self.add_number(audio_form, 'music_volume', 'Âm lượng thêm', .35, 0, 2)
        self.music_loop = QCheckBox('Lặp nhạc nếu ngắn hơn video'); self.music_loop.setChecked(True); audio_form.addRow(self.music_loop)
        hint = QLabel('Tắt lặp khi dùng giọng đọc để lời thoại chỉ phát một lần.'); hint.setWordWrap(True); hint.setObjectName('muted'); audio_form.addRow(hint)
        overlay = self.option_form(tabs, 'Lớp phủ')
        self.subtitle = QLineEdit(); overlay.addRow('Phụ đề SRT', self.file_row(self.subtitle, filter='Phụ đề (*.srt)'))
        self.sub_size = QSpinBox(); self.sub_size.setRange(8, 96); self.sub_size.setValue(24); overlay.addRow('Cỡ chữ', self.sub_size)
        self.logo = QLineEdit(); overlay.addRow('Logo / ảnh', self.file_row(self.logo, filter='Ảnh (*.png *.jpg *.jpeg *.webp)'))
        self.logo_pos = self.combo(['Trên phải', 'Trên trái', 'Dưới phải', 'Dưới trái']); overlay.addRow('Vị trí logo', self.logo_pos)
        self.logo_width = QSpinBox(); self.logo_width.setRange(16, 1000); self.logo_width.setValue(160); overlay.addRow('Chiều rộng logo', self.logo_width)
        self.blur_on = QCheckBox('Làm mờ vùng hình'); overlay.addRow(self.blur_on)
        self.blur_coords = []
        for text in ['X', 'Y', 'Rộng', 'Cao']:
            n = QSpinBox(); n.setRange(0, 8000); n.setValue(100 if text in ['Rộng', 'Cao'] else 0); self.blur_coords.append(n); overlay.addRow(text + ' (pixel)', n)
        hint = QLabel('Vùng mờ dùng tọa độ trên khung video đầu ra.'); hint.setWordWrap(True); hint.setObjectName('muted'); overlay.addRow(hint)
        split.addWidget(options); split.setStretchFactor(0, 3); split.setStretchFactor(1, 1); lay.addWidget(split, 1)
        for c in list(self.controls.values()) + [self.trim_start, self.trim_end, self.resolution, self.fit, self.fps, self.rotation, self.mirror, self.mute, self.music, self.music_loop, self.subtitle, self.sub_size, self.logo, self.logo_pos, self.logo_width, self.blur_on] + self.blur_coords:
            for sig in ['valueChanged', 'currentIndexChanged', 'toggled', 'textChanged']:
                if hasattr(c, sig): getattr(c, sig).connect(self.mark_dirty); break

    def option_form(self, tabs, title):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(); f = QFormLayout(body); f.setContentsMargins(12, 16, 12, 16); f.setSpacing(12); f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(body); tabs.addTab(scroll, title); return f

    def number(self, value, low, high, decimals=2):
        n = QDoubleSpinBox(); n.setRange(low, high); n.setDecimals(decimals); n.setSingleStep(.1); n.setValue(value); return n

    def add_number(self, form, key, text, value, low, high):
        n = self.number(value, low, high); self.controls[key] = n; form.addRow(text, n)

    def combo(self, items):
        c = QComboBox(); c.addItems(items); return c

    def file_row(self, edit, filter='Tất cả (*)', folder=False):
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.addWidget(edit, 1)
        def choose():
            path = QFileDialog.getExistingDirectory(self, 'Chọn thư mục', edit.text() or self.out_dir) if folder else QFileDialog.getOpenFileName(self, 'Chọn tệp', '', filter)[0]
            if path: edit.setText(path)
        b = self.button('…', choose); b.setFixedWidth(36); l.addWidget(b); return w

    def build_subtitles(self):
        lay = self.page('Phụ đề & giọng đọc', 'Chỉnh SRT trong cùng dự án, hoặc tạo phụ đề, dịch và giọng đọc với các công cụ tùy chọn.')
        bar = QHBoxLayout(); bar.addWidget(self.button('Mở SRT', self.open_srt)); bar.addWidget(self.button('Thêm dòng', self.add_subtitle)); bar.addWidget(self.button('Bỏ dòng', self.remove_subtitle)); bar.addWidget(self.button('Lưu SRT & áp dụng', self.save_srt, True)); bar.addStretch(); lay.addLayout(bar)
        self.srt_path = QLabel('Chưa mở phụ đề'); self.srt_path.setObjectName('muted'); lay.addWidget(self.srt_path)
        self.srt_table = QTableWidget(0, 3); self.srt_table.setHorizontalHeaderLabels(['Bắt đầu (giây)', 'Kết thúc (giây)', 'Nội dung'])
        self.srt_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.srt_table.setColumnWidth(0, 150); self.srt_table.setColumnWidth(1, 150); self.srt_table.setWordWrap(True); lay.addWidget(self.srt_table, 1)
        self.srt_table.itemChanged.connect(self.mark_subtitle_dirty)
        ai = QHBoxLayout(); self.source_language = self.combo(['auto', 'zh', 'en', 'vi', 'ja', 'ko'])
        ai.addWidget(QLabel('Tiếng nguồn')); ai.addWidget(self.source_language); ai.addWidget(self.button('Nhận dạng giọng nói', self.transcribe))
        self.language = self.combo(['vi', 'en', 'zh-CN', 'ja', 'ko']); ai.addWidget(QLabel('Dịch sang')); ai.addWidget(self.language)
        ai.addWidget(self.button('Dịch phụ đề', self.translate)); ai.addStretch(); lay.addLayout(ai)
        voices = QHBoxLayout(); voices.addWidget(QLabel('Giọng đọc')); self.voice = self.combo(['vi-VN-HoaiMyNeural', 'vi-VN-NamMinhNeural', 'en-US-JennyNeural']); voices.addWidget(self.voice)
        voices.addWidget(self.button('Tạo giọng đọc', self.dub)); voices.addStretch(); lay.addLayout(voices)
        note = QLabel('Nhận dạng cần mô hình Whisper tải về lần đầu. Dịch và giọng đọc cần Internet; nội dung phụ đề sẽ được gửi đến dịch vụ tương ứng khi bạn xác nhận thao tác.'); note.setObjectName('muted'); note.setWordWrap(True); lay.addWidget(note)

    def build_jobs(self):
        lay = self.page('Tác vụ', 'Theo dõi tiến độ xử lý. Kết quả thành công được đưa về kho video.')
        self.job_title = QLabel('Chưa có tác vụ đang chạy'); lay.addWidget(self.job_title)
        self.progress = QProgressBar(); lay.addWidget(self.progress)
        self.cancel_button = self.button('Hủy tác vụ đang chạy', self.cancel_job); self.cancel_button.setEnabled(False); lay.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.job_list = QListWidget(); lay.addWidget(self.job_list, 1)
        lay.addWidget(self.button('Mở thư mục kết quả', lambda: self.open_folder(self.out_dir)), 0, Qt.AlignmentFlag.AlignLeft)

    def build_help(self):
        lay = self.page('Bắt đầu với VideoEditorIndependent', 'Ứng dụng Windows độc lập dùng chung kho media, dự án và quy trình xuất.')
        text = QTextEdit(); text.setReadOnly(True)
        text.setHtml('''<h2>Quy trình</h2><p>1. Nhập video ở <b>Kho video</b> hoặc tải bằng đường dẫn ở <b>Tải video</b>.</p><p>2. Chọn video, đưa vào <b>Chỉnh sửa</b>. Đặt thời điểm bắt đầu/kết thúc từng đoạn và sắp xếp thứ tự.</p><p>3. Chọn khung hình, tốc độ, hiệu chỉnh màu, âm thanh thêm, SRT hoặc logo.</p><p>4. Nhấn <b>Xuất video</b>. Tab <b>Kết quả xuất</b> xem đúng video đã xử lý; tab <b>Video nguồn</b> dùng để chọn điểm cắt.</p><h2>Tệp dự án</h2><p>Ctrl+S lưu các đường dẫn và thông số vào .mfs. Giữ tệp nguồn tại vị trí ban đầu để mở lại dự án.</p><h2>Phụ đề và tự động hóa</h2><p>Nhập, sửa và lưu SRT hoạt động trực tiếp. Công cụ nhận dạng Whisper, dịch và giọng đọc đã được đóng gói. Mô hình Whisper cần tải về ở lần nhận dạng đầu tiên; dịch và giọng đọc cần kết nối Internet.</p><p>Tự động đăng bài, kết nối tài khoản, OCR phụ đề trên hình và tách giọng khỏi nhạc chưa được triển khai trong bản 1.0.</p>'''); lay.addWidget(text)
        f = QFormLayout(); self.output_dir_edit = QLineEdit(self.out_dir); f.addRow('Thư mục mặc định', self.file_row(self.output_dir_edit, folder=True)); lay.addLayout(f)
        lay.addWidget(self.button('Lưu thư mục', self.save_output_dir), 0, Qt.AlignmentFlag.AlignLeft)

    def mark_dirty(self, *_):
        if not self.syncing:
            self.dirty = True
            self.setWindowTitle('VideoEditorIndependent Studio • Dự án chưa lưu')

    def mark_subtitle_dirty(self, *_):
        if not self.syncing:
            self.subtitle_dirty = True
            self.mark_dirty()

    def save_output_dir(self):
        self.out_dir = self.output_dir_edit.text().strip() or self.out_dir
        Path(self.out_dir).mkdir(parents=True, exist_ok=True)
        self.prefs.setValue('output', self.out_dir); self.download_dir.setText(self.out_dir)
        self.statusBar().showMessage('Đã lưu thư mục mặc định', 5000)

    def restore_library(self):
        paths = self.prefs.value('library', [])
        if isinstance(paths, str): paths = [paths]
        valid = [p for p in paths if Path(p).is_file()]
        if valid: self.ingest(valid)

    def persist_library(self):
        self.prefs.setValue('library', [m['path'] for m in self.library])

    def ingest(self, paths, add_clips=False):
        paths = list(dict.fromkeys(str(Path(p).resolve()) for p in paths))
        def probe_all(progress, cancel):
            result = []; errors = []
            for p in paths:
                if cancel.is_set(): break
                try:
                    m = engine.probe(p)
                    if not m.get('width'): raise ValueError('Tệp không có hình ảnh video')
                    result.append(m)
                except Exception as e: errors.append(f'{Path(p).name}: {e}')
            return result, errors
        def done(data):
            media, errors = data
            for m in media:
                if m['path'] not in [x['path'] for x in self.library]: self.library.append(m)
                if add_clips: self.clips.append(dict(path=m['path'], start=0., end=m['duration'], duration=m['duration']))
            self.render_library(); self.persist_library()
            if add_clips:
                self.render_clips(len(self.clips)-1); self.mark_dirty(); self.navigate(2)
            if errors: self.info('Một số tệp chưa nhập được', '\n'.join(errors[:5]))
        self.background(probe_all, done)

    def background(self, func, done):
        worker = Worker(func); self.workers.add(worker)
        worker.result.connect(done); worker.failed.connect(lambda msg: self.info('Không thể xử lý', msg))
        worker.finished.connect(lambda: (self.workers.discard(worker), worker.deleteLater())); worker.start()

    def import_files(self):
        paths = QFileDialog.getOpenFileNames(self, 'Nhập video', '', 'Video (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts);;Tất cả (*)')[0]
        if paths: self.ingest(paths)

    def import_to_editor(self):
        paths = QFileDialog.getOpenFileNames(self, 'Thêm video', '', 'Video (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts);;Tất cả (*)')[0]
        if paths: self.ingest(paths, True)

    def render_library(self):
        self.lib_list.clear(); q = self.search.text().casefold()
        for m in self.library:
            if q not in m['name'].casefold(): continue
            item = QListWidgetItem(f"{m['name']}\n{time_label(m['duration'])}  ·  {m['width']} × {m['height']}\n{m['path']}")
            item.setData(Qt.ItemDataRole.UserRole, m); self.lib_list.addItem(item)
        self.lib_info.setText(f'{len(self.library)} video trong kho · Nhấp đúp để chỉnh sửa')

    def add_selected(self):
        for item in self.lib_list.selectedItems():
            m = item.data(Qt.ItemDataRole.UserRole)
            self.clips.append(dict(path=m['path'], start=0., end=m['duration'], duration=m['duration']))
        if self.lib_list.selectedItems(): self.render_clips(len(self.clips)-1); self.mark_dirty(); self.navigate(2)

    def remove_library(self):
        paths = {i.data(Qt.ItemDataRole.UserRole)['path'] for i in self.lib_list.selectedItems()}
        self.library = [m for m in self.library if m['path'] not in paths]; self.render_library(); self.persist_library()

    def open_selected_folder(self):
        item = self.lib_list.currentItem()
        if item: self.open_folder(str(Path(item.data(Qt.ItemDataRole.UserRole)['path']).parent))

    def open_folder(self, path):
        from PySide6.QtGui import QDesktopServices
        Path(path).mkdir(parents=True, exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def render_clips(self, select=0):
        self.syncing = True; self.clip_list.clear()
        for i, c in enumerate(self.clips):
            self.clip_list.addItem(f"{i+1:02d}  {Path(c['path']).name}\n{time_label(c['start'])} → {time_label(c['end'])}  ·  {c['end']-c['start']:.2f}s")
        self.syncing = False
        if self.clips: self.clip_list.setCurrentRow(min(select, len(self.clips)-1))
        else: self.select_clip(-1)

    def select_clip(self, index):
        if self.syncing: return
        self.current_clip = index
        if not (0 <= index < len(self.clips)):
            self.player.stop(); self.player.setSource(QUrl()); self.trim_start.setValue(0); self.trim_end.setValue(0); return
        c = self.clips[index]; self.syncing = True
        self.trim_start.setMaximum(c['duration']); self.trim_end.setMaximum(c['duration'])
        self.trim_start.setValue(c['start']); self.trim_end.setValue(c['end']); self.syncing = False
        self.player.setSource(QUrl.fromLocalFile(c['path']))
        QTimer.singleShot(200, lambda: self.player.setPosition(int(c['start']*1000)) if self.current_clip == index and self.player.source().toLocalFile() == c['path'] else None)

    def trim_changed(self):
        if self.syncing or not (0 <= self.current_clip < len(self.clips)): return
        c = self.clips[self.current_clip]; c['start'] = self.trim_start.value(); c['end'] = self.trim_end.value()
        self.clip_list.item(self.current_clip).setText(f"{self.current_clip+1:02d}  {Path(c['path']).name}\n{time_label(c['start'])} → {time_label(c['end'])}  ·  {c['end']-c['start']:.2f}s")
        self.mark_dirty()

    def move_clip(self, direction):
        ix = self.current_clip; new = ix + direction
        if 0 <= ix < len(self.clips) and 0 <= new < len(self.clips):
            self.clips[ix], self.clips[new] = self.clips[new], self.clips[ix]; self.render_clips(new); self.mark_dirty()

    def remove_clip(self):
        if 0 <= self.current_clip < len(self.clips):
            ix = self.current_clip; self.clips.pop(ix); self.render_clips(ix); self.mark_dirty()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState: self.player.pause()
        else: self.player.play()

    def position_changed(self, value):
        if not self.seek.isSliderDown(): self.seek.setValue(value)
        self.time.setText(time_label(value/1000))

    def settings(self):
        sizes = [(0, 0), (1920, 1080), (1080, 1920), (1280, 720), (720, 1280), (1080, 1080), (854, 480)]
        w, h = sizes[self.resolution.currentIndex()]
        s = {key: control.value() for key, control in self.controls.items()}
        s.update(width=w, height=h, fit='contain' if self.fit.currentIndex()==0 else 'cover', fps=self.fps.value(), rotation=self.rotation.currentIndex()*90,
                 mirror=self.mirror.isChecked(), mute=self.mute.isChecked(), music_path=self.music.text().strip(), music_loop=self.music_loop.isChecked(), subtitle_path=self.subtitle.text().strip(), subtitle_size=self.sub_size.value(),
                 logo_path=self.logo.text().strip(), logo_width=self.logo_width.value(), logo_position=['top-right', 'top-left', 'bottom-right', 'bottom-left'][self.logo_pos.currentIndex()],
                 blur_region=[n.value() for n in self.blur_coords] if self.blur_on.isChecked() else None)
        return s

    def reset_adjustments(self):
        for key, value in dict(speed=1, brightness=0, contrast=1, saturation=1, volume=1, music_volume=.35).items(): self.controls[key].setValue(value)
        self.rotation.setCurrentIndex(0); self.mirror.setChecked(False); self.mute.setChecked(False)

    def output_path(self, title, suffix, stem='video'):
        Path(self.out_dir).mkdir(parents=True, exist_ok=True)
        proposed = str(Path(self.out_dir) / f'{stem}_{datetime.now():%Y%m%d_%H%M%S}{suffix}')
        path = QFileDialog.getSaveFileName(self, title, proposed, f'Tệp (*{suffix})')[0]
        if not path: return None
        if not path.lower().endswith(suffix): path += suffix
        if Path(path).exists(): self.info('Chọn tên mới', 'Tệp đã tồn tại. Chọn tên mới để giữ kết quả hiện có.'); return None
        return path

    def start_job(self, title, func, done=None):
        if self.active_job: self.info('Đang xử lý', 'Hãy chờ tác vụ hiện tại hoàn thành hoặc hủy ở mục Tác vụ.'); return
        worker = Worker(func); self.workers.add(worker); self.active_job = worker
        item = QListWidgetItem(title + ' · Đang xử lý'); self.job_list.insertItem(0, item)
        self.progress.setValue(0); self.job_title.setText(title); self.cancel_button.setEnabled(True); self.export_button.setEnabled(False)
        worker.progress.connect(lambda value, msg: (self.progress.setValue(max(0, min(100, value))), self.job_title.setText(f'{title} · {msg}'), self.statusBar().showMessage(f'{title}: {value}% · {msg}')))
        def completed(result):
            item.setText(title + ' · Hoàn thành'); self.progress.setValue(100)
            self.statusBar().showMessage(title + ' · Hoàn thành', 15000)
            if done: done(result)
        def failed(msg):
            item.setText(title + ' · ' + msg); self.job_title.setText(title + ' · ' + msg)
            self.info('Tác vụ chưa hoàn thành', msg)
        def finished():
            self.active_job = None; self.workers.discard(worker); self.cancel_button.setEnabled(False); self.export_button.setEnabled(True); worker.deleteLater()
        worker.result.connect(completed); worker.failed.connect(failed); worker.finished.connect(finished); worker.start(); self.navigate(4)

    def cancel_job(self):
        if self.active_job:
            self.active_job.cancel_event.set(); self.cancel_button.setEnabled(False); self.job_title.setText('Đang dừng tác vụ…')

    def export(self):
        if not self.clips: self.info('Chưa có video', 'Thêm ít nhất một video vào chỉnh sửa.'); return
        if any(c['end']<=c['start'] for c in self.clips): self.info('Khoảng cắt chưa hợp lệ', 'Kết thúc phải lớn hơn bắt đầu cho mỗi đoạn video.'); return
        if self.subtitle_dirty:
            if self.srt_table.rowCount() and not self.save_srt(): return
            if not self.srt_table.rowCount(): self.subtitle.clear()
        path = self.output_path('Xuất video MP4', '.mp4')
        if not path: return
        clips = [dict(c) for c in self.clips]; settings = self.settings(); self.player.pause(); self.output_player.stop()
        def done(result):
            self.ingest([result]); self.output_player.setSource(QUrl.fromLocalFile(result)); self.output_hint.setText(result); self.preview_tabs.setCurrentIndex(1); self.navigate(2)
        self.start_job('Xuất video', lambda p,c: engine.media_process(clips, settings, path, p, c), done)

    def selected_source(self):
        if 0 <= self.current_clip < len(self.clips): return self.clips[self.current_clip]['path']
        self.info('Chưa chọn video', 'Chọn một đoạn video trong mục Chỉnh sửa.'); return None

    def split_selected(self):
        src = self.selected_source()
        if not src: return
        sec, ok = QInputDialog.getDouble(self, 'Chia video', 'Độ dài mỗi phần (giây):', 60, 1, 86400, 2)
        if not ok: return
        folder = QFileDialog.getExistingDirectory(self, 'Thư mục chứa các phần', self.out_dir)
        if folder: self.start_job('Chia video', lambda p,c: engine.split_video(src, sec, folder, p, c), lambda result: self.ingest(result))

    def extract_audio(self):
        src = self.selected_source()
        if not src: return
        path = self.output_path('Lưu âm thanh MP3', '.mp3', Path(src).stem)
        if path: self.start_job('Tách MP3', lambda p,c: engine.extract_mp3(src, path, p, c), lambda _: self.statusBar().showMessage(f'Đã lưu MP3: {path}', 15000))

    def download(self):
        url = self.url_edit.text().strip(); folder = self.download_dir.text().strip()
        if not url.startswith(('https://','http://')): self.info('Liên kết chưa hợp lệ', 'Dán liên kết bắt đầu bằng https:// hoặc http://.'); return
        if not folder: self.info('Thiếu thư mục', 'Chọn thư mục để lưu video.'); return
        self.start_job('Tải video', lambda p,c: engine.download_video(url, folder, p, c), lambda result: (self.ingest(result), self.navigate(0)))

    def open_srt(self):
        path = QFileDialog.getOpenFileName(self, 'Mở phụ đề', '', 'Phụ đề (*.srt)')[0]
        if path: self.load_srt_table(path)

    def load_srt_table(self, path):
        try: rows = ai_tools.load_srt(path)
        except Exception as e: self.info('Không đọc được SRT', str(e)); return
        old_syncing = self.syncing; self.syncing = True; self.srt_table.setRowCount(0)
        for row in rows: self.add_subtitle(row)
        self.syncing = old_syncing; self.subtitle_dirty = False
        self.srt_path.setText(path); self.subtitle.setText(path)

    def add_subtitle(self, row=None):
        if not isinstance(row, dict): row = dict(start=0, end=3, text='Nội dung phụ đề')
        ix = self.srt_table.rowCount(); self.srt_table.insertRow(ix)
        for j, value in enumerate([f"{row['start']:.3f}", f"{row['end']:.3f}", row['text']]): self.srt_table.setItem(ix, j, QTableWidgetItem(value))
        self.srt_table.resizeRowToContents(ix)

    def remove_subtitle(self):
        if self.srt_table.currentRow()>=0: self.srt_table.removeRow(self.srt_table.currentRow()); self.mark_subtitle_dirty()

    def subtitle_rows(self):
        rows = []
        for i in range(self.srt_table.rowCount()):
            try: start, end = [float(self.srt_table.item(i,j).text()) for j in [0,1]]; text = self.srt_table.item(i,2).text()
            except (ValueError, AttributeError): raise ValueError(f'Dòng {i+1}: thời gian phải là số giây.')
            if start<0 or end<=start: raise ValueError(f'Dòng {i+1}: thời gian kết thúc phải lớn hơn bắt đầu.')
            rows.append(dict(start=start,end=end,text=text))
        return rows

    def save_srt(self):
        try: rows = self.subtitle_rows()
        except Exception as e: self.info('Phụ đề chưa hợp lệ',str(e)); return
        if not rows: self.info('Chưa có phụ đề','Thêm một dòng phụ đề trước khi lưu.'); return
        path = self.output_path('Lưu phụ đề SRT', '.srt', 'phu_de')
        if not path: return
        try:
            ai_tools.save_srt(rows,path); self.subtitle.setText(path); self.srt_path.setText(path); self.subtitle_dirty=False
            return path
        except Exception as e: self.info('Không lưu được phụ đề',str(e))

    def current_srt(self):
        if self.subtitle_dirty: return self.save_srt()
        return self.subtitle.text().strip()

    def ai_confirm(self, text):
        return QMessageBox.question(self, 'Công cụ tự động', text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes

    def transcribe(self):
        src = self.selected_source()
        if not src: return
        if not self.ai_confirm('Nhận dạng chạy trên máy. Lần đầu cần Internet để tải mô hình Whisper. Bắt đầu nhận dạng?'): return
        path = self.output_path('Lưu phụ đề nhận dạng','.srt','nhan_dang')
        if path:
            lang = self.source_language.currentText()
            self.start_job('Nhận dạng giọng nói',lambda p,c: ai_tools.transcribe(src,path,lang,p,c),lambda result:(self.load_srt_table(result),self.navigate(3)))

    def translate(self):
        src = self.current_srt()
        if not src: return
        if not Path(src).is_file(): self.info('Thiếu SRT','Mở hoặc lưu phụ đề trước khi dịch.'); return
        lang = self.language.currentText()
        if lang=='auto': self.info('Chọn ngôn ngữ đích','Chọn mã ngôn ngữ để dịch tới, ví dụ vi hoặc en.'); return
        if not self.ai_confirm('Nội dung SRT sẽ được gửi tới Google Translate qua công cụ deep-translator. Bắt đầu dịch?'): return
        path = self.output_path('Lưu phụ đề dịch','.srt','phu_de_'+lang)
        if path: self.start_job('Dịch phụ đề',lambda p,c: ai_tools.translate_srt(src,path,lang,p,c),lambda result:(self.load_srt_table(result),self.navigate(3)))

    def dub(self):
        src = self.current_srt()
        if not src: return
        if not Path(src).is_file(): self.info('Thiếu SRT','Mở hoặc lưu phụ đề trước khi tạo giọng đọc.'); return
        if not self.ai_confirm('Nội dung SRT sẽ được gửi tới dịch vụ giọng đọc Microsoft Edge. Bắt đầu tạo âm thanh?'): return
        path = self.output_path('Lưu giọng đọc','.wav','giong_doc')
        if path:
            voice = self.voice.currentText()
            self.start_job('Tạo giọng đọc',lambda p,c: ai_tools.dub_srt(src,path,voice,p,c),lambda result:(self.music.setText(result),self.controls['music_volume'].setValue(1),self.music_loop.setChecked(False),self.mute.setChecked(True),self.navigate(2)))

    def confirm_new(self):
        if not self.dirty: return True
        answer = QMessageBox.question(self,'Dự án chưa lưu','Lưu dự án trước khi tiếp tục?',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
        if answer==QMessageBox.StandardButton.Cancel: return False
        if answer==QMessageBox.StandardButton.Save: return self.save_project()
        return True

    def new_project(self):
        if not self.confirm_new(): return
        self.clips=[]; self.render_clips(); self.reset_adjustments(); self.music.clear(); self.subtitle.clear(); self.logo.clear(); self.blur_on.setChecked(False)
        self.resolution.setCurrentIndex(0); self.fit.setCurrentIndex(0); self.fps.setValue(30)
        self.music_loop.setChecked(True)
        self.sub_size.setValue(24); self.logo_width.setValue(160); self.logo_pos.setCurrentIndex(0)
        for n, value in zip(self.blur_coords, [0, 0, 100, 100]): n.setValue(value)
        self.srt_table.setRowCount(0); self.srt_path.setText('Chưa mở phụ đề')
        self.subtitle_dirty=False
        self.output_player.stop(); self.output_player.setSource(QUrl()); self.output_hint.setText('Xuất video để xem kết quả có đầy đủ hiệu ứng.')
        self.project_path=None; self.dirty=False; self.setWindowTitle('VideoEditorIndependent Studio'); self.navigate(2)

    def save_project(self):
        path = self.project_path or QFileDialog.getSaveFileName(self,'Lưu dự án',str(Path(self.out_dir)/'du_an.mfs'),'VideoEditorIndependent (*.mfs)')[0]
        if not path: return False
        if not path.lower().endswith('.mfs'): path += '.mfs'
        try:
            Path(path).parent.mkdir(parents=True,exist_ok=True)
            rows = self.subtitle_rows()
            tmp = Path(path).with_suffix('.mfs.tmp'); tmp.write_text(json.dumps(dict(version=1,clips=self.clips,settings=self.settings(),subtitle_rows=rows,subtitle_dirty=self.subtitle_dirty),ensure_ascii=False,indent=2),encoding='utf-8'); os.replace(tmp,path)
        except Exception as e: self.info('Không lưu được dự án',str(e)); return False
        self.project_path=path; self.dirty=False; self.setWindowTitle('VideoEditorIndependent Studio · '+Path(path).stem); self.statusBar().showMessage('Đã lưu dự án',5000); return True

    def open_project(self):
        if not self.confirm_new(): return
        path = QFileDialog.getOpenFileName(self,'Mở dự án','','VideoEditorIndependent (*.mfs)')[0]
        if not path: return
        try:
            data=json.loads(Path(path).read_text(encoding='utf-8'))
            clips=data['clips']; s=data.get('settings',{})
            for c in clips:
                if not Path(c['path']).is_file(): raise ValueError('Không tìm thấy video nguồn: '+c['path'])
                for k in ['start','end','duration']: c[k]=float(c[k])
            self.syncing=True; self.clips=clips
            for key,n in self.controls.items(): n.setValue(s.get(key,1 if key in ['speed','volume','contrast','saturation'] else 0))
            sizes=[(0,0),(1920,1080),(1080,1920),(1280,720),(720,1280),(1080,1080),(854,480)]; self.resolution.setCurrentIndex(sizes.index((s.get('width',0),s.get('height',0))) if (s.get('width',0),s.get('height',0)) in sizes else 0)
            self.fit.setCurrentIndex(0 if s.get('fit','contain')=='contain' else 1); self.fps.setValue(s.get('fps',30)); self.rotation.setCurrentIndex(int(s.get('rotation',0))//90)
            self.mirror.setChecked(s.get('mirror',False)); self.mute.setChecked(s.get('mute',False))
            self.music_loop.setChecked(s.get('music_loop',True))
            for edit,key in [(self.music,'music_path'),(self.subtitle,'subtitle_path'),(self.logo,'logo_path')]: edit.setText(s.get(key,''))
            self.sub_size.setValue(s.get('subtitle_size',24)); self.logo_width.setValue(s.get('logo_width',160)); self.logo_pos.setCurrentIndex(['top-right','top-left','bottom-right','bottom-left'].index(s.get('logo_position','top-right')))
            blur=s.get('blur_region'); self.blur_on.setChecked(bool(blur))
            if blur:
                for n,v in zip(self.blur_coords,blur): n.setValue(v)
            self.srt_table.setRowCount(0)
            rows = data.get('subtitle_rows', [])
            if not rows and s.get('subtitle_path') and Path(s['subtitle_path']).is_file(): rows = ai_tools.load_srt(s['subtitle_path'])
            for row in rows: self.add_subtitle(row)
            self.subtitle_dirty=bool(data.get('subtitle_dirty',False))
            self.srt_path.setText(s.get('subtitle_path','') or ('Phụ đề trong dự án chưa lưu SRT' if self.srt_table.rowCount() else 'Chưa mở phụ đề'))
            self.syncing=False; self.render_clips(); self.project_path=path; self.dirty=False; self.setWindowTitle('VideoEditorIndependent Studio · '+Path(path).stem); self.navigate(2)
        except Exception as e: self.syncing=False; self.info('Không mở được dự án',str(e))

    def info(self,title,text): QMessageBox.information(self,title,text)

    def closeEvent(self,event):
        if self.workers:
            self.info('Đang xử lý','Chờ thao tác hoàn thành hoặc hủy tác vụ trước khi đóng ứng dụng.'); event.ignore(); return
        if not self.confirm_new(): event.ignore(); return
        self.player.stop(); self.output_player.stop(); self.persist_library(); event.accept()


def main():
    if '--job' in sys.argv:
        job_file = Path(sys.argv[sys.argv.index('--job') + 1])
        result_file = job_file.with_suffix('.result.json')
        events = []
        progress = lambda value, message: events.append({'percent':value,'message':message})
        cancel = threading.Event()
        try:
            job = json.loads(job_file.read_text(encoding='utf-8-sig'))
            op = job['operation']
            if op == 'export':
                from render_pipeline import render_project
                result = render_project(job['clips'],job.get('settings',{}),job['output'],progress,cancel)
            elif op == 'download': result = engine.download_video(job['url'],job['output'],progress,cancel)
            elif op == 'translate': result = ai_tools.translate_srt(job['input'],job['output'],job.get('language','vi'),progress,cancel)
            elif op == 'dub':
                import local_tts
                with local_tts.device_scope(job.get('tts_device', 'auto')):
                    result = ai_tools.dub_srt(job['input'],job['output'],job.get('voice','vi-VN-HoaiMyNeural'),progress,cancel)
            elif op == 'transcribe': result = ai_tools.transcribe(job['input'],job['output'],job.get('language','auto'),progress,cancel)
            elif op == 'mp3': result = engine.extract_mp3(job['input'],job['output'],progress,cancel)
            elif op == 'split': result = engine.split_video(job['input'],job['seconds'],job['output'],progress,cancel)
            elif op in ['ocr','separate','recognize']:
                import media_ai
                if op=='ocr': result=media_ai.extract_ocr(job['input'],job['output'],job.get('region'),job.get('language','ch'),job.get('interval',.5),progress,cancel)
                elif op=='separate': result=media_ai.separate_audio(job['input'],job['output'],job.get('mode','vocals'),progress,cancel,device=job.get('device','auto'))
                else: result=media_ai.transcribe_model(job['input'],job['output'],job.get('language','auto'),job.get('model','base'),progress,cancel,device=job.get('device','cpu'),batch_size=job.get('batch_size',1))
            elif op == 'workflow':
                import workflow
                result=workflow.run_workflow(job['videos'],job.get('config',{}),job['output'],progress,cancel)
            else: raise ValueError('Tác vụ không được hỗ trợ: '+str(op))
            payload = {'ok':True,'result':result,'progress':events}
            if op == 'dub' and local_tts.is_local_voice(job.get('voice', '')):
                payload['runtime'] = local_tts.last_runtime(job['voice'])
            result_file.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as exc:
            result_file.write_text(json.dumps({'ok':False,'error':str(exc),'progress':events},ensure_ascii=False,indent=2),encoding='utf-8')
            sys.exit(1)
        return
    if '--self-check' in sys.argv:
        import importlib
        import media_ai
        runtime=media_ai._load_runtime()
        results = {}
        for name in ['PySide6.QtMultimedia', 'faster_whisper', 'deep_translator', 'edge_tts','rapidocr_onnxruntime','torch','torchaudio','torchvision','easyocr','demucs']:
            try: importlib.import_module(name); results[name] = 'OK'
            except Exception as e: results[name] = str(e)
        for tool in ['ffmpeg', 'ffprobe', 'yt-dlp']:
            try: results[tool] = engine.tool_path(tool)
            except Exception as e: results[tool] = str(e)
        report = Path(sys.argv[sys.argv.index('--self-check') + 1])
        report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    app=QApplication(sys.argv); app.setApplicationName('VideoEditorIndependent Studio'); app.setOrganizationName('VideoEditorIndependent'); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    def exception_handler(kind,value,tb):
        directory=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)); directory.mkdir(parents=True,exist_ok=True)
        (directory/'error.log').write_text(''.join(traceback.format_exception(kind,value,tb)),encoding='utf-8')
        QMessageBox.critical(None,'VideoEditorIndependent Studio',f'Không thể hoàn thành thao tác: {value}\nChi tiết: {directory / "error.log"}')
    sys.excepthook=exception_handler
    from studio_ui import StudioWindow
    window=StudioWindow(); window.show()
    if '--screenshot' in sys.argv:
        def capture():
            path=APP_DIR/'preview.png'; window.grab().save(str(path)); app.quit()
        QTimer.singleShot(1200,capture)
    if '--icon' in sys.argv:
        make_icon().pixmap(256,256).save(str(APP_DIR/'app.ico')); QTimer.singleShot(500,app.quit)
    sys.exit(app.exec())

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
