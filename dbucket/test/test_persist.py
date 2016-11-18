
import unittest, asyncio

from ..conn import DBUS, DBUS_PATH
from ..persist import PersistentConnection
from .util import test_bus, test_bus_info, inloop

class TestPersistent(unittest.TestCase):
    timeout = 1.0

    def setUp(self):
        self.conn = PersistentConnection(test_bus_info)

    @inloop
    @asyncio.coroutine
    def tearDown(self):
        yield from self.conn.close()

    @inloop
    @asyncio.coroutine
    def test_reconn(self):
        yield from self.conn.connect

        before = self.conn.name

        test_bus().stop()

        yield from self.conn.disconnect

        test_bus().start()

        yield from self.conn.connect

        after = self.conn.name

        self.assertNotEqual(before, after)
        self.assertRegex(before, r'^:')
        self.assertRegex(after, r'^:')

    @inloop
    @asyncio.coroutine
    def test_queue_call(self):
        yield from self.conn.connect
        test_bus().stop()
        yield from self.conn.disconnect


        F = self.conn.call(
            destination=DBUS,
            interface=DBUS,
            path=DBUS_PATH,
            member='ListNames',
        )

        self.assertFalse(F.done())

        test_bus().start()
        yield from self.conn.connect


        names = yield from F

        self.assertIn(self.conn.name, names)
