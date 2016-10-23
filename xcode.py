
import logging, sys
_log = logging.getLogger(__name__)

import struct

_sys_lsb = sys.byteorder=='little'

class XCode(object):
    def __init__(self, lsb):
        L = '<' if lsb else '>'
        
        self._D = {
           ord(b'('):self._decode_struct_start,
           ord(b')'):self._decode_struct_end,
           ord(b'a'):self._decode_array,
        }
        for C, spec in (
                (b'y', 'B'),
                (b'b', 'I'),
                (b'n', 'h'),
                (b'q', 'H'),
                (b'i', 'i'),
                (b'u', 'I'),
                (b'x', 'l'),
                (b't', 'L'),
                (b'd', 'd'),
                (b'h', 'I'),
            ):
            self._D[ord(C)] = self.make_fixed_decode(L+spec)

    @staticmethod
    def make_fixed_decode(spec):
        ST = struct.Struct(spec)
        def _decode(self):
            SH = self.pos%ST.size
            if SH:
                SH = ST.size-SH
            B = self.buf[SH:SH+ST.size]
            assert len(B)==ST.size, (repr(B), ST.size, spec)
            V, = ST.unpack(B)
            self.buf = self.buf[SH+ST.size:]
            self.pos += SH+ST.size
            self.stack[-1].append(V)
            self.spos += 1
        return _decode

    @staticmethod
    def _decode_struct_start(self):
        self.stack.append([])
        self.spos += 1

    @staticmethod
    def _decode_struct_end(self):
        S = self.stack.pop()
        self.stack[-1].append(S)
        self.spos += 1

    @staticmethod
    def _decode_array(self):
        # uint32 with size in bytes
        assert len(self.buf)>=4
        self._D[ord(b'b')](self)
        BC = self.stack[-1].pop()

        arr = self.buf[0:BC]
        self.buf = self.buf[BC:]
        self.pos += BC
        self.spos += 1
        print('after array', self.spos, self.pos, arr, self.buf, file=sys.stderr)

        self.stack[-1].append(arr)

    def decode(self, sig, buf):
        """
        >>> L=XCode(True)
        >>> B=XCode(False)
        >>> L.decode(b'y', b'a')
        97
        >>> L.decode(b'b', b'dcba')
        1633837924
        >>> B.decode(b'b', b'abcd')
        1633837924
        >>> L.decode(b'yyyy', b'abcd')
        [97, 98, 99, 100]
        >>> L.decode(b'yb', b'hgfedcba') # gfe is pading
        [104, 1633837924]
        >>> L.decode(b'y(yy)', b'abc')
        [97, [98, 99]]
        >>> L.decode(b'bayb', b'dcba\\x04\\x00\\x00\\x001234dcba')
        [1633837924, [49, 47, 48, 49], 1633837924]
        """
        self.buf = buf
        self.spos= 0
        self.pos = 0
        self.stack = [[]]
        while self.spos<len(sig):
            print(self.spos, self.pos, sig[self.spos:], file=sys.stderr)
            SP = self.spos
            S=sig[SP]
            self._D[S](self)
            assert self.spos>SP, (sig, buf, self.spos, SP)
        if len(sig)!=self.spos:
            raise ValueError('Sig over consumed %s %s'%(sig, self.spos))
        elif len(buf)!=self.pos:
            raise ValueError('buf not fully consumed %d %d'%(len(buf), self.pos))
        if len(self.stack)!=1:
            raise ValueError('Decode leaves invalid stack: %s'%self.stack)
        R = self.stack[0]
        if len(R)==1:
            return R[0]
        else:
            return R

if __name__=='__main__':
    import doctest
    doctest.testmod()
