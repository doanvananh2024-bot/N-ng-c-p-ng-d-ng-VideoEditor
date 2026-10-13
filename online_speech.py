"""Explicit speech requests through documented APIs or a user's CapCut bridge.

No import-time network or credential discovery. Paid posts are not retried.
Contracts: platform.minimax.io/docs/api-reference/speech-t2a-http,
docs.siliconflow.cn/docs/api/audio-speech-post,
api-docs.vbee.vn/vbee-api/text-to-speech/realtime-api,
ai.zalo.solutions/docs/api/text-to-audio-converter.
"""
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import ai_tools
import engine

PROVIDERS=("gtts","minimax","siliconflow","vbee","zalo","capcut")

def options(provider,store):
    return store.get_options(provider) if hasattr(store,"get_options") else {}

def bridge_url(value):
    url=urlparse(str(value))
    if url.username or url.password or url.query or url.fragment or not url.hostname or (url.scheme!="https" and not (url.scheme=="http" and url.hostname in ("localhost","127.0.0.1","::1"))):
        raise ValueError("Địa chỉ cầu nối cần HTTPS, hoặc HTTP tại localhost.")
    return str(value).rstrip("/")

def row(provider,identity,name,locale="mul",gender="Unknown"):
    if "-" not in locale and locale!="mul":
        locale={"vi":"vi-VN","en":"en-US","zh":"zh-CN","ja":"ja-JP","fr":"fr-FR","de":"de-DE","es":"es-ES","ko":"ko-KR","it":"it-IT","pt":"pt-BR","hi":"hi-IN"}.get(locale,"mul")
    return dict(ShortName=provider+":"+identity,FriendlyName=name,Locale=locale,Gender=gender,Provider=provider,VoiceTag=dict(ContentCategories=[],VoicePersonalities=[]))

def seed_catalog(provider):
    if provider=="gtts":
        from gtts.lang import tts_langs
        return [row(provider,language,name,language) for language,name in tts_langs().items()]
    if provider=="siliconflow":
        return [row(provider,name,name.title(),gender="Male" if index<4 else "Female") for index,name in enumerate(("alex","benjamin","charles","david","anna","bella","claire","diana"))]
    if provider=="minimax":
        return [row(provider,"English_expressive_narrator","English expressive narrator","en-US")]
    if provider=="zalo":
        return [row(provider,str(index),name,"vi-VN",gender) for index,name,gender in ((1,"Nữ miền Nam","Female"),(2,"Nữ miền Bắc","Female"),(3,"Nam miền Nam","Male"),(4,"Nam miền Bắc","Male"))]
    if provider=="vbee":
        return [row(provider,"hn_female_ngochuyen_full_48k-fhg","Ngọc Huyền","vi-VN","Female")]
    return []

