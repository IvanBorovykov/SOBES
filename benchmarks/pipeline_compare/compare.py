"""Paired, paced synthetic audio: actual OptionQ vs Live STT + text LLMs.
No computer use, microphone, screen capture, or edits to application settings.
"""
import asyncio
import array
from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
import assistant
from api_keys import KeyPool, KeychainStore, credential_error
from google.genai import types
from benchmarks.option_q_probe import Audio, Overlay

CASES = {
 'python': 'Чем отличается список от кортежа в Питоне? Ответь коротко.',
 'last_question': 'Сначала хотел спросить про HTTP. Нет, другой вопрос. Сколько будет семнадцать плюс двадцать пять? Ответь коротко.',
 'cache': 'Что такое кэш и зачем он нужен? Объясни коротко.',
 'binary': 'Какова сложность бинарного поиска и какое условие нужно для массива?',
 'sql': 'Чем отличается WHERE от HAVING в SQL?',
 'async': 'Сделает ли async def функцию неблокирующей, если внутри вызвать time.sleep? Почему?',
}
MODELS = ('gemini-2.5-flash', 'gemini-3.5-flash-lite')
STT = 'gemini-3.5-transcribe-live'


def save(row):
 row['utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
 with (ROOT/'results.jsonl').open('a') as f: f.write(json.dumps(row, ensure_ascii=False)+'\n')
 print(json.dumps({k:v for k,v in row.items() if k not in ('text','transcript')}, ensure_ascii=False), flush=True)


def make_audio():
 manifest={}
 with tempfile.TemporaryDirectory(prefix='pipeline-voices-') as temp:
  for case, text in CASES.items():
   aiff=Path(temp)/(case+'.aiff');target=ROOT/(case+'.wav')
   subprocess.run(['say','-v','Milena','-o',str(aiff),text],check=True)
   subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(aiff),'-ar','16000','-ac','1',str(target)],check=True)
   with wave.open(str(target)) as wav: pcm=wav.readframes(wav.getnframes())
   manifest[case]={'question':text,'seconds':len(pcm)/32000,'sha256':hashlib.sha256(pcm).hexdigest()}
 (ROOT/'audio_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')


def samples(case,variant):
 with wave.open(str(ROOT/(case+'.wav'))) as f: pcm=f.readframes(f.getnframes())
 if variant=='clean':return pcm
 values=array.array('h',pcm)
 if variant=='quiet':return array.array('h',(int(x*.12) for x in values)).tobytes()
 rng=random.Random(42);rms=(sum(x*x for x in values)/len(values))**.5;noise=rms/(10**(15/20))
 return array.array('h',(max(-32768,min(32767,round(x+rng.gauss(0,noise)))) for x in values)).tobytes()


def text_answer(keys,model,transcript,pressed,common):
 started=time.monotonic();first=None;text='';attempts=[]
 try:
  for entry in keys.candidates(model):
   attempts.append(keys.entries().index(entry))
   config=types.GenerateContentConfig(
    system_instruction=assistant.LIVE_INSTRUCTION,
    thinking_config=types.ThinkingConfig(thinking_budget=0) if model=='gemini-2.5-flash' else types.ThinkingConfig(thinking_level='minimal'),
    http_options=types.HttpOptions(timeout=15_000,retry_options=types.HttpRetryOptions(attempts=1)))
   try:
    with closing(keys.client(entry).models.generate_content_stream(model=model,
      contents='Розпізнана розмова. Відповідай на останнє запитання:\n'+transcript,config=config)) as stream:
     for chunk in stream:
      if chunk.text:
       first=first or time.monotonic();text+=chunk.text
    keys.succeeded(entry);break
   except Exception as e:
    if credential_error(e):
     keys.failed(entry,model,e)
     if not text:continue
    raise
  save({**common,'pipeline':'stt_text','model':model,'status':'ok' if text else 'unavailable',
    'first_text_s':first-pressed if first else None,'text_llm_first_s':first-started if first else None,
    'total_s':time.monotonic()-pressed,'attempt_profiles':attempts,'transcript':transcript,'text':text})
 except Exception as e:
  save({**common,'pipeline':'stt_text','model':model,'status':'error','first_text_s':first-pressed if first else None,
    'total_s':time.monotonic()-pressed,'attempt_profiles':attempts,'transcript':transcript,'text':text,'error':keys.redact(e)[:800]})


