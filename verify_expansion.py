"""Functional expansion QA using real generated media and mocked paid APIs."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
QA=ROOT.parent/"qa"
run=QA/("expansion_"+uuid.uuid4().hex[:8]); run.mkdir()
local=Path(os.environ["LOCALAPPDATA"])
os.environ.update(MEDIAFUSION_LOCAL_TTS_ROOT=str(local/"VideoEditor/tts"),VEI_CUDA_PATH=str(local/"VideoEditor/cuda"),
                  LOCALAPPDATA=str(run/"local"),APPDATA=str(run/"roaming"),QT_QPA_PLATFORM="offscreen",QT_QPA_FONTDIR="C:/Windows/Fonts")
sys.path.insert(0,str(ROOT.parent.parent/"VideoEditorIndependent-1.2.1-gpu-voices/gpu-deps"))
sys.path.insert(0,str(ROOT.parent/'speech-deps'))
import engine, ai_tools, tts_providers, online_speech, local_tts, render_pipeline, subtitle_tools
import numpy as np
report=dict(checks=[],paid_requests=0,run_dir=str(run))
def require(value,message):
    if not value: raise AssertionError(message)
def check(name,function):
    row=dict(name=name); report["checks"].append(row)
    try: row.update(status="passed",details=function() or {})
    except Exception: row.update(status="failed",error=traceback.format_exc())
    print(json.dumps(dict(name=name,status=row["status"])),flush=True)
def command(args):
    result=subprocess.run([engine.tool_path("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin",*args],capture_output=True,timeout=45,creationflags=engine._creation_flags())
    require(result.returncode==0,result.stderr.decode("utf-8","replace")[-2000:]); return result.stdout
fixture=run/"fixture.mp3"
command(["-n","-f","lavfi","-i","sine=frequency=660:duration=0.3","-c:a","libmp3lame",str(fixture)])
audio=fixture.read_bytes()
class Store:
    def get_keys(self,provider): return ["fixture-private-token"]
    def get_options(self,provider): return dict(endpoint="http://localhost:8080",app_id="fixture-app",model="")
class Response:
    status_code=200
    def __init__(self,data): self.data=data if isinstance(data,bytes) else json.dumps(data).encode()
    def iter_content(self,chunk_size=65536): yield self.data
    def close(self): pass
def transports():
    calls=[]
    def request(method,url,provider,cancel,**kwargs):
        calls.append(dict(method=method,url=url,provider=provider,kwargs=kwargs))
        if provider=="minimax": return Response(dict(base_resp=dict(status_code=0),data=dict(audio=audio.hex())))
        if provider=="zalo" and method=="POST": return Response(dict(error_code=0,data=dict(url="https://tts.zdn.vn/generated.mp3")))
        return Response(audio)
    with patch.object(tts_providers,"_request",request):
        for provider,identity in (("minimax","English_expressive_narrator"),("siliconflow","alex"),("vbee","hn_female_ngochuyen_full_48k-fhg"),("zalo","1"),("capcut","voice_fixture")):
            result=tts_providers.synthesize("Xin chào, đây là kiểm tra.",provider+":"+identity,str(run/(provider+".mp3")),store=Store())
            require(engine.probe(result)["has_audio"],provider+" output cannot decode")
    require(len([call for call in calls if call["method"]=="POST"])==5,"Paid generation was retried")
    zalo_get=next(call for call in calls if call["provider"]=="zalo" and call["method"]=="GET")
    require(not zalo_get["kwargs"].get("headers"),"Credential leaked to audio download")
    silicon=next(call for call in calls if call["provider"]=="siliconflow")
    require(silicon["kwargs"]["json"]["voice"]=="FunAudioLLM/CosyVoice2-0.5B:alex","SiliconFlow voice prefix wrong")
    vbee=next(call for call in calls if call["provider"]=="vbee")
    require(vbee["kwargs"]["headers"]["App-Id"]=="fixture-app" and vbee["kwargs"]["json"]["mode"]=="sync","Vbee contract wrong")
    return dict(providers=5,actual_audio_decode=True,generation_posts=5,secret_not_logged=True)
def negatives():
    with patch.object(tts_providers,"_request") as request:
        cancelled=threading.Event(); cancelled.set()
        try: tts_providers.synthesize("test","minimax:voice",str(run/"cancel.mp3"),cancelled,store=Store())
        except ai_tools.CancelledError: pass
        else: raise AssertionError("Cancelled request accepted")
        require(not request.called,"Cancelled speech called network")
        class Empty(Store):
            def get_keys(self,provider): return []
        try: tts_providers.synthesize("test","minimax:voice",str(run/"no-key.mp3"),store=Empty())
        except ai_tools.AIError: pass
        else: raise AssertionError("No-key provider accepted")
        require(not request.called,"No-key speech called network")
    for url in ("file:///C:/data","http://example.com","https://name:password@example.com","https://example.com?secret=x"):
        try: online_speech.bridge_url(url)
        except ValueError: pass
        else: raise AssertionError("Ambiguous bridge accepted")
    return dict(cancel_before_request=True,missing_key_before_request=True,endpoint_validation=True)
def gtts():
    from gtts import gTTS
    def stream(self): yield audio
    with patch.object(gTTS,"stream",stream):
        output=tts_providers.synthesize("Xin chào.","gtts:vi",str(run/"gtts.mp3"))
    require(engine.probe(output)["has_audio"],"gTTS did not route/convert")
    from voice_catalog import normalize_catalog
    require(len(normalize_catalog(online_speech.seed_catalog("gtts")))>30,"gTTS catalog malformed")
    return dict(mocked_google=True,catalog_languages=len(online_speech.seed_catalog("gtts")))
def kokoro():
    require(len(local_tts.catalog("kokoro"))==54,"Installed 54 voice embeddings not listed")
    output=local_tts.synthesize("Hello. This is an offline voice for your video.","kokoro:af_heart",str(run/"kokoro.wav"),device="cuda")
    info=engine.probe(output); runtime=local_tts.last_runtime("kokoro:af_heart")
    require(info["has_audio"] and info["duration"]>.5 and runtime["device"]=="cuda","Real Kokoro CUDA failed")
    return dict(voices=54,actual_cuda=runtime,output=output,duration=info["duration"])
video=run/"source.mp4"
command(["-n","-f","lavfi","-i","color=c=0xC83010:size=320x180:rate=10:duration=2","-c:v","libx264","-pix_fmt","yuv420p",str(video)])
def pixels(path,seconds):
    values=command(["-ss",str(seconds),"-i",str(path),"-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"])
    return np.frombuffer(values,dtype=np.uint8).reshape(180,320,3)
def filters_text():
    from text_effects import EFFECTS,render_text
    hashes=[hashlib.sha256(render_text(dict(text="Video thử",font_size=30,outline=2,text_effect=effect),320).tobytes()).hexdigest() for effect in EFFECTS]
    require(len(set(hashes))==10,"Text effects alias identical raster")
    output=run/"layers.mp4"
    subtitle=run/"sub.srt"; subtitle_tools.save_subtitles([dict(start=.2,end=1.8,text="Video thử")],subtitle)
    settings=dict(width=320,height=180,fps=10,filter_layers=[dict(filter="grayscale",intensity=1,start=.5,end=1.5)],
        text_layers=[dict(text="Chữ",text_effect="glow",font_size=24,marquee="auto",speed=40,start=0,end=2)],
        subtitle_path=str(subtitle),subtitle_style=dict(text_effect="double_border",font="Arial",size=22,bg_fit="hug",alignment=2),
        encoder=dict(format="mp4",codec="h264",device="cpu",quality=18))
    render_pipeline.render_project([dict(path=str(video))],settings,str(output),lambda *_:None,threading.Event())
    before,during,after=[pixels(output,point)[80,160].astype(int) for point in (.1,.9,1.8)]
    require(max(during)-min(during)<8,"Timed grayscale not applied")
    require(before[0]>before[2]+80 and after[0]>after[2]+80,"Timed grayscale leaked outside layer interval")
    return dict(output=str(output),distinct_text_effects=10,timed_filter_pixels=[before.tolist(),during.tolist(),after.tolist()])
def review_ui():
    from PySide6.QtWidgets import QApplication,QDialog
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtCore import Qt,QSettings
    from subtitle_editor import AIFixReviewDialog,apply_review_rows
    app=QApplication.instance() or QApplication([])
    for name in ("segoeui.ttf","segoeuib.ttf"): QFontDatabase.addApplicationFont("C:/Windows/Fonts/"+name)
    QSettings.setDefaultFormat(QSettings.IniFormat); QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(run/"settings"))
    class Editor(QDialog):
        def __init__(self):
            super().__init__(); self.rows=[dict(start=0,end=1,text="Câu sai",voice="kokoro:af_heart"),dict(start=1,end=2,text="Giữ nguyên")]; self.history=[]
        def selected_indices(self): return [0,1]
        def set_rows(self,rows): self.history.append(copy.deepcopy(self.rows)); self.rows=rows
    editor=Editor(); original=copy.deepcopy(editor.rows)
    dialog=AIFixReviewDialog(editor); dialog.run_indices=[0,1]
    dialog.show_draft([dict(text="Câu sửa"),dict(text="Giữ nguyên")])
    require(editor.rows==original,"Draft changed source")
    require(dialog.table.item(1,0).checkState()==Qt.CheckState.Unchecked,"Unchanged row selected")
    dialog.apply(); require(editor.rows[0]["text"]=="Câu sửa" and editor.rows[0]["voice"]==original[0]["voice"],"Apply lost content/voice")
    require(editor.history==[original] and editor.rows[1]==original[1],"Review apply did not preserve undo/unchanged row")
    dialog.deleteLater(); editor.deleteLater(); app.processEvents()
    return dict(review_does_not_mutate=True,changed_only=True,undo_metadata_times=True)
def many_captions():
    from caption_raster import caption_stream
    from PIL import Image
    folder=run/"caption-many"; folder.mkdir()
    source=folder/"many.srt"
    subtitle_tools.save_subtitles([dict(start=i*.06,end=i*.06+.04,text=f"Câu {i}") for i in range(300)],source)
    stream=caption_stream(source,dict(text_effect="glow",font_size=20,size=20,opacity=.5),folder,320,180,18,threading.Event())
    image=Image.open(folder/"caption-0.png")
    require(100<=max(image.getchannel("A").getdata())<=128,"Caption alpha was applied twice")
    require(stream.is_file() and len(list(folder.glob("caption-*.png")))==301,"Timed caption sequence missing")
    output=run/"many-captions.mp4"
    command(["-n","-f","lavfi","-i","color=size=320x180:rate=10:duration=18","-f","concat","-safe","0","-i",str(stream),
             "-filter_complex","[0:v][1:v]overlay=0:0:format=auto:shortest=1","-t","18","-c:v","libx264","-pix_fmt","yuv420p",str(output)])
    require(engine.probe(str(output))["duration"]>17.8,"Caption sequence duration truncated")
    return dict(cues=300,ffmpeg_caption_inputs=1,actual_video=str(output),alpha_preserved=True)
check("Five independent online provider contracts decode real MP3",transports)
check("Missing keys, cancellation and endpoint errors make no requests",negatives)
check("Google TTS catalog and explicit generation routing",gtts)
check("Real installed Kokoro CUDA model and 54 voices",kokoro)
check("Ten text rasters and timed filter plus styled caption real export",filters_text)
check("AI draft review applies picked changes with undo and metadata",review_ui)
check("Three hundred styled cues use one FFmpeg input and preserve alpha",many_captions)
report["passed"]=sum(row["status"]=="passed" for row in report["checks"]); report["failed"]=len(report["checks"])-report["passed"]
report["status"]="passed" if not report["failed"] else "failed"
(QA/"expansion-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(dict(passed=report["passed"],failed=report["failed"])),flush=True)
raise SystemExit(bool(report["failed"]))
