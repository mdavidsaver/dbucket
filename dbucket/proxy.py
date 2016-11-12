
import logging
_log = logging.getLogger(__name__)

from collections import defaultdict
import asyncio, functools, inspect
import xml.etree.ElementTree as ET

from .conn import Variant, RemoteError, UnknownMethod
from .xcode import sigsplit

INTROSPECTABLE='org.freedesktop.DBus.Introspectable'
PROPERTIES = 'org.freedesktop.DBus.Properties'

IDOCTYPE = '''<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">\n'''

UNKNOWNMETHOD = "org.freedesktop.DBus.Error.UnknownMethod"
UNKNOWNOBJECT = "org.freedesktop.DBus.Error.UnknownObject"

class SimpleProxy(object):
    """Simple proxy object around a connection for a single (destination, path, interface)
    """
    def __init__(self, conn, *, name=None, path=None, interface=None):
        assert isinstance(name, str), "name= must be str"
        assert isinstance(path, str), "path= must be str"
        assert isinstance(interface, str), "interface= must be str"
        self.conn = conn
        self._name, self._path, self._interface = name, path, interface

    @asyncio.coroutine
    def AddMatch(self, **kws):
        Q = self.conn.new_queue()
        args = {
            'sender':self._name,
            'path':self._path,
            'interface':self._interface,
        }
        args.update(kws)
        yield from Q.add(**args)
        return Q

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
    def connect(self, Q=None):
        Q = Q or self.proxy._dbus_connection.new_queue()
        yield from Q.add(
            #sender=self.proxy._dbus_destination, #TODO track well-known names and check this?
            path=self.proxy._dbus_path,
            interface=self.proxy._dbus_interface,
            member=self.signame,
        )
        return Q

class PropertyAccessor(object):
    def __init__(self, sig, iface, name):
        self._sig, self._iface, self._name = sig, iface, name

    @asyncio.coroutine
    def __get__(self, inst, klass):
        return (yield from inst._dbus_connection.call(
            destination = inst._dbus_destination,
            path = inst._dbus_path,
            interface = PROPERTIES,
            member = 'Get',
            sig = 'ss',
            body = (self._iface or inst._dbus_interface, self._name),
        ))

    @asyncio.coroutine
    def __set__(self, inst, value):
        return (yield from inst._dbus_connection.call(
            destination = inst._dbus_destination,
            path = inst._dbus_path,
            interface = PROPERTIES,
            member = 'Get',
            sig = 'ssv',
            body = (self._iface or inst._dbus_interface, self._name, Variant(self._sig, value)),
        ))

def buildProxy(xml, *, interface=None):
    node = xml.find("interface[@name='%s']"%interface)
    if node is None:
        raise RuntimeError("No interface %s"%interface)

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

    for pnode in node.findall('property'):
        '<property name="Bar" type="y" access="readwrite"/>'
        name, sig = pnode.attrib['name'], pnode.attrib['type']
        klass[name] = PropertyAccessor(sig, interface, name)

    return type(interface.replace('.','_'), (ProxyBase,), klass)

@asyncio.coroutine
def createProxy(conn, *, destination=None, path=None, interface=None):
    raw = yield from conn.call(
        destination=destination,
        path=path,
        interface=INTROSPECTABLE,
        member='Introspect',
    )

    try:
        #TODO: cache klass?
        root = ET.fromstring(raw)
        klass = buildProxy(root, interface=interface)

        return (yield from klass(conn, destination=destination, path=path).setup())
    except Exception as e:
        raise RuntimeError("%s while building proxy for %s %s %s"%(e, destination, path, interface))

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

def Interface(name):
    def decorate(klass):
        klass._dbus_interface = name
        return klass
    return decorate

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
        for i, A in enumerate(SIG.parameters.values()):
            if i==0 and A.name=='self':
                continue
            elif A.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                if A.annotation is inspect.Parameter.empty:
                    raise TypeError("Method() requires annotation of '%s'"%A.name)
                elif isinstance(A.annotation, str) and len(list(sigsplit(A.annotation.encode('ascii'))))!=1:
                    raise TypeError("Method() '%s' annotation implied implies a struct"%A.name)
                args.append(A.annotation)
            else:
                raise TypeError("Method() allows only positional parameters.  not '%s'"%A.name)

        ret = meth._dbus_return = _infer_sig(*ret)
        sig = meth._dbus_sig = _infer_sig(*args)
        meth._dbus_nsig = len(args)

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
        for i,A in enumerate(SIG.parameters.values()):
            if i==0 and A.name=='self':
                continue
            elif A.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
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

