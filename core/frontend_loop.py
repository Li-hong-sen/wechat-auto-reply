"""Pure state helpers for the native frontend loop; no GUI or network."""
def signature(message):
    return [message['node_id'], message['direction'], message['kind'],
            message['text'] if message['kind'] == 'text' else '']


def sequence(messages):
    return [signature(m) for m in messages if m['direction'] in ('incoming', 'outgoing')]


def new_tail(previous, messages):
    current = [m for m in messages if m['direction'] in ('incoming', 'outgoing')]
    sigs = sequence(current)
    if previous is None:return None
    if previous == sigs:return []
    for size in range(min(len(previous), len(sigs)), 0, -1):
        if previous[-size:] == sigs[:size]:return current[size:]
    return None  # Never reset a known baseline merely because alignment failed.


def incoming_tail(messages):
    start = 0
    for index, m in enumerate(messages):
        if m['direction'] == 'outgoing':start = index+1
    return [m for m in messages[start:] if m['direction'] == 'incoming']
