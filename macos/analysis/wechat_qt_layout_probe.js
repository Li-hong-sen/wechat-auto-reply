'use strict';
// Qt 5 / arm64 frontend layout reader. No chat/kernel API and no UI actions.
if (Process.arch!=='arm64') throw new Error('Only arm64 ABI is validated');
const objc=Process.getModuleByName('libobjc.A.dylib');
const api=(name,result,args)=>new NativeFunction(objc.getExportByName(name),result,args);
const cls=api('objc_getClass','pointer',['pointer']);
const selector=api('sel_registerName','pointer',['pointer']);
const message=objc.getExportByName('objc_msgSend');
const get=new NativeFunction(message,'pointer',['pointer','pointer']);
const num=new NativeFunction(message,'ulong',['pointer','pointer']);
const at=new NativeFunction(message,'pointer',['pointer','pointer','ulong']);
const boolean=new NativeFunction(message,'bool',['pointer','pointer']);
const responds=new NativeFunction(message,'bool',['pointer','pointer','pointer']);
const rect=new NativeFunction(message,[['double','double'],['double','double']],['pointer','pointer']);
const selectors=new Map();
const qtModule=Process.getModuleByName('wechat.dylib');
const ranges=Process.enumerateRanges({protection:'r--',coalesce:true});
const classCache=new Map(),nameCache=new Map();
const propertyCache=new Map();
const helper=Module.load(__QT_UI_HELPER_PATH__);
const readText=new NativeFunction(helper.getExportByName('wx_ui_read_qstring'),'int',
  ['pointer','pointer','int','pointer','int'],{scheduling:'exclusive'});
const readBool=new NativeFunction(helper.getExportByName('wx_ui_read_bool'),'int',
  ['pointer','pointer','int'],{scheduling:'exclusive'});
