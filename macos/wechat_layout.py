"""浅色微信窗口版面检测：从连续分隔线与圆角面板边界定位，不读取微信进程。"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import argparse
import json
from PIL import Image, ImageDraw, ImageFont

@dataclass
class Region:
    name: str
    label: str
    box: tuple[int,int,int,int]
    method: str


def bands(values, max_gap=1):
    result=[]
    for value in sorted(set(values)):
        if not result or value>result[-1][-1]+max_gap: result.append([value])
        else: result[-1].append(value)
    return result


def detect_layout(image):
    im=image.convert('RGB');w,h=im.size;p=im.load()
    if w<500 or h<400: raise ValueError('图像太小，需要完整微信主窗口截图')
    step=max(2,h//320)
    ys=range(int(h*.15),int(h*.75),step)
    vertical=[]
    for x in range(int(w*.04),int(w*.65)):
        support=sum(sum(abs(p[x-1,y][c]-p[x+1,y][c]) for c in range(3))>14 for y in ys)/len(ys)
        if support>.65:vertical.append(x)
    splits=[round(sum(b)/len(b)) for b in bands(vertical)]
    candidates=[(a,b) for a in splits for b in splits if a<b and .04*w<a<.16*w and .2*w<b<.5*w and b-a>.14*w]
    if len(candidates)!=1: raise ValueError(f'无法唯一确定侧栏/会话分隔线：{splits}')
    nav,right=candidates[0]
    xs=range(right+int(w*.025),w-int(w*.025),max(2,w//440))
    def horizontal(y,threshold=14):
        return sum(sum(abs(p[x,y-1][c]-p[x,y+1][c]) for c in range(3))>threshold for x in xs)/len(xs)
    frame_lines=[y for y in range(int(h*.5),int(h*.9)) if horizontal(y)>.85]
    bs=bands(frame_lines,max_gap=max(3,round(h*.003)))
    if len(bs)!=1: raise ValueError('回复面板上边框不明确，拒绝猜测')
    top=round(sum(bs[0])/len(bs[0]))
    edge_ys=range(top+int(h*.035),int(h*.92),step)
    edges=[]
    for x in range(right+3,w-4):
        score=sum(sum(abs(p[x-1,y][c]-p[x+1,y][c]) for c in range(3))>7 for y in edge_ys)/max(1,len(edge_ys))
        if score>.8: edges.append(x)
    edge_bands=bands(edges)
    lefts=[b for b in edge_bands if b[-1]<right+(w-right)*.15]
    rights=[b for b in edge_bands if right+(w-right)*.85<b[0]<w-int(w*.008)]
    if len(lefts)!=1 or len(rights)!=1:raise ValueError('回复面板左右边框不明确')
    left=round(sum(lefts[0])/len(lefts[0]));end=round(sum(rights[0])/len(rights[0]))
    bottoms=[y for y in range(max(top+30,int(h*.9)),h-int(h*.01)) if horizontal(y,7)>.85]
    bottom_bands=bands(bottoms,max_gap=max(3,round(h*.003)))
    if len(bottom_bands)!=1: raise ValueError('回复面板下边框不明确')
    bottom=round(sum(bottom_bands[0])/len(bottom_bands[0]))
    # 在面板右下寻找近灰色连通区域；过滤小图标，只保留按钮尺寸候选。
    x0=max(left,int(w*.85));y0=max(top,int(h*.9))
    mask=set()
    for y in range(y0,bottom):
        for x in range(x0,end):
            rgb=p[x,y]
            if 232<=min(rgb) and max(rgb)<=239 and max(rgb)-min(rgb)<12:mask.add((x,y))
    components=[]
    while mask:
        first=mask.pop();stack=[first];points=[first]
        while stack:
            x,y=stack.pop()
            for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if point in mask:mask.remove(point);stack.append(point);points.append(point)
        if len(points)<100:continue
        xx=[v[0] for v in points];yy=[v[1] for v in points]
        box=(min(xx),min(yy),max(xx)+1,max(yy)+1)
        bw,bh=box[2]-box[0],box[3]-box[1]
        if .035*w<bw<.12*w and .02*h<bh<.07*h and len(points)/(bw*bh)>.5:components.append(box)
    if len(components)!=1: raise ValueError(f'未唯一检测到灰色发送按钮：{components}; frame={left,top,end,bottom}')
    button=components[0]
    # 标题分隔线强度较浅，仍需在聊天区横贯大部分宽度。
    header_lines=[y for y in range(int(h*.04),int(h*.14)) if horizontal(y,3)>.9]
    header_bands=bands(header_lines,max_gap=max(3,round(h*.003)))
    if len(header_bands)!=1: raise ValueError('聊天标题栏分隔线不明确')
    header=round(sum(header_bands[0])/len(header_bands[0]))
    # 输入区与工具栏之间没有可见分隔线，依据按钮顶部划分保守的文字输入范围。
    inset_x=max(4,round(w*12/1760));inset_y=max(4,round(h*12/1280))
    toolbar=max(top+round(h*20/1280),button[1]-inset_y)
    return [
      Region('contacts','联系人列表',(nav,header,right,h),'连续垂直分隔线'),
      Region('header','会话标题栏',(right,0,w,header),'横向标题分隔线'),
      Region('messages','聊天记录区',(right,header,w,top),'标题线与回复面板上边框'),
      Region('reply_panel','回复面板',(left,top,end,bottom),'圆角面板上下左右边界'),
      Region('input','文字输入区',(left+inset_x,top+inset_y,end-inset_x,toolbar-round(h*8/1280)),'面板内缩；下边界为工具栏保守估计'),
      Region('toolbar','回复工具栏',(left,toolbar,end,bottom),'发送按钮顶部定位，边界为估计'),
      Region('send','发送按钮',button,'灰色圆角连通区域'),
    ]


def save_analysis(path,output):
    source=Image.open(path).convert('RGB');regions=detect_layout(source);w,h=source.size
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    records=[{**asdict(r),'normalized_box':[round(v/(w if i%2==0 else h),5) for i,v in enumerate(r.box)]} for r in regions]
    (output/'regions.json').write_text(json.dumps({'image_size':[w,h],'coordinate_convention':'x1,y1,x2,y2; right/bottom exclusive','regions':records},ensure_ascii=False,indent=2))
    overlay=Image.new('RGBA',source.size,(0,0,0,0));draw=ImageDraw.Draw(overlay)
    fonts=['/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/STHeiti Light.ttc']
    font=next((ImageFont.truetype(f,20) for f in fonts if Path(f).exists()),ImageFont.load_default())
    colors=['#1476df','#8d3fb7','#05a5a5','#f3a000','#238b2e','#b44ad9','#e74040']
    for n,(r,color) in enumerate(zip(regions,colors),1):
        x1,y1,x2,y2=r.box;draw.rectangle((x1,y1,x2-1,y2-1),outline=color,width=4)
        # 嵌套面板标签分散摆放，避免互相覆盖。
        tx=x1+6;ty=y1+6
        if r.name=='reply_panel':tx=x2-215;ty=y1-28
        if r.name=='send':tx=x1-20;ty=y1-27
        label=f'{n} {r.label}'
        box=draw.textbbox((tx,ty),label,font=font);draw.rectangle((box[0]-3,box[1]-3,box[2]+3,box[3]+3),fill=color)
        draw.text((tx,ty),label,font=font,fill='white')
    Image.alpha_composite(source.convert('RGBA'),overlay).convert('RGB').save(output/'layout-annotated.png')
    print(json.dumps({'size':[w,h],'regions':records},ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('image');parser.add_argument('--output',default='artifacts/layout-test');args=parser.parse_args();save_analysis(args.image,args.output)
