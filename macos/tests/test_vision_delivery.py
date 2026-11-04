import unittest
from unittest.mock import Mock,patch
from core.desktop_vision import Observation
def sample(texts=(('other','你好'),),draft=''):
    return {'chat_title':'时平','is_group':False,'confidence':.95,'messages_box':[.34,.08,1,.77],
            'input_box':[.36,.80,.97,.91],'send_box':[.92,.93,.97,.97],
            'input_text':draft,'send_enabled':bool(draft),
            'messages':[{'sender':sender,'kind':'text','text':text,'box':[.43,.15+i*.08,.8,.20+i*.08]} for i,(sender,text) in enumerate(texts)]}
from macos.wechat_vision_agent import send_verified

class DeliveryTests(unittest.TestCase):
    def test_network_failure_before_paste_preserves_generated_state(self):
        from http.client import RemoteDisconnected
        before=Observation.parse(sample());vision=Mock()
        vision.observe.side_effect=RemoteDisconnected('closed')
        window={'kCGWindowNumber':42,'kCGWindowBounds':{}}
        marker=Mock()
        with patch('macos.wechat_vision_agent.capture',return_value=window),patch('macos.wechat_vision_agent.paste') as paste,patch('macos.wechat_vision_agent.click') as click:
            with self.assertRaisesRegex(RuntimeError,'发送前复核（尚未粘贴）失败'):
                send_verified(vision,None,window,before,'收到',True,marker)
            marker.assert_not_called();paste.assert_not_called();click.assert_not_called()

    def test_network_failure_after_paste_never_clicks_send(self):
        from http.client import RemoteDisconnected
        before=Observation.parse(sample());vision=Mock()
        vision.observe.side_effect=[before,RemoteDisconnected('closed')]
        window={'kCGWindowNumber':42,'kCGWindowBounds':{}}
        marker=Mock()
        with patch('macos.wechat_vision_agent.capture',return_value=window),patch('macos.wechat_vision_agent.paste') as paste,patch('macos.wechat_vision_agent.click') as click:
            with self.assertRaisesRegex(RuntimeError,'尚未点击发送'):
                send_verified(vision,None,window,before,'收到',True,marker)
            marker.assert_called_once();paste.assert_called_once();click.assert_not_called()

    def test_checked_paste_and_send_and_new_bubble(self):
        before=Observation.parse(sample())
        staged=Observation.parse(sample(draft='收到'))
        final=Observation.parse(sample((('other','你好'),('self','收到'))))
        vision=Mock();vision.observe.side_effect=[before,staged,final]
        window={'kCGWindowNumber':42,'kCGWindowBounds':{'X':0,'Y':0,'Width':880,'Height':640}}
        marker=Mock()
        with patch('macos.wechat_vision_agent.capture',return_value=window),patch('macos.wechat_vision_agent.paste') as paste,patch('macos.wechat_vision_agent.click') as click,patch('macos.wechat_vision_agent.time.sleep'):
            self.assertEqual(send_verified(vision,None,window,before,'收到',True,marker),final)
            marker.assert_called_once();paste.assert_called_once();click.assert_called_once_with(window,staged.send_box)

    def test_changed_conversation_never_pastes(self):
        before=Observation.parse(sample());data=sample();data['chat_title']='另一个人'
        vision=Mock();vision.observe.return_value=Observation.parse(data)
        window={'kCGWindowNumber':42,'kCGWindowBounds':{}}
        with patch('macos.wechat_vision_agent.capture',return_value=window),patch('macos.wechat_vision_agent.paste') as paste:
            with self.assertRaises(RuntimeError):send_verified(vision,None,window,before,'收到',True,Mock())
            paste.assert_not_called()

    def test_wrong_draft_never_clicks_send(self):
        before=Observation.parse(sample());wrong=Observation.parse(sample(draft='别人的草稿'))
        vision=Mock();vision.observe.side_effect=[before,wrong]
        window={'kCGWindowNumber':42,'kCGWindowBounds':{}}
        with patch('macos.wechat_vision_agent.capture',return_value=window),patch('macos.wechat_vision_agent.paste'),patch('macos.wechat_vision_agent.click') as click:
            with self.assertRaises(RuntimeError):send_verified(vision,None,window,before,'收到',True,Mock())
            click.assert_not_called()

class OneShotTests(unittest.TestCase):
    def test_self_test_captures_once_and_never_replies(self):
        import sys
        from macos.wechat_vision_agent import main
        from core.config import Config
        config=Config();config.llm.api_key='test-key'
        vision=Mock();vision.observe.return_value=Observation.parse(sample())
        with patch.object(sys,'argv',['agent','--self-test-once']),patch('macos.wechat_vision_agent.load_config',return_value=config),patch('macos.wechat_vision_agent.VisionClient',return_value=vision),patch('macos.wechat_vision_agent.capture',return_value={'kCGWindowNumber':42}) as capture,patch('macos.wechat_vision_agent.ReplyEngine') as engine,patch('macos.wechat_vision_agent.paste') as paste,patch('macos.wechat_vision_agent.click') as click:
            main()
            capture.assert_called_once();vision.observe.assert_called_once()
            engine.assert_not_called();paste.assert_not_called();click.assert_not_called()

class ImageChangeTests(unittest.TestCase):
    def test_diff_has_no_pillow_deprecation_and_preserves_threshold(self):
        import warnings
        from PIL import Image
        from macos.wechat_vision_agent import changed
        first=Image.new('L',(10,10),0);second=first.copy()
        with warnings.catch_warnings():
            warnings.simplefilter('error',DeprecationWarning)
            self.assertFalse(changed(first,second))
            second.putpixel((0,0),13);self.assertFalse(changed(first,second))
            second.putpixel((1,0),13);self.assertTrue(changed(first,second))
