import os
from pathlib import Path
import unittest
from PIL import Image
from macos.wechat_layout import detect_layout

class LayoutTests(unittest.TestCase):
    def test_blank_image_is_rejected(self):
        with self.assertRaises(ValueError): detect_layout(Image.new('RGB',(1760,1280),'white'))

    def test_small_image_is_rejected(self):
        with self.assertRaises(ValueError): detect_layout(Image.new('RGB',(100,100),'white'))

    def test_fixture_regions_and_scaled_coordinates(self):
        path=os.environ.get('WHALEGIRL_LAYOUT_FIXTURE')
        if not path or not Path(path).is_file(): self.skipTest('需通过环境变量提供授权微信截图')
        with Image.open(path) as image:
            base={r.name:r.box for r in detect_layout(image)}
            self.assertEqual(base['messages'],(600,102,1760,994))
            self.assertEqual(base['send'],(1616,1188,1712,1236))
            for scale in (.75,1.25):
                resized=image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
                actual={r.name:r.box for r in detect_layout(resized)}
                self.assertEqual(set(actual),set(base))
                for name in base:
                    for a,b in zip(actual[name],base[name]): self.assertLessEqual(abs(a/scale-b),5)
