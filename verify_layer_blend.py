"""Actual FFmpeg/Qt pixel checks for alpha-aware visual layer blends."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid


ROOT = Path(__file__).resolve().parent


def main():
    run = ROOT / 'qa' / ('blend_' + uuid.uuid4().hex[:8])
    run.mkdir()
    os.environ.update(QT_QPA_PLATFORM='offscreen', LOCALAPPDATA=str(run/'local'), APPDATA=str(run/'roaming'))
    from PIL import Image
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    import engine
    import layer_blend
    app = QApplication.instance() or QApplication([])
    base = run/'Source.png'
    overlay = run/'Alpha.png'
    Image.new('RGB',(160,100),(44,83,142)).save(base)
    image = Image.new('RGBA',(40,30),(180,92,38,128))
    image.putpixel((1,1),(255,0,255,0))
    image.save(overlay)
    report = dict(status='passed', passed=0, failed=0, checks=[], created_at=datetime.now(timezone.utc).isoformat(),
                  run_dir=str(run), offline=True)
    for mode in layer_blend.MODES:
        expected = QImage(160,100,QImage.Format.Format_ARGB32_Premultiplied)
        expected.fill(QColor(44,83,142))
        painter = QPainter(expected)
        painter.setCompositionMode(layer_blend.qt_composition_mode(mode))
        painter.setOpacity(.6)
        painter.drawImage(QRectF(20,30,40,30),QImage(str(overlay)))
        painter.end()
        graph = ['[1:v:0]format=rgba,colorchannelmixer=aa=0.6[top]']
        output = run/(mode+'.mp4')
        layer_blend.append_blend_overlay(graph,'0:v:0','top',width=160,height=100,fps=10,duration=1,
                                        x='20',y='30',enable='gte(t,0.2)*lt(t,0.8)',eof_action='repeat',
                                        mode=mode,output='video',prefix='layer1')
        args = ['-framerate','10','-loop','1','-i',str(base),'-framerate','10','-loop','1','-i',str(overlay),'-filter_complex',';'.join(graph),
                '-map','[video]','-t','1','-r','10','-c:v','libx264','-preset','fast','-crf','12',
                '-pix_fmt','yuv420p',str(output)]
        row = dict(name='Alpha blend '+mode)
        report['checks'].append(row)
        try:
            engine._ffmpeg(args,1,lambda *args:None,threading.Event(),'Blend regression')
            pixels = []
            for time in ('0.1','0.5','0.9'):
                frame = run/(mode+'-'+time+'.png')
                engine._ffmpeg(['-ss',time,'-i',str(output),'-frames:v','1',str(frame)],1,
                               lambda *args:None,threading.Event(),'Extract frame')
                with Image.open(frame) as picture:
                    rgb = picture.convert('RGB')
                    inside = rgb.getpixel((40,45))
                    outside = rgb.getpixel((110,75))
                wanted = expected.pixelColor(40,45).getRgb()[:3] if time=='0.5' else (44,83,142)
                assert max(abs(a-b) for a,b in zip(inside,wanted))<=8, (mode,time,inside,wanted)
                assert max(abs(a-b) for a,b in zip(outside,(44,83,142)))<=5, ('outside altered',mode,outside)
                pixels.append(dict(time=time,actual=inside,expected=wanted))
            row.update(status='passed',details=pixels)
            report['passed']+=1
        except Exception as exc:
            row.update(status='failed',error=str(exc));report['failed']+=1;report['status']='failed'
        print(json.dumps(row),flush=True)
    try:
        layer_blend.mode_id('unsupported')
        raise AssertionError('Invalid blend accepted')
    except ValueError:
        report['checks'].append(dict(name='Invalid blend is rejected',status='passed'));report['passed']+=1
    (ROOT/'qa'/'layer-blend-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 1 if report['failed'] else 0


if __name__=='__main__':
    raise SystemExit(main())
