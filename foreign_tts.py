"""Offline Kokoro tensor adapter using the user's installed model and voices.

The supplied IPA vocabulary is read from its config; Piper's eSpeak frontend
is already bundled. No model or service is contacted during catalog loading.
"""
import json
from pathlib import Path
import re
import tempfile
import threading
import wave

import ai_tools

LANGUAGES={"a":("en-US","en-us"),"b":("en-GB","en-gb"),"e":("es-ES","es"),"f":("fr-FR","fr"),
           "h":("hi-IN","hi"),"i":("it-IT","it"),"j":("ja-JP","ja"),"p":("pt-BR","pt-br"),"z":("zh-CN","cmn")}

def files():
    import local_tts
    folder=local_tts.model_root()/"kokoro"
    return dict(model=folder/"kokoro-v1.0.onnx",voices=folder/"voices-v1.0.bin",config=folder/"config.json")

def availability():
    try:
        paths=files()
        if not all(path.is_file() and path.stat().st_size>100 for path in paths.values()):
            raise ValueError("Chưa có model Kokoro và bộ giọng trong thư mục tts/kokoro.")
        return dict(ready=True,hint="Kokoro ngoại tuyến · dùng model và giọng đã cài.",model=str(paths["model"]))
    except (OSError,ValueError) as error:
        return dict(ready=False,hint=str(error),model="")

def catalog():
    if not availability()["ready"]: return []
    import numpy as np
    result=[]
    with np.load(files()["voices"],allow_pickle=False) as voices:
        for name in voices.files:
            if re.fullmatch(r"[abefhijpz][fm]_[a-z0-9_]{1,80}",name):
                result.append(dict(ShortName="kokoro:"+name,FriendlyName=name[3:].replace("_"," ").title(),
                    Locale=LANGUAGES[name[0]][0],Gender="Female" if name[1]=="f" else "Male",Provider="kokoro",Installed=True,
                    VoiceTag=dict(ContentCategories=["Offline"],VoicePersonalities=[])))
    return result

def synthesize(text,voice,output,cancel,progress,device):
    import local_tts
    import numpy as np
    import onnxruntime as ort
    provider,identity=local_tts.split_voice_id(voice)
    paths=files()
    if not availability()["ready"]: raise ai_tools.AIError(availability()["hint"])
    target=ai_tools._output_target(output)
    if target.suffix.lower() not in (".wav",".mp3"): raise ValueError("Chọn WAV hoặc MP3 cho Kokoro.")
    text=str(text).strip()
    if not text or len(text)>5000: raise ValueError("Nội dung Kokoro cần từ 1 đến 5.000 ký tự.")
    config=json.loads(paths["config"].read_text(encoding="utf-8")); vocabulary=config["vocab"]
    chunks=re.findall(r".{1,180}(?:\s+|$)|\S{1,180}",text,flags=re.DOTALL)
    session,actual_device=local_tts._session(paths,device,progress)
    language=LANGUAGES[identity[0]][1]
    with np.load(paths["voices"],allow_pickle=False) as voices:
        if identity not in voices.files: raise ValueError("Giọng Kokoro chưa được cài.")
        styles=voices[identity]
    from piper.phonemize_espeak import EspeakPhonemizer
    with tempfile.TemporaryDirectory(prefix=".kokoro-",dir=target.parent) as temporary:
        staged=Path(temporary)/"speech.wav"
        with wave.open(str(staged),"wb") as writer:
            writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(24000)
            for index,chunk in enumerate(chunks):
                ai_tools._check_cancel(cancel)
                with local_tts._ESPEAK_LOCK:
                    phonemizer=EspeakPhonemizer()
                    phones=phonemizer.phonemize(language,chunk.strip())
                for sentence in phones:
                    ids=[vocabulary[phone] for phone in sentence if phone in vocabulary]
                    if not ids or len(ids)>500: raise ValueError("Câu Kokoro quá dài hoặc chưa có âm vị hợp lệ.")
                    inputs={node.name:node for node in session.get_inputs()}
                    token_name="input_ids" if "input_ids" in inputs else "tokens"
                    speed_type=np.int32 if "int32" in inputs["speed"].type else np.float32
                    feeds={token_name:np.array([[0,*ids,0]],dtype=np.int64),"style":np.asarray(styles[len(ids)],dtype=np.float32),
                           "speed":np.array([1],dtype=speed_type)}
                    options=ort.RunOptions(); finished=threading.Event()
                    def monitor():
                        while not finished.wait(.05):
                            if cancel is not None and cancel.is_set(): options.terminate=True; return
                    observer=threading.Thread(target=monitor,daemon=True)
                    observer.start()
                    lock=local_tts._GPU_LOCK if actual_device=="cuda" else threading.Lock()
                    try:
                        while not lock.acquire(timeout=.1): ai_tools._check_cancel(cancel)
                        try:
                            ai_tools._check_cancel(cancel)
                            audio=np.asarray(session.run(None,feeds,options)[0],dtype=np.float32).reshape(-1)
                        finally: lock.release()
                    except Exception:
                        ai_tools._check_cancel(cancel)
                        raise
                    finally:
                        finished.set(); observer.join(timeout=.2)
                    if not audio.size or not np.isfinite(audio).all() or not np.any(np.abs(audio)>1e-6):
                        raise ai_tools.AIError("Kokoro chưa tạo âm thanh hợp lệ.")
                    writer.writeframesraw((np.clip(audio,-1,1)*32767).astype("<i2").tobytes())
                if progress: progress(int(90*(index+1)/len(chunks)),"Đã tạo câu Kokoro…")
        if target.suffix.lower()==".mp3":
            encoded=staged.with_suffix(".mp3")
            ai_tools._run_ffmpeg(["-i",str(staged),"-vn","-c:a","libmp3lame","-q:a","2",str(encoded)],cancel)
            staged=encoded
        result=ai_tools._publish(staged,target,cancel)
    local_tts._LAST_RUNTIME[str(voice)]=dict(device=actual_device,providers=session.get_providers(),model=str(paths["model"]),sample_rate=24000,sentences=len(chunks))
    return result
