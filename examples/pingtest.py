#!/usr/bin/env python3

# Low level test of connection setup
# Connect to the session daemon, then Ping it

import logging

import asyncio
from dbucket.conn import DBUS, DBUS_PATH
from dbucket.auth import connect_bus, get_session_infos

@asyncio.coroutine
def pingtest():
    print("Connecting to bus")
    conn = yield from connect_bus(get_session_infos())
    try:
        print("Send ListNames")
        rep = yield from conn.call(
            destination=DBUS,
            path=DBUS_PATH,
            interface=DBUS,
            member='ListNames',
        )
        print("Recv ListNames")
        for name in rep:
            print(name)
    finally:
        print("Closing")
        yield from conn.close()
        print("Closed")

def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(pingtest())

if __name__=='__main__':
    logging.basicConfig(level=logging.DEBUG)
    asyncio.get_event_loop().set_debug(True)
    main()
