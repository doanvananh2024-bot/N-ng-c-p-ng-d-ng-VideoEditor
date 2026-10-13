"""Offline 1.2 integration checks with real FFmpeg output and mocked TTS HTTP.

Only provider transport is mocked. Timed per-cue synthesis, conversion, mixing,
publication, preview painting and per-video GUI state use the actual app code.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
import wave
from unittest.mock import patch


ROOT=Path(__file__).resolve().parent


def require(condition,message):
    if not condition:
        raise AssertionError(message)


def main():
    run=ROOT/"qa"/("advanced_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6])
    run.mkdir(parents=True,exist_ok=False)
    os.environ.update(LOCALAPPDATA=str(run/"local-data"),APPDATA=str(run/"roaming-data"),
                      HF_HOME=str(run/"hf-cache"),TORCH_HOME=str(run/"torch-cache"),
                      HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",QT_QPA_PLATFORM="offscreen")
    from PIL import Image,ImageDraw
    import numpy as np
    from PySide6.QtCore import QCoreApplication,QEvent,QPointF,QSettings,Qt
    from PySide6.QtGui import QFont,QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QComboBox,QCheckBox,QSpinBox,QDoubleSpinBox,QLineEdit
    import ai_tools
    import engine
    import render_pipeline
    import subtitle_tools
    from preview_canvas import EditablePreview
    from desktop import IndependentWindow

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(run/"qt-settings"))
    app=QApplication.instance() or QApplication([])
    app.setApplicationName("VideoEditorIndependentAdvancedQA")
    app.setOrganizationName("VideoEditorIndependentAdvancedQA")
    app.setStyle("Fusion")
    for name in ("segoeui.ttf","segoeuib.ttf","segoeuii.ttf","seguisb.ttf","seguisym.ttf","seguiemj.ttf"):
        path=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/name
        if path.is_file():QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI",10))
    report=dict(phase="advanced-1.2",created_at=datetime.now(timezone.utc).isoformat(),run_dir=str(run),offline=True,
                paid_provider_requests=0,checks=[],artifacts={})
    errors=[]
    previous_hook=sys.excepthook
    sys.excepthook=lambda kind,value,trace:errors.append("".join(traceback.format_exception(kind,value,trace)))
    source=run/"Nguồn nâng cấp có dấu.mp4"
    second=run/"Nguồn riêng thứ hai.mp4"
    logo=run/"Logo bất đối xứng.png"
    poster=run/"Khung nguồn.png"
    synthetic=run/"Synthetic TTS fixture.mp3"
    window=None
    preview=None

    def persist():
        report["passed"]=sum(row["status"]=="passed" for row in report["checks"])
        report["failed"]=sum(row["status"]=="failed" for row in report["checks"])
        report["status"]="failed" if report["failed"] else "passed"
        value=json.dumps(report,ensure_ascii=False,indent=2)
        (ROOT/"qa"/"advanced-upgrade-verification-report.json").write_text(value,encoding="utf-8")
        (run/"advanced-upgrade-verification-report.json").write_text(value,encoding="utf-8")

    def settle():
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        app.processEvents()
        QTest.qWait(15)

    def wait_for(predicate,title,timeout=30):
        end=time.monotonic()+timeout
        while not predicate():
            require(time.monotonic()<end,"Timed out: "+title)
            app.processEvents();QTest.qWait(15)
        settle()

    def check(name,fn):
        began=time.monotonic();row=dict(name=name);report["checks"].append(row)
        try:
            details=fn()
            require(not errors,"Qt slot exceptions: "+"\n".join(errors))
            row.update(status="passed",details=details or {})
        except Exception as exc:row.update(status="failed",error=str(exc),traceback=traceback.format_exc())
        row["seconds"]=round(time.monotonic()-began,3)
        print(json.dumps({key:row[key] for key in ("name","status","seconds")},ensure_ascii=False),flush=True);persist()

    def ffmpeg(args):
        result=subprocess.run([engine.tool_path("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-n",*args],
                              stdin=subprocess.DEVNULL,capture_output=True,timeout=60,creationflags=engine._creation_flags())
        require(result.returncode==0,result.stderr.decode("utf-8","replace")[-1500:]);return result

    def frame(path,seconds):
        result=subprocess.run([engine.tool_path("ffmpeg"),"-v","error","-ss",str(seconds),"-i",str(path),
                              "-frames:v","1","-f","image2pipe","-vcodec","png","pipe:1"],
                             stdin=subprocess.DEVNULL,capture_output=True,timeout=30,creationflags=engine._creation_flags())
        require(result.returncode==0,result.stderr.decode("utf-8","replace")[-1000:])
        return Image.open(BytesIO(result.stdout)).convert("RGB")

    def fixture():
        ffmpeg(["-f","lavfi","-i","color=c=0x101010:size=320x180:rate=15:duration=3",
                "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(source)])
        os.link(source,second)
        image=Image.new("RGBA",(60,30),(255,0,0,255))
        ImageDraw.Draw(image).rectangle((30,0,59,29),fill=(0,255,0,255));image.save(logo)
        Image.new("RGB",(320,180),(16,16,16)).save(poster)
        ffmpeg(["-f","lavfi","-i","sine=frequency=440:sample_rate=24000:duration=0.3","-c:a","libmp3lame",str(synthetic)])
        report["artifacts"].update(source=str(source),asymmetric_logo=str(logo),synthetic_tts=str(synthetic))
        return dict(source=engine.probe(str(source)),synthetic_audio=True)

    def transform_render_preview():
        nonlocal preview
        settings=dict(width=320,height=180,fps=15,
                      encoder=dict(format="mp4",codec="h264",device="cpu",preset="fast",quality=18),
                      image_layers=[dict(path=str(logo),width=60,height=30,x=10,y=10,anchor="bottom-right",rotation=90,flip_horizontal=True,
                                         enabled=True,start=1,end=2)],
                      text_layers=[dict(text="CHÀO",font_size=20,x=10,y=10,color="#FFFFFF",outline_color="#FF0000",outline=4,rotation=90,
                                        enabled=True,start=1,end=2),
                                   dict(text="DISABLED",font_size=26,x=10,y=120,color="#FF00FF",enabled=False)],
                      blur_zones=[])
        target=run/"Lớp xoay và lật thật.mp4"
        render_pipeline.render_project([dict(path=str(source))],settings,str(target),lambda p,m:None,threading.Event())
        before,active,after=(frame(target,t) for t in (.5,1.5,2.5))
        # Flip horizontally first, then rotate clockwise: green top, red bottom.
        top=active.getpixel((295,120));bottom=active.getpixel((295,155))
        require(top[1]>180 and top[0]<80 and bottom[0]>180 and bottom[1]<80,
                f"Image rotate/flip/anchor not rendered as contracted: top={top},bottom={bottom}")
        require(max(before.getpixel((295,120)))<60 and max(after.getpixel((295,120)))<60,"Timed overlay is visible outside its interval")
        pixels=np.asarray(active)
        require(np.count_nonzero((pixels[:100,:100,0]>180)&(pixels[:100,:100,1]>180)&(pixels[:100,:100,2]>180))>20,"Rotated text foreground missing")
        require(np.count_nonzero((pixels[:100,:100,0]>150)&(pixels[:100,:100,1]<80))>10,"Thick text outline preset did not produce red outline")
        require(max(active.getpixel((20,145)))<60,"Disabled text layer rendered unexpectedly")
        preview=EditablePreview(engine.probe(str(source)),str(poster),settings,{})
        preview.resize(640,360);preview.show();preview.set_position(1.5);settle()
        captured=run/"advanced-preview.png";preview.grab().save(str(captured))
        view=Image.open(captured).convert("RGB")
        for point,expected in (((295,120),"green"),((295,155),"red")):
            p=preview.world_to_view(QPointF(*point));color=view.getpixel((round(p.x()),round(p.y())))
            require((color[1]>180 and color[0]<80) if expected=="green" else (color[0]>180 and color[1]<80),
                    f"Visible preview does not agree with real transformed output at {point}: {color}")
        rect=preview.layer_rect("image_layers",0)
        require(abs(rect.width()/rect.height()-.5)<.03,"Rotated preview layer bounds do not match expanded overlay")
        active_path=run/"advanced-transform-frame.png";active.save(active_path)
        report["artifacts"].update(transformed_export=str(target),transform_frame=str(active_path),advanced_preview=str(captured))
        return dict(top_pixel=list(top),bottom_pixel=list(bottom),timing_and_disable=True,preview_export_agreement=True)

    def text_styles():
        from advanced_render import _text_image
        baseline=dict(text="Tiếng Việt",font_size=24,font="Segoe UI",color="#FFFFFF",outline_color="#FF0000",outline=0,bg_color="#000000")
        styles={"plain":{},"bold":dict(bold=True),"uppercase":dict(uppercase=True),"red-outline":dict(outline=4),
                "rounded-background":dict(bg_opacity=.65,bg_color="#0000FF",bg_radius=12)}
        distinct=set()
        for effect,fields in styles.items():
            image=_text_image(dict(baseline,**fields),320)
            require(image.mode=="RGBA" and image.getbbox(),"Text styling returned an empty image: "+effect)
            distinct.add(hashlib.sha256(image.tobytes()).hexdigest())
            image.save(run/("text-style-"+effect+".png"))
            if effect=="rounded-background":
                corner=image.getpixel((0,0));top=image.getpixel((image.width//2,1))
                require(corner[3]==0 and top[2]>240 and abs(top[3]-round(.65*255))<=1,
                        "Rounded text background did not preserve selected color/opacity/corner")
        require(len(distinct)==len(styles),"Explicit typography controls did not produce distinct images")
        return dict(styles=list(styles),distinct_images=len(distinct),unicode_text=True)

    def tts_routing():
        import requests
        import edge_tts
        import tts_providers
        store=tts_providers.TTSKeyStore()
        store.save_keys("fpt",["qa-fpt-placeholder"]);store.save_keys("elevenlabs",["qa-eleven-placeholder"])
        mp3=synthetic.read_bytes();http_calls=[];edge_calls=[]
        def response(content,content_type="audio/mpeg"):
            value=requests.Response();value.status_code=200;value._content=content;value._content_consumed=True
            value.headers["Content-Type"]=content_type;return value
        def fake_http(method,url,**kwargs):
            http_calls.append(dict(method=method,url=url))
            if url.startswith("https://api.fpt.ai/hmi/tts/v5"):
                require(kwargs.get("headers",{}).get("voice")=="banmai","FPT per-cue voice was not routed")
                return response(json.dumps(dict(error=0,**{"async":"https://audio.fpt.ai/qa.mp3"})).encode(),"application/json")
            if url=="https://audio.fpt.ai/qa.mp3":
                return response(mp3)
            if "/v1/text-to-speech/voice_qa" in url:
                return response(mp3)
            raise AssertionError("Unexpected mocked transport URL: "+url)
        class FakeEdgeCommunicate:
            def __init__(self,text,voice,**kwargs):edge_calls.append(dict(text=text,voice=voice))
            async def save(self,path):Path(path).write_bytes(mp3)
        rows=[dict(start=.1,end=.6,text="Xin chào",voice="vi-VN-HoaiMyNeural"),
              dict(start=.8,end=1.4,text="Câu FPT",voice="fpt:banmai"),
              dict(start=1.6,end=2.4,text="Câu ElevenLabs",voice="elevenlabs:voice_qa")]
        untouched=copy.deepcopy(rows)
        target=run/"Giọng từng câu thực.wav"
        with patch.object(requests,"request",fake_http),patch.object(edge_tts,"Communicate",FakeEdgeCommunicate):
            subtitle_tools.dub_rows(rows,str(target),progress=lambda p,m:None,cancel=threading.Event())
            configured=run/"Giọng cấu hình thực.wav"
            subtitle_tools.dub_rows(rows,str(configured),progress=lambda p,m:None,cancel=threading.Event(),voice_speed=1.1,
                                    voice_delay=100,pronunciation={"Xin":"Chào"})
        require(rows==untouched,"Per-cue TTS mutated displayed subtitle rows")
        require(len(edge_calls)==2 and any(item["text"]=="Chào chào" for item in edge_calls),"Configured path bypassed Edge routing/pronunciation")
        require(sum("api.fpt.ai" in row["url"] for row in http_calls)==2 and sum("text-to-speech/voice_qa" in row["url"] for row in http_calls)==2,
                "Fast/configured per-cue paths did not both route paid voice IDs")
        with wave.open(str(target),"rb") as reader:
            require((reader.getnchannels(),reader.getsampwidth(),reader.getframerate())==(1,2,24000),"Mixed TTS WAV has incorrect format")
            samples=np.frombuffer(reader.readframes(reader.getnframes()),dtype="<i2")
            duration=len(samples)/24000
        def rms(start,end):return float(np.sqrt(np.mean(samples[round(start*24000):round(end*24000)].astype(float)**2)))
        require(abs(duration-2.4)<.05 and rms(.15,.3)>100 and rms(.65,.75)<5 and rms(.9,1.05)>100 and rms(1.75,1.9)>100,
                "Real per-cue mix lost audio or timed silence")
        report["artifacts"].update(per_cue_audio=str(target),configured_audio=str(configured))
        return dict(duration=duration,adapter_calls=http_calls,edge_calls=edge_calls,per_cue_ids=True,configured_path=True,no_paid_calls=True)

    def tts_cancel_and_protection():
        import requests
        import tts_providers
        store=tts_providers.TTSKeyStore();store.save_keys("fpt",["qa-fpt-placeholder"])
        cancel=threading.Event();calls=[]
        def cancelling_http(method,url,**kwargs):
            calls.append(url);cancel.set()
            value=requests.Response();value.status_code=200;value._content=json.dumps(dict(error=0,**{"async":"https://audio.fpt.ai/qa-cancel.mp3"})).encode()
            value._content_consumed=True;value.headers["Content-Type"]="application/json";return value
        target=run/"Không xuất sau hủy.wav"
        cancelled=False
        with patch.object(requests,"request",cancelling_http):
            try:subtitle_tools.dub_rows([dict(start=0,end=1,text="Huỷ giữa yêu cầu",voice="fpt:banmai")],str(target),cancel=cancel)
            except ai_tools.CancelledError:cancelled=True
        require(cancelled and not target.exists(),"Cancelled paid-voice routing published an output")
        existing=run/"Giọng từng câu thực.wav";before=hashlib.sha256(existing.read_bytes()).hexdigest();blocked=False
        with patch.object(requests,"request",side_effect=AssertionError("Existing-output check must run before transport")):
            try:subtitle_tools.dub_rows([dict(start=0,end=1,text="Không ghi đè",voice="fpt:banmai")],str(existing))
            except FileExistsError:blocked=True
        require(blocked and hashlib.sha256(existing.read_bytes()).hexdigest()==before,"TTS overwrote an existing output")
        return dict(cancelled_without_output=True,existing_output_protected=True,mocked_http_requests=len(calls))

    def advanced_gui_controls():
        nonlocal window
        window=IndependentWindow(testing=True)
        notifications=[];window.info=lambda title,text:notifications.append(dict(title=title,text=text))
        window.resize(1600,920);window.show();window.ingest([str(source),str(second)])
        wait_for(lambda:len(window.library)==2 and not window.workers,"advanced GUI imports")
        window._activate_media(str(source))
        window.add_layer("image_layers",dict(path=str(logo),x=10,y=10,width=60,height=30,anchor="top-left",enabled=True,start=1,end=2,
                                             rotation=0,flip_horizontal=False,flip_vertical=False,loop=True,opacity=1))
        window.choose_layer("image_layers",0)
        rotate=window.findChild(QDoubleSpinBox,"layerRotation")
        flip=window.findChild(QCheckBox,"layerFlipHorizontal")
        enabled=window.findChild(QCheckBox,"layerEnabled")
        require(rotate is not None and flip is not None and enabled is not None,"Layer transform/enable controls missing")
        rotate.setValue(90);flip.setChecked(True)
        window.anchor_buttons["bottom-right"].click()
        enabled.setChecked(False);settle()
        require(window.settings()["image_layers"][0]["enabled"] is False,"Layer enable switch does not persist")
        enabled.setChecked(True);settle()
        first=copy.deepcopy(window.settings()["image_layers"][0])
        require(first["rotation"]==90 and first["flip_horizontal"] and first["anchor"]=="bottom-right", "Image transform/anchor controls failed to persist")
        window.duplicate_layer()
        require(len(window.advanced["image_layers"])==2,"Duplicate layer did not create a copy")
        window.layer_fields["x"].setValue(25)
        require(window.advanced["image_layers"][0]["x"]==10 and window.advanced["image_layers"][1]["x"]==25,
                "Editing duplicated layer also changed its original")
        window.delete_layer()
        require(len(window.advanced["image_layers"])==1,"Deleting duplicate removed wrong number of layers")
        window.add_layer("blur_zones",dict(x=120,y=80,width=70,height=50,strength=8,shape="rect",corner=0,feather=0,enabled=True,start=.8,end=2.2))
        window.choose_layer("blur_zones",0)
        corner=window.findChild(QDoubleSpinBox,"layerBlurCorner")
        require(corner is not None,"Blur corner control missing")
        corner.setValue(14);settle()
        require(window.settings()["blur_zones"][0]["corner"]==14,"Blur corner does not persist into renderer state")
        saved=copy.deepcopy(window.settings())
        window._activate_media(str(second))
        require(not window.settings().get("image_layers"),"Image settings leaked into another video")
        window._activate_media(str(source))
        require(window.settings()["image_layers"]==saved["image_layers"] and window.settings()["blur_zones"]==saved["blur_zones"],
                "Advanced layer fields were lost after switching video focus")
        window.enc_device.setCurrentText("cpu");window.enc_codec.setCurrentText("h264");window.enc_format.setCurrentText("mp4")
        window.enc_mode.setCurrentText("cbr");window.enc_bitrate.setText("400k");window.enc_maxrate.setText("400k");window.enc_preset.setCurrentText("fast")
        require(hasattr(window,"enc_bframes"),"B-frame encoder control missing")
        window.enc_bframes.setChecked(False)
        encoder=copy.deepcopy(window.settings()["encoder"])
        require(encoder["bitrate_mode"]=="cbr" and encoder["bitrate"]=="400k" and encoder["bframes"] is False,
                "Advanced codec controls are not passed to actual export settings")
        for control in (window.pipe_recognize,window.pipe_translate,window.pipe_dub,window.pipe_separate,window.pipe_bilingual):control.setChecked(False)
        window.pipe_export.setChecked(True);window.pipe_folder.setText(str(run/"gui-advanced-export"))
        window._select_path(str(source),True);window.run_pipeline("export")
        wait_for(lambda:window.active_job is None and not window.workers,"advanced GUI export completes",timeout=45)
        require(not notifications,"GUI export reported errors: "+str(notifications))
        output=window.assets.get(str(source),{}).get("video")
        require(output and Path(output).is_file(),"Advanced GUI export did not attach video output")
        require(window.main_views.currentIndex()==0,"Completing export should keep the card workspace visible")
        stream_result=subprocess.run([engine.tool_path("ffprobe"),"-v","error","-show_streams","-of","json",output],
                                     stdin=subprocess.DEVNULL,capture_output=True,timeout=30,creationflags=engine._creation_flags())
        stream=next(row for row in json.loads(stream_result.stdout)["streams"] if row["codec_type"]=="video")
        require(stream["codec_name"]=="h264" and stream["has_b_frames"]==0,"Real encoder ignored the disabled B-frame control")
        image=frame(output,1.5);top=image.getpixel((295,120));bottom=image.getpixel((295,155))
        require(top[1]>180 and bottom[0]>180,"GUI's anchored/rotated image controls disagree with exported placement")
        window.navigate(6);window.right_tabs.setCurrentIndex(1);window.choose_layer("image_layers",0)
        window.component_tabs.setCurrentIndex(1)
        next(card for card in window.cards if card.path==str(source))._position(1500)
        settle();screenshot=run/"advanced-gui-controls.png";window.grab().save(str(screenshot))
        report["artifacts"].update(advanced_gui_export=output,advanced_gui_screenshot=str(screenshot))
        return dict(per_video_persistence=True,duplicate_independent=True,blur_corner=14,encoder=encoder,
                    exported_codec=stream["codec_name"],exported_bframes=stream["has_b_frames"],output=output)

    try:
        check("Generated Unicode media and asymmetric logo/audio fixtures",fixture)
        check("Real rotated/flipped anchored layers, timing, disable and visible preview agreement",transform_render_preview)
        check("Explicit text typography/outline/background controls render distinct Unicode images",text_styles)
        check("Actual mixed per-cue TTS routes Edge/FPT/ElevenLabs via mocked HTTP and real audio",tts_routing)
        check("Per-cue provider cancellation and existing-output protection",tts_cancel_and_protection)
        check("Advanced GUI fields persist per video, duplicate independently and drive real encoder output",advanced_gui_controls)
    finally:
        if preview is not None:preview.close()
        if window is not None:
            wait_for(lambda:not window.workers,"window workers settle")
            window.dirty=False;window.close()
        settle();sys.excepthook=previous_hook;report["qt_exceptions"]=errors;persist()
    print(json.dumps(dict(report=str(ROOT/"qa"/"advanced-upgrade-verification-report.json"),passed=report["passed"],failed=report["failed"]),ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__=="__main__":raise SystemExit(main())
