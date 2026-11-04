"""Native frontend auto reply: text, WeChat voice transcript and copied images."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.config import load_config
from core.engine import ReplyEngine
from core.frontend_loop import incoming_tail, new_tail, sequence
from core.llm_openai import build_writer
from core.models import IncomingMessage
from core.wechat_frontend import FrontendUnavailable
from macos.wechat_frontend_map import discover, read
from macos.wechat_frontend_media import run as media_run
from macos.wechat_sticker import capture_sticker
from macos.wechat_vision_agent import clipboard, save


class NativeUI:
    def __init__(self, pid):self.pid = pid

    def focus(self):
        import AppKit
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if app is None:raise FrontendUnavailable('调试微信已退出')
        if not app.isActive():
            app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
            time.sleep(.15)

    def observe(self):return read(self.pid)[0]

    def key(self, code, command=False):
        import AppKit, Quartz
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None or front.processIdentifier() != self.pid:raise FrontendUnavailable('微信失去焦点')
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
            if command:Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def click(self, snapshot, node, **checks):
        import AppKit, Quartz
        point = snapshot.click_point(node, **checks)
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None or front.processIdentifier() != self.pid:raise FrontendUnavailable('微信失去焦点')
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft))

    def send(self, before, text, mark_uncertain):
        fresh = self.observe()
        if fresh.conversation != before.conversation or fresh.window['window_id'] != before.window['window_id'] or sequence(fresh.messages) != sequence(before.messages):
            raise FrontendUnavailable('生成期间会话或消息变化，下轮重新处理')
        fresh.click_point(fresh.input, expected_conversation=before.conversation)
        mark_uncertain()  # Persist BEFORE any draft mutation; never blind-retry.
        self.click(fresh, fresh.input, expected_conversation=before.conversation)
        with clipboard(text):
            self.key(0, command=True);self.key(9, command=True)
            time.sleep(.15)
            staged = self.observe()
            if staged.window['window_id'] != fresh.window['window_id'] or sequence(staged.messages) != sequence(fresh.messages):
                raise FrontendUnavailable('粘贴期间消息或窗口变化，保留草稿并锁定')
            self.click(staged, staged.send, expected_conversation=before.conversation, expected_draft=text)
        for _ in range(3):
            time.sleep(.3)
            after = self.observe()
            if after.conversation != before.conversation:break
            additions = new_tail(sequence(before.messages), after.messages)
            if additions and any(m['direction'] == 'outgoing' and m['text'] == text for m in additions) and not after.draft.strip():return after
        raise FrontendUnavailable('未确认己方新气泡，本会话已锁定，不会自动重发')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--once', action='store_true', help='只观察并建立基线一次，不生成或发送')
    parser.add_argument('--reset-chat')
    args = parser.parse_args()
    os.chdir(ROOT);Path('var').mkdir(exist_ok=True)
    lock = open('var/vision-agent.lock', 'a')  # Same lock as the old OCR entry.
    try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('已有收发机器人运行，请先停止旧实例')
    cfg = load_config('core/config.yaml');cfg.llm.model = 'deepseek-flash';cfg.limits.cross_device_dedup_seconds = 0
    state_path = Path('var/native-state.json' if args.send else 'var/native-preview-state.json')
    state = json.loads(state_path.read_text()) if state_path.exists() else {'baselines': {}, 'pending': {}, 'media': {}}
    state.setdefault('media', {})
    if args.reset_chat:
        state['baselines'].pop(args.reset_chat, None);state['pending'].pop(args.reset_chat, None)
        save(state_path, state);print('已重置会话操作状态，保留聊天记忆。');return
    engine = ReplyEngine(cfg, llm_reply=build_writer(cfg,request_options={'thinking':{'type':'disabled'}}), state_path='var/native-rate.json' if args.send else 'var/native-preview-rate.json')
    pid = discover();ui = NativeUI(pid)
    # Runtime pointers cannot survive a process restart.
    if state.get('pid') != pid:state['baselines'] = {};state['pid'] = pid
    print('鲸鲸原生控件 Agent · '+('真实发送' if args.send else '试运行，不发送'), flush=True)
    print('仅处理当前打开会话；文字/语音转写及收到的图片会交给 DeepSeek。首次观察跳过已有消息。', flush=True)
    last_error = '';last_attempt = 0
    while True:
        try:
            ui.focus();m = ui.observe();title = m.conversation
            if args.once:
                print(json.dumps({'conversation':title,'messages':len(m.messages),'voices':sum(x['kind']=='voice' for x in m.messages),'stickers':sum(x['kind']=='sticker' for x in m.messages),'image_candidates':sum(bool(x['image_candidates']) for x in m.messages),'messages_sent':0},ensure_ascii=False));return
            if state['pending'].get(title, {}).get('phase') == 'uncertain':raise RuntimeError('「'+title+'」上次发送待人工确认，停止此会话自动操作；核对后使用 --reset-chat')
            prev = state['baselines'].get(title)
            batch = new_tail(prev, m.messages)
            if prev is None:
                state['baselines'][title] = sequence(m.messages);save(state_path,state)
                print('「'+title+'」已建立原生消息基线，等待新消息。',flush=True);continue
            if batch is None:raise RuntimeError('消息序列暂时无法对齐，保留原基线')
            incoming = incoming_tail(batch)
            if not incoming:
                state['baselines'][title] = sequence(m.messages);save(state_path,state);continue
            classes = {n.get('qt_class') for n in m.nodes if n.get('visible')}
            is_group = 'mmui::ChatTitleBarChatRoomView' in classes
            if not is_group and 'mmui::ChatTitleBarChatSingleView' not in classes:
                raise RuntimeError('无法确认当前会话类型')
            if is_group and title != '幸福快乐一家亲':
                state['baselines'][title]=sequence(m.messages);save(state_path,state);continue
            if time.monotonic()-last_attempt < 5:continue
            last_attempt = time.monotonic()
            key = hashlib.sha256(json.dumps(sequence(m.messages),ensure_ascii=False).encode()).hexdigest()
            pending = state['pending'].get(title)
            if not pending or pending['key'] != key:
                parts = [];image_paths = []
                for msg in incoming:
                    if msg['kind'] == 'text':parts.append(msg['text']);continue
                    cache_key = hashlib.sha256(json.dumps(['direct-media-v3',pid,title,key,msg['node_id']],ensure_ascii=False).encode()).hexdigest()
                    cached = state['media'].get(cache_key)
                    if cached:
                        if isinstance(cached,dict):
                            image_paths.extend(cached.get('paths') or [cached['path']]);parts.append('[好友表情包；附图为控件画面采样，非原始GIF]' if cached.get('kind')=='sticker' else '[好友发送了一张图片，见附图]')
                        else:parts.append(cached)
                        continue
                    output = ROOT/'var/native-media'/ (cache_key+'.json')
                    if msg['kind'] == 'voice':
                        result = media_run(pid,title,'transcribe',output,target_row=msg['node_id'])
                        if not result.get('transcript'):raise RuntimeError('语音转写尚未完成，稍后重试')
                        value = '[语音转写] '+result['transcript']
                    elif msg['image_candidates']:
                        result = media_run(pid,title,'open-image',output,target_row=msg['node_id'])
                        value = {'path':result['attachment']['path']}
                        image_paths.append(value['path'])
                    elif msg['kind']=='sticker':
                        result=capture_sticker(pid,title,msg['node_id'],output)
                        value={'kind':'sticker','paths':[f['path'] for f in result['frames']]}
                        image_paths.extend(value['paths'])
                    else:raise RuntimeError('暂不支持的消息类型，保留待处理：'+msg['qt_class'])
                    state['media'][cache_key] = value
                    while len(state['media']) > 200:state['media'].pop(next(iter(state['media'])))
                    save(state_path,state);parts.append(('[好友表情包；附图为控件画面采样，非原始GIF]' if value.get('kind')=='sticker' else '[好友发送了一张图片，见附图]') if isinstance(value,dict) else value)
                fresh = ui.observe()
                if fresh.conversation != title or sequence(fresh.messages) != sequence(m.messages):continue
                text = '\n'.join(parts)
                result = engine.decide(IncomingMessage(chat_id=title,chat_name=title,text=text,image_paths=image_paths,sender_name=incoming[-1]['sender'] or title,is_group=is_group,mentioned_me=bool(re.search(r'[@＠]明前茶话弄(?=\s|$)',text)),platform='macos-ocr'))
                if not result.should_reply:
                    print('暂不回复：'+result.reason,flush=True)
                    if not any(w in result.reason for w in ('冷却','频率','失败','限','未返回','未接入')):
                        state['baselines'][title]=sequence(m.messages);save(state_path,state)
                    continue
                pending = {'key':key,'reply':result.text,'phase':'generated'}
                state['pending'][title]=pending;save(state_path,state)
            print('「'+title+'」候选回复：'+pending['reply'],flush=True)
            if args.send:
                def mark_uncertain():
                    pending['phase']='uncertain';save(state_path,state)
                final = ui.send(m,pending['reply'],mark_uncertain)
                state['baselines'][title]=sequence(final.messages)
                print('已确认己方新气泡，发送成功。',flush=True)
            else:state['baselines'][title]=sequence(m.messages)
            state['pending'].pop(title,None);save(state_path,state);last_error=''
        except Exception as error:
            if str(error) != last_error:print('暂停：'+str(error),flush=True);last_error=str(error)
        finally:time.sleep(.7)


if __name__ == '__main__':
    try:main()
    except KeyboardInterrupt:print('\n已停止。')
