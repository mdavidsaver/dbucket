import logging
log = logging.getLogger(__name__)
import unittest
import asyncio
from collections import defaultdict
asyncio.get_event_loop().set_debug(True)

from .util import inloop
from ..signal import SignalQueue, Condition
from ..conn import BusEvent, SIGNAL, ConnectionClosed

class FakeConnection(object):
    log = logging.getLogger(__name__+'.FakeConnection')
    def __init__(self, loop):
        self._loop = loop
        self.matches = defaultdict(set)
    @asyncio.coroutine
    def AddMatch(self, obj, expr):
        log.debug('AddMatch %s %s -> %s', obj, expr, self.matches)
        self.matches[expr].add(obj)
    @asyncio.coroutine
    def RemoveMatch(self, obj, expr):
        log.debug('RemoveMatch %s %s <- %s', obj, expr, self.matches)
        L = self.matches[expr]
        L.remove(obj)
        if len(L)==0:
            del self.matches[expr]

class TestCond(unittest.TestCase):
    evt1 = BusEvent(SIGNAL, 1, [
        (1, '/path'),
        (2, 'i.face'),
        (3, 'member'),
        (6, 'dest'),
        (7, ':1.1'), # sender
    ], None)

    evt2 = BusEvent(SIGNAL, 2, [
        (1, '/path'),
        (2, 'i.face'),
        (3, 'member'),
        # no destination
        (7, ':1.1'), # sender
    ], None)

    evt3 = BusEvent(SIGNAL, 1, [
        (1, '/path/more'),
        (2, 'i.face2'),
        (3, 'other'),
        (6, 'destination'),
        (7, ':1.2'), # sender
    ], None)

    def test_wildcard(self):
        'Match anything'
        cond = Condition()
        self.assertTrue(cond.test(self.evt1))
        self.assertTrue(cond.test(self.evt2))
        self.assertTrue(cond.test(self.evt3))

    def test_path(self):
        cond = Condition(path='/path')
        self.assertTrue(cond.test(self.evt1))
        self.assertTrue(cond.test(self.evt2))
        self.assertFalse(cond.test(self.evt3))

    def test_iface(self):
        cond = Condition(interface='i.face2')
        self.assertFalse(cond.test(self.evt1))
        self.assertFalse(cond.test(self.evt2))
        self.assertTrue(cond.test(self.evt3))

    def test_member(self):
        cond = Condition(member='member')
        self.assertTrue(cond.test(self.evt1))
        self.assertTrue(cond.test(self.evt2))
        self.assertFalse(cond.test(self.evt3))

    def test_dest(self):
        cond = Condition(destination='dest')
        self.assertTrue(cond.test(self.evt1))
        self.assertFalse(cond.test(self.evt2))
        self.assertFalse(cond.test(self.evt3))

    def test_sender(self):
        cond = Condition(sender=':1.1')
        self.assertTrue(cond.test(self.evt1))
        self.assertTrue(cond.test(self.evt2))
        self.assertFalse(cond.test(self.evt3))

class TestQueue(unittest.TestCase):
    evt1 = BusEvent(SIGNAL, 1, [
        (1, '/path'),
        (2, 'i.face'),
        (3, 'member'),
        (6, 'dest'),
        (7, ':1.1'), # sender
    ], None)

    @inloop
    @asyncio.coroutine
    def setUp(self):
        self.conn = FakeConnection(self.loop)
        self.Q = SignalQueue(self.conn, qsize=2)

    def tearDown(self):
        self.assertDictEqual(self.conn.matches, {})

    @inloop
    @asyncio.coroutine
    def test_Q(self):
        self.Q._emit(self.evt1)

        self.assertTrue(self.Q.empty())
        self.assertFalse(self.Q.full())
        self.assertEqual(self.Q.qsize(), 0)

        C = yield from self.Q.add() # wildcard
        try:

            self.Q._emit(self.evt1)

            self.assertFalse(self.Q.empty())
            self.assertFalse(self.Q.full())
            self.assertEqual(self.Q.qsize(), 1)

            evt, sts = self.Q.poll()

            self.assertIs(evt, self.evt1)

            self.assertTrue(self.Q.empty())
            self.assertFalse(self.Q.full())
            self.assertEqual(self.Q.qsize(), 0)

            self.assertRaises(asyncio.QueueEmpty, self.Q.poll)
        finally:
            yield from self.Q.remove(C)

    @inloop
    @asyncio.coroutine
    def test_match(self):
        C = yield from self.Q.add(member='test')
        try:
            self.assertIn("member='test'", self.conn.matches)
        finally:
            yield from self.Q.remove(C)

    @inloop
    @asyncio.coroutine
    def test_oflow(self):
        C = yield from self.Q.add()
        try:

            self.Q._emit(self.evt1)
            self.Q._emit(self.evt1)
            self.Q._emit(self.evt1)

            evt, sts = self.Q.poll()
            self.assertIs(evt, self.evt1)
            self.assertEqual(sts, self.Q.NORMAL)

            self.Q._emit(self.evt1)
                

            evt, sts = self.Q.poll()
            self.assertIs(evt, self.evt1)
            self.assertEqual(sts, self.Q.NORMAL)

            evt, sts = self.Q.poll()
            self.assertIs(evt, self.evt1)
            self.assertEqual(sts, self.Q.OFLOW)

            self.assertTrue(self.Q.empty())

        finally:
            yield from self.Q.remove(C)

    @inloop
    @asyncio.coroutine
    def test_close(self):
        C = yield from self.Q.add()
        try:
            self.Q._emit(self.evt1)

            self.assertEqual(self.Q._done, 0)

            yield from self.Q.close()

            self.assertEqual(self.Q._done, 1)

            evt, sts = self.Q.poll()
            self.assertIs(evt, self.evt1)
            self.assertEqual(sts, self.Q.NORMAL)
            self.assertEqual(self.Q._done, 1)

            evt, sts = self.Q.poll(throw_done=False)
            self.assertIs(evt, None)
            self.assertEqual(sts, self.Q.DONE)
            self.assertEqual(self.Q._done, 2)

            evt, sts = self.Q.poll(throw_done=False)
            self.assertIs(evt, None)
            self.assertEqual(sts, self.Q.DONE)

        finally:
            yield from self.Q.remove(C)