class ExportNode(dict):
    def __init__(self, name, fullpath, *, parent=None):
        self.name, self.fullpath, self.obj, self.parent = name, fullpath, None, parent
        if parent is not None:
            parent[name] = self

        self.xml = None
        self.node_xml = IDOCTYPE+'<node></node>'

    @Method()
    def Introspect(self) -> str:
        if self.xml is None:
            if len(self)==0:
                self.xml = self.node_xml
            else:
                node = ET.fromstring(self.node_xml)
                for C in self.values():
                    ET.SubElement(node, 'node', name=C.name)
                self.xml = IDOCTYPE+ET.tostring(node).decode('ascii')

        return self.xml

    def attach(self, obj):
        self.xml = None
        if hasattr(obj, 'detach'):
            raise RuntimeError("detach would be replaced be generated detech() method")
        elif self.obj is not None:
            raise RuntimeError("Path %s is already attached by %s"%(eslf.fullpath, self.obj))

        root = ET.Element('node')
        intero = ET.SubElement(root, 'interface', name=INTROSPECTABLE)
        intero = ET.SubElement(intero, 'method', name='Introspect')
        ET.SubElement(intero, 'arg', dir='out', type='s')

        methods = {
            (INTROSPECTABLE, 'Introspect'):self.Introspect,
        }
        # our test code assumes stable iteration order of members
        for K,V in inspect.getmembers(obj):
            if not hasattr(V, '_dbus_xml'):
                continue

            try:
                iface = V._dbus_interface or obj._dbus_interface
            except AttributeError:
                raise ValueError("Default interface name required as '%s' specifies no interface.  Add @Interface('...') to %s definition"%(K, obj.__class__.__name__))

            inode = root.find("interface[@name='%s']"%iface) or ET.SubElement(root, 'interface', name=iface)

            inode.append(ET.fromstring(V._dbus_xml))

            if not hasattr(V, '_dbus_method'):
                continue

            mname = V._dbus_method
            assert iface is not None, (K,V)

            methods[(iface, mname)] = V

        self.node_xml = IDOCTYPE+ET.tostring(root).decode('ascii')

        self.methods = methods
        self.obj = obj

    def detach(self):
        if self.obj is not None:
            del self.obj._dbus_connection
            del self.obj._dbus_path
        self.obj = self.methods = None
        #if len(self)==0 and self.parent is not None:
        #    del self.parent[self]

        self.xml = None
        self.node_xml = '<node></node>'

class MethodDispatch(object):
    Node = ExportNode

    def __init__(self, conn):
        self.conn = conn

        self._dispatch = {} # {('/full/path':obj}

        self.root = self.Node('/', '/')

    def _get_node(self, path):
        assert path[0]=='/', path
        parts = path[1:].split('/')
        if parts==['']:
            parts = []
        node = self.root
        for P in parts:
            try:
                node = node[P]
            except KeyError:
                node = node[P] = self.Node(P, path, parent=node)
        return node

    def attach(self, obj, *, path='/'):
        assert path[0]=='/', path
        node = self._get_node(path)
        if node.obj is not None:
            raise RuntimeError("Path %s is already attached by %s"%(path, node.obj))

        node.attach(obj)
        self._dispatch[path] = node
        obj._dbus_connection = self.conn
        obj._dbus_path = path

    def detach(self, path):
        self._get_node(path).detach()

    def handle(self, evt):
        """Call method and return (value, 'sig')
        """
        node = self._dispatch.get(evt.path)
        if node is None:
            raise RemoteError('No path', name=UNKNOWNOBJECT)

        try:
            M = node.methods[(evt.interface, evt.member)]
        except KeyError:
            raise RemoteError("Unknown method %s.%s"%(evt.interface, evt.member), name=UnknownMethod)

        if M._dbus_nsig==0:
            return M(), M._dbus_return
        elif M._dbus_nsig==1:
            return M(evt.body), M._dbus_return
        else:
            return M(*evt.body), M._dbus_return
