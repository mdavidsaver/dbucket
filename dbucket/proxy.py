
import asyncio
import xml.etree.ElementTree as ET

INTROSPECTABLE='org.freedesktop.DBus.Introspectable'


class SimpleProxy(object):
    """Simple proxy object around a connection for a single (destination, path, interface)
    """
    def __init__(self, conn, *, name=None, path=None, interface=None):
        assert isinstance(name, str), "name= must be str"
        assert isinstance(path, str), "path= must be str"
        assert isinstance(interface, str), "interface= must be str"
        self.conn = conn
        self._name, self._path, self._interface = name, path, interface

    def AddMatch(self, **kws):
        args = {
            'sender':self._name,
            'path':self._path,
            'interface':self._interface,
        }
        args.update(kws)
        return self.conn.AddMatch(**args)

    def call(self, **kws):
        args = {
            'destination':self._name,
            'path':self._path,
            'interface':self._interface,
        }
        args.update(kws)
        return self.conn.call(**args)

class ProxyBase(object):
    _dbus_interface = None # set in sub-class (cf. buildProxy)

    def __init__(self, conn, destination=None, path=''):
        self._dbus_connection = conn
        self._dbus_destination = destination
        self._dbus_path = path

    @asyncio.coroutine
    def setup(self):
        for name, doc in self._dbus_signals:
            M = SignalManager(self, name)
            M.__doc__ = doc
            setattr(self, name, M)
        return self

def makeCall(mname, sig, nargs):
    if nargs==0:
        @asyncio.coroutine
        def meth(self):
            return (yield from self._dbus_connection.call(
                destination=self._dbus_destination,
                path=self._dbus_path,
                interface=self._dbus_interface,
                member=mname,
            ))
    else:
        @asyncio.coroutine
        def meth(self, *args):
            assert len(args)==nargs, "signature: "+sig
            return (yield from self._dbus_connection.call(
                destination=self._dbus_destination,
                path=self._dbus_path,
                interface=self._dbus_interface,
                member=mname,
                sig=sig,
                body=args
            ))
    meth._dbus_method = mname
    meth._dbus_sig = sig
    meth._dbus_nargs = nargs
    return meth

class SignalManager(object):
    def __init__(self, proxy, signame):
        self.proxy, self.signame = proxy, signame
    @asyncio.coroutine
    def connect(self):
        return (yield from self.proxy._dbus_connection.AddMatch(
            #sender=self.proxy._dbus_destination, #TODO track well-known names and check this?
            path=self.proxy._dbus_path,
            interface=self.proxy._dbus_interface,
            member=self.signame,
        ))

def buildProxy(xml, *, interface=None):
    node = xml.find("interface[@name='%s']"%interface)

    klass={
        '_dbus_interface':interface,
        #TODO: __doc__
    }

    for mnode in node.findall('method'):
        """<method name="GetConnectionCredentials">
              <arg direction="in" type="s"/>
              <arg direction="out" type="a{sv}"/>
           </method>
        """
        name = mnode.attrib['name']
        sig, ret = [], []
        for argnode in mnode.findall('arg'):
            if argnode.attrib['direction']=='in':
                sig.append(argnode.attrib['type'])
            elif argnode.attrib['direction']=='out':
                ret.append(argnode.attrib['type'])

        meth = makeCall(name, ''.join(sig), len(sig))
        meth.__name__ = name
        meth.__doc__ = '{ret} = {name}({arg})\n========================\n{xml}'.format(
            ret = ', '.join(ret),
            arg = ', '.join(sig),
            name = name,
            xml = ET.tostring(mnode),
        )
        klass[name] = meth

    sigs = []
    for snode in node.findall('signal'):
        """<signal name="NameOwnerChanged">
            <arg type="s"/>
            <arg type="s"/>
            <arg type="s"/>
           </signal>
        """
        name = snode.attrib['name']
        doc = 'signal {name} -> {sig}\n========================\n{xml}'.format(
            name = name,
            sig = ''.join([N.attrib['type'] for N in snode.findall('arg')]),
            xml = ET.tostring(snode),
        )
        sigs.append((name, doc))
    klass['_dbus_signals'] = sigs

    return type(interface.replace('.','_'), (ProxyBase,), klass)

@asyncio.coroutine
def createProxy(conn, *, destination=None, path=None, interface=None):
    raw = yield from conn.call(
        destination=destination,
        path=path,
        interface=INTROSPECTABLE,
        member='Introspect',
    )

    #TODO: cache klass?
    root = ET.fromstring(raw)
    klass = buildProxy(root, interface=interface)

    return (yield from klass(conn, destination=destination, path=path).setup())
