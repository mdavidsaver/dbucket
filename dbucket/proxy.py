
import asyncio
import xml.etree.ElementTree as ET

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
    meth._dbus_sig = sig
    meth._dbus_nargs = nargs
    return meth

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
        meth.__doc__ = '{ret} = {name}({arg})\n\n{xml}'.format(
            ret = ', '.join(ret),
            arg = ', '.join(sig),
            name = name,
            xml = ET.tostring(mnode),
        )
        klass[name] = meth

    return type(interface.replace('.','_'), (ProxyBase,), klass)
