
import logging
_log = logging.getLogger(__name__)

import os, sys

import asyncio
import binascii

__all__ = [
    'get_session_infos',
    'get_system_infos',
    'connect_bus',
    'with_connection',
    'with_session',
    'with_system',
]

 
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
    try:
        contents = os.listdir(sbase)
    except FileNotFoundError:
        pass
    else:
        for sdir in contents:
            with open(os.path.join(sbase,sdir),'r') as F:
                for L in F:
                    if L.startswith('DBUS_SESSION_BUS_ADDRESS='):
                        _key, _sep, val = L.strip().partition('=')
                        yield makedict(val)
                        break

def get_system_infos():
    for loc in ('/var/run/dbus/system_bus_socket',):
        yield {'unix:path':loc}

_supported_methods = set(['EXTERNAL', 'ANONYMOUS'])

def ConnectionFactory(W, R, info, *, loop=None, **kws):
    from .conn import Connection
    return Connection(W, R, info, loop=loop, **kws)

@asyncio.coroutine
def connect_bus(infos, *,
                allowed_methods=_supported_methods,
                factory=ConnectionFactory,
                loop=None,
                **kws):
    """Accepts a sequence/generator of dictionaries describing possible bus endpoints.
    Tries to connect to each until one succeeds.
    
    A coroutine
    
    :returns: Connection
    """
    for info in infos:
        R, W = None, None
        try:
            _log.debug('Trying bus %s', info)
            if 'unix:abstract' in info:
                R, W = yield from asyncio.open_unix_connection('\0'+info['unix:abstract'], loop=loop)
            elif 'unix:path' in info:
                R, W = yield from asyncio.open_unix_connection(info['unix:path'], loop=loop)
            else:
                _log.debug('No supported transport: %s', info)
                continue

            #TODO: transports not yet handled
            #       'nonce-tcp:host=xxx,port=...,family=...,noncefile='
            #       'tcp:host=xxx,port=...,family=...'
            #       'unixexec:path=...,arg0=...,...'

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

            conn = factory(W, R, info, loop=loop, **kws)
        except:
            if W is not None:
                W.close()
            _log.exception("Can't attach to %s", info)
            continue

        # Connection now has responsibility for call R.close()
        try:
            yield from conn.setup()
        except:
            conn.close()
            raise
        return conn

    raise RuntimeError('No Bus')

@asyncio.coroutine
def with_connection(auth_info, func, **kws):
    """A coroutine which run the provided co-routine add passes in
    a newly created Connection 'func(conn)'.
    
    The connection is closed after the func() completes.
    
    This coroutine completes with the value returned by func()
    
    Remaining keyword arguments are passed to connect_bus(**kws)
    """
    conn = yield from connect_bus(auth_info(), **kws)
    try:
        return (yield from func(conn))
    finally:
        yield from conn.close()

def with_session(func, **kws):
    return with_connection(get_session_infos, func, **kws)

def with_system(func, **kws):
    return with_connection(get_system_infos, func, **kws)
