
import logging, sys
_log = logging.getLogger(__name__)

from collections import OrderedDict
import struct

__all__ = [
    'encode',
    'decode',
    'Variant',
    'Object',
    'Signature',
    'Integer',
]

_sys_lsb = sys.byteorder=='little'

def _next_type(sig):
    """ Split type signature after first element (POD, array, or sub-struct)
    """
    assert isinstance(sig, bytes), 'Signature must be bytes'
    pos, A, B = 0, 0, 0
    while pos<len(sig):
        C = sig[pos]
        if C==ord(b'('):
            A += 1
        elif C==ord(b')'):
            A -= 1
        elif C==ord(b'{'):
            B += 1
        elif C==ord(b'}'):
            B -= 1
        pos += 1
        if A==0 and B==0 and C!=ord(b'a'):
            break
    if len(sig)==0 or pos==0:
        raise ValueError("Incomplete sig")
    elif A !=0:
        raise ValueError("Unbalenced ()")
    elif B !=0:
        raise ValueError("Unbalenced {}")
    return sig[:pos], sig[pos:]

def sigsplit(sig):
    """Split dbus signature
    """
    while len(sig)>0:
        S, sig = _next_type(sig)
        yield S

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

class Decoder(object):
    debug = False
    _log = logging.getLogger(__name__+'.decode')
    def __init__(self, buf, pos, lsb):
        self.buffer, self.bpos = buf, pos
        self.lsb, self._L = lsb, '<' if lsb else '>'

    def __repr__(self):
        return 'Decoder(pos=%d, lsb=%s, buf="%s")'%(self.bpos, self.lsb, self.buffer)

    def _dalign(self, size):
        M = self.bpos%size
        if M:
            N = size-M
            self.bpos += N
            self.buffer = self.buffer[N:]
            if self.debug:
                self._log.debug('Skip %d bytes', N)

    def _short_string(self):
        size = self.buffer[0]
        ret, self.buffer, self.bpos = self.buffer[1:1+size], self.buffer[2+size:], self.bpos+2+size
        return ret

    def decode(self, sig):
        if self.debug:
            self._log.debug('decode(%s) -> %s', sig, self.buffer)
        assert len(sig)>0, (sig, self)
        assert sig[0]!=ord('{'), sig
        ret = []

        while len(sig)>0:
            selem, sig = _next_type(sig)
            if self.debug:
                self._log.debug('Next elem %s at %d', selem, self.bpos)
            if selem[0]==ord(b'('):
                assert selem[-1]==ord(b')')
                self._dalign(8)
                ret.append(self.decode(selem[1:-1]))

            elif selem[0]==ord(b'a'):
                esig = selem[1:]
                adict = esig[0]==ord('{')
                if adict:
                    esig = b'('+esig[1:-1]+b')' # decode as a struct/tuple

                if self.debug:
                    self._log.debug('Decode array %s at %d', esig, self.bpos)

                # decode array size (in bytes)
                self._dalign(4)
                asize, = struct.unpack(self._L+'I', self.buffer[:4])
                self.buffer = self.buffer[4:]
                self.bpos += 4

                # now pad to array element boundary.
                # we're already aligned to 4 bytes, so only need to do more when
                # element alignment is 8 (int64, double, sub-array, struct, or dict
                if chr(esig[0]) in 'xtda({':
                    self._dalign(8)

                self.buffer, afterbuffer = self.buffer[:asize], self.buffer[asize:]
                assert len(self.buffer)==asize, (len(self.buffer), asize, self.buffer)

                after = self.bpos+asize
                ARR = []
                while len(self.buffer)>0:
                    ARR.append(self.decode(esig)[0])
                assert self.bpos==after, (self.bpos, after) # array decode
                if adict:
                    ARR = OrderedDict(ARR)
                ret.append(ARR)
                self.buffer = afterbuffer

            elif selem[0] in (ord(b'g'),):
                ret.append(self._short_string())

            elif selem[0] in (ord(b's'),ord(b'o')):
                self._dalign(4)
                asize, = struct.unpack(self._L+'I', self.buffer[:4])
                self.bpos += 4+asize+1
                V, self.buffer = self.buffer[4:4+asize], self.buffer[4+asize+1:]
                ret.append(V.decode('utf-8'))

            elif selem[0]==ord(b'v'):
                V = self.decode(self._short_string())
                assert len(V)==1, V
                ret.append(V[0])

            else:
                S = struct.Struct(self._L+_decode_plain[selem[0]])
                self._dalign(S.size)
                try:
                    V, = S.unpack(self.buffer[:S.size])
                except struct.error as e:
                    raise ValueError("Error %s decoding %s with %s at %s"%(e, self.buffer[:S.size], _decode_plain[selem[0]], self.bpos))
                self.buffer = self.buffer[S.size:]
                self.bpos += S.size
                ret.append(V)

        return tuple(ret)

def decode(sig, buffer, lsb=_sys_lsb, bpos=0, debug=False):
    """Decode a python value from the given bytestring with the given signature bytestring

    :param bytes sig: DBus type signature
    :param bytes buffer: Byte buffer to decode
    :param bool lsb: True if buffer was encoded as LSB, False for MSB.  Defaults to host byte order.
    :param int bpos: Offset of buffer[0] is original bytestring.  Used in dbus alignment rules.
    :param bool debug: Enabled verbose debugging of decoder processing
    :returns: The decoded value.
    """
    D = Decoder(buffer, bpos, lsb)
    D.debug = debug
    if debug:
        D._log.debug("Start decode %s %s", sig, buffer)
    try:
        R = D.decode(sig)
        remain, bpos = D.buffer, D.bpos
    except Exception as e:
        raise ValueError("Error %s while decoding %s %s.  %s"%(e, sig, repr(buffer), D))
    if bpos!=len(buffer) or len(remain)!=0:
        raise ValueError("Incomplete decode: %s"%repr(remain))
    if isinstance(R, tuple) and len(R)==1:
        return R[0]
    else:
        return R

