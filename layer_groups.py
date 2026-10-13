"""Compact, independently drawn grouped layer controls for the editor sidebar."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize, QRect, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QFontMetrics
from PySide6.QtWidgets import QWidget, QFrame, QLabel, QPushButton, QCheckBox, QHBoxLayout, QVBoxLayout, QLayout, QSizePolicy

GROUP_IDS = ("media", "text", "blur", "filter", "audio")


class FlowLayout(QLayout):
    """Wrap the five add actions to the available logical sidebar width."""
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, apply):
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self.items:
            hint = item.sizeHint()
            width = min(hint.width(), max(1, rect.width()))
            if x > rect.x() and (x + width > rect.right() + 1 or item.widget().property("newRow")):
                x = rect.x()
                y += row_height + self.spacing() + 12
                row_height = 0
            if apply:
                item.setGeometry(QRect(x, y, width, hint.height()))
            x += width + self.spacing()
            row_height = max(row_height, hint.height())
        return max(0, y - rect.y() + row_height)


class LayerAddBar(QWidget):
    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self.setObjectName("layerAddBar")
        self.buttons = {}
        flow = FlowLayout(self, 6)
        for identity, title, callback in actions:
            button = QPushButton(title)
            button.setObjectName("layerAdd_" + identity)
            button.setProperty("newRow",identity == "blur")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(16)
            button.setStyleSheet("QPushButton {background:#00b4d8;color:white;border:0;border-radius:7px;padding:0 14px;font-size:11px;font-weight:600;} QPushButton:hover {background:#20c6df;} QPushButton:disabled {background:#28525a;color:#769499;}")
            button.clicked.connect(callback)
            flow.addWidget(button)
            self.buttons[identity] = button


class LayerSwitch(QCheckBox):
    def __init__(self, checked, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 20)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda value:self.update())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#00b4d8" if self.isChecked() else "#252528"))
        painter.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)
        painter.setBrush(QColor("#ffffff" if self.isChecked() else "#a0a0a0"))
        painter.drawEllipse(QRectF(18 if self.isChecked() else 2, 2, 16, 16))


class DeleteButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Xóa thành phần")
        self.setStyleSheet("QPushButton {background:#252528;border:0;border-radius:5px;padding:0;} QPushButton:hover {background:#493033;}")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#a7b1b3"), 1.05))
        painter.drawRoundedRect(QRectF(8, 8, 10, 12), 1, 1)
        painter.drawLine(QPointF(7, 7), QPointF(19, 7))
        painter.drawRoundedRect(QRectF(10, 5, 6, 2), .5, .5)
        for x in (10.5, 13, 15.5):
            painter.drawLine(QPointF(x, 10), QPointF(x, 18))


class LayerName(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor("#e7e7e7"))
        painter.setFont(self.font())
        text = QFontMetrics(self.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class LayerRow(QFrame):
    picked = Signal(object)
    toggled = Signal(object, bool)
    deleted = Signal(object)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = dict(item)
        self.identity = tuple(item["identity"])
        self.setObjectName("layerRow_" + item["tag"])
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QFrame {background:transparent;border:0;border-radius:5px;}")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)
        tag = QLabel(item["tag"])
        tag.setObjectName("layerTag_" + item["tag"])
        tag.setFixedSize(30, 22)
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setStyleSheet("background:#00b4d8;color:#08191e;border:0;border-radius:4px;font-size:11px;font-weight:700;")
        tag.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(tag)
        name = LayerName(str(item["name"]))
        name.setObjectName("layerName_" + item["tag"])
        name.setStyleSheet("font-size:13px;background:transparent;")
        name.setMinimumWidth(0)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setToolTip(str(item.get("tooltip") or item["name"]))
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(name, 1)
        self.switch = None
        if item.get("toggle", True):
            self.switch = LayerSwitch(bool(item.get("enabled", True)))
            self.switch.setObjectName("layerToggle_" + item["tag"])
            self.switch.setAccessibleName("Bật / tắt " + str(item["name"]))
            self.switch.toggled.connect(lambda enabled:self.toggled.emit(self.identity, enabled))
            row.addWidget(self.switch)
        self.delete_button = None
        if item.get("delete", True):
            self.delete_button = DeleteButton()
            self.delete_button.setObjectName("layerDelete_" + item["tag"])
            self.delete_button.setAccessibleName("Xóa " + str(item["name"]))
            self.delete_button.clicked.connect(lambda:self.deleted.emit(self.identity))
            row.addWidget(self.delete_button)
        chevron = QPushButton("›")
        chevron.setObjectName("layerSelect_" + item["tag"])
        chevron.setFixedSize(13, 24)
        chevron.setStyleSheet("QPushButton {background:transparent;border:0;color:#929292;font-size:18px;padding:0;} QPushButton:hover {color:#00c6df;}")
        chevron.clicked.connect(lambda:self.picked.emit(self.identity))
        row.addWidget(chevron)
        self.chevron = chevron

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.identity)
            event.accept()
        else:
            super().mousePressEvent(event)

    def set_selected(self, selected):
        self.setStyleSheet("QFrame {background:" + ("#2c4c53" if selected else "transparent") + ";border:0;border-radius:5px;}")
        self.chevron.setStyleSheet("QPushButton {background:transparent;border:0;color:" + ("#00c6df" if selected else "#929292") + ";font-size:18px;padding:0;}")


class LayerGroup(QFrame):
    collapsed = Signal(str, bool)

    def __init__(self, identity, title, entries, collapsed=False, parent=None):
        super().__init__(parent)
        self.identity = identity
        self.setObjectName("layerGroup_" + identity)
        self.setStyleSheet("QFrame#layerGroup_" + identity + " {background:#363636;border:0;border-radius:8px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(3)
        self.header = QPushButton()
        self.header.setObjectName("layerGroupHeader_" + identity)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setFixedHeight(25)
        self.header.setStyleSheet("QPushButton {background:transparent;border:0;padding:0;text-align:left;}")
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(2, 0, 2, 0)
        header_row.setSpacing(5)
        self.arrow = QLabel("▸" if collapsed else "▾")
        self.arrow.setFixedWidth(9)
        self.arrow.setStyleSheet("color:#9d9d9d;font-size:10px;background:transparent;")
        title_label = QLabel(title)
        title_label.setStyleSheet("color:#00b4d8;font-size:12px;font-weight:600;background:transparent;")
        count = QLabel(str(len(entries)))
        count.setObjectName("layerGroupCount_" + identity)
        count.setStyleSheet("color:#00b4d8;font-size:11px;background:transparent;")
        for widget in (self.arrow, title_label, count):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header_row.addWidget(self.arrow)
        header_row.addWidget(title_label, 1)
        header_row.addWidget(count)
        layout.addWidget(self.header)
        self.body = QWidget()
        self.body.setStyleSheet("background:transparent;")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        self.rows = [LayerRow(item) for item in entries]
        for row in self.rows:
            body_layout.addWidget(row)
        layout.addWidget(self.body)
        self.body.setVisible(not collapsed)
        self.header.clicked.connect(self.toggle)

    def toggle(self):
        collapsed = not self.body.isHidden()
        self.body.setVisible(not collapsed)
        self.arrow.setText("▸" if collapsed else "▾")
        self.collapsed.emit(self.identity, collapsed)


class GroupedLayerList(QWidget):
    picked = Signal(object)
    toggled = Signal(object, bool)
    deleted = Signal(object)
    collapse_changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("groupedLayerList")
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(14)
        self.collapsed_states = {identity:False for identity in GROUP_IDS}
        self.groups = {}
        self._rows = {}
        self.selected_identity = None

    def set_groups(self, groups, selected=None):
        while self.column.count():
            widget = self.column.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        self.groups = {}
        self._rows = {}
        for data in groups:
            identity = data["id"]
            entries = data.get("entries", [])
            if not entries:
                continue
            group = LayerGroup(identity, data["title"], entries, self.collapsed_states.get(identity, False))
            group.collapsed.connect(self._collapse_changed)
            self.column.addWidget(group)
            self.groups[identity] = group
            for row in group.rows:
                row.picked.connect(self.picked)
                row.toggled.connect(self.toggled)
                row.deleted.connect(self.deleted)
                self._rows[row.identity] = row
        self.select(selected)

    def _collapse_changed(self, identity, collapsed):
        self.collapsed_states[identity] = collapsed
        self.collapse_changed.emit(identity, collapsed)

    def select(self, identity):
        self.selected_identity = tuple(identity) if identity is not None else None
        for key, row in self._rows.items():
            row.set_selected(key == self.selected_identity)

    def row_for(self, identity):
        return self._rows.get(tuple(identity))

    def rows(self):
        return [dict(row.item) for row in self._rows.values()]
