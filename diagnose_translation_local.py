from pathlib import Path
import json, sys, threading
import ai_tools, providers, subtitle_tools

if __name__ == '__main__':
    base=Path(__file__).resolve().parent.parent/'qa'/'translation-diagnosis'
    base.mkdir(parents=True, exist_ok=True)
    rows=[dict(start=0,end=2,text='Hello, how are you?',voice='ngochuyen:ngoc_huyen'),dict(start=2,end=4,text='Thank you for helping me.')]
    report={}
    try:
        translated=providers.translate_rows(rows,dict(provider='google',source_language='en',target_language='vi'))
        report['providers']=dict(ok=True,rows=translated)
    except Exception as error:
        report['providers']=dict(ok=False,error=str(error),type=type(error).__name__)
    source=base/'original.srt'
    subtitle_tools.save_subtitles(rows,source)
    try:
        output=base/'translated.srt'
        ai_tools.translate_srt(str(source),str(output),'vi')
        report['ai_tools']=dict(ok=True,rows=subtitle_tools.load_subtitles(output))
    except Exception as error:
        report['ai_tools']=dict(ok=False,error=str(error),type=type(error).__name__)
    (base/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))

