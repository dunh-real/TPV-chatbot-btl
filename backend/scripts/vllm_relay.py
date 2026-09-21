"""Relay TCP cho container Presenton gọi được vLLM chạy trên host.

    container Presenton (172.22.0.2)
        └─> 172.22.0.1:8001  [relay nay, chay o host]
                └─> 127.0.0.1:8001  [vLLM]

VÌ SAO CẦN

vLLM bind `127.0.0.1:8001`. Container nằm trên bridge `tpv-btl_default` nên nó
không với tới loopback của host - `Connection refused`. Ba cách nối, chọn cách
này:

  1. Đổi vLLM sang `--host 0.0.0.0`: mở model ra CẢ MẠNG LAN, và phải restart
     một tiến trình đang phục vụ chatbot lẫn OCR. Đổi thứ đang chạy vì một thứ
     đang thử.
  2. `network_mode: host` cho Presenton: bất khả thi. `start.js` của Presenton
     hardcode `appmcpPort = 8001` và `fastapiPort = 8000` - dùng host network là
     đụng đúng cổng vLLM (8001) và cổng backend (8081 thì không, nhưng 8000 thì
     có). Các cổng là hằng số, không đọc env.
  3. Relay này: chỉ nghe trên IP gateway của bridge, tức chỉ container trong
     bridge đó gọi được, không ra LAN. Tắt là hết, không để lại gì.

CHẠY

    cd backend && nohup .venv/bin/python scripts/vllm_relay.py > /tmp/relay.log 2>&1 &

Không chạy thì Presenton báo lỗi kết nối và workflow 5 rơi về bản tự dựng
(`pptx_builder`) - vẫn ra file, nhưng khô khan, không có phần nhận xét.
"""

from __future__ import annotations

import asyncio
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("vllm_relay")

NGHE_HOST = os.getenv("RELAY_HOST", "172.22.0.1")
NGHE_PORT = int(os.getenv("RELAY_PORT", "8001"))
DICH_HOST = os.getenv("RELAY_TARGET_HOST", "127.0.0.1")
DICH_PORT = int(os.getenv("RELAY_TARGET_PORT", "8001"))


async def _chuyen(doc: asyncio.StreamReader, ghi: asyncio.StreamWriter) -> None:
    try:
        while (data := await doc.read(65536)):
            ghi.write(data)
            await ghi.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        if not ghi.is_closing():
            ghi.close()


async def _phuc_vu(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    try:
        sr, sw = await asyncio.open_connection(DICH_HOST, DICH_PORT)
    except OSError as exc:
        logger.warning("Không nối được tới vLLM %s:%s - %s", DICH_HOST, DICH_PORT, exc)
        cw.close()
        return
    # Hai chiều chạy song song: sinh slide là một luồng dài, chờ xong một chiều
    # rồi mới đọc chiều kia là treo.
    await asyncio.gather(_chuyen(cr, sw), _chuyen(sr, cw))


async def main() -> None:
    server = await asyncio.start_server(_phuc_vu, NGHE_HOST, NGHE_PORT)
    logger.info("Relay %s:%s -> %s:%s", NGHE_HOST, NGHE_PORT, DICH_HOST, DICH_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Dừng relay")
