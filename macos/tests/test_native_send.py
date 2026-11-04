import copy
from contextlib import nullcontext
import time
import unittest
from unittest.mock import patch

from core.tests.test_wechat_frontend import snapshot, message
from core.wechat_frontend import FrontendMap, FrontendUnavailable
from macos.wechat_native_agent import NativeUI


def maps():
    raw = snapshot();raw['captured_at'] = time.time()
    before = FrontendMap(copy.deepcopy(raw))
    raw['windows'][0]['roots'][0]['tree']['children'][2]['children'][0]['properties']['plainText'] = '测试回复'
    staged = FrontendMap(copy.deepcopy(raw))
    raw['windows'][0]['roots'][0]['tree']['children'][2]['children'][0]['properties']['plainText'] = ''
    raw['windows'][0]['roots'][0]['tree']['children'][1]['children'].append(message(300,'测试回复',True))
    return before, staged, FrontendMap(raw)


class FakeUI(NativeUI):
    def __init__(self, observations):
        self.observations = iter(observations);self.events=[]
    def observe(self):return next(self.observations)
    def click(self, m, n, **checks):
        m.click_point(n, **checks);self.events.append('send' if n is m.send else 'input')
    def key(self, code, command=False):self.events.append(('key',code))


class NativeSendTests(unittest.TestCase):
    @patch('macos.wechat_native_agent.clipboard',lambda text:nullcontext())
    @patch('macos.wechat_native_agent.time.sleep',lambda _:None)
    def test_only_success_after_new_outgoing_bubble(self):
        before,staged,after=maps();ui=FakeUI([before,staged,after])
        result=ui.send(before,'测试回复',lambda:ui.events.append('uncertain'))
        self.assertEqual(result,after)
        self.assertEqual(ui.events[0],'uncertain')
        self.assertEqual(ui.events.count('send'),1)

    @patch('macos.wechat_native_agent.clipboard',lambda text:nullcontext())
    @patch('macos.wechat_native_agent.time.sleep',lambda _:None)
    def test_no_bubble_never_blind_retries(self):
        before,staged,_=maps();ui=FakeUI([before,staged,before,before,before])
        with self.assertRaises(FrontendUnavailable):ui.send(before,'测试回复',lambda:ui.events.append('uncertain'))
        self.assertEqual(ui.events.count('send'),1)

    def test_new_incoming_cancels_before_draft_mutation(self):
        before,_,changed=maps();ui=FakeUI([changed])
        with self.assertRaises(FrontendUnavailable):ui.send(before,'测试回复',lambda:ui.events.append('uncertain'))
        self.assertEqual(ui.events,[])

    @patch('macos.wechat_native_agent.clipboard',lambda text:nullcontext())
    @patch('macos.wechat_native_agent.time.sleep',lambda _:None)
    def test_mismatched_staged_text_never_clicks_send(self):
        before,staged,_=maps();staged.draft='其他文字';ui=FakeUI([before,staged])
        with self.assertRaises(FrontendUnavailable):ui.send(before,'测试回复',lambda:ui.events.append('uncertain'))
        self.assertNotIn('send',ui.events)


if __name__=='__main__':unittest.main()
