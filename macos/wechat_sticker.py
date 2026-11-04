"""Read a located sticker's Qt-rendered pixels; no screen capture or send.

Samples are rendered UI frames, not the original GIF. Requires the authorized
debug copy and version-pinned Qt 5 adapter.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import time

from PIL import Image
from core.wechat_frontend import FrontendMap, FrontendUnavailable, Rect, unique
from macos.wechat_frontend_map import read
from macos.analysis.wechat_runtime_probe import probe


def decode_frame(result, row_id, node_id):
    if result.get('error'):raise FrontendUnavailable(result['error'])
    value=unique((e['payload'] for e in result.get('events',[]) if e.get('payload',{}).get('probe')=='qt-widget-render'),'表情渲染结果')
    if value.get('error'):raise FrontendUnavailable(value['error'])
    if value.get('source')!='qt-qwidget-grab' or value.get('row_id')!=row_id or value.get('node_id')!=node_id:
        raise FrontendUnavailable('表情渲染来源或对象不一致')
    w,h=value.get('width'),value.get('height')
    if type(w) is not int or type(h) is not int or not 1<=w<=2048 or not 1<=h<=2048:
        raise FrontendUnavailable('表情画面尺寸无效')
    rgba=base64.b64decode(value['rgba_base64'],validate=True)
    if len(rgba)!=w*h*4:raise FrontendUnavailable('表情像素长度不一致')
    return Image.frombytes('RGBA',(w,h),rgba)


def capture_sticker(pid, conversation, row_id, output, samples=3):
    if samples not in (1,2,3):raise ValueError('仅允许最多3帧')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    first=read(pid)[0]
    row=unique((m for m in first.messages if m['node_id']==row_id and m['direction']=='incoming' and m['kind']=='sticker'),'表情来信')
    candidate=unique(row['sticker_candidates'],'表情控件');node_id=candidate['node_id']
    target=unique((n for n in first.nodes if n.get('node_id')==node_id),'表情节点')
    first.click_point(target,expected_conversation=conversation)  # Validation only.
    if not Rect.read(first.message_area['frame']).contains(Rect.read(candidate['frame'])):
        raise FrontendUnavailable('表情包未完整显示，请滚动到完整位置')
    frames=[];seen=set()
    for index in range(samples):
        raw=probe(pid,inspect_qt_layout=True,capture_widget={'row_id':row_id,'node_id':node_id})
        if raw.get('error'):
            errors=[e.get('payload',{}).get('error') or e.get('description') for e in raw.get('events',[]) if e.get('payload',{}).get('error') or e.get('type')=='error']
            raise FrontendUnavailable('表情渲染失败：'+'; '.join(x for x in errors if x))
        current=FrontendMap(raw)
        n=unique((n for n in current.nodes if n.get('node_id')==node_id),'当前表情控件')
        if current.window['window_id']!=first.window['window_id'] or current.bounds!=first.bounds or n.get('frame')!=candidate['frame']:
            raise FrontendUnavailable('表情位置或窗口变化，放弃采样')
        current.click_point(n,expected_conversation=conversation)
        image=decode_frame(raw,row_id,node_id);digest=hashlib.sha256(image.tobytes()).hexdigest()
        if digest not in seen:
            dest=output.with_name(output.stem+f'.frame{len(frames)+1}.png')
            image.save(dest);seen.add(digest)
            frames.append({'path':str(dest.resolve()),'width':image.width,'height':image.height,'pixel_sha256':digest})
        if index+1<samples:time.sleep(.25)
    result={'kind':'sticker','source':'qt-qwidget-grab','conversation':conversation,'target_row':row_id,
            'original_animation':False,'sample_count':samples,'frames':frames,'messages_sent':0}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result
