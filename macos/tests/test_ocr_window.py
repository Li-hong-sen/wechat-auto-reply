import unittest
from unittest.mock import Mock
from macos.wechat_ocr_bot import is_wechat_app, select_window

class WindowTests(unittest.TestCase):
    def test_both_wechat_frontends(self):
        for appid in ('com.tencent.xinWeChat', 'com.tencent.flue.WeChatAppEx'):
            self.assertTrue(is_wechat_app(Mock(bundleIdentifier=lambda: appid)))
        self.assertFalse(is_wechat_app(Mock(bundleIdentifier=lambda: 'com.apple.Terminal')))
        self.assertFalse(is_wechat_app(None))

    def test_helper_foreground_does_not_require_helper_window(self):
        main={'kCGWindowOwnerPID':123,'kCGWindowLayer':0,'kCGWindowBounds':{'Width':880},'kCGWindowNumber':42}
        unrelated={'kCGWindowOwnerPID':999,'kCGWindowLayer':0,'kCGWindowBounds':{'Width':900}}
        self.assertEqual(select_window([unrelated,main],{123}),main)

    def test_ambiguous_and_missing_windows_rejected(self):
        w={'kCGWindowOwnerPID':123,'kCGWindowLayer':0,'kCGWindowBounds':{'Width':880}}
        for windows in ([],[w,w]):
            with self.assertRaises(RuntimeError): select_window(windows,{123})

if __name__=='__main__': unittest.main()

class ForegroundRefreshTests(unittest.TestCase):
    def test_each_query_gets_new_process_state(self):
        import json
        from unittest.mock import patch
        from macos.wechat_ocr_bot import foreground_info
        responses=[Mock(returncode=0,stdout=json.dumps({'bundle_id':'com.apple.Terminal','name':'终端','main_pids':[1]})),
                   Mock(returncode=0,stdout=json.dumps({'bundle_id':'com.tencent.flue.WeChatAppEx','name':'微信','main_pids':[1]}))]
        with patch('macos.wechat_ocr_bot.subprocess.run',side_effect=responses) as run:
            self.assertEqual(foreground_info()['name'],'终端')
            self.assertEqual(foreground_info()['name'],'微信')
            self.assertEqual(run.call_count,2)

class AutoFocusTests(unittest.TestCase):
    def test_existing_frontmost_does_not_activate(self):
        from unittest.mock import patch
        from macos.wechat_ocr_bot import ensure_wechat_foreground
        info={'bundle_id':'com.tencent.flue.WeChatAppEx','name':'微信','main_pids':[1]}
        with patch('macos.wechat_ocr_bot.foreground_info',return_value=info), patch('macos.wechat_ocr_bot.activate_wechat') as activate:
            self.assertEqual(ensure_wechat_foreground(),info)
            activate.assert_not_called()

    def test_background_wechat_is_activated_and_rechecked(self):
        from unittest.mock import patch
        from macos.wechat_ocr_bot import ensure_wechat_foreground
        background={'bundle_id':'com.apple.Terminal','name':'终端','main_pids':[1]}
        foreground={'bundle_id':'com.tencent.xinWeChat','name':'微信','main_pids':[1]}
        with patch('macos.wechat_ocr_bot.foreground_info',side_effect=[background,foreground]), patch('macos.wechat_ocr_bot.activate_wechat') as activate, patch('macos.wechat_ocr_bot.time.sleep'):
            self.assertEqual(ensure_wechat_foreground(),foreground)
            activate.assert_called_once()

    def test_missing_wechat_and_manual_mode_do_not_activate(self):
        from unittest.mock import patch
        from macos.wechat_ocr_bot import ensure_wechat_foreground
        for pids,auto in (([],True),([1],False)):
            info={'bundle_id':'com.apple.Terminal','name':'终端','main_pids':pids}
            with patch('macos.wechat_ocr_bot.foreground_info',return_value=info), patch('macos.wechat_ocr_bot.activate_wechat') as activate:
                with self.assertRaises(RuntimeError): ensure_wechat_foreground(auto)
                activate.assert_not_called()

    def test_activation_not_confirmed_blocks_capture(self):
        from unittest.mock import patch
        from macos.wechat_ocr_bot import ensure_wechat_foreground
        info={'bundle_id':'com.apple.Terminal','name':'终端','main_pids':[1]}
        with patch('macos.wechat_ocr_bot.foreground_info',return_value=info), patch('macos.wechat_ocr_bot.activate_wechat'), patch('macos.wechat_ocr_bot.time.sleep'):
            with self.assertRaises(RuntimeError): ensure_wechat_foreground()

class DraftTests(unittest.TestCase):
    def test_empty_input_placeholder_does_not_block_send(self):
        from macos.wechat_ocr_bot import Text,has_input_draft
        self.assertFalse(has_input_draft([Text('按住鼠标 语音输入文字',.365,.796,.3,.03)]))
        self.assertFalse(has_input_draft([]))

    def test_real_draft_still_blocks_send(self):
        from macos.wechat_ocr_bot import Text,has_input_draft
        self.assertTrue(has_input_draft([Text('先别发送',.365,.796,.3,.03)]))
        self.assertTrue(has_input_draft([Text('按住鼠标 语音输入文字',.365,.796,.3,.03),Text('我输入的草稿',.365,.84,.3,.03)]))
