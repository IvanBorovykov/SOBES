"""Real API dialog/latency check; synthetic PCM, no UI/capture or settings edits."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import wave
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]))
import assistant
from api_keys import KeyPool,KeychainStore
from benchmarks.option_q_probe import Audio,Overlay

TURNS=[
 ('numbers','Давай обсудим конкретный список чисел: семь, одиннадцать, девятнадцать. Сколько в нём элементов?', '3 elements'),
 ('numbers_follow','А если добавить к нему число двадцать три, какая будет сумма всех элементов?', '60'),
 ('numbers_change','Теперь убери из этого списка число одиннадцать. Какая получится сумма?', '49'),
 ('topic','Сменим тему. В Python я использую asyncio и вызываю time.sleep внутри async def. Блокируется ли event loop?', 'yes, blocks'),
 ('follow','А чем заменить этот вызов, чтобы остальные задачи продолжали работать?', 'await asyncio.sleep'),
 ('sql','Теперь SQL. Чем WHERE отличается от HAVING?', 'rows before grouping vs aggregate filtering'),
 ('sql_follow','А в каком из этих двух условий можно использовать SUM?', 'HAVING'),
 ('correction','Нет, другой вопрос. Сколько будет семнадцать плюс двадцать пять?', '42'),
]

def emit(row,phase):
 with (ROOT/(phase+'.jsonl')).open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
 print({k:v for k,v in row.items() if k not in ('text','trace')},flush=True)

def generate():
 manifest=[]
 with tempfile.TemporaryDirectory(prefix='dialog-speech-') as temp:
  for case,text,gold in TURNS:
   aiff=Path(temp)/(case+'.aiff');target=ROOT/(case+'.wav')
   subprocess.run(['say','-v','Milena','-o',str(aiff),text],check=True)
   subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(aiff),'-ar','16000','-ac','1',str(target)],check=True)
   manifest.append({'case':case,'question':text,'gold':gold,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
 (ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')

def main(phase, hedge=.35, packet_frames=5):
 import live_transcription
 assistant.ANSWER_HEDGE_SECONDS=hedge
 live_transcription.PACKET_BYTES=640*packet_frames
 keys=KeyPool(KeychainStore());source=Audio();source._seconds=30;overlay=Overlay();app=assistant.Assistant(overlay,source,keys)
 try:
  with patch.object(assistant,'TRANSCRIPT_MODE',True),patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)):
   app.start_realtime();deadline=time.monotonic()+10
   while not app._realtime._channel.hint.ready and time.monotonic()<deadline:time.sleep(.02)
   assert app._realtime._channel.hint.ready,app._realtime.status()
   for case,question,gold in TURNS:
    with wave.open(str(ROOT/(case+'.wav'))) as f:pcm=f.readframes(f.getnframes())
    source.pcm=pcm;overlay.events=[];start=time.monotonic()
    for off in range(0,len(pcm),640):
     source.callback(pcm[off:off+640].ljust(640,b'\x00'))
     time.sleep(max(0,start+min(off+640,len(pcm))/32000-time.monotonic()))
    pressed=time.monotonic();worker=threading.Thread(target=app._run_live,args=(app._generation,time.perf_counter(),False),kwargs={'audio_only':True},daemon=True);worker.start()
    while worker.is_alive() and time.monotonic()-pressed<20:
     source.callback(bytes(640));time.sleep(.02)
    if worker.is_alive():app._generation+=1;worker.join(1)
    final=overlay.events[-1][1] if overlay.events else ''
    rendered=[(t,text) for t,text in overlay.events if 'перший текст' in text]
    emit({'case':case,'gold':gold,'first_text_s':rendered[0][0]-pressed if rendered else None,
      'total_s':time.monotonic()-pressed,'used_transcript':'Питання:' in final,'complete':'✓ Завершено' in final,
      'text':final,'metrics':getattr(app,'last_audio_metrics',None),'transport':getattr(app._realtime,'metrics',None)},phase)
 finally:app.close()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--generate',action='store_true');p.add_argument('--phase',default='baseline')
 p.add_argument('--hedge',type=float,default=.35);p.add_argument('--packet-frames',type=int,default=5);a=p.parse_args()
 generate() if a.generate else main(a.phase,a.hedge,a.packet_frames)
