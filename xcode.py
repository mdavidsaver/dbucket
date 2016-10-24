
import logging, sys
_log = logging.getLogger(__name__)

import struct

_sys_lsb = sys.byteorder=='little'

def _next_type(sig):
    """ Split type signature after first element (POD, array, or sub-struct)
    >>> _next_type(b'y')
    (b'y', b'')
    >>> _next_type(b'yy')
    (b'y', b'y')
    >>> _next_type(b'yyy')
    (b'y', b'yy')
    >>> _next_type(b'ay')
    (b'ay', b'')
    >>> _next_type(b'ayy')
    (b'ay', b'y')
    >>> _next_type(b'yay')
    (b'y', b'ay')
    >>> _next_type(b'a(ii)')
    (b'a(ii)', b'')
    >>> _next_type(b'a(ii)i')
    (b'a(ii)', b'i')
    >>> _next_type(b'aaii')
    (b'aai', b'i')
    >>> _next_type(b'aa(ai(yay)i)i')
    (b'aa(ai(yay)i)', b'i')
    """
    assert isinstance(sig, bytes)
    pos, depth = 0, 0
    while pos<len(sig):
        C = sig[pos]
        if C==ord(b'('):
            depth += 1
        elif C==ord(b')'):
            depth -= 1
        pos += 1
        if depth==0 and C!=ord(b'a'):
            break
    if len(sig)==0 or pos==0:
        raise ValueError("Incomplete sig")
    elif depth !=0:
        raise ValueError("Unbalenced ()")
    return sig[:pos], sig[pos:]

def _balign(buffer, bpos, size):
    """
    >>> buf=b'12345678'
    >>> _balign(buf[0:], 0, 4)
    (b'12345678', 0)
    >>> _balign(buf[1:], 1, 4)
    (b'5678', 4)
    >>> _balign(buf[2:], 2, 4)
    (b'5678', 4)
    >>> _balign(buf[3:], 3, 4)
    (b'5678', 4)
    >>> _balign(buf[4:], 4, 4)
    (b'5678', 4)
    """
    M = bpos%size
    if M:
        N = size-M
        bpos += N
        buffer = buffer[N:]
        #print(" skip", N, file=sys.stderr)
    return buffer, bpos

def _short_string(buffer, bpos):
    """
    >>> _short_string(b'\\x01o\\x00abcd', 0)
    (b'o', b'abcd', 3)
    """
    size = buffer[0]
    return buffer[1:1+size], buffer[2+size:], bpos+2+size

_dmap_plain = (
    (b'y', 'B'), # uint8
    (b'b', 'I'), # boolean (uint32)
    (b'n', 'h'), # int16
    (b'q', 'H'), # uint16
    (b'i', 'i'), # int32
    (b'u', 'I'), # uint32
    (b'x', 'l'), # int64
    (b't', 'L'), # uint64
    (b'd', 'd'), # double
    (b'h', 'I'), # unixfd (uint32)
)
_decode_plain = dict([(ord(d),p) for d,p in _dmap_plain])
del _dmap_plain

def _decode(sig, buffer, lsb=_sys_lsb, bpos=0):
    L = '<' if lsb else '>'
    assert len(sig)>0
    ret = []
    #print("_decode", sig, buffer, bpos, file=sys.stderr)

    while len(sig)>0:
        selem, sig = _next_type(sig)
        #print(" >>>", selem, bpos, buffer, file=sys.stderr)
        if selem[0]==ord(b'('):
            assert selem[-1]==ord(b')')
            buffer, bpos = _balign(buffer, bpos, 8)
            STR, buffer, bpos = _decode(selem[1:-1], buffer, lsb=lsb, bpos=bpos)
            ret.append(STR)

        elif selem[0]==ord(b'a'):
            buffer, bpos = _balign(buffer, bpos, 4)
            asize, = struct.unpack(L+'I', buffer[:4])
            bpos += 4
            abuffer, buffer = buffer[4:4+asize], buffer[4+asize:]
            #print("array with", bpos, asize, abuffer, file=sys.stderr)
            after = bpos+asize
            ARR = []
            while len(abuffer)>0:
                STR, abuffer, bpos = _decode(selem[1:], abuffer, lsb=lsb, bpos=bpos)
                #print("  [] ->", STR[0], file=sys.stderr)
                ARR.append(STR[0])
            assert bpos==after, (bpos, after)
            ret.append(ARR)

        elif selem[0] in (ord(b'g'),):
            V, buffer, bpos = _short_string(buffer, bpos)            
            ret.append(V)

        elif selem[0] in (ord(b's'),ord(b'o')):
            buffer, bpos = _balign(buffer, bpos, 4)
            asize, = struct.unpack(L+'I', buffer[:4])
            bpos += 4+asize+1
            V, buffer = buffer[4:4+asize], buffer[4+asize+1:]
            ret.append(V.decode('utf-8'))

        elif selem[0]==ord(b'v'):
            vsig, buffer, bpos = _short_string(buffer, bpos)
            #print(" variant sig", vsig, buffer, file=sys.stderr)
            V, buffer, bpos = _decode(vsig, buffer, lsb=lsb, bpos=bpos)
            ret.append(V)

        else:
            S = struct.Struct(L+_decode_plain[selem[0]])
            buffer, bpos = _balign(buffer, bpos, S.size)
            V, = S.unpack(buffer[:S.size])
            buffer = buffer[S.size:]
            bpos += S.size
            ret.append(V)

    return ret, buffer, bpos

