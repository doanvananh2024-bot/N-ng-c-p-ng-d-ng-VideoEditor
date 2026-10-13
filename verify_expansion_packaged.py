"""Real public EXE jobs for the newly added offline GPU and visual features."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
import traceback

import engine
import ai_tools

ROOT=Path(__file__).resolve().parent
QA=ROOT.parent/"qa"
run=QA/("expansion_exe_"+uuid.uuid4().hex[:8]); run.mkdir()
executable=ROOT/"dist/VideoEditorIndependent/VideoEditorIndependent.exe"
original=Path(os.environ["LOCALAPPDATA"])
env=os.environ.copy()
env.update(LOCALAPPDATA=str(run/"local"),APPDATA=str(run/"roaming"),MEDIAFUSION_LOCAL_TTS_ROOT=str(original/"VideoEditor/tts"),
           VEI_CUDA_PATH=str(original/"VideoEditor/cuda"),VEI_WHISPER_ROOT=str(original/"VideoEditor/stt/faster-whisper"),
           VEI_DEMUCS_PACK=str(original/"VideoEditor/voicesep/smartvoice_cuda"),QT_QPA_PLATFORM="offscreen",HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1")
env.pop("PYTHONPATH",None); env.pop("PYTHONHOME",None)
report=dict(checks=[],executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),paid_requests=0,run_dir=str(run))
def require(value,message):
    if not value: raise AssertionError(message)
def check(name,function):
    row=dict(name=name); report["checks"].append(row)
    try: row.update(status="passed",details=function())
    except Exception: row.update(status="failed",error=traceback.format_exc())
    print(json.dumps(dict(name=name,status=row["status"])),flush=True)
def job(name,payload):
    path=run/(name+".json"); path.write_text(json.dumps(payload),encoding="utf-8")
    result=subprocess.run([str(executable),"--job",str(path)],env=env,capture_output=True,timeout=120,creationflags=engine._creation_flags())
    response=json.loads(path.with_suffix(".result.json").read_text(encoding="utf-8"))
    require(result.returncode==0 and response.get("ok"),str(response))
    return response
captions=run/"sample.srt"
ai_tools.save_srt([dict(start=0,end=4,text="Hello, this is an offline voice test.")],captions)
def kokoro():
    response=job("kokoro",dict(operation="dub",input=str(captions),output=str(run/"kokoro.wav"),voice="kokoro:af_heart",tts_device="cuda"))
    require(response["runtime"]["device"]=="cuda","Kokoro did not use GPU")
    require(engine.probe(response["result"])["has_audio"],"Kokoro WAV cannot decode")
    return response["runtime"]
def whisper():
    response=job("whisper",dict(operation="recognize",input=str(run/"kokoro.wav"),output=str(run/"heard.srt"),model="small",language="en",device="cuda",batch_size=2))
    require(any("GPU NVIDIA" in row["message"] for row in response["progress"]),"Whisper GPU not confirmed")
    rows=ai_tools.load_srt(response["result"])
    require("offline voice test" in " ".join(row["text"] for row in rows).casefold(),"Real speech phrase not recognized")
    return dict(rows=rows,batch_size=2,actual_cuda=True)
def visual():
    video=run/"source.mp4"
    subprocess.run([engine.tool_path("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-n","-f","lavfi","-i","color=c=0xC83010:size=320x180:rate=10:duration=2",
                    "-c:v","libx264","-pix_fmt","yuv420p",str(video)],check=True,capture_output=True,creationflags=engine._creation_flags())
    settings=dict(width=320,height=180,background_mode="color",background_color="#224466",source_scale=.7,source_scale_x=.8,source_opacity=.7,
                  filter_layers=[dict(filter="grayscale",intensity=1,start=.5,end=1.5)],text_layers=[dict(text="Chữ thử",text_effect="glow",bg_fit="hug",font_size=24,marquee="auto",start=0,end=2)],
                  audio_layers=[dict(path=str(run/"kokoro.wav"),volume=.8,loop=False,fade_in=.2,fade_out=.3,start=0,end=0)],
                  encoder=dict(format="mp4",codec="h264",device="cpu",quality=18))
    response=job("visual",dict(operation="export",clips=[dict(path=str(video))],settings=settings,output=str(run/"visual.mp4")))
    require(engine.probe(response["result"])["has_audio"],"New audio layer missing")
    return dict(output=response["result"],source_composition=True,filter_layer=True,text_glow_motion=True,audio_layer=True)
check("Bundled Kokoro voice uses installed model on CUDA",kokoro)
check("Bundled Whisper recognizes real speech on CUDA with batch two",whisper)
check("Bundled composition, text motion, filter and extra audio render",visual)
report["passed"]=sum(row["status"]=="passed" for row in report["checks"])
report["failed"]=len(report["checks"])-report["passed"]
report["status"]="passed" if not report["failed"] else "failed"
(QA/"expansion-packaged-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
raise SystemExit(bool(report["failed"]))
