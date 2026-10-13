"""Actual frozen EXE regressions for translation reuse and local GPU dubbing.

Uses generated captions/video and read-only shared TTS/CUDA model packs. API
credentials are unavailable in the isolated profile; no online request is made.
"""
from __future__ import annotations
from array import array
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import traceback
import uuid
import wave

import ai_tools
import engine
import subtitle_tools

ROOT=Path(__file__).resolve().parent
QA=ROOT.parent/'qa'
VOICE='ngochuyen_v2:ngoc_huyen_v_2'

def require(condition,message):
    if not condition: raise AssertionError(message)

def sha256(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--exe')
    parser.add_argument('--output')
    options=parser.parse_args()
    exe=Path(options.exe) if options.exe else ROOT/'dist/VideoEditorIndependent/VideoEditorIndependent.exe'
    require(exe.is_file(),'Build the EXE before running this check')
    QA.mkdir(exist_ok=True)
    run=QA/('translate_dub_exe_'+uuid.uuid4().hex[:8]);run.mkdir()
    shared=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'VideoEditor'
    env=os.environ.copy()
    env.update(LOCALAPPDATA=str(run/'local'),APPDATA=str(run/'roaming'),QT_QPA_PLATFORM='offscreen',
               HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',MEDIAFUSION_LOCAL_TTS_ROOT=str(shared/'tts'),
               VEI_CUDA_PATH=str(shared/'cuda'),PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONNOUSERSITE='1')
    env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    report=dict(executable=str(exe),executable_sha256=sha256(exe),run_dir=str(run),checks=[],
                external_api_calls=0,profile_isolated=True,shared_models_read_only=True)
    def ffmpeg(arguments):
        completed=subprocess.run([engine.tool_path('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',*arguments],
                                 capture_output=True,timeout=45,creationflags=engine._creation_flags())
        require(completed.returncode==0,completed.stderr.decode('utf-8','replace')[-1800:])
    def job(name,payload,timeout=150):
        request=run/(name+'.json');request.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        completed=subprocess.run([str(exe),'--job',str(request)],env=env,capture_output=True,
                                 timeout=timeout,creationflags=engine._creation_flags())
        result_file=request.with_suffix('.result.json')
        require(result_file.is_file(),'Frozen job did not publish a result: '+completed.stderr.decode('utf-8','replace')[-1800:])
        result=json.loads(result_file.read_text(encoding='utf-8'))
        require(completed.returncode==0 and result.get('ok'),str(result))
        return result
    def workflow_job(name,config,**video_values):
        payload=job(name,dict(operation='workflow',videos=[dict(path=str(video),**video_values)],
                             config=config,output=str(run)))
        entries=payload['result'];require(len(entries)==1 and entries[0]['status']=='done',str(entries))
        return entries[0],payload
    def audio_info(path):
        with wave.open(str(path),'rb') as reader:
            require(reader.getnchannels()==1 and reader.getsampwidth()==2,'Narration is not mono PCM16')
            frames=reader.getnframes();rate=reader.getframerate();samples=array('h',reader.readframes(frames))
        require(frames>0,'Narration is empty')
        peak=max(abs(value) for value in samples)
        rms=(sum(value*value for value in samples)/max(1,len(samples)))**.5
        return dict(frames=frames,rate=rate,seconds=frames/rate,peak=peak,rms=round(rms,2))
    def check(name,function):
        item=dict(name=name);report['checks'].append(item)
        try:item.update(status='passed',details=function())
        except Exception:item.update(status='failed',error=traceback.format_exc())
        print(json.dumps(dict(name=name,status=item['status']),ensure_ascii=True),flush=True)
    video=run/'source.mp4'
    ffmpeg(['-n','-f','lavfi','-i','color=c=0x202020:size=320x180:rate=12:duration=7',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=24000:duration=7','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(video)])
    original=run/'original.srt';translated=run/'translated.srt'
    source_rows=[dict(start=.2,end=2.5,text='你好。',voice=VOICE,speaker='A'),
                 dict(start=3,end=6.5,text='谢谢你。',voice=VOICE,speaker='B')]
    target_rows=[dict(source_rows[0],text='Xin chào, bạn khỏe không?',original_text=source_rows[0]['text']),
                 dict(source_rows[1],text='Cảm ơn bạn đã giúp tôi.',original_text=source_rows[1]['text'])]
    subtitle_tools.save_subtitles(source_rows,original);subtitle_tools.save_subtitles(target_rows,translated)
    def translation_reuse():
        config=dict(recognize=False,translate=True,dub=False,export=False,reuse_artifacts=True,
                    provider='gemini',model='gemini-qa',target_language='vi',source_language='zh')
        first,_=workflow_job('reuse-first',config,original_srt=str(original),translated_srt=str(translated))
        first_path=first['paths']['translated_subtitle']
        second,payload=workflow_job('reuse-second',config,original_srt=str(original),translated_srt=first_path)
        output=second['paths']['translated_subtitle'];actual=subtitle_tools.load_subtitles(output)
        require(len(actual)==2,'Translation reuse lost cue')
        for before,after in zip(target_rows,actual):
            require(all(after.get(key)==value for key,value in before.items()),'Repeated translation changed text, time or original metadata')
        require(any('Dùng bản dịch đã chỉnh' in event['message'] for event in payload['progress']),'Did not reuse edited translation')
        return dict(iterations=2,cues=2,original_text_metadata_preserved=True,output=output,no_api_credentials=True)
    check('Frozen repeated translation reuse preserves original_text without duplicate keyword failure',translation_reuse)
    def ellipsis_gpu():
        captions=run/'ellipsis.srt';output=run/'ellipsis.wav'
        ai_tools.save_srt([dict(start=.15,end=6.8,text='Xin chào... tôi đang kiểm tra lồng tiếng.')],captions)
        payload=job('v2-ellipsis',dict(operation='dub',input=str(captions),output=str(output),voice=VOICE,tts_device='cuda'))
        runtime=payload.get('runtime') or {}
        require(runtime.get('device')=='cuda' and 'CUDAExecutionProvider' in runtime.get('providers',[]),'V2 did not actually run on CUDA: '+str(runtime))
        info=audio_info(output);require(info['peak']>500 and info['rms']>30,'Ellipsis discarded valid speech')
        return dict(output=str(output),runtime=runtime,audio=info)
    check('Frozen Ngoc Huyen V2 reads sentence with ellipsis on CUDA',ellipsis_gpu)
    def bilingual_vietnamese():
        captions=run/'bilingual.srt'
        rows=[dict(start=.2,end=6.5,text='你好，朋友。\nXin chào, bạn khỏe không?',
                   original_text='你好，朋友。',translated_text='Xin chào, bạn khỏe không?',voice=VOICE)]
        subtitle_tools.save_subtitles(rows,captions)
        config=dict(recognize=False,translate=False,dub=True,export=False,reuse_artifacts=False,
                    voice=VOICE,voice_fit='natural',tts_device='cuda',voice_parallel=1)
        entry,payload=workflow_job('bilingual-vietnamese',config,original_srt=str(captions))
        output=entry['paths']['narration'];info=audio_info(output)
        require(info['peak']>500 and info['rms']>30,'Bilingual speech was empty')
        manifest=json.loads(Path(entry['manifest']).read_text(encoding='utf-8'))
        require(manifest['inputs'][0]['config']['tts_device']=='cuda','Workflow lost the strict CUDA device setting')
        require(subtitle_tools.load_subtitles(captions)==rows,'Dubbing changed display captions')
        return dict(output=output,audio=info,original_chinese_kept_for_display=True,translated_vietnamese_spoken=True,strict_cuda_no_fallback=True)
    check('Frozen bilingual metadata keeps Chinese captions and speaks translated Vietnamese',bilingual_vietnamese)
    def force_voice():
        captions=run/'fresh-voice.srt';old=run/'cached-voice.wav'
        subtitle_tools.save_subtitles([dict(start=.2,end=6.5,text='Xin chào, đây là giọng đọc mới.',voice=VOICE)],captions)
        ffmpeg(['-n','-f','lavfi','-i','sine=frequency=440:sample_rate=24000:duration=6.5','-ac','1','-c:a','pcm_s16le',str(old)])
        old_digest=sha256(old)
        config=dict(recognize=False,translate=False,dub=True,export=False,reuse_artifacts=True,force_dub=True,
                    voice=VOICE,voice_fit='natural',tts_device='cuda',voice_parallel=1)
        entry,payload=workflow_job('force-new-dub',config,original_srt=str(captions),voice_path=str(old))
        output=Path(entry['paths']['narration']);info=audio_info(output)
        require(output.resolve()!=old.resolve() and sha256(output)!=old_digest,'Explicit dub reused cached narration')
        require(sha256(old)==old_digest,'Existing narration was overwritten')
        require(info['peak']>500,'New narration empty')
        require(not any('Dùng bản lồng tiếng đã có' in event['message'] for event in payload['progress']),'Cached narration reused')
        manifest=json.loads(Path(entry['manifest']).read_text(encoding='utf-8'))
        require(manifest['inputs'][0]['config']['tts_device']=='cuda','Workflow lost the strict CUDA device setting')
        return dict(output=str(output),cached_voice_kept=True,new_audio_generated=True,audio=info,strict_cuda_no_fallback=True)
    check('Frozen explicit dub creates fresh audio while retaining cached narration',force_voice)
    report['passed']=sum(item['status']=='passed' for item in report['checks'])
    report['failed']=len(report['checks'])-report['passed'];report['status']='passed' if not report['failed'] else 'failed'
    output=Path(options.output) if options.output else QA/'translate-dub-packaged-report.json'
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(report=str(output),passed=report['passed'],failed=report['failed'],executable_sha256=report['executable_sha256']),ensure_ascii=True),flush=True)
    return bool(report['failed'])

if __name__=='__main__':raise SystemExit(main())
