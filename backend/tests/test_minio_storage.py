from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.core.config import Settings
from app.services import object_storage
from scripts.ingest_minio import ingest_bucket


def test_content_addressed_upload_and_bucket_allowlist(monkeypatch, tmp_path):
    client = Mock()
    client.bucket_exists.return_value = True
    monkeypatch.setattr(object_storage, 'Minio', lambda *a, **kw: client)
    monkeypatch.setattr(object_storage, 'get_settings', lambda: Settings(
        minio_bucket='uploads', minio_ingest_buckets=['laws']))
    store = object_storage.ObjectStorage()
    path = tmp_path / 'a.pdf'
    path.write_bytes(b'content')
    first = store.upload(path, 'a.pdf')
    renamed = store.upload(path, 'renamed.pdf')
    assert first['storage_object_key'] == renamed['storage_object_key']
    assert len(first['checksum_sha256']) == 64
    path.write_bytes(b'changed')
    assert store.upload(path, 'a.pdf')['storage_object_key'] != first['storage_object_key']
    with pytest.raises(PermissionError):
        store.download('blocked', 'folder/a.pdf', path, 'a.pdf')
    client.fget_object.assert_not_called()
    client.fget_object.side_effect = lambda bucket, key, destination: path.write_bytes(b'original')
    metadata = store.download('laws', 'folder/a.pdf', path, 'a.pdf')
    assert metadata['storage_object_key'] == 'folder/a.pdf'
    assert metadata['file_size_bytes'] == len(b'original')


def test_script_sends_object_reference_only():
    minio = Mock()
    minio.list_objects.return_value = [SimpleNamespace(object_name='folder/a.pdf', is_dir=False, size=5)]
    def respond(request):
        import json
        assert json.loads(request.content) == {
            'bucket': 'laws', 'object_key': 'folder/a.pdf', 'doc_type': '', 'force_reingest': False}
        return httpx.Response(200, json={'doc_id': 'x', 'already_exists': False, 'chunk_count': 1})
    with httpx.Client(transport=httpx.MockTransport(respond)) as api:
        totals = ingest_bucket(minio, api, bucket='laws', upload_url='http://test/api/documents/ingest-minio')
    assert totals['ingested'] == 1
    minio.get_object.assert_not_called()
