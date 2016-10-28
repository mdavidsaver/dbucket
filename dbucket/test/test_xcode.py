
import unittest

from ..xcode import _next_type, encode, decode, Object, Signature, Variant

class TestXCode(unittest.TestCase):
    
    # [('sig', [struct], b'serialized')]
    """
    Actual DBus message headers
    
    Initial "Hello" call

            self.assertEqual(encode(b'yyyyuua(yv)', [108, 1, 0, 1, 0, 1, [[1, Object('/org/freedesktop/DBus')], [6, 'org.freedesktop.DBus'], [2, 'org.freedesktop.DBus'], [3, 'Hello']]], lsb=True)
    b'l\x01\x00\x01\x00\x00\x00\x00\x01\x00\x00\x00n\x00\x00\x00\x01\x01o\x00\x15\x00\x00\x00/org/freedesktop/DBus\x00\x00\x00\x06\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00\x02\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00\x03\x01s\x00\x05\x00\x00\x00Hello\x00'

    Hello reply

            self.assertEqual(encode(b'yyyyuua(yv)', [108, 2, 1, 1, 11, 1, [[6, ':1.336'], [5, Variant(b'u', 1)], [8, Signature('s')], [7, 'org.freedesktop.DBus']]], lsb=True)
    b'l\x02\x01\x01\x0b\x00\x00\x00\x01\x00\x00\x00=\x00\x00\x00\x06\x01s\x00\x06\x00\x00\x00:1.336\x00\x00\x05\x01u\x00\x01\x00\x00\x00\x08\x01g\x00\x01s\x00\x00\x07\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00'
            self.assertEqual(encode(b'yyyyuua(yv)', [108, 4, 1, 1, 11, 2, [[1, Object('/org/freedesktop/DBus')], [2, 'org.freedesktop.DBus'], [3, 'NameAcquired'], [6, ':1.336'], [8, Signature('s')], [7, 'org.freedesktop.DBus']]], lsb=True)
    b'l\x04\x01\x01\x0b\x00\x00\x00\x02\x00\x00\x00\x8d\x00\x00\x00\x01\x01o\x00\x15\x00\x00\x00/org/freedesktop/DBus\x00\x00\x00\x02\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00\x03\x01s\x00\x0c\x00\x00\x00NameAcquired\x00\x00\x00\x00\x06\x01s\x00\x06\x00\x00\x00:1.336\x00\x00\x08\x01g\x00\x01s\x00\x00\x07\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00'
        """
    data = [
        (b'y', 97, b'a'),
        (b'yy', [97, 98], b'ab'),
        (b'b', 0x61626364, b'dcba'),
        # int is aligned to 4 bytes
        (b'yb', [ord('e'), 0x61626364], b'e\x00\x00\x00dcba'),
        # struct is aligned to 8 bytes
        (b'y(yy)', [97, [98, 99]], b'a\x00\x00\x00\x00\x00\x00\x00bc'),
        (b'bayb', [1633837924, [49, 50, 51, 52], 1633837924],
                  b'dcba\x04\x00\x00\x001234dcba'),
        # array
        (b'yayb', [99, [49, 50, 51, 52], 1633837924],
                  b'c\x00\x00\x00\x04\x00\x00\x001234dcba'),
        # Hello method call
        (b'yyyyuua(yv)',
           [108, 1, 0, 1, 0, 1, [[1, Object('/org/freedesktop/DBus')], [6, 'org.freedesktop.DBus'], [2, 'org.freedesktop.DBus'], [3, 'Hello']]],
           b'l\x01\x00\x01\x00\x00\x00\x00\x01\x00\x00\x00n\x00\x00\x00\x01\x01o\x00\x15\x00\x00\x00/org/freedesktop/DBus\x00\x00\x00\x06\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00\x02\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00\x03\x01s\x00\x05\x00\x00\x00Hello\x00'
        ),
    ]
    
    def test_encode(self):
        for sig, inp, expect in self.data:
            actual = encode(sig, inp, lsb=True)
            self.assertEqual(actual, expect)

    def test_decode(self):
        for sig, expect, inp in self.data:
            actual = decode(sig, inp, lsb=True, debug=True)
            self.assertEqual(actual, expect)

