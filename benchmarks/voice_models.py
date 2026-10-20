"""Reproducible audio-only model survey. Synthetic speech only, no mic/screens.
Run with .venv/bin/python benchmarks/voice_models.py --phase rest|live|finalists
Credentials are read from this app's Keychain; never serialized.
"""
import argparse
import asyncio
import json
import random
import sys
import time
import wave
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google.genai import types
from api_keys import KeyPool, KeychainStore
from audio_payload import prepare_audio

ROOT = Path(__file__).resolve().parent
INSTRUCTION = ('Ти — голосовий помічник. Відповідай українською на останнє запитання з аудіо. '
               'Почни одразу з точної відповіді, без вступу і повторення питання. Максимум два короткі речення. '
               'Не відповідай на попереднє запитання, якщо мовець змінив його.')
PROMPT = 'В аудіо — запитання. Дай відповідь на останнє запитання. Без екрана.'
CASES = {
    'python': ('Чем отличается список от кортежа в Питоне? Ответь коротко.', ('незмін', 'неизмен', 'immutable', 'кортеж ні', 'кортеж — ні')),
    'last_question': ('Сначала хотел спросить про HTTP. Нет, другой вопрос. Сколько будет семнадцать плюс двадцать пять? Ответь коротко.', ('42', 'сорок два')),
    'cache': ('Что такое кэш и зачем он нужен? Объясни коротко.', ('кеш', 'кэш', 'cache')),
}


def audio(case):
    with wave.open(str(ROOT / f'{case}.wav')) as source:
        return source.readframes(source.getnframes())


def judge(case, text):
    text = text.lower()
    return bool(text.strip()) and any(word in text for word in CASES[case][1])


def emit(row, output):
    row['at_utc'] = datetime.now(timezone.utc).isoformat()
    with output.open('a') as target:
        target.write(json.dumps(row, ensure_ascii=False) + '\n')
    shown = {key: value for key, value in row.items() if key not in ('text', 'at_utc')}
    print(json.dumps(shown, ensure_ascii=False), flush=True)


def rest_config(model):
    if '2.5-pro' in model:
        thinking = types.ThinkingConfig(thinking_budget=128)
    elif model == 'gemini-flash-latest':
        thinking = types.ThinkingConfig(thinking_level='low')
    elif '2.5' in model and 'image' not in model:
        thinking = types.ThinkingConfig(thinking_budget=0)
    elif any(s in model for s in ('3.1-pro', '3.7', '3.8', '3.6', '3.5-flash')) and 'lite' not in model:
        thinking = types.ThinkingConfig(thinking_level='low')
    elif ('flash' in model and '3' in model) or model in ('gemini-flash-latest', 'gemini-flash-lite-latest'):
        thinking = types.ThinkingConfig(thinking_level='minimal')
    else:
        thinking = None
    return types.GenerateContentConfig(system_instruction=INSTRUCTION, max_output_tokens=512,
        thinking_config=thinking, http_options=types.HttpOptions(timeout=20_000,
            retry_options=types.HttpRetryOptions(attempts=1)))


def rest_trial(keys, entry, model, case, trial):
    started = time.monotonic(); first = None; text = ''; finish = ''; resolved = None
    row = dict(mode='rest', model=model, case=case, trial=trial)
    try:
        payload = prepare_audio(audio(case))
        row['prepare_s'] = time.monotonic()-started
        row['bytes'] = len(payload.data); row['mime'] = payload.mime_type
        request_at = time.monotonic()
        with closing(keys.client(entry).models.generate_content_stream(model=model,
                contents=[types.Part.from_bytes(data=payload.data, mime_type=payload.mime_type), PROMPT],
                config=rest_config(model))) as stream:
            for chunk in stream:
                resolved = chunk.model_version or resolved
                if chunk.candidates and chunk.candidates[0].finish_reason:
                    finish = str(chunk.candidates[0].finish_reason)
                # chunk.text can include thoughts in some SDK/model combinations. Exclude explicitly.
                parts = chunk.candidates[0].content.parts if chunk.candidates and chunk.candidates[0].content else []
                delta = ''.join(part.text or '' for part in (parts or []) if not part.thought)
                if delta:
                    first = first or time.monotonic()
                    text += delta
                if time.monotonic() - request_at > 25:
                    raise TimeoutError('Total stream exceeded 25 seconds')
        row.update(status='ok' if text else 'empty', first_text_s=first-started if first else None,
                   api_first_text_s=first-request_at if first else None, total_s=time.monotonic()-started,
                   correct=judge(case,text), text=text, finish=finish, resolved_model=resolved)
    except Exception as error:
        row.update(status='error', error=keys.redact(error)[:1200], error_type=type(error).__name__,
                   total_s=time.monotonic()-started, first_text_s=first-started if first else None,
                   correct=False, text=text)
    return row


def live_config(model):
    transcribe = 'transcribe' in model
    text_output = transcribe or 'robotics' in model
    config = types.LiveConnectConfig(response_modalities=['TEXT'] if text_output else ['AUDIO'],
        system_instruction=None if transcribe else INSTRUCTION,
        output_audio_transcription=None if text_output else types.AudioTranscriptionConfig(),
        input_audio_transcription=types.AudioTranscriptionConfig() if transcribe else None,
        realtime_input_config=types.RealtimeInputConfig(activity_handling=types.ActivityHandling.NO_INTERRUPTION,
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=False,
                prefix_padding_ms=20, silence_duration_ms=350)))
    if 'extended-thinking' in model:
        config.thinking_config = types.ThinkingConfig(thinking_level='low')
    elif '3.1' in model:
        config.thinking_config = types.ThinkingConfig(thinking_level='minimal')
    elif '2.5' in model:
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)
    return config