def decode(sig, buffer, lsb=_sys_lsb):
    """
    >>> decode(b'y', b'a', lsb=True)
    97
    >>> decode(b'b', b'dcba', lsb=True)
    1633837924
    >>> decode(b'b', b'abcd', lsb=False)
    1633837924
    >>> decode(b'yyyy', b'abcd', lsb=True)
    [97, 98, 99, 100]
    >>> decode(b'yb', b'h   dcba', lsb=True)
    [104, 1633837924]
    >>> decode(b'y(yy)', b'a       bc', lsb=True)
    [97, [98, 99]]
    >>> decode(b'bayb', b'dcba\\x04\\x00\\x00\\x001234dcba', lsb=True)
    [1633837924, [49, 50, 51, 52], 1633837924]
    >>> decode(b'baayb', b'dcba\\x0e\\x00\\x00\\x00\\x02\\x00\\x00\\x0012  \\x02\\x00\\x00\\x0034  dcba', lsb=True)
    [1633837924, [[49, 50], [51, 52]], 1633837924]
    >>> decode(b'yyyyuua(yv)', b"l\\x01\\x00\\x01\\x00\\x00\\x00\\x00\\x01\\x00\\x00\\x00n\\x00\\x00\\x00\\x01\\x01o\\x00\\x15\\x00\\x00\\x00/org/freedesktop/DBus\\x00\\x00\\x00\\x06\\x01s\\x00\\x14\\x00\\x00\\x00org.freedesktop.DBus\\x00\\x00\\x00\\x00\\x02\\x01s\\x00\\x14\\x00\\x00\\x00org.freedesktop.DBus\\x00\\x00\\x00\\x00\\x03\\x01s\\x00\\x05\\x00\\x00\\x00Hello\\x00")
    [108, 1, 0, 1, 0, 1, [[1, ['/org/freedesktop/DBus']], [6, ['org.freedesktop.DBus']], [2, ['org.freedesktop.DBus']], [3, ['Hello']]]]
    >>> decode(b'yyyyuua(yv)', b'l\\2\\1\\1\\v\\0\\0\\0\\1\\0\\0\\0=\\0\\0\\0\\6\\1s\\0\\6\\0\\0\\0:1.336\\0\\0\\5\\1u\\0\\1\\0\\0\\0\\10\\1g\\0\\1s\\0\\0\\7\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0')
    [108, 2, 1, 1, 11, 1, [[6, [':1.336']], [5, [1]], [8, [b's']], [7, ['org.freedesktop.DBus']]]]
    >>> decode(b'yyyyuua(yv)', b'l\\4\\1\\1\\v\\0\\0\\0\\2\\0\\0\\0\\215\\0\\0\\0\\1\\1o\\0\\25\\0\\0\\0/org/freedesktop/DBus\\0\\0\\0\\2\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0\\0\\0\\0\\3\\1s\\0\\f\\0\\0\\0NameAcquired\\0\\0\\0\\0\\6\\1s\\0\\6\\0\\0\\0:1.336\\0\\0\\10\\1g\\0\\1s\\0\\0\\7\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0')
    [108, 4, 1, 1, 11, 2, [[1, ['/org/freedesktop/DBus']], [2, ['org.freedesktop.DBus']], [3, ['NameAcquired']], [6, [':1.336']], [8, [b's']], [7, ['org.freedesktop.DBus']]]]
    >>> decode(b'yyyyuua(yv)', b'l\\1\\0\\1\\23\\0\\0\\0\\2\\0\\0\\0\\177\\0\\0\\0\\1\\1o\\0\\25\\0\\0\\0/org/freedesktop/DBus\\0\\0\\0\\6\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0\\0\\0\\0\\2\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0\\0\\0\\0\\3\\1s\\0\\10\\0\\0\\0AddMatch\\0\\0\\0\\0\\0\\0\\0\\0\\10\\1g\\0\\1s\\0')
    [108, 1, 0, 1, 19, 2, [[1, ['/org/freedesktop/DBus']], [6, ['org.freedesktop.DBus']], [2, ['org.freedesktop.DBus']], [3, ['AddMatch']], [8, [b's']]]]
    >>> decode(b'yyyyuua(yv)', b'l\\2\\1\\1\\0\\0\\0\\0\\3\\0\\0\\0005\\0\\0\\0\\6\\1s\\0\\6\\0\\0\\0:1.336\\0\\0\\5\\1u\\0\\2\\0\\0\\0\\7\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0')
    [108, 2, 1, 1, 0, 3, [[6, [':1.336']], [5, [2]], [7, ['org.freedesktop.DBus']]]]
    >>> decode(b'yyyyuua(yv)',     b'l\\1\\0\\1\\23\\0\\0\\0\\2\\0\\0\\0\\217\\0\\0\\0\\1\\1o\\0\\25\\0\\0\\0/org/freedesktop/DBus\\0\\0\\0\\6\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0\\0\\0\\0\\2\\1s\\0\\24\\0\\0\\0org.freedesktop.DBus\\0\\0\\0\\0\\3\\1s\\0\\10\\0\\0\\0AddMatch\\0\\0\\0\\0\\0\\0\\0\\0\\10\\1g\\0\\1s\\0\\0\\7\\1s\\0\\6\\0\\0\\0:1.336\\0')
    [108, 1, 0, 1, 19, 2, [[1, ['/org/freedesktop/DBus']], [6, ['org.freedesktop.DBus']], [2, ['org.freedesktop.DBus']], [3, ['AddMatch']], [8, [b's']], [7, [':1.336']]]]
    """
    try:
        R, remain, bpos = _decode(sig, buffer, lsb=lsb, bpos=0)
    except ValueError as e:
        raise ValueError("Error %s while decoding '%s' '%s'"%(e, sig, repr(buffer)))
    if bpos!=len(buffer) or len(remain)!=0:
        raise ValueError("Incomplete decode: %s"%repr(remain))
    if len(R)==1:
        return R[0]
    else:
        return R

if __name__=='__main__':
    import doctest
    doctest.testmod()
