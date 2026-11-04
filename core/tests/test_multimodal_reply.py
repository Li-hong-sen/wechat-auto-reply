import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

from core.config import Config
from core.llm_openai import OpenAICompatibleReplyWriter, multimodal_messages
from core.models import IncomingMessage
from core.persistent_memory import PersistentMemory, TokenCounter
from core.persona import Persona


class Counter:
    def __call__(self,text):return len(text)
    def messages(self,messages):return sum(len(x['content'])+1024*len(x.get('image_paths',[])) for x in messages)


class MultimodalTests(unittest.TestCase):
    def test_request_has_real_image_not_description_and_disk_has_only_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'image.jpg';Image.new('RGB',(8,8),'red').save(path)
            memory=PersistentMemory(Path(temp)/'memory',Counter())
            writer=OpenAICompatibleReplyWriter('https://example.invalid','test','deepseek-flash',memory=memory)
            cfg=Config();cfg.persona=Persona(identity='鲸鱼娘',max_chars=100)
            sent=[]
            def post(messages,max_tokens):
                sent.append(multimodal_messages(messages));return '这是一张红色图片。'
            with patch.object(writer,'_post',side_effect=post):
                writer(IncomingMessage('chat','用户','看图',image_paths=[str(path)]),cfg)
                writer(IncomingMessage('chat','用户','刚才是什么颜色？'),cfg)
            self.assertEqual(len(sent),2)  # One reply call per turn; no caption call.
            image=sent[0][-1]['content'][1]
            self.assertEqual(image['type'],'image_url')
            self.assertEqual(base64.b64decode(image['image_url']['url'].split(',')[1]),path.read_bytes())
            self.assertIsInstance(sent[1][1]['content'],list)  # Image retained in recent context.
            disk=next((Path(temp)/'memory').glob('*/聊天.json')).read_text()
            self.assertIn('image_paths',disk);self.assertNotIn('base64',disk)

    def test_counter_accounts_for_image_budget(self):
        counter=object.__new__(TokenCounter)
        class FakeTokenizer:
            def encode(self,text,add_special_tokens=False):
                class Encoding:ids=[1]
                return Encoding()
        counter.tokenizer=FakeTokenizer()
        self.assertEqual(counter.messages([{'content':'x','image_paths':['a','b']}]),16+16+1+2048)

    def test_missing_image_does_not_silently_turn_into_text_reply(self):
        with self.assertRaises(FileNotFoundError):
            multimodal_messages([{'role':'user','content':'看图','image_paths':['/no/such/image.jpg']}])

    def test_image_forbidden_in_assistant_role(self):
        with self.assertRaises(ValueError):
            multimodal_messages([{'role':'assistant','content':'x','image_paths':['x']}])


if __name__=='__main__':unittest.main()
