"""Kiểm tra màn hình UC37 bằng trình duyệt; bật riêng UC37_UI_TEST=1."""
import asyncio
import os
import socket
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.staticfiles import StaticFiles

from app.core.context import use_principal
from test_uc37 import environment, seed

pytestmark = pytest.mark.skipif(os.getenv('UC37_UI_TEST') != '1', reason='Bật UC37_UI_TEST=1 để kiểm thử trình duyệt')


async def test_task_screen(environment):
    import uvicorn
    from playwright.async_api import async_playwright, expect
    client, scope, principal, *_ = environment
    app = client._transport.app
    @app.middleware('http')
    async def identity(request, next):
        user = int(request.headers.get('x-user-id', '10'))
        with use_principal(replace(principal, user_id=str(user), access=replace(principal.access, user_id=user))):
            return await next(request)
    @app.get('/health')
    async def health():
        return dict(qdrant=True, llm=True, database=True, points=0)
    app.mount('/ui', StaticFiles(directory=Path(__file__).resolve().parents[2]/'frontend', html=True))
    async with scope() as repo:
        done = await seed(repo)
        failed = await seed(repo,status='Failed',kind='Slide',AgentCode='run_presentation_workflow',InputJson='{"request":"Test"}')
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started: await asyncio.sleep(.02)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, channel=os.getenv('UC37_BROWSER', 'chrome'))
            try:
                page = await browser.new_page(viewport={'width':1440,'height':1000})
                errors = []; page.on('pageerror', lambda exc: errors.append(str(exc)))
                await page.add_init_script("localStorage.setItem('tpv.userId','10'); localStorage.setItem('tpv.tenantId','94');")
                await page.goto(f'http://127.0.0.1:{sock.getsockname()[1]}/ui/#tasks')
                await expect(page.locator('#uc37List')).to_contain_text(f'#{done}')
                await page.locator(f'#uc37List [data-id="{done}"]').click()
                await expect(page.locator('#uc37Detail')).to_contain_text('Kết quả')
                await page.locator('#uc37Status').select_option('Failed')
                await expect(page.locator('#uc37List')).not_to_contain_text(f'#{done}')
                await page.locator(f'#uc37List [data-id="{failed}"]').click()
                await page.locator('#uc37Retry').click()
                await expect(page.locator('#uc37Detail')).to_contain_text(f'Chạy lại từ tác vụ #{failed}')
                await page.evaluate(f'location.hash="tasks/{done}"')
                await expect(page.locator('#uc37Delete')).to_be_visible()
                page.once('dialog', lambda dialog: dialog.accept())
                await page.locator('#uc37Delete').click()
                await expect(page.locator('#uc37Detail')).to_contain_text('Đầu ra đã được xóa.')
                output = Path(__file__).resolve().parents[2]/'artifacts'/'uc37'/'screen.png'
                output.parent.mkdir(parents=True,exist_ok=True)
                await page.screenshot(path=str(output),full_page=True)
                await page.evaluate("API.setUser('20')")
                await expect(page.locator('#uc37List')).to_contain_text('Chưa có tác vụ phù hợp.')
                await expect(page.locator('#uc37Detail')).not_to_contain_text('Kết quả')
                assert errors == []
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await serving
        sock.close()
