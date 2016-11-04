import unittest

import asyncio, functools, os, sys

from ..conn import DBUS, DBUS_PATH, INTROSPECTABLE, RemoteError
from ..auth import connect_bus, get_session_infos
from ..proxy import SimpleProxy, createProxy
from .util import inloop

class TestDBus(unittest.TestCase):
    'Talking to the dbus daemon'
    timeout = 1.0

    @inloop
    @asyncio.coroutine
    def setUp(self):
        self.conn = yield from connect_bus(get_session_infos(), loop=self.loop)
        # proxy for dbus daemon
        self.obj = SimpleProxy(self.conn,
                               name=DBUS,
                               interface=DBUS,
                               path=DBUS_PATH,
        )
        # another proxy for the dbus daemon (generated)
        self.obj2 = yield from createProxy(self.conn,
                               destination=DBUS,
                               path=DBUS_PATH,
                               interface=DBUS,
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
        names = yield from self.obj2.ListNames()
        # list should include me
        self.assertIn(self.conn.name, names)
        # list should include the daemon
        self.assertIn(DBUS, names)

    @inloop
    @asyncio.coroutine
    def test_RequestName(self):
        """Request a well known name on the bus
        """
        myname = 'foo.bar'

        ACQ =     yield from self.obj2.NameAcquired.connect()
        LOST =    yield from self.obj2.NameLost.connect()
        CHANGED = yield from self.obj2.NameOwnerChanged.connect()

        ret = yield from self.obj2.RequestName(myname, 4) # Don't Queue

        evt, sts = yield from ACQ.recv()
        self.assertEqual(evt.body, myname)

        evt, sts = yield from CHANGED.recv()
        thename, prev, cur = evt.body
        self.assertEqual(thename, myname)
        self.assertEqual(prev, '') # no previous owner
        self.assertEqual(cur, self.conn.name)

        names = yield from self.obj2.ListNames()
        # list should include me
        self.assertIn(self.conn.name, names)
        self.assertIn(myname, names)

        ret = yield from self.obj2.ListNames()
        ret = yield from self.obj2.ReleaseName(myname)
        self.assertEqual(ret, 1) # Released

        evt, sts = yield from LOST.recv()
        self.assertEqual(evt.body, myname)

        evt, sts = yield from CHANGED.recv()
        thename, prev, cur = evt.body
        self.assertEqual(thename, myname)
        self.assertEqual(prev, self.conn.name)
        self.assertEqual(cur, '') # no owner

        names = yield from self.obj2.ListNames()
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

    @inloop
    @asyncio.coroutine
    def test_introspect(self):
        msg = yield from self.conn.call(
                               destination=DBUS,
                               interface=INTROSPECTABLE,
                               path=DBUS_PATH,
                               member='Introspect',
        )

        import xml.etree.ElementTree as ET
        root = ET.fromstring(msg)
        self.assertEqual(root.tag, 'node')

    @inloop
    @asyncio.coroutine
    def test_cred(self):
        # also dests dict decode
        info = yield from self.obj.call(member='GetConnectionCredentials', sig='s', body=self.conn.name)
        if sys.platform in ('linux',):
            self.assertEqual(info['UnixUserID'], os.getuid())
            self.assertEqual(info['ProcessID'], os.getpid())