const textBuffer=Memory.alloc(8192);
const captureTarget=__QT_CAPTURE_TARGET__;
let deadline=Infinity;
function sel(name){if(!selectors.has(name))selectors.set(name,selector(Memory.allocUtf8String(name)));return selectors.get(name);}
function supported(o,name){return !o.isNull()&&responds(o,sel('respondsToSelector:'),sel(name));}
function readable(p,size){
  if(p.isNull())return false;
  let left=0,right=ranges.length-1;
  while(left<=right){
    const mid=(left+right)>>1,r=ranges[mid];
    if(p.compare(r.base)<0)right=mid-1;
    else if(p.compare(r.base.add(r.size))>=0)left=mid+1;
    else return p.add(size).compare(r.base.add(r.size))<=0;
  }
  return false;
}
function classOf(o){
  if(Date.now()>deadline)return null;
  if(classCache.has(o.toString()))return classCache.get(o.toString());
  if(!readable(o,16))return null;
  try {
    const d=o.add(8).readPointer();
    if(!readable(d,48)||!d.add(8).readPointer().equals(o))return null;
    const flags=d.add(32).readU32();
    if(flags&4)return null;
    const table=o.readPointer();if(!readable(table,8))return null;
    const fn=table.readPointer().strip();
    if(fn.compare(qtModule.base)<0||fn.compare(qtModule.base.add(qtModule.size))>=0)return null;
    const cachedName=nameCache.get(fn.toString());
    if(cachedName){
      const info={ptr:o,d,name:cachedName.name,meta:cachedName.meta,is_widget:!!(flags&1),parent:d.add(16).readPointer()};
      classCache.set(o.toString(),info);return info;
    }
    const meta=new NativeFunction(fn,'pointer',['pointer'],{scheduling:'exclusive'})(o);
    if(!readable(meta,24))return null;
    const names=meta.add(8).readPointer(),data=meta.add(16).readPointer();
    if(data.readU32()!==8)return null;
    const index=data.add(4).readU32();if(index>10000)return null;
    const header=names.add(index*24),length=header.add(4).readU32();
    if(length<1||length>200)return null;
    const name=header.add(Number(header.add(16).readS64())).readUtf8String(length);
    if(!/^[A-Za-z_][A-Za-z0-9_:<> ,*]*$/.test(name))return null;
    const info={ptr:o,d,name,meta,is_widget:!!(flags&1),parent:d.add(16).readPointer()};
    classCache.set(o.toString(),info);
    if(d.add(40).readPointer().isNull())nameCache.set(fn.toString(),{name,meta});
    return info;
  } catch(_){return null;}
}
function frontendProperties(info){
  const key=info.meta.toString();let specs=propertyCache.get(key);
  if(!specs){
    specs=[];let meta=info.meta;
    const allow={QObject:['objectName'],QWidget:['windowTitle','accessibleName'],
      'mmui::XTextView':['Text'],'mmui::XOutlineButton':['Text','Disabled'],
      QTextEdit:['plainText'],QPlainTextEdit:['plainText'],QLineEdit:['text','placeholderText'],
      QAbstractButton:['text']};
    for(let depth=0;depth<16&&readable(meta,48);depth++){
      const names=meta.add(8).readPointer(),data=meta.add(16).readPointer();
      if(!readable(data,56)||data.readU32()!==8)break;
      function nameAt(index){
        const h=names.add(index*24),length=h.add(4).readU32();
        if(length>200)throw new Error('Invalid Qt property string');
        return h.add(Number(h.add(16).readS64())).readUtf8String(length);
      }
      const name=nameAt(data.add(4).readU32());
      const count=data.add(24).readU32(),start=data.add(28).readU32();
      if(count>200||start>10000)break;
      if(allow[name]){
        const fn=meta.add(24).readPointer().strip();
        if(fn.compare(qtModule.base)>=0&&fn.compare(qtModule.base.add(qtModule.size))<0){
          for(let i=0;i<count;i++){
            const prop=data.add(4*(start+3*i)),label=nameAt(prop.readU32()),type=prop.add(4).readU32(),flags=prop.add(8).readU32();
            if(allow[name].includes(label)&&(flags&1)&&(type===10||type===1))specs.push({name:label,type,index:i,fn});
          }
        }
      }
      meta=meta.readPointer();
    }
    propertyCache.set(key,specs);
  }
  const values={};
  for(const spec of specs){
    if(Date.now()>deadline)break;
    if(spec.type===10){
      const length=readText(info.ptr,spec.fn,spec.index,textBuffer,4096);
      if(length>=0)values[spec.name]=textBuffer.readUtf16String(length);
    }else values[spec.name]=!!readBool(info.ptr,spec.fn,spec.index);
  }
  return values;
}
function childObjects(info){
  try {
    const list=info.d.add(24).readPointer();
    const allocated=list.add(4).readS32(),begin=list.add(8).readS32(),end=list.add(12).readS32();
    if(begin<0||end<begin||end>allocated||end-begin>2000)return [];
    const result=[];
    for(let i=begin;i<end&&Date.now()<deadline;i++){const child=classOf(list.add(16+i*8).readPointer());if(child)result.push(child);}
    return result;
  } catch(_){return [];}
}
function widgetRect(info,dataOffset){
  if(!info.is_widget)return null;
  try{
    const data=info.ptr.add(dataOffset).readPointer(),r=data.add(20);
    const x=r.readS32(),y=r.add(4).readS32(),right=r.add(8).readS32(),bottom=r.add(12).readS32();
    const w=right-x+1,h=bottom-y+1;
    if(Math.abs(x)>100000||Math.abs(y)>100000||w<0||h<0||w>100000||h>100000)return null;
    const flags=data.add(8).readU32();
    return {rect:[x,y,x+w,y+h],visible:!!(flags&(1<<15)),enabled:!(flags&1)};
  }catch(_){return null;}
}
function renderSticker(windows){
  if(!captureTarget)return;
  function find(n,id){if(n.node_id===id)return n;for(const c of n.children||[]){const v=find(c,id);if(v)return v;}return null;}
  const candidates=[];
  for(const window of windows){
    if(!window.is_key||window.has_sheet||window.minimized)continue;
    for(const root of window.roots){
      if(root.truncated||root.tree.qt_class!=='mmui::MainWindow')continue;
      const row=find(root.tree,captureTarget.row_id);
      if(!row||row.qt_class!=='mmui::ChatBubbleReferItemView'||!row.visible)continue;
      const node=find(row,captureTarget.node_id);
      if(node&&node.qt_class==='mmui::XImage'&&node.visible)candidates.push(node);
    }
  }
  if(candidates.length!==1)throw new Error('Sticker render target unavailable');
  const node=candidates[0],info=classOf(ptr(node.node_id.replace('qt-object:','')));
  let meta=info.meta,method=null;
  for(let depth=0;depth<16&&readable(meta,48);depth++){
    const names=meta.add(8).readPointer(),data=meta.add(16).readPointer();
    function nameAt(i){const h=names.add(i*24),len=h.add(4).readU32();if(len>200)throw new Error('Bad method label');return h.add(Number(h.add(16).readS64())).readUtf8String(len);}
    if(data.readU32()!==8)throw new Error('Unsupported Qt meta revision');
    if(nameAt(data.add(4).readU32())==='QWidget'){
      const count=data.add(16).readU32(),start=data.add(20).readU32();
      if(count>200||start>10000)throw new Error('Bad QWidget metadata');
      for(let i=0;i<count;i++){
        const record=data.add(4*(start+i*5));
        if(nameAt(record.readU32())==='grab'&&record.add(4).readU32()===1){
          const types=data.add(4*record.add(8).readU32());
          if(types.readU32()!==65||types.add(4).readU32()!==19)throw new Error('Unexpected QWidget grab signature');
          method={index:i,fn:meta.add(24).readPointer().strip()};break;
        }
      }
      break;
    }
    meta=meta.readPointer();
  }
  if(!method||method.fn.compare(qtModule.base)<0||method.fn.compare(qtModule.base.add(qtModule.size))>=0)throw new Error('QWidget grab unavailable');
  const buffer=Memory.alloc(16*1024*1024),details=Memory.alloc(12);
  const grab=new NativeFunction(helper.getExportByName('wx_ui_grab_widget'),'int',['pointer','pointer','int','pointer','int','pointer'],{scheduling:'exclusive'});
  const size=grab(info.ptr,method.fn,method.index,buffer,16*1024*1024,details);
  if(size<0)throw new Error('Qt widget rendering failed: '+size);
  send({probe:'qt-widget-render',node_id:node.node_id,row_id:captureTarget.row_id,
    width:details.readS32(),height:details.add(4).readS32(),qt_format:details.add(8).readS32(),source:'qt-qwidget-grab'},buffer.readByteArray(size));
}
const run=new NativeCallback(function(){
  try{
    deadline=Date.now()+2000;
    send({probe:'qt-layout-progress',stage:'main-thread-entered'});
    const app=get(cls(Memory.allocUtf8String('NSApplication')),sel('sharedApplication'));
    const screens=get(cls(Memory.allocUtf8String('NSScreen')),sel('screens'));
    const primary=rect(at(screens,sel('objectAtIndex:'),0),sel('frame'));
    const top=Number(primary[0][1])+Number(primary[1][1]);
    const windows=get(app,sel('windows')),result=[];
    for(let wi=0;wi<Math.min(num(windows,sel('count')),64);wi++){
      const window=at(windows,sel('objectAtIndex:'),wi);
      if(!boolean(window,sel('isVisible')))continue;
      const view=get(window,sel('contentView'));if(!supported(view,'platformWindow'))continue;
      const raw=rect(window,sel('frame'));
      const frame=[Number(raw[0][0]),top-Number(raw[0][1])-Number(raw[1][1]),Number(raw[0][0])+Number(raw[1][0]),top-Number(raw[0][1])];
      const platform=get(view,sel('platformWindow')),platformInfo=classOf(platform);
      send({probe:'qt-layout-progress',stage:'platform-window',platform_class:platformInfo?platformInfo.name:null});
      const candidates=[];
      for(let slot=16;slot<=64;slot+=8){
        try{
          const pointer=platform.add(slot).readPointer();
          for(const adjustment of [0,16]){
            const candidate=classOf(pointer.sub(adjustment));
            if(candidate&&!candidates.some(x=>x.ptr.equals(candidate.ptr)))candidates.push(candidate);
          }
        }catch(_){}
      }
      const roots=[];
      send({probe:'qt-layout-progress',stage:'window-candidates',classes:candidates.map(x=>x.name)});
      for(const qwindow of candidates){
        if(!/Window/.test(qwindow.name))continue;
        for(let slot=32;slot<=128;slot+=8){
          try{
            const widget=classOf(qwindow.ptr.add(slot).readPointer());
            if(!widget||!widget.is_widget||roots.some(x=>x.widget.ptr.equals(widget.ptr)))continue;
            for(const offset of [32,40]){
              const geometry=widgetRect(widget,offset);
              if(geometry&&Math.abs(geometry.rect[2]-geometry.rect[0]-(frame[2]-frame[0]))<4&&Math.abs(geometry.rect[3]-geometry.rect[1]-(frame[3]-frame[1]))<4){
                roots.push({widget,offset});break;
              }
            }
          }catch(_){}
        }
      }
      const output={window_id:Number(num(window,sel('windowNumber'))),frame,
        minimized:!!boolean(window,sel('isMiniaturized')),
        is_key:!!boolean(window,sel('isKeyWindow')),
        has_sheet:!get(window,sel('attachedSheet')).isNull(),
        platform_class:platformInfo?platformInfo.name:null,
        candidate_classes:candidates.map(x=>x.name),roots:[]};
      for(const root of roots){
        const visited=new Set();let total=0,depthLimited=false;
        function walk(info,origin,depth){
          if(depth>48){depthLimited=true;return null;}
          if(total>=2400||Date.now()>deadline||visited.has(info.ptr.toString()))return null;
          visited.add(info.ptr.toString());total++;
          const geometry=widgetRect(info,root.offset);
          const node={node_id:'qt-object:'+info.ptr,qt_class:info.name,is_widget:info.is_widget,children:[]};
          let next=origin;
          if(geometry){
            const r=geometry.rect;
            const x=depth===0?frame[0]:origin[0]+r[0],y=depth===0?frame[1]:origin[1]+r[1];
            node.frame=[x,y,x+r[2]-r[0],y+r[3]-r[1]];node.local_rect=r;
            node.visible=geometry.visible;node.enabled=geometry.enabled;next=[x,y];
            if(!node.visible)return node;
            node.properties=frontendProperties(info);
          }
          for(const child of childObjects(info)){const value=walk(child,next,depth+1);if(value)node.children.push(value);}
          return node;
        }
        const tree=walk(root.widget,[frame[0],frame[1]],0);
        output.roots.push({data_offset:root.offset,total_nodes:total,truncated:depthLimited||total>=2400||Date.now()>deadline,tree});
      }
      result.push(output);
    }
    if(captureTarget){
      try{if(!boolean(app,sel('isActive')))throw new Error('WeChat not active');renderSticker(result);}
      catch(error){send({probe:'qt-widget-render',error:String(error)});}
    }
    send({probe:'qt-frontend-layout',source:'qt5-qobject-qwidget-layout',schema_version:1,
      coordinate_space:'quartz-global-logical-points',captured_at:Date.now()/1000,
      app_active:!!boolean(app,sel('isActive')),has_modal:!get(app,sel('modalWindow')).isNull(),windows:result});
  }catch(error){send({probe:'qt-frontend-layout',error:String(error)});}
},'void',['pointer']);
new NativeFunction(Module.getGlobalExportByName('dispatch_async_f'),'void',['pointer','pointer','pointer'])(
  Module.getGlobalExportByName('_dispatch_main_q'),NULL,run);