class Variant(object):
    """Value wrapper to force a specific DBus type when encoding as a Variant
    
    :param bytes code: The DBus type signature of val
    :param val: A python value
    """
    def __init__(self, code, val):
        self.code, self.val = code, val
    def __repr__(self):
        return '%s(%s, %s)'%(self.__class__.__name__, self.code, self.val)

class Signature(str, Variant):
    "Wrap as a DBus Signature (code 'g')"
    def __init__(self, val):
        str.__init__(val)
        Variant.__init__(self, b'g', val.encode('utf-8'))

class Object(str, Variant):
    "Wrap as a DBus Object path (code 'o')"
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

class Encoder(object):
    debug = False
    _log = logging.getLogger(__name__+'.encode')
    def __init__(self, pos=0, lsb=_sys_lsb):
        self.lsb, self.L = lsb, '<' if lsb else '>'
        self.bpos = pos
        self.bufs = []

    def align(self, size):
        M = self.bpos%size
        if M:
            N = size-M
            self.bpos += N
            self.bufs.append(b'\0'*N)

    def encode(self, sig, val):
        assert isinstance(val, tuple), val
        if self.debug:
            self._log.debug("Encode %s %s.  out pos %d", sig, val, self.bpos)

        for mem in val:
            selem, sig =_next_type(sig)
            if self.debug:
                self._log.debug("Encode member %s %s.  out pos %d", selem, mem, self.bpos)

            if selem[0]==ord(b'('):
                assert selem[-1]==ord(b')'), selem
                self.align(8)

                self.encode(selem[1:-1], mem)

            elif selem[0]==ord(b'a'):
                esig = selem[1:]
                adict = esig[0]==ord('{')
                if adict:
                    esig = b'('+esig[1:-1]+b')' # decode as a struct/tuple
                    mem = mem.items()

                if self.debug:
                    self._log.debug("Encode array %s %s", esig, mem)

                self.align(4)

                sizeidx = len(self.bufs)
                self.bufs.append(b'\0\0\0\0') # placeholder
                self.bpos += 4

                # now pad to array element boundary.
                # we're already aligned to 4 bytes, so only need to do more when
                # element alignment is 8 (int64, double, sub-array, struct, or dict
                if chr(esig[0]) in 'xtda({':
                    self.align(8)

                # array size doesn't include padding before first element
                ipos = self.bpos

                for E in mem:
                    if self.debug:
                        self._log.debug("Encode array element %s %s.  out pos %d", esig, E, self.bpos)
                    self.encode(esig, (E,))

                # insert real size
                self.bufs[sizeidx] = struct.pack(self.L+"I", self.bpos-ipos)

            elif selem[0] in (ord(b'g'),):
                N = len(mem)
                assert N<=255
                self.bpos += N+2
                self.bufs.append(struct.pack("B", N)+mem+b'\0')

            elif selem[0] in (ord(b's'),ord(b'o')):
                N = len(mem)
                self.bpos += N+5
                if hasattr(mem, 'encode'):
                    mem = mem.encode('utf-8')
                assert isinstance(mem, bytes), mem
                self.bufs.append(struct.pack(self.L+"I", N)+mem+b'\0')

            elif selem[0]==ord(b'v'):
                vsig, mem = _infer_sig(mem)
                #print(" Encode Variant", vsig, mem, file=sys.stderr)
                self.bufs.append(struct.pack("B", len(vsig))+vsig+b'\0')
                self.bpos += len(vsig)+2

                self.encode(vsig, (mem,))

            else:
                S = struct.Struct(self.L+_decode_plain[selem[0]])
                self.align(S.size)

                self.bpos += S.size
                try:
                    self.bufs.append(S.pack(mem))
                except struct.error as e:
                    raise ValueError("%s while encoding %s with %s"%(e, mem, _decode_plain[selem[0]]))

        if self.debug:
            self._log.debug("After %s %s -> %s", sig, val, self.bufs)
        if len(sig)>0:
            raise ValueError("Incomplete value, stops before '%s'"%sig)

def encode(sig, val, lsb=_sys_lsb, debug=False):
    """Encode the given object using the given signature bytestring.

    :param bytes sig: DBus type signature
    :param val: The python value to encode
    :param bool lsb: True if buffer was encoded as LSB, False for MSB.  Defaults to host byte order.
    :param int bpos: Offset of buffer[0] is original bytestring.  Used in dbus alignment rules.
    :param bool debug: Enabled verbose debugging of decoder processing
    :returns: A bytestring
    :rtype: bytes
    """
    if not isinstance(val, tuple):
        val = (val,)
    E = Encoder(0, lsb)
    E.debug = debug
    try:
        E.encode(sig, val)
        return b''.join(E.bufs)
    except Exception as e:
        _log.exception('oops')
        raise ValueError("Error '%s' while encoding %s with (%s) %s.  near %d"%(e, sig, type(val), repr(val), E.bpos))
