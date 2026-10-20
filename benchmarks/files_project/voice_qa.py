"""Real API tests for OptionA: five synthetic code files + spoken questions.
No screenshots, microphone or user documents; credentials only from Keychain.
"""
import argparse
from array import array
from contextlib import ExitStack
import hashlib
import json
import math
import random
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import assistant
from api_keys import KeyPool,KeychainStore
from audio_payload import prepare_audio
from google.genai import types

ROOT=Path(__file__).resolve().parent
FILES=['models.py','pricing.py','storage.py','service.py','reporting.py']
CASES={
 'locate':dict(question='Где в проекте находится функция би би би и что она делает?',source='pricing.py',symbol='BBB',keywords=['BBB','pricing.py']),
 'callers':dict(question='Из каких функций вызывается би би би? Назови файл и обе функции.',source='service.py',symbol='checkout',keywords=['checkout','quote','service.py']),
 'shipping':dict(question='Порог бесплатной доставки проверяется до скидки или после? Где это видно в коде?',source='pricing.py',symbol='BBB',keywords=['до','subtotal']),
 'discount':dict(question='Какая максимальная скидка получится у премиум клиента с купоном студент? Покажи где она ограничена.',source='pricing.py',symbol='discount_percent',keywords=['15']),
 'rollback':dict(question='Если при сохранении оплаченного заказа возникнет ошибка, как вернутся товары на склад?',source='service.py',symbol='checkout',keywords=['release','except']),
 'cancel':dict(question='Какие файлы объясняют, почему отменённый оплаченный заказ исчезает из выручки и возвращает товар на склад?',source='service.py',symbol='cancel',keywords=['cancel','paid','reporting']),
 'missing':dict(question='Где в этих файлах реализована отправка электронной почты? Назови функцию.',source=None,symbol=None,keywords=['не']),
 'calculation':dict(question='Товар стоит десять тысяч копеек. Премиум клиент берёт одну штуку с купоном студент. Какой итог и сколько стоит доставка?',source='pricing.py',symbol='BBB',keywords=['8500','0']),
 'last_question':dict(question='Сначала хотел спросить про скидки. Нет, другой вопрос: где в проекте функция би би би?',source='pricing.py',symbol='BBB',keywords=['BBB','pricing.py']),
}
class Audio:
 def __init__(self,pcm):self.pcm=pcm
 def pcm_bytes(self):return self.pcm
 def request_audio(self,pcm=None):return prepare_audio(self.pcm if pcm is None else pcm)
class Overlay:
 def __init__(self):self.events=[]
 def show_text(self,text,**kwargs):self.events.append((time.monotonic(),text))

