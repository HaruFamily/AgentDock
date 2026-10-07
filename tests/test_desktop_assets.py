import base64
import hashlib
import struct
import zlib

from agentdock.desktop_assets import HEADER, EOF, cached_images
from agentdock.desktop_chat import messages
from test_desktop_chat import cache

def png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


PNG = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
       + png_chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\xff')) + png_chunk(b'IEND', b''))
URL = 'https://claude.ai/api/org/files/image/preview'


def simple_cache(folder, data=PNG):
    key = ('1/0/_dk_https://claude.ai https://claude.ai ' + URL).encode()
    meta = b'http metadata'
    eof = lambda b, flags: struct.pack('<QIII4x', EOF, flags, zlib.crc32(b), len(b))
    blob = (struct.pack('<QIII4x', HEADER, 9, len(key), 0) + key + data + eof(data, 1)
            + meta + hashlib.sha256(key).digest() + eof(meta, 3))
    path = folder / '0123456789abcdef_0'
    path.write_bytes(blob)
    return path


def test_cache_exact_reference_integrity_and_missing(tmp_path):
    p = simple_cache(tmp_path)
    assert cached_images(tmp_path, [URL])[URL].endswith(base64.b64encode(PNG).decode())
    assert cached_images(tmp_path, [URL + '/other']) == {}
    blob = bytearray(p.read_bytes()); blob[-25] ^= 1; p.write_bytes(blob)
    assert cached_images(tmp_path, [URL]) == {}


def test_files_v2_image_only_and_missing_placeholder():
    value = cache()
    row = value['tree']['messages'][0]
    row['content'] = []
    row['files_v2'] = [{'file_kind': 'image', 'file_name': 'sample.png',
                        'preview_asset': {'url': '/api/org/files/image/preview'}}]
    entry = messages(value)[2][0][0]
    assert entry['images'] == [{'name': 'sample.png', 'data_url': ''}]
    url = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
    assert messages(value, {URL: url})[2][0][0]['images'][0]['data_url'] == url


def test_block_cache_only_reads_referenced_bounded_image(tmp_path, monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from agentdock._vendor.chromium import ccl_chromium_cache as vendor
    (tmp_path / 'data_0').touch()
    class Cache:
        def __init__(self, folder): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cache_keys(self):
            return [SimpleNamespace(url=URL, raw_key='target'),
                    SimpleNamespace(url='https://elsewhere/image', raw_key='other')]
        def __getitem__(self, key):
            assert key == 'target'
            return SimpleNamespace(data_sizes=[0, len(PNG)], data_addrs=[0, 'address'])
        def get_stream_for_addr(self, addr):
            assert addr == 'address'
            return BytesIO(PNG)
    monkeypatch.setattr(vendor, 'ChromiumBlockFileCache', Cache)
    assert cached_images(tmp_path, [URL])[URL].endswith(base64.b64encode(PNG).decode())


def test_saved_image_survives_source_cache_eviction(tmp_path):
    from agentdock.qa.store import QuestionStore
    store = QuestionStore(tmp_path)
    entry = dict(key='u', kind='user_message', text='', created_at='2026-10-07T12:00:00Z',
                 images=[dict(name='sample.png', data_url='data:image/png;base64,' + base64.b64encode(PNG).decode())])
    store.import_chat('o', 'Desktop', 's', 'Title', [entry])
    saved = store._chat['events'][0]['images']
    entry['images'][0]['data_url'] = ''
    store.import_chat('o', 'Desktop', 's', 'Title', [entry])
    assert store._chat['events'][0]['images'] == saved


def test_late_cached_image_repairs_saved_message_without_importing_old_history(tmp_path, monkeypatch):
    from agentdock import desktop_chat
    from agentdock.chat_sync import ChatSync, Source
    from agentdock.qa.store import QuestionStore
    store = QuestionStore(tmp_path / 'dock')
    value = cache()
    row = value['tree']['messages'][0]
    row['files_v2'] = [{'file_kind': 'image', 'file_name': 'sample.png',
                        'preview_asset': {'url': URL}}]
    store.import_chat('owner', 'Desktop', 'session', 'Title', [messages(value)[2][0][0]])
    store.begin_chat_session()
    source = Source('claude-desktop', tmp_path / 'profile' / 'IndexedDB', 'Desktop', 'owner')
    monkeypatch.setattr(desktop_chat, 'signature', lambda p: (1,))
    monkeypatch.setattr(desktop_chat, 'read_cache', lambda p: ([value], (1,)))
    sync = ChatSync(store, tmp_path / 'dock', lambda: [source])
    sync.session_since = '2026-10-08T00:00:00Z'
    sync.scan()
    assert store._chat['events'][0]['images'][0]['unavailable']
    folder = source.root.parent / 'Cache' / 'Cache_Data'
    folder.mkdir(parents=True)
    simple_cache(folder)
    sync.scan()  # IndexedDB unchanged; HTTP cache changed.
    assert store._chat['events'][0]['images'][0]['id']
    assert len(store._chat['events']) == 1
    assert store.conversations() == []
