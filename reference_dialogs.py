"""Reference-style settings, resources and translation scenario dialogs.

Resource state is measured from real caches. Importing this module neither
downloads models nor imports heavyweight inference libraries.
"""
from __future__ import annotations

import importlib.util
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import threading

import gpu_runtime
import local_tts

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)


DIALOG_STYLE = """
QDialog {background:#171719;color:#e8e8ea;font-family:'Segoe UI';font-size:14px;}
QFrame#resourceCard,QFrame#keyCard,QFrame#scenarioCard {background:#252527;border:1px solid #3b3b3d;border-radius:10px;}
QFrame#sectionCard {background:#333335;border:0;border-radius:10px;}
QLabel#dialogTitle {font-size:19px;font-weight:700;}
QLabel#cardTitle {font-size:17px;font-weight:700;}
QLabel#muted,QLabel#description {color:#9b9ca3;}
QLabel#sectionTitle {color:#00bfd6;font-weight:700;font-size:15px;}
QLabel#readyBadge {color:#18c45c;background:#11362a;border:1px solid #155638;border-radius:12px;padding:4px 10px;font-size:12px;font-weight:700;}
QLabel#missingBadge {color:#a6a6ad;background:#252527;border:1px solid #404043;border-radius:12px;padding:4px 10px;font-size:12px;font-weight:700;}
QPushButton {background:#252527;border:1px solid #404043;border-radius:7px;padding:7px 12px;color:#e8e8ea;}
QPushButton:hover {border-color:#00bfd6;background:#303033;}
QPushButton:disabled {color:#737379;border-color:#333337;}
QPushButton#primary {color:#00bfd6;border-color:#00bfd6;font-weight:600;}
QLineEdit,QPlainTextEdit,QComboBox,QTableWidget {background:#242426;border:1px solid #404043;border-radius:7px;padding:7px;color:#e8e8ea;selection-background-color:#146475;}
QTabWidget::pane {border:1px solid #303033;border-radius:8px;background:#1d1d20;}
QTabBar::tab {background:#171719;color:#a3a3ac;padding:10px 20px;}
QTabBar::tab:selected {background:#252527;color:#f1f1f3;font-weight:700;}
QScrollArea {background:transparent;border:0;}
QHeaderView::section {background:#303033;border:0;padding:6px;}
"""

WHISPER_CHOICES = (
    ("tiny", "Rất nhỏ (nhanh)", "~75 MB"),
    ("base", "Cơ bản (nhanh)", "~145 MB"),
    ("small", "Nhỏ (cân bằng)", "~480 MB"),
    ("medium", "Vừa (chính xác)", "~1,5 GB"),
    ("large-v3", "Lớn v3", "~3 GB"),
    ("turbo", "Turbo", "~1,6 GB"),
)


def model_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VideoEditorIndependent" / "models"


def whisper_repository(model: str) -> str:
    if model == "turbo":
        return "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
    if model not in {row[0] for row in WHISPER_CHOICES}:
        raise ValueError("Mô hình Whisper không hợp lệ.")
    return "models--Systran--faster-whisper-" + model


def whisper_state(root: Path, model: str) -> tuple[bool, Path]:
    folder = Path(root) / "whisper" / whisper_repository(model)
    snapshots = folder / "snapshots"
    if snapshots.is_dir():
        for snapshot in snapshots.iterdir():
            if snapshot.is_dir() and all((snapshot / name).is_file() and (snapshot / name).stat().st_size > 0
                                         for name in ("config.json", "model.bin", "tokenizer.json")):
                return True, folder
    return False, folder


def own_cache_path(root: Path, candidate: Path) -> Path:
    """Constrain removal to this application's specific model cache."""
    base = Path(root).resolve()
    path = Path(candidate).resolve()
    if path == base or not path.is_relative_to(base):
        raise ValueError("Chỉ có thể gỡ mô hình trong thư mục dữ liệu VideoEditorIndependent.")
    return path


def remove_own_cache(root: Path, candidate: Path) -> None:
    path = own_cache_path(root, candidate)
    # A cache directory must not lead through a junction or symlink into another
    # application. Resolve every descendant before starting recursive removal.
    if path.is_dir():
        for item in path.rglob("*"):
            own_cache_path(root, item)
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()


def _download_resource_worker(connection, kind: str, root: str, selected: str) -> None:
    try:
        base = Path(root)
        base.mkdir(parents=True, exist_ok=True)
        if kind == "whisper":
            from faster_whisper.utils import download_model
            connection.send(("progress", 5, "Đang tải mô hình Whisper từ kho chính thức…"))
            downloaded = download_model(selected, cache_dir=str(base / "whisper"))
        elif kind == "demucs":
            import torch
            from demucs.pretrained import get_model
            torch.hub.set_dir(str(base / "demucs"))
            connection.send(("progress", 5, "Đang tải mô hình tách giọng Demucs…"))
            model = get_model("htdemucs").cpu().eval()
            del model
            downloaded = str(base / "demucs")
        elif kind == "hardware":
            import torch
            gpu = bool(torch.cuda.is_available())
            name = torch.cuda.get_device_name(0) if gpu else ""
            downloaded = dict(torch_version=str(torch.__version__), cuda=gpu, device=name)
        else:
            raise ValueError("Tài nguyên không được hỗ trợ.")
        connection.send(("result", downloaded))
    except Exception as exc:
        # No provider keys are passed to resource workers.
        connection.send(("error", str(exc) or type(exc).__name__))
    finally:
        connection.close()


