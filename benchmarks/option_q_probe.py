"""Actual OptionQ request path including Live deadline and REST fallback, synthetic input."""
import sys,time,threading,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import assistant
from benchmarks.voice_models import audio,emit,judge,ROOT
from api_keys import KeyPool,KeychainStore
from audio_payload import prepare_audio

class Overlay:
    def __init__(self):self.events=[]
    def show_text(self,text,**kwargs):self.events.append((time.monotonic(),text))

class Audio:
    def __init__(self):self.pcm=b''
    def subscribe(self,fn):self.callback=fn
    def unsubscribe(self,fn):pass
    def pcm_bytes(self):return self.pcm
    def request_audio(self,pcm=None):return prepare_audio(self.pcm if pcm is None else pcm)

def main(models):
    for model in models:
        keys=KeyPool(KeychainStore());overlay=Overlay();source=Audio()
        app=assistant.Assistant(overlay,source,keys)
        try:
            with patch.object(assistant,'LIVE_MODEL',model),patch.object(assistant,'PRIMARY_MODEL','gemini-2.5-flash'),patch.object(assistant,'FALLBACK_MODEL','gemini-3-flash-preview'),patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)):
                app.start_realtime()
                deadline=time.monotonic()+10
                while not app._realtime._channel.hint.ready and time.monotonic()<deadline:time.sleep(.02)
                if not app._realtime._channel.hint.ready:
                    emit(dict(mode='option-q',model=model,status='error',case='setup',error=app._realtime.status()),ROOT/'option_q.jsonl');continue
                for trial,case in enumerate(['python','last_question','cache','python','last_question','cache']):
                    pcm=audio(case);source.pcm=pcm;overlay.events=[];started=time.monotonic()
                    for off in range(0,len(pcm),640):
                        source.callback(pcm[off:off+640].ljust(640,b'\x00'))
                        time.sleep(max(0,started+(off+640)/32000-time.monotonic()))
                    pressed=time.monotonic()
                    worker=threading.Thread(target=app._run_live,args=(app._generation, time.perf_counter(),False),kwargs={'audio_only':True},daemon=True)
                    worker.start()
                    while worker.is_alive() and time.monotonic()-pressed<35:
                        source.callback(bytes(640));time.sleep(.02)
                    if worker.is_alive():app._generation+=1;worker.join(2)
                    rendered=[(t,text) for t,text in overlay.events if ('Live ·' in text and '\n\n' in text) or ('перший текст' in text and '\n\n' in text)]
                    final=overlay.events[-1][1] if overlay.events else ''
                    first=rendered[0][0]-pressed if rendered else None
                    emit(dict(mode='option-q',model=model,case=case,trial=trial,status='ok' if ('✓ Готово' in final or '✓ Завершено' in final) else 'error',
                      first_text_s=first,total_s=time.monotonic()-pressed,used_live=final.startswith('Live ·'),
                      correct=judge(case,final),text=final),ROOT/'option_q.jsonl')
                    time.sleep(.2)
        finally:app.close()

if __name__ == '__main__':
    main(sys.argv[1:])
