import unittest

import asyncio, functools

from ..conn import DBUS, DBUS_PATH, RemoteError
from ..auth import connect_bus, get_session_infos
from ..proxy import SimpleProxy
from .util import inloop

class TestDBus(unittest.TestCase):
    'Talking to the dbus daemon'
    timeout = 1.0

    def setUp(self):
        self.loop = asyncio.get_event_loop()
        self.loop.set_debug(True)
        self.conn = self.loop.run_until_complete(connect_bus(get_session_infos(), loop=self.loop))
        self.obj = SimpleProxy(self.conn,
                               name=DBUS,
                               interface=DBUS,
                               path=DBUS_PATH,
        )

    @inloop
    @asyncio.coroutine
    def tearDown(self):
        yield from self.conn.close()

    def test_Hello(self):
        self.assertIsNotNone(self.conn.name)
        self.assertEqual(self.conn.name[0], ':')

    @inloop
    @asyncio.coroutine
    def test_ListNames(self):
        names = yield from self.obj.call(member='ListNames')
        # list should include me
        self.assertIn(self.conn.name, names)

    @inloop
    @asyncio.coroutine
    def test_RequestName(self):
        """Request a well known name on the bus
        """
        myname = 'foo.bar'

        ACQ = yield from self.obj.AddMatch(
            member='NameAcquired',
        )
        LOST = yield from self.obj.AddMatch(
            member='NameLost',
        )
        CHANGED = yield from self.obj.AddMatch(
            member='NameOwnerChanged',
        )

        ret = yield from self.obj.call(
            member='RequestName',
            sig='su',
            body=(myname, 4), # Don't Queue
        )

        evt, sts = yield from ACQ.recv()
        self.assertEqual(evt.body, myname)

        evt, sts = yield from CHANGED.recv()
        thename, prev, cur = evt.body
        self.assertEqual(thename, myname)
        self.assertEqual(prev, '') # no previous owner
        self.assertEqual(cur, self.conn.name)

        names = yield from self.obj.call(
            member='ListNames',
        )
        # list should include me
        self.assertIn(self.conn.name, names)
        self.assertIn(myname, names)

        ret = yield from self.obj.call(
            member='ReleaseName',
            sig='s',
            body=myname,
        )
        self.assertEqual(ret, 1) # Released

        evt, sts = yield from LOST.recv()
        self.assertEqual(evt.body, myname)

        evt, sts = yield from CHANGED.recv()
        thename, prev, cur = evt.body
        self.assertEqual(thename, myname)
        self.assertEqual(prev, self.conn.name)
        self.assertEqual(cur, '') # no owner

        names = yield from self.obj.call(
            member='ListNames',
        )
        # list should include me
        self.assertIn(self.conn.name, names)
        self.assertNotIn(myname, names)

        self.assertTrue(ACQ._Q.empty())
        self.assertTrue(LOST._Q.empty())
        self.assertTrue(CHANGED._Q.empty())

        yield from ACQ.close()
        yield from LOST.close()
        yield from CHANGED.close()

    @inloop
    @asyncio.coroutine
    def test_badmethod(self):
        try:
            yield from self.obj.call(member='InvalidMethodName')
            self.fail('Unexpected success')
        except RemoteError as e:
            self.assertRegex(str(e), 'InvalidMethodName')
            self.assertEqual(e.name, 'org.freedesktop.DBus.Error.UnknownMethod')
        except:
            import trackback
            trackback.print_exc()
            self.fail("Unexpected exception type")
