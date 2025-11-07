import asyncio
import websockets
import socket
import json
import uuid
import time

HUB_URL = "ws://192.168.1.162:9000"  
DEVICE_ID = str(uuid.uuid4())
DEVICE_NAME = "Pi-01"

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

async def register():
    while True:
        try:
            async with websockets.connect(HUB_URL) as ws:
                info = {
                    "type": "register",
                    "deviceId": DEVICE_ID,
                    "deviceName": DEVICE_NAME,
                    "localIp": get_local_ip(),
                    "port": 5000,
                }
                await ws.send(json.dumps(info))
                print("Registered to hub.")
                while True:
                    # keep connection alive
                    await asyncio.sleep(30)
                    await ws.send(json.dumps(info))
        except Exception as e:
            print("Retrying connection:", e)
            time.sleep(5)

asyncio.run(register())