async def run():
 stt_only='--stt-only' in sys.argv
 vocabulary='--vocabulary' in sys.argv
 keys=KeyPool(KeychainStore());source=Audio();overlay=Overlay();app=assistant.Assistant(overlay,source,keys)
 context=session=receiver=None
 state={}
 try:
  for entry in keys.candidates(STT):
   try:
    context=keys.client(entry).aio.live.connect(model=STT,config=types.LiveConnectConfig(
     response_modalities=['TEXT'],input_audio_transcription=types.AudioTranscriptionConfig(language_codes=['ru-RU'],
      custom_vocabulary=['SQL','WHERE','HAVING','Python','async def','time.sleep','asyncio.sleep','бинарный поиск'] if vocabulary else None)))
    session=await asyncio.wait_for(context.__aenter__(),10);print('STT connected',flush=True);break
   except Exception as e:
    save({'pipeline':'stt_setup','status':'error','profile':keys.entries().index(entry),'error':keys.redact(e)[:800]})
    if credential_error(e):keys.failed(entry,STT,e)
  if session is None:raise RuntimeError('No accessible streaming STT session')

  async def receive():
   while True:
    async for message in session.receive():
     c=message.server_content
     if not c:continue
     if c.interim_input_transcription and c.interim_input_transcription.text:
      state['interim']=c.interim_input_transcription.text
     if c.input_transcription and c.input_transcription.text:
      state.setdefault('segments',[]).append(c.input_transcription.text)
      state['last_final']=time.monotonic()
     if c.generation_complete and state.get('pressed') is not None:
      state['finalized']=time.monotonic();state['done'].set()
  receiver=asyncio.create_task(receive())

  with patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)):
   app.start_realtime()
   deadline=time.monotonic()+10
   while not app._realtime._channel.hint.ready and time.monotonic()<deadline:await asyncio.sleep(.02)
   for variant,cases in [('clean',list(CASES)),('quiet',['python','binary','async']),('noise',['last_question','cache','sql'])]:
    for case in cases:
     state.clear();state.update(done=asyncio.Event(),segments=[],pressed=None)
     pcm=samples(case,variant);source.pcm=pcm[-8*32000:];overlay.events=[]
     started=time.monotonic();pending=bytearray()
     for off in range(0,len(pcm),640):
      frame=pcm[off:off+640].ljust(640,b'\x00');source.callback(frame);pending.extend(frame)
      if len(pending)>=3200:
       await session.send_realtime_input(audio=types.Blob(data=bytes(pending),mime_type='audio/pcm;rate=16000'));pending.clear()
      await asyncio.sleep(max(0,started+min(off+640,len(pcm))/32000-time.monotonic()))
     pressed=time.monotonic();state['pressed']=pressed
     common={'case':case,'variant':variant,'voice_seconds':len(pcm)/32000,'vocabulary':vocabulary}
     worker=None
     if not stt_only:
      worker=threading.Thread(target=app._run_live,args=(app._generation,time.perf_counter(),False),kwargs={'audio_only':True},daemon=True)
      worker.start()
     if pending:await session.send_realtime_input(audio=types.Blob(data=bytes(pending),mime_type='audio/pcm;rate=16000'))
     await session.send_realtime_input(audio_stream_end=True)
     async def silence():
      while True:source.callback(bytes(640));await asyncio.sleep(.02)
     feeder=asyncio.create_task(silence())
     try:
      await asyncio.wait_for(state['done'].wait(),5)
      transcript=' '.join(state['segments'])
      if not transcript:raise RuntimeError('STT finalized without text')
      common['stt_finalize_s']=state['finalized']-pressed
      models=('gemini-3.5-flash-lite',) if stt_only else MODELS
      await asyncio.gather(*(asyncio.to_thread(text_answer,keys,model,transcript,pressed,dict(common)) for model in models))
     except Exception as e:
      save({**common,'pipeline':'stt','status':'error','error':keys.redact(e)[:800],'transcript':' '.join(state['segments'])})
     if worker:
      remaining=max(0,pressed+25-time.monotonic())
      await asyncio.to_thread(worker.join,remaining)
      if worker.is_alive():app._generation+=1
      final=overlay.events[-1][1] if overlay.events else ''
      rendered=[(t,text) for t,text in overlay.events if ('Live ·' in text and '\n\n' in text) or ('перший текст' in text and '\n\n' in text)]
      save({**common,'pipeline':'current_option_q','status':'ok' if ('✓ Готово' in final or '✓ Завершено' in final) else 'error',
       'model':app._realtime.model if final.startswith('Live ·') else final.split(' · ')[0],
       'used_live':final.startswith('Live ·'),'first_text_s':rendered[0][0]-pressed if rendered else None,
       'total_s':overlay.events[-1][0]-pressed if overlay.events else None,'text':final})
     feeder.cancel();await asyncio.gather(feeder,return_exceptions=True)
     # Keep the warm connections alive without adding artificial latency to the next measured request.
     await asyncio.sleep(.4)
     if receiver.done():receiver.result()
 finally:
  if receiver:receiver.cancel();await asyncio.gather(receiver,return_exceptions=True)
  if session:await context.__aexit__(None,None,None)
  app.close()

def direct_control():
 """Same Flash-Lite, audio instead of text, without any Live wait (best case)."""
 keys=KeyPool(KeychainStore());source=Audio();overlay=Overlay();app=assistant.Assistant(overlay,source,keys)
 try:
  with patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)), patch.object(assistant,'AUDIO_PRIMARY_MODEL','gemini-3.5-flash-lite'):
   for variant,cases in [('clean',list(CASES)),('quiet',['python','binary','async']),('noise',['last_question','cache','sql'])]:
    for case in cases:
     pcm=samples(case,variant);source.pcm=pcm[-8*32000:];overlay.events=[];pressed=time.monotonic()
     app._run_live(app._generation,time.perf_counter(),audio_only=True)
     final=overlay.events[-1][1] if overlay.events else ''
     rendered=[(t,text) for t,text in overlay.events if 'перший текст' in text and '\n\n' in text]
     save({'case':case,'variant':variant,'voice_seconds':len(pcm)/32000,'pipeline':'direct_audio_control',
      'model':final.split(' · ')[0],'status':'ok' if '✓ Завершено' in final else 'error',
      'first_text_s':rendered[0][0]-pressed if rendered else None,'total_s':time.monotonic()-pressed,'text':final})
     time.sleep(.4)
 finally:app.close()


if __name__=='__main__':
 if '--generate' in sys.argv:make_audio()
 elif '--direct' in sys.argv:direct_control()
 else:asyncio.run(run())
