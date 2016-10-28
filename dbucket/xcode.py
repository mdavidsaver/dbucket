
import logging, sys
_log = logging.getLogger(__name__)

import struct

_sys_lsb = sys.byteorder=='little'

def _next_type(sig):
    """ Split type signature after first element (POD, array, or sub-struct)
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

def _dalign(buffer, bpos, size):
    """
    >>> buf=b'12345678'
    >>> _dalign(buf[0:], 0, 4)
    (b'12345678', 0)
    >>> _dalign(buf[1:], 1, 4)
    (b'5678', 4)
    >>> _dalign(buf[2:], 2, 4)
    (b'5678', 4)
    >>> _dalign(buf[3:], 3, 4)
    (b'5678', 4)
    >>> _dalign(buf[4:], 4, 4)
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
    assert len(sig)>0, (sig, buffer, lsb, bpos)
    ret = []
    #print("_decode", sig, buffer, bpos, file=sys.stderr)

    while len(sig)>0:
        selem, sig = _next_type(sig)
        #print(" >>>", selem, bpos, buffer, file=sys.stderr)
        if selem[0]==ord(b'('):
            assert selem[-1]==ord(b')')
            buffer, bpos = _dalign(buffer, bpos, 8)
            STR, buffer, bpos = _decode(selem[1:-1], buffer, lsb=lsb, bpos=bpos)
            ret.append(STR)

        elif selem[0]==ord(b'a'):
            buffer, bpos = _dalign(buffer, bpos, 4)
            asize, = struct.unpack(L+'I', buffer[:4])
            bpos += 4
            abuffer, buffer = buffer[4:4+asize], buffer[4+asize:]
            assert len(abuffer)==asize, (len(abuffer), asize, abuffer)
            #print("array with", bpos, asize, abuffer, file=sys.stderr)
            after = bpos+asize
            ARR = []
            while len(abuffer)>0:
                STR, abuffer, bpos = _decode(selem[1:], abuffer, lsb=lsb, bpos=bpos)
                #print("  [] ->", STR[0], file=sys.stderr)
                ARR.append(STR[0])
            assert bpos==after, (bpos, after) # array decode
            ret.append(ARR)

        elif selem[0] in (ord(b'g'),):
            V, buffer, bpos = _short_string(buffer, bpos)            
            ret.append(V)

        elif selem[0] in (ord(b's'),ord(b'o')):
            buffer, bpos = _dalign(buffer, bpos, 4)
            asize, = struct.unpack(L+'I', buffer[:4])
            bpos += 4+asize+1
            V, buffer = buffer[4:4+asize], buffer[4+asize+1:]
            ret.append(V.decode('utf-8'))
            assert isinstance(ret[-1], str), ret[-1]

        elif selem[0]==ord(b'v'):
            vsig, buffer, bpos = _short_string(buffer, bpos)
            #print(" variant sig", vsig, buffer, file=sys.stderr)
            V, buffer, bpos = _decode(vsig, buffer, lsb=lsb, bpos=bpos)
            assert len(V)==1, V
            ret.append(V[0])

        else:
            S = struct.Struct(L+_decode_plain[selem[0]])
            buffer, bpos = _dalign(buffer, bpos, S.size)
            V, = S.unpack(buffer[:S.size])
            buffer = buffer[S.size:]
            bpos += S.size
            ret.append(V)

    return ret, buffer, bpos

def decode(sig, buffer, lsb=_sys_lsb, bpos=0):
    """
    """
    try:
        R, remain, bpos = _decode(sig, buffer, lsb=lsb, bpos=bpos)
    except Exception as e:
        raise ValueError("Error %s while decoding %s %s"%(e, sig, repr(buffer)))
    if bpos!=len(buffer) or len(remain)!=0:
        raise ValueError("Incomplete decode: %s"%repr(remain))
    if len(R)==1:
        return R[0]
    else:
        return R

def _ealign(bufs, bpos, size):
    M = bpos%size
    if M:
        N = size-M
        bpos += N
        bufs.append(b'\0'*N)
    return bufs, bpos

