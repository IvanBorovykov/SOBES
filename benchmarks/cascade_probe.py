"""Measure Live STT + streamed text LLM; do not confuse transcript with answer."""
import sys,asyncio,time,json
from pathlib import Path
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.voice_models import ROOT,audio,emit,INSTRUCTION,rest_config,judge
from api_keys import KeyPool,KeychainStore
from google.genai import types

async def transcript(client,case):
    pcm=audio(case);first=None;text='';end=None
    async with client.aio.live.connect(model='gemini-3.5-transcribe-live',config=types.LiveConnectConfig(response_modalities=['TEXT'],input_audio_transcription=types.AudioTranscriptionConfig())) as session:
        complete=asyncio.Event()
        async def send():
            nonlocal end
            start=time.monotonic()
            for off in range(0,len(pcm),640):
                await session.send_realtime_input(audio=types.Blob(data=pcm[off:off+640].ljust(640,b'\x00'),mime_type='audio/pcm;rate=16000'))
                await asyncio.sleep(max(0,start+(off+640)/32000-time.monotonic()))
            end=time.monotonic();await session.send_realtime_input(audio_stream_end=True)
            while not complete.is_set():
                await session.send_realtime_input(audio=types.Blob(data=bytes(640),mime_type='audio/pcm;rate=16000'));await asyncio.sleep(.02)
        async def recv():
            nonlocal text,first
            while True:
                async for msg in session.receive():
                    c=msg.server_content
                    if c and c.input_transcription and c.input_transcription.text:
                        text+=c.input_transcription.text;first=first or time.monotonic()
                        if end:complete.set();return
        async with asyncio.timeout(len(pcm)/32000+10):await asyncio.gather(send(),recv())
    return text,first-end

async def main():
    keys=KeyPool(KeychainStore());client=keys.client(keys.entries()[0])
    try:
        for case in ['python','last_question','cache']:
            try:
                text,stt=await transcript(client,case)
            except Exception as e:
                emit(dict(mode='cascade',model='transcribe',case=case,status='error',error=keys.redact(e)[:800]),ROOT/'cascade.jsonl');continue
            for model in ['gemma-4-26b-a4b-it','gemma-4-31b-it','gemini-2.5-flash','gemini-3-flash-preview','gemini-3.1-flash-lite','gemini-3.5-flash-lite','gemini-3.6-flash','gemini-3.8-flash']:
                start=time.monotonic();first=None;answer=''
                try:
                    config=rest_config(model)
                    config.http_options.timeout=10000
                    with closing(client.models.generate_content_stream(model=model,contents='Розпізнана розмова: '+text,config=config)) as stream:
                        for chunk in stream:
                            delta=chunk.text or ''
                            if delta:first=first or time.monotonic();answer+=delta
                    emit(dict(mode='cascade',model='transcribe-live + '+model,case=case,status='ok' if answer else 'empty',
                              stt_s=stt,text_llm_s=first-start if first else None,
                              first_text_s=stt+first-start if first else None,total_s=stt+time.monotonic()-start,
                              text=answer,transcript=text,correct=judge(case,answer)),ROOT/'cascade.jsonl')
                except Exception as e:
                    emit(dict(mode='cascade',model='transcribe-live + '+model,case=case,status='error',
                              stt_s=stt,total_s=stt+time.monotonic()-start,error=keys.redact(e)[:800]),ROOT/'cascade.jsonl')
    finally:keys.close()

asyncio.run(main())
