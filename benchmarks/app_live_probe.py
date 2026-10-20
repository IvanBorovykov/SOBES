"""Exercise actual app RealtimeHints with synthetic speech, no computer use."""
import sys, time, json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.voice_models import audio, emit, judge, ROOT
from api_keys import KeyPool,KeychainStore
from realtime_hints import RealtimeHints
from assistant import LIVE_INSTRUCTION
keys=KeyPool(KeychainStore())
try:
 for model in sys.argv[1:]:
  engine=None
  try:
   engine=RealtimeHints(keys,model,LIVE_INSTRUCTION)
   deadline=time.monotonic()+10
   while not engine._channel.hint.ready and time.monotonic()<deadline:time.sleep(.02)
   if not engine._channel.hint.ready:
    emit(dict(mode='app-live',model=model,case='setup',status='error',error=engine.status()),ROOT/'app_live.jsonl');continue
   for trial,case in enumerate(['python','last_question','cache']):
    pcm=audio(case); start=time.monotonic();end=None;final=None;first=None;oldturn=engine._channel.hint.turn
    for i in range(int(len(pcm)/640)+1000):
     off=i*640
     chunk=pcm[off:off+640].ljust(640,b'\x00') if off<len(pcm) else bytes(640)
     engine.offer_audio(chunk)
     if off>=len(pcm) and end is None:end=time.monotonic();engine.request_flush()
     hint=engine._channel.hint
     if hint.turn>oldturn and hint.text:first=first or hint.first_text_at
     if hint.turn>oldturn and hint.complete and hint.text:final=hint;break
     time.sleep(max(0,start+(i+1)*.02-time.monotonic()))
    hint=final or engine._channel.hint
    emit(dict(mode='app-live',model=model,case=case,trial=trial,status='ok' if final else 'timeout',
      first_text_s=first-end if first and end else None,total_s=time.monotonic()-end if end else None,
      complete=bool(final),text=hint.text,correct=judge(case,hint.text),error=keys.redact(hint.error)),ROOT/'app_live.jsonl')
    if not final:break
    time.sleep(.2)
  finally:
   if engine:engine.close()
finally:keys.close()
