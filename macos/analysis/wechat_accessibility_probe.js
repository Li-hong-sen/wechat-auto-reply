'use strict';
// Read native accessibility properties on the app's main thread. No actions.
const objc = Process.getModuleByName('libobjc.A.dylib');
const objcClass = new NativeFunction(objc.getExportByName('objc_getClass'), 'pointer', ['pointer']);
const selector = new NativeFunction(objc.getExportByName('sel_registerName'), 'pointer', ['pointer']);
const objectClass = new NativeFunction(objc.getExportByName('object_getClass'), 'pointer', ['pointer']);
const className = new NativeFunction(objc.getExportByName('class_getName'), 'pointer', ['pointer']);
const sendAddress = objc.getExportByName('objc_msgSend');
const get = new NativeFunction(sendAddress, 'pointer', ['pointer','pointer']);
const getArg = new NativeFunction(sendAddress, 'pointer', ['pointer','pointer','pointer']);
const at = new NativeFunction(sendAddress, 'pointer', ['pointer','pointer','ulong']);
const count = new NativeFunction(sendAddress, 'ulong', ['pointer','pointer']);
const supports = new NativeFunction(sendAddress, 'bool', ['pointer','pointer','pointer']);
if (Process.arch !== 'arm64') throw new Error('CGRect ABI is only validated on arm64');
const getRect = new NativeFunction(sendAddress, [['double','double'],['double','double']], ['pointer','pointer']);
const getBool = new NativeFunction(sendAddress, 'bool', ['pointer','pointer']);
const getIvar = new NativeFunction(objc.getExportByName('class_getInstanceVariable'), 'pointer', ['pointer','pointer']);
const ivarOffset = new NativeFunction(objc.getExportByName('ivar_getOffset'), 'long', ['pointer']);
let primaryTop = 0;
const selectors = new Map();
function sel(name) {
  if (!selectors.has(name)) selectors.set(name, selector(Memory.allocUtf8String(name)));
  return selectors.get(name);
}
function responds(object, name) {
  return !object.isNull() && supports(object, sel('respondsToSelector:'), sel(name));
}
function text(object) {
  if (object.isNull()) return '';
  if (!responds(object, 'UTF8String')) object = get(object, sel('description'));
  if (object.isNull() || !responds(object, 'UTF8String')) return '';
  const value = get(object, sel('UTF8String'));
  return value.isNull() ? '' : value.readUtf8String().slice(0,600);
}
function prop(object, name) {
  return responds(object, name) ? text(get(object, sel(name))) : '';
}
function frame(object, name) {
  if (!responds(object,name)) return null;
  const raw=getRect(object,sel(name));
  const x=Number(raw[0][0]),y=Number(raw[0][1]),w=Number(raw[1][0]),h=Number(raw[1][1]);
  if (![x,y,w,h].every(Number.isFinite) || w<0 || h<0) return null;
  return [x,primaryTop-y-h,x+w,primaryTop-y];
}
function boolProp(object,name) { return responds(object,name) ? getBool(object,sel(name)) : null; }
function children(object) {
  if (responds(object, 'accessibilityChildren')) return get(object, sel('accessibilityChildren'));
  if (responds(object, 'accessibilityAttributeValue:')) {
    const ns = objcClass(Memory.allocUtf8String('NSString'));
    const attr = getArg(ns, sel('stringWithUTF8String:'), Memory.allocUtf8String('AXChildren'));
    return getArg(object, sel('accessibilityAttributeValue:'), attr);
  }
  return NULL;
}
const seen = new Set();
let nodes = 0;
function walk(object, depth, path) {
  if (object.isNull() || depth > 14 || nodes >= 600 || seen.has(object.toString())) return null;
  seen.add(object.toString()); nodes++;
  const cls=objectClass(object),name=className(cls).readUtf8String();
  let nodeId='path:'+path;
  if (name==='QMacAccessibilityElement') {
    const field=getIvar(cls,Memory.allocUtf8String('axid'));
    if (!field.isNull()) nodeId='qt-ax:'+object.add(Number(ivarOffset(field))).readU32();
  }
  const result = {node_id:nodeId,path,class_name:name,
    role:prop(object,'accessibilityRole'),title:prop(object,'accessibilityTitle'),
    label:prop(object,'accessibilityLabel'),value:prop(object,'accessibilityValue'),
    identifier:prop(object,'accessibilityIdentifier'),
    frame:frame(object,'accessibilityFrame'),enabled:boolProp(object,'isAccessibilityEnabled'),
    hidden:boolProp(object,'isAccessibilityHidden'),children:[]};
  const list = children(object);
  if (!list.isNull() && responds(list,'count') && responds(list,'objectAtIndex:')) {
    const length = Math.min(count(list,sel('count')),60);
    for (let i=0;i<length;i++) {
      const child=walk(at(list,sel('objectAtIndex:'),i),depth+1,path+'/'+i);
      if (child) result.children.push(child);
    }
  }
  return result;
}
const onMain = new NativeCallback(function () {
  try {
    const appClass=objcClass(Memory.allocUtf8String('NSApplication'));
    const app=get(appClass,sel('sharedApplication'));
    const screens=get(objcClass(Memory.allocUtf8String('NSScreen')),sel('screens'));
    const screen=at(screens,sel('objectAtIndex:'),0);
    const screenRect=getRect(screen,sel('frame'));
    primaryTop=Number(screenRect[0][1])+Number(screenRect[1][1]);
    const windows=get(app,sel('windows'));
    const result=[];
    for (let i=0;i<Math.min(count(windows,sel('count')),8);i++) {
      const window=at(windows,sel('objectAtIndex:'),i);
      const content=get(window,sel('contentView'));
      let root=content;
      if (responds(content,'childAccessibleElement')) {
        const direct=get(content,sel('childAccessibleElement'));
        if (!direct.isNull()) root=direct;
      }
      const number=Number(count(window,sel('windowNumber')));
      result.push({title:prop(window,'title'),window_id:number,frame:frame(window,'frame'),
        visible:boolProp(window,'isVisible'),minimized:boolProp(window,'isMiniaturized'),
        has_sheet:!get(window,sel('attachedSheet')).isNull(),tree:walk(root,0,String(number))});
    }
    send({probe:'native-accessibility-read-only',schema_version:2,
      source:'qt-cocoa-layout',coordinate_space:'quartz-global-logical-points',
      captured_at:Date.now()/1000,app_active:boolProp(app,'isActive'),
      has_modal:!get(app,sel('modalWindow')).isNull(),nodes,truncated:nodes>=600,windows:result});
  } catch (error) { send({probe:'native-accessibility-read-only',error:String(error)}); }
},'void',['pointer']);
const dispatch = new NativeFunction(Module.getGlobalExportByName('dispatch_async_f'),
  'void',['pointer','pointer','pointer']);
dispatch(Module.getGlobalExportByName('_dispatch_main_q'),NULL,onMain);