class TestEncode(unittest.TestCase):
    data = [
        # Hello method return
        (b'yyyyuua(yv)',
          [108, 2, 1, 1, 11, 1, [[6, ':1.336'], [5, Variant(b'u', 1)], [8, Signature('s')], [7, 'org.freedesktop.DBus']]],
          b'l\x02\x01\x01\x0b\x00\x00\x00\x01\x00\x00\x00=\x00\x00\x00\x06\x01s\x00\x06\x00\x00\x00:1.336\x00\x00\x05\x01u\x00\x01\x00\x00\x00\x08\x01g\x00\x01s\x00\x00\x07\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00'
        ),
        # NamedAcquired signal
        (b'yyyyuua(yv)',
          [108, 4, 1, 1, 11, 2, [[1, Object('/org/freedesktop/DBus')], [2, 'org.freedesktop.DBus'], [3, 'NameAcquired'], [6, ':1.336'], [8, Signature('s')], [7, 'org.freedesktop.DBus']]],
          b'l\x04\x01\x01\x0b\x00\x00\x00\x02\x00\x00\x00\x8d\x00\x00\x00\x01\x01o\x00\x15\x00\x00\x00/org/freedesktop/DBus\x00\x00\x00\x02\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00\x03\x01s\x00\x0c\x00\x00\x00NameAcquired\x00\x00\x00\x00\x06\x01s\x00\x06\x00\x00\x00:1.336\x00\x00\x08\x01g\x00\x01s\x00\x00\x07\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00'
        ),
    ]

    def test_encode(self):
        for sig, inp, expect in self.data:
            actual = encode(sig, inp, lsb=True)
            self.assertEqual(actual, expect)

class TestDecode(unittest.TestCase):
    data = [
        # Hello method return
        (b'yyyyuua(yv)',
          [108, 2, 1, 1, 11, 1, [[6, ':1.336'], [5, 1], [8, b's'], [7, 'org.freedesktop.DBus']]],
          b'l\x02\x01\x01\x0b\x00\x00\x00\x01\x00\x00\x00=\x00\x00\x00\x06\x01s\x00\x06\x00\x00\x00:1.336\x00\x00\x05\x01u\x00\x01\x00\x00\x00\x08\x01g\x00\x01s\x00\x00\x07\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00'
        ),
        # NamedAcquired signal
        (b'yyyyuua(yv)',
          [108, 4, 1, 1, 11, 2, [[1, '/org/freedesktop/DBus'], [2, 'org.freedesktop.DBus'], [3, 'NameAcquired'], [6, ':1.336'], [8, b's'], [7, 'org.freedesktop.DBus']]],
          b'l\x04\x01\x01\x0b\x00\x00\x00\x02\x00\x00\x00\x8d\x00\x00\x00\x01\x01o\x00\x15\x00\x00\x00/org/freedesktop/DBus\x00\x00\x00\x02\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00\x03\x01s\x00\x0c\x00\x00\x00NameAcquired\x00\x00\x00\x00\x06\x01s\x00\x06\x00\x00\x00:1.336\x00\x00\x08\x01g\x00\x01s\x00\x00\x07\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00'
        ),
    ]

    def test_decode(self):
        for sig, expect, inp in self.data:
            actual = decode(sig, inp, lsb=True, debug=True)
            self.assertEqual(actual, expect)

class TestSigSplit(unittest.TestCase):
    def test_split(self):
        self.assertEqual(_next_type(b'y'), (b'y', b''))
        self.assertEqual(_next_type(b'yy'), (b'y', b'y'))
        self.assertEqual(_next_type(b'yyy'), (b'y', b'yy'))
        self.assertEqual(_next_type(b'ay'),  (b'ay', b''))
        self.assertEqual(_next_type(b'ayy'), (b'ay', b'y'))
        self.assertEqual(_next_type(b'yay'), (b'y', b'ay'))
        self.assertEqual(_next_type(b'a(ii)'), (b'a(ii)', b''))
        self.assertEqual(_next_type(b'a(ii)i'),(b'a(ii)', b'i'))
        self.assertEqual(_next_type(b'aaii'),  (b'aai', b'i'))
        self.assertEqual(_next_type(b'aa(ai(yay)i)i'), (b'aa(ai(yay)i)', b'i'))