def catalog(provider,cancel,store):
    import tts_providers as api
    if provider in ("gtts","siliconflow","zalo"):
        return seed_catalog(provider)
    settings=options(provider,store)
    if provider=="capcut":
        endpoint=bridge_url(settings.get("endpoint",""))
        headers={}
        keys=store.get_keys(provider)
        if keys: headers["Authorization"]="Bearer "+keys[0]
        response=api._request("GET",endpoint+"/v2/speakers",provider,cancel,headers=headers)
        try:
            # Bridges commonly return an array; use the same bounded JSON reader.
            data=read_json(response,provider,cancel)
        finally: response.close()
        values=data if isinstance(data,list) else data.get("speakers",data.get("voices",[]))
        return [row(provider,str(value.get("id",value.get("speaker_id",""))),str(value.get("name",value.get("id",""))),str(value.get("locale","mul"))) for value in values if isinstance(value,dict)]
    key=api._key(provider,store)
    if provider=="minimax":
        response=api._request("POST","https://api.minimax.io/v1/get_voice",provider,cancel,headers={"Authorization":"Bearer "+key},json={"voice_type":"all"})
        try: data=read_json(response,provider,cancel)
        finally: response.close()
        if (data.get("base_resp") or {}).get("status_code",0)!=0:
            raise api.TTSError("MiniMax chưa trả danh sách giọng hợp lệ.")
        values=sum([data.get(name) or [] for name in ("system_voice","voice_cloning","voice_generation")],[])
        return [row(provider,str(value.get("voice_id","")),str(value.get("voice_name",value.get("voice_id","")))) for value in values if isinstance(value,dict)]
    app_id=settings.get("app_id","")
    if not app_id: raise api.TTSError("Nhập App ID Vbee trong cấu hình kết nối.")
    result=[]; cursor=None; seen=set()
    for _ in range(20):
        params={"limit":100}
        if cursor: params["cursor"]=cursor
        response=api._request("GET","https://vbee.vn/api/public/v1/voices",provider,cancel,headers={"Authorization":"Bearer "+key,"App-Id":app_id},params=params)
        try: data=read_json(response,provider,cancel)
        finally: response.close()
        if data.get("status")!=1: raise api.TTSError("Vbee từ chối danh sách giọng. Kiểm tra App ID và Token.")
        data=data.get("result") or {}
        for voice in data.get("voices") or []:
            code=str(voice.get("code",""))
            # The synchronous contract supports these five Vietnamese families.
            if any(name in code.lower() for name in ("ngochuyen","tuongvy","maiphuong","lantrinh","thaotrinh")):
                result.append(row(provider,code,str(voice.get("name",code)),str(voice.get("language_code","vi-VN")),str(voice.get("gender","Unknown")).title()))
        page=data.get("pagination") or {}
        if not page.get("has_next_page"): break
        cursor=page.get("next_cursor")
        if not cursor or cursor in seen: raise api.TTSError("Phân trang giọng Vbee chưa hợp lệ.")
        seen.add(cursor)
    return result

def read_json(response,provider,cancel):
    import tts_providers as api
    if not 200<=response.status_code<300: raise api._status_error(provider,response.status_code)
    data=bytearray()
    for chunk in response.iter_content(65536):
        ai_tools._check_cancel(cancel)
        data.extend(chunk)
        if len(data)>api._MAX_JSON: raise api.TTSError("Phản hồi danh sách giọng quá lớn.")
    try: return json.loads(data.decode("utf-8"))
    except (ValueError,UnicodeError): raise api.TTSError("Dịch vụ giọng trả dữ liệu không hợp lệ.") from None

def stream_audio(response,path,provider,cancel):
    import tts_providers as api
    if not 200<=response.status_code<300: raise api._status_error(provider,response.status_code)
    count=0
    with Path(path).open("wb") as output:
        for chunk in response.iter_content(65536):
            ai_tools._check_cancel(cancel)
            count+=len(chunk)
            if count>api._MAX_AUDIO: raise api.TTSError("Âm thanh trả về quá lớn.")
            output.write(chunk)
    if count<16: raise api.TTSError("Dịch vụ chưa trả âm thanh.")

