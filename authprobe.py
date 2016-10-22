#!/usr/bin/env python

from __future__ import print_function

import socket, os, sys

def hexencode(s):
    return ''.join(['%X'%ord(c) for c in s])

def makedict(str):
    R = {}
    for L in str.split(','):
        K, _sep, V = L.partition('=')
        R[K] = V
    return R

def getsessinfo():
    # DBUS_SESSION_BUS_ADDRESS=unix:abstract=/tmp/dbus-...,guid=...
    S = os.environ.get('DBUS_SESSION_BUS_ADDRESS')
    if S:
        return makedict(S)

def getsysteminfo():
    for path in ('/var/run/dbus/system_bus_socket', '/run/dbus/system_bus_socket'):
        if os.path.exists(path):
            return {'unix:path':path}

def connect_unix(path, timeout=None):
    S = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    S.settimeout(timeout)
    S.connect(path)
    return S

def probe_unix(info):
    print('probe', info)
    if 'unix:abstract' in info:
        S = connect_unix('\0'+info['unix:abstract'])
    elif 'unix:path' in info:
        S = connect_unix(info['unix:path'])
    R = S.makefile('r')

    S.send(b'\0AUTH\r\n')
    while True:
        L = R.readline().strip()
        print('>>>', repr(L))
        if L.startswith('REJECTED'):
            mechs = L.split(' ')[1:]
            break
        else:
            print('not REJECTED')
            sys.exit(1)

    print('supported', mechs)
    if 'EXTERNAL' in mechs:
        S.send(b'AUTH EXTERNAL %s\r\n'%hexencode(str(os.getuid())))
#    if 'DBUS_COOKIE_SHA1' in mechs:
#        S.send(b'\0AUTH DBUS_COOKIE_SHA1\r\n')

        while True:
            L = R.readline()
            print('>>>', repr(L))
            if L.startswith('OK'):
                break
            else:
                print('not OK')
                sys.exit(1)

        R.close()
        S.send(b'BEGIN\r\n')

        print('done')

    else:
        print('No auth')

    R.close()
    S.close()

def main():
    I = getsessinfo()
    if I:
        probe_unix(I)
    I = getsysteminfo()
    if I:
        probe_unix(I)

if __name__=='__main__':
    main()
