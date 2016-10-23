
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
            SH = self.bpos%ST.size
            if SH:
                SH = ST.size-SH
            B = self.buf[SH:SH+ST.size]
            assert len(B)==ST.size, (repr(B), ST.size, spec)
            V, = ST.unpack(B)
            self.buf = self.buf[SH+ST.size:]
            self.bpos += SH+ST.size
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
        # spos now after 'a'
        BC = self.stack[-1].pop()

        # find the end of the array element sig
        SP, depth = self.spos, 0
        while SP<len(self.sig):
            if self.sig[SP]==b'(':
                depth += 1
            elif self.sig[SP]==b')':
                depth -= 1
            SP += 1
            if self.sig[SP]==b'a':
                continue
            if depth==0:
                break

        if depth!=0:
            raise ValueError("Missing ')' in '%s'"%self.sig[self.spos:])
        elif self.sig[SP-1]==b'a':
            raise ValueError("expect type after 'a'")

        asig = self.sig[self.spos:SP]
        abuf = self.buf[0:BC]
        #print('before array', self.sig, asig, abuf, file=sys.stderr)
        afterbuf = self.buf[BC:]
        newpos = self.bpos + BC

        # buffer pos after array, sig. pos after array, original sig, remaining buf
        # self.spos, self.sig, self.buf = self.astack.pop()
        self.astack.append((SP, self.sig, afterbuf))
        self.stack.append([])

        self.sig  = asig
        self.spos = 0
        self.buf  = abuf
        #print('after array', self.spos, self.bpos, abuf, self.buf, file=sys.stderr)

    def _decode(self):
        while self.spos<len(self.sig) or len(self.astack)>0:
            #print('D', self.spos, self.sig, len(self.stack), self.bpos, self.buf, file=sys.stderr)
            # iterate until sig consumed
            while self.spos<len(self.sig):
                #print('P', self.spos, self.bpos, self.sig[self.spos:], self.buf[self.bpos:], file=sys.stderr)
                S=self.sig[self.spos]
                self._D[S](self)

            if self.spos!=len(self.sig):
                raise ValueError('Sig over consumed %s %s'%(self.sig, self.spos, len(self.sig)))

            if len(self.astack)>0:
                if len(self.buf)==0:
                    # finished last element
                    self.spos, self.sig, self.buf = self.astack.pop()
                    ARR = self.stack.pop()
                    self.stack[-1].append(ARR)
                else:
                    self.spos = 0

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
        [1633837924, [49, 50, 51, 52], 1633837924]
        """
        try:
            if len(sig)==0 or sig[-1]==b'a':
                raise ValueError("Invalid sig '%s'"%sig)
            self.sig = sig
            self.buf = buf
            self.spos = 0
            self.bpos = 0
            self.stack = [[]]
            self.astack = []
            self._decode()
            if len(buf)!=self.bpos:
                raise ValueError('buf not fully consumed %d %d'%(len(buf), self.bpos))
            elif len(self.stack)!=1:
                raise ValueError('Decode leaves invalid stack: %s'%self.stack)
            R = self.stack[0]
            if len(R)==1:
                return R[0]
            else:
                return R
        except ValueError as e:
            raise ValueError("DDecode fails: %s: sig=%s buf=%s"%(e, sig, buf))

if __name__=='__main__':
    import doctest
    doctest.testmod()
