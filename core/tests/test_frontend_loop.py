import unittest
from core.frontend_loop import new_tail, sequence, incoming_tail


def msg(id, text='', kind='text', direction='incoming'):
    return dict(node_id=id,text=text,kind=kind,direction=direction)


class LoopTests(unittest.TestCase):
    def test_first_observation_does_not_reply_to_history(self):
        self.assertIsNone(new_tail(None,[msg('a','旧消息')]))

    def test_duplicate_text_and_short_text_are_distinct_bubbles(self):
        old=[msg('a','1')];new=old+[msg('b','1'),msg('c','?')]
        self.assertEqual(new_tail(sequence(old),new),new[1:])

    def test_scrolled_window_alignment(self):
        old=[msg('a','a'),msg('b','b')];new=[old[1],msg('c','c')]
        self.assertEqual(new_tail(sequence(old),new),new[1:])

    def test_transcription_does_not_create_new_message(self):
        old=[msg('voice',kind='voice')];new=[dict(old[0],transcript='已转写')]
        self.assertEqual(new_tail(sequence(old),new),[])

    def test_no_overlap_preserves_baseline(self):
        self.assertIsNone(new_tail(sequence([msg('a','a')]),[msg('b','b')]))

    def test_own_reply_is_not_incoming(self):
        batch=[msg('a','已回'),msg('b','我已手动回复',direction='outgoing'),msg('c','新问题')]
        self.assertEqual(incoming_tail(batch),[batch[-1]])


if __name__=='__main__':unittest.main()