def synthesize(text,voice,output,cancel=None,store=None):
    import tts_providers as api
    provider,identity=api.split_voice_id(voice)
    store=store or api.TTSKeyStore()
    ai_tools._check_cancel(cancel)
    text=str(text).strip()
    if not text or len(text)>5000: raise api.TTSError("Nội dung cần từ 1 đến 5.000 ký tự.")
    target=ai_tools._output_target(output)
    if target.suffix.lower()!=".mp3": raise api.TTSError("Chọn đầu ra MP3 cho dịch vụ online.")
    settings=options(provider,store)
    key=api._key(provider,store) if provider not in ("gtts","capcut") else ""
    with tempfile.TemporaryDirectory(prefix=".speech-online-",dir=target.parent) as temporary:
        folder=Path(temporary); raw=folder/"received.audio"; staged=folder/"speech.mp3"
        if provider=="gtts":
            from gtts import gTTS
            if identity not in {row["ShortName"].split(":",1)[1] for row in seed_catalog(provider)}:
                raise api.TTSError("Ngôn ngữ gTTS chưa được hỗ trợ.")
            with raw.open("wb") as output_file:
                for chunk in gTTS(text,lang=identity,lang_check=False,timeout=(5,10)).stream():
                    ai_tools._check_cancel(cancel)
                    output_file.write(chunk)
        elif provider=="minimax":
            payload=dict(model=settings.get("model") or "speech-2.8-hd",text=text,stream=False,language_boost="auto",output_format="hex",
                voice_setting=dict(voice_id=identity,speed=1,vol=1,pitch=0),audio_setting=dict(sample_rate=32000,bitrate=128000,format="mp3",channel=1))
            response=api._request("POST","https://api.minimax.io/v1/t2a_v2",provider,cancel,headers={"Authorization":"Bearer "+key},json=payload)
            try: data=read_json(response,provider,cancel)
            finally: response.close()
            if (data.get("base_resp") or {}).get("status_code")!=0: raise api.TTSError("MiniMax từ chối tạo giọng. Kiểm tra khóa, mô hình và hạn mức.")
            encoded=(data.get("data") or {}).get("audio","")
            if not isinstance(encoded,str) or len(encoded)>api._MAX_AUDIO*2: raise api.TTSError("Âm thanh MiniMax chưa hợp lệ.")
            try: raw.write_bytes(bytes.fromhex(encoded))
            except ValueError: raise api.TTSError("Âm thanh MiniMax chưa hợp lệ.") from None
        elif provider=="zalo":
            response=api._request("POST","https://api.zalo.ai/v1/tts/synthesize",provider,cancel,headers={"apikey":key},data={"input":text,"speaker_id":identity,"speed":1})
            try: data=read_json(response,provider,cancel)
            finally: response.close()
            if data.get("error_code")!=0: raise api.TTSError("Zalo từ chối tạo giọng. Kiểm tra khóa và hạn mức.")
            url=str((data.get("data") or {}).get("url","")); host=(urlparse(url).hostname or "").lower()
            if urlparse(url).scheme!="https" or not any(host==domain or host.endswith("."+domain) for domain in ("zdn.vn","zalo.ai","zalo.solutions")):
                raise api.TTSError("Địa chỉ âm thanh Zalo chưa hợp lệ.")
            response=api._request("GET",url,provider,cancel)
            try: stream_audio(response,raw,provider,cancel)
            finally: response.close()
        elif provider=="vbee":
            app_id=settings.get("app_id","")
            if not app_id: raise api.TTSError("Nhập App ID Vbee trong cấu hình kết nối.")
            # Each request fits the official synchronous 300-character limit.
            chunks=re.findall(r".{1,250}(?:\s+|$)|\S{1,250}",text,flags=re.DOTALL)
            with raw.open("wb") as output_file:
                for index,chunk in enumerate(chunks):
                    response=api._request("POST","https://api.vbee.vn/v1/tts",provider,cancel,headers={"Authorization":"Bearer "+key,"App-Id":app_id},
                        json=dict(text=chunk.strip(),mode="sync",voiceCode=identity,outputFormat="mp3",bitrate=128,speed=1))
                    part=folder/f"part-{index}.mp3"
                    try: stream_audio(response,part,provider,cancel)
                    finally: response.close()
                    output_file.write(part.read_bytes())
        else:
            if provider=="siliconflow":
                model=settings.get("model") or "FunAudioLLM/CosyVoice2-0.5B"
                url="https://api.siliconflow.cn/v1/audio/speech"
                headers={"Authorization":"Bearer "+key}
                payload=dict(model=model,input=text,voice=model+":"+identity,response_format="mp3",speed=1)
            else:
                url=bridge_url(settings.get("endpoint",""))+"/v2/synthesize"
                headers={}; keys=store.get_keys(provider)
                if keys: headers["Authorization"]="Bearer "+keys[0]
                payload=dict(text=text,speaker=identity,method="buffer")
            response=api._request("POST",url,provider,cancel,headers=headers,json=payload)
            try: stream_audio(response,raw,provider,cancel)
            finally: response.close()
        ai_tools._check_cancel(cancel)
        ai_tools._run_ffmpeg(["-i",str(raw),"-vn","-c:a","libmp3lame","-q:a","2",str(staged)],cancel)
        api._verify_mp3(staged,cancel)
        return ai_tools._publish(staged,target,cancel)