async def live_model(keys, entry, model, cases, output, mode='live', flush=True):
    connect_at=time.monotonic()
    try:
        async with asyncio.timeout(12):
            context = keys.client(entry).aio.live.connect(model=model, config=live_config(model))
            session = await context.__aenter__()
    except Exception as error:
        emit(dict(mode=mode, model=model, case='setup', trial=0, status='error', correct=False,
                  total_s=time.monotonic()-connect_at,error_type=type(error).__name__,error=keys.redact(error)[:1200]),output)
        return
    setup_s=time.monotonic()-connect_at
    try:
        for trial, case in enumerate(cases):
            pcm=audio(case); text=''; first=None; complete=False; last_voice=None; end_at=None; seen_input=''
            row=dict(mode=mode,model=model,case=case,trial=trial,setup_s=setup_s,flush=flush)
            async def send():
                nonlocal last_voice,end_at
                start=time.monotonic()
                for offset in range(0,len(pcm),640):
                    part=pcm[offset:offset+640].ljust(640,b'\x00')
                    # Same RMS threshold as existing app, recorded as a proxy, not true word-end.
                    from array import array
                    samples=array('h',part)
                    if sum(x*x for x in samples)/len(samples)>250**2:last_voice=time.monotonic()
                    await session.send_realtime_input(audio=types.Blob(data=part,mime_type='audio/pcm;rate=16000'))
                    await asyncio.sleep(max(0,start+(offset+640)/32000-time.monotonic()))
                end_at=time.monotonic()
                if flush:
                    await session.send_realtime_input(audio_stream_end=True)
                while not complete:
                    await session.send_realtime_input(audio=types.Blob(data=bytes(640),mime_type='audio/pcm;rate=16000'))
                    await asyncio.sleep(.02)
            async def receive():
                nonlocal text,first,complete,seen_input
                while not complete:
                    got=False
                    async for message in session.receive():
                        got=True; c=message.server_content
                        if not c:continue
                        if c.input_transcription and c.input_transcription.text:
                            seen_input+=c.input_transcription.text
                            if 'transcribe' in model:
                                first=first or time.monotonic();text+=c.input_transcription.text
                                if end_at is not None:complete=True;return
                        delta=c.output_transcription.text if c.output_transcription else ''
                        if not delta and c.model_turn:
                            delta=''.join(p.text or '' for p in c.model_turn.parts or [] if not p.thought)
                        if delta:first=first or time.monotonic();text+=delta
                        if c.interrupted:raise RuntimeError('Server interrupted answer')
                        if c.turn_complete:complete=True;return
                    if not got:raise ConnectionError('Empty receive')
            started=time.monotonic()
            try:
                async with asyncio.timeout(len(pcm)/32000+20):
                    await asyncio.gather(send(),receive())
                row.update(status='ok' if text else 'empty',correct=judge(case,text),text=text,complete=complete,
                           first_text_s=first-end_at if first and end_at else None,
                           last_voice_to_text_s=first-last_voice if first and last_voice else None,
                           total_s=time.monotonic()-end_at if end_at else None,
                           audio_s=len(pcm)/32000)
            except Exception as error:
                row.update(status='error',correct=False,text=text,error_type=type(error).__name__,
                           error=keys.redact(error)[:1200],total_s=time.monotonic()-started,
                           first_text_s=first-end_at if first and end_at else None)
                emit(row,output);break
            emit(row,output)
            await asyncio.sleep(.25)
    finally:
        try:await asyncio.wait_for(context.__aexit__(None,None,None),3)
        except Exception:pass


def classify(name, model):
    if 'bidiGenerateContent' in model.get('supported_actions',[]):return 'live'
    if 'generateContent' not in model.get('supported_actions',[]):return 'not_question_answering_endpoint'
    if any(s in name for s in ('tts','lyria','nano-banana')):return 'generation_not_audio_question_answering'
    return 'rest'


async def main(args):
    keys=KeyPool(KeychainStore()); entries=keys.entries()
    if not entries:raise RuntimeError('No saved credentials')
    inventory=json.loads((ROOT/'model_inventory.json').read_text())['models']
    output=ROOT/args.output
    try:
        classified={name:classify(name,d['model']) for name,d in inventory.items()}
        (ROOT/'scope.json').write_text(json.dumps(classified,indent=2))
        if args.phase in ('rest','finalists'):
            models=[name.removeprefix('models/') for name,kind in classified.items() if kind=='rest'] if not args.models else args.models
            random.Random(2718).shuffle(models)
            sem=asyncio.Semaphore(args.concurrency)
            async def run(model):
                for trial in range(args.repeats):
                    case=list(CASES)[trial%len(CASES)]
                    async with sem:
                        row=await asyncio.to_thread(rest_trial,keys,entries[0],model,case,trial)
                        emit(row,output)
                    if row['status']=='error' and any(code in row.get('error','') for code in ('400 INVALID','404 NOT_FOUND','429 RESOURCE')):break
            await asyncio.gather(*(run(model) for model in models))
        else:
            models=[name.removeprefix('models/') for name,kind in classified.items() if kind=='live'] if not args.models else args.models
            random.Random(2718).shuffle(models)
            for model in models:
                await live_model(keys,entries[0],model,[list(CASES)[i%len(CASES)] for i in range(args.repeats)],
                                 output,mode=args.phase,flush=not args.no_flush)
    finally:keys.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['rest','live','finalists','live-finalists'],required=True)
    parser.add_argument('--models',nargs='*');parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--concurrency',type=int,default=2)
    parser.add_argument('--no-flush',action='store_true');parser.add_argument('--output',default='survey.jsonl')
    asyncio.run(main(parser.parse_args()))
