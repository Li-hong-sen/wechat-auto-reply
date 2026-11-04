// Small Qt-5 QString ABI adapter for read-only frontend property access.
// No Qt dependency, messaging API, or UI action. ABI is pinned to this arm64
// Qt-5 build; the caller must validate the metaobject revision and property type.
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>

struct ArrayHeader {
    int32_t references;
    int32_t length;
    uint32_t allocation;
    uint32_t padding;
    int64_t offset;
};
static_assert(sizeof(ArrayHeader)==24, "Unsupported Qt array ABI");
struct EmptyString { ArrayHeader header; uint16_t zero[4]; };
static EmptyString empty={{-1,0,0,0,24},{0,0,0,0}};
using MetaCall=void(*)(void*,int,int,void**);

static void release(ArrayHeader* data) {
    const int32_t references=__atomic_load_n(&data->references,__ATOMIC_RELAXED);
    if(references==-1)return;
    if(references==0 || __atomic_fetch_sub(&data->references,1,__ATOMIC_ACQ_REL)==1)
        std::free(data);
}

extern "C" int wx_ui_read_qstring(void* object,void* function,int index,
                                   uint16_t* output,int capacity) {
    if(!object||!function||!output||capacity<1)return -1;
    ArrayHeader* value=&empty.header;
    void* arguments[]={&value};
    reinterpret_cast<MetaCall>(function)(object,1,index,arguments); // ReadProperty
    if(!value)return -2;
    int length=value->length;
    if(length<0||length>1048576){release(value);return -3;}
    if(length>=capacity)length=capacity-1;
    std::memcpy(output,reinterpret_cast<const char*>(value)+value->offset,
                static_cast<size_t>(length)*2);
    output[length]=0;
    release(value);
    return length;
}

extern "C" int wx_ui_read_bool(void* object,void* function,int index) {
    bool value=false;void* arguments[]={&value};
    reinterpret_cast<MetaCall>(function)(object,1,index,arguments);
    return value?1:0;
}

// Qt 5 QWidget::grab (UI rendering only), then the validated raster pixmap
// buffer. No messaging method, screen-capture setting or original media access.
extern "C" int wx_ui_grab_widget(void* object,void* function,int index,
                                  unsigned char* output,int capacity,int* info) {
    struct Pixmap { uint64_t paint_device[3]; void* platform; } pixmap{};
    int rectangle[4]={0,0,-2,-2}; // QRect(0,0,-1,-1), inclusive right/bottom.
    void* arguments[]={&pixmap,rectangle};
    reinterpret_cast<MetaCall>(function)(object,0,index,arguments); // InvokeMetaMethod
    if(!pixmap.platform)return -1;
    auto* p=static_cast<unsigned char*>(pixmap.platform);
    struct Cleanup {
        unsigned char* p;
        ~Cleanup(){
            auto* refs=reinterpret_cast<int32_t*>(p+24);
            if(__atomic_fetch_sub(refs,1,__ATOMIC_ACQ_REL)==1){
                auto** table=*reinterpret_cast<void***>(p);
                reinterpret_cast<void(*)(void*)>(table[1])(p);
            }
        }
    } cleanup{p};
    // QPlatformPixmap / QRasterPlatformPixmap layout from Qt 5.15 headers.
    const int class_id=*reinterpret_cast<int*>(p+36);
    if(class_id!=0)return -2; // Only the raster backend has this buffer layout.
    auto* image_data=*reinterpret_cast<unsigned char**>(p+48+24);
    if(!image_data)return -3;
    const int w=*reinterpret_cast<int*>(image_data+4),h=*reinterpret_cast<int*>(image_data+8);
    const int depth=*reinterpret_cast<int*>(image_data+12),format=*reinterpret_cast<int*>(image_data+48);
    const int64_t stride=*reinterpret_cast<int64_t*>(image_data+56);
    auto* pixels=*reinterpret_cast<unsigned char**>(image_data+40);
    if(!pixels||w<1||h<1||w>2048||h>2048||depth!=32||stride<w*4||stride>w*4+4096)return -4;
    if(static_cast<int64_t>(w)*h*4>capacity)return -5;
    if(format!=4&&format!=5&&format!=6&&format!=16&&format!=17&&format!=18)return -6;
    info[0]=w;info[1]=h;info[2]=format;
    for(int y=0;y<h;y++)for(int x=0;x<w;x++){
        const auto* src=pixels+y*stride+x*4;auto* dest=output+(y*w+x)*4;
        const bool rgba=format>=16;
        unsigned a=(format==4||format==16)?255:src[3];
        unsigned r=src[rgba?0:2],g=src[1],b=src[rgba?2:0];
        if((format==6||format==18)&&a&&a<255){
            r=r*255/a;g=g*255/a;b=b*255/a;
            if(r>255)r=255;if(g>255)g=255;if(b>255)b=255;
        }
        dest[0]=r;dest[1]=g;dest[2]=b;dest[3]=a;
    }
    return w*h*4;
}