def run_resource_job(kind: str, root: Path, selected: str, progress, cancel) -> object:
    """Keep native imports and downloads off the GUI, and permit hard cancel."""
    from ai_tools import AIError, CancelledError
    pack = gpu_runtime.discover_demucs_pack() if kind in ("demucs", "hardware") else None
    if pack:
        if cancel.is_set():
            raise CancelledError("Đã dừng kiểm tra tài nguyên.")
        if kind == "hardware":
            result = gpu_runtime.inspect_demucs_hardware(progress, cancel)
            progress(100, "Đã kiểm tra GPU bằng bộ tách giọng đã cài.")
            return result
        progress(100, "Đang dùng mô hình GPU đã cài; không cần tải lại.")
        return str(Path(pack["checkpoint"]).parent)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_download_resource_worker,
                             args=(sender, kind, str(root), selected), daemon=True)
    try:
        if cancel.is_set():
            raise CancelledError("Đã dừng tải tài nguyên.")
        worker.start()
        sender.close()
        while True:
            if cancel.is_set():
                raise CancelledError("Đã dừng tải tài nguyên. Có thể tải tiếp lần sau.")
            if receiver.poll(.1):
                try:
                    message = receiver.recv()
                except EOFError as exc:
                    raise AIError("Tiến trình tài nguyên đã dừng trước khi hoàn tất.") from exc
                if message[0] == "progress":
                    progress(message[1], message[2])
                elif message[0] == "result":
                    progress(100, "Đã hoàn tất.")
                    return message[1]
                elif message[0] == "error":
                    raise AIError(message[1])
            elif not worker.is_alive():
                raise AIError("Tiến trình tài nguyên đã đóng bất ngờ.")
    finally:
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
        receiver.close()
        sender.close()


class _ResourceThread(QThread):
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


def _button(text: str, slot, primary=False) -> QPushButton:
    button = QPushButton(text)
    if primary:
        button.setObjectName("primary")
    button.clicked.connect(slot)
    return button


def _label(text: str, name="", wrap=False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _scroll(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    return scroll


def _folder(path: Path) -> bool:
    Path(path).mkdir(parents=True, exist_ok=True)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))


class KeysDialog(QDialog):
    """Multiple API keys remain password-masked until explicitly revealed."""
    def __init__(self, parent, store, provider: str, title: str):
        super().__init__(parent)
        self.store, self.provider = store, provider
        self.rows: list[QLineEdit] = []
        self.setWindowTitle("Khóa API · " + title)
        self.resize(670, 450)
        self.setStyleSheet(DIALOG_STYLE)
        layout = QVBoxLayout(self)
        layout.addWidget(_label(title + " · Danh sách khóa API", "dialogTitle"))
        layout.addWidget(_label("Mỗi dòng một khóa. Khóa được mã hóa cho tài khoản Windows hiện tại; không đưa vào dự án hoặc nhật ký.", "muted", True))
        body = QWidget()
        self.key_layout = QVBoxLayout(body)
        self.key_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(_scroll(body), 1)
        for key in store.get_keys(provider):
            self.add_row(key)
        if not self.rows:
            self.add_row()
        actions = QHBoxLayout()
        actions.addWidget(_button("+ Thêm khóa", lambda: self.add_row()))
        actions.addWidget(_button("Dán nhiều khóa", self.paste_keys))
        actions.addStretch()
        self.reveal = QCheckBox("Hiện khóa")
        self.reveal.toggled.connect(self.show_keys)
        actions.addWidget(self.reveal)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Lưu khóa")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_row(self, key=""):
        row = QWidget()
        horizontal = QHBoxLayout(row)
        horizontal.setContentsMargins(0, 3, 0, 3)
        edit = QLineEdit(str(key))
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText("Nhập khóa API")
        edit.setMinimumWidth(380)
        self.rows.append(edit)
        horizontal.addWidget(edit, 1)
        def remove():
            self.rows.remove(edit)
            row.deleteLater()
        horizontal.addWidget(_button("Gỡ", remove))
        self.key_layout.addWidget(row)
        if hasattr(self, "reveal"):
            self.show_keys(self.reveal.isChecked())
        return edit

    def show_keys(self, visible):
        for edit in self.rows:
            edit.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)

    def paste_keys(self):
        # Clipboard access happens only in response to this explicit user action.
        values = [line.strip() for line in QApplication.clipboard().text().splitlines() if line.strip()]
        if not values:
            QMessageBox.information(self, "Không có khóa", "Clipboard chưa có khóa API. Mỗi dòng một khóa.")
            return
        if len(values) > 32:
            QMessageBox.warning(self, "Quá nhiều khóa", "Chỉ nhập tối đa 32 khóa cho mỗi nhà cung cấp.")
            return
        existing = {edit.text().strip() for edit in self.rows}
        for key in values:
            if key not in existing:
                self.add_row(key)
                existing.add(key)

    def save(self):
        try:
            self.store.save_keys(self.provider, [edit.text().strip() for edit in self.rows if edit.text().strip()])
        except Exception:
            # Validation or DPAPI errors must never echo pasted credentials.
            QMessageBox.warning(self, "Chưa lưu khóa", "Không thể lưu khóa. Kiểm tra mỗi dòng chỉ chứa một khóa hợp lệ và dữ liệu có thể ghi được.")
            return
        self.accept()


