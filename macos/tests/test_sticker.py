import base64
import copy
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from core.tests.test_wechat_frontend import snapshot,node
from core.wechat_frontend import FrontendMap,FrontendUnavailable
from macos.wechat_sticker import decode_frame,capture_sticker


def fixture():
    p=snapshot();p['captured_at']=time.time()
    row=p['windows'][0]['roots'][0]['tree']['children'][1]['children'][0]
    row['qt_class']='mmui::ChatBubbleReferItemView'
    row['children'][-1]['children']=[node('XImage',[300,102,330,122])]
    m=FrontendMap(p);rid=m.messages[0]['node_id'];nid=m.messages[0]['sticker_candidates'][0]['node_id']
    rendered={'probe':'qt-widget-render','source':'qt-qwidget-grab','row_id':rid,'node_id':nid,'width':2,'height':2,'rgba_base64':base64.b64encode(bytes([20,40,60,255])*4).decode()}
    raw={'events':[{'payload':p},{'payload':rendered}]}
    p['probe']='qt-frontend-layout'
    return m,raw,rid,nid


class StickerTests(unittest.TestCase):
    def test_frame_is_validated_and_pixels_preserved(self):
        _,raw,rid,nid=fixture();im=decode_frame(raw,rid,nid)
        self.assertEqual(im.getpixel((0,0)),(20,40,60,255))
        with self.assertRaises(FrontendUnavailable):decode_frame(raw,'other',nid)
        raw['events'][1]['payload']['rgba_base64']='AA=='
        with self.assertRaises(FrontendUnavailable):decode_frame(raw,rid,nid)

    def test_sampling_deduplicates_static_sticker_without_any_send(self):
        m,raw,rid,_=fixture()
        with tempfile.TemporaryDirectory() as temp,patch('macos.wechat_sticker.read',return_value=(m,{})),patch('macos.wechat_sticker.probe',return_value=raw) as probe,patch('macos.wechat_sticker.time.sleep'):
            result=capture_sticker(1,'文件传输助手',rid,Path(temp)/'capture.json')
            self.assertEqual(probe.call_count,3)
            self.assertEqual(len(result['frames']),1)
            self.assertEqual(result['messages_sent'],0)
            self.assertFalse(result['original_animation'])

    def test_switched_conversation_discards_sample(self):
        m,raw,rid,_=fixture()
        raw['events'][0]['payload']['windows'][0]['roots'][0]['tree']['children'][0]['children'][0]['properties']['Text']='另一会话'
        with tempfile.TemporaryDirectory() as temp,patch('macos.wechat_sticker.read',return_value=(m,{})),patch('macos.wechat_sticker.probe',return_value=raw):
            with self.assertRaises(FrontendUnavailable):capture_sticker(1,'文件传输助手',rid,Path(temp)/'capture.json')
            self.assertFalse(list(Path(temp).glob('*.png')))


if __name__=='__main__':unittest.main()
