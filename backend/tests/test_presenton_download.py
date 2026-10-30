"""Đường dẫn file export và URL file tĩnh phải tải qua cùng dịch vụ Presenton."""
import httpx
import pytest

from app.services.presenton import PresentonClient, PresentonError


@pytest.mark.parametrize('path,expected', [
    ('/home/abc/opt/presenton/data/exports/pptx/test.pptx', '/app_data/exports/pptx/test.pptx'),
    ('/app_data/exports/pptx/test.pptx', '/app_data/exports/pptx/test.pptx'),
    ('app_data/exports/pptx/test.pptx', '/app_data/exports/pptx/test.pptx'),
    ('/app_data/test.pptx', '/app_data/test.pptx'),
])
async def test_download_path(path, expected):
    client = PresentonClient.__new__(PresentonClient)
    async def request(method, url):
        assert method=='GET' and url==expected
        return httpx.Response(200, content=b'pptx', request=httpx.Request(method,'http://presenton'+url))
    client._request=request
    assert await client._download(path)==b'pptx'


async def test_download_still_reports_missing_file():
    client = PresentonClient.__new__(PresentonClient)
    async def request(method,url):
        return httpx.Response(404, request=httpx.Request(method,'http://presenton'+url))
    client._request=request
    with pytest.raises(PresentonError,match='404'): await client._download('/app_data/missing.pptx')