def fixtures():
 meta={}
 with tempfile.TemporaryDirectory() as directory:
  for name,case in CASES.items():
   aiff=Path(directory)/f'{name}.aiff';wav=ROOT/f'{name}.wav'
   subprocess.run(['say','-v','Milena','-o',str(aiff),case['question']],check=True)
   subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(aiff),'-ar','16000','-ac','1',str(wav)],check=True)
   with wave.open(str(wav)) as source:
    meta[name]=dict(question=case['question'],seconds=source.getnframes()/16000,sha256=hashlib.sha256(wav.read_bytes()).hexdigest())
 (ROOT/'audio_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))

def read_audio(name,variant):
 with wave.open(str(ROOT/f'{name}.wav')) as source:pcm=source.readframes(source.getnframes())
 if variant=='clean':return pcm
 values=array('h',pcm)
 if variant=='quiet':values=array('h',(int(x*.12) for x in values))
 elif variant=='noise':
  rng=random.Random(42)
  rms=math.sqrt(sum(x*x for x in values)/len(values));sigma=rms/(10**(15/20))
  values=array('h',(max(-32768,min(32767,int(x+rng.gauss(0,sigma)))) for x in values))
 return values.tobytes()

def evaluate(case,text):
 body=text.split('\n\nМатеріали:')[0]
 citations=[]
 for match in re.finditer(r'\[F([1-5])[^\]]*?(\d+)(?:\s*[–—-]\s*(\d+))?\]',body):
  source=int(match[1]);start=int(match[2]);end=int(match[3] or start)
  citations.append(dict(file=FILES[source-1],start=start,end=end))
 manifest=json.loads((ROOT/'project_manifest.json').read_text())
 in_bounds=bool(citations) and all(1<=r['start']<=r['end']<=manifest[r['file']]['lines'] for r in citations)
 gold=CASES[case]
 evidence=False
 if gold['source']:
  span=manifest[gold['source']]['symbols'][gold['symbol']]
  evidence=any(r['file']==gold['source'] and r['start']<=span['end'] and r['end']>=span['line'] for r in citations)
 else:evidence=True
 compact=re.sub(r'\s+','',body.lower())
 keywords=all(x.lower().replace(' ','') in compact for x in gold['keywords'])
 return dict(citations=citations,citations_in_bounds=in_bounds,citation_hits_gold=evidence,keyword_check=keywords)

def run(args):
 if args.generate:fixtures();return
 paths=tuple(str(ROOT/file) for file in FILES)
 keys=KeyPool(KeychainStore());profile_count=len(keys.entries())
 print('Saved profiles:',profile_count,flush=True)
 try:
  for model in args.models:
   overlay=Overlay();source=Audio(b'');app=assistant.Assistant(overlay,source,keys)
   try:
    api_errors=[];attempts=[]
    def wrap(original,profile):
     def generate(*,model,contents,config):
      config.http_options=types.HttpOptions(timeout=20_000,retry_options=types.HttpRetryOptions(attempts=1))
      if model=='gemini-2.5-flash':config.thinking_config=types.ThinkingConfig(thinking_budget=0)
      elif model=='gemini-3.1-flash-lite':config.thinking_config=types.ThinkingConfig(thinking_level='low')
      elif model=='gemini-3-flash-preview':config.thinking_config=types.ThinkingConfig(thinking_level='low')
      if args.thinking:config.thinking_config=types.ThinkingConfig(thinking_level=args.thinking)
      attempts.append(dict(model=model,profile=profile))
      stream=original(model=model,contents=contents,config=config)
      try:
       yield from stream
      except Exception as error:
       api_errors.append(keys.redact(error)[:1500])
       raise
      finally:stream.close()
     return generate
    with ExitStack() as patches:
     for index,entry in enumerate(keys.entries()):
      client=keys.client(entry)
      patches.enter_context(patch.object(client.models,'generate_content_stream',side_effect=wrap(client.models.generate_content_stream,index)))
     for name in ['PRIMARY_MODEL','FALLBACK_MODEL','FILES_PRIMARY_MODEL','FILES_FALLBACK_MODEL']:
      patches.enter_context(patch.object(assistant,name,model))
     patches.enter_context(patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*a:fn(*a)))
     patches.enter_context(patch.object(assistant,'capture_screenshot',side_effect=AssertionError('No screen')))
     for repeat in range(args.repeats):
      for variant in args.variants:
       for name in args.cases:
        source.pcm=read_audio(name,variant);overlay.events=[];api_errors.clear();attempts.clear()
        wait_started=time.monotonic()
        while not keys.candidates(model) and time.monotonic()-wait_started<args.quota_wait:time.sleep(1)
        cooldown_wait=time.monotonic()-wait_started;start=time.monotonic()
        app._run(app._generation,time.perf_counter(),False,audio_only=True,context_paths=paths,captured_pcm=source.pcm)
        final=overlay.events[-1][1] if overlay.events else ''
        shown=[(t,text) for t,text in overlay.events if 'перший текст' in text]
        row=dict(model=model,case=name,variant=variant,repeat=repeat,thinking=args.thinking,first_text_s=shown[0][0]-start if shown else None,
          total_s=time.monotonic()-start,status='ok' if '✓ Завершено' in final else 'error',text=final,
          audio_seconds=len(source.pcm)/32000,profiles=profile_count,api_errors=list(api_errors),network_attempts=len(attempts),attempts=list(attempts),cooldown_wait_s=cooldown_wait,**evaluate(name,final))
        with (ROOT/args.output).open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps({k:v for k,v in row.items() if k!='text'},ensure_ascii=False),flush=True)
        time.sleep(max(0,args.pace-(time.monotonic()-start)))
   finally:app._capture_pool.shutdown(wait=False,cancel_futures=True)
 finally:keys.close()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--quota-wait',type=float,default=65);p.add_argument('--thinking',choices=['minimal','low','medium','high']);p.add_argument('--pace',type=float,default=15);p.add_argument('--generate',action='store_true');p.add_argument('--models',nargs='+',default=['gemini-3.5-flash-lite','gemini-2.5-flash','gemini-3-flash-preview']);p.add_argument('--cases',nargs='+',default=list(CASES));p.add_argument('--variants',nargs='+',default=['clean']);p.add_argument('--repeats',type=int,default=1);p.add_argument('--output',default='results.jsonl');run(p.parse_args())
