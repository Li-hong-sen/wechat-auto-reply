"""Read Qt 5 QByteArrayData string tables from a local Mach-O; no app execution.

Tables expose class/method/type names, not callable APIs. All offsets in the
report are absolute file offsets (including the FAT slice offset).
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


def slices(data):
    if data[:4] == b'\xca\xfe\xba\xbe':
        count=struct.unpack_from('>I',data,4)[0]
        for index in range(count):
            cpu,_,offset,size,_=struct.unpack_from('>IIIII',data,8+20*index)
            yield {'architecture':{0x100000c:'arm64',0x1000007:'x86_64'}.get(cpu,hex(cpu)),
                   'offset':offset,'size':size}
    else:
        yield {'architecture':'unknown','offset':0,'size':len(data)}


def table_at(data,position,name,slice_start,slice_end):
    # Qt 5 on 64-bit uses 24-byte QByteArrayData headers. Each string's
    # signed relative offset is measured from its own header, not the table.
    signature=struct.pack('<iI',-1,len(name))
    cursor=max(slice_start,position-24000)
    while True:
        base=data.find(signature,cursor,position)
        if base<0:return None
        cursor=base+1
        if base+24>slice_end or (position-base)%24:continue
        if base+struct.unpack_from('<q',data,base+16)[0]!=position:continue
        strings=[]
        valid=True
        for header in range(base,position,24):
            reference,length,allocation,padding,offset=struct.unpack_from('<iIIIq',data,header)
            location=header+offset
            if reference!=-1 or allocation or padding or length>4096 or location<position or location+length>=slice_end or data[location+length]!=0:
                valid=False;break
            try:text=data[location:location+length].decode('utf-8')
            except UnicodeDecodeError:valid=False;break
            strings.append({'text':text,'file_offset':hex(location)})
        if valid and strings and strings[0]['text']==name.decode():
            return {'class':name.decode(),'table_file_offset':hex(base),'strings':strings}


def decode_methods(data,section,table):
    """Follow candidate staticMetaObject pointers; validate Qt revision-8 data.

    Low 36 bits are candidate image offsets in this target's chained pointers.
    They are accepted only when mappings and the referenced Qt tables validate.
    This is deliberately not a general dyld chained-fixup decoder.
    """
    start=section['offset'];end=start+section['size']
    if data[start:start+4]!=b'\xcf\xfa\xed\xfe':return None
    command=start+32;segments=[]
    for _ in range(struct.unpack_from('<I',data,start+16)[0]):
        kind,size=struct.unpack_from('<II',data,command)
        if kind==0x19:
            vm,vmsize,fileoff,filesize=struct.unpack_from('<4Q',data,command+24)
            segments.append((vm,vmsize,start+fileoff,filesize))
        command+=size
    def to_file(address):
        for vm,_,offset,size in segments:
            if vm<=address<vm+size:return offset+address-vm
    string_offset=int(table['table_file_offset'],16)
    address=None
    for vm,_,offset,size in segments:
        if offset<=string_offset<offset+size:address=vm+string_offset-offset
    if address is None:return None
    needle=struct.pack('<I',address&0xffffffff);cursor=start
    strings=[entry['text'] for entry in table['strings']]
    builtin={0:'unknown',1:'bool',2:'int',3:'uint',4:'qlonglong',5:'qulonglong',
             6:'double',7:'QChar',10:'QString',11:'QStringList',12:'QByteArray',
             19:'QRect',20:'QRectF',21:'QSize',22:'QSizeF',25:'QPoint',26:'QPointF',
             31:'void*',39:'QObject*',41:'QVariant',42:'QModelIndex',43:'void'}
    def typename(value):
        if value&0x80000000:return strings[value&0x7fffffff]
        return builtin.get(value,'QMetaType#'+str(value))
    while True:
        cursor=data.find(needle,cursor,end)
        if cursor<0:return None
        position=cursor;cursor+=1
        if position%8 or position+40>end:continue
        ptr=struct.unpack_from('<Q',data,position)[0]&0xfffffffff
        if ptr!=address:continue
        meta=to_file(struct.unpack_from('<Q',data,position+8)[0]&0xfffffffff)
        dispatch=struct.unpack_from('<Q',data,position+16)[0]&0xfffffffff
        if meta is None or meta+56>end:continue
        header=struct.unpack_from('<14I',data,meta)
        revision,name,_,_,count,entry=header[:6]
        if revision!=8 or name!=0 or count>300 or entry<14:continue
        methods=[]
        try:
            for index in range(count):
                name_index,argc,params,tag,flags=struct.unpack_from('<5I',data,meta+4*(entry+5*index))
                if argc>30 or params>100000 or name_index>=len(strings) or tag>=len(strings):raise ValueError()
                values=struct.unpack_from('<'+'I'*(1+2*argc),data,meta+4*params)
                arguments=[{'type':typename(values[1+i]),'name':strings[values[1+argc+i]]} for i in range(argc)]
                methods.append({'name':strings[name_index],'kind':{0:'method',4:'signal',8:'slot',12:'constructor'}[flags&12],
                                'access':{0:'private',1:'protected',2:'public'}.get(flags&3,'unknown'),
                                'return_type':typename(values[0]),'arguments':arguments})
        except (IndexError,KeyError,ValueError,struct.error):continue
        properties=[]
        try:
            for index in range(header[6]):
                name_index,type_id,flags=struct.unpack_from('<3I',data,meta+4*(header[7]+3*index))
                properties.append({'name':strings[name_index],'type':typename(type_id),'flags':hex(flags)})
        except (IndexError,struct.error):continue
        return {'metaobject_file_offset':hex(position-8),'metadata_file_offset':hex(meta),
                'static_metacall_image_address':hex(dispatch),'static_metacall_file_offset':
                hex(to_file(dispatch)) if to_file(dispatch) is not None else None,
                'metadata_revision':revision,'signal_count':header[13],'methods':methods,'properties':properties}


def extract(path,methods_for=()):
    data=Path(path).read_bytes()
    report={'target':str(Path(path).resolve()),'sha256':hashlib.sha256(data).hexdigest(),
            'method':'Qt5 64-bit QByteArrayData structural validation',
            'limitations':['String tables do not prove callable functions, method signatures or runtime accessibility.',
                           'No process attachment, message reads, screenshots, or app modifications.'],
            'slices':[]}
    for section in slices(data):
        start=section['offset'];end=start+section['size'];tables=[]
        for match in re.finditer(rb'mmui::[A-Za-z_][A-Za-z0-9_:]{1,120}\x00',data[start:end]):
            table=table_at(data,start+match.start(),match.group()[:-1],start,end)
            if table:
                if table['class'] in methods_for:table['metaobject']=decode_methods(data,section,table)
                tables.append(table)
        section['tables']=tables;report['slices'].append(section)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--methods-for',action='append',default=[],help='recover revision-8 method metadata for an exact class name')
    args=parser.parse_args()
    result=extract(args.binary,args.methods_for)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(args.output.resolve()),'tables':{
        section['architecture']:len(section['tables']) for section in result['slices']}},ensure_ascii=False))
