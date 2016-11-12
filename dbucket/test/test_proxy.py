
import unittest, asyncio
import xml.etree.ElementTree as ET

from ..conn import DBUS, DBUS_PATH, METHOD_CALL, BusEvent
from ..proxy import buildProxy, ProxyBase, Method, Signal, MethodDispatch
from .util import inloop, FakeConnection

class TestBuilder(unittest.TestCase):
    xml = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.freedesktop.DBus.Other">
    <method name="Hello">
    </method>
  </interface>
  <interface name="org.freedesktop.DBus">
    <method name="Hello">
      <arg direction="out" type="s"/>
    </method>
    <method name="ListQueuedOwners">
      <arg direction="in" type="s"/>
      <arg direction="out" type="as"/>
    </method>
  </interface>
</node>
"""
    
    def setUp(self):
        self.loop = asyncio.get_event_loop()
        self.conn = FakeConnection()
        self.root = ET.fromstring(self.xml)

    @inloop
    @asyncio.coroutine
    def test_call0(self):
        klass = buildProxy(self.root, interface=DBUS)

        self.assertTrue(hasattr(klass, 'Hello'))
        self.assertEqual(klass.Hello._dbus_sig, '')
        self.assertRegex(klass.Hello.__doc__, r's = Hello\(\)')

        self.assertTrue(hasattr(klass, 'ListQueuedOwners'))
        self.assertEqual(klass.ListQueuedOwners._dbus_sig, 's')
        self.assertRegex(klass.ListQueuedOwners.__doc__, r'as = ListQueuedOwners\(s\)')

        inst = klass(self.conn, destination=DBUS, path=DBUS_PATH)

        self.conn.prep_call(':1.1',
                            interface=DBUS,
                            path=DBUS_PATH,
                            destination=DBUS,
                            member='Hello',
        )
        self.conn.prep_call([],
                            interface=DBUS,
                            path=DBUS_PATH,
                            destination=DBUS,
                            member='ListQueuedOwners',
        )

        ret = yield from inst.Hello()
        self.assertEqual(ret, ':1.1')

        ret = yield from inst.ListQueuedOwners('test')
        self.assertEqual(ret, [])


class TestExport(unittest.TestCase):

    def test_Method(self):
        class Test(object):
            @Method()
            def Empty(self):
                pass

            @Method(name="Alt")
            def Input(self, a:int, b:(int, str), c:[str]) -> [(int, str)]:
                pass

            @Method(interface="org.other")
            def Manual(self, a:'i', b:'(is)', c:'as') -> 'a(is)':
                pass

        self.assertEqual(Test.Empty._dbus_method, 'Empty')
        self.assertIs(Test.Empty._dbus_interface, None)
        self.assertEqual(Test.Empty._dbus_sig, '')
        self.assertEqual(Test.Empty._dbus_return, '')

        self.assertEqual(Test.Input._dbus_method, 'Alt')
        self.assertIs(Test.Input._dbus_interface, None)
        self.assertEqual(Test.Input._dbus_sig, 'i(is)as')
        self.assertEqual(Test.Input._dbus_return, 'a(is)')

        self.assertEqual(Test.Manual._dbus_method,'Manual')
        self.assertEqual(Test.Manual._dbus_interface, 'org.other')
        self.assertEqual(Test.Manual._dbus_sig, 'i(is)as')
        self.assertEqual(Test.Manual._dbus_return, 'a(is)')

    def test_Signal(self):
        class Test(object):
            @Signal()
            def Sig1():
                pass

            @Signal(name="Alt")
            def Sig2(a:int):
                pass

            @Signal(interface="org.other")
            def Sig3(a:'i', b:int):
                pass

        self.assertEqual(Test.Sig1._dbus_signal, 'Sig1')
        self.assertEqual(Test.Sig2._dbus_signal, 'Alt')
        self.assertEqual(Test.Sig3._dbus_signal, 'Sig3')

        self.assertIs(Test.Sig1._dbus_interface, None)
        self.assertIs(Test.Sig2._dbus_interface, None)
        self.assertEqual(Test.Sig3._dbus_interface, 'org.other')

        self.assertEqual(Test.Sig1._dbus_sig, '')
        self.assertEqual(Test.Sig2._dbus_sig, 'i')
        self.assertEqual(Test.Sig3._dbus_sig, 'ii')

class TestDispatch(unittest.TestCase):
    class Test(object):
        @Method(interface='foo.Op')
        def Zero(self) -> int:
            return 0
        @Method(interface='foo.Op')
        def Inv(self, a:int) -> int:
            return -a
        @Method(interface='foo.Op')
        def Add(self, a:int, b:int) -> int:
            return a+b

    def setUp(self):
        self.conn = FakeConnection()
        self.disp = MethodDispatch(self.conn)
        self.inst = self.Test()
        self.disp.attach(self.inst)

    def test_Zero(self):

        val, sig = self.disp.handle(BusEvent.build(METHOD_CALL, 1,
            path='/',
            interface='foo.Op',
            member='Zero',
        ))

        self.assertEqual(sig, 'i')
        self.assertEqual(val, 0)

    def test_Inv(self):

        val, sig = self.disp.handle(BusEvent.build(METHOD_CALL, 1,
            path='/',
            interface='foo.Op',
            member='Inv',
            sig='i',
            body=42,
        ))

        self.assertEqual(sig, 'i')
        self.assertEqual(val, -42)

    def test_Add(self):

        val, sig = self.disp.handle(BusEvent.build(METHOD_CALL, 1,
            path='/',
            interface='foo.Op',
            member='Add',
            sig='ii',
            body=(1,3),
        ))

        self.assertEqual(sig, 'i')
        self.assertEqual(val, 4)
