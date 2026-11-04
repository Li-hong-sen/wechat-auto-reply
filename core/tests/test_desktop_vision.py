import copy
import json
from unittest.mock import Mock,patch
import pytest
from core.desktop_vision import Observation,VisionClient,new_messages,incoming_tail,reply_is_visible

def sample(texts=(('other','你好'),),draft=''):
    return {'chat_title':'时平','is_group':False,'confidence':.95,'messages_box':[.34,.08,1,.77],
            'input_box':[.36,.80,.97,.91],'send_box':[.92,.93,.97,.97],
            'input_text':draft,'send_enabled':bool(draft),
            'messages':[{'sender':sender,'kind':'text','text':text,'box':[.43,.15+i*.08,.8,.20+i*.08]} for i,(sender,text) in enumerate(texts)]}

def observe(texts):return Observation.parse(sample(texts))

def test_baseline_and_unchanged():
    obs=observe((('other','你好'),))
    assert new_messages(None,obs) is None
    assert new_messages(obs.signatures,obs)==[]

def test_repeated_identical_text_is_new_bubble():
    old=observe((('other','你好'),))
    new=observe((('other','你好'),('other','你好')))
    assert [m.text for m in new_messages(old.signatures,new)]==['你好']

def test_consecutive_messages_and_question_mark_preserved():
    old=observe((('other','旧问题'),('self','旧回答')))
    new=observe((('other','旧问题'),('self','旧回答'),('other','能查天气吗'),('other','?')))
    assert [m.text for m in incoming_tail(new_messages(old.signatures,new))]==['能查天气吗','?']

def test_scroll_overlap_and_no_overlap():
    old=observe((('other','a'),('self','b'),('other','c')))
    assert len(new_messages(old.signatures,observe((('self','b'),('other','c'),('other','d')))))==1
    assert new_messages(old.signatures,observe((('other','完全不同的屏幕'),))) is None

@pytest.mark.parametrize('field,value', [('confidence',.4),('input_box',[0,0,1,1]),('send_box',[-1,.9,1,1]),('send_box',[0,.9,1,1]),('is_group','false'),('messages',{})])
def test_invalid_observation_rejected(field,value):
    data=sample();data[field]=value
    with pytest.raises(ValueError):Observation.parse(data)

def test_only_new_outgoing_bubble_proves_delivery():
    old=observe((('self','收到'),('other','你好')))
    assert not reply_is_visible(old,'收到',old.signatures)
    new=observe((('self','收到'),('other','你好'),('self','收到')))
    assert reply_is_visible(new,'收到',old.signatures)
    draft=sample((('other','你好'),('self','收到')),draft='收到')
    assert not reply_is_visible(Observation.parse(draft),'收到',old.signatures)

def test_vision_request_uses_actual_image_blocks(tmp_path):
    from core.config import Config,LLMSettings
    path=tmp_path/'window.png';path.write_bytes(b'test-png')
    client=VisionClient(Config(llm=LLMSettings(api_key='test-key')))
    client.client._post=Mock(return_value=json.dumps(sample()))
    result=client.observe(path)
    assert result.title=='时平'
    request=client.client._post.call_args.args[0]
    assert request[1]['content'][1]['type']=='image_url'
    assert request[1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert client.client._model=='deepseek-flash'

def test_vision_retries_disconnected_read_without_changing_image(tmp_path):
    from http.client import RemoteDisconnected
    from core.config import Config,LLMSettings
    path=tmp_path/'window.png';path.write_bytes(b'test-png')
    client=VisionClient(Config(llm=LLMSettings(api_key="test-key")))
    client.client._post=Mock(side_effect=[RemoteDisconnected('closed'),TimeoutError('timeout'),json.dumps(sample())])
    with patch('core.desktop_vision.time.sleep'):
        assert client.observe(path).title=='时平'
    assert client.client._post.call_count==3
    assert all(call==client.client._post.call_args_list[0] for call in client.client._post.call_args_list)

def test_vision_disconnect_retries_are_bounded(tmp_path):
    from http.client import RemoteDisconnected
    from core.config import Config,LLMSettings
    path=tmp_path/'window.png';path.write_bytes(b'test-png')
    client=VisionClient(Config(llm=LLMSettings(api_key="test-key")));client.client._post=Mock(side_effect=RemoteDisconnected('closed'))
    with patch('core.desktop_vision.time.sleep'),pytest.raises(RemoteDisconnected):client.observe(path)
    assert client.client._post.call_count==3

def test_invalid_vision_json_is_not_retried(tmp_path):
    from core.config import Config,LLMSettings
    path=tmp_path/'window.png';path.write_bytes(b'test-png')
    client=VisionClient(Config(llm=LLMSettings(api_key="test-key")));client.client._post=Mock(return_value='not json')
    with pytest.raises(ValueError):client.observe(path)
    client.client._post.assert_called_once()

def test_private_sender_name_variation_does_not_reset_baseline():
    old=observe((('other','你好'),('self','收到'))).signatures
    old[0][3]='时平';old[1][3]='明前茶话弄'
    new=observe((('other','你好'),('self','收到'),('other','新的问题')))
    assert [m.text for m in new_messages(old,new)]==['新的问题']

def test_punctuation_variation_in_old_text_does_not_drop_new_message():
    old=observe((('other','介绍一下自己'),('self','你好……我是鲸鱼娘。')))
    new=observe((('other','介绍一下自己'),('self','你好......我是鲸鱼娘。'),('other','?')))
    assert [m.text for m in new_messages(old.signatures,new)]==['?']

def test_missing_middle_old_bubble_can_align_with_two_anchors():
    old=observe((('other','a'),('self','b'),('other','c'),('self','d')))
    new=observe((('other','a'),('other','c'),('self','d'),('other','new')))
    assert [m.text for m in new_messages(old.signatures,new)]==['new']

def test_question_and_exclamation_are_distinct():
    old=observe((('other','?'),))
    assert new_messages(old.signatures,observe((('other','!'),))) is None
