"""Studio workspace: batch media, layers, subtitle tools and local AI utilities."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QSize, Signal, QRectF, QUrl
from PySide6.QtGui import QColor, QFont, QPixmap, QIcon, QPainter, QPen, QAction, QTransform
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QSplitter, QTabWidget, QLineEdit,
    QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QScrollArea,
    QInputDialog, QFileDialog, QMessageBox, QColorDialog, QFontComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsItem,
    QGraphicsTextItem, QGraphicsPixmapItem, QAbstractItemView, QTableWidgetItem,
)
from app import Window, time_label
import engine
import ai_tools
import subtitle_tools
import media_ai
import workflow
from render_pipeline import render_project


DEFAULT_ADVANCED = {
    'text_layers': [], 'image_layers': [], 'blur_zones': [], 'audio_layers': [], 'filter_layers': [],
    'background_mode': 'black', 'background_color': '#000000', 'background_blur': 12,
    'source_x': 0., 'source_y': 0., 'source_scale': 1., 'source_opacity': 1.,
    'source_scale_x': 1., 'source_scale_y': 1., 'source_rotation': 0.,
    'source_flip_vertical': False,
    'filter': 'none', 'filter_intensity': .5, 'zoom_enabled': False,
    'zoom_amount': 1.08, 'zoom_period': 8., 'zoom_hold': 0., 'frame_border': 0,
    'frame_border_color': '#ffffff', 'fade_in': 0., 'fade_out': 0.,
    'subtitle_style': {'font':'Arial','size':24,'color':'#ffffff','outline':2,
                       'outline_color':'#000000','bg_color':'#000000','bg_opacity':0,
                       'alignment':2,'bold':False,'uppercase':False},
    'encoder': {'format':'mp4','codec':'h264','device':'auto','quality':20,
                'preset':'veryfast','bitrate_mode':'crf','bitrate':'5M','maxrate':'8M','bframes':True},
    'audio_effect': 'none', 'voice_path':'', 'voice_volume':1., 'voice_delay':0.,
    'background_path':'', 'original_volume':1.,
}


class PositionedLayer(QGraphicsRectItem):
    def __init__(self, kind, index, data, changed, selected):
        w = data.get('width', 260 if kind=='text_layers' else 160)
        h = data.get('height', data.get('font_size',36)*1.5 if kind=='text_layers' else 90)
        super().__init__(0,0,w,h)
        self.kind, self.index, self.data = kind,index,data
        self.changed, self.selected_callback = changed,selected
        self.resizing=False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(data.get('x',0), data.get('y',0))
        self.child=None
        if kind=='text_layers':
            self.child=QGraphicsTextItem(data.get('text','Văn bản'),self)
            self.child.setDefaultTextColor(QColor(data.get('color','#ffffff')))
            self.child.setOpacity(data.get('opacity',1))
            font=QFont(data.get('font','Arial')); font.setPixelSize(int(data.get('font_size',36))); self.child.setFont(font)
            self.child.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        elif kind=='image_layers' and Path(data.get('path','')).is_file():
            self.original=QPixmap(data['path']); self.child=QGraphicsPixmapItem(self.original.scaled(int(w),int(h),Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation),self)
            self.child.setOpacity(data.get('opacity',1)); self.child.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setBrush(QColor(70,140,255,70) if kind=='blur_zones' else QColor(0,0,0,20))
        self.setPen(QPen(QColor('#a88bff'),2,Qt.PenStyle.DashLine))
    def paint(self,painter,option,widget=None):
        super().paint(painter,option,widget)
        if self.isSelected():
            painter.fillRect(QRectF(self.rect().right()-12,self.rect().bottom()-12,12,12),QColor('#bb9fff'))
    def itemChange(self,change,value):
        if change==QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and hasattr(self,'changed'):
            self.data['x']=round(value.x()); self.data['y']=round(value.y()); self.changed(self.kind,self.index,self.data)
        if change==QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and value and hasattr(self,'selected_callback'):
            self.selected_callback(self.kind,self.index)
        return super().itemChange(change,value)
    def mousePressEvent(self,event):
        self.resizing=self.isSelected() and event.pos().x()>self.rect().right()-16 and event.pos().y()>self.rect().bottom()-16
        if self.resizing: event.accept()
        else: super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if not self.resizing: return super().mouseMoveEvent(event)
        w,h=max(20,event.pos().x()),max(20,event.pos().y())
        old_h=max(1,self.rect().height()); self.setRect(0,0,w,h)
        self.data['width']=round(w); self.data['height']=round(h)
        if self.kind=='text_layers':
            self.data['font_size']=max(8,round(self.data.get('font_size',36)*h/old_h))
            f=self.child.font(); f.setPixelSize(self.data['font_size']); self.child.setFont(f)
        elif self.kind=='image_layers' and self.child:
            self.child.setPixmap(self.original.scaled(int(w),int(h),Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation))
        self.changed(self.kind,self.index,self.data); event.accept()
    def mouseReleaseEvent(self,event): self.resizing=False; super().mouseReleaseEvent(event)


class LayerCanvas(QGraphicsView):
    changed=Signal(str,int,dict)
    chosen=Signal(str,int)
    def __init__(self):
        super().__init__(); self.setScene(QGraphicsScene(self)); self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.width_px,self.height_px=1280,720; self.back=None; self.setMinimumHeight(310)
        self.setStyleSheet('QGraphicsView {background:#080b12;border:1px solid #343b55;border-radius:8px;}')
    def rebuild(self,state,size=(1280,720),background=None,fit='contain'):
        self.scene().clear(); self.width_px,self.height_px=size
        self.setSceneRect(0,0,*size)
        if background is not None: self.back=background
        if self.back and not self.back.isNull():
            frame=QPixmap(*size); frame.fill(QColor('#080b12')); painter=QPainter(frame)
            mode=Qt.AspectRatioMode.KeepAspectRatioByExpanding if fit=='cover' else Qt.AspectRatioMode.KeepAspectRatio
            scaled=self.back.scaled(*size,mode,Qt.TransformationMode.SmoothTransformation); painter.drawPixmap((size[0]-scaled.width())//2,(size[1]-scaled.height())//2,scaled); painter.end()
            pix=self.scene().addPixmap(frame); pix.setZValue(-10)
        else: self.scene().addRect(QRectF(0,0,*size),QPen(Qt.PenStyle.NoPen),QColor('#151b2a')).setZValue(-10)
        for kind in ['text_layers','image_layers','blur_zones']:
            for index,row in enumerate(state.get(kind,[])):
                if row.get('enabled',True): self.scene().addItem(PositionedLayer(kind,index,copy.deepcopy(row),self.changed.emit,self.chosen.emit))
        self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
    def resizeEvent(self,event): super().resizeEvent(event); self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)


class StudioWindow(Window):
    def __init__(self,testing=False):
        self.advanced=copy.deepcopy(DEFAULT_ADVANCED)
        self.video_settings={}; self.assets={}; self.current_media=None
        self.layer_loading=False; self.layer_selection=None; self.undo_subtitles=[]
        self.original_srt=''; self.translated_srt=''; self.resume_manifest=None
        self.thumb_cache=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'VideoEditorIndependent' / 'thumbnails'
        super().__init__(testing)
        self.default_settings=copy.deepcopy(self.settings())
        self.resize(1600,940); self.setWindowTitle('VideoEditorIndependent Studio 2.0')

    def build(self):
        super().build()
        self.build_studio(); self.build_layers(); self.build_utilities(); self.build_export_options()
        sidebar=self.centralWidget().findChild(QWidget,'sidebar'); nav=sidebar.layout()
        while nav.count():
            item=nav.takeAt(0)
            if item.widget(): item.widget().hide(); item.widget().deleteLater()
        brand=QLabel('VideoEditorIndependent'); brand.setObjectName('brand'); nav.addWidget(brand)
        tag=QLabel('STUDIO  /  2.0'); tag.setObjectName('muted'); nav.addWidget(tag); nav.addSpacing(22)
        self.nav_buttons=[]; self.nav_targets=[]
        names=[(6,'Studio / Pipeline'),(2,'Cắt ghép'),(7,'Thành phần & hiệu ứng'),(3,'Phụ đề & lồng tiếng'),(8,'OCR & tách âm thanh'),(9,'Thiết lập xuất'),(0,'Kho video'),(1,'Tải xuống'),(4,'Tác vụ'),(5,'Hướng dẫn')]
        for position,(target,title) in enumerate(names):
            b=self.button(title.replace('&','&&'),lambda _=False,ix=target:self.navigate(ix)); b.setObjectName('nav'); b.setCheckable(True)
            self.nav_buttons.append(b); self.nav_targets.append(target); nav.addWidget(b)
        nav.addStretch(); footer=QLabel('Một kho media\nMột quy trình làm việc'); footer.setObjectName('muted'); nav.addWidget(footer)
        self.navigate(6)
        bar=self.addToolBar('Công cụ'); bar.setMovable(False)
        for title,func in [('+ Thêm video',self.import_files),('Pipeline',lambda:self.navigate(6)),('Thành phần',lambda:self.navigate(7)),('Cắt ghép',lambda:self.navigate(2)),('Tải xuống',lambda:self.navigate(1)),('Thiết lập',lambda:self.navigate(9)),('Lưu dự án',self.save_project)]:
            action=QAction(title,self); action.triggered.connect(func); bar.addAction(action)

    def navigate(self,index):
        self.pages.setCurrentIndex(index)
        for n,b in enumerate(self.nav_buttons): b.setChecked((self.nav_targets[n] if hasattr(self,'nav_targets') else n)==index)
        if index==7 and hasattr(self,'canvas'): self.refresh_canvas()

    def build_studio(self):
        lay=self.page('VideoEditorIndependent Studio', 'Chọn nhiều video, thiết lập các bước và xử lý trong cùng một Pipeline.')
        top=QHBoxLayout(); top.addWidget(self.button('+ Thêm video',self.import_files,True)); top.addWidget(self.button('Chọn tất cả',lambda:self.media_grid.selectAll())); top.addWidget(self.button('Bỏ chọn',lambda:self.media_grid.clearSelection()))
        top.addWidget(self.button('Áp dụng cài đặt cho tất cả',self.apply_all)); top.addStretch(); lay.addLayout(top)
        splitter=QSplitter(); self.media_grid=QListWidget(); self.media_grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.media_grid.setResizeMode(QListWidget.ResizeMode.Adjust); self.media_grid.setMovement(QListWidget.Movement.Static)
        self.media_grid.setIconSize(QSize(246,138)); self.media_grid.setGridSize(QSize(270,218)); self.media_grid.setWordWrap(True)
        self.media_grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.media_grid.currentItemChanged.connect(self.grid_current_changed); self.media_grid.itemDoubleClicked.connect(self.grid_edit)
        splitter.addWidget(self.media_grid)
        panel=QTabWidget(); panel.setMinimumWidth(310); panel.setMaximumWidth(390)
        form=self.option_form(panel,'Pipeline')
        self.pipe_recognize=QCheckBox('Trích phụ đề'); self.pipe_recognize.setChecked(True); form.addRow(self.pipe_recognize)
        self.pipe_method=self.combo(['Nghe giọng nói (Whisper)','Đọc chữ trên hình (OCR)']); form.addRow('Phương pháp',self.pipe_method)
        self.pipe_model=self.combo(['base','tiny','small','medium','large-v3','turbo']); form.addRow('Mô hình Whisper',self.pipe_model)
        self.pipe_source=self.combo(['auto','zh','vi','en','ja','ko','fr','th']); form.addRow('Tiếng nguồn',self.pipe_source)
        self.pipe_translate=QCheckBox('Dịch phụ đề'); self.pipe_translate.setChecked(True); form.addRow(self.pipe_translate)
        self.pipe_target=self.combo(['vi','en','zh-CN','ja','ko','fr','th']); form.addRow('Dịch sang',self.pipe_target)
        self.pipe_dub=QCheckBox('Lồng tiếng'); self.pipe_dub.setChecked(True); form.addRow(self.pipe_dub)
        self.pipe_voice=self.combo(['vi-VN-HoaiMyNeural','vi-VN-NamMinhNeural','en-US-JennyNeural']); form.addRow('Giọng mặc định',self.pipe_voice)
        self.pipe_separate=QCheckBox('Tách giọng gốc, giữ nhạc nền'); form.addRow(self.pipe_separate)
        self.pipe_bilingual=QCheckBox('Phụ đề song ngữ'); form.addRow(self.pipe_bilingual)
        self.pipe_export=QCheckBox('Xuất video'); self.pipe_export.setChecked(True); form.addRow(self.pipe_export)
        self.pipe_folder=QLineEdit(self.out_dir); form.addRow('Thư mục lưu',self.file_row(self.pipe_folder,folder=True))
        form.addRow(self.button('Tiếp tục Pipeline đã lưu…',self.resume_pipeline))
        form=self.option_form(panel,'Thành phần')
        for label,index in [('Văn bản / chữ chạy / logo',7),('Phụ đề gốc và phụ đề dịch',3),('Vùng mờ / bộ lọc / zoom',7),('Âm thanh / tách giọng',8),('Codec và chất lượng xuất',9)]: form.addRow(self.button(label,lambda _=False,ix=index:self.navigate(ix)))
        form.addRow(QLabel('Nhấp đúp video để mở cắt ghép.'))
        form=self.option_form(panel,'Tinh chỉnh')
        form.addRow(self.button('Khung hình / lật / tốc độ',lambda:self.navigate(2)))
        form.addRow(self.button('Bộ lọc màu và âm thanh',lambda:self.navigate(7)))
        form.addRow(self.button('Xem thử kết quả 5 giây',self.preview_render))
        splitter.addWidget(panel); splitter.setStretchFactor(0,4); splitter.setStretchFactor(1,1); lay.addWidget(splitter,1)
        bottom=QHBoxLayout()
        for text,stage in [('Trích phụ đề','recognize'),('Dịch','translate'),('Lồng tiếng','dub'),('Xuất đã chọn','export'),('Chạy toàn bộ','all')]: bottom.addWidget(self.button(text,lambda _=False,s=stage:self.run_pipeline(s),stage=='all'))
        bottom.addWidget(self.button('Dừng',self.cancel_job)); lay.addLayout(bottom)
        self.batch_summary=QLabel('Chưa có video · Kéo thả tệp hoặc nhấn Thêm video'); self.batch_summary.setObjectName('muted'); lay.addWidget(self.batch_summary)
        self.setAcceptDrops(True)

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self,event):
        paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths: self.ingest(paths); self.navigate(6); event.acceptProposedAction()

    def render_library(self):
        super().render_library()
        if not hasattr(self,'media_grid'): return
        previous=self.current_media; self.media_grid.blockSignals(True); self.media_grid.clear()
        for media in self.library:
            asset=self.assets.get(media['path'],{}); badge=[]
            for key,name in [('original_subtitle','Gốc'),('translated_subtitle','Dịch'),('narration','Giọng'),('video','Đã xuất')]:
                if asset.get(key): badge.append(name)
            item=QListWidgetItem(f"{media['name']}\n{time_label(media['duration'])} · {media['width']}×{media['height']}\n{' / '.join(badge) or 'Sẵn sàng'}")
            item.setData(Qt.ItemDataRole.UserRole,media); item.setToolTip(media['path']); self.media_grid.addItem(item)
            thumb=self.thumbnail_path(media['path'])
            if thumb.is_file(): item.setIcon(QIcon(str(thumb)))
            else:
                pix=QPixmap(246,138); pix.fill(QColor('#242a40')); item.setIcon(QIcon(pix)); self.make_thumbnail(media['path'])
            if media['path']==previous: self.media_grid.setCurrentItem(item)
        self.media_grid.blockSignals(False); self.batch_summary.setText(f'{len(self.library)} video · Ctrl hoặc Shift để chọn nhiều video')

    def thumbnail_path(self,path):
        try: identity=path+str(Path(path).stat().st_mtime_ns)
        except OSError: identity=path
        return self.thumb_cache/(hashlib.sha256(identity.encode()).hexdigest()+'.png')

    def make_thumbnail(self,path):
        target=self.thumbnail_path(path); target.parent.mkdir(parents=True,exist_ok=True)
        if getattr(self,'thumbnail_pending',None) is None: self.thumbnail_pending=set()
        if path in self.thumbnail_pending: return
        self.thumbnail_pending.add(path)
        def generate(p,c):
            command=[engine.tool_path('ffmpeg'),'-v','error','-y','-ss','0','-i',path,'-frames:v','1','-vf','scale=720:720:force_original_aspect_ratio=decrease','-threads','1',str(target)]
            run=subprocess.run(command,capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return str(target) if run.returncode==0 else ''
        def done(result):
            self.thumbnail_pending.discard(path)
            if result:
                for ix in range(self.media_grid.count()):
                    item=self.media_grid.item(ix)
                    if item.data(Qt.ItemDataRole.UserRole)['path']==path: item.setIcon(QIcon(result))
        self.background(generate,done)

    def grid_current_changed(self,item,previous):
        if previous:
            old=previous.data(Qt.ItemDataRole.UserRole)['path']; self.video_settings[old]=copy.deepcopy(self.settings())
        if item:
            media=item.data(Qt.ItemDataRole.UserRole); self.current_media=media['path']
            self.apply_all_settings(self.video_settings.get(self.current_media,getattr(self,'default_settings',self.settings())))
            assets=self.assets.get(self.current_media,{})
            self.original_srt=assets.get('original_subtitle',''); self.translated_srt=assets.get('translated_subtitle','')
            if hasattr(self,'canvas'): self.refresh_canvas()

    def grid_edit(self,item):
        media=item.data(Qt.ItemDataRole.UserRole)
        self.ingest([media['path']],True)

    def selected_videos(self):
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.media_grid.selectedItems()]

    def apply_all(self):
        settings=self.settings()
        for media in self.library:
            previous=self.video_settings.get(media['path'],{})
            value=copy.deepcopy(settings)
            for key in ['subtitle_path','voice_path','background_path']: value[key]=previous.get(key,'')
            self.video_settings[media['path']]=value
        self.mark_dirty(); self.statusBar().showMessage(f'Đã áp dụng cài đặt cho {len(self.library)} video. Phụ đề và giọng riêng được giữ theo từng tệp.',8000)

    def run_pipeline(self,stage='all'):
        selected=self.selected_videos()
        if not selected: self.info('Chưa chọn video','Chọn ít nhất một video trong Studio.'); return
        if self.current_media: self.video_settings[self.current_media]=copy.deepcopy(self.settings())
        config=dict(recognize=self.pipe_recognize.isChecked(),translate=self.pipe_translate.isChecked(),dub=self.pipe_dub.isChecked(),export=self.pipe_export.isChecked(),
                    source_language=self.pipe_source.currentText(),target_language=self.pipe_target.currentText(),voice=self.pipe_voice.currentText(),model=self.pipe_model.currentText(),
                    recognition_method='speech' if self.pipe_method.currentIndex()==0 else 'ocr',ocr_language=self.ocr_language.currentText(),ocr_region=self.ocr_region(),ocr_interval=self.ocr_interval.value(),
                    split_voice=self.pipe_separate.isChecked(),bilingual=self.pipe_bilingual.isChecked(),settings=self.settings())
        if stage!='all':
            for name in ['recognize','translate','dub','export']: config[name]=name==stage
            config['split_voice']=self.pipe_separate.isChecked() and stage=='dub'
            if stage in ['recognize','dub']: config['bilingual']=False
        if (config['translate'] or config['dub']) and not self.ai_confirm('Pipeline sẽ gửi nội dung phụ đề tới Google để dịch và Microsoft Edge để tạo giọng. Video không được gửi. Chạy các bước đã chọn?'): return
        videos=[]
        for media in selected:
            asset=self.assets.get(media['path'],{})
            videos.append(dict(path=media['path'],settings=copy.deepcopy(self.video_settings.get(media['path'],self.settings())),original_srt=asset.get('original_subtitle',''),translated_srt=asset.get('translated_subtitle',''),voice_path=asset.get('narration',''),background_path=asset.get('background','')))
        folder=self.pipe_folder.text().strip() or self.out_dir
        self.start_job('Pipeline · '+str(len(videos))+' video',lambda p,c:workflow.run_workflow(videos,config,folder,p,c),self.pipeline_done)

    def pipeline_done(self,results):
        outputs=[]; ok=0; failures=[]
        for result in results:
            source=result['source']; paths=result.get('paths',{}); self.assets.setdefault(source,{}).update(paths)
            self.resume_manifest=result.get('manifest',self.resume_manifest)
            if result.get('status')=='done': ok+=1
            elif result.get('error'): failures.append(Path(source).name+': '+result['error'])
            if paths.get('video'): outputs.append(paths['video'])
        self.render_library()
        if outputs: self.ingest(outputs)
        self.batch_summary.setText(f'Hoàn thành {ok}/{len(results)} video'+(' · Có lỗi, xem Tác vụ' if failures else ''))
        self.mark_dirty(); self.navigate(6)
        if failures: self.info('Kết quả Pipeline','\n'.join(failures[:5]))

    def resume_pipeline(self):
        path=QFileDialog.getOpenFileName(self,'Mở manifest Pipeline',self.out_dir,'Manifest (*.json)')[0]
        if not path: return
        try: data=json.loads(Path(path).read_text(encoding='utf-8')); configs=[i['config'] for i in data['inputs']]
        except Exception as exc: self.info('Không đọc được Pipeline',str(exc)); return
        if any(c.get('translate') or c.get('dub') for c in configs) and not self.ai_confirm('Tiếp tục các bước dịch/giọng đọc có thể gửi nội dung phụ đề tới dịch vụ trực tuyến. Tiếp tục?'): return
        self.start_job('Tiếp tục Pipeline',lambda p,c:workflow.resume_workflow(path,p,c),self.pipeline_done)

    def build_layers(self):
        lay=self.page('Thành phần & hiệu ứng','Thêm lớp, kéo vị trí và kéo góc dưới phải để đổi kích thước. Tọa độ dùng pixel của video đầu ra.')
        top=QHBoxLayout()
        for title,func in [('+ Văn bản',self.add_text_layer),('+ Ảnh / GIF',self.add_image_layer),('+ Vùng mờ',self.add_blur_layer),('+ Âm thanh',self.add_audio_layer),('+ Bộ lọc',self.add_filter_layer),('Xóa lớp',self.delete_layer),('Xem thử 5 giây',self.preview_render)]: top.addWidget(self.button(title,func))
        top.addStretch(); lay.addLayout(top)
        split=QSplitter(); left=QWidget(); l=QVBoxLayout(left)
        self.canvas=LayerCanvas(); self.canvas.changed.connect(self.canvas_changed); self.canvas.chosen.connect(self.choose_layer); l.addWidget(self.canvas,3)
        self.layer_list=QListWidget(); self.layer_list.setMaximumHeight(170); self.layer_list.currentRowChanged.connect(self.layer_selected); l.addWidget(self.layer_list,1); split.addWidget(left)
        tabs=QTabWidget(); tabs.setMinimumWidth(335)
        self.layer_editor_tabs=tabs
        self.layer_form=self.option_form(tabs,'Lớp')
        self.layer_fields={}
        for key,label,typ,low,high,value in [('text','Nội dung','text',0,0,''),('x','Ngang (pixel)','int',-8000,8000,0),('y','Dọc (pixel)','int',-8000,8000,0),('width','Rộng','int',8,8000,260),('height','Cao','int',8,8000,90),('font_size','Cỡ chữ','int',8,200,36),('opacity','Độ rõ','float',0,1,1),('start','Từ giây','float',0,864000,0),('end','Đến giây (0: hết)','float',0,864000,0),('strength','Độ mờ','float',1,100,12),('feather','Mềm viền','int',0,200,0),('corner','Bo góc vùng mờ','float',0,200,0),('speed','Tốc độ chữ chạy','float',1,500,80),('rotation','Xoay lớp (độ)','float',-180,180,0),('outline','Độ dày viền chữ','int',0,32,2),('bg_opacity','Độ rõ nền chữ','float',0,1,0),('bg_radius','Bo góc nền chữ','int',0,200,0),('color','Màu chữ (HEX)','text',0,0,'#ffffff'),('outline_color','Màu viền (HEX)','text',0,0,'#000000'),('bg_color','Màu nền (HEX)','text',0,0,'#000000')]:
            control=QLineEdit(value) if typ=='text' else self.number(value,low,high) if typ=='float' else QSpinBox()
            if typ=='int': control.setRange(low,high); control.setValue(value)
            self.layer_fields[key]=control; self.layer_form.addRow(label,control)
            (control.textChanged if typ=='text' else control.valueChanged).connect(self.layer_property_changed)
        self.layer_font=QFontComboBox(); self.layer_form.addRow('Font',self.layer_font); self.layer_font.currentFontChanged.connect(self.layer_property_changed)
        self.layer_fields['path']=QLineEdit(); self.layer_form.addRow('Tệp ảnh / GIF',self.layer_fields['path']); self.layer_fields['path'].textChanged.connect(self.layer_property_changed)
        self.layer_marquee=QComboBox()
        for label,value in [('Không','none'),('Chạy quanh','around'),('Trong khung','inside'),('Chạy chéo','diagonal'),('Tự động','auto'),('Ngang (cũ)','horizontal'),('Sang trái','left'),('Sang phải','right'),('Lên trên','up'),('Xuống dưới','down')]: self.layer_marquee.addItem(label,value)
        self.layer_form.addRow('Chạy chữ',self.layer_marquee); self.layer_marquee.currentIndexChanged.connect(self.layer_property_changed)
        self.layer_text_effect=QComboBox()
        for label,value in [('Viền chữ','outline'),('Viền đậm','outline_thick'),('Đổ bóng','shadow'),('Phát sáng','glow'),('Chữ rỗng','hollow'),('Bóng mềm','soft_shadow'),('Lệch retro','retro_offset'),('Viền kép','double_border'),('Bóng dài','long_shadow'),('Không','none')]: self.layer_text_effect.addItem(label,value)
        self.layer_form.addRow('Hiệu ứng chữ',self.layer_text_effect); self.layer_text_effect.currentIndexChanged.connect(self.layer_property_changed)
        self.layer_bg_fit=QComboBox()
        for label,value in [('Ôm chữ','hug'),('Theo khung','box')]: self.layer_bg_fit.addItem(label,value)
        self.layer_form.addRow('Nền chữ',self.layer_bg_fit); self.layer_bg_fit.currentIndexChanged.connect(self.layer_property_changed)
        self.layer_shape=self.combo(['rect','ellipse']); self.layer_form.addRow('Hình vùng mờ',self.layer_shape); self.layer_shape.currentIndexChanged.connect(self.layer_property_changed)
        self.layer_loop=QCheckBox('Lặp ảnh GIF'); self.layer_form.addRow(self.layer_loop); self.layer_loop.toggled.connect(self.layer_property_changed)
        self.layer_blend=QComboBox()
        for label,value in [('Bình thường','normal'),('Làm sáng','lighten'),('Screen','screen'),('Làm tối','darken'),('Overlay','overlay'),('Ánh sáng mạnh','hardlight'),('Ánh sáng mềm','softlight'),('Nhân màu','multiply')]: self.layer_blend.addItem(label,value)
        self.layer_form.addRow('Hòa trộn',self.layer_blend); self.layer_blend.currentIndexChanged.connect(self.layer_property_changed)
        self.layer_enabled=QCheckBox('Hiện thành phần'); self.layer_enabled.setChecked(True); self.layer_form.addRow(self.layer_enabled); self.layer_enabled.toggled.connect(self.layer_property_changed)
        self.layer_bold=QCheckBox('In đậm'); self.layer_upper=QCheckBox('Viết hoa'); self.layer_flip_horizontal=QCheckBox('Lật ngang thành phần'); self.layer_flip_vertical=QCheckBox('Lật dọc thành phần')
        for control in [self.layer_bold,self.layer_upper,self.layer_flip_horizontal,self.layer_flip_vertical]:
            self.layer_form.addRow(control); control.toggled.connect(self.layer_property_changed)
        self.layer_anchor=self.combo(['top-left','top','top-right','left','center','right','bottom-left','bottom','bottom-right']); self.layer_form.addRow('Điểm bám ảnh',self.layer_anchor); self.layer_anchor.currentIndexChanged.connect(self.layer_property_changed)
        self.layer_form.addRow(self.button('Nhân đôi thành phần',self.duplicate_layer))
        self.layer_form.addRow(self.button('Đổi ảnh / GIF…',self.choose_image_layer_source))
        self.layer_color=self.button('Màu chữ…',lambda:self.pick_layer_color('color')); self.layer_form.addRow(self.layer_color)
        self.layer_bg=self.button('Màu nền chữ…',lambda:self.pick_layer_color('bg_color')); self.layer_form.addRow(self.layer_bg)
        self.fx_form=self.option_form(tabs,'Bộ lọc')
        self.filter_pick=self.combo(['none','vignette','vintage','grain','glow','crt','stripe','sharpen','white_balance','grayscale','saturation','bright_contrast','lift_gamma_gain','lightleak'])
        for index in range(self.filter_pick.count()): self.filter_pick.setItemData(index,self.filter_pick.itemText(index))
        self.fx_form.addRow('Kiểu',self.filter_pick)
        self.fx_fields={}
        for key,label,default,low,high in [('filter_intensity','Cường độ',.5,0,1),('zoom_amount','Mức zoom',1.08,1,2),('zoom_period','Chu kỳ zoom (s)',8,.5,120),('zoom_hold','Giữ zoom (s)',0,0,3),('frame_border','Khung viền (px)',0,0,300),('fade_in','Fade vào (s)',0,0,30),('fade_out','Fade ra (s)',0,0,30)]:
            n=self.number(default,low,high); self.fx_fields[key]=n; self.fx_form.addRow(label,n); n.valueChanged.connect(self.advanced_changed)
        self.zoom_on=QCheckBox('Phóng động'); self.fx_form.addRow(self.zoom_on); self.zoom_on.toggled.connect(self.advanced_changed); self.filter_pick.currentIndexChanged.connect(self.advanced_changed)
        self.fx_fields['zoom_hold'].valueChanged.connect(self.ensure_zoom_cycle)
        self.fx_fields['zoom_period'].valueChanged.connect(self.ensure_zoom_cycle)
        self.fx_fields['zoom_hold'].setToolTip('Giữ ở mức phóng tối đa. Thời gian giữ phải ngắn hơn toàn chu kỳ.')
        self.fx_form.addRow(self.button('Màu khung viền…',self.pick_border_color))
        form=self.option_form(tabs,'Phụ đề')
        self.style_font=QFontComboBox(); form.addRow('Font',self.style_font)
        self.style_text_effect=QComboBox(); self.style_bg_fit=QComboBox()
        for source,target in [(self.layer_text_effect,self.style_text_effect),(self.layer_bg_fit,self.style_bg_fit)]:
            for index in range(source.count()): target.addItem(source.itemText(index),source.itemData(index))
        form.addRow('Hiệu ứng chữ',self.style_text_effect); form.addRow('Nền chữ',self.style_bg_fit)
        self.style_size=QSpinBox(); self.style_size.setRange(8,140); self.style_size.setValue(24); form.addRow('Cỡ chữ',self.style_size)
        self.style_outline=self.number(2,0,12); form.addRow('Viền',self.style_outline)
        self.style_bg=self.number(0,0,1); form.addRow('Độ đậm nền',self.style_bg)
        self.style_align=self.combo(['Dưới trái','Dưới giữa','Dưới phải','Giữa trái','Chính giữa','Giữa phải','Trên trái','Trên giữa','Trên phải']); self.style_align.setCurrentIndex(1); form.addRow('Vị trí',self.style_align)
        self.style_bold=QCheckBox('In đậm'); self.style_upper=QCheckBox('Viết hoa'); form.addRow(self.style_bold); form.addRow(self.style_upper)
        for label,key in [('Màu chữ','color'),('Màu viền','outline_color'),('Màu nền','bg_color')]: form.addRow(self.button(label+'…',lambda _=False,k=key:self.pick_subtitle_color(k)))
        self.style_template=self.combo(['Tùy chỉnh','Cơ bản','Vàng nổi bật','Đậm nền tối','Trắng bóng','Tiêu đề lớn']); form.addRow('Bộ style',self.style_template); self.style_template.currentIndexChanged.connect(self.apply_style_template)
        for c in [self.style_font,self.style_text_effect,self.style_bg_fit,self.style_size,self.style_outline,self.style_bg,self.style_align,self.style_bold,self.style_upper]:
            for sig in ['currentFontChanged','valueChanged','currentIndexChanged','toggled']:
                if hasattr(c,sig): getattr(c,sig).connect(self.advanced_changed); break
        form=self.option_form(tabs,'Video gốc')
        self.source_fields={}
        for key,label,default,low,high in [('source_x','Ngang (-1…1)',0,-1,1),('source_y','Dọc (-1…1)',0,-1,1),('source_scale','Tỉ lệ (×)',1,.05,5),('source_scale_x','Tỉ lệ ngang (×)',1,.05,5),('source_scale_y','Tỉ lệ dọc (×)',1,.05,5),('source_rotation','Xoay (độ)',0,-180,180),('source_opacity','Độ rõ',1,0,1),('background_blur','Độ mờ nền',12,1,30)]:
            control=self.number(default,low,high); control.setSingleStep(.01 if key!='background_blur' else 1)
            self.source_fields[key]=control; form.addRow(label,control); control.valueChanged.connect(self.source_property_changed)
        self.source_flip_vertical=QCheckBox('Lật dọc video gốc'); form.addRow(self.source_flip_vertical); self.source_flip_vertical.toggled.connect(self.source_property_changed)
        self.source_background_mode=QComboBox()
        for label,value in [('Nền đen','black'),('Màu nền','color'),('Nền mờ','blur')]: self.source_background_mode.addItem(label,value)
        form.addRow('Nền khung hình',self.source_background_mode); self.source_background_mode.currentIndexChanged.connect(self.source_property_changed)
        self.source_background_color=QLineEdit('#000000'); form.addRow('Màu nền (HEX)',self.source_background_color); self.source_background_color.textChanged.connect(self.source_property_changed)
        form.addRow(self.button('Chọn màu nền…',self.pick_source_background_color))
        form=self.option_form(tabs,'Lớp âm thanh')
        self.audio_layer_tab_index=tabs.count()-1
        self.audio_layer_fields={}
        self.audio_layer_fields['path']=QLineEdit(); self.audio_layer_fields['path'].setReadOnly(True); form.addRow('Tệp âm thanh',self.audio_layer_fields['path'])
        form.addRow(self.button('Đổi tệp âm thanh…',self.choose_audio_layer_source))
        for key,label,default,low,high in [('volume','Âm lượng (×)',1,0,2),('start','Bắt đầu (giây)',0,0,864000),('end','Kết thúc (0: hết video)',0,0,864000),('fade_in','Fade vào (giây)',0,0,5),('fade_out','Fade ra (giây)',0,0,5)]:
            control=self.number(default,low,high); control.setSingleStep(.05 if key=='volume' else .1)
            if key=='end': control.setSpecialValueText('Hết video')
            self.audio_layer_fields[key]=control; form.addRow(label,control); control.valueChanged.connect(self.audio_layer_property_changed)
        self.audio_layer_loop=QCheckBox('Lặp đến cuối đoạn'); self.audio_layer_loop.setChecked(True)
        self.audio_layer_enabled=QCheckBox('Bật lớp âm thanh'); self.audio_layer_enabled.setChecked(True)
        for control in [self.audio_layer_loop,self.audio_layer_enabled]: form.addRow(control); control.toggled.connect(self.audio_layer_property_changed)
        form.addRow(self.button('Xem thử bản phối 5 giây',self.preview_render))
        form=self.option_form(tabs,'Lớp bộ lọc')
        self.filter_layer_tab_index=tabs.count()-1
        self.filter_layer_kind=QComboBox()
        filter_names={'none':'Không','vignette':'Tối viền','vintage':'Hoài cổ','grain':'Hạt phim','glow':'Ánh sáng mềm','crt':'Màn hình CRT','stripe':'Sọc ngang','sharpen':'Làm nét','white_balance':'Cân bằng trắng','grayscale':'Đen trắng','saturation':'Tăng bão hòa','bright_contrast':'Độ sáng / Tương phản','lift_gamma_gain':'Lift / Gamma / Gain','lightleak':'Rò sáng'}
        for index in range(self.filter_pick.count()):
            value=self.filter_pick.itemData(index); self.filter_layer_kind.addItem(filter_names.get(value,value),value)
        form.addRow('Bộ lọc',self.filter_layer_kind); self.filter_layer_kind.currentIndexChanged.connect(self.filter_layer_property_changed)
        self.filter_layer_fields={}
        for key,label,default,low,high in [('intensity','Cường độ',.5,0,1),('start','Bắt đầu (giây)',0,0,864000),('end','Kết thúc (0: hết video)',0,0,864000)]:
            control=self.number(default,low,high); control.setSingleStep(.01 if key=='intensity' else .1)
            self.filter_layer_fields[key]=control; form.addRow(label,control); control.valueChanged.connect(self.filter_layer_property_changed)
        self.filter_layer_enabled=QCheckBox('Bật lớp bộ lọc'); self.filter_layer_enabled.setChecked(True)
        form.addRow(self.filter_layer_enabled); self.filter_layer_enabled.toggled.connect(self.filter_layer_property_changed)
        form.addRow(self.button('Xem thử 5 giây',self.preview_render))
        split.addWidget(tabs); split.setStretchFactor(0,3); split.setStretchFactor(1,1); lay.addWidget(split,1)

    def source_property_changed(self,*_):
        if self.layer_loading or self.syncing: return
        for key,control in self.source_fields.items(): self.advanced[key]=control.value()
        self.advanced['source_flip_vertical']=self.source_flip_vertical.isChecked()
        self.advanced['background_mode']=self.source_background_mode.currentData() or 'black'
        color=self.source_background_color.text().strip()
        if QColor(color).isValid(): self.advanced['background_color']=QColor(color).name()
        self.mark_dirty()

    def pick_source_background_color(self):
        color=QColorDialog.getColor(QColor(self.advanced.get('background_color','#000000')),self,'Màu nền khung hình')
        if color.isValid(): self.source_background_color.setText(color.name())

    def _valid_audio_layer_source(self,path):
        try:
            if not engine.probe(path).get('has_audio'): raise ValueError('Tệp đã chọn không có âm thanh.')
        except Exception as exc:
            self.info('Không mở được âm thanh',str(exc)); return False
        return True

    def add_audio_layer(self):
        path=QFileDialog.getOpenFileName(self,'Thêm lớp âm thanh','','Âm thanh (*.mp3 *.wav *.m4a *.aac *.ogg *.flac);;Tất cả (*)')[0]
        if path and self._valid_audio_layer_source(path): self.add_layer('audio_layers',dict(path=path,volume=1.,loop=True,enabled=True,start=0.,end=0.,fade_in=0.,fade_out=0.))

    def choose_audio_layer_source(self):
        if not self.layer_selection or self.layer_selection[0]!='audio_layers': return
        path=QFileDialog.getOpenFileName(self,'Đổi tệp âm thanh','','Âm thanh (*.mp3 *.wav *.m4a *.aac *.ogg *.flac);;Tất cả (*)')[0]
        if path and self._valid_audio_layer_source(path):
            kind,index=self.layer_selection; self.advanced[kind][index]['path']=path
            self.audio_layer_fields['path'].setText(path); self.mark_dirty(); self.refresh_canvas()

    def audio_layer_property_changed(self,*_):
        if self.layer_loading or not self.layer_selection or self.layer_selection[0]!='audio_layers': return
        kind,index=self.layer_selection
        if not 0<=index<len(self.advanced[kind]): return
        data=self.advanced[kind][index]
        for key,control in self.audio_layer_fields.items(): data[key]=control.text() if isinstance(control,QLineEdit) else control.value()
        data.update(loop=self.audio_layer_loop.isChecked(),enabled=self.audio_layer_enabled.isChecked())
        self.mark_dirty(); self.refresh_canvas()

    def add_filter_layer(self):
        self.add_layer('filter_layers',dict(filter='vintage',intensity=.5,start=0.,end=0.,enabled=True))

    def filter_layer_property_changed(self,*_):
        if self.layer_loading or not self.layer_selection or self.layer_selection[0]!='filter_layers': return
        kind,index=self.layer_selection
        if not 0<=index<len(self.advanced[kind]): return
        data=self.advanced[kind][index]
        data.update(filter=self.filter_layer_kind.currentData(),enabled=self.filter_layer_enabled.isChecked())
        for key,control in self.filter_layer_fields.items(): data[key]=control.value()
        self.mark_dirty(); self.refresh_canvas()

    def add_text_layer(self):
        text,ok=QInputDialog.getMultiLineText(self,'Thêm văn bản','Nội dung:','Văn bản mới')
        if ok and text.strip(): self.add_layer('text_layers',dict(text=text,font='Arial',font_size=36,color='#ffffff',opacity=1,x=40,y=40,width=300,height=60,start=0,end=0,outline=2,outline_color='#000000',bg_color='#000000',bg_opacity=0,bg_radius=0,bold=False,uppercase=False,enabled=True,rotation=0,flip_horizontal=False,flip_vertical=False,marquee='none',speed=80))
    def add_image_layer(self):
        path=QFileDialog.getOpenFileName(self,'Thêm ảnh / GIF','','Ảnh và GIF (*.png *.jpg *.jpeg *.webp *.gif)')[0]
        if path: self.add_layer('image_layers',dict(path=path,x=40,y=100,width=200,height=140,opacity=1,start=0,end=0,loop=True,anchor='top-left',enabled=True,rotation=0,flip_horizontal=False,flip_vertical=False))
    def add_blur_layer(self): self.add_layer('blur_zones',dict(x=40,y=40,width=200,height=100,strength=12,shape='rect',feather=0,corner=0,enabled=True))
    def add_layer(self,kind,data):
        self.advanced[kind].append(data); self.layer_selection=(kind,len(self.advanced[kind])-1); self.mark_dirty(); self.refresh_canvas()
        self.choose_layer(*self.layer_selection)
    def delete_layer(self):
        if not self.layer_selection: return
        kind,index=self.layer_selection
        if index<len(self.advanced[kind]): self.advanced[kind].pop(index)
        self.layer_selection=None; self.refresh_canvas(); self.mark_dirty()
        for control in self.layer_fields.values(): control.setEnabled(False)
    def duplicate_layer(self):
        if not self.layer_selection: return
        kind,index=self.layer_selection
        if not 0<=index<len(self.advanced[kind]): return
        self.add_layer(kind,copy.deepcopy(self.advanced[kind][index]))
        self.choose_layer(kind,len(self.advanced[kind])-1)
    def choose_image_layer_source(self):
        if not self.layer_selection or self.layer_selection[0]!='image_layers': return
        path=QFileDialog.getOpenFileName(self,'Thay ảnh / GIF','','Ảnh và GIF (*.png *.jpg *.jpeg *.webp *.gif)')[0]
        if path: self.layer_fields['path'].setText(path)
    def layer_entries(self):
        return [(kind,i,row) for kind in ['text_layers','image_layers','blur_zones'] for i,row in enumerate(self.advanced[kind])]
    def layer_selected(self,index):
        entries=self.layer_entries()
        if 0<=index<len(entries): self.choose_layer(*entries[index][:2])
    def choose_layer(self,kind,index):
        if kind not in self.advanced or not 0<=index<len(self.advanced[kind]): return
        self.layer_selection=(kind,index); self.layer_loading=True; data=self.advanced[kind][index]
        if kind=='filter_layers':
            for control in list(self.layer_fields.values())+[self.layer_font,self.layer_marquee,self.layer_text_effect,self.layer_bg_fit,self.layer_shape,self.layer_loop,self.layer_enabled,self.layer_bold,self.layer_upper,self.layer_flip_horizontal,self.layer_flip_vertical,self.layer_anchor,self.layer_blend,self.layer_color,self.layer_bg]: control.setEnabled(False)
            self.filter_layer_kind.setCurrentIndex(max(0,self.filter_layer_kind.findData(data.get('filter','none'))))
            for key,control in self.filter_layer_fields.items(): control.setValue(float(data.get(key,.5 if key=='intensity' else 0)))
            self.filter_layer_enabled.setChecked(data.get('enabled',True))
            self.layer_editor_tabs.setCurrentIndex(self.filter_layer_tab_index)
            self.layer_loading=False; return
        if kind=='audio_layers':
            for control in list(self.layer_fields.values())+[self.layer_font,self.layer_marquee,self.layer_text_effect,self.layer_bg_fit,self.layer_shape,self.layer_loop,self.layer_enabled,self.layer_bold,self.layer_upper,self.layer_flip_horizontal,self.layer_flip_vertical,self.layer_anchor,self.layer_blend,self.layer_color,self.layer_bg]: control.setEnabled(False)
            for key,control in self.audio_layer_fields.items():
                value=data.get(key,'' if key=='path' else 1. if key=='volume' else 0.)
                control.setText(str(value)) if isinstance(control,QLineEdit) else control.setValue(float(value))
            self.audio_layer_loop.setChecked(data.get('loop',True)); self.audio_layer_enabled.setChecked(data.get('enabled',True))
            self.layer_editor_tabs.setCurrentIndex(self.audio_layer_tab_index)
            self.layer_loading=False; return
        for key,c in self.layer_fields.items():
            defaults=dict(text='',path='',font_size=36,opacity=1,speed=80,strength=12,outline=2,color='#ffffff',outline_color='#000000',bg_color='#000000')
            default=defaults.get(key,0)
            if isinstance(c,QLineEdit): c.setText(str(data.get(key,default)))
            else: c.setValue(float(data.get(key,default))) if isinstance(c,QDoubleSpinBox) else c.setValue(int(data.get(key,default)))
            text_only={'text','font_size','speed','outline','bg_opacity','bg_radius','color','outline_color','bg_color'}
            blur_only={'strength','feather','corner'}
            image_only={'path'}
            visual_only={'rotation','opacity'}
            c.setEnabled((key not in text_only or kind=='text_layers') and (key not in blur_only or kind=='blur_zones') and (key not in image_only or kind=='image_layers') and (key not in visual_only or kind!='blur_zones'))
        self.layer_font.setCurrentFont(QFont(data.get('font','Arial'))); self.layer_marquee.setCurrentIndex(max(0,self.layer_marquee.findData(data.get('marquee','none')))); self.layer_shape.setCurrentText(data.get('shape','rect')); self.layer_loop.setChecked(data.get('loop',False))
        self.layer_text_effect.setCurrentIndex(max(0,self.layer_text_effect.findData(data.get('text_effect','outline')))); self.layer_bg_fit.setCurrentIndex(max(0,self.layer_bg_fit.findData(data.get('bg_fit','hug'))))
        self.layer_enabled.setChecked(data.get('enabled',True)); self.layer_bold.setChecked(data.get('bold',False)); self.layer_upper.setChecked(data.get('uppercase',False)); self.layer_flip_horizontal.setChecked(data.get('flip_horizontal',False)); self.layer_flip_vertical.setChecked(data.get('flip_vertical',False)); self.layer_anchor.setCurrentText(data.get('anchor','top-left'))
        for control in [self.layer_font,self.layer_marquee,self.layer_text_effect,self.layer_bg_fit,self.layer_bold,self.layer_upper]: control.setEnabled(kind=='text_layers')
        for control in [self.layer_loop,self.layer_anchor]: control.setEnabled(kind=='image_layers')
        for control in [self.layer_flip_horizontal,self.layer_flip_vertical]: control.setEnabled(kind!='blur_zones')
        self.layer_shape.setEnabled(kind=='blur_zones')
        self.layer_enabled.setEnabled(True); self.layer_color.setEnabled(kind=='text_layers'); self.layer_bg.setEnabled(kind=='text_layers')
        self.layer_blend.setCurrentIndex(max(0,self.layer_blend.findData(data.get('blend_mode','normal')))); self.layer_blend.setEnabled(kind in ('text_layers','image_layers'))
        self.layer_loading=False
    def layer_property_changed(self,*_):
        if self.layer_loading or not self.layer_selection: return
        if self.layer_selection[0] in ('audio_layers','filter_layers'): return
        kind,index=self.layer_selection; data=self.advanced[kind][index]
        for key,c in self.layer_fields.items():
            if c.isEnabled():
                if key in {'color','outline_color','bg_color'} and not QColor(c.text()).isValid(): continue
                data[key]=c.text() if isinstance(c,QLineEdit) else c.value()
        if kind=='text_layers': data.update(font=self.layer_font.currentFont().family(),marquee=self.layer_marquee.currentData(),text_effect=self.layer_text_effect.currentData(),bg_fit=self.layer_bg_fit.currentData(),bold=self.layer_bold.isChecked(),uppercase=self.layer_upper.isChecked())
        elif kind=='image_layers': data.update(loop=self.layer_loop.isChecked(),anchor=self.layer_anchor.currentText())
        else: data['shape']=self.layer_shape.currentText()
        data['enabled']=self.layer_enabled.isChecked()
        if kind!='blur_zones': data.update(flip_horizontal=self.layer_flip_horizontal.isChecked(),flip_vertical=self.layer_flip_vertical.isChecked(),blend_mode=self.layer_blend.currentData())
        self.mark_dirty(); self.refresh_canvas()
    def canvas_changed(self,kind,index,data):
        if self.layer_loading: return
        self.advanced[kind][index]=copy.deepcopy(data); self.choose_layer(kind,index); self.mark_dirty()
    def pick_layer_color(self,key):
        if not self.layer_selection: return
        if self.layer_selection[0]!='text_layers': return
        kind,index=self.layer_selection; data=self.advanced[kind][index]; color=QColorDialog.getColor(QColor(data.get(key,'#ffffff')),self)
        if color.isValid(): data[key]=color.name(); self.choose_layer(kind,index); self.mark_dirty(); self.refresh_canvas()
    def pick_subtitle_color(self,key):
        color=QColorDialog.getColor(QColor(self.advanced['subtitle_style'].get(key,'#ffffff')),self)
        if color.isValid(): self.advanced['subtitle_style'][key]=color.name(); self.mark_dirty()
    def pick_border_color(self):
        color=QColorDialog.getColor(QColor(self.advanced['frame_border_color']),self)
        if color.isValid(): self.advanced['frame_border_color']=color.name(); self.mark_dirty()
    def apply_style_template(self,index):
        templates=[{},dict(font='Arial',size=24,color='#ffffff',outline=2,bg_opacity=0,bold=False),dict(font='Arial',size=30,color='#ffdf33',outline=3,bg_opacity=0,bold=True),dict(font='Arial',size=28,color='#ffffff',outline=0,bg_opacity=.75,bold=True),dict(font='Arial',size=28,color='#ffffff',outline=2,bg_opacity=0,bold=False),dict(font='Arial',size=44,color='#ffffff',outline=3,bg_opacity=0,bold=True)]
        if index: self.advanced['subtitle_style'].update(templates[index]); self.apply_advanced(self.advanced); self.mark_dirty()
    def advanced_changed(self,*_):
        if self.layer_loading or not hasattr(self,'style_font'): return
        self.advanced['filter']=self.filter_pick.currentData(); self.advanced['zoom_enabled']=self.zoom_on.isChecked()
        for key,c in self.fx_fields.items(): self.advanced[key]=c.value()
        self.advanced['subtitle_style'].update(font=self.style_font.currentFont().family(),text_effect=self.style_text_effect.currentData(),bg_fit=self.style_bg_fit.currentData(),size=self.style_size.value(),outline=self.style_outline.value(),bg_opacity=self.style_bg.value(),alignment=self.style_align.currentIndex()+1,bold=self.style_bold.isChecked(),uppercase=self.style_upper.isChecked())
        self.mark_dirty()
    def ensure_zoom_cycle(self,*_):
        if self.layer_loading: return
        hold=self.fx_fields['zoom_hold'].value()
        period=self.fx_fields['zoom_period']
        period.setMinimum(max(.5,hold+.05))
        self.advanced['zoom_period']=period.value()
        self.mark_dirty()
    def apply_advanced(self,settings):
        self.layer_loading=True
        self.fx_fields['zoom_period'].setMinimum(.5)
        for key,default in DEFAULT_ADVANCED.items(): self.advanced[key]=copy.deepcopy(settings.get(key,default))
        for key,control in self.source_fields.items(): control.setValue(self.advanced[key])
        self.source_flip_vertical.setChecked(self.advanced['source_flip_vertical'])
        self.source_background_mode.setCurrentIndex(max(0,self.source_background_mode.findData(self.advanced['background_mode'])))
        self.source_background_color.setText(self.advanced['background_color'])
        self.filter_pick.setCurrentIndex(max(0,self.filter_pick.findData(self.advanced.get('filter','none')))); self.zoom_on.setChecked(self.advanced['zoom_enabled'])
        for key,c in self.fx_fields.items(): c.setValue(self.advanced[key])
        s=self.advanced['subtitle_style']; self.style_font.setCurrentFont(QFont(s.get('font','Arial'))); self.style_size.setValue(s.get('size',24)); self.style_outline.setValue(s.get('outline',2)); self.style_bg.setValue(s.get('bg_opacity',0)); self.style_align.setCurrentIndex(s.get('alignment',2)-1); self.style_bold.setChecked(s.get('bold',False)); self.style_upper.setChecked(s.get('uppercase',False))
        self.style_text_effect.setCurrentIndex(max(0,self.style_text_effect.findData(s.get('text_effect','outline')))); self.style_bg_fit.setCurrentIndex(max(0,self.style_bg_fit.findData(s.get('bg_fit','hug'))))
        if hasattr(self,'enc_format'):
            enc=self.advanced['encoder']
            for c,key in [(self.enc_format,'format'),(self.enc_codec,'codec'),(self.enc_device,'device'),(self.enc_mode,'bitrate_mode'),(self.enc_preset,'preset')]: c.setCurrentText(enc.get(key,''))
            self.enc_quality.setValue(enc.get('quality',20)); self.enc_bitrate.setText(str(enc.get('bitrate','5M'))); self.enc_maxrate.setText(str(enc.get('maxrate','8M')))
            self.enc_bframes.setChecked(enc.get('bframes',True))
            self.audio_fx.setCurrentText(self.advanced.get('audio_effect','none')); self.voice_path.setText(self.advanced.get('voice_path','')); self.background_path.setText(self.advanced.get('background_path','')); self.voice_volume.setValue(self.advanced.get('voice_volume',1)); self.voice_delay.setValue(self.advanced.get('voice_delay',0)); self.background_volume.setValue(settings.get('background_volume',1))
        self.layer_loading=False; self.ensure_zoom_cycle(); self.refresh_canvas()
    def apply_all_settings(self,settings):
        was_syncing=self.syncing; self.syncing=True
        for key,c in self.controls.items():
            defaults=dict(speed=1,brightness=0,contrast=1,saturation=1,volume=1,music_volume=.35)
            c.setValue(settings.get(key,defaults.get(key,0)))
        sizes=[(0,0),(1920,1080),(1080,1920),(1280,720),(720,1280),(1080,1080),(854,480)]
        size=(settings.get('width',0),settings.get('height',0)); self.resolution.setCurrentIndex(sizes.index(size) if size in sizes else 0)
        self.fit.setCurrentIndex(0 if settings.get('fit','contain')=='contain' else 1); self.fps.setValue(settings.get('fps',30)); self.rotation.setCurrentIndex(int(settings.get('rotation',0))//90)
        self.mirror.setChecked(settings.get('mirror',False)); self.mute.setChecked(settings.get('mute',False)); self.music_loop.setChecked(settings.get('music_loop',True))
        for edit,key in [(self.music,'music_path'),(self.subtitle,'subtitle_path'),(self.logo,'logo_path')]: edit.setText(settings.get(key,''))
        self.sub_size.setValue(settings.get('subtitle_size',24)); self.logo_width.setValue(settings.get('logo_width',160)); self.logo_pos.setCurrentIndex(['top-right','top-left','bottom-right','bottom-left'].index(settings.get('logo_position','top-right')))
        blur=settings.get('blur_region'); self.blur_on.setChecked(bool(blur))
        for n,value in zip(self.blur_coords,blur or [0,0,100,100]): n.setValue(value)
        self.apply_advanced(settings); self.syncing=was_syncing
    def refresh_canvas(self):
        if not hasattr(self,'canvas'): return
        settings=super().settings(); width,height=settings['width'],settings['height']; background=None
        path=self.current_media or (self.clips[self.current_clip]['path'] if 0<=self.current_clip<len(self.clips) else None)
        if path:
            media=next((m for m in self.library if m['path']==path),None)
            if not width and media: width,height=media['width'],media['height']
            if not settings['width'] and settings['rotation']%180==90: width,height=height,width
            thumb=self.thumbnail_path(path)
            if thumb.is_file():
                background=QPixmap(str(thumb)).transformed(QTransform().rotate(settings['rotation']))
                if settings['mirror']: background=background.transformed(QTransform().scale(-1,1))
        self.layer_loading=True; self.layer_list.clear()
        for kind,index,row in self.layer_entries(): self.layer_list.addItem(f"{index+1} · "+(row.get('text','Văn bản')[:35] if kind=='text_layers' else Path(row.get('path','Ảnh')).name if kind=='image_layers' else 'Vùng làm mờ')+f" · ({row.get('x',0)}, {row.get('y',0)})")
        self.canvas.rebuild(self.advanced,(width or 1280,height or 720),background,settings['fit']); self.layer_loading=False

    def build_subtitles(self):
        super().build_subtitles(); self.srt_table.setColumnCount(4); self.srt_table.setHorizontalHeaderLabels(['Bắt đầu (giây)','Kết thúc (giây)','Nội dung','Giọng từng câu']); self.srt_table.setColumnWidth(3,170)
        lay=self.pages.widget(3).layout(); tools=QHBoxLayout()
        for text,func in [('Phụ đề gốc',lambda:self.show_subtitle_role('original')),('Phụ đề dịch',lambda:self.show_subtitle_role('translated')),('Song ngữ',self.make_bilingual),('Gộp dòng',self.merge_subtitle_rows),('Dịch thời gian',self.shift_subtitle_rows),('Hoàn tác',self.undo_subtitle),('Nhập VTT',self.import_vtt),('Xuất VTT',self.export_vtt)]: tools.addWidget(self.button(text,func))
        lay.insertLayout(2,tools)
        search=QHBoxLayout(); self.sub_find=QLineEdit(); self.sub_find.setPlaceholderText('Tìm trong phụ đề'); self.sub_replace=QLineEdit(); self.sub_replace.setPlaceholderText('Thay bằng'); self.sub_case=QCheckBox('Hoa/thường'); self.sub_word=QCheckBox('Nguyên từ')
        for c in [self.sub_find,self.sub_replace,self.sub_case,self.sub_word]: search.addWidget(c)
        search.addWidget(self.button('Tìm',self.find_subtitle)); search.addWidget(self.button('Thay tất cả',self.replace_subtitle)); lay.insertLayout(3,search)
    def add_subtitle(self,row=None):
        super().add_subtitle(row)
        if self.srt_table.columnCount()<4: return
        ix=self.srt_table.rowCount()-1; c=self.combo(['Mặc định','vi-VN-HoaiMyNeural','vi-VN-NamMinhNeural','en-US-JennyNeural'])
        c.setCurrentText(row.get('voice','Mặc định') if isinstance(row,dict) else 'Mặc định'); c.currentIndexChanged.connect(self.mark_subtitle_dirty); self.srt_table.setCellWidget(ix,3,c)
    def subtitle_rows(self):
        rows=super().subtitle_rows()
        for ix,row in enumerate(rows):
            c=self.srt_table.cellWidget(ix,3)
            if c and c.currentText()!='Mặc định': row['voice']=c.currentText()
        return rows
    def set_subtitle_rows(self,rows):
        self.syncing=True; self.srt_table.setRowCount(0)
        for row in rows: self.add_subtitle(row)
        self.syncing=False; self.mark_subtitle_dirty()
    def snapshot_subtitle(self):
        try: self.undo_subtitles.append(copy.deepcopy(self.subtitle_rows())); self.undo_subtitles=self.undo_subtitles[-30:]; return True
        except Exception as exc: self.info('Phụ đề chưa hợp lệ',str(exc)); return False
    def undo_subtitle(self):
        if self.undo_subtitles: self.set_subtitle_rows(self.undo_subtitles.pop())
    def merge_subtitle_rows(self):
        if not self.snapshot_subtitle(): return
        indices=sorted({i.row() for i in self.srt_table.selectedIndexes()})
        if len(indices)<2: self.info('Chọn nhiều dòng','Chọn ít nhất hai dòng để gộp.'); return
        self.set_subtitle_rows(subtitle_tools.merge_rows(self.subtitle_rows(),indices))
    def shift_subtitle_rows(self):
        value,ok=QInputDialog.getDouble(self,'Dịch thời gian','Số giây (âm: sớm hơn):',0,-86400,86400,3)
        if ok and self.snapshot_subtitle():
            try: self.set_subtitle_rows(subtitle_tools.shift_timings(self.subtitle_rows(),value))
            except Exception as exc: self.info('Không dịch được thời gian',str(exc))
    def find_subtitle(self):
        needle=self.sub_find.text(); self.srt_table.clearSelection()
        if not needle: return
        for ix,row in enumerate(self.subtitle_rows()):
            text=row['text']
            if (needle if self.sub_case.isChecked() else needle.casefold()) in (text if self.sub_case.isChecked() else text.casefold()): self.srt_table.item(ix,2).setSelected(True)
    def replace_subtitle(self):
        if not self.sub_find.text() or not self.snapshot_subtitle(): return
        rows,count=subtitle_tools.replace_text(self.subtitle_rows(),self.sub_find.text(),self.sub_replace.text(),case_sensitive=self.sub_case.isChecked(),whole_word=self.sub_word.isChecked()); self.set_subtitle_rows(rows); self.statusBar().showMessage(f'Đã thay {count} vị trí',5000)
    def import_vtt(self):
        path=QFileDialog.getOpenFileName(self,'Nhập WebVTT','','Phụ đề (*.vtt)')[0]
        if path:
            try: self.set_subtitle_rows(subtitle_tools.load_vtt(path)); self.srt_path.setText(path)
            except Exception as exc: self.info('Không đọc được VTT',str(exc))
    def export_vtt(self):
        path=self.output_path('Xuất WebVTT','.vtt','phu_de')
        if path:
            try: subtitle_tools.save_vtt(self.subtitle_rows(),path)
            except Exception as exc: self.info('Không xuất được VTT',str(exc))
    def load_srt_table(self,path,role=None):
        try: rows=subtitle_tools.load_subtitles(path)
        except Exception as exc: self.info('Không đọc được phụ đề',str(exc)); return
        self.syncing=True; self.srt_table.setRowCount(0)
        for row in rows: self.add_subtitle(row)
        self.syncing=False; self.subtitle_dirty=False; self.srt_path.setText(path); self.subtitle.setText(path)
        if role=='translated': self.translated_srt=path
        elif role!='bilingual': self.original_srt=path
    def show_subtitle_role(self,role):
        if self.subtitle_dirty and not self.save_srt(): return
        path=self.original_srt if role=='original' else self.translated_srt
        if path and Path(path).is_file(): self.load_srt_table(path,role)
        else: self.info('Chưa có phụ đề','Chạy Pipeline hoặc mở tệp phụ đề tương ứng trước.')
    def make_bilingual(self):
        try: rows=subtitle_tools.bilingual_rows(subtitle_tools.load_subtitles(self.original_srt),subtitle_tools.load_subtitles(self.translated_srt))
        except Exception as exc: self.info('Cần hai bản phụ đề',str(exc)); return
        if self.snapshot_subtitle(): self.set_subtitle_rows(rows); self.srt_path.setText('Phụ đề song ngữ chưa lưu')
    def save_srt(self):
        try: rows=self.subtitle_rows()
        except Exception as exc: self.info('Phụ đề chưa hợp lệ',str(exc)); return
        if not rows: self.info('Chưa có phụ đề','Thêm nội dung trước khi lưu.'); return
        path=self.output_path('Lưu phụ đề và giọng từng câu','.srt','phu_de')
        if path:
            try: subtitle_tools.save_subtitles(rows,path); self.subtitle.setText(path); self.srt_path.setText(path); self.subtitle_dirty=False; return path
            except Exception as exc: self.info('Không lưu được phụ đề',str(exc))
    def translate(self):
        src=self.current_srt()
        if not src or not Path(src).is_file(): self.info('Cần phụ đề','Mở hoặc lưu phụ đề trước.'); return
        if not self.ai_confirm('Gửi nội dung phụ đề tới Google để dịch?'): return
        path=self.output_path('Lưu phụ đề dịch','.srt','ban_dich')
        if path:
            self.original_srt=src; lang=self.language.currentText()
            self.start_job('Dịch phụ đề',lambda p,c:ai_tools.translate_srt(src,path,lang,p,c),lambda r:(self.load_srt_table(r,'translated'),self.navigate(3)))
    def dub(self):
        src=self.current_srt()
        if not src or not Path(src).is_file(): self.info('Cần phụ đề','Mở hoặc lưu phụ đề trước.'); return
        if not self.ai_confirm('Gửi nội dung từng câu tới Microsoft Edge để tạo giọng đọc?'): return
        path=self.output_path('Lưu giọng theo từng câu','.wav','long_tieng')
        if path:
            voice=self.voice.currentText()
            self.start_job('Lồng tiếng',lambda p,c:workflow.dub_subtitles(src,path,voice,p,c),lambda r:(self.voice_path.setText(r),self.mute.setChecked(True),self.navigate(9)))

    def build_utilities(self):
        lay=self.page('OCR & tách âm thanh','Chạy trên máy: đọc phụ đề đã gắn trên hình, nhận dạng giọng nói, tách lời thoại và nhạc nền.')
        tabs=QTabWidget(); lay.addWidget(tabs,1)
        form=self.option_form(tabs,'OCR phụ đề')
        self.ocr_source=QLineEdit(); form.addRow('Video nguồn',self.file_row(self.ocr_source,filter='Video (*.mp4 *.mkv *.mov *.avi *.webm)'))
        self.ocr_language=self.combo(['ch','en','vi']); form.addRow('Tiếng trên hình',self.ocr_language)
        self.ocr_interval=self.number(.5,.1,5); form.addRow('Lấy mẫu (giây)',self.ocr_interval)
        self.ocr_custom=QCheckBox('Chọn vùng quét (pixel nguồn)'); form.addRow(self.ocr_custom); self.ocr_coords=[]
        for text,value in [('X',0),('Y',0),('Rộng',1280),('Cao',200)]:
            n=QSpinBox(); n.setRange(0,12000); n.setValue(value); self.ocr_coords.append(n); form.addRow(text,n)
        note=QLabel('Không chọn vùng: quét phần dưới video. OCR chạy trên CPU; mô hình Latin/tiếng Việt có thể cần tải lần đầu.'); note.setWordWrap(True); note.setObjectName('muted'); form.addRow(note)
        form.addRow(self.button('Trích chữ trên video',self.ocr_run,True))
        form=self.option_form(tabs,'Tách giọng')
        self.separate_source=QLineEdit(); form.addRow('Video / âm thanh',self.file_row(self.separate_source))
        self.separate_mode=self.combo(['vocals','stems']); form.addRow('2 phần / 4 phần',self.separate_mode)
        note=QLabel('Demucs chạy trên CPU, cần tải mô hình lần đầu. Chế độ vocals tạo giọng và nền; stems tạo giọng, trống, bass, nhạc.'); note.setWordWrap(True); note.setObjectName('muted'); form.addRow(note)
        form.addRow(self.button('Tách âm thanh',self.separate_run,True))
        form=self.option_form(tabs,'Nhận dạng')
        self.rec_source=QLineEdit(); form.addRow('Video / âm thanh',self.file_row(self.rec_source)); self.rec_model=self.combo(['base','tiny','small','medium','large-v3','turbo']); form.addRow('Whisper',self.rec_model)
        self.rec_language=self.combo(['auto','zh','vi','en','ja','ko','th']); form.addRow('Tiếng nguồn',self.rec_language); form.addRow(self.button('Tạo phụ đề từ giọng nói',self.recognize_run,True))
    def ocr_region(self):
        if not self.ocr_custom.isChecked(): return None
        return dict(zip(['x','y','width','height'],[n.value() for n in self.ocr_coords]))
    def utility_source(self,edit):
        path=edit.text().strip() or self.current_media or (self.clips[self.current_clip]['path'] if 0<=self.current_clip<len(self.clips) else '')
        if not Path(path).is_file(): self.info('Thiếu tệp nguồn','Chọn tệp nguồn hoặc chọn một video trong Studio.'); return None
        return path
    def ocr_run(self):
        source=self.utility_source(self.ocr_source)
        if not source: return
        path=self.output_path('Lưu phụ đề OCR','.srt','ocr')
        if path:
            region=self.ocr_region(); lang=self.ocr_language.currentText(); interval=self.ocr_interval.value()
            self.start_job('OCR phụ đề',lambda p,c:media_ai.extract_ocr(source,path,region,lang,interval,p,c),lambda r:(self.load_srt_table(r,'original'),self.navigate(3)))
    def separate_run(self):
        source=self.utility_source(self.separate_source)
        if not source: return
        folder=QFileDialog.getExistingDirectory(self,'Lưu các phần âm thanh',self.out_dir)
        if not folder: return
        mode=self.separate_mode.currentText()
        def done(result):
            self.background_path.setText(result.get('background','')); self.assets.setdefault(source,{}).update(result); self.navigate(9); self.info('Đã tách âm thanh','\n'.join(f'{k}: {v}' for k,v in result.items()))
        self.start_job('Tách giọng / nhạc',lambda p,c:media_ai.separate_audio(source,folder,mode,p,c),done)
    def recognize_run(self):
        source=self.utility_source(self.rec_source)
        if not source: return
        path=self.output_path('Lưu phụ đề nhận dạng','.srt','nhan_dang')
        if path:
            lang=self.rec_language.currentText(); model=self.rec_model.currentText()
            self.start_job('Nhận dạng '+model,lambda p,c:media_ai.transcribe_model(source,path,lang,model,p,c),lambda r:(self.load_srt_table(r,'original'),self.navigate(3)))

    def build_export_options(self):
        lay=self.page('Xuất video & âm thanh','Chọn định dạng, codec, chất lượng và các nguồn âm thanh độc lập.')
        tabs=QTabWidget(); lay.addWidget(tabs,1); form=self.option_form(tabs,'Xuất')
        self.enc_format=self.combo(['mp4','mkv','mov','webm']); form.addRow('Định dạng',self.enc_format)
        self.enc_codec=self.combo(['h264','hevc','vp9']); form.addRow('Codec',self.enc_codec)
        self.enc_format.currentTextChanged.connect(self.format_changed)
        self.enc_device=self.combo(['auto','cpu']); form.addRow('Thiết bị',self.enc_device); form.addRow(self.button('Kiểm tra GPU encoder',self.detect_hardware))
        self.enc_quality=QSpinBox(); self.enc_quality.setRange(0,51); self.enc_quality.setValue(20); form.addRow('CRF / CQ (nhỏ: nét)',self.enc_quality)
        self.enc_mode=self.combo(['crf','vbr','cbr']); form.addRow('Điều khiển bitrate',self.enc_mode)
        self.enc_bitrate=QLineEdit('5M'); self.enc_maxrate=QLineEdit('8M'); form.addRow('Bitrate',self.enc_bitrate); form.addRow('Trần bitrate',self.enc_maxrate)
        self.enc_preset=self.combo(['veryfast','fast','medium','slow']); form.addRow('Tốc độ encode',self.enc_preset)
        self.enc_bframes=QCheckBox('Cho phép B-frames'); self.enc_bframes.setChecked(True); form.addRow(self.enc_bframes)
        self.enc_codec.currentTextChanged.connect(lambda codec:self.enc_bframes.setEnabled(codec!='vp9'))
        self.export_folder=QLineEdit(self.out_dir); form.addRow('Thư mục kết quả',self.file_row(self.export_folder,folder=True))
        form.addRow(self.button('Xuất dự án cắt ghép',self.export,True)); form.addRow(self.button('Xuất các video đã chọn',lambda:self.run_pipeline('export')))
        form=self.option_form(tabs,'Âm thanh')
        self.voice_path=QLineEdit(); self.background_path=QLineEdit(); form.addRow('Giọng lồng',self.file_row(self.voice_path,filter='Âm thanh (*.wav *.mp3 *.m4a *.aac)')); form.addRow('Nền đã tách',self.file_row(self.background_path,filter='Âm thanh (*.wav *.mp3 *.m4a *.aac)'))
        self.voice_volume=self.number(1,0,4); self.voice_delay=self.number(0,0,60); self.background_volume=self.number(1,0,4); form.addRow('Âm lượng giọng',self.voice_volume); form.addRow('Trễ giọng (s)',self.voice_delay); form.addRow('Âm lượng nền tách',self.background_volume)
        self.audio_fx=self.combo(['none','denoise','compressor','reverb','normalize','bass','treble']); form.addRow('Hiệu ứng âm thanh',self.audio_fx)
        form.addRow(self.button('Âm gốc / nhạc nền / lặp',lambda:self.navigate(2))); form.addRow(self.button('Tách giọng khỏi nền',lambda:self.navigate(8)))
        for c in [self.enc_format,self.enc_codec,self.enc_device,self.enc_quality,self.enc_mode,self.enc_bitrate,self.enc_maxrate,self.enc_preset,self.enc_bframes,self.voice_path,self.background_path,self.voice_volume,self.voice_delay,self.background_volume,self.audio_fx]:
            for sig in ['valueChanged','currentIndexChanged','textChanged','toggled']:
                if hasattr(c,sig): getattr(c,sig).connect(self.mark_dirty); break
    def detect_hardware(self):
        from advanced_render import hardware_encoders
        def done(encoders):
            devices=['auto','cpu']+sorted({e['device'] for e in encoders if e['device']!='cpu'}); current=self.enc_device.currentText(); self.enc_device.clear(); self.enc_device.addItems(devices); self.enc_device.setCurrentText(current if current in devices else 'auto'); self.info('Encoder khả dụng','\n'.join(e.get('label',e['encoder']) for e in encoders))
        self.background(lambda p,c:hardware_encoders(),done)
    def format_changed(self,fmt):
        if fmt=='webm': self.enc_codec.setCurrentText('vp9')
        elif fmt=='mov' and self.enc_codec.currentText()=='vp9': self.enc_codec.setCurrentText('h264')
        model=self.enc_codec.model()
        for index in range(self.enc_codec.count()):
            item=model.item(index)
            item.setEnabled((self.enc_codec.itemText(index)=='vp9') if fmt=='webm' else (self.enc_codec.itemText(index)!='vp9') if fmt=='mov' else True)

    def settings(self):
        settings=super().settings(); settings.update(copy.deepcopy(self.advanced))
        if hasattr(self,'enc_format'):
            settings['encoder']=dict(format=self.enc_format.currentText(),codec=self.enc_codec.currentText(),device=self.enc_device.currentText(),quality=self.enc_quality.value(),preset=self.enc_preset.currentText(),bitrate_mode=self.enc_mode.currentText(),bitrate=self.enc_bitrate.text().strip(),maxrate=self.enc_maxrate.text().strip(),bframes=self.enc_bframes.isChecked())
            settings.update(voice_path=self.voice_path.text().strip(),background_path=self.background_path.text().strip(),voice_volume=self.voice_volume.value(),voice_delay=self.voice_delay.value(),background_volume=self.background_volume.value(),audio_effect=self.audio_fx.currentText(),original_volume=0 if self.mute.isChecked() else self.controls['volume'].value())
        return settings
    def build_help(self):
        super().build_help()
        from PySide6.QtWidgets import QTextEdit
        text=self.pages.widget(5).findChild(QTextEdit)
        text.setHtml('<h2>Studio 2.0</h2><p><b>Studio/Pipeline:</b> Nhập hoặc kéo thả nhiều video, chọn các bước trích phụ đề, dịch, lồng tiếng, tách nền và xuất. Mỗi lần chạy có manifest để tiếp tục nếu bị dừng.</p><p><b>Thành phần:</b> Văn bản, chữ chạy, ảnh/GIF, logo, nhiều vùng mờ, bộ lọc, zoom, khung viền và fade. Kéo lớp để đổi vị trí; kéo góc dưới phải để đổi cỡ. Nhấn Xem thử 5 giây để xem hiệu ứng thật.</p><p><b>Phụ đề:</b> SRT/VTT, hai bản gốc/dịch, song ngữ, gộp dòng, dịch thời gian, tìm/thay, hoàn tác và giọng nam/nữ theo từng câu.</p><p><b>OCR &amp; tách âm thanh:</b> OCR Trung/Anh/Việt, Whisper nhiều mô hình, Demucs tách giọng/nhạc hoặc bốn stem. Tất cả xử lý trên máy; mô hình mới cần Internet để tải lần đầu.</p><p><b>Thiết lập xuất:</b> MP4/MKV/MOV/WebM; H.264/H.265/VP9; CPU hoặc GPU đã vượt kiểm tra thực tế; CRF/VBR/CBR; trộn âm gốc, nền tách, giọng lồng và nhạc.</p><p>Phụ đề và các lớp dùng thời gian/tọa độ của video đầu ra. Dịch/giọng Edge dùng Internet và cần bạn xác nhận gửi nội dung. Những tài khoản, dịch vụ trả phí và API riêng của Video Editor cũ không được chuyển sang ứng dụng này.</p><p>Ctrl+S lưu dự án .mfs cùng thông số lớp, tài nguyên, giọng từng câu và các tệp nguồn.</p>')
    def export(self):
        if not self.clips: self.info('Chưa có video','Thêm video vào Cắt ghép hoặc chọn video trong Studio để xuất hàng loạt.'); return
        if any(c['end']<=c['start'] for c in self.clips): self.info('Khoảng cắt chưa hợp lệ','Kết thúc phải lớn hơn bắt đầu.'); return
        if self.subtitle_dirty and self.srt_table.rowCount() and not self.save_srt(): return
        extension='.'+self.enc_format.currentText(); path=self.output_path('Xuất dự án',extension)
        if not path: return
        clips=copy.deepcopy(self.clips); settings=self.settings(); self.player.pause(); self.output_player.stop()
        def done(result): self.ingest([result]); self.output_player.setSource(QUrl.fromLocalFile(result)); self.output_hint.setText(result); self.preview_tabs.setCurrentIndex(1); self.navigate(2)
        self.start_job('Xuất dự án',lambda p,c:render_project(clips,settings,path,p,c),done)
    def preview_render(self):
        path=self.current_media or (self.clips[self.current_clip]['path'] if 0<=self.current_clip<len(self.clips) else '')
        if not Path(path).is_file(): self.info('Chưa chọn video','Chọn một video trước khi xem thử.'); return
        info=engine.probe(path); folder=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'VideoEditorIndependent'/'previews'; folder.mkdir(parents=True,exist_ok=True)
        output=str(folder/('preview_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.mp4')); settings=self.settings(); settings['encoder'].update(format='mp4',codec='h264',device='cpu')
        self.start_job('Xem thử 5 giây',lambda p,c:render_project([dict(path=path,start=0,end=min(5,info['duration']))],settings,output,p,c),lambda r:(self.output_player.setSource(QUrl.fromLocalFile(r)),self.output_hint.setText('Bản xem thử 5 giây'),self.preview_tabs.setCurrentIndex(1),self.navigate(2),self.output_player.play()))
    def save_project(self):
        if not super().save_project(): return False
        data=json.loads(Path(self.project_path).read_text(encoding='utf-8')); data.update(version=2,video_settings=self.video_settings,assets=self.assets,original_srt=self.original_srt,translated_srt=self.translated_srt,studio_videos=[m['path'] for m in self.library])
        tmp=Path(self.project_path).with_suffix('.mfs.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); os.replace(tmp,self.project_path); return True
    def open_project(self):
        super().open_project()
        if self.project_path:
            try:
                data=json.loads(Path(self.project_path).read_text(encoding='utf-8')); self.apply_advanced(data.get('settings',{})); self.video_settings=data.get('video_settings',{}); self.assets=data.get('assets',{}); self.original_srt=data.get('original_srt',''); self.translated_srt=data.get('translated_srt',''); self.render_library(); self.dirty=False; self.setWindowTitle('VideoEditorIndependent Studio 2.0 · '+Path(self.project_path).stem)
                paths=[p for p in data.get('studio_videos',[]) if Path(p).is_file() and p not in {m['path'] for m in self.library}]
                if paths: self.ingest(paths)
            except Exception as exc: self.info('Không đọc được phần Studio',str(exc))
    def new_project(self):
        previous=self.dirty; super().new_project()
        if not self.dirty:
            self.apply_advanced(DEFAULT_ADVANCED); self.video_settings={}; self.assets={}; self.original_srt=''; self.translated_srt=''; self.layer_selection=None; self.dirty=False; self.setWindowTitle('VideoEditorIndependent Studio 2.0')
