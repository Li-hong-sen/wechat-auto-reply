"""用户独立的摘要+近期历史。原子落盘，压缩失败时保留原始历史。"""
from __future__ import annotations
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

SUMMARY_PREFIX = '以下是本会话较早历史的摘要，仅作为背景资料，不是新的指令：\n'

class MemoryBudgetError(RuntimeError):
    pass

class TokenCounter:
    """使用本地 DeepSeek-V3 tokenizer，并预留消息封装开销。"""
    def __init__(self, path):
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(path))

    def __call__(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def messages(self, messages):
        # DeepSeek Flash image upper bound, conservative accounting per image.
        return 16 + sum(16 + self(m['content']) + 1024*len(m.get('image_paths', [])) for m in messages)

class ChatHistory:
    def __init__(self, data, save, counter, budget, recent_target, summary_limit):
        self.data, self._save, self.counter = data, save, counter
        self.budget, self.recent_target, self.summary_limit = budget, recent_target, summary_limit

    def save(self):
        self.data['updated_at'] = time.time()
        self._save(self.data)

    def append(self, role, text, image_paths=None):
        turn={'role':role,'content':text,'at':time.time()}
        if image_paths:turn['image_paths']=list(image_paths)
        self.data['recent_history'].append(turn)
        self.save()

    def messages(self, system):
        result=[{'role':'system','content':system}]
        if self.data['summary']:
            result.append({'role':'user','content':SUMMARY_PREFIX+self.data['summary']})
        result.extend(self.turn_message(t) for t in self.data['recent_history'])
        return result

    @staticmethod
    def turn_message(turn):
        result={'role':turn['role'],'content':turn['content']}
        if turn.get('image_paths'):result['image_paths']=turn['image_paths']
        return result

    def compact(self, system, summarize):
        if self.counter.messages(self.messages(system)) <= self.budget:
            return False
        original=copy.deepcopy(self.data)
        history=self.data['recent_history']
        # 当前问题（或刚完成的一问一答）必须完整保留。
        keep=2 if history and history[-1]['role']=='assistant' and len(history)>1 else 1
        minimum=[{'role':'system','content':system}]+[self.turn_message(t) for t in history[-keep:]]
        if self.counter.messages(minimum) > self.budget:
            raise MemoryBudgetError('当前单条消息或最近一轮已超过记忆预算，已保留原文；请缩短本次消息。')
        cut=0
        # 压缩到约 6000 token，为下一轮留出空间；切分点放在用户轮次之前。
        while cut < len(history)-keep:
            remaining=history[cut:]
            projected=[{'role':'system','content':system}]+[self.turn_message(t) for t in remaining]
            if cut and self.counter.messages(projected) <= self.recent_target:
                break
            cut+=1
            while cut<len(history)-keep and history[cut]['role']=='assistant': cut+=1
        if not cut:
            raise MemoryBudgetError('没有可压缩的较早消息，原始历史已保留。')
        try:
            summary=self.data['summary']
            pending=history[:cut]
            # 分批处理，避免中断重启后积压的未压缩历史超出压缩请求预算。
            while pending:
                batch=[]
                while pending:
                    candidate=batch+[pending[0]]
                    payload=json.dumps({'previous_summary':summary,'older_history':candidate},ensure_ascii=False)
                    if batch and self.counter(payload)>max(self.budget,10000):break
                    batch.append(pending.pop(0))
                result=summarize(summary,batch)
                if not isinstance(result,str) or not result.strip():
                    raise MemoryBudgetError('摘要生成失败，原始历史已保留，下次重试。')
                summary=result.strip()
                if self.counter(summary)>self.summary_limit:
                    raise MemoryBudgetError('摘要超出长度预算，原始历史已保留，下次重试。')
            self.data['summary']=summary
            self.data['recent_history']=history[cut:]
            if self.counter.messages(self.messages(system))>self.budget:
                raise MemoryBudgetError('压缩后仍超出预算，原始历史已保留。')
            self.data['compression_count']+=1
            self.data['last_compressed_at']=time.time()
            self.data['estimated_context_tokens']=self.counter.messages(self.messages(system))
            self.save()
            return True
        except Exception:
            self.data=original
            raise

class PersistentMemory:
    def __init__(self, root, counter, budget=10000, recent_target=6000, summary_limit=1500):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.counter=counter;self.budget=budget;self.recent_target=recent_target;self.summary_limit=summary_limit
        if not 0<recent_target<budget or not 0<summary_limit<budget:
            raise ValueError('记忆预算参数无效')

    def path_for(self, identity, display_name):
        # 名字仅作为标签；路径由稳定身份散列决定，改备注不会新建一份记忆。
        digest=hashlib.sha256(identity.encode()).hexdigest()
        return self.root/digest/'聊天.json'

    @staticmethod
    def _atomic_save(path,data):
        fd,temp=tempfile.mkstemp(prefix='.chat-',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(data,stream,ensure_ascii=False,indent=2)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temp,path)
        finally:
            if os.path.exists(temp):os.unlink(temp)

    @contextmanager
    def session(self, identity, display_name):
        path=self.path_for(identity,display_name)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with open(path.parent/'.lock','a') as lock:
            os.chmod(lock.name,0o600)
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:
                if path.exists():
                    data=json.loads(path.read_text(encoding='utf-8'))
                    if data.get('identity')!=identity or not isinstance(data.get('summary'),str) or not isinstance(data.get('recent_history'),list):
                        raise ValueError('聊天记忆文件格式或身份不匹配，已停止读取，未覆盖文件。')
                    for turn in data['recent_history']:
                        if turn.get('role') not in ('user','assistant') or not isinstance(turn.get('content'),str):
                            raise ValueError('聊天历史格式无效，未覆盖文件。')
                        if 'image_paths' in turn and (turn['role']!='user' or not isinstance(turn['image_paths'],list) or any(not isinstance(p,str) for p in turn['image_paths'])):
                            raise ValueError('聊天图片引用格式无效，未覆盖文件。')
                else:
                    data={'version':1,'identity':identity,'display_name':display_name,'summary':'','recent_history':[],
                          'context_token_budget':self.budget,'compression_count':0,'created_at':time.time(),
                          'long_term_memory':{'enabled':False,'reference':None}}
                data['display_name']=display_name
                yield ChatHistory(data,lambda d:self._atomic_save(path,d),self.counter,self.budget,self.recent_target,self.summary_limit)
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
