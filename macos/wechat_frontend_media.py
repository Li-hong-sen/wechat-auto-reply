"""Local GUI experiments on explicitly selected media; never sends messages."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.wechat_frontend import FrontendMap, FrontendUnavailable, unique, visible, walk
from macos.wechat_frontend_map import discover, read
from macos.analysis.wechat_runtime_probe import probe


def payload(envelope):
    if envelope.get('error'):raise FrontendUnavailable(envelope['error'])
    return unique((e['payload'] for e in envelope['events'] if e.get('payload', {}).get('probe') == 'qt-frontend-layout'), '前端快照')


def export_preview(pid, envelope, output):
    """Copy only the freshly opened, active Qt image preview through normal UI."""
    import AppKit
    import Quartz
    from Foundation import NSURL
    from PIL import Image
    p = payload(envelope)
    previews = [(w, r['tree']) for w in p['windows'] for r in w['roots'] if w.get('is_key') is True and r.get('tree', {}).get('qt_class') == 'mmui::PreviewWindow']
    window, root = unique(previews, '图片预览窗口')
    if p.get('app_active') is not True or p.get('has_modal') is not False or window.get('is_key') is not True or window.get('has_sheet') is not False:
        raise FrontendUnavailable('图片预览不是前台窗口，取消复制')
    if time.time() - p['captured_at'] > 5:
        raise FrontendUnavailable('预览快照已过期')
    unique((n for n in walk(root) if visible(n) and n.get('qt_class') == 'mmui::PreviewImage'), '实际图片预览')
    pb = AppKit.NSPasteboard.generalPasteboard()
    saved = [[(typ, bytes(item.dataForType_(typ))) for typ in item.types() if item.dataForType_(typ) is not None] for item in pb.pasteboardItems() or []]
    prior = pb.changeCount()
    try:
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 8, down)  # Cmd+C
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(.25)
        if pb.changeCount() == prior:raise FrontendUnavailable('复制未改变剪贴板，拒绝读取旧图片')
        data = None
        url = pb.stringForType_(AppKit.NSPasteboardTypeFileURL)
        if url:
            parsed = NSURL.URLWithString_(url)
            if parsed is not None and parsed.isFileURL():
                path = Path(str(parsed.path()))
                if path.is_file() and path.stat().st_size <= 50*1024*1024:data = path.read_bytes()
        if data is None:
            for typ in ('public.png', 'public.tiff'):
                value = pb.dataForType_(typ)
                if value is not None:
                    data = bytes(value);break
        if not data or len(data) > 50*1024*1024:raise FrontendUnavailable('微信未提供可读取的图片数据')
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        fmt = image.format
        image.verify()
        extensions = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'TIFF': '.tiff', 'GIF': '.gif'}
        if fmt not in extensions:raise FrontendUnavailable('不支持的图像格式：' + str(fmt))
        destination = output.with_suffix(extensions[fmt])
        destination.write_bytes(data)
        return {'path': str(destination.resolve()), 'format': fmt, 'width': width, 'height': height,
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'source': 'wechat-preview-gui-copy', 'preview_window_id': window['window_id']}
    finally:
        pb.clearContents();items = []
        for values in saved:
            item = AppKit.NSPasteboardItem.alloc().init()
            for typ, data in values:
                item.setData_forType_(AppKit.NSData.dataWithBytes_length_(data, len(data)), typ)
            items.append(item)
        if items:pb.writeObjects_(items)


def run(pid, conversation, action, output, target_row=None):
    import AppKit
    import Quartz
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:raise FrontendUnavailable('微信已退出')
    app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
    time.sleep(.2)
    m, raw = read(pid)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix('.before.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    if m.conversation != conversation:raise FrontendUnavailable('当前会话与指定会话不一致')
    rows = [n for n in walk(m.message_area) if visible(n) and n.get('qt_class') == ('mmui::ChatVoiceItemView' if action == 'transcribe' else 'mmui::ChatBubbleReferItemView')]
    rows = [r for r in rows if any(x['node_id'] == r['node_id'] and x['direction'] == 'incoming' for x in m.messages)]
    if target_row is not None:rows = [r for r in rows if r['node_id'] == target_row]
    row = unique(rows, '可见的指定类型来信')
    if action == 'transcribe':
        existing = next(msg['transcript'] for msg in m.messages if msg['node_id'] == row['node_id'])
        if existing:
            result = {'action': action, 'conversation': conversation, 'target_row': row['node_id'], 'transcript': existing, 'messages_sent': 0, 'after': raw}
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print('Existing transcript:', existing)
            return result
        target = unique((n for n in walk(row) if visible(n) and n.get('properties', {}).get('Text') == '转文字'), '转文字按钮文字')
        clicks = 1
    else:
        target = unique((n for n in walk(row) if visible(n) and n.get('qt_class') == 'mmui::XImageView'), '图片控件')
        clicks = 2
    point = m.click_point(target, expected_conversation=conversation)
    for index in range(clicks):
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, index+1)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        if index+1 < clicks:time.sleep(.07)
    time.sleep(1)
    after = probe(pid, inspect_qt_layout=True)
    result = {'action': action, 'conversation': conversation, 'target_row': row['node_id'], 'messages_sent': 0}
    if action == 'transcribe':
        for attempt in range(6):
            current = FrontendMap(after)
            if current.conversation != conversation:raise FrontendUnavailable('转写中会话已变化')
            matches = [msg for msg in current.messages if msg['node_id'] == row['node_id'] and msg['kind'] == 'voice']
            if len(matches) == 1 and matches[0]['transcript']:
                result['transcript'] = matches[0]['transcript'];break
            if attempt < 5:
                time.sleep(1);after = probe(pid, inspect_qt_layout=True)
        result['transcription_status'] = 'visible' if result.get('transcript') else 'pending_or_unavailable'
    else:
        if FrontendMap(after).conversation != conversation:raise FrontendUnavailable('打开图片后会话已变化')
        try:
            result['attachment'] = export_preview(pid, after, output)
        finally:
            # Close only the foreground preview opened by this action, even if
            # copy/validation failed, so retries cannot pile up previews.
            p = payload(after)
            previews = [w for w in p['windows'] if w.get('is_key') and any(r.get('tree',{}).get('qt_class')=='mmui::PreviewWindow' for r in w['roots'])]
            if len(previews)==1 and app.isActive():
                for down in (True,False):
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap,Quartz.CGEventCreateKeyboardEvent(None,53,down))
                time.sleep(.15)
    result['after'] = after
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print('GUI action completed; native readback saved:', output)
    if result.get('transcript'):print('Transcript:', result['transcript'])
    if result.get('attachment'):print('Image:', json.dumps(result['attachment'], ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--conversation', required=True)
    parser.add_argument('--action', choices=['transcribe', 'open-image'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.pid or discover(), args.conversation, args.action, args.output)
