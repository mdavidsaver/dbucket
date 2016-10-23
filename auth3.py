#!/usr/bin/env python3

import logging
_log = logging.getLogger(__name__)

import os, sys
import asyncio
import struct


def unpack(lsb, fmt, B):
    if lsb:
        return struct.unpack('<%s'%fmt, B)
    else:
        return struct.unpack('>%s'%fmt, B)

class Connection(object):
    def __init__(self, W, R, info):
        self._W, self._R, self._info = W, R, info
        self._running = True
        self._inprog  = {}
        self._nextsn = 1 #TODO: randomize?
        self._RX = asyncio.get_event_loop().create_task(self._recv())

    @asyncio.coroutine
    def close(self):
        if not self._running:
            return
        self._running = False
        self._RX.cancel()
        if asyncio.Task.current_task() is not self._RX:
            yield from self._RX

    def __enter__(self):
        return self
    def __exit__(self,A,B,C):
        self.close()

    def call(self, path=None, iface=None, member=None, dest=None, sig=None, body=None):
        request = None

    @asyncio.coroutine
    def _recv(self):
        try:
            while True:
                try:
                    # message is
                    #   yyyyuua(yv) ...body...
                    head = yield from self._R.readexactly(16)

                    if head[0] not in (b'l', b'B') or head[3]!=b'\1':
                        raise RuntimeError('Invalid header %s'%head)

                    mtype, flags = ord(head[1]), ord(head[2])
                    lsb = head[0]==b'l'

                    blen, sn, hlen = unpack(lsb, 'III', head[4:])
                    if hlen+blen>2**27 or hlen>=2**26:
                        raise RuntimeError('Message too big')
                    rest = yield from self._R.readexactly(hlen+blen)
                    headers, body = rest[:hlen], rest[hlen:]

                    _log.debug('RX mtype=%d flags=%x hlen=%s blen=%s')

                    if mtype not in range(5):
                        _log.debug('Ignore unknown message type %s', mtype)
                        continue

                    try:
                        F = self._inprog[sn]
                    except KeyError:
                        _log.warn('Received message with unknown S/N %s', sn)
                    else:
                        if F:
                            F.set_result((mtype, flags, headers, body))

                except asyncio.IncompleteReadError:
                    return # connection closed
                _log.debug('Recv %s', head)
                raise RuntimeError('not impl')
        except:
            _log.exception('Error in Connnection RX')
            self._W.close()
            self._running = False


import binascii
# 
hexencode = lambda s:binascii.b2a_hex(s).upper()
hexdecode = binascii.a2b_hex
def hexencode2(s):
    return (''.join(['%X'%ord(c) for c in s])).encode('ascii')

assert hexencode(b'1000')==b'31303030'
assert hexdecode(b'31303030')==b'1000'

def makedict(str):
    R = {}
    for L in str.split(','):
        K, _sep, V = L.partition('=')
        R[K] = V
    return R

def get_session_infos():
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

def get_system_infos():
    for loc in ('/var/run/dbus/system_bus_socket', '/var/run/dbus/system_bus_socket'):
        yield {'unix:path':loc}

_supported_methods = set(['EXTERNAL', 'ANONYMOUS'])

@asyncio.coroutine
def connect_bus(infos, allowed_methods=_supported_methods):
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

            methods = set(L.decode('ascii').strip().split(' ')[1:])
            ok = False
            _log.debug('Advertised auth methods: %s', methods)
            methods.intersection_update(allowed_methods)
            _log.debug('Proceed with methods: %s', methods)

            if not ok and 'EXTERNAL' in methods:
                _log.debug('Attempt EXTERNAL')
                W.write(b'AUTH EXTERNAL '+hexencode(str(os.getuid()).encode('ascii'))+b'\r\n')
                L = yield from R.readline()
                if L.startswith(b'OK'):
                    ok = True
                    _log.debug('EXTERNAL accepted')
                elif L.startswith(b'REJECTED'):
                    _log.debug('EXTERNAL rejected: %s', L)
                else:
                    raise RuntimeError('EXTERNAL incomplete: %s'%L)

            # TODO: not working
            if not ok and 'ANONYMOUS' in methods:
                _log.debug('Attempt ANONYMOUS')
                W.write(b'AUTH ANONYMOUS'+hexencode(b'Nemo')+b'\r\n')
                if L.startswith(b'OK'):
                    ok = True
                    _log.debug('ANONYMOUS accepted')
                elif L.startswith(b'REJECTED'):
                    _log.debug('ANONYMOUS rejected: %s', L)
                else:
                    raise RuntimeError('ANONYMOUS incomplete: %s'%L)

            if not ok:
                _log.debug('No supported auth method')
                continue

            _log.debug('Authenticated with bus %s', info)
            return Connection(W, R, info)
        except:
            _log.exception("Can't attach to %s", info)
        finally:
            W.close()

    raise RuntimeError('No Bus')

def main():
    loop = asyncio.get_event_loop()
    conn = loop.run_until_complete(connect_bus(get_session_infos()))
    loop.run_until_complete(conn.close())
    loop.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.DEBUG)
    asyncio.get_event_loop().set_debug(True)
    main()
