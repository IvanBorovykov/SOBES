"""Race at most two eligible credentials; commit to the first text-producing stream."""
from contextlib import closing
from dataclasses import dataclass
import queue
import threading
import time

from api_keys import ApiKey, credential_error


@dataclass(frozen=True)
class AnswerChunk:
    entry: ApiKey
    text: str


def first_answer(keys, model, contents, config, cancelled, *, avoid_id=None, after_id=None,
                 hedge_seconds=.18, metrics=None):
    entries = keys.candidates(model)
    if after_id and any(entry.id == after_id for entry in entries):
        available = {entry.id for entry in entries}
        entries = [entry for entry in keys.entries() if entry.id in available]
        index = next(i for i, entry in enumerate(entries) if entry.id == after_id)
        entries = entries[index + 1:] + entries[:index + 1]
    entries.sort(key=lambda entry: entry.id == avoid_id)
    if cancelled():
        return
    if not entries:
        raise ConnectionError(keys.unavailable_message())
    events = queue.Queue(maxsize=64)
    stopped = threading.Event()
    lock = threading.Lock()
    winner = None
    started = time.monotonic()
    stats = metrics if metrics is not None else {}
    stats.update(attempts=0, winner_profile=None, hedge_started=False)

    def put(event):
        while not stopped.is_set() and not cancelled():
            try:
                events.put(event, timeout=.05)
                return
            except queue.Full:
                continue

    def run(entry):
        nonlocal winner
        produced = False
        try:
            if stopped.is_set() or cancelled() or entry not in keys.candidates(model):
                return
            with closing(keys.client(entry).models.generate_content_stream(
                    model=model, contents=contents, config=config)) as stream:
                for chunk in stream:
                    if stopped.is_set() or cancelled():
                        return
                    delta = chunk.text
                    if not delta:
                        continue
                    with lock:
                        if winner is None:
                            winner = entry
                            stats['winner_profile'] = keys.entries().index(entry)
                        if winner != entry:
                            return
                    produced = True
                    put(('text', AnswerChunk(entry, delta)))
            if produced:
                put(('complete', entry))
        except Exception as error:
            if credential_error(error):
                keys.failed(entry, model, error)
            put(('error', (entry, error)))
        finally:
            put(('done', entry))

    def launch(entry):
        stats['attempts'] += 1
        threading.Thread(target=run, args=(entry,), daemon=True, name='text-answer').start()

    finished = 0
    launched = []
    active = set()
    last_error = None
    try:
        while not cancelled():
            if winner is None and len(active) < 2:
                remaining = [entry for entry in entries if entry not in launched]
                eligible = [entry for entry in remaining if not active or not entry.project
                            or all(entry.project != other.project for other in active)]
                if eligible and (not active or time.monotonic() - started >= hedge_seconds):
                    entry = eligible[0]
                    stats['hedge_started'] = stats['hedge_started'] or bool(active)
                    launched.append(entry)
                    active.add(entry)
                    launch(entry)
            try:
                kind, value = events.get(timeout=.01)
            except queue.Empty:
                continue
            if kind == 'text':
                yield value
            elif kind == 'complete':
                return
            elif kind == 'error':
                entry, error = value
                if winner == entry:
                    raise error  # Never replace or replay a partly visible answer.
                last_error = error
            elif kind == 'done':
                finished += 1
                active.discard(value)
                if (finished == len(entries)
                        or (winner is not None and finished == stats['attempts'])):
                    if last_error:
                        raise last_error
                    raise ValueError('Модель не вернула текст ответа')
    finally:
        stopped.set()
