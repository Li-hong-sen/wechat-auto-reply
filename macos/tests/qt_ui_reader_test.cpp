#include "../analysis/qt_ui_reader.cpp"
#include <cassert>
#include <cstdio>
static ArrayHeader* shared=nullptr;
static int destroyed=0;
static unsigned char imageData[64],pixels[]={10,20,30,255,0,0,64,128};
static void destroyPixmap(void* p){destroyed++;std::free(p);}
static void fakeGrab(void*,int call,int index,void** args){
    assert(call==0);
    auto* p=static_cast<unsigned char*>(std::calloc(1,80));
    static void* table[]={nullptr,reinterpret_cast<void*>(destroyPixmap)};
    *reinterpret_cast<void***>(p)=table;
    *reinterpret_cast<int*>(p+24)=1;
    *reinterpret_cast<int*>(p+36)=index==1?1024:0;
    *reinterpret_cast<unsigned char**>(p+72)=imageData;
    *reinterpret_cast<int*>(imageData+4)=2;*reinterpret_cast<int*>(imageData+8)=1;
    *reinterpret_cast<int*>(imageData+12)=32;
    *reinterpret_cast<unsigned char**>(imageData+40)=pixels;
    *reinterpret_cast<int*>(imageData+48)=6;
    *reinterpret_cast<int64_t*>(imageData+56)=8;
    *reinterpret_cast<void**>(static_cast<unsigned char*>(args[0])+24)=p;
}
static void fakeGetter(void*,int call,int property,void** arguments) {
    assert(call==1);
    if(property==2){*static_cast<bool*>(arguments[0])=true;return;}
    if(property==0)return;
    auto* value=static_cast<ArrayHeader*>(std::malloc(32));
    *value={property==3?2:1,2,2,0,24};
    auto* text=reinterpret_cast<uint16_t*>(value+1);text[0]=0x9cb8;text[1]=0x9cb8;text[2]=0;
    *static_cast<ArrayHeader**>(arguments[0])=value;
    if(property==3)shared=value;
}
int main() {
    uint16_t text[8];void* object=reinterpret_cast<void*>(1);
    assert(wx_ui_read_qstring(object,reinterpret_cast<void*>(fakeGetter),0,text,8)==0);
    assert(wx_ui_read_qstring(object,reinterpret_cast<void*>(fakeGetter),1,text,8)==2);
    assert(text[0]==0x9cb8&&text[1]==0x9cb8&&text[2]==0);
    assert(wx_ui_read_qstring(object,reinterpret_cast<void*>(fakeGetter),1,text,2)==1);
    assert(text[1]==0);
    assert(wx_ui_read_qstring(object,reinterpret_cast<void*>(fakeGetter),3,text,8)==2);
    assert(shared->references==1);std::free(shared);
    assert(wx_ui_read_bool(object,reinterpret_cast<void*>(fakeGetter),2)==1);
    unsigned char rgba[8];int dimensions[3];
    assert(wx_ui_grab_widget(object,reinterpret_cast<void*>(fakeGrab),0,rgba,8,dimensions)==8);
    assert(dimensions[0]==2&&dimensions[1]==1&&rgba[0]==30&&rgba[2]==10&&rgba[4]==127);
    assert(wx_ui_grab_widget(object,reinterpret_cast<void*>(fakeGrab),0,rgba,4,dimensions)==-5);
    assert(wx_ui_grab_widget(object,reinterpret_cast<void*>(fakeGrab),1,rgba,8,dimensions)==-2);
    assert(destroyed==3);
    std::puts("Qt UI string ownership, truncation and bool checks passed");
}
