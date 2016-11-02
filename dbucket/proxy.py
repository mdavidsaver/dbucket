
import logging
_log = logging.getLogger(__name__)

from collections import defaultdict
import asyncio, functools, inspect
import xml.etree.ElementTree as ET

from .xcode import sigsplit

INTROSPECTABLE='org.freedesktop.DBus.Introspectable'

IDOCTYPE = '''<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">\n'''

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

def _infer_sig(*args):
    """Guess dbus type signature based on python classes provided
    """
    sig = []
    for T in args:
        if isinstance(T, str):
            sig.append(T)
        elif T is int:
            sig.append('i')
        elif T is str:
            sig.append('s')
        elif isinstance(T, tuple): # sub-struct
            sig.append('(%s)'%_infer_sig(*T))
        elif isinstance(T, list):
            if len(T)!=1:
                raise TypeError("Type array sig must be length 1: %s"%T)
            sig.append('a'+_infer_sig(T[0]))
        else:
            raise TypeError("Can't infer dbus type for %s"%T)
    return ''.join(sig)

def Method(*, name=None, interface=None):
    """Apply this decorator to export as a dbus method

    @Method()
    def meth():
        pass

    Exports a method with no arguments or return value

    @Method()
    def meth(a:int, b:int) -> (int, int):
        return a,b

    @Method()
    def meth(a:'i', b:'i') -> ('i', 'i'):
        return a,b

    All result in a signature "ii = meth(ii)"
    """
    def decorate(meth):

        SIG = inspect.signature(meth)

        ret = SIG.return_annotation
        if ret is inspect.Parameter.empty:
            ret = []
        elif not isinstance(ret, tuple):
            ret = [ret]

        args = []
        for A in SIG.parameters.values():
            if A.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                if A.annotation is inspect.Parameter.empty:
                    raise TypeError("Method() requires annotation of '%s'"%A.name)
                elif isinstance(A.annotation, str) and len(list(sigsplit(A.annotation.encode('ascii'))))!=1:
                    raise TypeError("Method() '%s' annotation implied implies a struct"%A.name)
                args.append(A.annotation)
            else:
                raise TypeError("Method() allows only positional parameters.  not '%s'"%A.name)

        ret = meth._dbus_return = _infer_sig(*ret)
        sig = meth._dbus_sig = _infer_sig(*args)

        meth._dbus_method = name or meth.__name__
        meth._dbus_interface = interface

        node = ET.Element('method', name=meth._dbus_method)
        
        for S in sigsplit(sig.encode('ascii')):
            ET.SubElement(node, 'arg', direction='in', type=S.decode('ascii'))
        for S in sigsplit(ret.encode('ascii')):
            ET.SubElement(node, 'arg', direction='out', type=S.decode('ascii'))
        meth._dbus_xml = ET.tostring(node)

        meth.__doc__ = meth.__doc__ or 'No doc'
        meth.__doc__+='\n\ndbus export method: {ret} = {iface}.{name}({arg})\n========================\n{xml}'.format(
            ret = ret,
            arg = sig,
            iface = meth._dbus_interface or '<deferred>',
            name = meth._dbus_method,
            xml = meth._dbus_xml,
        )
        return meth
    return decorate

def Signal(*args, name=None, interface=None):
    def decorate(meth):
        SIG = inspect.signature(meth)

        if SIG.return_annotation is not inspect.Parameter.empty:
            raise TypeError("Signal() methods may not return")

        args = []
        for A in SIG.parameters.values():
            if A.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                if A.annotation is inspect.Parameter.empty:
                    raise TypeError("Signal() requires annotation of '%s'"%A.name)
                elif isinstance(A.annotation, str) and len(list(sigsplit(A.annotation.encode('ascii'))))!=1:
                    raise TypeError("Signal() '%s' annotation implied implies a struct"%A.name)
                args.append(A.annotation)
            else:
                raise TypeError("Signal() allows only positional parameters.  not '%s'"%A.name)

        sig = _infer_sig(*args)

        sname = name or meth.__name__

        if len(args)==0:
            def sendsig(self):
                self._dbus_connection.signal(
                    #destination=
                    interface=interface or self._dbus_interface,
                    path=self._dbus_path,
                    member=sname,
                )
        else:
            def sendsig(self, *args):
                self._dbus_connection.signal(
                    #destination=
                    interface=interface or self._dbus_interface,
                    path=self._dbus_path,
                    member=sname,
                    sig=sig,
                    body=args,
                )

        sendsig._dbus_signal = sname
        sendsig._dbus_interface = interface
        sendsig._dbus_sig = sig

        node = ET.Element('signal', name=sname)

        for S in sigsplit(sig.encode('ascii')):
            ET.SubElement(node, 'arg', direction='out', type=S.decode('ascii'))

        sendsig._dbus_xml = ET.tostring(node)

        sendsig.__doc__ = meth.__doc__ or 'No doc'
        sendsig.__doc__+='\n\ndbus emit signal: {iface}.{name}({arg})\n========================\n{xml}'.format(
            arg = sig,
            iface = interface or '<deferred>',
            name = sname,
            xml = sendsig._dbus_xml,
        )
        return sendsig
    return decorate

def Introspect(self):
    """s = Introspect()
    """
    return self._dbus_xml

Introspect._dbus_method = 'Introspect'
Introspect._dbus_interface = INTROSPECTABLE
Introspect._dbus_sig = ''
Introspect._dbus_return = 's'

@asyncio.coroutine
def exportObject(conn, obj, *, path='/', interface=None):
    if hasattr(obj, '_dbus_connection'):
        raise RuntimeError("Already exported") # also prevent client proxy from being exported
    if hasattr(obj, 'detach'):
        raise RuntimeError("detach would be replaced be generated detech() method")

    root = ET.Element('node')
    intero = ET.SubElement('interface', name=INTROSPECTABLE)
    intero = ET.SubElement('method', name='Introspect')
    ET.SubElement(intero, 'arg', dir='out', type='s')

    methods = {
        (INTROSPECTABLE, 'Introspect'):functools.partial(Introspect, obj),
    }
    for K,V in inspect.getmembers(obj):
        if interface is None and hasattr(V, '_dbus_interface') and V._dbus_interface is None:
            raise ValueError("Default interface name required as '%s' specifies no interface"%K)

        if not hasattr(V, '_dbus_xml'):
            continue

        inode = root.find("interface[@name='%s']"%iface) or ET.SubElement(root, 'interface', name=iface)

        inode.append(ET.fromstring(V._dbus_xml))

        if not hasattr(V, '_dbus_method'):
            continue

        mname = V._dbus_method
        iface = V._dbus_interface or interface
        assert iface is not None, (K,V)

        methods[(iface, mname)] = V


    obj._dbus_xml = IDOCTYPE+ET.tostring(root)
    obj._dbus_connection = conn

