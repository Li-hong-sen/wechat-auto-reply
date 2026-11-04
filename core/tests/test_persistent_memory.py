import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from core.persistent_memory import PersistentMemory, MemoryBudgetError
from core.llm_openai import OpenAICompatibleReplyWriter
from core.config import Config
from core.models import IncomingMessage
from core.persona import Persona

class Counter:
    def __call__(self,text):return len(text)
    def messages(self,messages):return sum(len(m['content'])+5 for m in messages)

def store(tmp_path,**kwargs):
    return PersistentMemory(tmp_path,Counter(),**kwargs)

def test_restart_isolation_and_path_safety(tmp_path):
    memory=store(tmp_path)
    with memory.session('one','../../同名联系人') as chat:
        chat.append('user','我的偏好是绿茶')
    with memory.session('two','../../同名联系人') as chat:
        assert not chat.data['recent_history']
        chat.append('user','我是另一个人')
    restarted=store(tmp_path)
    with restarted.session('one','新备注') as chat:
        assert chat.data['recent_history'][0]['content']=='我的偏好是绿茶'
        assert chat.data['long_term_memory']['enabled'] is False
    assert len(list(tmp_path.glob('*/聊天.json')))==2
    assert memory.path_for('one','新备注')==memory.path_for('one','旧备注')

def test_rollup_preserves_tail_and_existing_summary(tmp_path):
    memory=store(tmp_path,budget=300,recent_target=100,summary_limit=60)
    with memory.session('one','用户') as chat:
        chat.data['summary']='先前偏好：绿茶'
        for n in range(8):chat.append('user' if n%2==0 else 'assistant',str(n)*40)
        old_tail=chat.data['recent_history'][-2:]
        summary=Mock(return_value='滚动摘要：偏好绿茶；保留早期结论')
        assert chat.compact('角色设定',summary)
        assert summary.call_args.args[0]=='先前偏好：绿茶'
        assert chat.data['recent_history'][-2:]==old_tail
        assert chat.counter.messages(chat.messages('角色设定'))<=300
        assert chat.data['compression_count']==1
    with memory.session('one','用户') as chat:assert '绿茶' in chat.data['summary']

@pytest.mark.parametrize('result',[None,'x'*100])
def test_failed_summary_never_deletes_history(tmp_path,result):
    memory=store(tmp_path,budget=200,recent_target=70,summary_limit=40)
    with memory.session('one','用户') as chat:
        for n in range(6):chat.append('user' if n%2==0 else 'assistant','x'*40)
        before=json.loads(memory.path_for('one','用户').read_text())
        with pytest.raises(MemoryBudgetError):chat.compact('角色',lambda *_:result)
        assert json.loads(memory.path_for('one','用户').read_text())==before
        assert chat.data['recent_history']==before['recent_history']

def test_single_oversized_message_is_saved_but_not_truncated(tmp_path):
    memory=store(tmp_path,budget=200,recent_target=70,summary_limit=40)
    with memory.session('one','用户') as chat:
        chat.append('user','x'*300)
        with pytest.raises(MemoryBudgetError):chat.compact('角色',Mock())
    assert len(json.loads(memory.path_for('one','用户').read_text())['recent_history'][0]['content'])==300

def test_corrupt_file_is_not_overwritten(tmp_path):
    memory=store(tmp_path);path=memory.path_for('one','用户');path.parent.mkdir();path.write_text('{broken')
    with pytest.raises(ValueError):
        with memory.session('one','用户'):pass
    assert path.read_text()=='{broken'

def test_writer_restores_history_and_separates_same_name(tmp_path):
    config=Config(persona=Persona(identity='鲸鱼娘',max_chars=100))
    memory=store(tmp_path)
    writer=OpenAICompatibleReplyWriter('https://example.invalid','test-key','test',memory=memory)
    writer._post=Mock(return_value='记住了')
    writer(IncomingMessage(chat_id='user-1',chat_name='同名',text='我喜欢绿茶'),config)
    restarted=OpenAICompatibleReplyWriter('https://example.invalid','test-key','test',memory=store(tmp_path))
    restarted._post=Mock(return_value='你喜欢绿茶')
    restarted(IncomingMessage(chat_id='user-1',chat_name='同名',text='我喜欢什么'),config)
    messages=restarted._post.call_args.args[0]
    assert any(m['content']=='我喜欢绿茶' for m in messages)
    restarted(IncomingMessage(chat_id='user-2',chat_name='同名',text='你好'),config)
    assert not any('绿茶' in m['content'] for m in restarted._post.call_args.args[0])
