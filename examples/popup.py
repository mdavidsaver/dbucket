#!/usr/bin/env python3

import logging
_log = logging.getLogger(__name__)
import asyncio, signal, functools, sys

from dbucket.conn import RemoteError
from dbucket.auth import with_session

SERVICE = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'

@asyncio.coroutine
def run(args, conn):
    note = yield from conn.proxy(destination=SERVICE, path=PATH)

    SIGQ = conn.new_queue()
    yield from note.NotificationClosed.connect(SIGQ)
    yield from note.ActionInvoked.connect(SIGQ)

    id = yield from note.Notify(
        "Pop-up Example",
        0, # no replace
        "", # no icon
        "Wake-up!",
        args.message,
        ["ok", "Ok", "cancel", "Cancel"],
        {}, # no hints
        args.timeout,
    )
    print("Raised", id)

    ret = 0
    while True:
        evt, sts = yield from SIGQ.recv()
        print("signal", evt)
        if evt.member=='ActionInvoked':
            aid, act = evt.body
            if aid==id and act=='cancel':
                ret = 1
        break

    print("Closing", id)
    # TODO: doesn't seem to cause close w/ KDE?
    yield from note.CloseNotification(id)

    return ret

def getargs():
    from argparse import ArgumentParser
    P = ArgumentParser()
    P.add_argument('-d', '--debug', action='store_true', default=False)
    P.add_argument('-t', '--timeout', type=int, default=10000)
    P.add_argument('message')
    return P.parse_args()

def main(args):
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    loop = asyncio.get_event_loop()
    loop.set_debug(args.debug)
    ret = loop.run_until_complete(with_session(functools.partial(run, args)))
    sys.exit(ret)

if __name__=='__main__':
    main(getargs())

