"""Read-only semantic map of the validated WeChat 4.1.15 / Qt 5 frontend.

No OCR, message API or business method invocation. Runtime node IDs are transient
UI identities, never durable message IDs. Coordinates are global logical points.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time


class FrontendUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    @classmethod
    def read(cls, value):
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise FrontendUnavailable('缺少控件坐标')
        r = cls(*map(float, value))
        if not all(math.isfinite(v) for v in value) or r.right <= r.left or r.bottom <= r.top:
            raise FrontendUnavailable('控件坐标无效')
        return r

    @property
    def center(self):
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)

    def contains(self, other):
        return self.left <= other.left < other.right <= self.right and self.top <= other.top < other.bottom <= self.bottom

    def intersects(self, other):
        return self.left < other.right and other.left < self.right and self.top < other.bottom and other.top < self.bottom

    def json(self):
        return [self.left, self.top, self.right, self.bottom]


def walk(node):
    yield node
    for child in node.get('children', []):
        yield from walk(child)


def visible(node):
    return node.get('is_widget') is True and node.get('visible') is True and bool(node.get('frame'))


def texts(node):
    return [n for n in walk(node) if visible(n) and n.get('qt_class') == 'mmui::XTextView' and n.get('properties', {}).get('Text')]


def unique(nodes, label):
    nodes = list(nodes)
    if len(nodes) != 1:
        raise FrontendUnavailable(f'{label}数量异常：{len(nodes)}')
    return nodes[0]


class FrontendMap:
    def __init__(self, envelope):
        if envelope.get('error'):
            raise FrontendUnavailable(envelope['error'])
        if 'events' in envelope:
            envelope = unique((e['payload'] for e in envelope['events'] if e.get('payload', {}).get('probe') == 'qt-frontend-layout'), '布局结果')
        self.raw = envelope
        if envelope.get('source') != 'qt5-qobject-qwidget-layout' or envelope.get('schema_version') != 1 or envelope.get('coordinate_space') != 'quartz-global-logical-points':
            raise FrontendUnavailable('不支持的布局来源或坐标系')
        roots = [(w, r) for w in envelope.get('windows', []) for r in w.get('roots', []) if (r.get('tree') or {}).get('qt_class') == 'mmui::MainWindow']
        self.window, root = unique(roots, '微信主窗口')
        if root.get('truncated') is not False:
            raise FrontendUnavailable('控件树不完整')
        self.tree = root['tree']
        self.nodes = list(walk(self.tree))
        self.bounds = Rect.read(self.window['frame'])
        self.captured_at = float(envelope['captured_at'])
        self.contacts = self._contacts()
        self.message_area = self.one('mmui::MessageView')
        self.input_area = self.one('mmui::ChatInputView')
        self.input = self.one('mmui::ChatInputField', within=self.input_area)
        self.send = unique((n for n in walk(self.input_area) if visible(n) and n.get('qt_class') == 'mmui::XOutlineButton' and n.get('properties', {}).get('Text') == '发送'), '发送按钮')
        title = self.one('mmui::ChatTitleBarMasterView')
        labels = texts(title)
        labels.sort(key=lambda n: (n['frame'][0], n['frame'][1]))
        if not labels:
            raise FrontendUnavailable('无法确认当前会话名称')
        self.conversation = labels[0]['properties']['Text']
        self.draft = self.input.get('properties', {}).get('plainText')
        if not isinstance(self.draft, str):
            raise FrontendUnavailable('无法读取输入框文字')
        self.messages = self._messages()

    def one(self, class_name, within=None):
        return unique((n for n in walk(within or self.tree) if visible(n) and n.get('qt_class') == class_name), class_name)

    def _contacts(self):
        result = []
        for cell in self.nodes:
            if not visible(cell) or cell.get('qt_class') != 'mmui::ChatSessionCell':
                continue
            labels = sorted(texts(cell), key=lambda n: (n['frame'][1], n['frame'][0]))
            if labels:
                result.append({'name': labels[0]['properties']['Text'], 'node_id': cell['node_id'], 'frame': cell['frame']})
        return sorted(result, key=lambda c: c['frame'][1])

    def _messages(self):
        # Only classes actually named as message items; generic rows remain system.
        kinds = {'ChatTextItemView': 'text', 'ChatVoiceItemView': 'voice',
                 'ChatImageGroupItemView': 'image'}
        area = Rect.read(self.message_area['frame'])
        result = []
        for row in walk(self.message_area):
            name = row.get('qt_class', '').removeprefix('mmui::')
            if not visible(row) or not (name.startswith('Chat') and name.endswith('ItemView')):
                continue
            if not area.intersects(Rect.read(row['frame'])):
                continue
            bubbles = [n for n in walk(row) if visible(n) and n.get('qt_class') == 'mmui::ChatBubbleFrame']
            heads = [n for n in walk(row) if visible(n) and n.get('qt_class') == 'mmui::ContactHeadView']
            # Voice transcription adds a second bubble below the original.
            # Bind sender/duration to the primary bubble aligned with the avatar.
            if len(heads) == 1 and len(bubbles) > 1:
                aligned = [n for n in bubbles if abs(n['frame'][1] - heads[0]['frame'][1]) <= 1]
                if len(aligned) == 1:bubbles = aligned
            direction, sender = 'unknown', ''
            if len(bubbles) == len(heads) == 1:
                b, h = Rect.read(bubbles[0]['frame']), Rect.read(heads[0]['frame'])
                if h.right <= b.left:
                    direction = 'incoming'
                elif h.left >= b.right:
                    direction = 'outgoing'
                sender = heads[0].get('properties', {}).get('accessibleName', '')
            labels = sorted(texts(bubbles[0] if len(bubbles) == 1 else row), key=lambda n: (n['frame'][1], n['frame'][0]))
            kind = kinds.get(name, 'system' if name in ('ChatItemView', 'ChatSystemInfoItemView') else 'unknown')
            transcript = ''
            if kind == 'voice':
                bottoms = [n for n in walk(row) if visible(n) and n.get('properties', {}).get('objectName') == 'bottom_content_v_view_']
                transcript = '\n'.join(t['properties']['Text'] for bottom in bottoms for t in texts(bottom))
            images = [n for n in walk(row) if visible(n) and n.get('qt_class') == 'mmui::XImageView']
            stickers = [n for bubble in bubbles for n in walk(bubble) if visible(n) and n.get('qt_class') == 'mmui::XImage'] if name == 'ChatBubbleReferItemView' and not images else []
            if len(stickers) == 1:kind = 'sticker'
            result.append({'node_id': row['node_id'], 'qt_class': row['qt_class'],
                           'kind': kind,
                           'direction': direction, 'sender': sender,
                           'text': '' if kind == 'voice' else '\n'.join(n['properties']['Text'] for n in labels),
                           'transcript': transcript,
                           'duration_label': '\n'.join(n['properties']['Text'] for n in labels) if kind == 'voice' else '',
                           'image_candidates': [{'node_id': n['node_id'], 'frame': n['frame']} for n in images],
                           'sticker_candidates': [{'node_id': n['node_id'], 'frame': n['frame']} for n in stickers],
                           'bubble_frame': bubbles[0]['frame'] if len(bubbles) == 1 else None,
                           'frame': row['frame']})
        return sorted(result, key=lambda m: (m['frame'][1], m['frame'][0]))

    def click_point(self, target, expected_conversation=None, expected_draft=None, now=None):
        """Fresh, fail-closed plan; caller must execute immediately and read back.

        This does not emit input. In particular, generating a reply is not proof
        of successful sending; require a new outgoing bubble after GUI action.
        """
        age = (time.time() if now is None else now) - self.captured_at
        if not 0 <= age <= 5:
            raise FrontendUnavailable('布局已过期，请重新读取')
        if self.raw.get('app_active') is not True:
            raise FrontendUnavailable('微信未在前台')
        if self.raw.get('has_modal') is not False or self.window.get('has_sheet') is not False or self.window.get('minimized') is not False:
            raise FrontendUnavailable('窗口存在弹窗、最小化或状态未知')
        if self.window.get('is_key') is not True:
            raise FrontendUnavailable('主聊天窗口不是当前键盘窗口，可能有图片预览遮挡')
        if expected_conversation is not None and self.conversation != expected_conversation:
            raise FrontendUnavailable('会话已变化，取消操作')
        if target not in self.nodes or not visible(target) or target.get('enabled') is not True:
            raise FrontendUnavailable('目标控件不可操作')
        box = Rect.read(target['frame'])
        if not self.bounds.contains(box):
            raise FrontendUnavailable('目标不在微信窗口内')
        if target is self.input and self.draft.strip():
            raise FrontendUnavailable('输入框已有草稿')
        if target is self.send:
            if expected_draft is None or self.draft != expected_draft:
                raise FrontendUnavailable('输入框文字与待发送文字不一致')
            if target.get('properties', {}).get('Disabled') is not False:
                raise FrontendUnavailable('发送按钮未启用或状态未知')
        return box.center

    def json(self):
        return {'source': self.raw['source'], 'coordinate_space': self.raw['coordinate_space'],
                'window_id': self.window['window_id'], 'captured_at': self.captured_at,
                'conversation': self.conversation, 'draft': self.draft,
                'regions': {label: node['frame'] for label, node in [('messages', self.message_area), ('composer', self.input_area), ('input', self.input), ('send', self.send)]},
                'contacts': self.contacts, 'messages': self.messages}
