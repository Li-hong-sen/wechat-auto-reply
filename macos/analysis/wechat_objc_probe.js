'use strict';
// Only reads Objective-C class/method/ivar metadata. No UI actions or hooks.
const objc = Process.getModuleByName('libobjc.A.dylib');
function api(name, result, args) {
  return new NativeFunction(objc.getExportByName(name), result, args);
}
const getClassList = api('objc_getClassList', 'int', ['pointer', 'int']);
const getClassName = api('class_getName', 'pointer', ['pointer']);
const copyIvars = api('class_copyIvarList', 'pointer', ['pointer', 'pointer']);
const ivarName = api('ivar_getName', 'pointer', ['pointer']);
const ivarType = api('ivar_getTypeEncoding', 'pointer', ['pointer']);
const ivarOffset = api('ivar_getOffset', 'long', ['pointer']);
const copyMethods = api('class_copyMethodList', 'pointer', ['pointer', 'pointer']);
const methodName = api('method_getName', 'pointer', ['pointer']);
const selectorName = api('sel_getName', 'pointer', ['pointer']);
const methodType = api('method_getTypeEncoding', 'pointer', ['pointer']);
const release = new NativeFunction(Module.getGlobalExportByName('free'), 'void', ['pointer']);
function cstr(pointer) { return pointer.isNull() ? '' : pointer.readUtf8String(); }
const total = getClassList(NULL, 0);
if (total < 0 || total > 150000) throw new Error('Unexpected class count');
const classes = Memory.alloc(Math.max(total, 1) * Process.pointerSize);
const observed = Math.min(getClassList(classes, total), total);
const selected = [];
for (let index = 0; index < observed; index++) {
  const cls = classes.add(index * Process.pointerSize).readPointer();
  const name = cstr(getClassName(cls));
  if (!/^(QNSView|QNSWindow|QMacAccessibilityElement|QCocoaView)$/.test(name)) continue;
  const count = Memory.alloc(4);
  count.writeU32(0);
  const fields = copyIvars(cls, count);
  const ivars = [];
  try {
    for (let i = 0; i < Math.min(count.readU32(), 100); i++) {
      const item = fields.add(i * Process.pointerSize).readPointer();
      ivars.push({name:cstr(ivarName(item)), type:cstr(ivarType(item)), offset:ivarOffset(item).toString()});
    }
  } finally { if (!fields.isNull()) release(fields); }
  count.writeU32(0);
  const entries = copyMethods(cls, count);
  const methods = [];
  try {
    for (let i = 0; i < Math.min(count.readU32(), 400); i++) {
      const item = entries.add(i * Process.pointerSize).readPointer();
      const selector = cstr(selectorName(methodName(item)));
      if (/qt|accessib|window|view|platform/i.test(selector)) {
        methods.push({selector, encoding:cstr(methodType(item))});
      }
    }
  } finally { if (!entries.isNull()) release(entries); }
  selected.push({class_name:name, ivars, methods});
}
send({probe:'ui-runtime-class-metadata',total_classes:observed,classes:selected});
