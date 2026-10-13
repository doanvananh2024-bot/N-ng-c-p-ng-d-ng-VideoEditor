"""Translator setup and transport regression checks; generated dialogue only."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import threading
from unittest.mock import patch

import ai_tools
import providers

class Store:
    def __init__(self, count=1): self.count=count
    def get_keys(self, provider): return ['generated-qa-credential'] * self.count

class Response:
    def __init__(self, status=200, payload=None, raw=None):
        self.status_code=status
        self.payload=payload
        self.content=raw if raw is not None else json.dumps(payload).encode()
        self.headers={}
        self.closed=False
    def json(self): return self.payload
    def close(self): self.closed=True

ROWS=[dict(start=.25,end=2.25,text='Hello, how are you?',voice='ngochuyen:ngoc_huyen',speaker='A'),
      dict(start=2.5,end=4.5,text='Thank you for helping me.',voice='ngochuyen_v2:ngoc_huyen',speaker='B')]

def require(value, message):
    if not value: raise AssertionError(message)

def expect_error(function, phrase):
    try: function()
    except providers.ProviderError as error:
        require(phrase in str(error), 'Incorrect setup error')
        require('generated-qa-credential' not in str(error), 'Credential leaked')
    else: raise AssertionError('Expected provider error')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--output')
    options=parser.parse_args()
    checks=[]
    def check(name,function):
        detail=function()
        checks.append(dict(name=name,status='passed',detail=detail))
    def preflight():
        original=dict(provider='gemini',model='gemini-qa',target_language='vi',source_language='en',
                      batch_size=205,translate_workers=3,image_context=True)
        saved=copy.deepcopy(original)
        with patch.object(providers.requests,'request') as http:
            ready=providers.validate_translation_config(original,store=Store(2))
            require(ready['key_count']==2 and ready['model']=='gemini-qa','Readiness incorrect')
            require(ready['translate_workers']==3 and original==saved,'Config changed')
            require('generated-qa-credential' not in json.dumps(ready),'Credential returned')
            require(not http.called,'Preflight issued network')
        return dict(no_network=True,config_preserved=True,key_count_only=True)
    check('Local preflight keeps configuration and returns no credentials',preflight)
    def setup_errors():
        with patch.object(providers.requests,'request') as http:
            expect_error(lambda:providers.validate_translation_config(dict(provider='gemini',model='gemini-qa'),store=Store(0)),'Chưa có khóa')
            expect_error(lambda:providers.validate_translation_config(dict(provider='gemini'),store=Store()),'ID mô hình')
            expect_error(lambda:providers.validate_translation_config(dict(provider='google',target_language='auto')),'ngôn ngữ đích')
            expect_error(lambda:providers.validate_translation_config(dict(provider='google',image_context=True)),'ảnh ngữ cảnh')
            expect_error(lambda:providers.validate_translation_config(dict(provider='deepseek',image_context=True),store=Store()),'xử lý văn bản')
            expect_error(lambda:providers.validate_translation_config(dict(provider='google',batch_size='bad')),'số nguyên')
            expect_error(lambda:providers.validate_translation_config(dict(provider='google',translate_workers=9)),'1–8')
            require(not http.called,'Bad setup issued network')
        return dict(error_cases=7,no_network=True)
    check('Missing keys, models and unsupported setup fail before HTTP',setup_errors)
    def gemini_transport():
        captured=[]
        original=copy.deepcopy(ROWS)
        def request(method,url,**kwargs):
            require(method=='POST' and url.endswith('/models/gemini-qa:generateContent'),'Wrong endpoint')
            require(kwargs['headers'].get('x-goog-api-key')=='generated-qa-credential','Credential header missing')
            require(not kwargs.get('params'),'Key sent as URL parameter')
            cues=json.loads(kwargs['json']['contents'][0]['parts'][0]['text'])['cues']
            captured.extend(cues)
            translated=[dict(id=cue['id'],text=('Xin chào, bạn khỏe không?' if i==0 else 'Cảm ơn bạn đã giúp tôi.')) for i,cue in enumerate(cues)]
            return Response(payload={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(dict(cues=translated),ensure_ascii=False)}]}}]})
        with patch.object(providers.requests,'request',side_effect=request):
            translated=providers.translate_rows(ROWS,dict(provider='gemini',model='gemini-qa',target_language='vi',source_language='en'),store=Store())
        require(ROWS==original,'Input rows mutated')
        require(len(translated)==2 and len(captured)==2,'Lost cue')
        for source,target in zip(ROWS,translated):
            require({k:v for k,v in source.items() if k!='text'}=={k:v for k,v in target.items() if k!='text'},'Timing or metadata changed')
            require(source['text']!=target['text'],'Text not translated')
        return dict(cues=2,timing_and_voice_metadata=True,real_api_response_contract=True,transport='mock')
    check('Gemini response produces two aligned translated cues',gemini_transport)
    def google_transport():
        calls=[]
        def request(method,url,**kwargs):
            calls.append(kwargs['params'])
            return Response(payload=[[[('Xin chào.' if len(calls)==1 else 'Cảm ơn.'),kwargs['params']['q'],None,None]],None,'en'])
        original=copy.deepcopy(ROWS)
        with patch.object(providers.requests,'request',side_effect=request),patch.object(ai_tools,'_wait'):
            result=providers.translate_rows(ROWS,dict(provider='Google Translate',source_language='en',target_language='vi'))
        require(ROWS==original and len(result)==2,'Google mutation or missing cue')
        require(all(row['sl']=='en' and row['tl']=='vi' for row in calls),'Languages ignored')
        require(result[0]['voice']==ROWS[0]['voice'] and result[1]['start']==ROWS[1]['start'],'Metadata changed')
        return dict(cues=2,explicit_source_language=True,transport='mock')
    check('Google translator honors source and target and preserves metadata',google_transport)
    def malformed():
        for payload in ({},[],[{}],[None]):
            response=Response(payload=payload)
            with patch.object(providers.requests,'request',return_value=response):
                expect_error(lambda:providers.translate_rows(ROWS[:1],dict(provider='google')),'đúng định dạng')
            require(response.closed,'Response not closed')
        return dict(malformed_responses=4,redacted_error=True)
    check('Malformed Google JSON returns a clear error',malformed)
    def google_limit():
        response=Response(429,payload={'error':'generated-qa-credential'})
        with patch.object(providers.requests,'request',return_value=response) as http:
            expect_error(lambda:providers.translate_rows(ROWS,dict(provider='google')),'HTTP 429')
            require(http.call_count==1,'429 was retried or alternate endpoint used')
        require(response.closed,'429 response not closed')
        require('Khóa API' in str(providers._http_error('google',429)),'Missing recovery instructions')
        return dict(requests=1,api_setup_guidance=True,no_partial_result=True)
    check('Google 429 stops with account API guidance',google_limit)
    def cancellation():
        signal=threading.Event();signal.set()
        with patch.object(providers.requests,'request') as http:
            try:providers.translate_rows(ROWS,dict(provider='google'),cancel=signal)
            except ai_tools.CancelledError:pass
            else:raise AssertionError('Cancellation ignored')
            require(not http.called,'Cancelled operation sent HTTP')
        return dict(no_network=True)
    check('Cancelled translation sends no request',cancellation)
    report=dict(checks=checks,passed=len(checks))
    if options.live:
        try:
            result=providers.translate_rows(ROWS,dict(provider='google',source_language='en',target_language='vi'))
            require(len(result)==2 and all(a['text']!=b['text'] for a,b in zip(ROWS,result)),'Live translation unchanged')
            report['live_google']=dict(status='translated',cues=len(result),sample_texts=[row['text'] for row in result])
        except providers.ProviderError as error:
            report['live_google']=dict(status='rate_limited' if '429' in str(error) else 'unavailable',error=str(error),translated=False)
    output=Path(options.output) if options.output else Path(__file__).resolve().parent.parent/'qa'/'translation-fix.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'],report=str(output),live_google=report.get('live_google')),ensure_ascii=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
