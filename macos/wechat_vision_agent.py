"""微信视觉收发闭环。模型只返回观察，执行器只运行限定的本地动作。"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageChops
import Quartz
from AppKit import NSPasteboard,NSPasteboardItem
from core.config import load_config
from core.engine import ReplyEngine
from core.llm_openai import build_writer
from core.models import IncomingMessage,normalize_chat_name
from core.desktop_vision import VisionClient,new_messages,incoming_tail,normalized,reply_is_visible
from macos.wechat_ocr_bot import capture,foreground_info,select_window,WECHAT_APP_IDS


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.vision-')
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def same_window(expected,actual):
    return expected['kCGWindowNumber']==actual['kCGWindowNumber'] and dict(expected['kCGWindowBounds'])==dict(actual['kCGWindowBounds'])


def click(window,rect):
    info=foreground_info()
    if info['bundle_id'] not in WECHAT_APP_IDS:raise RuntimeError('微信失去焦点，取消动作')
    current=select_window(Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,0) or [],set(info['main_pids']))
    if not same_window(window,current):raise RuntimeError('微信窗口位置或尺寸变化，取消动作')
    bounds=window['kCGWindowBounds']
    point=(bounds['X']+(rect[0]+rect[2])/2*bounds['Width'],bounds['Y']+(rect[1]+rect[3])/2*bounds['Height'])
    for kind in (Quartz.kCGEventLeftMouseDown,Quartz.kCGEventLeftMouseUp):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap,Quartz.CGEventCreateMouseEvent(None,kind,point,Quartz.kCGMouseButtonLeft))

@contextmanager
def clipboard(text):
    pb=NSPasteboard.generalPasteboard()
    original=[[(str(t),i.dataForType_(t)) for t in i.types()] for i in pb.pasteboardItems() or []]
    pb.clearContents();pb.setString_forType_(text,'public.utf8-plain-text')
    try:yield
    finally:
        pb.clearContents();items=[]
        for values in original:
            item=NSPasteboardItem.alloc().init()
            for kind,data in values:
                if data is not None:item.setData_forType_(data,kind)
            items.append(item)
        if items:pb.writeObjects_(items)


def paste(window,rect,text):
    import subprocess
    click(window,rect)
    with clipboard(text):
        time.sleep(.15)
        subprocess.run(['osascript','-e','tell application "System Events" to keystroke "v" using command down'],capture_output=True,check=True)
        time.sleep(.2)


def changed(before,after):
    if before is None:return True
    diff=ImageChops.difference(before,after)
    return sum(diff.convert('L').histogram()[13:])>=2


def send_verified(vision,path,window,before,reply,auto_focus,mark_uncertain):
    def observe_step(stage):
        print('发送进度：'+stage,flush=True)
        try:
            current_window=capture(path,auto_focus=auto_focus)
            return current_window,vision.observe(path)
        except Exception as error:
            raise RuntimeError(stage+'失败：'+str(error)) from error
    fresh_window,fresh=observe_step('发送前复核（尚未粘贴）')
    if not same_window(window,fresh_window) or fresh.title!=before.title or fresh.signatures!=before.signatures:
        raise RuntimeError('会话/消息/窗口已变化，取消发送；下轮重新观察')
    if fresh.input_text.strip():raise RuntimeError('输入框有草稿，取消发送，不覆盖草稿')
    # 从粘贴开始就记录待人工确认状态；意外中断后不自动重复粘贴或发送。
    mark_uncertain()
    print('发送进度：粘贴回复到输入框',flush=True)
    paste(fresh_window,fresh.input_box,reply)
    staged_window,staged=observe_step('核对输入框草稿（尚未点击发送）')
    if not same_window(fresh_window,staged_window) or staged.title!=before.title or staged.signatures!=before.signatures or normalized(staged.input_text)!=normalized(reply) or not staged.send_enabled:
        raise RuntimeError('输入文字/会话/发送按钮未验证通过，保留草稿并停止本会话自动操作')
    print('发送进度：点击发送按钮',flush=True)
    click(staged_window,staged.send_box)
    for _ in range(2):
        time.sleep(.4)
        final_window,final=observe_step('回读确认己方新气泡')
        if not same_window(staged_window,final_window) or final.title!=before.title:
            break
        if reply_is_visible(final,reply,before.signatures):return final
    raise RuntimeError('未确认己方新气泡，停止本会话自动操作；不会自动重发')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--send',action='store_true')
    parser.add_argument('--self-test-once',action='store_true',help='只捕获一次当前微信窗口并上传DeepSeek识别，随后退出，不生成或发送回复')
    parser.add_argument('--reset-chat',help='人工核对后，清除此会话待确认状态并重新建立基线；不删除聊天记忆')
    parser.add_argument('--no-auto-focus',action='store_true')
    parser.add_argument('--image',help='只分析一张选定图片，会上传到 DeepSeek，不操作微信')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];os.chdir(root)
    cfg=load_config('core/config.yaml');cfg.llm.model='deepseek-flash'
    vision=VisionClient(cfg)
    if args.self_test_once:
        if args.send:
            parser.error('--self-test-once 不允许与 --send 一起使用')
        with tempfile.TemporaryDirectory(prefix='whalegirl-selftest-') as temp:
            path=Path(temp)/'window.png'
            window=capture(path,auto_focus=not args.no_auto_focus)
            started=time.monotonic()
            obs=vision.observe(path)
            print(json.dumps({'self_test':'ok','window_id':window['kCGWindowNumber'],
                              'title':obs.title,'confidence':obs.confidence,
                              'visible_messages':len(obs.messages),'input_box':obs.input_box,
                              'send_box':obs.send_box,'has_draft':bool(obs.input_text),
                              'vision_seconds':round(time.monotonic()-started,2),
                              'wechat_messages_sent':0},ensure_ascii=False,indent=2))
        return
    if args.image:
        print(vision.observe(args.image));return
    statepath=Path('var/vision-state.json' if args.send else 'var/vision-preview-state.json')
    state=json.loads(statepath.read_text()) if statepath.exists() else {'baselines':{},'pending':{}}
    # 消息序列层负责去重，不再按正文丢弃新发出的同文气泡。
    cfg.limits.cross_device_dedup_seconds=0
    engine=ReplyEngine(cfg,llm_reply=build_writer(cfg),state_path='var/vision-rate.json' if args.send else 'var/vision-preview-rate.json')
    print('微信视觉 Agent v9 · deepseek-flash · '+('真实发送' if args.send else '试运行，不发送'),flush=True)
    print('微信窗口截图会上传 DeepSeek 用于识别，按用户加载摘要+最近历史。仅处理当前打开的会话。',flush=True)
    print('首次进入会话建立基线，跳过已有消息；Ctrl+C停止。',flush=True)
    Path('var').mkdir(exist_ok=True)
    lock=open('var/vision-agent.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('另一个视觉Agent已在运行，请先停止旧实例')
    if args.reset_chat:
        state['pending'].pop(args.reset_chat,None)
        state['baselines'].pop(args.reset_chat,None)
        save(statepath,state)
        print('已重置会话操作状态，聊天记忆保持原样。');lock.close();return
    lastimage=None;lastobserve=0;cached=None;lastlog='';lastskip=0;lastattempt=0
    with lock,tempfile.TemporaryDirectory(prefix='whalegirl-vision-') as temp:
        path=Path(temp)/'window.png'
        while True:
            try:
                window=capture(path,auto_focus=not args.no_auto_focus)
                with Image.open(path) as image:
                    thumb=image.convert('L').resize((160,100))
                if changed(lastimage,thumb) or time.monotonic()-lastobserve>30:
                    cached=vision.observe(path);lastimage=thumb;lastobserve=time.monotonic()
                    print(f'观察「{cached.title}」：{len(cached.messages)}个可见气泡，置信度{cached.confidence:.2f}',flush=True)
                obs=cached
                previous=state['baselines'].get(obs.title)
                batch=new_messages(previous,obs)
                if batch is None and previous is not None:
                    reason=f'「{obs.title}」视觉结果暂时无法与历史对齐，保留原基线，等待下一次有效观察。'
                    if reason!=lastlog:print(reason,flush=True);lastlog=reason
                    continue
                if batch is None:
                    state['baselines'][obs.title]=obs.signatures;save(statepath,state)
                    print(f'「{obs.title}」已建立消息序列基线，等待新消息。',flush=True);continue
                incoming=incoming_tail(batch)
                if not incoming:
                    state['baselines'][obs.title]=obs.signatures;save(statepath,state);continue
                if obs.is_group and normalize_chat_name(obs.title)!='幸福快乐一家亲':
                    state['baselines'][obs.title]=obs.signatures;save(statepath,state);continue
                pending=state['pending'].get(obs.title)
                if pending and pending['phase']=='uncertain':
                    raise RuntimeError(f'「{obs.title}」上次操作结果不确定，已锁定；请核对微信与 var/vision-state.json 后人工处理')
                text='\n'.join(m.text if m.kind=='text' else f'[{m.kind}：{m.text}]' for m in incoming)
                if all(m.kind in ('voice','file') for m in incoming):
                    print('语音/文件内容尚未接入，跳过本批消息。',flush=True)
                    state['baselines'][obs.title]=obs.signatures;save(statepath,state);continue
                key=hashlib.sha256(json.dumps(obs.signatures,ensure_ascii=False).encode()).hexdigest()
                started=time.monotonic()
                if not pending or pending['key']!=key:
                    if time.monotonic()-lastattempt<5:continue
                    lastattempt=time.monotonic()
                    result=engine.decide(IncomingMessage(chat_id=obs.title,chat_name=obs.title,text=text,sender_name=incoming[-1].sender_name or obs.title,is_group=obs.is_group,mentioned_me=bool(re.search(r'[@＠]明前茶话弄(?=\s|$)',text)),platform='macos-ocr'))
                    if not result.should_reply:
                        if time.monotonic()-lastskip>10:print('暂不回复：'+result.reason,flush=True);lastskip=time.monotonic()
                        continue
                    pending={'key':key,'reply':result.text,'phase':'generated','delay':max(0,result.delay_seconds-(time.monotonic()-started))}
                    state['pending'][obs.title]=pending;save(statepath,state)
                print(f'「{obs.title}」候选回复：{pending["reply"]}',flush=True)
                if not args.send:
                    state['baselines'][obs.title]=obs.signatures;state['pending'].pop(obs.title,None);save(statepath,state);continue
                if pending['delay']>0:time.sleep(min(pending['delay'],3));pending['delay']=0
                def mark_uncertain():
                    pending['phase']='uncertain';save(statepath,state)
                final=send_verified(vision,path,window,obs,pending['reply'],not args.no_auto_focus,mark_uncertain)
                state['baselines'][obs.title]=final.signatures
                state['pending'].pop(obs.title,None);save(statepath,state)
                cached=None;lastimage=None
                print('已回读确认：正确会话中出现己方回复气泡。',flush=True)
                lastlog=''
            except Exception as error:
                if str(error)!=lastlog:print('暂停：'+str(error),flush=True);lastlog=str(error)
            finally:time.sleep(1)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\n已停止。')
