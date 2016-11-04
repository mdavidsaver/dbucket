import logging
import unittest
import asyncio
asyncio.get_event_loop().set_debug(True)

from ..conn import SignalMatch, BusEvent

from .util import FakeConnection

class TestSignalMatch(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.get_event_loop()
        self.conn = FakeConnection()

    def tearDown(self):
        self.assertListEqual([], self.conn._signals)

    def test_exact(self):
        M = SignalMatch(self.conn,
            sender='SEND',
            destination='DEST',
            interface='IFACE',
            member='METH',
            path='/PATH',
        )
        self.conn._signals.append(M)
        conds = set(M._expr.split(','))
        self.assertSetEqual(conds, set([
            "destination='DEST'",
            "interface='IFACE'",
            "member='METH'",
            "path='/PATH'",
            "sender='SEND'",
        ]))

        E = BusEvent(3, 1, [(1, '/PATH'), (2, 'IFACE'), (3, 'METH'), (6, 'DEST'), (7, 'SEND')], None)

        self.assertTrue(M._emit(E))
        self.assertIn(M, self.conn._signals)

        self.assertEqual(M._Q.get_nowait(), (E, SignalMatch.NORMAL))

        self.loop.run_until_complete(M.close())
        self.assertNotIn(M, self.conn._signals)

    def test_mismatch(self):
        M = SignalMatch(self.conn,
            sender='SEND',
            destination='DEST',
            interface='IFACE',
            member='METH',
            path='/PATH',
        )
        self.conn._signals.append(M)
        conds = set(M._expr.split(','))
        self.assertSetEqual(conds, set([
            "destination='DEST'",
            "interface='IFACE'",
            "member='METH'",
            "path='/PATH'",
            "sender='SEND'",
        ]))

        E = BusEvent(3, 1, [(1, '/PATH'), (2, 'IFACE'), (3, 'OTHER'), (6, 'DEST'), (7, 'SEND')], None)

        self.assertFalse(M._emit(E))
        self.assertTrue(M._Q.empty())

        self.loop.run_until_complete(M.close())
        self.assertNotIn(M, self.conn._signals)
