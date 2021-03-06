import unittest, logging
_log = logging.getLogger(__name__)

import asyncio, functools, os, sys

from ..conn import DBUS, DBUS_PATH, INTROSPECTABLE, RemoteError
from ..auth import connect_bus
from ..proxy import SimpleProxy, createProxy
from .util import inloop, test_bus, test_bus_info

class TestDBus(unittest.TestCase):
    'Talking to the dbus daemon'
    timeout = 1.0

    @inloop
    @asyncio.coroutine
    def setUp(self):
        self.conn = yield from connect_bus(test_bus_info(), loop=self.loop)
        # proxy for dbus daemon
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
        names = yield from self.conn.daemon.ListNames()
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

        ACQ =     yield from self.conn.daemon.NameAcquired.connect()
        LOST =    yield from self.conn.daemon.NameLost.connect()
        CHANGED = yield from self.conn.daemon.NameOwnerChanged.connect()

        ret = yield from self.conn.daemon.RequestName(myname, 4) # Don't Queue

        evt, sts = yield from ACQ.recv()
        self.assertEqual(evt.body, myname)

        evt, sts = yield from CHANGED.recv()
        thename, prev, cur = evt.body
        self.assertEqual(thename, myname)
        self.assertEqual(prev, '') # no previous owner
        self.assertEqual(cur, self.conn.name)

        names = yield from self.conn.daemon.ListNames()
        # list should include me
        self.assertIn(self.conn.name, names)
        self.assertIn(myname, names)

        ret = yield from self.conn.daemon.ListNames()
        ret = yield from self.conn.daemon.ReleaseName(myname)
        self.assertEqual(ret, 1) # Released

        evt, sts = yield from LOST.recv()
        self.assertEqual(evt.body, myname)

        evt, sts = yield from CHANGED.recv()
        thename, prev, cur = evt.body
        self.assertEqual(thename, myname)
        self.assertEqual(prev, self.conn.name)
        self.assertEqual(cur, '') # no owner

        names = yield from self.conn.daemon.ListNames()
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
        if hasattr(self.conn.daemon, 'GetConnectionCredentials'):
            # GetConnectionCredentials added in dbus 1.7
            # also tests dict decode
            info = yield from self.conn.daemon.GetConnectionCredentials(self.conn.name)
        elif sys.platform in ('linux',):
            info = {}
            info['UnixUserID'] = (yield from self.conn.daemon.GetConnectionUnixUser(self.conn.name))
            info['ProcessID'] = (yield from self.conn.daemon.GetConnectionUnixProcessID(self.conn.name))

        if sys.platform in ('linux',):
            self.assertEqual(info['UnixUserID'], os.getuid())
            self.assertEqual(info['ProcessID'], os.getpid())

    @inloop
    @asyncio.coroutine
    def test_isolation(self):
        """Try to detect if we are connected to the real session daemon,
        which would violate our testing isolation.
        
        The only well-known name should be the daemon
        """

        names = yield from self.conn.daemon.ListNames()

        for N in names:
            unique = N[0]==':'
            dbus = N==DBUS

            self.assertTrue(unique or dbus, N)

    @inloop
    @asyncio.coroutine
    def test_wait_for_disconnect(self):

        test_bus().stop()
        try:
            yield from self.conn._lost
            self.assertFalse(self.conn.running)
        finally:
            test_bus().start()

    @inloop
    @asyncio.coroutine
    def test_call_after_restart(self):
        initial = yield from self.conn.daemon.GetId()
        self.assertNotEqual(initial, '')

        _log.debug("Force close")
        yield from test_bus().restart()

        try:
            after = yield from self.conn.daemon.GetId()
        except RemoteError as e:
            _log.debug("XX %s", e.name)
            self.assertRegex(e.name, 'NoReply')
