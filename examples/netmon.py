#!/usr/bin/env python3

import logging
_log = logging.getLogger(__name__)
import asyncio

from dbucket.conn import RemoteError
from dbucket.auth import connect_bus, get_system_infos
from dbucket.proxy import createProxy

SERVICE = 'org.freedesktop.NetworkManager'
PATH = '/org/freedesktop/NetworkManager'

states = {
    0:'Unknown',
    10:'ASleep',
    20:'Disconnected',
    30:'Disconnecting',
    40:'Connecting',
    50:'Local',
    60:'Site',
    70:'Global',
}

@asyncio.coroutine
def onStateChange(conn, netman, state):
    try:
        _log.info('Current State %s', states.get(state,'???'))

        # routes to get SSID
        #
        # netman -> PrimaryConnection -> SpecificObject
        #
        # netman -> PrimaryConnection -> Devices (assume first) -> ActiveAccessPoint -> Ssid

        # are we connected to anything?
        if state not in (60,70):
            _log.info('Not connected (site or global)')
            return

        # How are we connected?
        con = yield from netman.PrimaryConnection
        _log.info("PrimaryConnection: %s", con)
        if con=='/':
            # when no active connnection we get root instead of an error :(
            _log.warn('No primary connection')
            return

        con = yield from netman[con]

        ctype = yield from con.Type

        if ctype not in ('802-11-wireless',):
            _log.debug('Primary is not WIFI? %s', ctype)

        ap = yield from netman[(yield from con.SpecificObject)]
        _log.info("Access Point %s", ap)

        if not hasattr(ap, 'Ssid'):
            _log.info('Primary no WIFI')

        ssid = ''.join(map(chr, (yield from ap.Ssid)))

        _log.info("WIFI connected to '%s'", ssid)

        if not ssid.startswith('MSUnet Guest'):
            return

        yield from conn.loop.run_in_executor(None, msulogin)
    except:
        _log.exception("Error in onStateChange")

def msulogin():
    _log.info("Login")
    import requests
    requests.post('https://login.wireless.msu.edu/login.html',
                  timeout=(3.0, 1.0),
                  data={
        'buttonClicked':'4',
        'redirect_url':'',
        'err_flag':'0',
        'username':'wirelessguest',
        'password':'wirelessguest',
    }).raise_for_status()
    _log.info("Login Successful")

@asyncio.coroutine
def run():
    conn = yield from connect_bus(get_system_infos())
    #conn.debug_net = True
    try:
        netman = yield from conn.proxy(
            destination=SERVICE,
            path=PATH,
        )

        SIGQ = conn.new_queue()
        yield from netman.StateChanged.connect(SIGQ)

        istate = yield from netman.State
        yield from onStateChange(conn, netman, istate)

        while True:
            evt, sts = yield from SIGQ.recv()
            yield from onStateChange(conn, netman, evt.body)

    finally:
        yield from conn.close()

def getargs():
    from argparse import ArgumentParser
    P = ArgumentParser()
    P.add_argument('-d', '--debug', action='store_true', default=False)
    return P.parse_args()

def main(args):
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    loop = asyncio.get_event_loop()
    loop.set_debug(args.debug)
    loop.run_until_complete(run())

if __name__=='__main__':
    main(getargs())