class Variant(object):
    """Value wrapper to force a specific DBus type when encoding as a Variant
    """
    def __init__(self, code, val):
        self.code, self.val = code, val
    def __repr__(self):
        return '%s(%s, %s)'%(self.__class__.__name__, self.code, self.val)

class Signature(str, Variant):
    def __init__(self, val):
        str.__init__(val)
        Variant.__init__(self, b'g', val.encode('utf-8'))

class Object(str, Variant):
    def __init__(self, val):
        str.__init__(val)
        Variant.__init__(self, b'o', val.encode('utf-8'))
        #print(" Object", self, file=sys.stderr)

class Integer(int, Variant):
    def __init__(self, val):
        int.__init__(val)
        Variant.__init__(self, b'i', val)
        #print(" Object", self, file=sys.stderr)

def _infer_sig(val):
    if isinstance(val, Variant):
        #print(" Explicit Variant", val.code, val.val, file=sys.stderr)
        return val.code, val.val
    elif isinstance(val, str):
        #print(" Infer string", val, file=sys.stderr)
        return b's', val.encode('utf-8')
    elif isinstance(val, bytes):
        #print(" Infer string", val, file=sys.stderr)
        return b's', val
    raise ValueError("Can't infer variant type for '%s'"%val)

def _encode(sig, val, lsb=_sys_lsb, bpos=0):
    L = '<' if lsb else '>'
    if not isinstance(val, (tuple, list)):
        val = (val,)
    mem = 0
    bufs = []
    for mem in val:
        selem, sig =_next_type(sig)
        #print("E", selem, sig, mem, file=sys.stderr)

        if selem[0]==ord(b'('):
            assert selem[-1]==ord(b')')
            bufs, bpos = _ealign(bufs, bpos, 8)
            sbufs, bpos = _encode(selem[1:-1], mem, lsb=lsb, bpos=bpos)
            bufs.extend(sbufs)

        elif selem[0]==ord(b'a'):
            #print(" array", selem[0], mem, file=sys.stderr)
            bufs, bpos = _ealign(bufs, bpos, 4)
            bpos += 4 # account for size here, add after it is known

            ipos = bpos
            ebufs = []
            for E in mem:
                #print("  elem", selem[1:], E, file=sys.stderr)
                mbufs, bpos = _encode(selem[1:], [E], lsb=lsb, bpos=bpos)
                ebufs.extend(mbufs)

            bufs.append(struct.pack(L+"I", bpos-ipos))
            bufs.extend(ebufs)

        elif selem[0] in (ord(b'g'),):
            N = len(mem)
            assert N<=255
            bpos += N+2
            bufs.append(struct.pack("B", N)+mem+b'\0')

        elif selem[0] in (ord(b's'),ord(b'o')):
            N = len(mem)
            bpos += N+5
            if hasattr(mem, 'encode'):
                mem = mem.encode('utf-8')
            bufs.append(struct.pack(L+"I", N)+mem+b'\0')

        elif selem[0]==ord(b'v'):
            vsig, mem = _infer_sig(mem)
            #print(" Encode Variant", vsig, mem, file=sys.stderr)
            bufs.append(struct.pack("B", len(vsig))+vsig+b'\0')
            bpos += len(vsig)+2

            vbufs, bpos = _encode(vsig, mem, lsb=lsb, bpos=bpos)
            bufs.extend(vbufs)

        else:
            S = struct.Struct(L+_decode_plain[selem[0]])
            bufs, bpos = _ealign(bufs, bpos, S.size)
            bpos += S.size
            bufs.append(S.pack(mem))

    if len(sig)>0:
        raise ValueError("Incomplete value, stops before '%s'"%sig)
    return bufs, bpos

def encode(sig, val, lsb=_sys_lsb):
    """
    """
    try:
        bufs, bpos = _encode(sig, val, lsb=lsb, bpos=0)
    except Exception as e:
        raise ValueError("Error %s while encoding %s with %s"%(e, sig, val))
    return b''.join(bufs)

if __name__=='__main__':
    import doctest
    doctest.testmod()
