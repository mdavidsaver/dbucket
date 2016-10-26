#!/usr/bin/env python3

import logging
_log = logging.getLogger(__name__)

import os, sys
import asyncio
import struct

from xcode import encode, decode, Object

_sys_lsb = sys.byteorder=='little'
_sys_L   = b'l' if _sys_lsb else b'B'

class RemoteError(RuntimeError):
    pass

class Connection(object):
    # ignore/error all calls from peers
    ignore_calls = True

    def __init__(self, W, R, info, loop=None):
        self._W, self._R, self._info, self._loop = W, R, info, loop
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
        _log.debug('call %s', (path, iface, member, dest, sig, body))
        if body is not None:
            body = encode(sig, body)
        else:
            body = b''
        SN = self._nextsn
        while SN in self._inprog:
            SN += 1
        self._nextsn = SN+1

        opts = [
            [1, Object(path)],
            [3, member],
        ]
        if iface is not None:
            opts.append([2, iface])
        if dest is not None:
            opts.append([6, dest])
        req = [ord(_sys_L), 1, 0, 1,   len(body), SN,   opts]
        header = encode(b'yyyyuua(yv)', req)

        try:
            ret = asyncio.Future(loop=self._loop)
            self._inprog[SN] = ret

            _log.debug("send head %s", repr(header))
            self._W.write(header)
            M = len(header)%8
            if M:
                self._W.write(b'\0'*(8-M))
            _log.debug("send body %s", repr(body))
            self._W.write(body)
        except:
            # failure at this point means part of the message is queued,
            # so we must close the connection
            self.close()
            raise
        return ret

    @asyncio.coroutine
    def _recv(self):
        try:
            while True:
                # full message spec is
                #   yyyyuua(yv) ...body...
                # Treat the first part as
                #   yyyyuuu
                # to get body and header array sizes to compute the size of the complete message
                head = yield from self._R.readexactly(16)
                _log.debug("Header %s", repr(head))

                # validate byte order and version
                if head[0] not in (ord(b'l'), ord(b'B')) or head[3]!=1:
                    raise RuntimeError('Invalid header %s'%head)

                mtype, flags = head[1], head[2]
                lsb = head[0]==ord(b'l')
                L = '<' if lsb else '>'

                blen, sn, hlen = struct.unpack(L+'III', head[4:])

                # dbus spec puts arbitrary upper bounds on message and header size
                if hlen+blen>2**27 or hlen>=2**26:
                    raise RuntimeError('Message too big %s %s'%(hlen, blen))

                # header is padded so the body starts on an 8 byte boundary
                bstart = ((hlen+7)&~7)
                fullsize = bstart + blen
                _log.debug('Remainder hlen=%d bstart=%d blen=%d, fullsize=%d', hlen, bstart, blen, fullsize)

                rest = yield from self._R.readexactly(fullsize)
                headers, body = head+rest[:hlen], rest[bstart:]
                _log.debug('Raw Headers=%s body=%s', headers, body)

                headers = decode(b'yyyyuua(yv)', headers, lsb=lsb)
                headers = headers[-1]

                H = [None]*10
                for code, val in headers:
                    H[code] = val
                headers = H
                _log.debug('Headers %s', headers)
                del H

                if len(body):
                    sig = headers[8]
                    body = decode(sig, body, lsb=lsb)

                if mtype==1: # METHOD_CALL
                    if self.ignore_calls:
                        pass # TODO: return error
                    else:
                        pass
                elif mtype in (2, 3): # METHOD_RETURN or ERROR
                    rsn = headers[5]
                    try:
                        F = self._inprog[rsn]
                    except KeyError:
                        _log.warn('Received reply/error with unknown S/N %s', rsn)
                    else:
                        if F:
                            if mtype==2:
                                F.set_result(body)
                            else:
                                F.set_exception(RemoteError(headers[4]))
                elif mtype==4: # SIGNAL
                    path, iface, member = headers[1], headers[2], headers[3]
                    _log.info("Ignore signal %s %s %s %s", path, iface, member, body)
                else:
                    _log.debug('Ignoring unknown dbus message type %s', mtype)

        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            return # connection closed
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
            else:
                # TODO: NEGOTIATE_UNIX_FD
                W.write(b'BEGIN\r\n')

            _log.debug('Authenticated with bus %s', info)
        except:
            _log.exception("Can't attach to %s", info)

        conn = Connection(W, R, info)
        try:

            hello = yield from conn.call(path='/org/freedesktop/DBus',
                            member='Hello',
                            iface='org.freedesktop.DBus',
                            dest='org.freedesktop.DBus',
                            )
            print('IAM', hello)
        except:
            conn.close()
            raise
        return conn

    raise RuntimeError('No Bus')

def main():
    loop = asyncio.get_event_loop()
    try:
        print("Connect")
        conn = loop.run_until_complete(connect_bus(get_session_infos()))
        print("Close")
        loop.run_until_complete(conn.close())
    finally:
        loop.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.DEBUG)
    asyncio.get_event_loop().set_debug(True)
    main()
