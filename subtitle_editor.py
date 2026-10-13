"""Independent timed subtitle editor with preview and real processing actions."""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import uuid

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSlider, QSplitter, QTableWidget, QTableWidgetItem, QPlainTextEdit,
    QVBoxLayout, QWidget,
)

from app import Worker, time_label
from dialogs import THEME
from preview_canvas import EditablePreview
import providers
import subtitle_tools


def apply_review_rows(original, proposed, picked):
    """Commit only selected text proposals, preserving cue metadata and timing."""
    result = copy.deepcopy(original)
    seen = set()
    for index in picked:
        if type(index) is not int or not 0 <= index < len(result) or index in seen:
            raise ValueError("Câu được chọn để sửa chưa hợp lệ.")
        seen.add(index)
        value = proposed.get(index)
        if not isinstance(value,str) or not value.strip():
            raise ValueError("Nội dung sửa không được rỗng.")
        result[index]["text"] = value
    return result


class AIFixReviewDialog(QDialog):
    """Generate a draft; application to the document requires an explicit click."""
    def __init__(self, editor):
        super().__init__(editor)
        from dialogs import _reference_modal
        _reference_modal(self)
        self.editor = editor
        self.base = copy.deepcopy(editor.rows)
        self.proposed = {}
        self.worker = None
        self._closing = False
        self.setObjectName("aiFixReviewDialog")
        self.setWindowTitle("AI sửa phụ đề")
        self.resize(1000,650)
        self.setStyleSheet(THEME)
        layout = QVBoxLayout(self)
        title = QLabel("AI sửa phụ đề · Xem lại trước khi áp dụng")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.only_selected = QCheckBox("Chỉ sửa những câu đang chọn")
        self.indices = editor.selected_indices()
        self.only_selected.setChecked(bool(self.indices))
        self.only_selected.setEnabled(bool(self.indices))
        layout.addWidget(self.only_selected)
        self.prompt = QPlainTextEdit("Sửa lỗi chính tả và dấu câu; giữ nguyên ý, ngôn ngữ và từng câu phụ đề.")
        self.prompt.setObjectName("aiFixPrompt")
        self.prompt.setMaximumHeight(80)
        layout.addWidget(self.prompt)
        self.table = QTableWidget(0,4)
        self.table.setObjectName("aiFixReviewTable")
        self.table.setHorizontalHeaderLabels(["Chọn","Câu gốc","Đề xuất","Trạng thái"])
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0,55)
        self.table.setColumnWidth(3,100)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table,1)
        self.status = QLabel("Chọn phạm vi và kịch bản, sau đó nhấn Chạy AI.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.run_button = QPushButton("Chạy AI")
        self.run_button.setObjectName("aiFixRun")
        self.run_button.clicked.connect(self.run)
        actions.addWidget(self.run_button)
        self.apply_button = QPushButton("Áp dụng các câu đã chọn")
        self.apply_button.setObjectName("aiFixApply")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        actions.addWidget(self.apply_button)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.cancel)
        actions.addWidget(self.stop_button)
        close = QPushButton("Đóng")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)

    def run(self):
        if self.worker:
            return
        config = copy.deepcopy(self.editor.host.translation_config)
        if config.get("provider","google") == "google":
            self.status.setText("Chọn Gemini/Groq/DeepSeek/Grok và khóa của bạn trong Thiết lập.")
            return
        self.run_indices = list(self.indices if self.only_selected.isChecked() else range(len(self.base)))
        selected = [copy.deepcopy(self.base[index]) for index in self.run_indices]
        config["scenario_prompt"] = self.prompt.toPlainText().strip()
        if not config["scenario_prompt"]:
            self.status.setText("Nhập yêu cầu sửa phụ đề.")
            return
        self.proposed = {}
        self.apply_button.setEnabled(False)
        self.worker = Worker(lambda progress,cancel:providers.rewrite_rows(selected,config,progress,cancel,store=self.editor.host.provider_store))
        self.editor.host.workers.add(self.worker)
        self.run_button.setEnabled(False)
        self.only_selected.setEnabled(False)
        self.prompt.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker.progress.connect(lambda percent,text:self.status.setText(f"{percent}% · {text}"))
        self.worker.result.connect(self.show_draft)
        self.worker.failed.connect(self.status.setText)
        worker = self.worker
        def finished():
            self.worker = None
            self.editor.host.workers.discard(worker)
            worker.deleteLater()
            self.run_button.setEnabled(True)
            self.prompt.setEnabled(True)
            self.only_selected.setEnabled(bool(self.indices))
            self.stop_button.setEnabled(False)
            if self._closing:
                QTimer.singleShot(0,self.reject)
        worker.finished.connect(finished)
        worker.start()

    def show_draft(self, rows):
        if self._closing:
            return
        if len(rows) != len(self.run_indices):
            self.status.setText("Đề xuất thiếu câu; chưa áp dụng thay đổi.")
            return
        self.table.setRowCount(len(rows))
        changed = 0
        for position,(index,row) in enumerate(zip(self.run_indices,rows)):
            value = row.get("text","")
            valid = isinstance(value,str) and bool(value.strip()) and value != self.base[index]["text"]
            picked = QTableWidgetItem()
            picked.setData(Qt.ItemDataRole.UserRole,index)
            picked.setFlags(Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.ItemIsUserCheckable if valid else Qt.ItemFlag.NoItemFlags))
            picked.setCheckState(Qt.CheckState.Checked if valid else Qt.CheckState.Unchecked)
            self.table.setItem(position,0,picked)
            for column,text in ((1,self.base[index]["text"]),(2,value),(3,"Đã sửa" if valid else "Không đổi")):
                self.table.setItem(position,column,QTableWidgetItem(str(text)))
            if valid:
                self.proposed[index] = value
                changed += 1
        self.apply_button.setEnabled(bool(changed))
        self.status.setText(f"{changed} câu có đề xuất. Bỏ chọn những câu bạn muốn giữ nguyên.")

    def apply(self):
        if self.worker or self.editor.rows != self.base:
            self.status.setText("Phụ đề đã thay đổi; hãy tạo lại đề xuất.")
            return
        picked = [self.table.item(row,0).data(Qt.ItemDataRole.UserRole) for row in range(self.table.rowCount())
                  if self.table.item(row,0).checkState()==Qt.CheckState.Checked]
        if picked:
            self.editor.set_rows(apply_review_rows(self.base,self.proposed,picked))
        self.accept()

    def cancel(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.status.setText("Đang dừng…")

    def reject(self):
        if self.worker:
            self._closing = True
            self.cancel()
            return
        super().reject()

    def closeEvent(self,event):
        event.ignore()
        self.reject()


class SubtitleEditorDialog(QDialog):
    saved = Signal(str, str)
    voice_ready = Signal(str)

    def __init__(self, parent, media, settings, assets):
        super().__init__(parent)
        self.host = parent
        self.media = dict(media)
        self.settings = copy.deepcopy(settings)
        self.assets = dict(assets)
        self.role = "original"
        self.documents = {"original": [], "translated": [], "bilingual": []}
        self._bilingual_manual = False
        self.paths = {}
        self.dirty_roles = set()
        self.history = []
        self._loading = False
        self.worker = None
        self._pending_close = False
        self._buttons = []
        self.setWindowTitle("Sửa phụ đề · " + media.get("name", Path(media["path"]).name))
        self.resize(1220, 780)
        self.setMinimumSize(940, 620)
        self.setStyleSheet(THEME)
        self.setModal(True)
        for role, key in (("original", "original_subtitle"), ("translated", "translated_subtitle")):
            path = assets.get(key, "")
            if not path and role == "original":
                path = settings.get("subtitle_path", "")
            if path and Path(path).is_file():
                self.documents[role] = subtitle_tools.load_subtitles(path)
                self.paths[role] = path
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 16, 18, 16)
        column.setSpacing(10)
        title = QLabel("Sửa phụ đề")
        title.setObjectName("dialogTitle")
        column.addWidget(title)
        top = QHBoxLayout()
        self.roles = QComboBox()
        for title, role in (("Phụ đề gốc", "original"), ("Phụ đề dịch", "translated"), ("Song ngữ", "bilingual")):
            self.roles.addItem(title, role)
        self.roles.currentIndexChanged.connect(self.switch_role)
        top.addWidget(self.roles)
        top.addWidget(self.button("Nhập SRT / VTT", self.import_subtitles))
        top.addStretch()
        self.path_label = QLabel()
        self.path_label.setObjectName("muted")
        self.path_label.setMaximumWidth(680)
        top.addWidget(self.path_label)
        column.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        preview_panel = QWidget()
        preview_column = QVBoxLayout(preview_panel)
        preview_column.setContentsMargins(0, 0, 10, 0)
        self.preview = EditablePreview(media, str(parent.thumbnail_path(media["path"])), settings, {})
        self.preview.setMinimumSize(300, 220)
        self.preview.setEnabled(False)  # Subtitle edits are made in the timed table.
        preview_column.addWidget(self.preview, 1)
        transport = QHBoxLayout()
        self.play_button = self.button("▶", self.toggle_play)
        transport.addWidget(self.play_button)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, int(media["duration"] * 1000))
        self.seek.sliderMoved.connect(self.seek_to)
        transport.addWidget(self.seek, 1)
        self.clock = QLabel(time_label(0))
        transport.addWidget(self.clock)
        preview_column.addLayout(transport)
        hint = QLabel("Nhấp đúp một câu để xem từ mốc bắt đầu. Giọng từng câu được lưu cùng phụ đề.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        preview_column.addWidget(hint)
        splitter.addWidget(preview_panel)

        table_panel = QWidget()
        table_column = QVBoxLayout(table_panel)
        table_column.setContentsMargins(0, 0, 0, 0)
        operations = QHBoxLayout()
        for title, action in (("+ Câu", self.add_cue), ("Xóa", self.delete_cues),
                              ("Tách", self.split_cue), ("Gộp", self.merge_cues), ("Hoàn tác", self.undo)):
            operations.addWidget(self.button(title, action))
        operations.addStretch()
        table_column.addLayout(operations)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("subtitleCueTable")
        self.table.setHorizontalHeaderLabels(["Bắt đầu (s)", "Kết thúc (s)", "Nội dung", "Giọng đọc"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setColumnWidth(0, 84)
        self.table.setColumnWidth(1, 84)
        self.table.setColumnWidth(3, 150)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.item_changed)
        self.table.cellDoubleClicked.connect(self.seek_cue)
        table_column.addWidget(self.table, 1)
        timing = QHBoxLayout()
        timing.addWidget(QLabel("Dịch thời gian (s)"))
        self.shift_value = QDoubleSpinBox()
        self.shift_value.setRange(-3600, 3600)
        self.shift_value.setDecimals(3)
        self.shift_value.setSingleStep(.1)
        timing.addWidget(self.shift_value)
        timing.addWidget(self.button("Áp dụng", self.shift_cues))
        timing.addStretch()
        table_column.addLayout(timing)
        splitter.addWidget(table_panel)
        splitter.setSizes([450, 700])
        column.addWidget(splitter, 1)

        search = QHBoxLayout()
        self.find = QLineEdit()
        self.find.setPlaceholderText("Tìm trong phụ đề…")
        self.replace = QLineEdit()
        self.replace.setPlaceholderText("Thay bằng…")
        self.case_sensitive = QCheckBox("Phân biệt hoa/thường")
        self.whole_word = QCheckBox("Nguyên từ")
        for widget in (self.find, self.replace, self.case_sensitive, self.whole_word):
            search.addWidget(widget)
        search.addWidget(self.button("Tìm", self.find_cues))
        search.addWidget(self.button("Thay tất cả", self.replace_cues))
        column.addLayout(search)
        actions = QHBoxLayout()
        self.fix_button = self.button("AI sửa phụ đề", self.ai_fix)
        self.regenerate_button = self.button("Tạo lại giọng đọc", self.regenerate_voice)
        self.cancel_button = self.button("Dừng", self.cancel_task)
        self.cancel_button.setEnabled(False)
        actions.addWidget(self.fix_button)
        actions.addWidget(self.regenerate_button)
        actions.addWidget(self.cancel_button)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        actions.addWidget(self.status, 1)
        self.save_button = self.button("Lưu phụ đề", self.save_dialog)
        self.save_button.setObjectName("primary")
        actions.addWidget(self.save_button)
        actions.addWidget(self.button("Đóng", self.reject))
        column.addLayout(actions)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.player.setVideoOutput(self.sink)
        self.sink.videoFrameChanged.connect(lambda frame: self.preview.set_frame(frame.toImage()))
        self.player.positionChanged.connect(self.position_changed)
        self.player.errorOccurred.connect(lambda *_: self.status.setText(self.player.errorString()))
        self.player.setSource(QUrl.fromLocalFile(media["path"]))
        self.refresh_table()

    def button(self, title, action):
        button = QPushButton(title)
        button.clicked.connect(lambda checked=False: action())
        self._buttons.append(button)
        return button

    @property
    def rows(self):
        return self.documents[self.role]

    def snapshot(self):
        self.history.append((self.role, copy.deepcopy(self.rows)))
        self.history = self.history[-60:]
        self.dirty_roles.add(self.role)
        if self.role == "bilingual":
            self._bilingual_manual = True

    def set_rows(self, rows, *, record=True):
        rows = subtitle_tools._rows(rows) if rows else []
        if record:
            self.snapshot()
        self.documents[self.role] = copy.deepcopy(rows)
        self.refresh_table()

    def refresh_table(self):
        self._loading = True
        self.table.setRowCount(0)
        names = [self.host.pipe_voice.itemText(index) for index in range(self.host.pipe_voice.count())]
        names += [row.get("voice", "") for row in self.rows if row.get("voice")]
        names = list(dict.fromkeys(names))
        for index, row in enumerate(self.rows):
            self.table.insertRow(index)
            for col, value in enumerate((f'{row["start"]:.3f}', f'{row["end"]:.3f}', row["text"])):
                self.table.setItem(index, col, QTableWidgetItem(str(value)))
            voice = QComboBox()
            voice.setEditable(True)
            voice.addItems(["Mặc định", *names])
            voice.setCurrentText(row.get("voice") or "Mặc định")
            voice.setMinimumWidth(0)
            voice.currentTextChanged.connect(lambda text, ix=index: self.voice_changed(ix, text))
            self.table.setCellWidget(index, 3, voice)
            self.table.setRowHeight(index, max(36, min(100, 20 * (row["text"].count("\n") + 1) + 12)))
        self._loading = False
        self.path_label.setText(Path(self.paths.get(self.role, "Chưa lưu")).name)
        self.preview.set_subtitle_rows(self.rows)
        self.status.setText(f"{len(self.rows)} câu")

    def item_changed(self, item):
        if self._loading or item.row() >= len(self.rows):
            return
        try:
            value = item.text() if item.column() == 2 else float(item.text())
            key = ("start", "end", "text")[item.column()]
            row = dict(self.rows[item.row()], **{key: value})
            subtitle_tools._rows([row])
        except (ValueError, TypeError):
            self._loading = True
            item.setBackground(QColor("#653333"))
            self._loading = False
            self.status.setText("Mốc thời gian cần không âm và kết thúc sau bắt đầu.")
            return
        self.snapshot()
        self.rows[item.row()] = row
        self._loading = True
        item.setBackground(QColor("transparent"))
        self._loading = False
        self.preview.set_subtitle_rows(self.rows)

    def voice_changed(self, index, voice):
        if self._loading or index >= len(self.rows):
            return
        self.snapshot()
        if voice and voice != "Mặc định":
            self.rows[index]["voice"] = voice
        else:
            self.rows[index].pop("voice", None)

    def selected_indices(self):
        return sorted({item.row() for item in self.table.selectedItems()})

    def switch_role(self):
        role = self.roles.currentData()
        if role == "bilingual" and not self._bilingual_manual:
            try:
                self.documents[role] = subtitle_tools.bilingual_rows(self.documents["original"], self.documents["translated"])
            except (ValueError, IndexError) as exc:
                self.status.setText(str(exc))
        self.role = role
        self.refresh_table()

    def add_cue(self):
        self.snapshot()
        start = self.player.position() / 1000
        self.rows.append(dict(start=start, end=start + 2, text="Câu phụ đề mới"))
        self.rows.sort(key=lambda row: row["start"])
        self.refresh_table()

    def delete_cues(self):
        picked = set(self.selected_indices())
        if picked:
            self.set_rows([row for index, row in enumerate(self.rows) if index not in picked])

    def split_cue(self):
        selected = self.selected_indices()
        if len(selected) != 1:
            self.status.setText("Chọn một câu để tách.")
            return
        index = selected[0]
        original = self.rows[index]
        words = original["text"].split()
        if len(words) < 2:
            self.status.setText("Câu cần có ít nhất hai từ để tách.")
            return
        middle = len(words) // 2
        midpoint = (original["start"] + original["end"]) / 2
        rows = copy.deepcopy(self.rows)
        rows[index:index + 1] = [dict(original, text=" ".join(words[:middle]), end=midpoint),
                                dict(original, text=" ".join(words[middle:]), start=midpoint)]
        self.set_rows(rows)

    def merge_cues(self):
        try:
            self.set_rows(subtitle_tools.merge_rows(self.rows, self.selected_indices()))
        except (ValueError, IndexError) as exc:
            self.status.setText(str(exc))

    def shift_cues(self):
        self.set_rows(subtitle_tools.shift_timings(self.rows, self.shift_value.value(), self.selected_indices() or None))

    def undo(self):
        if self.history:
            role, rows = self.history.pop()
            self.role = role
            self.roles.blockSignals(True)
            self.roles.setCurrentIndex(self.roles.findData(role))
            self.roles.blockSignals(False)
            self.documents[role] = rows
            self.refresh_table()

    def find_cues(self):
        needle = self.find.text()
        if not needle:
            return
        self.table.clearSelection()
        count = 0
        for index, row in enumerate(self.rows):
            unused, matches = subtitle_tools.replace_text([row], needle, needle,
                case_sensitive=self.case_sensitive.isChecked(), whole_word=self.whole_word.isChecked())
            if matches:
                for column in range(3):
                    self.table.item(index, column).setSelected(True)
                count += 1
        self.status.setText(f"Tìm thấy {count} câu")

    def replace_cues(self):
        if self.find.text():
            rows, count = subtitle_tools.replace_text(self.rows, self.find.text(), self.replace.text(),
                case_sensitive=self.case_sensitive.isChecked(), whole_word=self.whole_word.isChecked())
            self.set_rows(rows)
            self.status.setText(f"Đã thay {count} vị trí")

    def import_path(self, path):
        self.set_rows(subtitle_tools.load_subtitles(path))
        self.paths[self.role] = str(path)
        self.refresh_table()

    def import_subtitles(self):
        path = QFileDialog.getOpenFileName(self, "Nhập phụ đề", "", "Phụ đề (*.srt *.vtt)")[0]
        if path:
            try:
                self.import_path(path)
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def save_to(self, path):
        if not self.rows:
            raise ValueError("Thêm câu phụ đề trước khi lưu.")
        # Read table values as well, so invalid intermediate edits cannot be silently lost.
        rows = copy.deepcopy(self.rows)
        for index, row in enumerate(rows):
            row.update(start=float(self.table.item(index, 0).text()),
                       end=float(self.table.item(index, 1).text()), text=self.table.item(index, 2).text())
        subtitle_tools.save_subtitles(rows, path)
        self.documents[self.role] = rows
        self.paths[self.role] = str(path)
        self.dirty_roles.discard(self.role)
        self.path_label.setText(Path(path).name)
        self.saved.emit(self.role, str(path))
        self.status.setText("Đã lưu phụ đề và giọng từng câu")
        return str(path)

    def save_dialog(self):
        proposed = str(Path(self.host.out_dir) / ("phu_de_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".srt"))
        path = QFileDialog.getSaveFileName(self, "Lưu phụ đề", proposed, "SubRip (*.srt);;WebVTT (*.vtt)")[0]
        if path:
            if Path(path).suffix.lower() not in (".srt", ".vtt"):
                path += ".srt"
            try:
                self.save_to(path)
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setText("▶")
        else:
            self.player.play()
            self.play_button.setText("Ⅱ")

    def seek_to(self, position):
        self.player.setPosition(position)
        self.preview.set_position(position / 1000)

    def seek_cue(self, row, column=0):
        if row < len(self.rows):
            self.seek_to(round(self.rows[row]["start"] * 1000))

    def position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.clock.setText(time_label(position / 1000))
        self.preview.set_position(position / 1000)

    def start_task(self, title, function, done):
        if self.worker:
            self.status.setText("Chờ tác vụ hiện tại hoàn thành hoặc nhấn Dừng.")
            return
        self.worker = Worker(function)
        self.host.workers.add(self.worker)
        self._prior_button_states = [(button, button.isEnabled()) for button in self._buttons]
        for button, enabled in self._prior_button_states:
            button.setEnabled(button is self.cancel_button or button.text() == "Đóng")
        self.fix_button.setEnabled(False)
        self.regenerate_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.table.setEnabled(False)
        self.roles.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText(title)
        self.worker.progress.connect(lambda percent, text: self.status.setText(f"{percent}% · {text}"))
        self.worker.result.connect(lambda result: done(result) if not self._pending_close else None)
        self.worker.failed.connect(lambda error: self.status.setText(error))
        worker = self.worker
        def finished():
            self.worker = None
            self.host.workers.discard(worker)
            worker.deleteLater()
            for button, enabled in self._prior_button_states:
                button.setEnabled(enabled)
            for widget in (self.fix_button, self.regenerate_button, self.save_button, self.table, self.roles):
                widget.setEnabled(True)
            self.cancel_button.setEnabled(False)
            if self._pending_close:
                self._pending_close = False
                QTimer.singleShot(0, self.reject)
        worker.finished.connect(finished)
        worker.start()

    def ai_fix(self):
        if not self.rows:
            self.status.setText("Thêm hoặc nhập phụ đề trước.")
            return
        config = copy.deepcopy(self.host.translation_config)
        if config.get("provider", "google") == "google":
            self.status.setText("Chọn Gemini/Groq/DeepSeek/Grok và khóa của bạn trong Thiết lập để sửa bằng AI.")
            return
        AIFixReviewDialog(self).exec()

    def regenerate_voice(self):
        if not self.rows:
            self.status.setText("Thêm hoặc nhập phụ đề trước.")
            return
        rows = copy.deepcopy(self.rows)
        path = Path(self.host.out_dir) / ("giong_phu_de_" + uuid.uuid4().hex[:8] + ".wav")
        default_voice = self.host.pipe_voice.currentText()
        speed = self.host.tts_speed.value() if hasattr(self.host, "tts_speed") else 1
        delay = self.host.tts_delay.value() if hasattr(self.host, "tts_delay") else 0
        fit = self.host.tts_fit.currentData() if hasattr(self.host,"tts_fit") else "fit"
        def done(result):
            self.voice_ready.emit(str(result))
            self.status.setText("Đã tạo giọng: " + Path(result).name)
        def create(progress,cancel):
            import local_tts
            with local_tts.device_scope(self.host.tts_device.currentData() if hasattr(self.host,"tts_device") else "auto"):
                return subtitle_tools.dub_rows(rows, str(path), default_voice, progress, cancel,
                    voice_speed=speed, voice_delay=delay, voice_fit=fit,
                    voice_parallel=self.host.voice_parallel.value() if hasattr(self.host,"voice_parallel") else 1)
        self.start_task("Tạo giọng theo câu", create, done)

    def cancel_task(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.status.setText("Đang dừng…")

    def reject(self):
        if self.worker:
            self._pending_close = True
            self.cancel_task()
            return
        if self.dirty_roles and not self._pending_close:
            answer = QMessageBox.question(self, "Phụ đề chưa lưu", "Đóng và bỏ các thay đổi chưa lưu?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        self.player.stop()
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
