import unittest

import logging
_log = logging.getLogger(__name__)

import asyncio, functools

from ..conn import DBUS, DBUS_PATH, RemoteError, Match
from ..auth import connect_bus, get_session_infos
from ..proxy import SimpleProxy
from .util import inloop

class TestDBus(unittest.TestCase):
    'Two peers talking to eachother'
    timeout = 1.0

    servname = 'foo.bar'
    servpath = '/foo/bar'

    @inloop
    @asyncio.coroutine
    def setUp(self):
        self.client = yield from connect_bus(get_session_infos(), loop=self.loop)
        self.server = yield from connect_bus(get_session_infos(), loop=self.loop)
        self.server.ignore_calls = False
        # Server's proxy for the dbus daemon
        self.serverdaemon = SimpleProxy(self.server,
                               name=DBUS,
                               interface=DBUS,
                               path=DBUS_PATH,
        )
        # Client's proxy to the server
        self.serverobj = SimpleProxy(self.client,
                               name=self.servname,
                               interface=self.servname,
                               path=self.servpath,
        )

        ACQ = yield from self.serverdaemon.AddMatch(member='NameAcquired')
        yield from self.serverdaemon.call(member='RequestName',
                                          sig='su',
                                          body=(self.servname, 4), # don't queue
        )
        evt, sts = yield from ACQ.recv()
        self.assertEqual(evt.body, self.servname)

    @inloop
    @asyncio.coroutine
    def tearDown(self):
        yield from asyncio.gather(self.client.close(),
                                  self.server.close())

    @inloop
    @asyncio.coroutine
    def test_badcall(self):
        try:
            yield from self.serverobj.call(member='baz')
            self.fail("Unexpected success")
        except RemoteError as e:
            self.assertEqual(e.name, 'org.freedesktop.DBus.Error.UnknownMethod')

    @inloop
    @asyncio.coroutine
    def test_callecho(self):
        CALL = self.server.AddCall(
            member='Echo',
        )
        @asyncio.coroutine
        def answer():
            _log.info("answer starts")
            while True:
                evt, sts = yield from CALL.recv()
                _log.info("answer recv <- %s %s", evt, sts)
                if sts==Match.DONE:
                    break
                try:
                    CALL.done(evt, 's', evt.body+' world')
                except:
                    _log.exception('answer oops')
                    raise
            _log.info("answer stops")

        T = self.loop.create_task(answer())
        try:
            msg = yield from self.serverobj.call(member='Echo', sig='s', body='hello')
            self.assertEqual(msg, 'hello world')
        finally:
            _log.info("result received")
            yield from CALL.close()
            yield from T

    @inloop
    @asyncio.coroutine
    def test_signal(self):
        SIG = yield from self.client.AddMatch(
            interface=self.servname,
            path=self.servpath,
            member='Testing',
        )

        self.server.signal(
            interface=self.servname,
            path=self.servpath,
            member='Testing',
            sig='s',
            body='one',
        )

        evt, sts = yield from SIG.recv()
        self.assertEqual(evt.body, 'one')

        self.assertTrue(SIG._Q.empty())

        yield from SIG.close()
