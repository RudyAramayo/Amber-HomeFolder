#!/usr/bin/env python3
import asyncio
import json
import sys


async def main(host: str, port: int, token: str) -> None:
    reader, writer = await asyncio.open_connection(host, port)
    print(json.loads(await reader.readline()))
    writer.write((json.dumps({"type": "hello", "protocol": "rob-amber-gateway/1", "token": token}) + "\n").encode())
    await writer.drain()
    print(json.loads(await reader.readline()))
    writer.write(b'{"type":"heartbeat"}\n')
    await writer.drain()
    for _ in range(5):
        print(json.loads(await reader.readline()))
    writer.close()
    await writer.wait_closed()


asyncio.run(main(sys.argv[1], int(sys.argv[2]), sys.argv[3]))
