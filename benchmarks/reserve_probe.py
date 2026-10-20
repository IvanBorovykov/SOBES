"""Actual audio-only REST reserve path, without opening a Live session."""
import sys,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import assistant
from benchmarks.option_q_probe import Overlay,Audio
from benchmarks.voice_models import ROOT,audio,emit,judge
from api_keys import KeyPool,KeychainStore
for model in ['gemini-2.5-flash','gemini-3-flash-preview']:
    keys=KeyPool(KeychainStore());overlay=Overlay();source=Audio();app=assistant.Assistant(overlay,source,keys)
    try:
        with patch.object(assistant,'AUDIO_PRIMARY_MODEL',model),patch.object(assistant,'AUDIO_FALLBACK_MODEL',model),patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)):
            for case in ['python','last_question','cache']:
                source.pcm=audio(case);overlay.events=[];start=time.monotonic()
                app._run_live(app._generation,time.perf_counter(),audio_only=True)
                rendered=[(t,text) for t,text in overlay.events if 'перший текст' in text]
                final=overlay.events[-1][1] if overlay.events else ''
                emit(dict(mode='app-reserve',model=model,case=case,status='ok' if '✓ Завершено' in final else 'error',
                          first_text_s=rendered[0][0]-start if rendered else None,total_s=time.monotonic()-start,
                          correct=judge(case,final),text=final),ROOT/'app_reserve.jsonl')
    finally:app.close()
