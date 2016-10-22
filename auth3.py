#!/usr/bin/env python3

import logging
_log = logging.getLogger(__name__)

import os, sys
import asyncio


def hexencode(s):
    return (''.join(['%X'%ord(c) for c in s])).encode('ascii')

def makedict(str):
    R = {}
    for L in str.split(','):
        K, _sep, V = L.partition('=')
        R[K] = V
    return R

def get_session_bus():
    '''Yield a dict describing possible session bus locations
    '''
    if 'DBUS_SESSION_BUS_ADDRESS' in os.environ:
        yield makedict(os.environ['DBUS_SESSION_BUS_ADDRESS'])
    sbase = os.path.expanduser('~/.dbus/session-bus')
    for sdir in os.listdir(sbase):
        with open(os.path.join(sbase,sdir),'r') as F:
            for L in F:
                if L.startswith('DBUS_SESSION_BUS_ADDRESS='):
                    _key, _sep, val = L.strip().partition('=')
                    yield makedict(val)
                    break

def get_system_bus():
    for loc in ('/var/run/dbus/system_bus_socket', '/var/run/dbus/system_bus_socket'):
        yield {'unix:path':loc}

@asyncio.coroutine
def connect_bus(infos):
    for info in infos:
        _log.debug('Trying bus %s', info)
        if 'unix:abstract' in info:
            R, W = yield from asyncio.open_unix_connection('\0'+info['unix:abstract'])
        elif 'unix:path' in info:
            R, W = yield from asyncio.open_unix_connection(info['unix:path'])
        else:
            _log.debug('No supported transport: %s', info)
            continue

        try:
            # start authentication phase

            W.write(b'\0AUTH\r\n')
            L = yield from R.readline()
            if not L.startswith(b'REJECTED'):
                raise RuntimeError('Bad auth phase (not dbus?)')

            methods = L.decode('ascii').strip().split(' ')[1:]
            ok = False
            _log.debug('Supported auth methods: %s', methods)

            if 'EXTERNAL' in methods:
                W.write(b'AUTH EXTERNAL '+hexencode(str(os.getuid()))+b'\r\n')
                L = yield from R.readline()
                if L.startswith(b'OK'):
                    ok = True
                    _log.debug('EXTERNAL accepted')
                elif L.startswith(b'REJECTED'):
                    _log.debug('EXTERNAL rejected: %s', L)
                else:
                    raise RuntimeError('EXTERNAL incomplete: %s'%L)

            if not ok:
                _log.debug('No supported auth method')
                continue

            _log.debug('Authenticated with bus %s', info)
            return
        except:
            _log.exception("Can't attach to %s", info)
        finally:
            W.close()

    raise RuntimeError('No Bus')

def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(connect_bus(get_session_bus()))
    loop.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.DEBUG)
    asyncio.get_event_loop().set_debug(True)
    main()
