import asyncio
from collections import deque
import threading
import time
import unittest
from unittest.mock import Mock

from google.genai import types
from live_transcription import Finalize, LiveTranscription, TranscriptionChannel
from realtime_hints import Channel, Hint


class TranscriptionTests(unittest.TestCase):
    def engine(self):
        e=LiveTranscription.__new__(LiveTranscription)
        e._segments=deque(maxlen=200);e._pending=None;e._condition=threading.Condition()
        e._pcm_pending=bytearray();e._pcm_started=0
        e._interim='';e._interim_at=0
        e.metrics={'audio_packets':0,'pcm_bytes':0,'max_queue_ms':0}
        e._channel=TranscriptionChannel();e._channel.hint=Hint(ready=True)
        e._voice_active=False;e._last_loud=0;e._voiced_frames=0
        e.enabled=True;e._closed=False;e._generation=0;e._on_status=None
        return e

    def message(self, text=None, done=False):
        return types.LiveServerMessage(server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text=text) if text else None,
            generation_complete=done))

    def test_old_completion_before_ordered_marker_cannot_finalize_question(self):
        e=self.engine();t=e._pending=Finalize(time.monotonic(),8)
        e._receive_message(e._channel,self.message('old',True))
        self.assertFalse(t.event.is_set())
        t.sent=True
        e._receive_message(e._channel,self.message('new question',True))
        self.assertTrue(t.event.is_set());self.assertIn('new question',t.text)
        e._receive_message(e._channel,self.message('later speech',True))
        self.assertNotIn('later speech',t.text)

    def test_silent_turn_completes_without_reusing_previous_question(self):
        for internal in (False,True):
            e=self.engine();e._segments.append((time.monotonic(),'previous question'))
            t=e._pending=Finalize(time.monotonic(),8,sent=True,internal=internal)
            e._receive_message(e._channel,self.message(done=True))
            self.assertTrue(t.event.is_set());self.assertEqual(t.text,'')
            self.assertEqual(bool(t.error),not internal)

    def test_question_places_barrier_after_existing_pcm_and_returns_frozen_text(self):
        e=self.engine();e._channel.audio.put((time.monotonic(),b'earlier pcm'))
        answer=[]
        worker=threading.Thread(target=lambda:answer.append(e.question(8,lambda:False)))
        worker.start()
        self.assertEqual(e._channel.audio.get(timeout=1)[1],b'earlier pcm')
        ticket=e._channel.audio.get(timeout=1)[1];self.assertIsInstance(ticket,Finalize)
        ticket.sent=True;e._receive_message(e._channel,self.message('WHERE versus HAVING',True))
        worker.join(1);self.assertEqual(answer,['WHERE versus HAVING'])

    def test_partial_packet_is_flushed_before_keypress_marker_without_padding(self):
        e=self.engine();pcm=b'\x03\x00'*320
        e.offer_audio(pcm)
        self.assertTrue(e._channel.audio.empty())
        answers=[]
        worker=threading.Thread(target=lambda:answers.append(e.question(8,lambda:False)))
        worker.start()
        self.assertEqual(e._channel.audio.get(timeout=1)[1],pcm)
        ticket=e._channel.audio.get(timeout=1)[1]
        ticket.sent=True;e._receive_message(e._channel,self.message('current',True))
        worker.join(1);self.assertEqual(answers,['current'])

    def test_batching_preserves_every_pcm_byte_in_order(self):
        e=self.engine();frames=[bytes([i,0])*320 for i in range(10)]
        for frame in frames:e.offer_audio(frame)
        self.assertEqual(e._channel.audio.qsize(),2)
        sent=b''.join(e._channel.audio.get_nowait()[1] for _ in range(2))
        self.assertEqual(sent,b''.join(frames))

    def test_finalization_excludes_segments_outside_audio_window(self):
        e=self.engine();e._segments.append((time.monotonic()-40,'stale question'))
        t=e._pending=Finalize(time.monotonic(),8,sent=True)
        e._receive_message(e._channel,self.message('current question',True))
        self.assertEqual(t.text,'current question')

    def test_disconnect_unblocks_question_and_discards_previous_session_text(self):
        e=self.engine();e._segments.append((time.monotonic(),'old'))
        t=e._pending=Finalize(time.monotonic(),8)
        e._invalidate(e._channel,'disconnected')
        self.assertTrue(t.event.is_set());self.assertEqual(t.error,'disconnected')
        self.assertFalse(e._segments);self.assertFalse(e._channel.hint.ready)

    def test_paused_stream_cannot_return_previous_question(self):
        e=self.engine();e.enabled=False
        with self.assertRaises(ConnectionError):e.question(8,lambda:False)

    def test_continuing_speech_fits_while_waiting_for_finalization_after_reconnect(self):
        e=self.engine();e._channel.reset_audio()
        e._pending=Finalize(time.monotonic(),8,sent=True)
        for _ in range(80):  # 1.6s of continuing speech during a valid hold.
            e.offer_audio(b'\x01\x00'*320)
        self.assertTrue(e._channel.hint.ready)
        self.assertEqual(e._channel.audio.qsize(),16)
        self.assertIsNotNone(e._pending)

    def test_later_pcm_is_not_sent_until_current_question_is_finalized(self):
        async def run():
            e=self.engine();t=e._pending=Finalize(time.monotonic(),8)
            e._channel.audio.put((time.monotonic(),t))
            e._channel.audio.put((time.monotonic(),b'future pcm'))
            sent=[]
            class Session:
                async def send_realtime_input(self,**kwargs):sent.append(kwargs)
            task=asyncio.create_task(e._sender(e._channel,Session(),0))
            try:
                await asyncio.sleep(.05)
                self.assertIn('activity_start',sent[0])
                self.assertIn('activity_end',sent[1])
                self.assertEqual(len(sent),2)
                t.event.set();await asyncio.sleep(.05)
                self.assertIn('activity_start',sent[2])
                self.assertEqual(sent[3]['audio'].data,b'future pcm')
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
        asyncio.run(run())

if __name__=='__main__':unittest.main()
