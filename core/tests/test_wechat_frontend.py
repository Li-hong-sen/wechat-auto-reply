import copy
import unittest

from core.wechat_frontend import FrontendMap, FrontendUnavailable


def node(name, box, text=None, children=(), **properties):
    if text is not None:
        properties['Text'] = text
    return dict(node_id='test:' + name + str(box), qt_class='mmui::' + name,
                is_widget=True, visible=True, enabled=True, frame=box,
                properties=properties, children=list(children))


def message(y, text, outgoing=False):
    head = node('ContactHeadView', [670 if outgoing else 120, y, 700 if outgoing else 150, y+30], accessibleName='我' if outgoing else '对方')
    label = node('XTextView', [300, y+2, 340, y+22], text)
    bubble = node('ChatBubbleFrame', [290, y, 350, y+30], children=[label])
    return node('ChatTextItemView', [100, y, 750, y+40], children=[head, bubble])


def snapshot():
    title = node('ChatTitleBarMasterView', [100, 0, 800, 50], children=[node('XTextView', [110, 10, 220, 30], '文件传输助手')])
    messages = node('MessageView', [100, 50, 800, 500], children=[message(100, '?'), message(150, '1'), message(200, '1'), message(250, '收到', True)])
    input_ = node('ChatInputField', [120, 520, 780, 650], plainText='')
    send = node('XOutlineButton', [700, 660, 780, 690], '发送', Disabled=False)
    composer = node('ChatInputView', [100, 500, 800, 700], children=[input_, send])
    root = node('MainWindow', [0, 0, 800, 700], children=[title, messages, composer])
    return dict(source='qt5-qobject-qwidget-layout', schema_version=1,
                coordinate_space='quartz-global-logical-points', captured_at=100,
                app_active=True, has_modal=False,
                windows=[dict(window_id=7, frame=[0, 0, 800, 700], minimized=False, has_sheet=False, is_key=True,
                              roots=[dict(truncated=False, tree=root)])])


class FrontendTests(unittest.TestCase):
    def test_sender_direction_and_duplicate_short_text_preserved(self):
        m = FrontendMap(snapshot())
        self.assertEqual([x['text'] for x in m.messages], ['?', '1', '1', '收到'])
        self.assertEqual([x['direction'] for x in m.messages], ['incoming']*3 + ['outgoing'])
        self.assertEqual(m.messages[-1]['sender'], '我')

    def test_coordinates_are_logical_not_retina_pixels(self):
        m = FrontendMap(snapshot())
        self.assertEqual(m.click_point(m.input, now=101), (450, 585))

    def test_stale_and_future_data_rejected(self):
        m = FrontendMap(snapshot())
        for now in (99, 106):
            with self.assertRaises(FrontendUnavailable):m.click_point(m.input, now=now)

    def test_changed_conversation_rejected(self):
        m = FrontendMap(snapshot())
        with self.assertRaises(FrontendUnavailable):
            m.click_point(m.input, expected_conversation='另一用户', now=101)

    def test_draft_not_overwritten(self):
        m = FrontendMap(snapshot());m.input['properties']['plainText'] = '我的草稿'
        m.draft = '我的草稿'
        with self.assertRaises(FrontendUnavailable):m.click_point(m.input, now=101)

    def test_send_requires_exact_text_and_button_state(self):
        m = FrontendMap(snapshot());m.draft = '测试'
        with self.assertRaises(FrontendUnavailable):m.click_point(m.send, expected_draft='其他', now=101)
        self.assertEqual(m.click_point(m.send, expected_draft='测试', now=101), (740, 675))
        del m.send['properties']['Disabled']
        with self.assertRaises(FrontendUnavailable):m.click_point(m.send, expected_draft='测试', now=101)

    def test_incomplete_or_ambiguous_tree_rejected(self):
        p = snapshot();p['windows'][0]['roots'][0]['truncated'] = True
        with self.assertRaises(FrontendUnavailable):FrontendMap(p)
        p = snapshot();p['windows'].append(copy.deepcopy(p['windows'][0]))
        with self.assertRaises(FrontendUnavailable):FrontendMap(p)

    def test_blocked_window_states(self):
        for key, value in [('app_active', False), ('has_modal', True), ('has_modal', None)]:
            p = snapshot();p[key] = value;m = FrontendMap(p)
            with self.assertRaises(FrontendUnavailable):m.click_point(m.input, now=101)
        for key in ('has_sheet', 'minimized'):
            p = snapshot();p['windows'][0][key] = True;m = FrontendMap(p)
            with self.assertRaises(FrontendUnavailable):m.click_point(m.input, now=101)

    def test_hidden_disabled_outside_controls_rejected(self):
        for key, value in [('visible', False), ('enabled', False), ('frame', [790, 520, 900, 650])]:
            m = FrontendMap(snapshot());m.input[key] = value
            with self.assertRaises(FrontendUnavailable):m.click_point(m.input, now=101)

    def test_unknown_message_type_not_guessed_as_text(self):
        p = snapshot();row = p['windows'][0]['roots'][0]['tree']['children'][1]['children'][0]
        row['qt_class'] = 'mmui::ChatBubbleItemView'
        m = FrontendMap(p)
        self.assertEqual(m.messages[0]['kind'], 'unknown')

    def test_unknown_coordinate_source_rejected(self):
        p = snapshot();p['coordinate_space'] = 'retina-pixels'
        with self.assertRaises(FrontendUnavailable):FrontendMap(p)

    def test_preview_window_blocks_chat_click(self):
        p = snapshot();p['windows'][0]['is_key'] = False;m = FrontendMap(p)
        with self.assertRaises(FrontendUnavailable):m.click_point(m.input, now=101)

    def test_voice_transcript_stays_bound_to_original_row(self):
        p = snapshot();row = p['windows'][0]['roots'][0]['tree']['children'][1]['children'][0]
        row['qt_class'] = 'mmui::ChatVoiceItemView'
        row['children'][-1]['children'][0]['properties']['Text'] = '3"'
        row['children'].append(node('XVBoxView', [290, 130, 450, 160],
            children=[node('ChatBubbleFrame', [290, 130, 450, 160], children=[node('XTextView', [300, 135, 430, 155], '测试语音。')])], objectName='bottom_content_v_view_'))
        m = FrontendMap(p)
        self.assertEqual(m.messages[0]['transcript'], '测试语音。')
        self.assertEqual(m.messages[0]['duration_label'], '3"')
        self.assertEqual(m.messages[0]['text'], '')
        self.assertEqual(m.messages[0]['direction'], 'incoming')
        self.assertEqual(m.messages[0]['sender'], '对方')
        self.assertEqual(len(m.messages), 4)

    def test_sticker_uses_image_widget_but_not_avatar(self):
        p=snapshot();row=p['windows'][0]['roots'][0]['tree']['children'][1]['children'][0]
        row['qt_class']='mmui::ChatBubbleReferItemView'
        row['children'][-1]['children']=[node('XImage',[300,102,330,122])]
        m=FrontendMap(p)
        self.assertEqual(m.messages[0]['kind'],'sticker')
        self.assertEqual(len(m.messages[0]['sticker_candidates']),1)
        self.assertFalse(m.messages[0]['image_candidates'])
        row['children'][-1]['children']=[]
        row['children'][0]['children']=[node('XImage',[120,100,150,130])]
        self.assertEqual(FrontendMap(p).messages[0]['kind'],'unknown')


if __name__ == '__main__':
    unittest.main()
