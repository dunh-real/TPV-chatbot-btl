"""Kiểm tra chuyển bytes MinIO sang HTTP và tiếp tục sau một file lỗi."""
import io
from types import SimpleNamespace

import httpx

from scripts.ingest_minio import ingest_bucket


def test_upload_bytes_and_continue_after_error():
    streams = []

    class Stream(io.BytesIO):
        def release_conn(self):
            self.released = True

    class Bucket:
        def list_objects(self, bucket, prefix, recursive):
            assert (bucket, prefix, recursive) == ('vanbandiaphuong', '', True)
            return [SimpleNamespace(object_name=n, is_dir=n.endswith('/'))
                    for n in ['folder/', 'image.png', 'notes.txt', 'folder/a.PDF', 'b.doc', 'c.docx']]

        def get_object(self, bucket, name):
            assert name not in {'image.png', 'notes.txt'}
            stream = Stream(b'%PDF-fixture-content')
            streams.append(stream)
            return stream

    requests = []

    def upload(request):
        requests.append(request)
        body = request.read()
        assert b'%PDF-fixture-content' in body
        assert b'filename="' in body
        assert b'name="force_reingest"' in body and b'false' in body
        if len(requests) == 2:
            return httpx.Response(500)
        return httpx.Response(200, json={'doc_id': 'abc', 'chunk_count': 2,
                                        'already_exists': len(requests) == 1})

    with httpx.Client(transport=httpx.MockTransport(upload)) as api:
        totals = ingest_bucket(Bucket(), api, bucket='vanbandiaphuong',
                               upload_url='http://localhost:8080/api/documents/upload')
    assert totals == {'ingested': 1, 'already_exists': 1, 'errors': 1, 'skipped': 3}
    assert len(requests) == 3
    assert all(s.closed and s.released for s in streams)
