"""微信 4.x 实验性本地 OCR 模式：仅处理前台当前会话，不切换聊天。"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import Quartz
import Vision
from Foundation import NSURL
from AppKit import NSWorkspace, NSPasteboard, NSPasteboardItem
from core.config import load_config
from core.engine import ReplyEngine
from core.llm_openai import build_writer
from core.models import IncomingMessage, normalize_chat_name

@dataclass
class Text:
    text: str
    x: float
    y: float
    w: float
    h: float


def recognize(path):
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLanguages_(['zh-Hans', 'en-US'])
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), {})
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f'本地 OCR 失败：{error}')
    result = []
    for item in request.results() or []:
        candidate = item.topCandidates_(1)[0]
        if candidate.confidence() < .45:
            continue
        b = item.boundingBox()
        result.append(Text(str(candidate.string()), b.origin.x, 1-b.origin.y-b.size.height, b.size.width, b.size.height))
    return sorted(result, key=lambda t: (t.y, t.x))


def parse(path, rows):
    with Image.open(path) as source:
        image = source.convert('RGB')
    width, height = image.size
    if not 1.25 < width / height < 1.50:
        raise RuntimeError('当前 OCR 布局要求窗口比例接近所提供的 1760×1280 截图，请保持该布局。')
    titles = [t.text for t in rows if .34 < t.x < .70 and .015 < t.y < .07]
    if len(titles) != 1:
        raise RuntimeError('无法唯一识别当前会话标题，本轮跳过。')
    incoming, outgoing_y = [], -1
    for t in rows:
        if not (.10 < t.y < .76 and t.x > .39):
            continue
        cy = min(height-1, int((t.y+t.h/2)*height))
        # 自己的消息使用绿色气泡；若出现更晚的己方气泡，不回复旧的对方消息。
        green = any(g > r+25 and g > b+15 for r,g,b in [image.getpixel((int(x*width),cy)) for x in (.80,.85,.90,.94)])
        if green:
            outgoing_y = max(outgoing_y, t.y)
            continue
        # 对方气泡左边有浅灰底色；只接受位于左侧气泡中的文字，排除时间和系统提示。
        r,g,b = image.getpixel((int(.425*width), cy))
        if .40 < t.x < .50 and 215 <= min(r,g,b) <= max(r,g,b) < 246 and max(r,g,b)-min(r,g,b) < 12:
            incoming.append(t)
    if not incoming or incoming[-1].y <= outgoing_y:
        return titles[0], ''
    block = [incoming[-1]]
    for t in reversed(incoming[:-1]):
        if block[0].y - (t.y+t.h) > .022:
            break
        block.insert(0, t)
    text = '\n'.join(t.text for t in block)
    if any(word in text for word in ('我通过了你的朋友验证请求', '我们可以开始聊天了')):
        text = ''
    return titles[0], text


WECHAT_APP_IDS = {'com.tencent.xinWeChat', 'com.tencent.flue.WeChatAppEx'}


def is_wechat_app(app):
    return app is not None and str(app.bundleIdentifier()) in WECHAT_APP_IDS


def select_window(windows, main_pids):
    # 新版微信的前台进程可能是 WeChatAppEx，窗口却归主进程所有。
    candidates = [w for w in windows if w.get('kCGWindowOwnerPID') in main_pids
                  and w.get('kCGWindowLayer') == 0
                  and w.get('kCGWindowBounds', {}).get('Width', 0) > 700]
    if len(candidates) != 1:
        raise RuntimeError('微信主窗口不唯一或未显示，本轮暂停。')
    return candidates[0]


def foreground_info():
    # 无 GUI 事件循环的长驻 Python 进程可能保留 NSWorkspace 的旧状态。
    # 用独立短进程查询当前前台信息，避免依赖通知刷新。
    query = """import json
