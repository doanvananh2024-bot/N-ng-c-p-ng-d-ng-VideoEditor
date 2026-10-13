"""Independent S0/S1 layer editing, with legacy single-caption compatibility."""
import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QComboBox,QFontComboBox,QDoubleSpinBox,QLineEdit,QPushButton,QColorDialog

class SubtitleDetailMixin:
    def _build_subtitle_details(self,column):
        self._subtitle_tracks_state=None
        self._subtitle_selection=None
        self._subtitle_detail_loading=False
        self.subtitle_detail_frame=QWidget()
        self.subtitle_detail_frame.setObjectName("subtitleLayerDetail")
        layout=QVBoxLayout(self.subtitle_detail_frame)
        layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
        self.subtitle_detail_controls={}
        source=self._audio_section(layout,"Danh sách phụ đề")
        self.subtitle_file_label=QLabel("Chưa có tệp phụ đề")
        self.subtitle_file_label.setWordWrap(True)
        self.subtitle_file_label.setStyleSheet("color:#aaaaaa;font-size:12px;")
        source.addWidget(self.subtitle_file_label)
        button=QPushButton("Mở / chỉnh các câu…")
        button.setObjectName("subtitleDetailOpenEditor")
        button.clicked.connect(self._open_subtitle_layer_editor)
        source.addWidget(button)
        self.subtitle_detail_enabled=QCheckBox("Hiển thị phụ đề")
        self.subtitle_detail_enabled.setObjectName("subtitleDetailEnabled")
        self.subtitle_detail_enabled.toggled.connect(lambda value:self._set_subtitle_enabled(self._subtitle_selection,value) if not self._subtitle_detail_loading else None)
        source.addWidget(self.subtitle_detail_enabled)
        text=self._audio_section(layout,"Kiểu chữ")
        font=QFontComboBox()
        self.subtitle_detail_controls['font']=font
        self._field(text,"Font",font,"subtitleDetailFont")
        effect=QComboBox()
        from text_effects import EFFECTS
        names=("Viền chữ","Viền đậm","Đổ bóng","Phát sáng","Chữ rỗng","Bóng mềm","Lệch retro","Viền kép","Bóng dài","Không")
        for name,value in zip(names,EFFECTS): effect.addItem(name,value)
        self.subtitle_detail_controls['text_effect']=effect
        self._field(text,"Hiệu ứng",effect,"subtitleDetailEffect")
        for key,label,value,low,high,unit in [('size','Cỡ chữ',24,8,140,''),('outline','Độ dày viền',2,0,32,''),('opacity','Độ rõ',1,0,1,'%')]:
            control=QDoubleSpinBox(); control.setRange(low,high); control.setValue(value); control.setDecimals(2)
            self.subtitle_detail_controls[key]=control
            self._audio_slider(text,label,control,"subtitleDetail_"+key,unit=unit)
        for key,label in [('color','Màu chữ'),('outline_color','Màu viền')]:
            control=QLineEdit('#ffffff' if key=='color' else '#000000')
            self.subtitle_detail_controls[key]=control
            self._field(text,label,control,"subtitleDetail_"+key)
            button=QPushButton("Chọn "+label.lower()+"…")
            button.clicked.connect(lambda checked=False,identity=key:self._subtitle_pick_color(identity))
            text.addWidget(button)
        for key,label in [('bold','In đậm'),('uppercase','Viết hoa')]:
            control=QCheckBox(label)
            self.subtitle_detail_controls[key]=control
            text.addWidget(control)
        background=self._audio_section(layout,"Nền phụ đề")
        mode=QComboBox(); mode.addItem("Ôm chữ","hug"); mode.addItem("Toàn khung","box")
        self.subtitle_detail_controls['bg_fit']=mode
        self._field(background,"Bám nền",mode,"subtitleDetailBackgroundFit")
        for key,label,value,high,unit in [('bg_opacity','Độ rõ nền',0,1,'%'),('bg_radius','Bo góc',0,200,'')]:
            control=QDoubleSpinBox(); control.setRange(0,high); control.setValue(value); control.setDecimals(2)
            self.subtitle_detail_controls[key]=control
            self._audio_slider(background,label,control,"subtitleDetail_"+key,unit=unit)
        color=QLineEdit('#000000'); self.subtitle_detail_controls['bg_color']=color
        self._field(background,"Màu nền",color,"subtitleDetail_bg_color")
        position=self._audio_section(layout,"Vị trí phụ đề")
        align=QComboBox()
        for value,label in enumerate(("Dưới trái","Dưới giữa","Dưới phải","Giữa trái","Chính giữa","Giữa phải","Trên trái","Trên giữa","Trên phải"),1): align.addItem(label,value)
        self.subtitle_detail_controls['alignment']=align
        self._field(position,"Điểm bám",align,"subtitleDetailAlignment")
        for key,label in [('x','Ngang (px)'),('y','Dọc (px)')]:
            control=QDoubleSpinBox(); control.setRange(-32768,32768); control.setDecimals(1)
            self.subtitle_detail_controls[key]=control
            self._field(position,label,control,"subtitleDetail_"+key)
        for control in self.subtitle_detail_controls.values():
            signal=control.currentFontChanged if isinstance(control,QFontComboBox) else control.currentIndexChanged if isinstance(control,QComboBox) else control.toggled if isinstance(control,QCheckBox) else control.textChanged if isinstance(control,QLineEdit) else control.valueChanged
            signal.connect(self._subtitle_detail_changed)
        self.subtitle_detail_frame.hide()
        column.addWidget(self.subtitle_detail_frame)

    def _subtitle_paths(self):
        assets=self.assets.get(self.current_media,{}) if self.current_media else {}
        original=assets.get('original_subtitle') or ''
        translated=assets.get('translated_subtitle') or ''
        preferred=self.subtitle.text().strip() if hasattr(self,'subtitle') else ''
        if preferred and not original and not translated:
            original=preferred
        return {'original':str(original),'translated':str(translated)}

    def _subtitle_role_enabled(self,role):
        if self._subtitle_tracks_state is not None:
            return bool(self._subtitle_tracks_state.get(role,{}).get('enabled',False))
        paths=self._subtitle_paths()
        preferred=self.subtitle.text().strip() if hasattr(self,'subtitle') else ''
        if preferred and preferred==paths.get('original'): selected='original'
        elif paths.get('translated'): selected='translated'
        else: selected='original'
        return role==selected

    def _materialize_subtitle_tracks(self):
        if self._subtitle_tracks_state is None:
            paths=self._subtitle_paths()
            self._subtitle_tracks_state={role:dict(role=role,path=paths[role],enabled=self._subtitle_role_enabled(role),style=copy.deepcopy(self.advanced.get('subtitle_style') or {})) for role in ('original','translated')}

    def _set_subtitle_enabled(self,role,enabled):
        if role not in ('original','translated') or self._subtitle_detail_loading:
            return
        self._materialize_subtitle_tracks()
        self._subtitle_tracks_state[role]['enabled']=bool(enabled)
        self.advanced['subtitle_tracks']=copy.deepcopy(list(self._subtitle_tracks_state.values()))
        if self._subtitle_selection==role:
            self.subtitle_detail_enabled.blockSignals(True); self.subtitle_detail_enabled.setChecked(bool(enabled)); self.subtitle_detail_enabled.blockSignals(False)
        self.mark_dirty(); self.refresh_canvas()

    def _with_subtitle_settings(self,settings):
        if not hasattr(self,'_subtitle_tracks_state') or self._subtitle_tracks_state is None:
            settings.pop('subtitle_tracks',None)
            return settings
        paths=self._subtitle_paths()
        tracks=[]
        for role in ('original','translated'):
            track=copy.deepcopy(self._subtitle_tracks_state[role])
            if paths[role]: track['path']=paths[role]
            tracks.append(track)
        settings['subtitle_tracks']=tracks
        return settings

    def _load_subtitle_settings(self,settings):
        merged={**settings,**(settings.get('advanced') or {})}
        if 'subtitle_tracks' in merged:
            self._subtitle_tracks_state={role:dict(role=role,path='',enabled=False,style=copy.deepcopy(self.advanced.get('subtitle_style') or {})) for role in ('original','translated')}
            for track in merged.get('subtitle_tracks') or []:
                if isinstance(track,dict) and track.get('role') in self._subtitle_tracks_state:
                    self._subtitle_tracks_state[track['role']].update(copy.deepcopy(track))
            self.advanced['subtitle_tracks']=copy.deepcopy(list(self._subtitle_tracks_state.values()))
        else:
            self._subtitle_tracks_state=None
            self.advanced.pop('subtitle_tracks',None)
        if self._subtitle_selection:
            self._choose_subtitle_detail(self._subtitle_selection)

    def _choose_subtitle_detail(self,role):
        if role not in ('original','translated'): return
        self._subtitle_selection=role
        self._audio_selection=None
        self.layer_selection=None
        self.generic_detail_frame.hide()
        for frame in self.audio_detail_frames.values(): frame.hide()
        self.subtitle_detail_frame.show()
        self.audio_badge.setText('S0' if role=='original' else 'S1'); self.audio_badge.show()
        self.inline_detail_title.setText('Phụ đề gốc' if role=='original' else 'Phụ đề dịch')
        self.inline_detail_title.setStyleSheet('font-size:15px;font-weight:600;color:#eeeeee;')
        paths=self._subtitle_paths()
        track=(self._subtitle_tracks_state or {}).get(role,{})
        path=paths.get(role) or track.get('path','')
        self.subtitle_file_label.setText(Path(path).name if path else 'Chưa có tệp phụ đề. Trích hoặc dịch trong Pipeline.')
        style={**(self.advanced.get('subtitle_style') or {}),**(track.get('style') or {})}
        self._subtitle_detail_loading=True
        for key,control in self.subtitle_detail_controls.items():
            default={'font':'Arial','text_effect':'outline','bg_fit':'hug','size':24,'outline':2,'opacity':1,'color':'#ffffff','outline_color':'#000000','bg_color':'#000000','alignment':2}.get(key,0)
            value=style.get(key,default)
            if isinstance(control,QFontComboBox): control.setCurrentFont(QFont(str(value)))
            elif isinstance(control,QComboBox): control.setCurrentIndex(max(0,control.findData(value)))
            elif isinstance(control,QCheckBox): control.setChecked(bool(value))
            elif isinstance(control,QLineEdit): control.setText(str(value))
            else: control.setValue(float(value))
        self.subtitle_detail_enabled.setChecked(self._subtitle_role_enabled(role))
        self._subtitle_detail_loading=False
        self.component_tabs.setCurrentIndex(1)

    def _subtitle_detail_changed(self,*_):
        if self._subtitle_detail_loading or self.syncing or self._subtitle_selection not in ('original','translated'): return
        self._materialize_subtitle_tracks()
        style=self._subtitle_tracks_state[self._subtitle_selection]['style']
        for key,control in self.subtitle_detail_controls.items():
            if isinstance(control,QFontComboBox): value=control.currentFont().family()
            elif isinstance(control,QComboBox): value=control.currentData()
            elif isinstance(control,QCheckBox): value=control.isChecked()
            elif isinstance(control,QLineEdit): value=control.text().strip()
            else: value=control.value()
            # Position is inherited until the user specifically edits it.
            if key in ('x','y') and key not in style and control is not self.sender(): continue
            style[key]=value
        self.advanced['subtitle_tracks']=copy.deepcopy(list(self._subtitle_tracks_state.values()))
        self.mark_dirty(); self.refresh_canvas()

    def _subtitle_pick_color(self,key):
        color=QColorDialog.getColor(parent=self,title='Chọn màu phụ đề')
        if color.isValid(): self.subtitle_detail_controls[key].setText(color.name())

    def _open_subtitle_layer_editor(self):
        if not self.current_media: return
        from subtitle_editor import SubtitleEditorDialog
        media=next((value for value in self.library if value['path']==self.current_media),None)
        if media is None: return
        dialog=SubtitleEditorDialog(self,media,self.settings(),self.assets.get(self.current_media,{}))
        if self._subtitle_selection=='translated': dialog.roles.setCurrentIndex(dialog.roles.findData('translated'))
        dialog.saved.connect(lambda role,path:self._attach_saved_subtitle(self.current_media,role,path))
        dialog.exec()
