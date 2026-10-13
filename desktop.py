"""Independent desktop presentation for the locally owned video toolset.

The widgets in this module are authored independently. The processing engine
is the existing MediaFusion implementation, exposed through StudioWindow.
"""
from __future__ import annotations

import copy
import json
import math
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QUrl, QSettings, QPointF, QRectF, QSize, QObject, QEvent, QIdentityProxyModel, QAbstractItemModel
from PySide6.QtGui import QAction, QColor, QFont, QPixmap, QPainter, QPen, QIcon, QPolygonF, QPainterPath, QRegion
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFontComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QInputDialog, QLayout,
    QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget,
    QListWidget, QListWidgetItem, QProgressBar, QApplication, QTabBar,
)

import engine
import workflow
import providers
from studio_ui import StudioWindow
from app import time_label, Worker
from dialogs import DialogMixin
from layer_groups import GroupedLayerList, LayerAddBar
from subtitle_detail import SubtitleDetailMixin
try:
    from preview_canvas import EditablePreview
except ImportError:
    EditablePreview = None


APP_TITLE = "Video Editor Independent"
RATIOS = {"Gốc": (0, 0), "9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350), "3:4": (1080, 1440), "4:3": (1440, 1080)}

STYLE = """
QWidget { background:#252527; color:#e7e7e7; font-family:'VE UI'; font-size:13px; }
QMainWindow { background:#161618; }
QLabel { background:transparent; }
QLabel#muted { color:#929292; }
QLabel#title { font-size:18px; font-weight:600; }
QFrame#topbar { background:#252527; border:0; border-radius:12px; }
QFrame#gridPanel { background:#252527; border:0; border-radius:12px; }
QFrame#batchBar { background:#252527; border:0; border-radius:12px; }
QFrame#mediaCard { background:#252525; border:1px solid #343434; border-radius:4px; }
QFrame#mediaCard[current="true"] { border:2px solid #00b4d8; }
QFrame#section { background:#353535; border:0; border-radius:9px; }
QFrame#sidebar { background:#191919; border:1px solid #303030; border-radius:9px; }
QLabel#sectionTitle { font-weight:600; color:#00b4d8; font-size:12px; }
QLabel#emptyTitle { color:#737373; font-size:13px; }
QPushButton,QToolButton { background:#2a2a2a; border:1px solid #404040; border-radius:5px; padding:6px 10px; }
QPushButton:hover,QToolButton:hover { background:#343434; border-color:#626262; }
QPushButton:pressed,QToolButton:pressed { background:#393939; }
QPushButton:disabled { color:#6c6c6c; background:#202020; border-color:#303030; }
QPushButton#primary { background:#00b4d8; border-color:#00b4d8; color:#05171b; font-weight:600; }
QPushButton#primary:hover { background:#2ad3e6; border-color:#2ad3e6; }
QPushButton#primary:disabled {background:#224852;border-color:#2c525c;color:#778b90;}
QPushButton#anchorButton {padding:4px 6px;}
QPushButton#anchorButton:checked {background:#155664;border-color:#00b4d8;color:#aeefff;}
QPushButton#subtle { background:transparent; border:0; padding:8px 9px; }
QPushButton#subtle:hover { background:#303030; }
QPushButton#language { padding:3px 7px; border:0; background:transparent; color:#969696; }
QPushButton#language:checked { background:#05bdd2; color:#071b20; font-weight:600; }
QPushButton#cardAction { padding:4px 8px; font-size:11px; }
QPushButton#danger { color:#ef4444; border-color:#5b3535; }
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QFontComboBox,QTextEdit {
 background:#181818; border:1px solid #3c3c3c; border-radius:4px; padding:5px 6px; selection-background-color:#04798a;
}
QComboBox::drop-down { border:0; width:20px; }
QComboBox::down-arrow {image:url(__DROPDOWN_ICON__);width:10px;height:7px;}
QComboBox QAbstractItemView { background:#232323; border:1px solid #454545; selection-background-color:#087486; padding:4px; }
QSpinBox::up-button,QDoubleSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::down-button { width:15px; border:0; }
QCheckBox { spacing:7px; background:transparent; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #5b5b5b; border-radius:3px; background:#171717; }
QCheckBox::indicator:checked { background:#04bed6; border-color:#04bed6; }
QCheckBox::indicator:hover { border-color:#05bfd6; }
QTabWidget#rightTabs { background:#252527; border:0; border-radius:12px; }
QTabWidget::pane { border:0; background:#252527; }
QTabBar::tab { background:#363636; color:#a0a0a0; border:0; padding:12px 8px; }
QTabBar::tab:selected { color:#00b4d8; background:#363636; border-bottom:2px solid #00b4d8; }
QTabWidget#pipelineSubtabs QTabBar::tab {font-size:11px;padding:0;}
QTabBar#sidebarTabs::tab {height:40px;padding:8px 7px;font-size:11px;}
QTabBar#sidebarTabs {background:#363636;}
QTabWidget#pipelineSubtabs {padding:0;}
QLabel#cardBadge {background:#18523a;color:#79d8a0;border:0;border-radius:3px;font-size:9px;font-weight:600;padding:0 5px;}
QFrame#mediaCardHeader {background:#363636;border:0;}
QFrame#mediaCardFooter {background:#191919;border:0;}
QPushButton#cardAction,QPushButton#mediaPlay {padding:0;border:0;border-radius:4px;background:transparent;font-size:12px;}
QPushButton#cardAction:hover,QPushButton#mediaPlay:hover {background:#343434;}
QComboBox#cardAspectRatio {padding:0 4px;font-size:11px;}
QLabel#ownerBadge {background:#102e23;color:#16cf65;border:1px solid #145437;border-radius:11px;padding:2px 10px;font-size:11px;font-weight:bold;}
QTabBar::tab:hover { color:#e7e7e7; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:#1a1a1a; width:7px; margin:2px; }
QScrollBar::handle:vertical { background:#4c4c4c; border-radius:3px; min-height:24px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { height:7px; background:#1a1a1a; }
QScrollBar::handle:horizontal { background:#4c4c4c; border-radius:3px; min-width:20px; }
QSlider::groove:horizontal { height:3px; background:#444444; border-radius:1px; }
QSlider::handle:horizontal { background:#0ac5da; width:9px; margin:-3px 0; border-radius:4px; }
QProgressBar { background:#242424; border:0; border-radius:3px; height:13px; text-align:center; font-size:10px; }
QProgressBar::chunk { background:#07bfd4; border-radius:3px; }
QListWidget,QTableWidget { background:#191919; border:1px solid #363636; border-radius:5px; alternate-background-color:#222222; }
QListWidget::item { padding:7px; }
QListWidget::item:selected { background:#16474e; color:#eefbff; }
QHeaderView::section { background:#2a2a2a; padding:6px; border:0; }
QMenu { background:#252525; border:1px solid #494949; }
QMenu::item { padding:7px 24px; }
QMenu::item:selected { background:#165962; }
QStatusBar { color:#909090; background:#0e0e0e; font-size:11px; }
QToolTip { background:#292929; color:#ededed; border:1px solid #555555; padding:6px; }
QPushButton#topAction {background:transparent;border:1px solid #393939;border-radius:6px;padding:2px 16px;}
QPushButton#topAction:hover {background:#343434;border-color:#626262;}
QToolButton#projectMenu {background:#f04448;border:0;border-radius:6px;color:white;font-weight:bold;padding:3px 14px;}
QPushButton#addVideo {background:#19c65a;border:0;color:white;font-weight:bold;}
QPushButton#addVideo:hover {background:#20d969;}
QPushButton#runTranslate {background:#3466ae;border:0;}
QPushButton#runDub,QPushButton#primary {background:#008ca7;border:0;color:white;}
QPushButton#runRecognize:disabled {background:#2b2b2d;color:#626264;border-color:#2b2b2d;}
QPushButton#runTranslate:disabled {background:#2e4e7b;color:#777b80;}
QPushButton#runDub:disabled,QPushButton#primary:disabled {background:#155c6b;color:#73777a;}
QPushButton#stopBatch {background:transparent;border:1px solid #393939;color:#eeeeee;}
QPushButton#stopBatch:disabled {color:#626264;}
QFrame#batchBar QPushButton {padding:2px 12px;border-radius:6px;min-height:20px;}
QTabWidget#pipelineSubtabs QComboBox,QTabWidget#pipelineSubtabs QLineEdit {background:#252527;border:0;border-radius:8px;padding:3px 9px;min-height:23px;font-size:13px;}
QTabWidget#pipelineSubtabs QComboBox::drop-down {width:22px;}
QLabel#pipelineFieldLabel {color:#a0a0a0;font-size:13px;}
QPushButton[sectionHeading=true] {text-align:left;background:transparent;border:0;padding:0;color:#00b4d8;font-size:13px;font-weight:bold;}
QLabel#pipelineHelp {color:#92999e;background:#303436;border-radius:7px;font-size:11px;}

"""


def _button(text, function, name="", primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setObjectName("primary" if primary else name)
    widget.clicked.connect(function)
    return widget


def line_icon(kind, color="#cccccc", size=18):
    """Small locally drawn UI symbols that do not depend on emoji fonts."""
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 18, size / 18)
    painter.setPen(QPen(QColor(color), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if kind == "settings":
        painter.setPen(QPen(QColor("#101012"),1.1))
        painter.setBrush(QColor(color if color=="#00b4d8" else "#dedede"))
        points=[]
        for index in range(32):
            angle=2*math.pi*index/32
            radius=7.5 if index%4 in (0,1) else 6
            points.append(QPointF(9+radius*math.cos(angle),9+radius*math.sin(angle)))
        painter.drawPolygon(QPolygonF(points))
        painter.setBrush(QColor("#363636"))
        painter.drawEllipse(QRectF(6.5,6.5,5,5))
    elif kind == "prompt":
        painter.drawRoundedRect(QRectF(4, 2, 10, 14), 1, 1)
        for y in (6, 9, 12):
            painter.drawLine(QPointF(6, y), QPointF(12, y))
    elif kind == "tools":
        painter.drawRoundedRect(QRectF(2, 6, 14, 10), 1, 1)
        painter.drawRoundedRect(QRectF(6, 3, 6, 3), 1, 1)
        painter.drawLine(QPointF(2, 10), QPointF(16, 10))
        painter.drawLine(QPointF(9, 9), QPointF(9, 12))
    elif kind == "download":
        painter.drawLine(QPointF(9, 2), QPointF(9, 11))
        painter.drawLine(QPointF(5, 7), QPointF(9, 11))
        painter.drawLine(QPointF(13, 7), QPointF(9, 11))
        painter.drawLine(QPointF(3, 12), QPointF(3, 16))
        painter.drawLine(QPointF(3, 16), QPointF(15, 16))
        painter.drawLine(QPointF(15, 12), QPointF(15, 16))
    elif kind == "folder":
        painter.setPen(QPen(QColor("#101012"), 1.1))
        painter.setBrush(QColor("#3694b7"))
        painter.drawRoundedRect(QRectF(2.2, 3, 4.5, 4), 1, 1)
        painter.setBrush(QColor("#f3b931"))
        painter.drawPolygon(QPolygonF([QPointF(2, 5), QPointF(8, 5), QPointF(9, 6), QPointF(16, 6), QPointF(15, 15), QPointF(2, 15)]))
        painter.setBrush(QColor("#ffda70"))
        painter.drawPolygon(QPolygonF([QPointF(4, 6), QPointF(17, 6), QPointF(14.8, 15), QPointF(2, 15)]))
    elif kind == "add":
        painter.drawLine(QPointF(9, 3), QPointF(9, 15))
        painter.drawLine(QPointF(3, 9), QPointF(15, 9))
    elif kind == "play":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([QPointF(5, 3), QPointF(15, 9), QPointF(5, 15)]))
    elif kind == "stop":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(4, 4, 10, 10), 1, 1)
    elif kind == "menu":
        for y in (4, 9, 14):
            painter.drawLine(QPointF(3, y), QPointF(15, y))
    elif kind == "layers":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(9,1),QPointF(11.5,6.5),QPointF(17,9),QPointF(11.5,11.5),QPointF(9,17),QPointF(6.5,11.5),QPointF(1,9),QPointF(6.5,6.5)]))
    elif kind == "tuning":
        painter.setPen(QPen(QColor("#161618"),1))
        painter.setBrush(QColor("#f3f3f3"))
        painter.drawPolygon(QPolygonF([QPointF(9,1),QPointF(15,3),QPointF(14,10),QPointF(11.5,14.5),QPointF(9,17),QPointF(6.5,14.5),QPointF(4,10),QPointF(3,3)]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#df3b43"))
        painter.drawPolygon(QPolygonF([QPointF(9,3),QPointF(12.8,4),QPointF(12,6),QPointF(10,6),QPointF(12,8),QPointF(10,9),QPointF(11,11),QPointF(9,14)]))
    painter.end()
    return QIcon(canvas)


class DisplayLabels(QIdentityProxyModel):
    """Translate presentation labels without changing stored encoder/language IDs."""
    def __init__(self, source, labels, parent):
        super().__init__(parent)
        self.labels = labels
        self.setSourceModel(source)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        value = super().data(index, role)
        if role == Qt.ItemDataRole.DisplayRole:
            return self.labels.get(value, value)
        return value

    def match(self, start, role, value, hits=1, flags=Qt.MatchFlag.MatchStartsWith | Qt.MatchFlag.MatchWrap):
        # QIdentityProxyModel delegates matching to the untranslated source.
        # Use the base algorithm so QComboBox.findText sees displayed labels.
        return QAbstractItemModel.match(self, start, role, value, hits, flags)


class ControlStateBinding(QObject):
    """Keep a sidebar control consistent with its full editor counterpart."""
    def __init__(self, source, control):
        super().__init__(control)
        self.source = source
        self.control = control
        source.installEventFilter(self)
        self.sync()

    def sync(self):
        self.control.setEnabled(self.source.isEnabled())
        self.control.setToolTip(self.source.toolTip())

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.ToolTipChange):
            self.sync()
        return False


class ReferenceSwitch(QCheckBox):
    """Compact 36 by 20 switch used by the reference's section headers."""
    def __init__(self, source, name, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setFixedSize(36, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(source.text() or name)
        self.setChecked(source.isChecked())
        self.toggled.connect(source.setChecked)
        source.toggled.connect(self.setChecked)
        self.toggled.connect(lambda _checked: self.update())
        self.binding = ControlStateBinding(source, self)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.4)
        color = QColor("#00b4d8" if self.isChecked() else "#252527")
        painter.setPen(QPen(color if self.isChecked() else QColor("#404040"), 1))
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(.5, .5, 35, 19), 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff" if self.isChecked() else "#929292"))
        painter.drawEllipse(QRectF(18 if self.isChecked() else 2, 2, 16, 16))
        painter.end()


class SidebarTabBar(QTabBar):
    """Three equal sidebar tabs with independently drawn icon/caption chrome."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setExpanding(False)
        self.setUsesScrollButtons(False)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setDrawBase(False)
        self.setFixedHeight(56)

    def tabSizeHint(self,index):
        font = QFont(self.font())
        font.setPixelSize(11)
        from PySide6.QtGui import QFontMetrics
        return QSize(max(72, QFontMetrics(font).horizontalAdvance(self.tabText(index))+20),56)

    def minimumTabSizeHint(self,index):
        return self.tabSizeHint(index)

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(),QColor("#363636"))
        font = self.font()
        font.setPixelSize(11)
        painter.setFont(font)
        for index in range(self.count()):
            bounds = self.tabRect(index)
            active = index == self.currentIndex()
            color = "#00b4d8" if active else "#a0a0a0"
            font.setBold(active)
            painter.setFont(font)
            icon = line_icon(["settings","layers","tuning"][index],color,17)
            painter.drawPixmap(round(bounds.center().x()-8),9,icon.pixmap(17,17))
            painter.setPen(QColor(color))
            painter.drawText(QRectF(bounds.x()+1,31,bounds.width()-2,18),Qt.AlignmentFlag.AlignCenter,self.tabText(index))
            if active:
                painter.fillRect(round(bounds.center().x()-12),54,24,2,QColor("#00b4d8"))
        painter.end()


class RoundedSidebar(QTabWidget):
    """Clip child panes to the sidebar's rounded lower corners."""
    def resizeEvent(self, event):
        super().resizeEvent(event)
        outline = QPainterPath()
        width, height, radius = self.width(), self.height(), 12
        outline.moveTo(0, 0)
        outline.lineTo(width, 0)
        outline.lineTo(width, height-radius)
        outline.quadTo(width, height, width-radius, height)
        outline.lineTo(radius, height)
        outline.quadTo(0, height, 0, height-radius)
        outline.closeSubpath()
        self.setMask(QRegion(outline.toFillPolygon().toPolygon()))


class SegmentedTabBar(QTabBar):
    """A 32 px segmented selector with the surrounding 10 px pane spacing."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setExpanding(True)
        self.setUsesScrollButtons(False)
        self.setDrawBase(False)
        self.setFixedHeight(52)

    def tabSizeHint(self, index):
        return QSize(75,52)

    def minimumTabSizeHint(self, index):
        return self.tabSizeHint(index)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(10,10,max(1,self.width()-20),32)
        painter.setPen(QPen(QColor("#303030"),1))
        painter.setBrush(QColor("#161618"))
        painter.drawRoundedRect(box,4,4)
        width = (box.width()-6)/max(1,self.count())
        for index in range(self.count()):
            rect = QRectF(13+index*width,13,width,26)
            active = index == self.currentIndex()
            if active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#252527" if self.parentWidget() and self.parentWidget().objectName()=="componentViews" else "#363636"))
                painter.drawRoundedRect(rect,3,3)
            font = QFont(self.font())
            font.setPixelSize(13)
            font.setBold(active)
            painter.setFont(font)
            painter.setPen(QColor("#e7e7e7" if active else "#929292"))
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,self.tabText(index))
        painter.end()

    def mousePressEvent(self,event):
        if event.button() == Qt.MouseButton.LeftButton and 10 <= event.position().y() <= 42:
            index = int((event.position().x()-10)*self.count()/max(1,self.width()-20))
            if 0 <= index < self.count():
                self.setCurrentIndex(index)
            event.accept()
            return
        super().mousePressEvent(event)


class ElidedLabel(QLabel):
    def __init__(self, text="", parent=None):
        self.full_text = text
        super().__init__(text,parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        super().setText(self.fontMetrics().elidedText(self.full_text,Qt.TextElideMode.ElideMiddle,max(1,self.width())))

    def setText(self,text):
        self.full_text = str(text)
        super().setText(self.fontMetrics().elidedText(self.full_text,Qt.TextElideMode.ElideMiddle,max(1,self.width())))


class ReferenceSlider(QSlider):
    """Compact round slider, with the usual keyboard and accessibility values."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        start, end = 7, max(7,self.width()-7)
        center = self.height()/2
        fraction = (self.value()-self.minimum())/max(1,self.maximum()-self.minimum())
        position = start+(end-start)*fraction
        painter.setPen(QPen(QColor("#28636c"),4,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(start,center),QPointF(end,center))
        painter.setPen(QPen(QColor("#00b4ce"),4,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(start,center),QPointF(position,center))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#00b4ce"))
        painter.drawEllipse(QPointF(position,center),6.5,6.5)

    def _point_value(self, event):
        fraction = min(1,max(0,(event.position().x()-7)/max(1,self.width()-14)))
        self.setValue(round(self.minimum()+fraction*(self.maximum()-self.minimum())))

    def mousePressEvent(self,event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._point_value(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._point_value(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)


class MediaCard(QFrame):
    """A selectable thumbnail with its own on-demand native video preview."""
    selected = Signal(str, bool)
    activated = Signal(str)
    removed = Signal(str)
    edit_subtitles = Signal(str)
    more_requested = Signal(str, object)
    ratio_changed = Signal(str, str)
    apply_requested = Signal(str)
    fullscreen_requested = Signal(str, object)
    preview_changed = Signal(str, str, int, dict)
    preview_chosen = Signal(str, str, int)

    def __init__(self, media, number, thumbnail, assets, checked=False, parent=None, settings=None):
        super().__init__(parent)
        self.media = media
        self.path = media["path"]
        self.thumbnail = thumbnail
        self.settings_snapshot = copy.deepcopy(settings or {})
        self.assets_snapshot = copy.deepcopy(assets)
        self.media_player = None
        self.audio_output = None
        self.video_sink = None
        self.fullscreen_preview = None
        self.setObjectName("mediaCard")
        self.setProperty("selected", checked)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(160)
        column = QVBoxLayout(self)
        column.setContentsMargins(1, 1, 1, 1)
        column.setSpacing(0)
        column.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        self.header = QFrame()
        self.header.setObjectName("mediaCardHeader")
        self.header.setFixedHeight(44)
        head = QHBoxLayout(self.header)
        head.setContentsMargins(6,0,4,0)
        head.setSpacing(4)
        head.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.check = QCheckBox()
        self.check.setObjectName("mediaSelect")
        self.check.setChecked(checked)
        self.check.toggled.connect(self._selection_changed)
        self.check.setFixedSize(22,22)
        head.addWidget(self.check)
        self.name = ElidedLabel(media['name'])
        self.name.setStyleSheet("font-size:12px;font-weight:600;")
        self.name.setToolTip(self.path)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self.name, 1)
        for key, name in [("original_subtitle", "Gốc"), ("translated_subtitle", "Dịch"), ("narration", "Giọng")]:
            if assets.get(key):
                badge = QLabel(name)
                badge.setObjectName("cardBadge")
                badge.setFixedHeight(18)
                if key == "original_subtitle":
                    badge.setStyleSheet("background:#173544;color:#62b9ed;")
                elif key == "translated_subtitle":
                    badge.setStyleSheet("background:#47371e;color:#e6b75e;")
                head.addWidget(badge)
        self.stage_status = QLabel("Đã xuất" if assets.get("video") else "")
        self.stage_status.setObjectName("cardStageStatus")
        self.stage_status.setStyleSheet("font-size:11px;color:#79d8a0;font-weight:600;")
        self.stage_status.setMaximumWidth(110)
        self.stage_status.setVisible(bool(assets.get("video")))
        head.addWidget(self.stage_status)
        self.stage_progress = QProgressBar()
        self.stage_progress.setObjectName("cardStageProgress")
        self.stage_progress.setFixedSize(60,4)
        self.stage_progress.setTextVisible(False)
        self.stage_progress.setVisible(False)
        head.addWidget(self.stage_progress)
        self.stage_percent = QLabel()
        self.stage_percent.setStyleSheet("font-size:10px;color:#929292;")
        self.stage_percent.hide()
        head.addWidget(self.stage_percent)
        remove = _button("×", lambda: self.removed.emit(self.path), "cardAction")
        remove.setFixedSize(24, 24)
        remove.setToolTip("Bỏ video khỏi dự án")
        head.addWidget(remove)
        column.addWidget(self.header)

        self.preview = QStackedWidget()
        self.preview.setObjectName("mediaPreview")
        self.poster = None
        if EditablePreview is not None:
            self.poster = EditablePreview(media, thumbnail, self.settings_snapshot, assets)
            self.poster.changed.connect(lambda kind, index, data: self.preview_changed.emit(self.path, kind, index, data))
            self.poster.chosen.connect(lambda kind, index: self.preview_chosen.emit(self.path, kind, index))
            self.poster.activated.connect(lambda: self.activated.emit(self.path))
            self.image = self.poster
        else:
            self.image = QLabel()
            self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.image.setStyleSheet("background:#090909; color:#565656; font-size:34px; border-radius:4px;")
        self.preview.addWidget(self.image)
        self.video = QVideoWidget()
        self.preview.addWidget(self.video)
        column.addWidget(self.preview)
        self.update_thumbnail()

        seek_strip = QWidget()
        seek_strip.setFixedHeight(12)
        seek_layout = QHBoxLayout(seek_strip)
        seek_layout.setContentsMargins(8,0,8,0)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setObjectName("mediaSeek")
        self.seek.setRange(0, round(media["duration"] * 1000))
        self.seek.sliderMoved.connect(self.seek_to)
        self.seek.setFixedHeight(12)
        seek_layout.addWidget(self.seek)
        column.addWidget(seek_strip)

        self.footer = QFrame()
        self.footer.setObjectName("mediaCardFooter")
        self.footer.setFixedHeight(40)
        transport = QHBoxLayout(self.footer)
        transport.setContentsMargins(8,0,8,0)
        transport.setSpacing(6)
        transport.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.ratio = QComboBox()
        self.ratio.setObjectName("cardAspectRatio")
        self.ratio.addItems(list(RATIOS))
        self.ratio.setFixedSize(80,26)
        size = (self.settings_snapshot.get("width", 0), self.settings_snapshot.get("height", 0))
        self.ratio.setCurrentText(next((label for label, dimensions in RATIOS.items() if dimensions == size), "Gốc"))
        self.ratio.currentTextChanged.connect(lambda ratio: self.ratio_changed.emit(self.path, ratio))
        transport.addWidget(self.ratio)
        transport.addStretch()
        backward = _button("⏪",lambda:self._skip(-5000),"cardAction")
        backward.setFixedSize(26,26)
        backward.setToolTip("Lùi 5 giây")
        transport.addWidget(backward)
        self.play = _button("▶", self.toggle_play, "cardAction")
        self.play.setObjectName("mediaPlay")
        self.play.setFixedSize(26,26)
        self.play.setToolTip("Phát / tạm dừng video nguồn")
        transport.addWidget(self.play)
        forward = _button("⏩",lambda:self._skip(5000),"cardAction")
        forward.setFixedSize(26,26)
        forward.setToolTip("Tiến 5 giây")
        transport.addWidget(forward)
        self.clock = QLabel(f"00:00 / {self._short_time(media['duration'])}")
        self.clock.setStyleSheet("font-family:Consolas;font-size:10px;color:#929292;")
        transport.addWidget(self.clock)
        transport.addStretch()
        fullscreen = _button("⛶",lambda:self.fullscreen_requested.emit(self.path,self),"cardAction")
        fullscreen.setObjectName("mediaFullscreen")
        fullscreen.setFixedSize(26,26)
        fullscreen.setToolTip("Xem toàn màn hình")
        transport.addWidget(fullscreen)
        apply = _button("📋", lambda: self.apply_requested.emit(self.path), "cardAction")
        apply.setToolTip("Áp dụng style cho tất cả video")
        apply.setFixedSize(26,26)
        transport.addWidget(apply)
        subtitle = _button("Phụ đề", lambda: self.edit_subtitles.emit(self.path), "cardAction")
        subtitle.setObjectName("mediaSubtitles")
        subtitle.setParent(self)
        subtitle.hide()
        more = _button("•••", lambda: self.more_requested.emit(self.path, self), "cardAction")
        more.setToolTip("Cắt ghép, hiệu ứng và thao tác video")
        more.setParent(self)
        more.hide()
        self.setToolTip(f"{media['name']}\n{media['width']} × {media['height']}\nChuột phải để mở thao tác video / phụ đề.")
        column.addWidget(self.footer)

    @staticmethod
    def _short_time(value):
        seconds = max(0, int(value))
        if seconds >= 3600:
            return f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}"
        return f"{seconds // 60:02}:{seconds % 60:02}"

    def _skip(self, milliseconds):
        player = self.ensure_player()
        duration = player.duration() or round(self.media["duration"]*1000)
        self.seek_to(max(0,min(duration,player.position()+milliseconds)))

    def _selection_changed(self, checked):
        self.setProperty("selected", checked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.selected.emit(self.path, checked)

    def update_thumbnail(self):
        if self.poster:
            self.poster.set_content(self.thumbnail, self.settings_snapshot, self.assets_snapshot)
            return
        if Path(self.thumbnail).is_file():
            self.pixmap = QPixmap(str(self.thumbnail))
        else:
            self.pixmap = QPixmap()
        self._fit_preview()

    def _fit_preview(self):
        width = max(160, self.width() - 2)
        self.preview.setFixedHeight(round(width * 9 / 16))
        self.setFixedHeight(self.preview.height()+44+12+40+2)
        if self.poster:
            return
        if hasattr(self, "pixmap") and not self.pixmap.isNull():
            self.image.setPixmap(self.pixmap.scaled(width, self.preview.height(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.image.setPixmap(line_icon("play", "#555555", 40).pixmap(40, 40))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_preview()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.path)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.activated.emit(self.path)
        self.toggle_play()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        self.more_requested.emit(self.path, self)
        event.accept()

    def set_content(self, settings, assets):
        self.settings_snapshot = copy.deepcopy(settings)
        self.assets_snapshot = copy.deepcopy(assets)
        if self.poster:
            self.poster.set_content(self.thumbnail, settings, assets)
        self.ratio.blockSignals(True)
        size = (settings.get("width", 0), settings.get("height", 0))
        self.ratio.setCurrentText(next((label for label, dimensions in RATIOS.items() if dimensions == size), "Gốc"))
        self.ratio.blockSignals(False)

    def ensure_player(self):
        if self.media_player is None:
            self.media_player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(.7)
            self.media_player.setAudioOutput(self.audio_output)
            if self.poster:
                self.video_sink = QVideoSink(self)
                self.video_sink.videoFrameChanged.connect(self._video_frame)
                self.media_player.setVideoOutput(self.video_sink)
            else:
                self.media_player.setVideoOutput(self.video)
            self.media_player.setSource(QUrl.fromLocalFile(self.path))
            self.media_player.positionChanged.connect(self._position)
            self.media_player.playbackStateChanged.connect(lambda state: self.play.setText("Ⅱ" if state == QMediaPlayer.PlaybackState.PlayingState else "▶"))
            self.media_player.errorOccurred.connect(lambda *_: self.image.setToolTip(self.media_player.errorString()))
        return self.media_player

    def _video_frame(self, frame):
        image = frame.toImage()
        if not image.isNull():
            self.poster.set_frame(image)
            if self.fullscreen_preview:
                self.fullscreen_preview.set_frame(image)

    def toggle_play(self):
        self.activated.emit(self.path)
        player = self.ensure_player()
        self.preview.setCurrentIndex(0 if self.poster else 1)
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        else:
            player.play()

    def seek_to(self, position):
        self.ensure_player().setPosition(position)
        self.preview.setCurrentIndex(0 if self.poster else 1)

    def _position(self, value):
        if not self.seek.isSliderDown():
            self.seek.setValue(value)
        self.clock.setText(f"{self._short_time(value / 1000)} / {self._short_time(self.media['duration'])}")
        if self.poster:
            self.poster.set_position(value / 1000)

    def stop(self):
        if self.media_player:
            self.media_player.stop()


class IndependentWindow(SubtitleDetailMixin, DialogMixin, StudioWindow):
    """Real editing application with a batch-first desktop workspace."""

    def __init__(self, testing=False):
        from ui_theme import load_ui_fonts, ui_font_family
        load_ui_fonts()
        self._shell_ready = False
        self._selected_paths = set()
        self._page_number = 0
        self.cards = []
        self._language = "vi"
        self._translated_widgets = []
        self._tool_dialogs = {}
        self._close_requested = False
        self._closing_workers = set()
        self._canvas_size = None
        self._inline_refreshing = False
        self._property_identity = None
        self._batch_errors = {}
        self._running_sources = []
        super().__init__(testing=testing)
        from ui_theme import font_directory
        dropdown_icon = (font_directory().parent / "ui_icons" / "chevron-down.svg").as_posix()
        self.setStyleSheet(STYLE.replace("'VE UI'", "'" + ui_font_family() + "'").replace("__DROPDOWN_ICON__", '"' + dropdown_icon + '"'))
        self.setWindowTitle(APP_TITLE)
        self.resize(1600, 920)
        self.setMinimumSize(1120, 690)
        self._dialog_stores()
        self._restore_translation_controls()
        self.refresh_scenarios()
        self.set_language(str(self.prefs.value("ui_language", "vi")))
        self.render_library()
        self.refresh_voice_display()
        self._refresh_job_status()

    def build(self):
        # Keep the owned editor's full tools available as secondary views.
        # All visible batch chrome, forms and cards are independently authored.
        StudioWindow.build(self)
        self._init_audio_reference()
        self._legacy_root = self.takeCentralWidget()
        self._legacy_root.hide()
        self.pages.setParent(None)
        for toolbar in self.findChildren(QWidget):
            if toolbar.metaObject().className() == "QToolBar":
                toolbar.hide()
        for action in self.findChildren(QAction):
            action.setShortcut("")
        self.menuBar().clear()
        self.menuBar().hide()

        shell = QWidget()
        shell.setObjectName("independentShell")
        shell.setStyleSheet("QWidget#independentShell {background:#161618;}")
        column = QVBoxLayout(shell)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(8)
        column.addWidget(self._build_topbar())
        self.main_views = QStackedWidget()
        self.workspace = self._build_workspace()
        self.main_views.addWidget(self.workspace)
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        back = _button("‹  Trở về danh sách video", lambda: self.navigate(6), "subtle")
        advanced_layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        advanced_layout.addWidget(self.pages, 1)
        self.main_views.addWidget(advanced)
        column.addWidget(self.main_views, 1)
        column.addWidget(self._build_bottom_bar())
        self.setCentralWidget(shell)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().hide()
        self.setAcceptDrops(True)
        self._shell_ready = True
        self.resolution.currentIndexChanged.connect(self._resolution_changed)
        self.navigate(6)
        self._job_timer = QTimer(self)
        self._job_timer.setInterval(200)
        self._job_timer.timeout.connect(self._refresh_job_status)
        self._job_timer.start()

    def _translated(self, widget, vietnamese, english):
        widget.setText(vietnamese)
        self._translated_widgets.append((widget, vietnamese, english))
        return widget

    def _build_topbar(self):
        frame = QFrame()
        frame.setObjectName("topbar")
        frame.setFixedHeight(48)
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(6)
        actions = [
            ("Thiết Lập", "Settings", self.open_settings, "openSettings", "settings"),
            ("Kịch bản", "Scenarios", self.open_scenarios, "openPrompts", "prompt"),
            ("Cắt ghép", "Cut / join", self.open_tools, "openMiniTools", "tools"),
            ("Tải xuống", "Downloads", self.open_downloads, "openDownloads", "download"),
            ("Điều khoản", "Terms", self.show_owner, "openTerms", "prompt"),
        ]
        for vi, en, function, name, icon in actions:
            button = _button(vi, function, "topAction")
            button.setFixedHeight(25)
            button.setMinimumWidth(121)
            button.setIcon(line_icon(icon))
            button.setProperty("qaName", name)
            button.setAccessibleName(name)
            row.addWidget(self._translated(button, vi, en))
        row.addStretch()
        version = QLabel("v1.3.2")
        version.setObjectName("muted")
        version.setStyleSheet("font-size:11px;color:#00b4d8;font-weight:bold;")
        version.setToolTip(APP_TITLE)
        row.addWidget(version)
        language = QFrame()
        language.setFixedSize(64,28)
        language.setStyleSheet("QFrame {background:#222222;border:1px solid #3b3b3b;border-radius:4px;}")
        languages = QHBoxLayout(language)
        languages.setContentsMargins(2, 2, 2, 2)
        languages.setSpacing(0)
        self.vi_button = _button("VI", lambda: self.set_language("vi"), "language")
        self.en_button = _button("EN", lambda: self.set_language("en"), "language")
        for button in (self.vi_button, self.en_button):
            button.setCheckable(True)
            languages.addWidget(button)
        self.vi_button.setChecked(True)
        row.addWidget(language)
        owner = QLabel("CHỦ SỞ HỮU")
        owner.setObjectName("ownerBadge")
        owner.setFixedHeight(23)
        owner.setToolTip("Quyền chủ sở hữu trên máy cục bộ")
        row.addWidget(owner)
        menu_button = QToolButton()
        menu_button.setObjectName("projectMenu")
        menu_button.setFixedHeight(25)
        menu_button.setText("Tài khoản")
        menu_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        menu_button.setToolTip("Dự án và công cụ")
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        for title, shortcut, function in [
            ("Dự án mới", "Ctrl+N", self.new_project),
            ("Mở dự án…", "Ctrl+O", self.open_project),
            ("Lưu dự án…", "Ctrl+S", self.save_project),
            ("Nhập video…", "Ctrl+I", self.import_files),
        ]:
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(function)
            self.addAction(action)
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Tìm video…", self._open_video_search)
        menu.addAction("Chọn tất cả video",lambda:self.select_all.setChecked(True))
        menu.addAction("Bỏ chọn tất cả",self._select_none)
        menu.addSeparator()
        for title, target in [("Cắt ghép video", 2), ("Thành phần và hiệu ứng", 7), ("Biên tập phụ đề", 3), ("OCR và tách âm thanh", 8), ("Thiết lập xuất", 9), ("Lịch sử tác vụ", 4), ("Tải video từ URL", 1)]:
            menu.addAction(title, lambda checked=False, ix=target: self.navigate(ix))
        menu.addSeparator()
        if hasattr(self,"open_gpu_performance"):
            menu.addAction("Hiệu năng GPU…",self.open_gpu_performance)
        menu.addAction("Chủ sở hữu · Máy cục bộ", self.show_owner)
        menu_button.setMenu(menu)
        row.addWidget(menu_button)
        return frame

    def _build_workspace(self):
        workspace = QWidget()
        workspace.setObjectName("workspace")
        workspace.setStyleSheet("QWidget#workspace {background:#161618;}")
        layout = QHBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.grid_panel = QFrame()
        self.grid_panel.setObjectName("gridPanel")
        center = QVBoxLayout(self.grid_panel)
        center.setContentsMargins(10, 10, 10, 10)
        center.setSpacing(0)
        self.grid_options = QWidget(self.grid_panel)
        options = QHBoxLayout(self.grid_options)
        options.setSpacing(8)
        self.select_all = QCheckBox("Chọn tất cả")
        self.select_all.setObjectName("selectAllVideos")
        self.select_all.toggled.connect(self._select_all_changed)
        options.addWidget(self._translated(self.select_all, "Chọn tất cả", "Select all"))
        self.selected_count = QLabel("0 video")
        self.selected_count.setObjectName("muted")
        options.addWidget(self.selected_count)
        options.addStretch()
        self.search_box = QLineEdit()
        self.search_box.setObjectName("videoSearch")
        self.search_box.setPlaceholderText("Tìm video…")
        self.search_box.setMaximumWidth(190)
        self.search_box.textChanged.connect(self._search_changed)
        options.addWidget(self.search_box)
        self.grid_options.hide()

        self.grid_views = QStackedWidget()
        empty = QWidget()
        empty_column = QVBoxLayout(empty)
        empty_column.addStretch()
        glyph = QLabel()
        glyph.setPixmap(line_icon("folder", "#ffda70", 52).pixmap(52, 52))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setStyleSheet("font-size:64px;color:#444444;")
        empty_column.addWidget(glyph)
        empty_hint = QLabel("Kéo thả video vào đây hoặc nhấn “Thêm video”.")
        empty_hint.setObjectName("emptyTitle")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._translated(empty_hint, "Kéo thả video vào đây hoặc nhấn “Thêm video”.", "Drag video files here or click Add video")
        empty_column.setSpacing(12)
        empty_column.addWidget(empty_hint)
        empty_column.addStretch()
        self.grid_views.addWidget(empty)
        self.card_scroll = QScrollArea()
        self.card_scroll.setObjectName("videoGrid")
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_body = QWidget()
        self.card_body.setStyleSheet("background:transparent;")
        self.card_layout = QGridLayout(self.card_body)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setHorizontalSpacing(12)
        self.card_layout.setVerticalSpacing(12)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.card_scroll.setWidget(self.card_body)
        self.grid_views.addWidget(self.card_scroll)
        center.addWidget(self.grid_views, 1)

        self.video_count = QLabel("0 video")
        self.video_count.setObjectName("muted")
        options.insertWidget(2, self.video_count)
        layout.addWidget(self.grid_panel, 1)

        self.right_tabs = RoundedSidebar()
        self.right_tabs.setObjectName("rightTabs")
        self.right_tabs.setFixedWidth(300)
        self.right_tabs.setTabBar(SidebarTabBar())
        self.right_tabs.setDocumentMode(True)
        self.right_tabs.tabBar().setObjectName("sidebarTabs")
        self.right_tabs.tabBar().setFixedHeight(56)
        self.right_tabs.tabBar().setDrawBase(False)
        self.right_tabs.addTab(self._build_pipeline_tab(), "Pipeline")
        self.right_tabs.addTab(self._build_components_tab(), "Thành phần")
        self.right_tabs.addTab(self._build_tweaks_tab(), "Tinh chỉnh video")
        for index, kind in enumerate(["settings", "layers", "tuning"]):
            self.right_tabs.setTabIcon(index, line_icon(kind))
        layout.addWidget(self.right_tabs)
        return workspace

    def _scroll_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(10, 0, 10, 12)
        column.setSpacing(10)
        scroll.setWidget(body)
        return scroll, column

    def _section(self, column, title):
        frame = QFrame()
        frame.setObjectName("section")
        content = QVBoxLayout(frame)
        content.setContentsMargins(12, 8, 12, 12)
        content.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        label.setFixedHeight(28)
        content.addWidget(label)
        column.addWidget(frame)
        return content

    def _init_audio_reference(self):
        self._audio_loading = False
        self.audio_fields = {}
        for key, default in [("vocals", .24), ("drums", 1), ("bass", 1), ("other", 0),
                             ("source_fade_in", 0), ("source_fade_out", 0),
                             ("voice_fade_in", 0), ("voice_fade_out", 0)]:
            control = QDoubleSpinBox(self)
            control.setDecimals(2)
            control.setRange(0, 30 if "fade" in key else 2)
            control.setSingleStep(.05 if "fade" in key else .01)
            control.setValue(default)
            control.hide()
            control.valueChanged.connect(self._audio_controls_changed)
            self.audio_fields[key] = control
        self.source_audio_enabled = QCheckBox(self)
        self.voice_audio_enabled = QCheckBox(self)
        for control in (self.source_audio_enabled, self.voice_audio_enabled):
            control.setChecked(True)
            control.hide()
            control.toggled.connect(self._audio_controls_changed)
        self.mute.toggled.connect(lambda value:self.source_audio_enabled.setChecked(not value))
        self.controls["speed"].setValue(.9)
        self.voice_volume.setMaximum(2)
        self.voice_volume.setValue(1.26)
        self._audio_controls_changed()

    def _audio_controls_changed(self, *_):
        if self._audio_loading:
            return
        self.advanced["source_stem_volumes"] = {key: self.audio_fields[key].value() for key in ("vocals", "drums", "bass", "other")}
        for key in ("source_fade_in", "source_fade_out", "voice_fade_in", "voice_fade_out"):
            self.advanced[key] = self.audio_fields[key].value()
        self.advanced["source_audio_enabled"] = self.source_audio_enabled.isChecked()
        self.advanced["voice_audio_enabled"] = self.voice_audio_enabled.isChecked()
        if self._shell_ready and not self.syncing:
            self.mute.setChecked(not self.source_audio_enabled.isChecked())
            self.mark_dirty()

    def _audio_section(self, column, title, *, name="", expanded=True, toggle=None):
        frame = QFrame()
        frame.setObjectName(name or "audioReferenceSection")
        frame.setProperty("referenceSection", True)
        frame.setStyleSheet("QFrame[referenceSection=true] {background:#353535;border:0;border-radius:9px;} QLabel {background:transparent;}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        heading = QPushButton(title)
        heading.setObjectName("pipelineHeading")
        heading.setProperty("sectionHeading", True)
        heading.setCheckable(True)
        heading.setChecked(expanded)
        heading.setCursor(Qt.CursorShape.PointingHandCursor)
        heading.setFixedHeight(26)
        header.addWidget(heading, 1)
        if toggle is not None:
            header.addWidget(toggle)
        arrow = QLabel("▾" if expanded else "▸")
        arrow.setStyleSheet("color:#969696;font-size:10px;")
        header.addWidget(arrow)
        layout.addLayout(header)
        body = QWidget()
        body.setObjectName("audioSectionBody")
        body.setStyleSheet("QWidget#audioSectionBody {background:transparent;}")
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(8)
        layout.addWidget(body)
        body.setVisible(expanded)
        heading.toggled.connect(body.setVisible)
        heading.toggled.connect(lambda checked: arrow.setText("▾" if checked else "▸"))
        content._header, content._heading, content._body = header, heading, body
        column.addWidget(frame)
        return content

    def _pipeline_field(self, column, title, source, name="", *, labels=None, direct=False):
        row = QHBoxLayout()
        row.setSpacing(8)
        caption = QLabel(title)
        caption.setObjectName("pipelineFieldLabel")
        caption.setFixedWidth(104)
        caption.setWordWrap(True)
        row.addWidget(caption)
        control = source if direct else self._clone(source, name, labels=labels)
        if direct:
            control.setMinimumWidth(0)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        control._field_caption = caption
        control.setMinimumHeight(29)
        row.addWidget(control, 1)
        column.addLayout(row)
        return control

    def _pipeline_toggle(self, column, title, source, name):
        row = QHBoxLayout()
        caption = QLabel(title)
        caption.setObjectName("pipelineFieldLabel")
        caption.setWordWrap(True)
        row.addWidget(caption, 1)
        control = ReferenceSwitch(source, name)
        row.addWidget(control)
        column.addLayout(row)
        return control

    def _audio_slider(self, column, title, source, name, unit="%", hint=""):
        row = QHBoxLayout()
        row.setSpacing(7)
        label = ElidedLabel(title)
        label.setToolTip(title)
        label.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Preferred)
        label.setStyleSheet("color:#9d9d9d;font-size:13px;")
        label.setFixedWidth(104)
        row.addWidget(label)
        slider = ReferenceSlider(Qt.Orientation.Horizontal)
        slider.setObjectName(name + "Slider")
        slider.setMinimumWidth(32)
        slider.setFixedHeight(24)
        scale = 100 if isinstance(source, QDoubleSpinBox) else 1
        display = 100 if unit == "%" else 1
        slider.setRange(round(source.minimum()*scale), round(source.maximum()*scale))
        slider.setSingleStep(max(1, round(source.singleStep()*scale)))
        slider.setPageStep(max(1, round(source.singleStep()*scale*5)))
        slider.setValue(round(source.value()*scale))
        slider.setStyleSheet("QSlider {background:transparent;} QSlider::groove:horizontal {height:4px;border:0;background:#28636c;border-radius:2px;} QSlider::sub-page:horizontal {background:#00b4ce;border-radius:2px;} QSlider::handle:horizontal {width:13px;background:#00b4ce;border:0;border-radius:7px;margin:-5px 0;}")
        row.addWidget(slider, 1)
        badge = QDoubleSpinBox()
        badge.setObjectName(name)
        badge.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        badge.setDecimals(2 if unit in ("s", "x") else 0)
        badge.setRange(source.minimum()*display, source.maximum()*display)
        badge.setSingleStep(source.singleStep()*display)
        badge.setSuffix(unit)
        badge.setValue(source.value()*display)
        badge.setAlignment(Qt.AlignmentFlag.AlignRight)
        badge.setFixedSize(53 if unit in ("s", "x", "ms", " câu") else 34, 23)
        badge.setStyleSheet("QDoubleSpinBox {background:#252527;border:0;border-radius:3px;padding:1px 4px;color:#00c6df;font-size:12px;font-weight:600;}")
        row.addWidget(badge)
        slider.valueChanged.connect(lambda value: source.setValue(value/scale if isinstance(source, QDoubleSpinBox) else value))
        slider._source_binding = ControlStateBinding(source, slider)
        badge._source_binding = ControlStateBinding(source, badge)
        badge.valueChanged.connect(lambda value: source.setValue(value/display if isinstance(source, QDoubleSpinBox) else round(value/display)))
        def sync(value):
            slider.blockSignals(True); badge.blockSignals(True)
            slider.setRange(round(source.minimum()*scale),round(source.maximum()*scale))
            badge.setRange(source.minimum()*display,source.maximum()*display)
            slider.setValue(round(value*scale)); badge.setValue(value*display)
            slider.blockSignals(False); badge.blockSignals(False)
        source.valueChanged.connect(sync)
        if hint:
            help_label = QLabel("?")
            help_label.setFixedSize(12, 16)
            help_label.setObjectName("pipelineHelp")
            help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            help_label.setToolTip(hint)
            help_label.setStyleSheet("color:#9c9c9c;font-size:12px;")
            row.addWidget(help_label)
        column.addLayout(row)
        badge._field_slider = slider
        badge._field_caption = label
        badge._field_row = row
        badge.sync_from_source = sync
        return badge

    def _clone(self, source, name="", labels=None):
        """Bind a compact sidebar control to the full editor control."""
        if isinstance(source, QFontComboBox):
            control = QFontComboBox()
            control.setCurrentFont(source.currentFont())
            control.currentFontChanged.connect(source.setCurrentFont)
            source.currentFontChanged.connect(control.setCurrentFont)
        elif isinstance(source, QComboBox):
            control = QComboBox()
            # Share item roles and availability as well as displayed labels.
            # Export format changes can disable incompatible codec items.
            control.setModel(DisplayLabels(source.model(), labels, control) if labels else source.model())
            control.setModelColumn(source.modelColumn())
            control.setEditable(source.isEditable())
            control.setCurrentIndex(source.currentIndex())
            control.currentIndexChanged.connect(source.setCurrentIndex)
            source.currentIndexChanged.connect(control.setCurrentIndex)
            if source.isEditable():
                control.editTextChanged.connect(source.setEditText)
                source.editTextChanged.connect(control.setEditText)
        elif isinstance(source, QDoubleSpinBox):
            control = QDoubleSpinBox()
            control.setRange(source.minimum(), source.maximum())
            control.setDecimals(source.decimals())
            control.setSingleStep(source.singleStep())
            control.setValue(source.value())
            control.valueChanged.connect(source.setValue)
            source.valueChanged.connect(control.setValue)
        elif isinstance(source, QSpinBox):
            control = QSpinBox()
            control.setRange(source.minimum(), source.maximum())
            control.setSingleStep(source.singleStep())
            control.setValue(source.value())
            control.valueChanged.connect(source.setValue)
            source.valueChanged.connect(control.setValue)
        elif isinstance(source, QCheckBox):
            control = QCheckBox(source.text())
            control.setChecked(source.isChecked())
            control.toggled.connect(source.setChecked)
            source.toggled.connect(control.setChecked)
        elif isinstance(source, QLineEdit):
            control = QLineEdit(source.text())
            control.setReadOnly(source.isReadOnly())
            control.textChanged.connect(source.setText)
            source.textChanged.connect(control.setText)
        else:
            raise TypeError(type(source).__name__)
        control.setMinimumWidth(0)
        control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if isinstance(control, QComboBox):
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(0)
        if name:
            control.setObjectName(name)
        control._source_binding = ControlStateBinding(source, control)
        return control

    def _field(self, column, label, source, name=""):
        if isinstance(source,QComboBox):
            row = QHBoxLayout()
            row.setSpacing(8)
            caption = QLabel(label)
            caption.setObjectName("muted")
            caption.setFixedWidth(94)
            caption.setWordWrap(True)
            row.addWidget(caption)
            control = self._clone(source,name)
            control._field_caption = caption
            row.addWidget(control, 1)
            column.addLayout(row)
            return control
        if isinstance(source, (QSpinBox, QDoubleSpinBox)):
            box = QVBoxLayout()
            box.setSpacing(5)
            header = QHBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("muted")
            header.addWidget(caption, 1)
            control = self._clone(source, name)
            control._field_caption = caption
            control.setFixedWidth(67)
            control.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
            header.addWidget(control)
            box.addLayout(header)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setObjectName((name or source.objectName() or label) + "Slider")
            scale = 10 ** source.decimals() if isinstance(source, QDoubleSpinBox) else 1
            slider.setRange(round(source.minimum()*scale), round(source.maximum()*scale))
            slider.setSingleStep(max(1, round(source.singleStep()*scale)))
            slider.setPageStep(max(1, round(source.singleStep()*scale*5)))
            slider.setValue(round(source.value()*scale))
            slider.valueChanged.connect(lambda value: source.setValue(value/scale) if isinstance(source,QDoubleSpinBox) else source.setValue(value))
            source.valueChanged.connect(lambda value: slider.setValue(round(value*scale)))
            box.addWidget(slider)
            slider._source_binding = ControlStateBinding(source, slider)
            control._field_slider = slider
            column.addLayout(box)
            return control
        row = QHBoxLayout()
        row.setSpacing(8)
        text = QLabel(label)
        text.setObjectName("muted")
        text.setFixedWidth(94)
        row.addWidget(text)
        control = self._clone(source, name)
        control._field_caption = text
        row.addWidget(control, 1)
        column.addLayout(row)
        return control

    def _build_pipeline_tab(self):
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0,0,0,12)
        outer.setSpacing(0)
        self.pipeline_tabs = QTabWidget()
        self.pipeline_tabs.setTabBar(SegmentedTabBar())
        outer.addWidget(self.pipeline_tabs)
        self.pipeline_tabs.setObjectName("pipelineSubtabs")
        self.pipeline_tabs.setDocumentMode(True)
        self.pipeline_tabs.tabBar().setDrawBase(False)
        self.worker_controls = {}
        scroll, column = self._scroll_tab()
        self.pipeline_tabs.addTab(scroll, "Phụ đề")
        column.setContentsMargins(10, 10, 10, 12)
        languages = {"auto": "Tự động", "vi": "Tiếng Việt", "en": "Tiếng Anh",
                     "zh": "Tiếng Trung", "zh-CN": "Tiếng Trung", "ja": "Tiếng Nhật",
                     "ko": "Tiếng Hàn", "fr": "Tiếng Pháp", "th": "Tiếng Thái"}
        subtitles = self._audio_section(column, "Phụ đề", name="subtitleSection",
                                       toggle=ReferenceSwitch(self.pipe_recognize, "pipelineRecognize"))
        self._pipeline_field(subtitles, "Trích phụ đề", self.pipe_method, "recognitionMethod")
        self._pipeline_field(subtitles, "Ngôn ngữ gốc", self.pipe_source, "sourceLanguage", labels=languages)
        recognition_workers = QSpinBox()
        recognition_workers.setRange(1, 2)
        recognition_workers.setValue(1)
        recognition_workers.setToolTip("Giới hạn 1–2 mô hình cục bộ chạy đồng thời để kiểm soát bộ nhớ.")
        self.worker_controls["recognition_workers"] = recognition_workers
        self._audio_slider(subtitles, "Luồng song song", recognition_workers, "recognition_workers",
                           unit="", hint=recognition_workers.toolTip())
        self.sub_lead = QDoubleSpinBox()
        self.sub_lead.setRange(0, .5)
        self.sub_lead.setDecimals(2)
        self.sub_lead.setSingleStep(.05)
        self.sub_lead.setObjectName("subtitleLead")
        self._audio_slider(subtitles, "Sub hiện sớm", self.sub_lead, "subtitleLeadControl", unit="s",
                           hint="Hiển thị phụ đề sớm hơn mốc gốc theo số giây đã chọn.")
        recognition = self._audio_section(column, "Thiết lập nhận dạng", expanded=False, name="recognitionOptions")
        self._whisper_field = self._pipeline_field(recognition, "Whisper", self.pipe_model, "whisperModel")
        self.recognition_device = QComboBox()
        for label, value in (("Tự động", "auto"), ("GPU NVIDIA", "cuda"), ("CPU", "cpu")):
            self.recognition_device.addItem(label, value)
        self._pipeline_field(recognition, "Thiết bị", self.recognition_device, "recognitionDevice")
        self.recognition_batch_size = QSpinBox()
        self.recognition_batch_size.setRange(1, 12)
        self.recognition_batch_size.setValue(1)
        self._audio_slider(recognition, "Đoạn song song", self.recognition_batch_size, "recognitionBatch", unit="")
        self._ocr_language_field = self._pipeline_field(recognition, "OCR", self.ocr_language, "ocrLanguage")
        self._ocr_interval_field = self._pipeline_field(recognition, "Lấy mẫu (s)", self.ocr_interval)
        self.pipe_method.currentIndexChanged.connect(self._show_recognition_method)
        self._show_recognition_method()
        recognition.addWidget(_button("Sửa phụ đề gốc / bản dịch…", lambda: self.navigate(3)))
        column.addStretch()

        scroll, column = self._scroll_tab()
        self.pipeline_tabs.addTab(scroll, "Dịch thuật")
        column.setContentsMargins(10, 10, 10, 12)
        translate = self._audio_section(column, "Dịch thuật", name="translationSection",
                                       toggle=ReferenceSwitch(self.pipe_translate, "pipelineTranslate"))
        self._pipeline_field(translate, "Ngôn ngữ đích", self.pipe_target, "targetLanguage", labels=languages)
        self.pipe_scenario = QComboBox()
        self.pipe_scenario.setObjectName("translationScenario")
        self.pipe_scenario.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pipe_scenario.setMinimumContentsLength(0)
        self.pipe_scenario.currentIndexChanged.connect(self._scenario_changed)
        self._pipeline_field(translate, "Kịch bản", self.pipe_scenario, direct=True)

        translation_tools = self._audio_section(column, "Công cụ", name="translationTools")
        self.provider_display = QLabel("Google Translate", container)
        self.provider_display.setObjectName("translationProvider")
        self.provider_display.hide()
        self.pipe_provider = QComboBox()
        self.pipe_provider.setObjectName("providerSelect")
        for label, provider in [("Google Translate", "google")]+[(data["label"], key) for key, data in providers.PROVIDERS.items()]:
            self.pipe_provider.addItem(label, provider)
        self.pipe_provider.currentIndexChanged.connect(self._provider_changed)
        self._pipeline_field(translation_tools, "Công cụ", self.pipe_provider, direct=True)
        self.translation_model = QComboBox()
        self.translation_model.setObjectName("translationModel")
        self.translation_model.setEditable(True)
        self.translation_model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.translation_model.setMinimumContentsLength(0)
        self.translation_model.setPlaceholderText("Chọn mô hình")
        self.translation_model.currentTextChanged.connect(self._translation_model_changed)
        self._pipeline_field(translation_tools, "Mô hình", self.translation_model, direct=True)

        options = self._audio_section(column, "Tùy chọn", name="translationOptions")
        self.context_frames = QCheckBox("Kèm ảnh ngữ cảnh", container)
        self.context_frames.setObjectName("translationImageContext")
        self.context_frames.setToolTip("Gửi khung hình đầu của từng video cùng phụ đề. Mô hình đã chọn cần hỗ trợ nhận ảnh.")
        self.context_frames.toggled.connect(lambda value: self._set_translation_option("image_context", value))
        self.context_frames.hide()
        self.context_switch = self._pipeline_toggle(options, "Kèm ảnh ngữ cảnh", self.context_frames, "translationImageContextSwitch")
        self.batch_request = QSpinBox()
        self.batch_request.setRange(5, 500)
        self.batch_request.setSingleStep(5)
        self.batch_request.setValue(40)
        self.batch_request.valueChanged.connect(lambda value: self._set_translation_option("batch_size", value))
        self.translation_batch_control = self._audio_slider(options, "Số câu mỗi lượt", self.batch_request, "translationBatchSize", unit=" câu")
        self.request_workers = QSpinBox()
        self.request_workers.setRange(1, 5)
        self.request_workers.setValue(1)
        self.request_workers.valueChanged.connect(lambda value: self._set_translation_option("translate_workers", value))
        self.translation_workers_control = self._audio_slider(options, "Luồng song song", self.request_workers, "translationWorkers", unit="")
        provider_options = self._audio_section(column, "Thiết lập dịch", expanded=False, name="translationSettings")
        self.refresh_models_button = _button("Tải danh sách mô hình", self.fetch_translation_models)
        provider_options.addWidget(self.refresh_models_button)
        provider_options.addWidget(_button("Chọn kịch bản dịch…", self.open_scenarios))
        provider_options.addWidget(_button("Nhà cung cấp / API…", self.open_settings))
        provider_options.addWidget(self._clone(self.pipe_bilingual, "bilingualCaptions"))
        column.addStretch()

        scroll, column = self._scroll_tab()
        self.pipeline_tabs.addTab(scroll, "Lồng tiếng")
        column.setContentsMargins(10, 10, 10, 12)
        voice = self._audio_section(column, "Giọng đọc")
        voice_header = voice._header
        dub_toggle = ReferenceSwitch(self.pipe_dub,"pipelineDub")
        voice_header.insertWidget(voice_header.count()-1, dub_toggle)
        voice_row = QHBoxLayout()
        voice_row.setSpacing(8)
        voice_caption = QLabel("Giọng")
        voice_caption.setObjectName("muted")
        voice_caption.setFixedWidth(104)
        voice_row.addWidget(voice_caption)
        self.voice_select_button = _button("",self.open_voice_picker)
        self.voice_select_button.setObjectName("pipelineVoice")
        self.voice_select_button.setFixedHeight(36)
        self.voice_select_button.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.voice_select_button.setStyleSheet("QPushButton {padding:0;background:#252527;border:1px solid #3c3c3c;border-radius:4px;} QPushButton:hover {background:#303030;border-color:#00b4d8;}")
        selected_voice = QHBoxLayout(self.voice_select_button)
        selected_voice.setContentsMargins(10,0,10,0)
        selected_voice.setSpacing(6)
        self.voice_display = ElidedLabel(self.pipe_voice.currentText())
        self.voice_display.setObjectName("chosenVoiceDisplay")
        self.voice_display.setStyleSheet("color:#e7e7e7;font-size:12px;")
        self.voice_engine_display = QLabel("▶ edge")
        self.voice_engine_display.setStyleSheet("color:#00b4d8;font-size:11px;")
        chevron = QLabel("›")
        chevron.setStyleSheet("color:#929292;font-size:16px;")
        for label in (self.voice_display,self.voice_engine_display,chevron):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        selected_voice.addWidget(self.voice_display,1)
        selected_voice.addWidget(self.voice_engine_display)
        selected_voice.addWidget(chevron)
        voice_row.addWidget(self.voice_select_button,1)
        voice.addLayout(voice_row)
        import local_tts
        for title, identity, name in [("Ngọc Huyền", "ngochuyen:ngoc_huyen", "localVoiceNgocHuyen"),
                                      ("Ngọc Huyền V2", "ngochuyen_v2:ngoc_huyen_v_2", "localVoiceNgocHuyenV2")]:
            button = _button(title,lambda checked=False,value=identity:self._use_voice(value))
            button.setObjectName(name)
            state = local_tts.availability(identity)
            button.setEnabled(state["ready"])
            button.setToolTip(state["hint"])
            button.setParent(container)
            button.hide()
        self.tts_device = QComboBox(container)
        self.tts_device.setObjectName("ttsDevice")
        for title,value in [("Tự động", "auto"), ("GPU NVIDIA", "cuda"), ("CPU", "cpu")]:
            self.tts_device.addItem(title,value)
        saved_device = self.prefs.value("tts_device","auto")
        self.tts_device.setCurrentIndex(max(0,self.tts_device.findData(saved_device)))
        self.tts_device.currentIndexChanged.connect(lambda:self.prefs.setValue("tts_device",self.tts_device.currentData()))
        self.tts_device.setToolTip("Thiết bị cho giọng offline và tách giọng. Chọn GPU sẽ báo lỗi rõ nếu CUDA không khả dụng.")
        self.tts_device.hide()
        self.pipe_voice.currentTextChanged.connect(self.refresh_voice_display)
        self.voice_parallel = QSpinBox()
        self.voice_parallel.setRange(1,8)
        self.voice_parallel.setValue(3)
        self._audio_slider(voice,"Luồng song song",self.voice_parallel,"voiceWorkers",unit="",hint="Số câu tạo giọng đồng thời trong một video. Tác vụ GPU được xếp hàng để giữ đủ bộ nhớ.")
        voice = self._audio_section(column,"Video gốc")
        self.separation_location = QComboBox()
        self.separation_location.addItem("Tắt",False)
        self.separation_location.addItem("Máy này",True)
        self.separation_location.setCurrentIndex(1)
        self.pipe_separate.setChecked(True)
        self.separation_location.currentIndexChanged.connect(lambda:self.pipe_separate.setChecked(bool(self.separation_location.currentData())))
        self.pipe_separate.toggled.connect(lambda value:self.separation_location.setCurrentIndex(1 if value else 0))
        self._pipeline_field(voice,"Tách giọng",self.separation_location,"separateOriginalVoice")
        self.separation_mode = QComboBox()
        self.separation_mode.addItem("Cơ bản","vocals")
        self.separation_mode.addItem("Chi tiết","stems")
        self.separation_mode.setCurrentIndex(1)
        self.separation_mode.currentIndexChanged.connect(lambda:self._choose_audio_detail("A0") if getattr(self,"_audio_selection",None)=="A0" else None)
        self.pipe_separate.toggled.connect(lambda:self._choose_audio_detail("A0") if getattr(self,"_audio_selection",None)=="A0" else None)
        self._pipeline_field(voice,"Mức tách",self.separation_mode,"separationMode")
        self._audio_slider(voice,"Tốc độ video",self.controls["speed"],"sourceVideoSpeed",unit="x")
        column.addSpacing(10)
        voice = self._audio_section(column,"Giọng lồng")
        self.tts_fit = QComboBox()
        self.tts_fit.addItem("Khớp theo câu","fit")
        self.tts_fit.addItem("Giọng đều","natural")
        self.tts_fit.addItem("Không khớp","off")
        self.tts_fit.setCurrentIndex(1)
        self._pipeline_field(voice, "Khớp giọng", self.tts_fit, "voiceFit")
        self.tts_speed = QDoubleSpinBox()
        self.tts_speed.setRange(.25, 4)
        self.tts_speed.setSingleStep(.05)
        self.tts_speed.setDecimals(2)
        self.tts_speed.setValue(1.2)
        self._audio_slider(voice, "Tốc độ giọng", self.tts_speed, "voiceSpeed",unit="x")
        self.voice_max_speed=QDoubleSpinBox()
        self.voice_max_speed.setRange(1,4); self.voice_max_speed.setValue(2); self.voice_max_speed.setSingleStep(.05)
        maximum_speed = self._audio_slider(voice, "Tốc độ tối đa", self.voice_max_speed, "voiceMaxSpeed", unit="x")
        def show_maximum_speed():
            for index in range(maximum_speed._field_row.count()):
                widget = maximum_speed._field_row.itemAt(index).widget()
                if widget is not None:
                    widget.setVisible(self.tts_fit.currentData() == "fit")
        self.tts_fit.currentIndexChanged.connect(show_maximum_speed)
        show_maximum_speed()
        self.tts_delay = QSpinBox()
        self.tts_delay.setRange(-60000, 60000)
        self.tts_delay.setSingleStep(50)
        self._audio_slider(voice, "Trễ giọng", self.tts_delay, "voiceDelayMs",unit="ms")
        self.pipe_pronunciation = QComboBox()
        self.pipe_pronunciation.setObjectName("pronunciationDictionary")
        self.pipe_pronunciation.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pipe_pronunciation.setMinimumContentsLength(0)
        self.pipe_pronunciation.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.pipe_pronunciation.currentIndexChanged.connect(self._pronunciation_changed)
        self._pipeline_field(voice,"Sửa cách đọc",self.pipe_pronunciation,"voicePronunciation")
        self.pipe_pronunciation.setToolTip("Các bảng tạo trong Kịch bản → Sửa cách đọc.")
        for control in (self.voice_parallel,self.tts_speed,self.tts_delay,self.tts_fit,self.separation_mode):
            signal = control.valueChanged if hasattr(control,"valueChanged") else control.currentIndexChanged
            signal.connect(self.mark_dirty)
        if local_tts.availability("ngochuyen_v2")["ready"]:
            self.pipe_voice.addItem("ngochuyen_v2:ngoc_huyen_v_2")
            self.pipe_voice.setCurrentText("ngochuyen_v2:ngoc_huyen_v_2")
        column.addStretch()

        scroll, column = self._scroll_tab()
        self.pipeline_tabs.addTab(scroll, "Hệ thống")
        workers_frame = QFrame()
        workers_frame.setObjectName("systemWorkers")
        workers_frame.setStyleSheet("QFrame#systemWorkers {background:#353535;border-radius:9px;}")
        workers = QVBoxLayout(workers_frame)
        workers.setContentsMargins(12, 4, 12, 6)
        workers.setSpacing(10)
        column.addWidget(workers_frame)
        for key, label, default in [("general_workers", "Xử lý chung", 2), ("tts_workers", "Lồng tiếng", 2), ("render_workers", "Xuất video", 2)]:
            source = QSpinBox()
            source.setRange(1, 8)
            source.setValue(default)
            self.worker_controls[key] = source
            self._audio_slider(workers, label, source, key, unit="", hint="Số tác vụ "+label.lower()+" chạy đồng thời.")
        output = self._audio_section(column, "Xuất video", name="exportSection")
        self._pipeline_field(output, "Định dạng", self.enc_format, "exportFormat",
                             labels={"mp4": "MP4", "mkv": "MKV", "mov": "MOV", "webm": "WebM"})
        self._pipeline_field(output, "Độ phân giải", self.resolution, "exportResolution", labels={"Theo video đầu": "Bản gốc"})
        self._pipeline_field(output, "Tốc độ", self.enc_preset, "encoderPreset",
                             labels={"veryfast": "Rất nhanh", "fast": "Nhanh", "medium": "Cân bằng", "slow": "Chậm"})
        self._pipeline_field(output, "Thiết bị encode", self.enc_device, "exportDevice",
                             labels={"auto": "Tự động", "cpu": "CPU", "nvidia": "NVIDIA", "qsv": "Intel", "amf": "AMD"})
        self._pipeline_field(output, "Codec", self.enc_codec, "exportCodec",
                             labels={"h264": "H.264", "hevc": "H.265", "vp9": "VP9"})
        self._pipeline_field(output, "FPS", self.fps, "exportFps")
        quality_preset = QComboBox()
        quality_preset.setObjectName("exportQualityPreset")
        for label, quality in (("Tùy chỉnh", None), ("Cao", 18), ("Cân bằng", 23), ("Nhỏ gọn", 28)):
            quality_preset.addItem(label, quality)
        def sync_quality_preset(value):
            quality_preset.blockSignals(True)
            quality_preset.setCurrentIndex(max(0, quality_preset.findData(value)))
            quality_preset.blockSignals(False)
        quality_preset.currentIndexChanged.connect(
            lambda: self.enc_quality.setValue(quality_preset.currentData()) if quality_preset.currentData() is not None else None)
        self.enc_quality.valueChanged.connect(sync_quality_preset)
        sync_quality_preset(self.enc_quality.value())
        self._pipeline_field(output, "Chất lượng xuất", quality_preset, direct=True)

        advanced = self._audio_section(column, "Nâng cao", name="advancedEncoderSection")
        self.export_advanced_button = advanced._heading
        self.export_advanced_button.setObjectName("toggleAdvancedEncoder")
        advanced._body.setObjectName("advancedEncoderOptions")
        advanced._body.setStyleSheet("background:transparent;")
        self._pipeline_field(advanced, "Kiểm soát bitrate", self.enc_mode, "encoderMode",
                             labels={"crf": "Chất lượng", "vbr": "Biến đổi (VBR)", "cbr": "Cố định (CBR)"})
        self._audio_slider(advanced, "Chất lượng", self.enc_quality, "exportQuality", unit="Q",
                           hint="CRF / CQ: số nhỏ giữ nhiều chi tiết hơn và tạo tệp lớn hơn.")
        bitrate = self._pipeline_field(advanced, "Bitrate", self.enc_bitrate, "encoderBitrate")
        bitrate.setToolTip("Ví dụ 5M hoặc 2500k. Được dùng ở chế độ VBR / CBR.")
        maxrate = self._pipeline_field(advanced, "Trần bitrate", self.enc_maxrate, "encoderMaxrate")
        maxrate.setToolTip("Ví dụ 8M; để trống khi dùng CRF không giới hạn bitrate.")
        self.encoder_bframes = self._clone(self.enc_bframes, "encoderBframes")
        self.encoder_bframes.setToolTip("Tắt để xuất H.264/H.265 với -bf 0; VP9 không dùng B-frames.")
        advanced.addWidget(self.encoder_bframes)
        destination = self._audio_section(column, "Lưu và tác vụ", expanded=False, name="outputDestination")
        destination.addWidget(self._clone(self.pipe_export, "pipelineExport"))
        folder = self._clone(self.pipe_folder, "outputFolder")
        folder.setToolTip("Thư mục lưu kết quả")
        destination.addWidget(folder)
        destination.addWidget(_button("Chọn thư mục lưu…", self._choose_batch_folder))
        destination.addWidget(_button("Tiếp tục Pipeline…", self.resume_pipeline))
        if hasattr(self, "open_gpu_performance"):
            performance = _button("Hiệu năng GPU…", self.open_gpu_performance)
            performance.setObjectName("openGpuPerformance")
            destination.addWidget(performance)
        column.addStretch()
        return container

    def _show_recognition_method(self,*_):
        speech = self.pipe_method.currentIndex() == 0
        for control,visible in [(self._whisper_field,speech),(self._ocr_language_field,not speech),(self._ocr_interval_field,not speech)]:
            control.setVisible(visible)
            control._field_caption.setVisible(visible)
            if hasattr(control,"_field_slider"):
                control._field_slider.setVisible(visible)

    def _build_components_tab(self):
        self.component_tabs = QTabWidget()
        self.component_tabs.setTabBar(SegmentedTabBar())
        self.component_tabs.setObjectName("componentViews")
        self.component_tabs.setDocumentMode(True)
        self.component_tabs.tabBar().setDrawBase(False)
        scroll, column = self._scroll_tab()
        self.component_tabs.addTab(scroll,"Lớp")
        self.current_video_label = QLabel("Chọn video để chỉnh thành phần")
        self.current_video_label.setObjectName("muted")
        self.current_video_label.setWordWrap(True)
        self.current_video_label.hide()
        self.inline_layers = QListWidget()
        self.inline_layers.setObjectName("componentLayerList")
        self.inline_layers.setParent(self.component_tabs)
        self.inline_layers.hide()
        self.inline_layers.currentItemChanged.connect(self._inline_layer_chosen)
        self.inline_layers.itemChanged.connect(self._inline_layer_enabled)
        self.inline_layers.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.inline_layers.customContextMenuRequested.connect(self._layer_context_menu)
        add_bar = LayerAddBar([("text","+ Văn bản",self.add_text_layer),("media","+ Phương tiện",self.add_image_layer),
                              ("blur","+ Vùng mờ",self.add_blur_layer),("filter","+ Bộ lọc",self.add_filter_layer),
                              ("audio","+ Âm thanh",self.add_audio_layer)])
        column.addSpacing(6)
        column.addWidget(add_bar)
        column.addSpacing(10)
        self.layer_add_buttons = list(add_bar.buttons.values())
        self.layer_groups = GroupedLayerList()
        try:
            collapsed = json.loads(str(self.prefs.value("layer_group_collapsed","{}")))
            if isinstance(collapsed,dict):
                for identity in self.layer_groups.collapsed_states:
                    self.layer_groups.collapsed_states[identity] = bool(collapsed.get(identity,False))
        except (ValueError,TypeError):
            pass
        self.layer_groups.collapse_changed.connect(lambda key,value:self.prefs.setValue("layer_group_collapsed",json.dumps(self.layer_groups.collapsed_states)))
        self.layer_groups.picked.connect(self._group_layer_chosen)
        self.layer_groups.toggled.connect(self._group_layer_enabled)
        self.layer_groups.deleted.connect(self._group_layer_deleted)
        column.addWidget(self.layer_groups)
        self.layer_count = QLabel("0 thành phần")
        self.layer_count.setObjectName("muted")
        self.layer_count.hide()
        self.component_duplicate_button = _button("Nhân đôi thành phần",self.duplicate_layer)
        self.component_apply_button = _button("Áp dụng style cho tất cả",self.apply_all)
        self.component_duplicate_button.hide()
        self.component_apply_button.hide()
        column.addStretch()

        scroll, column = self._scroll_tab()
        self.component_tabs.addTab(scroll,"Chi tiết")
        self.inline_detail_title = QLabel("Chọn một thành phần ở tab Các lớp")
        self.inline_detail_title.setObjectName("muted")
        self.inline_detail_title.setWordWrap(True)
        heading = QHBoxLayout()
        self.audio_badge = QLabel()
        self.audio_badge.setFixedSize(30,23)
        self.audio_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_badge.setStyleSheet("background:#00b4ce;color:#07181c;border-radius:4px;font-size:13px;font-weight:700;")
        self.audio_badge.hide()
        heading.addWidget(self.audio_badge)
        heading.addWidget(self.inline_detail_title,1)
        column.addLayout(heading)
        self._build_subtitle_details(column)
        self.audio_detail_frames = {}
        for identity,title in (("A0","Âm gốc"),("A1","Lồng tiếng")):
            frame = QWidget()
            frame.setObjectName("audioDetail"+identity)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(0,0,0,0)
            layout.setSpacing(8)
            audio = self._audio_section(layout,"Âm gốc" if identity=="A0" else "Âm lượng")
            if identity == "A0":
                self.source_total_volume = self._audio_slider(audio,"Âm lượng",self.controls["volume"],"a0Volume")
                self.source_stem_rows = []
                for key,label in (("vocals","Giọng gốc"),("drums","Trống / nhịp"),("bass","Âm trầm (bass)"),("other","Nhạc nền")):
                    self.source_stem_rows.append(self._audio_slider(audio,label,self.audio_fields[key],"a0"+key.title(),hint="Âm lượng riêng của lớp "+label+" sau khi tách giọng tại máy."))
            else:
                self._audio_slider(audio,"Âm lượng",self.voice_volume,"a1Volume")
            fade = self._audio_section(layout,"Fade")
            prefix = "source" if identity=="A0" else "voice"
            self._audio_slider(fade,"Fade vào",self.audio_fields[prefix+"_fade_in"],identity.lower()+"FadeIn",unit="s")
            self._audio_slider(fade,"Fade ra",self.audio_fields[prefix+"_fade_out"],identity.lower()+"FadeOut",unit="s")
            frame.hide()
            column.addWidget(frame)
            self.audio_detail_frames[identity] = frame
        self.generic_detail_frame = QWidget()
        column.addWidget(self.generic_detail_frame)
        column.addStretch()
        column = QVBoxLayout(self.generic_detail_frame)
        column.setContentsMargins(0,0,0,0)
        column.setSpacing(8)
        source_detail = self._section(column,"Video gốc")
        self.source_detail_frame = source_detail.parentWidget()
        self._field(source_detail,"Khung hình",self.resolution)
        self._field(source_detail,"Tốc độ",self.controls["speed"])
        self._field(source_detail,"Xoay",self.rotation)
        source_detail.addWidget(self._clone(self.mirror))
        source_detail.addWidget(self._clone(self.source_flip_vertical,"sourceFlipVertical"))
        composition = self._audio_section(source_detail,"Bố cục video gốc")
        for key,label,name,unit in [("source_x","Ngang","sourcePositionX","%"),("source_y","Dọc","sourcePositionY","%"),
                                    ("source_scale","Tỉ lệ","sourceScale","x"),("source_scale_x","Tỉ lệ ngang","sourceScaleX","x"),
                                    ("source_scale_y","Tỉ lệ dọc","sourceScaleY","x"),("source_opacity","Độ rõ","sourceOpacity","%")]:
            self._audio_slider(composition,label,self.source_fields[key],name,unit=unit)
        self._audio_slider(composition,"Xoay tự do",self.source_fields['source_rotation'],"sourceRotation","°")
        background = self._audio_section(source_detail,"Nền khung hình")
        self._field(background,"Kiểu nền",self.source_background_mode,"sourceBackgroundMode")
        self.background_color_field = self._field(background,"Màu (HEX)",self.source_background_color,"sourceBackgroundColor")
        self.background_color_button = _button("Chọn màu nền…",self.pick_source_background_color)
        background.addWidget(self.background_color_button)
        self.background_blur_field = self._field(background,"Độ mờ nền",self.source_fields['background_blur'],"sourceBackgroundBlur")
        self.source_background_mode.currentIndexChanged.connect(self._update_background_controls)
        self._update_background_controls()
        audio_detail = self._audio_section(column,"Lớp âm thanh")
        self.audio_layer_frame = audio_detail.parentWidget().parentWidget()
        self._field(audio_detail,"Tệp",self.audio_layer_fields['path'],"audioLayerPath")
        audio_detail.addWidget(_button("Đổi tệp âm thanh…",self.choose_audio_layer_source))
        audio_detail.addWidget(self._clone(self.audio_layer_enabled,"audioLayerEnabled"))
        audio_detail.addWidget(self._clone(self.audio_layer_loop,"audioLayerLoop"))
        for key,label,name,unit in [("volume","Âm lượng","audioLayerVolume","%"),("start","Bắt đầu","audioLayerStart","s"),
                                    ("end","Kết thúc","audioLayerEnd","s"),("fade_in","Fade vào","audioLayerFadeIn","s"),
                                    ("fade_out","Fade ra","audioLayerFadeOut","s")]:
            self._audio_slider(audio_detail,label,self.audio_layer_fields[key],name,unit=unit)
        note = QLabel("Kết thúc 0: phát đến hết video. Bắt đầu / kết thúc tính theo video xuất.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        note.setStyleSheet("font-size:11px;color:#a0a0a0;")
        audio_detail.addWidget(note)
        audio_detail.addWidget(_button("Xem thử bản phối 5 giây",self.preview_render,primary=True))
        audio_detail.addWidget(_button("Nhân đôi lớp âm thanh",self.duplicate_layer))
        audio_detail.addWidget(_button("Xóa lớp âm thanh",self.delete_layer,"danger"))
        self.audio_layer_frame.hide()
        filter_detail = self._audio_section(column,"Lớp bộ lọc")
        self.filter_layer_frame = filter_detail.parentWidget().parentWidget()
        self._field(filter_detail,"Bộ lọc",self.filter_layer_kind,"filterLayerKind")
        self._audio_slider(filter_detail,"Cường độ",self.filter_layer_fields['intensity'],"filterLayerIntensity",unit="%")
        self._audio_slider(filter_detail,"Bắt đầu",self.filter_layer_fields['start'],"filterLayerStart",unit="s")
        self._audio_slider(filter_detail,"Kết thúc",self.filter_layer_fields['end'],"filterLayerEnd",unit="s")
        filter_detail.addWidget(self._clone(self.filter_layer_enabled,"filterLayerEnabled"))
        note = QLabel("Kết thúc 0: áp dụng đến hết video. Các lớp được phối theo thứ tự trong danh sách.")
        note.setObjectName("muted"); note.setWordWrap(True); note.setStyleSheet("font-size:11px;color:#a0a0a0;")
        filter_detail.addWidget(note)
        filter_detail.addWidget(_button("Xem thử 5 giây",self.preview_render,primary=True))
        filter_detail.addWidget(_button("Nhân đôi lớp bộ lọc",self.duplicate_layer))
        filter_detail.addWidget(_button("Xóa lớp bộ lọc",self.delete_layer,"danger"))
        self.filter_layer_frame.hide()
        detail = self._section(column,"Vị trí / Kích thước / Thời gian")
        self.layer_detail_frame = detail.parentWidget()
        self.layer_detail_frame.setEnabled(False)
        self.inline_layer_controls = []
        self.inline_toggle_controls = []
        enabled = self._clone(self.layer_enabled,"layerEnabled")
        detail.addWidget(enabled)
        self.inline_toggle_controls.append((self.layer_enabled,enabled))
        for key,label in [("text","Nội dung"),("x","Ngang (px)"),("y","Dọc (px)"),("width","Rộng (px)"),("height","Cao (px)"),("opacity","Độ rõ"),("start","Từ giây"),("end","Đến giây")]:
            control = self._field(detail,label,self.layer_fields[key],"layerDetail_"+key)
            self.inline_layer_controls.append((key,control))
            control.setEnabled(False)
        rotation = self._field(detail,"Xoay thành phần (độ)",self.layer_fields['rotation'],"layerRotation")
        self.inline_layer_controls.append(('rotation',rotation))
        for source,name in [(self.layer_flip_horizontal,"layerFlipHorizontal"),(self.layer_flip_vertical,"layerFlipVertical")]:
            control = self._clone(source,name)
            detail.addWidget(control)
            self.inline_toggle_controls.append((source,control))
        options = self._section(column,"Định dạng thành phần")
        self.layer_options_frame = options.parentWidget()
        self.layer_options_frame.setEnabled(False)
        blend = self._audio_section(options,"Hòa trộn")
        self.visual_blend_frame = blend.parentWidget().parentWidget()
        self._field(blend,"Chế độ",self.layer_blend,"layerBlendMode")
        text = self._section(options,"Văn bản")
        self.text_layer_frame = text.parentWidget()
        self._field(text,"Hiệu ứng chữ",self.layer_text_effect,"layerTextEffect")
        self._field(text,"Nền chữ",self.layer_bg_fit,"layerTextBackgroundFit")
        for source,label,name in [(self.layer_font,"Font","layerTextFont"),(self.layer_fields["font_size"],"Cỡ chữ","layerTextSize"),(self.layer_fields["color"],"Màu chữ","layerTextColor"),(self.layer_fields["outline"],"Độ dày viền","layerTextOutline"),(self.layer_fields["outline_color"],"Màu viền","layerTextOutlineColor"),(self.layer_fields["bg_opacity"],"Độ rõ nền","layerTextBackgroundOpacity"),(self.layer_fields["bg_color"],"Màu nền","layerTextBackgroundColor"),(self.layer_fields["bg_radius"],"Bo góc nền","layerTextBackgroundRadius"),(self.layer_marquee,"Chữ chạy","layerMarquee"),(self.layer_fields["speed"],"Tốc độ chữ","layerMarqueeSpeed")]:
            self._field(text,label,source,name)
        text.addWidget(self._clone(self.layer_bold,"layerTextBold"))
        text.addWidget(self._clone(self.layer_upper,"layerTextUppercase"))
        text.addWidget(_button("Màu chữ…",lambda:self.pick_layer_color("color")))
        text.addWidget(_button("Màu viền chữ…",lambda:self.pick_layer_color("outline_color")))
        text.addWidget(_button("Màu nền chữ…",lambda:self.pick_layer_color("bg_color")))
        image = self._section(options,"Ảnh / GIF")
        self.image_layer_frame = image.parentWidget()
        self._field(image,"Tệp ảnh",self.layer_fields['path'],"layerImageSource")
        image.addWidget(_button("Đổi ảnh / GIF…",self.choose_image_layer_source))
        image.addWidget(self._clone(self.layer_loop,"layerImageLoop"))
        image.addWidget(QLabel("Điểm bám · 3 × 3"))
        anchor_note = QLabel("Ngang / Dọc là khoảng lệch so với điểm bám đã chọn.")
        anchor_note.setObjectName('muted')
        anchor_note.setWordWrap(True)
        image.addWidget(anchor_note)
        grid = QGridLayout()
        self.anchor_buttons = {}
        for index,(anchor,glyph,label) in enumerate([('top-left','↖','Trên trái'),('top','↑','Trên giữa'),('top-right','↗','Trên phải'),('left','←','Giữa trái'),('center','•','Chính giữa'),('right','→','Giữa phải'),('bottom-left','↙','Dưới trái'),('bottom','↓','Dưới giữa'),('bottom-right','↘','Dưới phải')]):
            button = _button(glyph,lambda checked=False,value=anchor:self.layer_anchor.setCurrentText(value),'anchorButton')
            button.setCheckable(True)
            button.setToolTip(label)
            button.setProperty('qaName','anchor_'+anchor)
            button.setAccessibleName('anchor_'+anchor)
            self.anchor_buttons[anchor] = button
            grid.addWidget(button,index//3,index%3)
        image.addLayout(grid)
        self.layer_anchor.currentTextChanged.connect(self._update_anchor_buttons)
        self._update_anchor_buttons()
        blur = self._section(options,"Vùng làm mờ")
        self.blur_layer_frame = blur.parentWidget()
        for source,label,name in [(self.layer_shape,"Hình vùng mờ","layerBlurShape"),(self.layer_fields["strength"],"Độ mờ","layerBlurStrength"),(self.layer_fields["feather"],"Mềm viền","layerBlurFeather"),(self.layer_fields["corner"],"Bo góc","layerBlurCorner")]:
            self._field(blur,label,source,name)
        blur.addWidget(QLabel("Xem thử 5 giây để kiểm tra độ mờ xuất thực tế."))
        options.addWidget(_button("Nhân đôi thành phần",self.duplicate_layer))
        options.addWidget(_button("Xóa thành phần",self.delete_layer,"danger"))
        subtitles = self._section(column, "Phụ đề gốc / Phụ đề dịch")
        self._field(subtitles,"Hiệu ứng chữ",self.style_text_effect,"subtitleTextEffect")
        self._field(subtitles,"Nền chữ",self.style_bg_fit,"subtitleBackgroundFit")
        self._field(subtitles, "Bộ style", self.style_template)
        self._field(subtitles, "Font", self.style_font)
        self._field(subtitles, "Cỡ chữ", self.style_size)
        self._field(subtitles, "Vị trí", self.style_align)
        self._field(subtitles, "Viền chữ", self.style_outline)
        self._field(subtitles, "Nền chữ", self.style_bg)
        subtitles.addWidget(self._clone(self.style_bold))
        subtitles.addWidget(self._clone(self.style_upper))
        subtitles.addWidget(_button("Màu chữ…", lambda: self.pick_subtitle_color("color")))
        subtitles.addWidget(_button("Màu viền…", lambda: self.pick_subtitle_color("outline_color")))
        subtitles.addWidget(_button("Màu nền…", lambda: self.pick_subtitle_color("bg_color")))
        subtitles.addWidget(_button("Mở / chỉnh phụ đề…", lambda: self.open_card_subtitles(self.current_media)))

        logo = self._section(column, "Logo")
        self._field(logo, "Kích thước", self.logo_width)
        self._field(logo, "Vị trí", self.logo_pos)
        logo.addWidget(_button("Chọn ảnh logo…", self._choose_logo))
        audio = self._section(column, "Âm thanh")
        audio.addWidget(self._clone(self.mute))
        self._field(audio, "Âm gốc", self.controls["volume"])
        self._field(audio, "Nhạc nền", self.controls["music_volume"])
        self._field(audio, "Hiệu ứng", self.audio_fx)
        audio.addWidget(_button("Chọn nhạc nền…", self._choose_music))
        audio.addWidget(self._clone(self.music_loop))
        column.addStretch()
        return self.component_tabs

    def _update_background_controls(self,*_):
        mode = self.source_background_mode.currentData()
        for control,visible in [(self.background_color_field,mode=='color'),(self.background_blur_field,mode=='blur')]:
            control.setVisible(visible)
            control._field_caption.setVisible(visible)
            if hasattr(control,'_field_slider'):
                control._field_slider.setVisible(visible)
        self.background_color_button.setVisible(mode=='color')

    def _build_tweaks_tab(self):
        scroll, column = self._scroll_tab()
        shape = self._section(column, "Khung hình")
        self._field(shape, "Kích thước", self.resolution, "outputResolution")
        self._field(shape, "Vừa khung", self.fit)
        self._field(shape, "Khung / giây", self.fps)
        self._field(shape, "Tốc độ", self.controls["speed"], "videoSpeed")
        self._field(shape, "Xoay", self.rotation)
        shape.addWidget(self._clone(self.mirror, "mirrorVideo"))
        colors = self._section(column, "Màu và bộ lọc")
        self._field(colors, "Độ sáng", self.controls["brightness"])
        self._field(colors, "Tương phản", self.controls["contrast"])
        self._field(colors, "Bão hòa", self.controls["saturation"])
        filter_control = self._field(colors, "Bộ lọc", self.filter_pick,"videoFilter")
        labels = {'none':'Không','vignette':'Tối viền','vintage':'Hoài cổ','grain':'Hạt phim','glow':'Ánh sáng mềm','crt':'Màn hình CRT','stripe':'Sọc ngang','sharpen':'Làm nét','white_balance':'Cân bằng trắng','grayscale':'Đen trắng','saturation':'Tăng bão hòa','bright_contrast':'Độ sáng / Tương phản','lift_gamma_gain':'Lift / Gamma / Gain','lightleak':'Rò sáng'}
        for index in range(filter_control.count()):
            filter_control.setItemText(index,labels[self.filter_pick.itemData(index)])
        self._field(colors, "Cường độ", self.fx_fields["filter_intensity"])
        effects = self._section(column, "Zoom / Fade / Khung viền")
        effects.addWidget(self._clone(self.zoom_on))
        self._field(effects, "Mức zoom", self.fx_fields["zoom_amount"])
        self._field(effects, "Chu kỳ (s)", self.fx_fields["zoom_period"])
        self._field(effects,"Giữ zoom (s)",self.fx_fields['zoom_hold'],"zoomHold")
        self._field(effects, "Fade vào", self.fx_fields["fade_in"])
        self._field(effects, "Fade ra", self.fx_fields["fade_out"])
        self._field(effects, "Khung viền", self.fx_fields["frame_border"])
        effects.addWidget(_button("Màu khung viền…", self.pick_border_color))
        column.addWidget(_button("Xem thử 5 giây", self.preview_render, primary=True))
        column.addWidget(_button("Áp dụng style cho tất cả", self.apply_all))
        column.addWidget(_button("Đặt lại hiệu chỉnh", self.reset_adjustments))
        column.addStretch()
        return scroll

    def _build_bottom_bar(self):
        frame = QFrame()
        frame.setObjectName("batchBar")
        frame.setFixedHeight(48)
        column = QVBoxLayout(frame)
        column.setContentsMargins(10, 0, 10, 0)
        column.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.add_video_button = _button("Thêm video", self.import_files)
        self.add_video_button.setIcon(line_icon("folder", "#ffda70"))
        self.add_video_button.setObjectName("addVideo")
        self.add_video_button.setMinimumWidth(132)
        self._translated(self.add_video_button, "Thêm video", "Add video")
        row.addWidget(self.add_video_button)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setStyleSheet("color:#424242;")
        row.addWidget(separator)
        for vi, en, stage, name in [
            ("📝 Trích phụ đề", "📝 Extract subtitles", "recognize", "runRecognize"),
            ("🌐 Dịch", "🌐 Translate", "translate", "runTranslate"),
            ("🎤 Lồng tiếng", "🎤 Voice over", "dub", "runDub"),
        ]:
            button = _button(vi, lambda checked=False, s=stage: self.run_pipeline(s))
            button.setObjectName(name)
            button.setMinimumWidth({"runRecognize": 140, "runTranslate": 87, "runDub": 128}[name])
            self._translated(button, vi, en)
            row.addWidget(button)
        self.run_all_button = _button("📤 Xuất (0)", lambda: self.run_pipeline("all"), primary=True)
        self.run_all_button.setMinimumWidth(113)
        self.run_all_button.setProperty("qaName", "runAll")
        self.run_all_button.setAccessibleName("runAll")
        row.addWidget(self.run_all_button)
        self.stop_batch_button = _button("⏹ Dừng", self.cancel_job, "stopBatch")
        self.stop_batch_button.setMinimumWidth(90)
        self.stop_batch_button.setProperty("qaName", "stopBatch")
        self._translated(self.stop_batch_button, "⏹ Dừng", "⏹ Stop")
        self.stop_batch_button.setEnabled(False)
        row.addWidget(self.stop_batch_button)
        self.batch_progress = QLabel("Sẵn sàng")
        self.batch_progress.setObjectName("batchStatus")
        self.batch_progress.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.batch_progress.setStyleSheet("color:#949494;font-size:11px;")
        self.batch_progress.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        row.addWidget(self.batch_progress,1)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setStyleSheet("color:#424242;")
        row.addWidget(separator)
        self.page_navigation = QWidget()
        paging = QHBoxLayout(self.page_navigation)
        paging.setContentsMargins(0,0,0,0)
        paging.setSpacing(5)
        self.page_back = _button("‹", lambda: self._change_page(-1))
        self.page_back.setObjectName("previousPage")
        self.page_back.setFixedWidth(26)
        paging.addWidget(self.page_back)
        self.page_label = QLabel("1 / 1")
        self.page_label.setObjectName("pageIndicator")
        paging.addWidget(self.page_label)
        self.page_next = _button("›", lambda: self._change_page(1))
        self.page_next.setObjectName("nextPage")
        self.page_next.setFixedWidth(26)
        paging.addWidget(self.page_next)
        row.addWidget(self.page_navigation)
        row.addWidget(QLabel("Mỗi trang"))
        self.page_size = QComboBox()
        self.page_size.setObjectName("pageSize")
        self.page_size.addItems(["10","20","30","50","100"])
        self.page_size.setCurrentIndex(1)
        self.page_size.setFixedWidth(80)
        self.page_size.currentIndexChanged.connect(self._search_changed)
        row.addWidget(self.page_size)
        row.addWidget(QLabel("Layout"))
        self.columns = QComboBox()
        self.columns.setObjectName("gridColumns")
        self.columns.addItems(["1", "2", "3", "4"])
        self.columns.setCurrentIndex(1)
        self.columns.setFixedWidth(70)
        self.columns.currentIndexChanged.connect(lambda *_: self._render_cards())
        row.addWidget(self.columns)
        column.addLayout(row)
        return frame

    def navigate(self, index):
        if not self._shell_ready:
            return StudioWindow.navigate(self, index)
        if index == 6:
            self.main_views.setCurrentIndex(0)
        elif index == 4 and self.active_job:
            # Progress remains directly visible while the batch runs.
            self.main_views.setCurrentIndex(0)
        else:
            self.pages.setCurrentIndex(index)
            self.main_views.setCurrentIndex(1)
            if index == 7:
                self.refresh_canvas()

    def render_library(self):
        StudioWindow.render_library(self)
        if self._shell_ready:
            self._selected_paths.intersection_update(m["path"] for m in self.library)
            if not any(media["path"] == self.current_media for media in self.library):
                self.current_media = None
                self.layer_selection = None
                self.source_detail_frame.hide()
                self.layer_detail_frame.hide()
                self.layer_options_frame.hide()
                self.inline_detail_title.setText("Chọn một thành phần ở tab Các lớp")
                self.component_tabs.setCurrentIndex(0)
                for card in self.cards:
                    if card.poster:
                        card.poster.set_selected_layer("", -1)
            self._refresh_inline_layers()
            self._render_cards()

    def _filtered_media(self):
        query = self.search_box.text().strip().casefold()
        return [media for media in self.library if query in media["name"].casefold()]

    def _render_cards(self):
        if not self._shell_ready:
            return
        for card in self.cards:
            card.stop()
        self.cards = []
        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        filtered = self._filtered_media()
        per_page = int(self.page_size.currentText())
        page_count = max(1, math.ceil(len(filtered) / per_page))
        self._page_number = min(self._page_number, page_count - 1)
        start = self._page_number * per_page
        columns = self.columns.currentIndex() + 1
        for index, media in enumerate(filtered[start:start + per_page]):
            settings = self.settings() if media["path"] == self.current_media else self.video_settings.get(media["path"],self.default_settings)
            card = MediaCard(media, start + index + 1, self.thumbnail_path(media["path"]), self.assets.get(media["path"], {}), media["path"] in self._selected_paths, settings=settings)
            if media["path"] in self._batch_errors:
                card.stage_status.setText("Lỗi")
                card.stage_status.setToolTip(self._batch_errors[media["path"]])
                card.stage_status.setStyleSheet("font-size:11px;color:#ef4444;font-weight:600;")
                card.stage_status.show()
            card.selected.connect(self._select_path)
            card.activated.connect(self._activate_media)
            card.removed.connect(self.remove_video)
            card.edit_subtitles.connect(self.open_card_subtitles)
            card.more_requested.connect(self._card_menu)
            card.ratio_changed.connect(self.set_card_ratio)
            card.apply_requested.connect(self._apply_card_all)
            card.fullscreen_requested.connect(lambda path,item:self._fullscreen_card(item))
            card.preview_changed.connect(self._preview_layer_changed)
            card.preview_chosen.connect(self._preview_layer_chosen)
            card.setProperty("current",media["path"] == self.current_media)
            self.card_layout.addWidget(card, index // columns, index % columns)
            self.cards.append(card)
            if not self.thumbnail_path(media["path"]).is_file():
                self.make_thumbnail(media["path"])
        for index in range(columns):
            self.card_layout.setColumnStretch(index, 1)
        self.grid_views.setCurrentIndex(1 if filtered else 0)
        self.video_count.setText(f"{len(self.library)} video")
        self.page_label.setText(f"{self._page_number + 1} / {page_count}")
        self.page_back.setEnabled(self._page_number > 0)
        self.page_next.setEnabled(self._page_number + 1 < page_count)
        self.page_navigation.setVisible(page_count > 1)
        self._update_selection_count()

    def make_thumbnail(self, path):
        target = self.thumbnail_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not hasattr(self, "thumbnail_pending"):
            self.thumbnail_pending = set()
        if path in self.thumbnail_pending:
            return
        self.thumbnail_pending.add(path)

        def generate(progress, cancel):
            command = [engine.tool_path("ffmpeg"), "-v", "error", "-y", "-ss", "0", "-i", path, "-frames:v", "1", "-vf", "scale=720:720:force_original_aspect_ratio=decrease", "-threads", "1", str(target)]
            result = subprocess.run(command, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return result.returncode == 0

        def done(ok):
            self.thumbnail_pending.discard(path)
            if ok:
                for card in self.cards:
                    if card.path == path:
                        card.update_thumbnail()

        self.background(generate, done)

    def background(self, function, done):
        if self._close_requested:
            return
        worker = Worker(function)
        self.workers.add(worker)
        worker.result.connect(lambda result: done(result) if not self._close_requested else None)
        worker.failed.connect(lambda message: self.info("Không thể xử lý", message) if not self._close_requested else None)

        def finished():
            self.workers.discard(worker)
            worker.deleteLater()

        worker.finished.connect(finished)
        worker.start()

    def info(self, title, message):
        if not self._close_requested:
            return StudioWindow.info(self, title, message)

    def _search_changed(self, *_):
        self._page_number = 0
        self._render_cards()

    def _change_page(self, direction):
        self._page_number = max(0, self._page_number + direction)
        self._render_cards()
        self.card_scroll.verticalScrollBar().setValue(0)

    def _select_all_changed(self, checked):
        paths = {media["path"] for media in self._filtered_media()}
        if checked:
            self._selected_paths.update(paths)
        else:
            self._selected_paths.difference_update(paths)
        for card in self.cards:
            card.check.setChecked(card.path in self._selected_paths)
        self._update_selection_count()

    def _select_path(self, path, checked):
        if checked:
            self._selected_paths.add(path)
            self._activate_media(path)
        else:
            self._selected_paths.discard(path)
        self._update_selection_count()

    def _update_selection_count(self):
        self.selected_count.setText(f"{len(self._selected_paths)} đã chọn" if self._language == "vi" else f"{len(self._selected_paths)} selected")
        paths = {media["path"] for media in self._filtered_media()}
        self.select_all.blockSignals(True)
        self.select_all.setChecked(bool(paths) and paths.issubset(self._selected_paths))
        self.select_all.blockSignals(False)
        self.run_all_button.setText(f"📤 Xuất ({len(self._selected_paths)})" if self._language == "vi" else f"📤 Export ({len(self._selected_paths)})")
        self.run_all_button.setEnabled(bool(self._selected_paths) and bool(self.pipe_folder.text().strip()) and self.active_job is None)

    def _activate_media(self, path):
        if not path or path == self.current_media:
            return
        if self.current_media:
            self.video_settings[self.current_media] = copy.deepcopy(self.settings())
        self.current_media = path
        self.layer_selection = None
        self._property_identity = ("video",0)
        self._subtitle_selection = None
        self.subtitle_detail_frame.hide()
        self.apply_all_settings(self.video_settings.get(path, getattr(self, "default_settings", self.settings())))
        self.inline_detail_title.setText("Video gốc")
        self.source_detail_frame.show()
        self.layer_detail_frame.hide()
        self.layer_options_frame.hide()
        self.component_tabs.setCurrentIndex(0)
        assets = self.assets.get(path, {})
        self.original_srt = assets.get("original_subtitle", "")
        self.translated_srt = assets.get("translated_subtitle", "")
        self.layer_count.setText(f"{len(self.layer_entries())+len(self.advanced.get("audio_layers",[]))+len(self.advanced.get("filter_layers",[]))} thành phần")
        self.current_video_label.setText(Path(path).name)
        for card in self.cards:
            card.setProperty("current",card.path == path)
            card.style().unpolish(card)
            card.style().polish(card)
        self._refresh_inline_layers()
        self.statusBar().showMessage(Path(path).name)

    def selected_videos(self):
        if self._shell_ready:
            return [media for media in self.library if media["path"] in self._selected_paths]
        return StudioWindow.selected_videos(self)

    def remove_video(self, path):
        was_current = self.current_media == path
        for card in self.cards:
            if card.path == path:
                card.stop()
        self.library = [media for media in self.library if media["path"] != path]
        self._selected_paths.discard(path)
        self.video_settings.pop(path, None)
        self.assets.pop(path, None)
        if was_current:
            self.current_media = None
        self.render_library()
        if was_current and self.library:
            self._activate_media(self.library[0]["path"])
        self.persist_library()
        self.mark_dirty()

    def _card_menu(self, path, card):
        self._activate_media(path)
        menu = QMenu(self)
        menu.addAction("Cắt / ghép video…", lambda: self._edit_video(path))
        menu.addAction("Xem toàn màn hình", lambda: self._fullscreen_card(card))
        menu.addAction("Thành phần và hiệu ứng…", self._show_inline_components)
        menu.addAction("Sửa phụ đề…", lambda: self.open_card_subtitles(path))
        menu.addAction("Nhập phụ đề gốc…", lambda: self._import_card_subtitles(path,"original"))
        menu.addAction("Nhập phụ đề dịch…", lambda: self._import_card_subtitles(path,"translated"))
        menu.addAction("Sao chép phụ đề gốc",lambda:self._copy_subtitles(path,"original_subtitle"))
        menu.addAction("Sao chép phụ đề dịch",lambda:self._copy_subtitles(path,"translated_subtitle"))
        menu.addAction("Áp dụng style cho tất cả", self.apply_all)
        menu.addSeparator()
        menu.addAction("Mở thư mục", lambda: self.open_folder(str(Path(path).parent)))
        output = self.assets.get(path,{}).get("video")
        if output:
            menu.addAction("Mở thư mục kết quả",lambda:self.open_folder(str(Path(output).parent)))
            menu.addAction("Phát video đã xuất",lambda:self._play_output(output))
        menu.addSeparator()
        menu.addAction("Chọn tất cả",lambda:self.select_all.setChecked(True))
        menu.addAction("Bỏ chọn tất cả",self._select_none)
        menu.addAction("Chọn video có lỗi",self._select_errors)
        menu.addAction("Bỏ kết quả đã chọn khỏi dự án",self._clear_selected_outputs)
        menu.addAction("Bỏ video đã chọn",self._remove_selected)
        menu.addAction("Bỏ tất cả video",self._remove_all)
        menu.addAction("Bỏ khỏi dự án", lambda: self.remove_video(path))
        menu.exec(card.mapToGlobal(card.rect().bottomRight()))

    def _show_inline_components(self):
        self.navigate(6)
        self.right_tabs.setCurrentIndex(1)
        self.component_tabs.setCurrentIndex(0)

    def _copy_subtitles(self,path,role):
        subtitle = self.assets.get(path,{}).get(role)
        if subtitle and Path(subtitle).is_file():
            import subtitle_tools
            rows = subtitle_tools.load_subtitles(subtitle)
            QApplication.clipboard().setText("\n".join(row["text"] for row in rows))
            self.statusBar().showMessage("Đã sao chép nội dung phụ đề.",5000)
        else:
            self.info("Chưa có phụ đề","Nhập hoặc tạo phụ đề trước khi sao chép.")

    def _play_output(self,path):
        self.output_player.setSource(QUrl.fromLocalFile(path))
        self.output_hint.setText(path)
        self.preview_tabs.setCurrentIndex(1)
        self.navigate(2)
        self.output_player.play()

    def _select_none(self):
        self._selected_paths.clear()
        for card in self.cards:
            card.check.setChecked(False)
        self._update_selection_count()

    def _select_errors(self):
        self._selected_paths = set(self._batch_errors)
        for card in self.cards:
            card.check.setChecked(card.path in self._selected_paths)
        self._update_selection_count()

    def _clear_selected_outputs(self):
        for path in self._selected_paths:
            self.assets.pop(path,None)
            settings = self.video_settings.get(path,{})
            for key in ("subtitle_path","voice_path","background_path"):
                settings[key] = ""
        self.mark_dirty()
        self.render_library()

    def _remove_selected(self):
        for path in list(self._selected_paths):
            self.remove_video(path)

    def _remove_all(self):
        for path in [media["path"] for media in self.library]:
            self.remove_video(path)

    def _fullscreen_card(self, card):
        if card.poster:
            dialog = QDialog(self)
            dialog.setWindowTitle(card.media["name"])
            dialog.setWindowFlags(dialog.windowFlags()|Qt.WindowType.Window)
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(0,0,0,0)
            preview = EditablePreview(card.media,card.thumbnail,card.settings_snapshot,card.assets_snapshot)
            preview.changed.connect(lambda kind,index,data:self._preview_layer_changed(card.path,kind,index,data))
            preview.chosen.connect(lambda kind,index:self._preview_layer_chosen(card.path,kind,index))
            layout.addWidget(preview)
            close = _button("Đóng / Esc",dialog.close,"subtle")
            layout.addWidget(close,0,Qt.AlignmentFlag.AlignRight)
            card.fullscreen_preview = preview
            card.ensure_player().play()
            dialog.showFullScreen()
            dialog.exec()
            card.fullscreen_preview = None
            return
        card.ensure_player()
        card.preview.setCurrentIndex(1)
        card.video.setFullScreen(True)
        card.media_player.play()

    def settings(self):
        settings = StudioWindow.settings(self)
        if self._canvas_size is not None:
            settings["width"],settings["height"] = self._canvas_size
        if hasattr(self,"voice_parallel"):
            settings["voice_pipeline"] = dict(voice=self.pipe_voice.currentText(), voice_parallel=self.voice_parallel.value(),
                voice_speed=self.tts_speed.value(), voice_delay=self.tts_delay.value(), voice_fit=self.tts_fit.currentData(),
                split_voice=self.pipe_separate.isChecked(), separation_mode=self.separation_mode.currentData(),voice_max_speed=self.voice_max_speed.value())
            settings["recognition_pipeline"]=dict(recognition_device=self.recognition_device.currentData(),recognition_batch_size=self.recognition_batch_size.value())
        if hasattr(self,"subtitle_detail_frame"):
            settings = self._with_subtitle_settings(settings)
        return settings

    def apply_all_settings(self, settings):
        previous_syncing = self.syncing
        self.syncing = True
        self._audio_loading = True
        self.voice_volume.setMaximum(max(2,float(settings.get("voice_volume",1))))
        StudioWindow.apply_all_settings(self,settings)
        advanced = {**settings, **(settings.get("advanced") or {})}
        for key in ("source_stem_volumes","source_stem_paths","source_fade_in","source_fade_out","voice_fade_in","voice_fade_out","source_audio_enabled","voice_audio_enabled"):
            self.advanced.pop(key,None)
            if key in advanced:
                self.advanced[key] = copy.deepcopy(advanced[key])
        gains = advanced.get("source_stem_volumes") or {}
        for key,control in self.audio_fields.items():
            control.setValue(gains.get(key,1) if key in ("vocals","drums","bass","other") else advanced.get(key,0))
        self.source_audio_enabled.setChecked(advanced.get("source_audio_enabled",not settings.get("mute",False)))
        self.voice_audio_enabled.setChecked(advanced.get("voice_audio_enabled",True))
        pipeline = settings.get("voice_pipeline") or {}
        self.voice_parallel.setValue(pipeline.get("voice_parallel",1))
        self.tts_speed.setValue(pipeline.get("voice_speed",1))
        self.tts_delay.setValue(pipeline.get("voice_delay",0))
        self.voice_max_speed.setValue(pipeline.get("voice_max_speed",4))
        recognition=settings.get("recognition_pipeline") or {}
        self.recognition_device.setCurrentIndex(max(0,self.recognition_device.findData(recognition.get("recognition_device","cpu"))))
        self.recognition_batch_size.setValue(recognition.get("recognition_batch_size",1))
        self.tts_fit.setCurrentIndex(max(0,self.tts_fit.findData(pipeline.get("voice_fit","fit"))))
        self.separation_mode.setCurrentIndex(max(0,self.separation_mode.findData(pipeline.get("separation_mode","vocals"))))
        self.pipe_separate.setChecked(pipeline.get("split_voice",False))
        if pipeline.get("voice"):
            if self.pipe_voice.findText(pipeline["voice"]) < 0:
                self.pipe_voice.addItem(pipeline["voice"])
            self.pipe_voice.setCurrentText(pipeline["voice"])
        self._audio_loading = False
        if hasattr(self,"subtitle_detail_frame"):
            self._load_subtitle_settings(settings)
        self.syncing = previous_syncing
        self._canvas_size = (int(settings.get("width",0)),int(settings.get("height",0)))
        if self._shell_ready:
            self._refresh_inline_layers()
            self._sync_current_card()

    def _resolution_changed(self, *_):
        if not self.syncing:
            sizes = [(0,0),(1920,1080),(1080,1920),(1280,720),(720,1280),(1080,1080),(854,480)]
            self._canvas_size = sizes[self.resolution.currentIndex()]
            self.mark_dirty()

    def set_card_ratio(self,path,ratio):
        self._activate_media(path)
        self._canvas_size = RATIOS[ratio]
        self.mark_dirty()
        self._sync_current_card()

    def _apply_card_all(self,path):
        self._activate_media(path)
        self.apply_all()

    def apply_all(self):
        shared = self.settings()
        for media in self.library:
            existing = self.video_settings.get(media["path"],{})
            value = copy.deepcopy(shared)
            for key in ("subtitle_path","voice_path","background_path"):
                value[key] = existing.get(key,"")
            assets = self.assets.get(media["path"],{})
            value["subtitle_path"] = assets.get("translated_subtitle") or assets.get("original_subtitle") or value["subtitle_path"]
            value["voice_path"] = assets.get("narration") or value["voice_path"]
            value["background_path"] = assets.get("background") or value["background_path"]
            old_tracks={track.get('role'):track for track in existing.get('subtitle_tracks',[]) if isinstance(track,dict)}
            if 'subtitle_tracks' in value:
                for track in value['subtitle_tracks']:
                    role=track.get('role')
                    track['path']=assets.get('original_subtitle' if role=='original' else 'translated_subtitle') or old_tracks.get(role,{}).get('path','')
            elif old_tracks:
                value['subtitle_tracks']=copy.deepcopy(list(old_tracks.values()))
            own_stems=existing.get('source_stem_paths') or {}
            own_stems={role:assets.get(role) or own_stems.get(role) for role in ('vocals','drums','bass','other') if assets.get(role) or own_stems.get(role)}
            if own_stems:
                value['source_stem_paths']=own_stems
            else:
                value.pop('source_stem_paths',None)
            self.video_settings[media["path"]] = value
        if self.current_media:
            self.apply_all_settings(self.video_settings[self.current_media])
        self.mark_dirty()
        for card in self.cards:
            card.set_content(self.video_settings[card.path],self.assets.get(card.path,{}))
        self.statusBar().showMessage(f"Đã áp dụng style cho {len(self.library)} video.",5000)

    def _sync_current_card(self):
        if not self._shell_ready or not self.current_media or self.syncing or self.layer_loading:
            return
        settings = copy.deepcopy(self.settings())
        self.video_settings[self.current_media] = settings
        for card in self.cards:
            if card.path == self.current_media:
                card.set_content(settings,self.assets.get(card.path,{}))
                if card.fullscreen_preview:
                    card.fullscreen_preview.set_content(card.thumbnail,settings,self.assets.get(card.path,{}))

    def _preview_layer_changed(self,path,kind,index,data):
        self._activate_media(path)
        if kind in ("text_layers","image_layers","blur_zones") and 0 <= index < len(self.advanced.get(kind,[])):
            self.canvas_changed(kind,index,data)
            self.mark_dirty()
            self._refresh_inline_layers()

    def _preview_layer_chosen(self,path,kind,index):
        self._activate_media(path)
        if kind == "subtitle_tracks":
            tracks=self.settings().get("subtitle_tracks",[])
            if 0 <= index < len(tracks):
                self._group_layer_chosen(("subtitle",tracks[index].get("role","original")))
                self.right_tabs.setCurrentIndex(1)
            return
        if kind in ("text_layers","image_layers","blur_zones","audio_layers","filter_layers"):
            self.choose_layer(kind,index)
            self.right_tabs.setCurrentIndex(1)
            self.component_tabs.setCurrentIndex(1)

    def _inline_layer_chosen(self,item,previous):
        if self._inline_refreshing or item is None:
            return
        kind,index = item.data(Qt.ItemDataRole.UserRole)
        self._property_identity = (kind,index)
        if kind == "subtitle":
            self._choose_subtitle_detail(index)
            self.layer_groups.select((kind,index))
            return
        self._subtitle_selection = None
        self.subtitle_detail_frame.hide()
        if kind == "audio":
            self._choose_audio_detail(index)
            return
        self.audio_badge.hide()
        for frame in self.audio_detail_frames.values():
            frame.hide()
        self.generic_detail_frame.show()
        self._audio_selection = None
        if kind in ("text_layers","image_layers","blur_zones","audio_layers","filter_layers"):
            self.choose_layer(kind,index)
            self.component_tabs.setCurrentIndex(1)
        elif kind == "video":
            self.layer_selection = None
            self.inline_detail_title.setText("Video gốc")
            self.source_detail_frame.show()
            self.audio_layer_frame.hide()
            self.filter_layer_frame.hide()
            self.layer_detail_frame.hide()
            self.layer_options_frame.hide()
            self.component_tabs.setCurrentIndex(1)
            for card in self.cards:
                if card.poster:
                    card.poster.set_selected_layer("",-1)

    def _inline_layer_enabled(self,item):
        if self._inline_refreshing or not self._shell_ready:
            return
        identity = item.data(Qt.ItemDataRole.UserRole)
        if identity and identity[0] == "subtitle":
            self._set_subtitle_enabled(identity[1],item.checkState() == Qt.CheckState.Checked)
            return
        if identity and identity[0] == "audio":
            control = self.source_audio_enabled if identity[1] == "A0" else self.voice_audio_enabled
            control.setChecked(item.checkState() == Qt.CheckState.Checked)
            return
        if not identity or identity[0] not in ('text_layers','image_layers','blur_zones','audio_layers','filter_layers'):
            return
        kind,index = identity
        self.advanced[kind][index]['enabled'] = item.checkState() == Qt.CheckState.Checked
        if self.layer_selection == (kind,index):
            self.choose_layer(kind,index)
        self.mark_dirty()
        self.refresh_canvas()

    def _layer_context_menu(self,point):
        item = self.inline_layers.itemAt(point)
        if item is None:
            return
        identity = item.data(Qt.ItemDataRole.UserRole)
        if not identity or identity[0] not in ('text_layers','image_layers','blur_zones','audio_layers','filter_layers'):
            return
        self.choose_layer(*identity)
        menu = QMenu(self)
        menu.addAction('Nhân đôi thành phần',self.duplicate_layer)
        enabled = self.audio_layer_enabled if identity[0] == 'audio_layers' else self.filter_layer_enabled if identity[0] == 'filter_layers' else self.layer_enabled
        menu.addAction('Bật / Tắt' if identity[0] in ('audio_layers','filter_layers') else 'Hiện / Ẩn',
                       lambda:enabled.setChecked(not enabled.isChecked()))
        menu.addAction('Xóa thành phần',self.delete_layer)
        menu.exec(self.inline_layers.mapToGlobal(point))

    def delete_layer(self):
        StudioWindow.delete_layer(self)
        self._property_identity = ("video",0)
        if self._shell_ready:
            self.layer_detail_frame.setEnabled(False)
            self.layer_options_frame.setEnabled(False)
            self.layer_detail_frame.hide()
            self.layer_options_frame.hide()
            self.audio_layer_frame.hide()
            self.filter_layer_frame.hide()
            self.inline_detail_title.setText('Chọn một thành phần ở tab Các lớp')
            self.component_tabs.setCurrentIndex(0)
            for card in self.cards:
                if card.poster:
                    card.poster.set_selected_layer('', -1)
            self._refresh_inline_layers()

    def _group_layer_chosen(self, identity):
        identity = tuple(identity)
        self._property_identity = identity
        for index in range(self.inline_layers.count()):
            item = self.inline_layers.item(index)
            if tuple(item.data(Qt.ItemDataRole.UserRole)) == identity:
                previous = self.inline_layers.currentItem()
                self.inline_layers.setCurrentItem(item)
                if previous is item:
                    self._inline_layer_chosen(item, previous)
                break
        self.layer_groups.select(identity)

    def _group_layer_enabled(self, identity, enabled):
        identity = tuple(identity)
        for index in range(self.inline_layers.count()):
            item = self.inline_layers.item(index)
            if tuple(item.data(Qt.ItemDataRole.UserRole)) == identity:
                item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
                return

    def _group_layer_deleted(self, identity):
        identity = tuple(identity)
        if identity[0] not in ("text_layers","image_layers","blur_zones","filter_layers","audio_layers"):
            return
        self.choose_layer(*identity)
        self.delete_layer()

    def _refresh_inline_groups(self):
        self._refresh_inline_layers()

    def _grouped_layer_data(self, has_media):
        groups = {key:[] for key in ("media","text","blur","filter","audio")}
        def entry(group, tag, name, identity, enabled=True, toggle=True, delete=True, tooltip=""):
            groups[group].append(dict(tag=tag,name=name,identity=identity,enabled=bool(enabled),toggle=toggle,delete=delete,tooltip=tooltip))
        if has_media:
            entry("media","V","Video gốc",("video",0),toggle=False,delete=False)
            for index,row in enumerate(self.advanced.get("image_layers",[])):
                entry("media","I"+str(index),Path(row.get("path","Phương tiện")).name,("image_layers",index),row.get("enabled",True))
            for tag,role,name in (("S0","original","Phụ đề gốc"),("S1","translated","Phụ đề dịch")):
                entry("text",tag,name,("subtitle",role),self._subtitle_role_enabled(role),delete=False)
            for index,row in enumerate(self.advanced.get("text_layers",[])):
                entry("text","T"+str(index),"Tiêu đề",("text_layers",index),row.get("enabled",True),tooltip=str(row.get("text", "Tiêu đề")))
            for index,row in enumerate(self.advanced.get("blur_zones",[])):
                entry("blur","B"+str(index),"Vùng mờ",("blur_zones",index),row.get("enabled",True))
            for index,row in enumerate(self.advanced.get("filter_layers",[])):
                selected = self.filter_layer_kind.findData(row.get("filter","none"))
                name = self.filter_layer_kind.itemText(selected) if selected >= 0 else "Bộ lọc"
                entry("filter","F"+str(index),name,("filter_layers",index),row.get("enabled",True))
            entry("audio","A0","Âm gốc",("audio","A0"),self.source_audio_enabled.isChecked(),delete=False)
            entry("audio","A1","Lồng tiếng",("audio","A1"),self.voice_audio_enabled.isChecked(),delete=False)
            for index,row in enumerate(self.advanced.get("audio_layers",[])):
                entry("audio","A"+str(index+2),Path(row.get("path","Âm thanh")).name,("audio_layers",index),row.get("enabled",True))
        titles = dict(media="Phương tiện",text="Văn bản",blur="Vùng mờ",filter="Bộ lọc",audio="Âm thanh")
        return [dict(id=key,title=titles[key],entries=groups[key]) for key in groups]

    def _update_anchor_buttons(self,*_):
        for anchor,button in self.anchor_buttons.items():
            button.setChecked(anchor==self.layer_anchor.currentText())

    def _refresh_inline_layers(self):
        if not self._shell_ready:
            return
        self._inline_refreshing = True
        self.inline_layers.clear()
        has_media = bool(self.current_media)
        if has_media:
            item = QListWidgetItem("Video gốc · "+Path(self.current_media).name)
            item.setData(Qt.ItemDataRole.UserRole,("video",0))
            self.inline_layers.addItem(item)
            for role,title in (("original","Phụ đề gốc"),("translated","Phụ đề dịch")):
                item = QListWidgetItem(title)
                item.setData(Qt.ItemDataRole.UserRole,("subtitle",role))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if self._subtitle_role_enabled(role) else Qt.CheckState.Unchecked)
                self.inline_layers.addItem(item)
            for identity,title,control in (("A0","Âm gốc",self.source_audio_enabled),("A1","Lồng tiếng",self.voice_audio_enabled)):
                item = QListWidgetItem(identity+"   "+title)
                item.setData(Qt.ItemDataRole.UserRole,("audio",identity))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if control.isChecked() else Qt.CheckState.Unchecked)
                self.inline_layers.addItem(item)
                if getattr(self,"_audio_selection",None) == identity:
                    self.inline_layers.setCurrentItem(item)
        entries = self.layer_entries()+[(kind,i,row) for kind in ('audio_layers','filter_layers') for i,row in enumerate(self.advanced.get(kind,[]))]
        for kind,index,row in (entries if has_media else []):
            name = row.get("text","Văn bản")[:32] if kind == "text_layers" else Path(row.get("path","Ảnh / GIF")).name if kind == "image_layers" else "Âm thanh · "+Path(row.get('path','')).name if kind == 'audio_layers' else "Vùng làm mờ"
            if kind == 'filter_layers':
                label_index=self.filter_layer_kind.findData(row.get('filter','none'))
                name="Bộ lọc · "+(self.filter_layer_kind.itemText(label_index) if label_index>=0 else str(row.get('filter','none')))
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole,(kind,index))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row.get('enabled',True) else Qt.CheckState.Unchecked)
            self.inline_layers.addItem(item)
            if self.layer_selection == (kind,index):
                self.inline_layers.setCurrentItem(item)
        self._inline_refreshing = False
        self.layer_groups.set_groups(self._grouped_layer_data(has_media),self._property_identity)
        self.current_video_label.setText(Path(self.current_media).name if has_media else "Chọn video để chỉnh thành phần")
        self.layer_count.setText(f"{len(entries) if has_media else 0} thành phần")
        self.inline_layers.setEnabled(has_media)
        for button in self.layer_add_buttons:
            button.setEnabled(has_media)
        self.component_apply_button.setEnabled(has_media)
        self.component_duplicate_button.setEnabled(has_media and self.layer_selection is not None)
        self.component_tabs.setTabEnabled(1, has_media)

    def refresh_canvas(self):
        StudioWindow.refresh_canvas(self)
        if self._shell_ready:
            self._refresh_inline_layers()
            self._sync_current_card()

    def choose_layer(self,kind,index):
        if kind not in self.advanced or not 0 <= index < len(self.advanced[kind]):
            return
        StudioWindow.choose_layer(self,kind,index)
        if self._shell_ready:
            self._property_identity = (kind,index)
            self._subtitle_selection = None
            self.subtitle_detail_frame.hide()
            self.layer_groups.select((kind,index))
            self._audio_selection = None
            self.audio_badge.hide()
            for frame in self.audio_detail_frames.values():
                frame.hide()
            self.generic_detail_frame.show()
            self.source_detail_frame.hide()
            self.audio_layer_frame.setVisible(kind=='audio_layers')
            self.filter_layer_frame.setVisible(kind=='filter_layers')
            if kind in ('audio_layers','filter_layers'):
                self.layer_detail_frame.hide()
                self.layer_options_frame.hide()
                self.inline_detail_title.setText('Âm thanh · '+Path(self.advanced[kind][index].get('path','')).name if kind=='audio_layers' else 'Lớp bộ lọc')
                self.component_tabs.setCurrentIndex(1)
                self.component_duplicate_button.setEnabled(bool(self.current_media))
                for card in self.cards:
                    if card.poster:
                        card.poster.set_selected_layer('',-1)
                return
            self.layer_detail_frame.show()
            self.layer_options_frame.show()
            self.layer_detail_frame.setEnabled(True)
            self.layer_options_frame.setEnabled(True)
            self.text_layer_frame.setVisible(kind=='text_layers')
            self.image_layer_frame.setVisible(kind=='image_layers')
            self.blur_layer_frame.setVisible(kind=='blur_zones')
            self.visual_blend_frame.setVisible(kind in ('text_layers','image_layers'))
            self.inline_detail_title.setText({"text_layers":"Văn bản","image_layers":"Ảnh / GIF","blur_zones":"Vùng làm mờ"}[kind])
            for key,control in self.inline_layer_controls:
                control.setEnabled(self.layer_fields[key].isEnabled())
                if hasattr(control,'_field_slider'):
                    control._field_slider.setEnabled(self.layer_fields[key].isEnabled())
            for source,control in self.inline_toggle_controls:
                control.setEnabled(source.isEnabled())
            self._update_anchor_buttons()
            self.component_tabs.setCurrentIndex(1)
            self.component_duplicate_button.setEnabled(bool(self.current_media))
            for card in self.cards:
                if card.path == self.current_media and card.poster:
                    card.poster.set_selected_layer(kind,index)

    def _choose_audio_detail(self, identity):
        self._property_identity = ("audio",identity)
        self._subtitle_selection = None
        self.subtitle_detail_frame.hide()
        self.layer_groups.select(("audio",identity))
        self._audio_selection = identity
        self.layer_selection = None
        self.generic_detail_frame.hide()
        self.audio_badge.setText(identity)
        self.audio_badge.show()
        self.inline_detail_title.setText("Âm gốc" if identity=="A0" else "Lồng tiếng")
        self.inline_detail_title.setStyleSheet("font-size:15px;font-weight:600;color:#eeeeee;")
        for key,frame in self.audio_detail_frames.items():
            frame.setVisible(key==identity)
        split = self.pipe_separate.isChecked()
        # Row layouts have no QWidget container; toggle all items of the matching
        # row together, leaving exactly the stem sliders supported by the mode.
        audio_layout = self.source_total_volume.parentWidget().layout()
        for index in range(audio_layout.count()):
            layout = audio_layout.itemAt(index).layout()
            if layout:
                detailed = self.separation_mode.currentData()=="stems"
                visible = (not split) if index==0 else split and (detailed or index in (1,4))
                for child in range(layout.count()):
                    widget = layout.itemAt(child).widget()
                    if widget:
                        widget.setVisible(visible)
        self.component_tabs.setCurrentIndex(1)

    def refresh_voice_display(self,*_):
        if hasattr(self,"voice_display"):
            identity = self.pipe_voice.currentText()
            provider = identity.split(":",1)[0] if ":" in identity else "edge"
            labels = {"edge":"Edge TTS", "fpt":"FPT AI", "elevenlabs":"ElevenLabs",
                      "ngochuyen":"Ngọc Huyền · Offline", "ngochuyen_v2":"Ngọc Huyền V2 · Offline"}
            if provider in ("ngochuyen","ngochuyen_v2"):
                import local_tts
                name=next((row['FriendlyName'] for row in local_tts.catalog(provider) if row['ShortName']==identity),identity.split(':',1)[-1])
                self.voice_display.setText(name)
            else:
                friendly = {"vi-VN-HoaiMyNeural":"Hoài My", "vi-VN-NamMinhNeural":"Nam Minh", "en-US-JennyNeural":"Jenny"}.get(identity,identity.split(":",1)[-1])
                self.voice_display.setText(friendly)
            if hasattr(self,"voice_engine_display"):
                self.voice_engine_display.setText("▸ "+provider)
            self.voice_select_button.setToolTip(labels.get(provider,provider)+" · "+identity)

    def _edit_video(self, path):
        self._activate_media(path)
        existing = next((i for i, clip in enumerate(self.clips) if clip["path"] == path), None)
        if existing is not None:
            self.render_clips(existing)
            self.navigate(2)
        else:
            self.ingest([path], True)

    def _import_card_subtitles(self, path,role="original"):
        if not path:
            self.info("Chưa chọn video", "Chọn một video để nhập phụ đề.")
            return
        selected = QFileDialog.getOpenFileName(self, "Nhập phụ đề", "", "Phụ đề (*.srt *.vtt)")[0]
        if selected:
            self._activate_media(path)
            self.assets.setdefault(path, {})["translated_subtitle" if role == "translated" else "original_subtitle"] = selected
            self.video_settings.setdefault(path, copy.deepcopy(self.settings()))["subtitle_path"] = selected
            self.load_srt_table(selected, role)
            self.open_card_subtitles(path)
            self.render_library()

    def open_card_subtitles(self, path):
        if path:
            self._activate_media(path)
        assets = self.assets.get(path, {}) if path else {}
        media = next((item for item in self.library if item["path"] == path),None)
        if media:
            try:
                from subtitle_editor import SubtitleEditorDialog
            except ImportError:
                pass
            else:
                dialog = SubtitleEditorDialog(self,media,self.settings(),assets)
                dialog.saved.connect(lambda role,subtitle:self._attach_saved_subtitle(path,role,subtitle))
                dialog.voice_ready.connect(lambda voice:self._attach_narration(path,voice))
                dialog.exec()
                return
        subtitle = assets.get("translated_subtitle") or assets.get("original_subtitle") or self.subtitle.text().strip()
        if subtitle and Path(subtitle).is_file():
            self.load_srt_table(subtitle, "translated" if subtitle == assets.get("translated_subtitle") else "original")
        self.navigate(3)

    def _attach_saved_subtitle(self,path,role,subtitle):
        self._activate_media(path)
        role_key = "translated_subtitle" if role in ("translated","translated_subtitle","bilingual") else "original_subtitle"
        self.assets.setdefault(path,{})[role_key] = subtitle
        self.load_srt_table(subtitle,"translated" if role_key == "translated_subtitle" else "original")
        self.video_settings.setdefault(path,copy.deepcopy(self.settings()))["subtitle_path"] = subtitle
        self.mark_dirty()
        self._sync_current_card()

    def _attach_narration(self,path,voice):
        self._activate_media(path)
        self.assets.setdefault(path,{})["narration"] = voice
        self.voice_path.setText(voice)
        self.mute.setChecked(True)
        self.mark_dirty()
        self._sync_current_card()

    def save_srt(self):
        saved = StudioWindow.save_srt(self)
        if saved and self.current_media:
            role = "translated_subtitle" if getattr(self, "_subtitle_role", "original") == "translated" else "original_subtitle"
            self.assets.setdefault(self.current_media, {})[role] = saved
            if role == "translated_subtitle":
                self.translated_srt = saved
            else:
                self.original_srt = saved
            self.video_settings.setdefault(self.current_media, copy.deepcopy(self.settings()))["subtitle_path"] = saved
            if self._shell_ready:
                self._render_cards()
        return saved

    def load_srt_table(self, path, role=None):
        self._subtitle_role = role or "original"
        return StudioWindow.load_srt_table(self, path, role)

    def _restore_translation_controls(self):
        config = copy.deepcopy(self.translation_config)
        self.pipe_provider.blockSignals(True)
        selected = self.pipe_provider.findData(config.get("provider", "google"))
        self.pipe_provider.setCurrentIndex(max(0, selected))
        self.pipe_provider.blockSignals(False)
        self.translation_model.blockSignals(True)
        self.translation_model.setCurrentText(str(config.get("model", "")))
        self.translation_model.blockSignals(False)
        self.translation_model.setEnabled(config.get("provider", "google") != "google")
        self.refresh_models_button.setEnabled(config.get("provider", "google") != "google")
        for control,key,default in [(self.batch_request,"batch_size",40),(self.request_workers,"translate_workers",1)]:
            control.blockSignals(True)
            control.setValue(int(config.get(key,default)))
            control.blockSignals(False)
        self.context_frames.blockSignals(True)
        self.context_frames.setChecked(bool(config.get("image_context",False)) and config.get("provider","google") != "google")
        self.context_frames.setEnabled(config.get("provider","google") != "google")
        self.context_frames.blockSignals(False)
        self.context_switch.setChecked(self.context_frames.isChecked())
        self.translation_batch_control.sync_from_source(self.batch_request.value())
        self.translation_workers_control.sync_from_source(self.request_workers.value())

    def _provider_changed(self, *_):
        self._dialog_stores()
        provider = self.pipe_provider.currentData()
        self.translation_config["provider"] = provider
        self.translation_config.pop("model", None)
        self.translation_model.clear()
        self.translation_model.setEnabled(provider != "google")
        self.refresh_models_button.setEnabled(provider != "google")
        self.context_frames.setEnabled(provider != "google")
        if provider == "google":
            self.context_frames.setChecked(False)
        self.prefs.setValue("translation_config", json.dumps(self.translation_config, ensure_ascii=False))

    def _set_translation_option(self,key,value):
        if hasattr(self,"translation_config"):
            self.translation_config[key] = value
            self.prefs.setValue("translation_config",json.dumps(self.translation_config,ensure_ascii=False))

    def _translation_model_changed(self, value):
        if not hasattr(self, "translation_config"):
            return
        if value.strip():
            self.translation_config["model"] = value.strip()
        else:
            self.translation_config.pop("model", None)
        self.prefs.setValue("translation_config", json.dumps(self.translation_config, ensure_ascii=False))

    def fetch_translation_models(self):
        self._dialog_stores()
        provider = self.pipe_provider.currentData()
        if provider == "google":
            return
        self.refresh_models_button.setEnabled(False)

        def done(models):
            self.refresh_models_button.setEnabled(True)
            previous = self.translation_config.get("model", "")
            self.translation_model.blockSignals(True)
            self.translation_model.clear()
            self.translation_model.addItems(models)
            if previous in models:
                self.translation_model.setCurrentText(previous)
            self.translation_model.blockSignals(False)
            self._translation_model_changed(self.translation_model.currentText())
            self.statusBar().showMessage(f"Đã tải {len(models)} mô hình.", 5000)

        # Return errors as a result so the button always becomes available again.
        def request(progress, cancel):
            try:
                return self.provider_store.list_models(provider, cancel)
            except Exception as error:
                return error

        def completed(result):
            if isinstance(result, Exception):
                self.refresh_models_button.setEnabled(True)
                self.info("Không tải được mô hình", str(result))
            else:
                done(result)

        self.background(request, completed)

    def refresh_scenarios(self):
        self._dialog_stores()
        for combo, setting, items in [
            (self.pipe_scenario, "scenario_translate", self.scenario_store.list_templates("translate")),
            (self.pipe_pronunciation, "pronunciation_id", self.scenario_store.list_pronunciation()),
        ]:
            selected = self.prefs.value(setting, "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Không dùng", "")
            for item in items:
                combo.addItem(item["name"], item["id"])
            index = combo.findData(selected)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        self._scenario_changed()
        self._pronunciation_changed()

    def _scenario_changed(self, *_):
        if not hasattr(self, "scenario_store"):
            return
        identity = self.pipe_scenario.currentData()
        self.prefs.setValue("scenario_translate", identity or "")
        if identity:
            template = self.scenario_store.get_template(identity, "translate")
            self.translation_config["scenario_prompt"] = template["prompt"]
        else:
            self.translation_config.pop("scenario_prompt", None)
        self.prefs.setValue("translation_config", json.dumps(self.translation_config, ensure_ascii=False))

    def _pronunciation_changed(self, *_):
        if not hasattr(self, "scenario_store"):
            return
        identity = self.pipe_pronunciation.currentData()
        self.prefs.setValue("pronunciation_id", identity or "")

    def pipeline_config(self):
        config = dict(
            recognize=self.pipe_recognize.isChecked(), translate=self.pipe_translate.isChecked(),
            dub=self.pipe_dub.isChecked(), export=self.pipe_export.isChecked(),
            source_language=self.pipe_source.currentText(), target_language=self.pipe_target.currentText(),
            voice=self.pipe_voice.currentText(), recognition_model=self.pipe_model.currentText(),
            recognition_device=self.recognition_device.currentData(),recognition_batch_size=self.recognition_batch_size.value(),
            recognition_method="speech" if self.pipe_method.currentIndex() == 0 else "ocr",
            ocr_language=self.ocr_language.currentText(), ocr_region=self.ocr_region(),
            ocr_interval=self.ocr_interval.value(), split_voice=self.pipe_separate.isChecked(),
            bilingual=self.pipe_bilingual.isChecked(), settings=self.settings(), reuse_artifacts=True,
            sub_lead=self.sub_lead.value(), voice_speed=self.tts_speed.value(),
            voice_delay=self.tts_delay.value(), voice_fit=self.tts_fit.currentData(), voice_parallel=self.voice_parallel.value(),
            voice_max_speed=self.voice_max_speed.value(),
            separation_mode=self.separation_mode.currentData(),
            tts_device=self.tts_device.currentData(), separation_device=self.tts_device.currentData(),
            join_enabled=False,
        )
        config.update({key: control.value() for key, control in self.worker_controls.items()})
        config.update(copy.deepcopy(getattr(self, "translation_config", {})))
        pronunciation = self.pipe_pronunciation.currentData()
        if pronunciation:
            dictionary = self.scenario_store.get_pronunciation(pronunciation)
            config["pronunciation"] = {rule["source"]: rule["target"] for rule in dictionary["rules"]}
        return config

    @staticmethod
    def _pipeline_uses_network(config, subtitle_paths=()):
        if config.get("translate"):
            return True
        if not config.get("dub"):
            return False
        import local_tts
        if not local_tts.is_local_voice(config.get("voice", "vi-VN-HoaiMyNeural")):
            return True
        rows = list(config.get("subtitle_rows") or [])
        paths = set(subtitle_paths)
        paths.update(str(config.get(key) or "") for key in ("subtitle_path", "original_srt", "translated_srt"))
        import subtitle_tools
        for path in paths - {""}:
            try:
                rows.extend(subtitle_tools.load_subtitles(path))
            except (OSError, ValueError):
                # Keep the existing disclosure if metadata cannot be checked.
                return True
        return any(row.get("voice") and not local_tts.is_local_voice(row["voice"])
                   for row in rows if isinstance(row, dict))

    def run_pipeline(self, stage="all"):
        selected = self.selected_videos()
        if not selected:
            self.info("Chưa chọn video", "Chọn ít nhất một video để bắt đầu.")
            return
        if self.current_media:
            self.video_settings[self.current_media] = copy.deepcopy(self.settings())
        config = self.pipeline_config()
        if stage != "all":
            for name in ("recognize", "translate", "dub", "export"):
                config[name] = name == stage
            config["split_voice"] = self.pipe_separate.isChecked() and stage in ("dub", "export")
            if stage in ("recognize", "dub"):
                config["bilingual"] = False
            # A direct button is an explicit request to run that step again.
            # Keep edited captions/stems, but regenerate the requested result.
            config["force_translate"] = stage == "translate"
            config["force_dub"] = stage == "dub"
        if config.get("translate"):
            try:
                providers.validate_translation_config(config)
            except (ValueError, providers.ProviderError) as error:
                self.info("Chưa thể dịch", str(error))
                return
        if not any(config.get(name) for name in ("recognize", "translate", "dub", "export", "split_voice")):
            self.info("Chọn bước xử lý", "Bật ít nhất một bước trong tab Pipeline.")
            return
        message = "Dịch và lồng tiếng sẽ gửi nội dung phụ đề tới dịch vụ đã chọn."
        if config.get("image_context") and config.get("translate"):
            message += " Dịch sẽ gửi thêm khung hình đầu của từng video."
        subtitle_paths = [str(self.assets.get(media["path"], {}).get(key) or "")
                          for media in selected for key in ("original_subtitle", "translated_subtitle")]
        if self._pipeline_uses_network(config, subtitle_paths) and not self.ai_confirm(message+" Tiếp tục xử lý?"):
            return
        videos = []
        for media in selected:
            asset = self.assets.get(media["path"], {})
            videos.append(dict(path=media["path"], settings=copy.deepcopy(self.video_settings.get(media["path"], self.settings())), original_srt=asset.get("original_subtitle", ""), translated_srt=asset.get("translated_subtitle", ""), voice_path=asset.get("narration", ""), background_path=asset.get("background", "")))
            if stage in ("translate", "dub"):
                # Enable the recognition prerequisite only for a card without
                # usable captions, honoring the user's recognition checkbox.
                supplied = [asset.get("original_subtitle"), asset.get("translated_subtitle"),
                            videos[-1]["settings"].get("subtitle_path"),
                            str(Path(media["path"]).with_suffix(".srt"))]
                if not any(value and Path(value).is_file() for value in supplied):
                    videos[-1]["config"] = {"recognize": self.pipe_recognize.isChecked()}
            clip = next((value for value in self.clips if value["path"] == media["path"]), None)
            if clip:
                videos[-1].update(start=clip["start"], end=clip["end"])
            if config.get("image_context") and config.get("translate"):
                thumbnail = self.thumbnail_path(media["path"])
                if not thumbnail.is_file():
                    self.info("Chưa có khung hình","Chờ ảnh xem trước của video hoàn thành trước khi dịch với ảnh ngữ cảnh.")
                    return
                videos[-1].setdefault("config", {})["context_paths"] = [str(thumbnail)]
        output = self.pipe_folder.text().strip() or self.out_dir
        for card in self.cards:
            card.stop()
        execute = workflow.export_all if config.get("export") else workflow.run_workflow
        self.start_job(f"Pipeline · {len(videos)} video", lambda progress, cancel: execute(videos, config, output, progress, cancel), self.pipeline_done)
        self._running_sources = [media["path"] for media in selected]
        if self.active_job:
            self.active_job.progress.connect(self._update_card_progress)

    def _update_card_progress(self,percent,message):
        for card in self.cards:
            if card.path in self._running_sources:
                card.stage_status.setText("Đang xử lý")
                card.stage_status.setVisible(True)
                card.stage_status.setStyleSheet("font-size:11px;color:#00b4d8;font-weight:600;")
                card.stage_progress.setVisible(True)
                card.stage_progress.setValue(percent)
                card.stage_percent.setText(str(percent)+"%")
                card.stage_percent.show()

    def pipeline_done(self, results):
        manifest = results.get("manifest") if isinstance(results, dict) else None
        if isinstance(results, dict):
            results = results.get("results", [])
        self._batch_errors = {result["source"]:result.get("error","") for result in results if result.get("error")}
        StudioWindow.pipeline_done(self, results)
        for result in results:
            source = result["source"]
            paths = result.get("paths") or {}
            roles = ("vocals","drums","bass","other") if all(paths.get(key) for key in ("vocals","drums","bass","other")) else ("vocals","background")
            stems = {key:paths[key] for key in roles if paths.get(key)}
            if "vocals" in stems:
                self.video_settings.setdefault(source,copy.deepcopy(self.settings()))["source_stem_paths"] = stems
                if self.current_media == source:
                    self.advanced["source_stem_paths"] = stems
        if manifest:
            self.resume_manifest = manifest
        self.batch_progress.setText(self.batch_summary.text())
        self._running_sources = []
        self.navigate(6)

    def resume_pipeline(self):
        path = QFileDialog.getOpenFileName(self, "Tiếp tục Pipeline", self.pipe_folder.text() or self.out_dir, "Manifest (*.json)")[0]
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self.info("Không đọc được manifest", str(error))
            return
        configs = [value.get("config", {}) for value in payload.get("inputs", [])]
        if any(self._pipeline_uses_network(config) for config in configs) and not self.ai_confirm("Tiếp tục các bước dịch / lồng tiếng sẽ gửi nội dung phụ đề tới dịch vụ đã chọn. Tiếp tục?"):
            return
        resume = workflow.resume_export if payload.get("kind") == "oneclick_export" else workflow.resume_workflow
        self.start_job("Tiếp tục Pipeline", lambda progress, cancel: resume(path, progress, cancel), self.pipeline_done)

    def translate(self):
        """The subtitle editor uses the same selected provider as Pipeline."""
        source = self.current_srt()
        if not source or not Path(source).is_file():
            self.info("Cần phụ đề", "Mở hoặc lưu phụ đề trước khi dịch.")
            return
        config = self.pipeline_config()
        config["target_language"] = self.language.currentText()
        if config.get("image_context") and self.current_media:
            config["context_paths"] = [str(self.thumbnail_path(self.current_media))]
        try:
            providers.validate_translation_config(config, require_context=True)
        except (ValueError, providers.ProviderError) as error:
            self.info("Chưa thể dịch", str(error))
            return
        if not self.ai_confirm("Gửi nội dung phụ đề tới công cụ dịch đã chọn trong Pipeline?"):
            return
        output = self.output_path("Lưu phụ đề dịch", ".srt", "ban_dich")
        if not output:
            return
        import subtitle_tools
        def execute(progress, cancel):
            rows = subtitle_tools.load_subtitles(source)
            translated = providers.translate_rows(rows, config, progress, cancel)
            return subtitle_tools.save_subtitles(
                [{**old, **new, "original_text": old["text"]} for old, new in zip(rows, translated)], output)
        self.original_srt = source
        self.start_job("Dịch phụ đề", execute,
                       lambda result: (self.load_srt_table(result, "translated"), self.navigate(3)))

    def dub(self):
        """The subtitle editor honors local voices, GPU and Pipeline timing."""
        source = self.current_srt()
        if not source or not Path(source).is_file():
            self.info("Cần phụ đề", "Mở hoặc lưu phụ đề trước khi lồng tiếng.")
            return
        config = self.pipeline_config()
        config.update(translate=False, dub=True)
        if self._pipeline_uses_network(config, [source]) and not self.ai_confirm("Gửi nội dung phụ đề tới dịch vụ giọng đọc đã chọn?"):
            return
        output = self.output_path("Lưu giọng theo từng câu", ".wav", "long_tieng")
        if not output:
            return
        import local_tts
        import subtitle_tools
        options = {key: config[key] for key in ("voice_speed", "voice_delay", "voice_fit", "voice_parallel", "voice_max_speed", "pronunciation") if key in config}
        def execute(progress, cancel):
            with local_tts.device_scope(config.get("tts_device", "auto")):
                return subtitle_tools.dub_rows(subtitle_tools.load_subtitles(source), output, config["voice"], progress, cancel, **options)
        self.start_job("Lồng tiếng", execute,
                       lambda result: (self.voice_path.setText(result), self.mute.setChecked(True), self.navigate(9)))

    def _refresh_job_status(self):
        if self._close_requested:
            self.run_all_button.setEnabled(False)
            self.stop_batch_button.setEnabled(False)
            self.add_video_button.setEnabled(False)
            self.batch_progress.setText("Đang dừng tác vụ…")
            return
        busy = self.active_job is not None
        self.run_all_button.setEnabled(not busy and bool(self._selected_paths) and bool(self.pipe_folder.text().strip()))
        for name in ("runRecognize", "runTranslate", "runDub"):
            self.findChild(QPushButton, name).setEnabled(not busy and bool(self._selected_paths))
        self.stop_batch_button.setEnabled(busy)
        if busy:
            self.batch_progress.setText(f"{self.progress.value()}%  ·  {self.job_title.text()}")
        elif self.batch_progress.text().startswith(tuple(f"{i}%" for i in range(101))):
            self.batch_progress.setText("Hoàn thành" if self.progress.value() == 100 else "Sẵn sàng")
        if not busy and self.library:
            counts = [sum(bool(value.get(key)) for value in self.assets.values()) for key in ("original_subtitle","translated_subtitle","narration","video")]
            parts = [f"{icon} {count}" for icon, count in zip(("📝", "🌐", "🎤"), counts[:3]) if count]
            parts.append(f"📤 {counts[3]}/{len(self.library)}")
            if self._batch_errors:
                parts.append(f"⚠ {len(self._batch_errors)}")
            self.batch_progress.setText("   ".join(parts))
        config = getattr(self, "translation_config", {})
        provider = config.get("provider", "google")
        self.provider_display.setText({"google": "Google Translate", "gemini": "Gemini", "openai": "OpenAI", "deepseek": "DeepSeek"}.get(provider, provider))
        self.layer_count.setText(f"{len(self.layer_entries())+len(self.advanced.get("audio_layers",[]))+len(self.advanced.get("filter_layers",[]))} thành phần")

    def _choose_batch_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Thư mục lưu kết quả", self.pipe_folder.text() or self.out_dir)
        if path:
            self.pipe_folder.setText(path)
            self.export_folder.setText(path)
            self.out_dir = path
            self.prefs.setValue("output", path)

    def _choose_logo(self):
        path = QFileDialog.getOpenFileName(self, "Ảnh logo", "", "Ảnh (*.png *.jpg *.jpeg *.webp)")[0]
        if path:
            self.logo.setText(path)

    def _choose_music(self):
        path = QFileDialog.getOpenFileName(self, "Nhạc nền", "", "Âm thanh (*.mp3 *.wav *.m4a *.aac *.ogg)")[0]
        if path:
            self.music.setText(path)

    def show_owner(self):
        self.info("Chủ sở hữu", "Video Editor Independent\nQuyền chủ sở hữu trên máy cục bộ.\nDự án, media và thiết lập được lưu trên máy này.")

    def _open_video_search(self):
        value, accepted = QInputDialog.getText(self,"Tìm video","Tên video trong dự án:",text=self.search_box.text())
        if accepted:
            self.search_box.setText(value)

    def set_language(self, language):
        self._language = language
        self.vi_button.setChecked(language == "vi")
        self.en_button.setChecked(language == "en")
        for widget, vietnamese, english in self._translated_widgets:
            widget.setText(vietnamese if language == "vi" else english)
        self.right_tabs.setTabText(1, "Thành phần" if language == "vi" else "Components")
        self.right_tabs.setTabText(2, "Tinh chỉnh video" if language == "vi" else "Video tuning")
        self.search_box.setPlaceholderText("Tìm video…" if language == "vi" else "Search video…")
        self._update_selection_count()
        self.prefs.setValue("ui_language", language)

    set_ui_language = set_language

    def mark_dirty(self, *_):
        if not self.syncing:
            self.dirty = True
            self.setWindowTitle(APP_TITLE + " • Dự án chưa lưu")
            self._sync_current_card()

    def import_files(self):
        paths = QFileDialog.getOpenFileNames(self,"Nhập video","","Video (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.flv *.ts);;Tất cả (*)")[0]
        if paths:
            self.ingest(paths)

    def keyPressEvent(self,event):
        if event.key() == Qt.Key.Key_Space and self.main_views.currentIndex() == 0:
            focused = QApplication.focusWidget()
            if not isinstance(focused,(QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox)):
                card = next((card for card in self.cards if card.path == self.current_media),None)
                if card:
                    card.toggle_play()
                    event.accept()
                    return
        super().keyPressEvent(event)

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            if self._shell_ready:
                self.grid_panel.setStyleSheet("QFrame#gridPanel {border:2px solid #00b4d8;background:#243239;}")

    def dragLeaveEvent(self,event):
        if self._shell_ready:
            self.grid_panel.setStyleSheet("")
        event.accept()

    def dropEvent(self,event):
        if self._shell_ready:
            self.grid_panel.setStyleSheet("")
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in {".mp4",".mkv",".mov",".avi",".webm",".m4v",".flv",".ts"}]
        if paths:
            self.ingest(paths)
            self.navigate(6)
            event.acceptProposedAction()

    def save_project(self):
        if self.current_media:
            self.video_settings[self.current_media] = copy.deepcopy(self.settings())
        result = StudioWindow.save_project(self)
        if result:
            self.setWindowTitle(APP_TITLE + " · " + Path(self.project_path).stem)
        return result

    def open_project(self):
        StudioWindow.open_project(self)
        if self.project_path:
            self.setWindowTitle(APP_TITLE + " · " + Path(self.project_path).stem)
            self.navigate(6)

    def new_project(self):
        StudioWindow.new_project(self)
        if not self.dirty:
            self._selected_paths.clear()
            self.setWindowTitle(APP_TITLE)
            self.render_library()
            self.navigate(6)

    def closeEvent(self, event):
        pending = set(self.workers)
        if self.active_job:
            pending.add(self.active_job)
        pending = [worker for worker in pending if worker.isRunning()]
        self._close_requested = True
        if pending:
            event.ignore()
            for card in self.cards:
                card.stop()
            self.statusBar().showMessage("Đang dừng tác vụ trước khi đóng…")
            for worker in pending:
                if hasattr(worker, "cancel_event"):
                    worker.cancel_event.set()
                if worker not in self._closing_workers:
                    self._closing_workers.add(worker)
                    worker.finished.connect(lambda: QTimer.singleShot(0, self.close))
            return
        self._closing_workers.clear()
        StudioWindow.closeEvent(self, event)
        if event.isAccepted():
            self._job_timer.stop()
            for card in self.cards:
                card.stop()
        else:
            # A user can cancel the unsaved-project prompt and continue editing.
            self._close_requested = False
            self.add_video_button.setEnabled(True)
            if hasattr(self, "thumbnail_pending"):
                self.thumbnail_pending.clear()
            QTimer.singleShot(0, self.render_library)


VideoEditorWindow = IndependentWindow