from AppKit import NSWorkspace
w=NSWorkspace.sharedWorkspace()
a=w.frontmostApplication()
print(json.dumps({'bundle_id':str(a.bundleIdentifier()) if a else '',
'name':str(a.localizedName()) if a else '未知',
'main_pids':[x.processIdentifier() for x in w.runningApplications() or []
if str(x.bundleIdentifier())=='com.tencent.xinWeChat']}))
"""
    result=subprocess.run([sys.executable, '-c', query],capture_output=True,text=True,timeout=5)
    if result.returncode:
        raise RuntimeError('无法查询当前前台应用，本轮暂停。')
    return json.loads(result.stdout)


def activate_wechat():
    # 只激活已经运行的微信，不启动未运行的客户端。
    script = """import sys
from AppKit import NSWorkspace, NSApplicationActivateAllWindows, NSApplicationActivateIgnoringOtherApps
apps=[a for a in NSWorkspace.sharedWorkspace().runningApplications() or []
if str(a.bundleIdentifier())=='com.tencent.xinWeChat']
if not apps:
    sys.exit(2)
app=apps[0]
app.unhide()
if not app.activateWithOptions_(NSApplicationActivateAllWindows | NSApplicationActivateIgnoringOtherApps):
    sys.exit(3)
"""
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=5)
    if result.returncode == 2:
        raise RuntimeError('未找到微信进程，请先打开微信并登录。')
    if result.returncode:
        raise RuntimeError('找到微信进程，但切换前台失败，请手动点击微信窗口。')


def ensure_wechat_foreground(auto_focus=True):
    info=foreground_info()
    if info['bundle_id'] in WECHAT_APP_IDS:
        return info
    if not auto_focus:
        raise RuntimeError(f"微信不是前台应用（当前：{info['name']}），本轮暂停。")
    if not info['main_pids']:
        raise RuntimeError('未找到微信进程，请先打开微信并登录。')
    activate_wechat()
    for _ in range(5):
        time.sleep(.2)
        info=foreground_info()
        if info['bundle_id'] in WECHAT_APP_IDS:
            print('已将微信切换到前台。',flush=True)
            return info
    raise RuntimeError('微信尚未切到前台，本轮暂停并稍后重试。')


def capture(path, auto_focus=True):
    info = ensure_wechat_foreground(auto_focus)
    main_pids = set(info['main_pids'])
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    window = select_window(windows, main_pids)
    path.unlink(missing_ok=True)
    result = subprocess.run(['screencapture','-x','-o','-l',str(window['kCGWindowNumber']),str(path)],capture_output=True)
    if result.returncode or not path.exists():
        raise RuntimeError('窗口截图失败，请给终端开启“录屏与系统录音”权限并重开终端。')
    return window


def send_text(window, text):
    # 调用前已重新截图核对会话及最后一条消息；使用窗口全局坐标点击输入区。
    bounds = window['kCGWindowBounds']
    point = (bounds['X']+bounds['Width']*.65, bounds['Y']+bounds['Height']*.83)
    if foreground_info()['bundle_id'] not in WECHAT_APP_IDS:
        raise RuntimeError('发送前微信失去前台焦点，取消发送。')
    pasteboard = NSPasteboard.generalPasteboard()
    saved = [[(str(kind), item.dataForType_(kind)) for kind in item.types()] for item in pasteboard.pasteboardItems() or []]
    try:
        pasteboard.clearContents()
        pasteboard.setString_forType_(text, 'public.utf8-plain-text')
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None,kind,point,Quartz.kCGMouseButtonLeft))
        time.sleep(.15)
        # 输入框若存在草稿，在此模式中不主动清空；调用方检测到可见草稿会取消发送。
        subprocess.run(['osascript','-e','tell application "System Events" to keystroke "v" using command down'],check=True,capture_output=True)
        time.sleep(.2)
        subprocess.run(['osascript','-e','tell application "System Events" to key code 36'],check=True,capture_output=True)
    finally:
        pasteboard.clearContents()
        items=[]
        for types in saved:
            item=NSPasteboardItem.alloc().init()
            for kind,data in types:
                if data is not None: item.setData_forType_(data,kind)
            items.append(item)
        if items: pasteboard.writeObjects_(items)


def has_input_draft(rows):
    # 微信空输入框中的占位提示不是用户草稿。
    placeholders={'按住鼠标语音输入文字'}
    return any(.36<t.x<.95 and .79<t.y<.90
               and re.sub(r'\s+','',t.text) not in placeholders for t in rows)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image', help='仅在已有截图上离线检查，不连接模型或微信')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--no-auto-focus', action='store_true', help='不自动把微信切到前台')
    args=parser.parse_args()
    if args.image:
        title,text=parse(args.image,recognize(args.image))
        print('当前会话：',title,'\n可回复文字：',text or '无（旧系统提示或无对方新消息）')
        return
    os.chdir(Path(__file__).resolve().parents[1])
    cfg=load_config('core/config.yaml')
    engine=ReplyEngine(cfg,llm_reply=build_writer(cfg),state_path='var/ocr-state.json' if args.send else 'var/ocr-preview-state.json')
    print('OCR 模式 v6（修复低置信文字过滤与占位提示）：检测到微信进程后自动切到前台，仅处理当前打开的聊天。',flush=True)
    print('真实发送已开启。' if args.send else '试运行：只输出候选回复，不发送。',flush=True)
    print('截图只用于本地识别，只有识别出的消息文字会发送到 DeepSeek。',flush=True)
    baseline,seen={},set()
    with tempfile.TemporaryDirectory(prefix='whalegirl-ocr-') as folder:
        path=Path(folder)/'window.png'
        last_error=''
        last_status_at=0
        while True:
            try:
                window=capture(path, auto_focus=not args.no_auto_focus)
                rows=recognize(path)
                title,text=parse(path,rows)
                if last_error:
                    print(f'已恢复识别微信，当前会话「{title}」。',flush=True)
                    last_error=''
                if title not in baseline:
                    baseline[title]=text
                    print(f'已记录「{title}」当前消息基线（已有文字：{text[:60] or "无"}）。请现在从另一账号发一条不同的新消息。',flush=True)
                    continue
                if not text or baseline[title]==text:
                    if time.monotonic()-last_status_at > 15:
                        print(f'正在监测「{title}」；末条对方文字：{text[:60] or "无"}，等待不同的新消息。',flush=True)
                        last_status_at=time.monotonic()
                    continue
                baseline[title]=text
                fingerprint=hashlib.sha256((title+'\0'+text).encode()).hexdigest()
                if fingerprint in seen: continue
                seen.add(fingerprint)
                is_group=normalize_chat_name(title)=='幸福快乐一家亲' or bool(re.search(r'[（(]\d+[)）]$',title))
                if is_group and normalize_chat_name(title)!='幸福快乐一家亲': continue
                mention=bool(re.search(r'[@＠]明前茶话弄(?=\s|$)',text))
                decision_started=time.monotonic()
                decision=engine.decide(IncomingMessage(chat_id=title,chat_name=title,text=text,is_group=is_group,mentioned_me=mention,platform='macos-ocr'))
                if not decision.should_reply:
                    print('跳过：',decision.reason,flush=True);continue
                print(f'「{title}」候选回复：{decision.text}',flush=True)
                if not args.send: continue
                # 引擎从消息决策开始计时，模型生成期间已消耗的时间不应重复等待。
                remaining=max(0.0,decision.delay_seconds-(time.monotonic()-decision_started))
                if remaining:
                    print(f'发送间隔尚需等待 {remaining:.1f} 秒。',flush=True)
                    time.sleep(remaining)
                fresh_window=capture(path, auto_focus=not args.no_auto_focus);fresh=recognize(path)
                if parse(path,fresh)!=(title,text) or fresh_window['kCGWindowNumber']!=window['kCGWindowNumber']:
                    print('会话或消息已改变，取消发送。',flush=True);continue
                if has_input_draft(fresh):
                    print('检测到输入区草稿，取消发送。',flush=True);continue
                send_text(fresh_window,decision.text)
                print('已执行发送；请在微信核对投递结果。',flush=True)
            except Exception as error:
                message=str(error)
                if message!=last_error:
                    print(message,flush=True);last_error=message
            finally:
                time.sleep(1)

if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('\n已停止。')