class SettingsDialog(QDialog):
    settings_changed = Signal()

    def __init__(self, parent, provider_store, scenario_store=None, prefs=None, *, resources_root=None):
        super().__init__(parent)
        self.provider_store = provider_store
        self.scenario_store = scenario_store
        self.prefs = prefs
        self.resources_root = Path(resources_root) if resources_root else model_root()
        self.key_cards = {}
        self.resource_cards = {}
        self._worker = None
        self._owns_worker = False
        self.setWindowTitle("Thiết Lập · VideoEditorIndependent Studio")
        self.resize(900, 720)
        self.setMinimumSize(760, 560)
        self.setStyleSheet(DIALOG_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.addWidget(_label("Thiết Lập", "dialogTitle"))
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self.build_keys(), "Khóa API")
        self.tabs.addTab(self.build_accounts(), "Đăng nhập (miễn phí)")
        self.tabs.addTab(self.build_common(), "Cài đặt chung")
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(_button("Đóng", self.accept))
        outer.addLayout(footer)
        self.refresh_keys()
        self.refresh_resources()

    def build_keys(self):
        from providers import PROVIDERS
        body = QWidget()
        layout = QVBoxLayout(body)
        section = QFrame()
        section.setObjectName("sectionCard")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(14, 13, 14, 14)
        section_layout.addWidget(_label("▾ Phụ đề & Dịch thuật", "sectionTitle"))
        grid = QGridLayout()
        grid.setSpacing(14)
        for index, (provider, metadata) in enumerate(PROVIDERS.items()):
            frame = QFrame()
            frame.setObjectName("keyCard")
            card = QVBoxLayout(frame)
            card.setContentsMargins(16, 13, 16, 14)
            top = QHBoxLayout()
            top.addWidget(_label(metadata["label"], "cardTitle"), 1)
            badge = _label("CHƯA CÓ", "missingBadge")
            top.addWidget(badge)
            card.addLayout(top)
            masked = _label("(Chưa có)", "muted")
            card.addWidget(masked)
            card.addStretch()
            card.addWidget(_button("Quản lý khóa", lambda checked=False, p=provider: self.edit_keys(p)))
            frame.setMinimumHeight(132)
            grid.addWidget(frame, index // 3, index % 3)
            self.key_cards[provider] = dict(frame=frame, badge=badge, masked=masked)
        section_layout.addLayout(grid)
        layout.addWidget(section)
        speech = QFrame()
        speech.setObjectName("sectionCard")
        speech_layout = QVBoxLayout(speech)
        speech_layout.addWidget(_label("▾ Lồng tiếng", "sectionTitle"))
        speech_layout.addWidget(_label("Ngọc Huyền và Ngọc Huyền V2 dùng mô hình đã cài trên máy. Edge TTS đọc nhiều ngôn ngữ qua Internet.", "muted", True))
        layout.addWidget(speech)
        privacy = _label("Chỉ dùng khóa do bạn nhập. Khóa được lưu bằng Windows DPAPI; dịch AI gửi nội dung phụ đề tới nhà cung cấp đã chọn.", "muted", True)
        layout.addWidget(privacy)
        layout.addStretch()
        return _scroll(body)

    def refresh_keys(self):
        for provider, card in self.key_cards.items():
            try:
                summary = self.provider_store.summary(provider)
                count = int(summary.get("count", 0))
                last4 = str(summary.get("last4", ""))[-4:]
            except Exception:
                count, last4 = 0, ""
            card["badge"].setText(f"{count} KEY" if count else "CHƯA CÓ")
            card["badge"].setObjectName("readyBadge" if count else "missingBadge")
            card["badge"].style().unpolish(card["badge"])
            card["badge"].style().polish(card["badge"])
            card["masked"].setText("••••••" + last4 + (f" + {count - 1} khóa khác" if count > 1 else "") if count else "(Chưa có)")

    def edit_keys(self, provider):
        from providers import PROVIDERS
        dialog = KeysDialog(self, self.provider_store, provider, PROVIDERS[provider]["label"])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_keys()
            self.settings_changed.emit()

    def build_accounts(self):
        from providers import PROVIDERS
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(_label("Dịch và đọc miễn phí", "dialogTitle"))
        layout.addWidget(_label("Google Translate công khai và Edge TTS có thể dùng ngay qua Internet, không cần tài khoản của phần mềm gốc. Dịch bằng Gemini, Groq, DeepSeek hoặc Grok dùng khóa do bạn đăng ký với nhà cung cấp.", "muted", True))
        for metadata in PROVIDERS.values():
            card = QFrame()
            card.setObjectName("resourceCard")
            line = QHBoxLayout(card)
            line.addWidget(_label(metadata["label"]), 1)
            url = metadata.get("keys_url") or metadata.get("docs_url")
            line.addWidget(_button("Mở trang cấp khóa", lambda checked=False, u=url: QDesktopServices.openUrl(QUrl(u))))
            layout.addWidget(card)
        layout.addWidget(_label("Đăng ký và xác thực diễn ra trên trang chính thức. Ứng dụng không đọc tài khoản, mật khẩu hoặc khóa từ phần mềm khác.", "muted", True))
        layout.addStretch()
        return body

    def build_common(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        general = QFrame()
        general.setObjectName("sectionCard")
        form = QFormLayout(general)
        output = QWidget()
        line = QHBoxLayout(output)
        line.setContentsMargins(0, 0, 0, 0)
        value = self.prefs.value("output_dir", "") if self.prefs else ""
        self.output_directory = QLineEdit(str(value or ""))
        self.output_directory.setPlaceholderText("Chọn khi xuất video")
        line.addWidget(self.output_directory, 1)
        line.addWidget(_button("Chọn", self.choose_output))
        line.addWidget(_button("Lưu", self.save_common, True))
        form.addRow("Thư mục xuất", output)
        form.addRow("Dữ liệu mô hình", _label(str(self.resources_root), "muted", True))
        form.addRow("Giao diện", _label("Tối · Xám / xanh cyan"))
        layout.addWidget(general)
        layout.addWidget(_label("Tải dữ liệu offline", "dialogTitle"))
        layout.addWidget(_label("Dung lượng hiển thị là ước tính. Chỉ tài nguyên có đủ tệp trong máy mới được đánh dấu đã cài.", "muted", True))
        grid = QGridLayout()
        grid.setSpacing(14)
        whisper = self.resource_card("whisper", "Tạo phụ đề (Whisper)", "Tạo phụ đề từ giọng nói. Tải một lần; nhận dạng âm thanh chạy trên máy.")
        self.whisper_combo = QComboBox()
        for value, title, size in WHISPER_CHOICES:
            self.whisper_combo.addItem(title + " · " + size, value)
        chosen = self.prefs.value("whisper_model", "base") if self.prefs else "base"
        index = self.whisper_combo.findData(chosen)
        self.whisper_combo.setCurrentIndex(max(0, index))
        self.whisper_combo.currentIndexChanged.connect(self.whisper_changed)
        whisper["body"].addWidget(self.whisper_combo)
        whisper["open"] = _button("Mở thư mục", lambda: _folder(self.resources_root / "whisper"))
        whisper["download"] = _button("Tải mô hình", lambda: self.download_resource("whisper"), True)
        whisper["remove"] = _button("Gỡ", lambda: self.remove_resource("whisper"))
        self.card_actions(whisper, ("open", "download", "remove"))
        grid.addWidget(whisper["frame"], 0, 0)

        ocr = self.resource_card("ocr", "Dò vùng phụ đề", "RapidOCR dùng mô hình dò chữ và nhận dạng Trung / Anh có sẵn trong bộ cài. Tiếng Việt dùng EasyOCR tải khi trích lần đầu.")
        ocr["open"] = _button("Mở thư mục", self.open_ocr_folder)
        self.card_actions(ocr, ("open",))
        grid.addWidget(ocr["frame"], 0, 1)

        speech = self.resource_card("speech", "Ngọc Huyền / Ngọc Huyền V2", "Hai giọng tiếng Việt ngoại tuyến dùng mô hình đã cài trên máy. Edge TTS vẫn có trong bảng chọn giọng.")
        speech["body"].addWidget(_label("Chọn giọng trong Pipeline → Lồng tiếng.", "muted", True))
        speech["open"] = _button("Mở thư mục", lambda: _folder(local_tts.model_root()))
        self.card_actions(speech, ("open",))
        grid.addWidget(speech["frame"], 1, 0)
        kokoro=self.resource_card("kokoro","Giọng đọc ngoại ngữ","Kokoro ngoại tuyến dùng model và bộ 54 giọng đã cài. Chọn ngôn ngữ và giọng trong bảng Giọng đọc.")
        kokoro["open"]=_button("Mở thư mục",lambda:_folder(local_tts.model_root()/"kokoro"))
        self.card_actions(kokoro,("open",))
        grid.addWidget(kokoro["frame"],2,1)

        demucs = self.resource_card("demucs", "Tách giọng", "Demucs tách lời nói và nhạc nền trên máy. Tự dùng bộ GPU NVIDIA đã cài; khi không có GPU, dùng CPU. Mô hình đã cài được dùng lại.")
        self.demucs_device_label = _label("Đang kiểm tra bộ mô hình…", "muted", True)
        demucs["body"].addWidget(self.demucs_device_label)
        demucs["open"] = _button("Mở thư mục", self.open_demucs_folder)
        demucs["download"] = _button("Tải mô hình", lambda: self.download_resource("demucs"), True)
        demucs["remove"] = _button("Gỡ", lambda: self.remove_resource("demucs"))
        self.card_actions(demucs, ("open", "download", "remove"))
        grid.addWidget(demucs["frame"], 1, 1)

        hardware = self.resource_card("hardware", "Tăng tốc GPU", "Tách giọng dùng bộ CUDA NVIDIA đã cài. OCR và giọng ONNX dùng GPU khi runtime hỗ trợ; thanh tiến độ báo thiết bị thực tế. Thiết bị encode điều khiển xuất video.")
        self.hardware_description = _label("Bấm Kiểm tra để xác nhận CUDA trên máy. Whisper vẫn dùng CPU.", "muted", True)
        hardware["body"].addWidget(self.hardware_description)
        hardware["inspect"] = _button("Kiểm tra trên máy", self.inspect_hardware, True)
        self.card_actions(hardware, ("inspect",))
        grid.addWidget(hardware["frame"], 2, 0)
        layout.addLayout(grid)
        self.model_status = _label("Sẵn sàng", "muted", True)
        layout.addWidget(self.model_status)
        self.cancel_download = _button("Dừng tải / kiểm tra", self.cancel_resource)
        self.cancel_download.setEnabled(False)
        layout.addWidget(self.cancel_download)
        layout.addStretch()
        return _scroll(body)

    def resource_card(self, key, title, description):
        frame = QFrame()
        frame.setObjectName("resourceCard")
        frame.setMinimumHeight(225)
        body = QVBoxLayout(frame)
        body.setContentsMargins(16, 15, 16, 14)
        top = QHBoxLayout()
        top.addWidget(_label(title, "cardTitle"), 1)
        badge = _label("CHƯA CÀI", "missingBadge")
        top.addWidget(badge)
        body.addLayout(top)
        body.addWidget(_label(description, "description", True))
        result = dict(frame=frame, body=body, badge=badge)
        self.resource_cards[key] = result
        return result

    def card_actions(self, card, keys):
        card["body"].addStretch()
        line = QHBoxLayout()
        line.addStretch()
        for key in keys:
            line.addWidget(card[key])
        card["body"].addLayout(line)

    @staticmethod
    def _package_directory(module):
        try:
            spec = importlib.util.find_spec(module)
            if spec and spec.submodule_search_locations:
                return Path(next(iter(spec.submodule_search_locations)))
        except (ImportError, ValueError):
            pass
        return None

    def ocr_path(self):
        package = self._package_directory("rapidocr_onnxruntime")
        candidates = [package] if package else []
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "rapidocr_onnxruntime")
        for folder in candidates:
            if folder and folder.is_dir():
                models = list(folder.rglob("*.onnx"))
                if any("det" in path.name and path.stat().st_size > 0 for path in models) and any("rec" in path.name and path.stat().st_size > 0 for path in models):
                    return folder
        return None

    def open_ocr_folder(self):
        folder = self.ocr_path()
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))
        else:
            QMessageBox.information(self, "Chưa có OCR", "Bộ cài chưa có mô hình RapidOCR. Cài thành phần Advanced AI hoặc dùng Whisper để trích phụ đề.")

    def refresh_resources(self):
        selected = self.whisper_combo.currentData()
        installed, folder = whisper_state(self.resources_root, selected)
        self.set_resource_badge("whisper", "ĐÃ CÀI" if installed else "CHƯA CÀI", installed)
        self.resource_cards["whisper"]["remove"].setEnabled(folder.exists() and not self._worker)
        self.resource_cards["whisper"]["download"].setText("Kiểm tra / tải lại" if installed else "Tải mô hình")
        self.resource_cards["whisper"]["download"].setEnabled(self._package_directory("faster_whisper") is not None and not self._worker)
        ocr_installed = bool(self.ocr_path())
        self.set_resource_badge("ocr", "ĐÃ CÀI" if ocr_installed else "CHƯA CÀI", ocr_installed)
        speech_ready = sum(bool(local_tts.availability(provider)["ready"]) for provider in ("ngochuyen","ngochuyen_v2"))
        self.set_resource_badge("speech", "ĐÃ CÀI" if speech_ready == 2 else (f"ĐÃ CÀI {speech_ready}/2" if speech_ready else "CHƯA CÀI"), bool(speech_ready))
        kokoro_ready=bool(local_tts.availability("kokoro")["ready"])
        self.set_resource_badge("kokoro","ĐÃ CÀI" if kokoro_ready else "CHƯA CÀI",kokoro_ready)
        demucs_dir = self.resources_root / "demucs" / "checkpoints"
        # htdemucs uses this exact checkpoint; another checkpoint cannot satisfy it.
        demucs_file = demucs_dir / "955717e8-8726e21a.th"
        pack = gpu_runtime.discover_demucs_pack()
        demucs_installed = bool(pack) or (demucs_file.is_file() and demucs_file.stat().st_size > 0)
        self.set_resource_badge("demucs", "GPU ĐÃ CÀI" if pack else ("ĐÃ CÀI" if demucs_installed else "CHƯA CÀI"), demucs_installed)
        self.demucs_device_label.setText("Tự động · dùng bộ GPU NVIDIA đã cài. Bấm Kiểm tra để xác nhận thiết bị." if pack
                                         else "Tự động · CPU. Chưa có bộ tách giọng GPU.")
        self.resource_cards["demucs"]["remove"].setEnabled(demucs_file.exists() and not self._worker)
        self.resource_cards["demucs"]["download"].setText("Dùng bộ GPU đã cài" if pack else ("Kiểm tra / tải lại" if demucs_installed else "Tải mô hình"))
        self.resource_cards["demucs"]["download"].setEnabled((bool(pack) or self._package_directory("demucs") is not None) and not self._worker)
        self.set_resource_badge("hardware", "KIỂM TRA", False)
        self.resource_cards["hardware"]["inspect"].setEnabled((bool(pack) or self._package_directory("torch") is not None) and not self._worker)

    def open_demucs_folder(self):
        pack = gpu_runtime.discover_demucs_pack()
        _folder(Path(pack["checkpoint"]).parent if pack else self.resources_root / "demucs")

    def set_resource_badge(self, key, text, ready):
        badge = self.resource_cards[key]["badge"]
        badge.setText(text)
        badge.setObjectName("readyBadge" if ready else "missingBadge")
        badge.style().unpolish(badge)
        badge.style().polish(badge)

    def whisper_changed(self, *_):
        if self.prefs:
            self.prefs.setValue("whisper_model", self.whisper_combo.currentData())
        self.refresh_resources()
        self.settings_changed.emit()

    def choose_output(self):
        chosen = QFileDialog.getExistingDirectory(self, "Thư mục xuất video", self.output_directory.text())
        if chosen:
            self.output_directory.setText(chosen)
            self.save_common()

    def save_common(self):
        path = self.output_directory.text().strip()
        if path and not Path(path).is_dir():
            QMessageBox.warning(self, "Thư mục không hợp lệ", "Chọn một thư mục hiện có để xuất video.")
            return
        if self.prefs:
            self.prefs.setValue("output_dir", path)
            self.prefs.sync()
        self.settings_changed.emit()
        self.model_status.setText("Đã lưu cài đặt chung.")

    def download_resource(self, kind):
        selected = self.whisper_combo.currentData() if kind == "whisper" else "htdemucs"
        self._run_job("Tải " + ("Whisper " + selected if kind == "whisper" else "Demucs htdemucs"),
                      lambda progress, cancel: run_resource_job(kind, self.resources_root, selected, progress, cancel),
                      lambda result: self.model_status.setText("Đã tải tài nguyên. Có thể xử lý trên máy."))

    def inspect_hardware(self):
        def done(result):
            text = "PyTorch " + result["torch_version"]
            text += " · CUDA: " + result["device"] if result["cuda"] else " · Không có CUDA khả dụng trong bản PyTorch đang cài."
            self.hardware_description.setText(text)
        self._run_job("Kiểm tra PyTorch / CUDA", lambda progress, cancel: run_resource_job("hardware", self.resources_root, "", progress, cancel), done)

    def _run_job(self, title, function, done):
        if self._worker:
            return
        parent = self.parentWidget()
        if parent and getattr(parent, "active_job", None):
            QMessageBox.information(self, "Đang xử lý", "Chờ tác vụ hiện tại hoàn thành hoặc dừng trước khi tải tài nguyên.")
            return
        self.model_status.setText(title + "…")
        if parent and callable(getattr(parent, "start_job", None)):
            parent.start_job(title, function, done)
            self._worker = getattr(parent, "active_job", None)
            self._owns_worker = False
        else:
            self._worker = _ResourceThread(function, self)
            self._owns_worker = True
            self._worker.result.connect(done)
            self._worker.failed.connect(lambda text: QMessageBox.warning(self, "Tác vụ chưa hoàn tất", text))
        if not self._worker:
            return
        self._worker.progress.connect(lambda value, message: self.model_status.setText(f"{value}% · {message}"))
        self._worker.finished.connect(self._resource_finished)
        self.cancel_download.setEnabled(True)
        self.refresh_resources()
        if self._owns_worker:
            self._worker.start()

    def _resource_finished(self):
        old = self._worker
        self._worker = None
        if self._owns_worker and old:
            old.deleteLater()
        self._owns_worker = False
        self.cancel_download.setEnabled(False)
        self.refresh_resources()

    def cancel_resource(self):
        if self._worker:
            self._worker.cancel_event.set()
            self.cancel_download.setEnabled(False)
            self.model_status.setText("Đang dừng tài nguyên…")

    def remove_resource(self, kind):
        if self._worker:
            return
        if kind == "whisper":
            selected = self.whisper_combo.currentData()
            target = self.resources_root / "whisper" / whisper_repository(selected)
            label = "Whisper " + selected
        elif kind == "demucs":
            target = self.resources_root / "demucs" / "checkpoints" / "955717e8-8726e21a.th"
            label = "Demucs htdemucs"
        else:
            return
        if not target.exists():
            return
        answer = QMessageBox.question(self, "Gỡ mô hình", f"Gỡ {label} khỏi thư mục dữ liệu VideoEditorIndependent? Bạn có thể tải lại khi cần.\n\n{target}",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            remove_own_cache(self.resources_root, target)
        except Exception as exc:
            QMessageBox.warning(self, "Chưa gỡ mô hình", str(exc))
        self.refresh_resources()

    def done(self, code):
        if self._worker:
            if self._owns_worker:
                self.cancel_resource()
                QMessageBox.information(self, "Đang dừng", "Đợi tải / kiểm tra dừng xong rồi đóng cửa sổ.")
                return
            # Parent owns its worker; closing settings does not end the task.
        super().done(code)


class TemplateEditorDialog(QDialog):
    selected = Signal(str, str)

    def __init__(self, parent, store, template):
        super().__init__(parent)
        self.store, self.template = store, dict(template)
        self.setWindowTitle(template["name"])
        self.resize(810, 600)
        self.setStyleSheet(DIALOG_STYLE)
        layout = QVBoxLayout(self)
        self.name_edit = QLineEdit(template["name"])
        layout.addWidget(self.name_edit)
        self.prompt_edit = QPlainTextEdit(template["prompt"])
        layout.addWidget(self.prompt_edit, 1)
        layout.addWidget(_label("Kịch bản được gửi cùng nội dung phụ đề khi bạn dùng nhà cung cấp AI. Google Translate công khai không hỗ trợ kịch bản tùy chỉnh.", "muted", True))
        if template.get("builtin"):
            layout.addWidget(_label("Chỉnh kịch bản mặc định sẽ lưu thành bản riêng trong “Của tôi”.", "muted", True))
        line = QHBoxLayout()
        line.addWidget(_button("Chọn dùng", self.use, True))
        line.addWidget(_button("Lưu bản của tôi" if template.get("builtin") else "Lưu thay đổi", self.save))
        if not template.get("builtin") and template.get("id"):
            line.addWidget(_button("Xóa", self.delete))
        line.addStretch()
        line.addWidget(_button("Đóng", self.reject))
        layout.addLayout(line)

    def save(self):
        name, prompt = self.name_edit.text().strip(), self.prompt_edit.toPlainText().strip()
        if not name or not prompt:
            QMessageBox.warning(self, "Thiếu nội dung", "Nhập tên và nội dung kịch bản.")
            return False
        try:
            saved = self.store.save_template(name, prompt,
                                            template_id=None if self.template.get("builtin") else self.template.get("id") or None,
                                            kind=self.template.get("kind", "translate"))
        except Exception as exc:
            QMessageBox.warning(self, "Chưa lưu kịch bản", str(exc))
            return False
        self.template = dict(saved)
        self.accept()
        return True

    def use(self):
        if self.name_edit.text() != self.template["name"] or self.prompt_edit.toPlainText() != self.template["prompt"]:
            if not self.save():
                return
        self.selected.emit(self.template["id"], self.template.get("kind", "translate"))
        self.accept()

    def delete(self):
        if QMessageBox.question(self, "Xóa kịch bản", "Xóa kịch bản của bạn?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_template(self.template["id"], kind=self.template.get("kind", "translate"))
        self.accept()


class PronunciationEditorDialog(QDialog):
    def __init__(self, parent, store, dictionary=None):
        super().__init__(parent)
        self.store, self.dictionary = store, dictionary
        self.setWindowTitle("Bảng sửa cách đọc")
        self.resize(790, 580)
        self.setStyleSheet(DIALOG_STYLE)
        layout = QVBoxLayout(self)
        self.name_edit = QLineEdit(dictionary["name"] if dictionary else "Bảng mới")
        layout.addWidget(self.name_edit)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Từ / cụm từ", "Đọc thành", "Phân biệt hoa", "Nguyên từ"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        for rule in dictionary.get("rules", []) if dictionary else []:
            self.add_rule(rule)
        line = QHBoxLayout()
        line.addWidget(_button("+ Thêm dòng", lambda: self.add_rule()))
        line.addWidget(_button("Xóa dòng chọn", self.remove_rule))
        line.addStretch()
        line.addWidget(_button("Lưu bảng", self.save, True))
        line.addWidget(_button("Hủy", self.reject))
        layout.addLayout(line)
        layout.addWidget(_label("Chỉ thay cách phát âm trong văn bản lồng tiếng; giữ nguyên nội dung phụ đề hiển thị.", "muted", True))

    def add_rule(self, rule=None):
        rule = rule or {}
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, key in enumerate(("source", "target")):
            self.table.setItem(row, column, QTableWidgetItem(str(rule.get(key, ""))))
        for column, key in ((2, "case_sensitive"), (3, "whole_word")):
            check = QCheckBox()
            check.setChecked(bool(rule.get(key, False)))
            self.table.setCellWidget(row, column, check)

    def remove_rule(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def collect_rules(self):
        rules = []
        for row in range(self.table.rowCount()):
            source = self.table.item(row, 0).text().strip() if self.table.item(row, 0) else ""
            target = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            if not source:
                if target:
                    raise ValueError("Mỗi dòng phải có từ / cụm từ gốc.")
                continue
            rules.append(dict(source=source, target=target,
                              case_sensitive=self.table.cellWidget(row, 2).isChecked(),
                              whole_word=self.table.cellWidget(row, 3).isChecked()))
        return rules

    def save(self):
        try:
            name = self.name_edit.text().strip()
            if not name:
                raise ValueError("Nhập tên bảng sửa cách đọc.")
            self.store.save_pronunciation(name, self.collect_rules(), dictionary_id=self.dictionary["id"] if self.dictionary else None)
        except Exception as exc:
            QMessageBox.warning(self, "Chưa lưu bảng", str(exc))
            return
        self.accept()


class ScenariosDialog(QDialog):
    template_selected = Signal(str, str)
    store_changed = Signal()

    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Kịch bản dịch · VideoEditorIndependent Studio")
        self.resize(960, 750)
        self.setMinimumSize(780, 590)
        self.setStyleSheet(DIALOG_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.addWidget(_label("Kịch bản dịch", "dialogTitle"))
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.template_pages = []
        for kind, builtin, title in (("translate", True, "Dịch – Mặc định"), ("translate", False, "Dịch – Của tôi"),
                                     ("rewrite", True, "Sửa – Mặc định"), ("rewrite", False, "Sửa – Của tôi")):
            body = QWidget()
            page_layout = QVBoxLayout(body)
            page_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            if not builtin:
                page_layout.addWidget(_button("+ Tạo kịch bản mới", lambda checked=False, k=kind: self.new_template(k), True))
            grid_widget = QWidget()
            grid = QGridLayout(grid_widget)
            grid.setSpacing(14)
            grid.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(grid_widget)
            self.template_pages.append(dict(kind=kind, builtin=builtin, grid=grid))
            self.tabs.addTab(_scroll(body), title)
        self.pronunciation_page = QWidget()
        self.pronunciation_layout = QVBoxLayout(self.pronunciation_page)
        self.pronunciation_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.pronunciation_layout.addWidget(_label("Bảng sửa cách đọc cho lồng tiếng", "dialogTitle"))
        self.pronunciation_layout.addWidget(_label("Thay các tên riêng, ký hiệu và cách đọc trong văn bản đưa tới giọng đọc. Chọn bảng trong Pipeline → Lồng tiếng.", "muted", True))
        self.pronunciation_layout.addWidget(_button("+ Tạo bảng cách đọc", self.new_dictionary, True))
        self.dictionary_body = QWidget()
        self.dictionary_layout = QVBoxLayout(self.dictionary_body)
        self.dictionary_layout.setContentsMargins(0, 0, 0, 0)
        self.pronunciation_layout.addWidget(self.dictionary_body)
        self.tabs.addTab(_scroll(self.pronunciation_page), "Cách đọc")
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(_button("Đóng", self.accept))
        outer.addLayout(footer)
        self.refresh()

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                ScenariosDialog.clear_layout(item.layout())

    def refresh(self):
        for page in self.template_pages:
            grid = page["grid"]
            self.clear_layout(grid)
            templates = [item for item in self.store.list_templates(kind=page["kind"]) if bool(item.get("builtin")) == page["builtin"]]
            for index, template in enumerate(templates):
                card = QFrame()
                card.setObjectName("scenarioCard")
                card.setMinimumHeight(144)
                card.setMinimumWidth(220)
                box = QVBoxLayout(card)
                title = template["name"]
                if page["builtin"] and index == 0:
                    title = "★ " + title
                name = _label(title, "cardTitle", True)
                name.setStyleSheet("font-size:15px;")
                box.addWidget(name)
                plain = " ".join(template["prompt"].split())
                box.addWidget(_label(plain[:95] + ("…" if len(plain) > 95 else ""), "muted", True))
                box.addStretch()
                box.addWidget(_button("Xem / chỉnh kịch bản", lambda checked=False, t=dict(template): self.edit_template(t)))
                grid.addWidget(card, index // 3, index % 3)
            if not templates:
                grid.addWidget(_label("Chưa có kịch bản của bạn. Nhấn “Tạo kịch bản mới” hoặc lưu bản sao từ kịch bản mặc định.", "muted", True), 0, 0, 1, 3)
        self.clear_layout(self.dictionary_layout)
        dictionaries = self.store.list_pronunciation()
        for dictionary in dictionaries:
            card = QFrame()
            card.setObjectName("resourceCard")
            row = QHBoxLayout(card)
            row.addWidget(_label(dictionary["name"] + f" · {len(dictionary.get('rules', []))} quy tắc"), 1)
            row.addWidget(_button("Chỉnh sửa", lambda checked=False, d=dict(dictionary): self.edit_dictionary(d)))
            row.addWidget(_button("Xóa", lambda checked=False, d=dict(dictionary): self.delete_dictionary(d)))
            self.dictionary_layout.addWidget(card)
        if not dictionaries:
            self.dictionary_layout.addWidget(_label("Chưa có bảng sửa cách đọc.", "muted"))

    def edit_template(self, template):
        dialog = TemplateEditorDialog(self, self.store, template)
        dialog.selected.connect(self.template_selected.emit)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.store_changed.emit()

    def new_template(self, kind):
        template = dict(id="", name="Kịch bản mới", prompt="", builtin=False, kind=kind)
        dialog = TemplateEditorDialog(self, self.store, template)
        dialog.selected.connect(self.template_selected.emit)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.store_changed.emit()

    def new_dictionary(self):
        self.edit_dictionary(None)

    def edit_dictionary(self, dictionary):
        if PronunciationEditorDialog(self, self.store, dictionary).exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.store_changed.emit()

    def delete_dictionary(self, dictionary):
        if QMessageBox.question(self, "Xóa bảng cách đọc", "Xóa bảng “" + dictionary["name"] + "”?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_pronunciation(dictionary["id"])
        self.refresh()
        self.store_changed.emit()
