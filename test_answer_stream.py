import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from contextlib import closing
from google.genai import errors
from api_keys import ApiKey,KeyPool
from test_api_keys import MemoryStore
from answer_stream import first_answer

class AnswerStreamTests(unittest.TestCase):
    def setUp(self):
        self.entries=[ApiKey(str(i),str(i),'test-secret-for-profile-'+str(i)) for i in range(4)]
        self.keys=KeyPool(MemoryStore(self.entries,'0'))
        self.clients={e.id:Mock() for e in self.entries}
        self.keys.client=Mock(side_effect=lambda entry:self.clients[entry.id])

    def stream(self,**kwargs):
        return first_answer(self.keys,'model',['question'],None,lambda:False,hedge_seconds=.01,**kwargs)

    def test_faster_secondary_wins_and_later_primary_is_never_mixed(self):
        release=threading.Event();closed=threading.Event()
        def slow():
            try:
                release.wait(1);yield SimpleNamespace(text='old slow')
            finally:closed.set()
        self.clients['0'].models.generate_content_stream.return_value=slow()
        self.clients['1'].models.generate_content_stream.return_value=(SimpleNamespace(text=x) for x in ['fast',' answer'])
        try:
            result=list(self.stream())
            self.assertEqual(''.join(c.text for c in result),'fast answer')
            self.assertEqual({c.entry.id for c in result},{'1'})
        finally:
            release.set();self.assertTrue(closed.wait(1))

    def test_four_credential_failures_continue_in_order_until_valid_profile(self):
        for entry in self.entries[:3]:
            self.clients[entry.id].models.generate_content_stream.side_effect=errors.APIError(403,{'message':'denied'})
        self.clients['3'].models.generate_content_stream.return_value=(SimpleNamespace(text=x) for x in ['correct'])
        self.assertEqual(''.join(c.text for c in self.stream()),'correct')
        self.assertEqual([e.id for e in self.keys.candidates('model')],['3'])
        self.assertEqual(len(self.keys.candidates('other')),4)

    def test_partial_stream_error_never_uses_different_answer(self):
        def partial():
            yield SimpleNamespace(text='partial')
            raise RuntimeError('dropped')
        self.clients['0'].models.generate_content_stream.return_value=partial()
        stream=self.stream();self.assertEqual(next(stream).text,'partial')
        with self.assertRaisesRegex(RuntimeError,'dropped'):list(stream)
        self.clients['1'].models.generate_content_stream.assert_not_called()

    def test_automatic_switch_off_starts_only_selected_key(self):
        self.keys.replace(self.entries,'2',False)
        self.clients['2'].models.generate_content_stream.return_value=(SimpleNamespace(text=x) for x in ['selected'])
        result=list(self.stream(avoid_id='2'))
        self.assertEqual(result[0].entry.id,'2')
        for i in ('0','1','3'):self.clients[i].models.generate_content_stream.assert_not_called()

    def test_shared_known_project_is_not_raced_but_remains_sequential_failover(self):
        entries=[ApiKey('a','a','test-first-secret-long','same'),ApiKey('b','b','test-second-secret-long','same')]
        self.keys=KeyPool(MemoryStore(entries,'a'))
        failure=threading.Event();overlapped=[]
        def first():
            time.sleep(.04);failure.set();raise errors.APIError(403,{'message':'denied'});yield
        clients={'a':Mock(),'b':Mock()}
        clients['a'].models.generate_content_stream.return_value=first()
        def second(**kwargs):
            overlapped.append(not failure.is_set());return (SimpleNamespace(text=x) for x in ['works'])
        clients['b'].models.generate_content_stream.side_effect=second
        self.keys.client=lambda e:clients[e.id]
        self.assertEqual(''.join(c.text for c in self.stream()),'works')
        self.assertEqual(overlapped,[False])

    def test_cancelled_request_never_starts_extra_key(self):
        called=[]
        self.assertEqual(list(first_answer(self.keys,'model',[],None,lambda:True)),[])
        self.keys.client.assert_not_called()

    def test_round_robin_uses_all_text_profiles_without_moving_stt_connection(self):
        for client in self.clients.values():
            client.models.generate_content_stream.side_effect=lambda **kwargs:(SimpleNamespace(text='ok') for _ in range(1))
        after=None;order=[]
        for _ in range(4):
            result=list(self.stream(avoid_id='0',after_id=after))
            after=result[0].entry.id;order.append(after)
            self.keys.succeeded(result[0].entry)
        self.assertEqual(order,['1','2','3','1'])

if __name__=='__main__':unittest.main()
