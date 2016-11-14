import unittest

import logging
_log = logging.getLogger(__name__)

import asyncio, functools

from ..conn import DBUS, DBUS_PATH, RemoteError
from ..signal import SignalQueue
from ..auth import connect_bus
from ..proxy import Interface, Method, Signal
from .util import inloop, test_bus_info

class TestPeer(unittest.TestCase):
    'Two peers talking to eachother'
    timeout = 1.0

    servname = 'foo.bar'
    servpath = '/foo/bar'

    @Interface(servname)
    class Foo(object):
        @Method()
        def Echo(self, s:str) -> str:
            return s+' world'
        @Method()
        @asyncio.coroutine
        def DelayEcho(self, s:str) -> str:
            return s+' is a test'
        @Signal()
        def Testing(self, s:str):
            pass

    @inloop
    @asyncio.coroutine
    def setUp(self):
        self.client = yield from connect_bus(test_bus_info(), loop=self.loop)
        self.server = yield from connect_bus(test_bus_info(), loop=self.loop)
        self.server.debug_net = True
        try:
            self.serverobj = self.Foo()
            self.server.attach(self.serverobj, path=self.servpath)

            ret = yield from self.server.daemon.RequestName(self.servname, 4) # don't queue
            self.assertEqual(ret, 1) # now primary owner

            self.obj = yield from self.client.proxy(
                destination=self.servname,
                interface=self.servname,
                path=self.servpath,
            )
        except:
            yield from asyncio.gather(self.client.close(),
                                    self.server.close())
            raise

    @inloop
    @asyncio.coroutine
    def tearDown(self):
        self.server.detach(self.servpath)
        yield from asyncio.gather(self.client.close(),
                                  self.server.close(),
                                  loop=self.loop)

    @inloop
    @asyncio.coroutine
    def test_badcall(self):
        try:
            yield from self.client.call(
                destination=self.servname,
                interface=self.servname,
                path=self.servpath,
                member='baz'
            )
            self.fail("Unexpected success")
        except RemoteError as e:
            self.assertEqual(e.name, 'org.freedesktop.DBus.Error.UnknownMethod')

    @inloop
    @asyncio.coroutine
    def test_callecho(self):
        msg = yield from self.obj.Echo('hello')
        self.assertEqual(msg, 'hello world')

    @inloop
    @asyncio.coroutine
    def test_callechodelay(self):
        msg = yield from self.obj.DelayEcho('hello')
        self.assertEqual(msg, 'hello is a test')

    @inloop
    @asyncio.coroutine
    def test_signal(self):
        SIG = self.client.new_queue()
        yield from self.obj.Testing.connect(SIG)

        self.serverobj.Testing('one')

        evt, sts = yield from SIG.recv()
        self.assertEqual(evt.body, 'one')

        self.assertTrue(SIG._Q.empty())

        yield from SIG.close()
