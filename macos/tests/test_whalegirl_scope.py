import json
from unittest.mock import Mock
import pytest
from macos import wechat_mac_bot as bot

@pytest.mark.parametrize('text,expected', [('@鲸鲸\u2005你好', True), ('＠鲸鲸 你好', True), ('@别人 你好', False), ('@鲸鲸同学 你好', False)])
def test_only_test_group_and_exact_mention(monkeypatch, text, expected):
    monkeypatch.setenv('WXAUTO_TEST_GROUPS', json.dumps(['幸福快乐一家亲']))
    monkeypatch.setenv('WXAUTO_BOT_NAME', '鲸鲸')
    calls = []
    def script(source, *args):
        calls.append((source, args))
        return '别的群\n幸福快乐一家亲（3）' if source == bot._LIST_UNREAD else text
    monkeypatch.setattr(bot, 'run_applescript', script)
    engine = Mock()
    engine.decide.return_value = {'should_reply': True, 'text': '你好', 'delay_seconds': 0}
    bot.tick(engine, dry_run=True)
    engine.decide.assert_called_once_with(chat_name='幸福快乐一家亲', text=text, is_group=True, mentioned_me=expected)
    assert [args for source,args in calls if source == bot._OPEN_AND_READ] == [('幸福快乐一家亲（3）',)]
    assert not any(source == bot._SEND for source,args in calls)

def test_missing_scope_does_not_read(monkeypatch):
    monkeypatch.delenv('WXAUTO_TEST_GROUPS', raising=False)
    script = Mock(return_value='幸福快乐一家亲')
    monkeypatch.setattr(bot, 'run_applescript', script)
    engine = Mock()
    bot.tick(engine, dry_run=True)
    assert script.call_count == 1
    engine.decide.assert_not_called()

@pytest.mark.parametrize('private_enabled,expected_count', [('1', 1), ('0', 0)])
def test_private_switch_and_unknown_group_excluded(monkeypatch, private_enabled, expected_count):
    monkeypatch.setenv('WXAUTO_TEST_GROUPS', json.dumps(['幸福快乐一家亲']))
    monkeypatch.setenv('WXAUTO_BOT_NAME', '鲸鲸')
    monkeypatch.setenv('WXAUTO_PRIVATE_ENABLED', private_enabled)
    calls = []
    def script(source, *args):
        calls.append((source, args))
        return '小明\n其他群（8）' if source == bot._LIST_UNREAD else '你好'
    monkeypatch.setattr(bot, 'run_applescript', script)
    engine = Mock()
    engine.decide.return_value = {'should_reply': False, 'reason': '测试'}
    bot.tick(engine, dry_run=True)
    assert engine.decide.call_count == expected_count
    if expected_count:
        engine.decide.assert_called_once_with(chat_name='小明', text='你好', is_group=False, mentioned_me=False)
    assert not any(args == ('其他群（8）',) for source, args in calls)
