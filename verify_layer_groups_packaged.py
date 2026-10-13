"""Verify actual EXE S0/S1 rendering and the bundled reference font."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import traceback
import uuid
from io import BytesIO

import numpy as np
from PIL import Image
import engine
import ai_tools

ROOT=Path(__file__).resolve().parent
QA=ROOT.parent/'qa'; run=QA/('groups_exe_'+uuid.uuid4().hex[:8]); run.mkdir()
exe=ROOT/'dist/VideoEditorIndependent/VideoEditorIndependent.exe'
env=os.environ.copy(); env.update(LOCALAPPDATA=str(run/'local'),APPDATA=str(run/'roaming'),QT_QPA_PLATFORM='offscreen',HF_HUB_OFFLINE='1')
env.pop('PYTHONPATH',None); env.pop('PYTHONHOME',None)
report=dict(checks=[],run_dir=str(run),executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
def require(condition,message):
    if not condition: raise AssertionError(message)
def command(args):
    result=subprocess.run([engine.tool_path('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',*args],capture_output=True,timeout=40,creationflags=engine._creation_flags())
    require(result.returncode==0,result.stderr.decode('utf-8','replace')[-1400:]); return result.stdout
def check(name,fn):
    row=dict(name=name); report['checks'].append(row)
    try: row.update(status='passed',details=fn())
    except Exception: row.update(status='failed',error=traceback.format_exc())
    print(json.dumps(dict(name=name,status=row['status'])),flush=True)
video=run/'source.mp4'
command(['-n','-f','lavfi','-i','color=c=0x202020:size=320x180:rate=12:duration=2','-c:v','libx264','-pix_fmt','yuv420p',str(video)])
original=run/'original.srt'; translated=run/'translated.srt'
ai_tools.save_srt([dict(start=.2,end=1.2,text='ORIGINAL ALPHA')],original)
ai_tools.save_srt([dict(start=.5,end=1.5,text='TRANSLATED BETA')],translated)
def exported(s0,s1):
    target=run/f'roles-{s0}-{s1}.mp4'; job=target.with_suffix('.json')
    tracks=[dict(role='original',path=str(original),enabled=s0,style=dict(font='VE UI',size=22,bold=True,color='#ffff00',outline=0,alignment=8,text_effect='none')),
            dict(role='translated',path=str(translated),enabled=s1,style=dict(font='VE UI',size=22,bold=True,color='#00ffff',outline=0,alignment=2,text_effect='none'))]
    settings=dict(width=320,height=180,fps=12,subtitle_tracks=tracks,subtitle_path=str(original),subtitle_style=dict(color='#ff00ff'),encoder=dict(format='mp4',codec='h264',device='cpu',quality=16))
    job.write_text(json.dumps(dict(operation='export',clips=[dict(path=str(video))],settings=settings,output=str(target))),encoding='utf-8')
    result=subprocess.run([str(exe),'--job',str(job)],env=env,capture_output=True,timeout=90,creationflags=engine._creation_flags())
    payload=json.loads(job.with_suffix('.result.json').read_text(encoding='utf-8'))
    require(result.returncode==0 and payload.get('ok'),str(payload))
    frame=np.asarray(Image.open(BytesIO(command(['-ss','0.75','-i',str(target),'-frames:v','1','-f','image2pipe','-c:v','png','pipe:1']))).convert('RGB'))
    r,g,b=[frame[...,index].astype(int) for index in range(3)]
    counts=dict(s0=int(((r>140)&(g>140)&(b<100))[:90].sum()),s1=int(((g>140)&(b>140)&(r<100))[90:].sum()),legacy=int(((r>140)&(b>140)&(g<100)).sum()))
    require((counts['s0']>100,counts['s1']>100)==(s0,s1),'Subtitle flags lost: '+str(counts))
    require(counts['legacy']<30,'Single caption fallback duplicated tracks')
    return dict(output=str(target),pixels=counts)
def fonts():
    for name in ('VEUI-Regular.ttf','VEUI-Bold.ttf','VEUI-Italic.ttf','OFL.txt'):
        source=ROOT/'ui_fonts'/name; packaged=exe.parent/'_internal/ui_fonts'/name
        require(packaged.is_file() and hashlib.sha256(packaged.read_bytes()).digest()==hashlib.sha256(source.read_bytes()).digest(),'Font/license copy differs: '+name)
    return dict(fonts=3,license_included=True,byte_identical=True)
check('Frozen S0 and S1 both render with the bundled font',lambda:exported(True,True))
check('Frozen S0 switch disables only original captions',lambda:exported(False,True))
check('Frozen S1 switch disables only translated captions',lambda:exported(True,False))
check('Frozen both off suppresses legacy single-caption fallback',lambda:exported(False,False))
check('Reference OFL font files and license are preserved in package',fonts)
report['passed']=sum(row['status']=='passed' for row in report['checks']); report['failed']=len(report['checks'])-report['passed']; report['status']='passed' if not report['failed'] else 'failed'
(QA/'layer-groups-packaged-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
raise SystemExit(bool(report['failed']))
