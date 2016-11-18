
import logging
#_log = logging.getLogger(__name__)

import sys, struct, re
from functools import partial
import asyncio

ensure_future = getattr(asyncio, 'ensure_future', asyncio.async)

from .xcode import encode, decode, Object, Signature, Variant
from .valid import is_interface
from .signal import SignalQueue, Condition

#: Bus name and Interface name for DBUS daemon
DBUS='org.freedesktop.DBus'
#: Path for DBUS daemon
DBUS_PATH='/org/freedesktop/DBus'

#: Interface name for Introspect method
INTROSPECTABLE='org.freedesktop.DBus.Introspectable'

# Common error names
# see dbus/dbus-protocol.h
UnknownMethod = 'org.freedesktop.DBus.Error.UnknownMethod'
LimitsExceed = 'org.freedesktop.DBus.Error.LimitsExceeded'
NoReply = 'org.freedesktop.DBus.Error.NoReply'

METHOD_CALL = 1
METHOD_RETURN = 2
ERROR = 3
SIGNAL = 4

_sys_lsb = sys.byteorder=='little'
_sys_L   = b'l' if _sys_lsb else b'B'

class ConnectionClosed(asyncio.CancelledError):
    """Thrown when underlying Connection has become dis-connected
    """
    def __init__(self):
        asyncio.CancelledError.__init__(self, 'Connection closed')

class RemoteError(RuntimeError):
    'Thrown when a DBus Error is received'
    def __init__(self, msg, *, name='dbucket.UncatagorizedError'):
        RuntimeError.__init__(self, msg)
        self.name = name

class NoReplyError(RemoteError):
    def __init__(self):
        RemoteError.__init__(self, "Bus Connection closed/lost", name=NoReply)

def _loop_sync(loop):
    '''Synchronize loop callback queue.
    Returns after all presently pending callbacks have run
    '''
    F=asyncio.Future(loop=loop)
    loop.call_soon(partial(F.set_result, None))
    return F

class BusEvent(object):
    """Representation of a METHOD_CALL or SIGNAL message
    """
    #: Message type.  METHOD_CALL or SIGNAL
    type=None
    #: Bus Path string
    path=None
    #: Interface name.  May be None for METHOD_CALL
    interface=None
    #: Destination.  May be None
    destination=None
    #: Member name (aka method name)
    member=None
    #: Originator of the message.  Will be a unique name or DBUS
    sender=None
    #: Body signature.  Used only if body is not None
    sig=None
    #: Body value
    body=None
    _dattrs = ('sender', 'interface', 'member', 'path', 'destination', 'type', '_error', '_return_sn', 'sig')
    def __init__(self, mtype, sn, headers, body=None):
        self.type, self.serial, self.body = mtype, sn, body
        for code, val in headers:
            if code==1:
                self.path = val
            elif code==2:
                self.interface = val
            elif code==3:
                self.member = val
            elif code==4:
                self._error = val
            elif code==5:
                self._return_sn = val
            elif code==6:
                self.destination = val
            elif code==7:
                self.sender = val
            elif code==8:
                self.sig = val

    @classmethod
    def build(klass, mtype, sn, **kws):
        evt = klass(mtype, sn, [], body=kws.pop('body', None))
        for N in klass._dattrs:
            if N in kws:
                setattr(evt, N, kws.pop(N))
        assert len(kws)==0, kws
        return evt

    def __repr__(self):
        S = ','.join(["%s='%s'"%(K,getattr(self, K, None)) for K in self._dattrs+('body',)])
        return "%s(%s)"%(self.__class__.__name__, S)

class Connection(object):
    #: whether to log message byte strings (very verbose)
    debug_net = False

    def __init__(self, W, R, info, loop=None, name=None):
        self.log = logging.getLogger(__name__) # replaced in setup
        self._W, self._R, self._info, self._loop = W, R, info, loop or asyncio.get_event_loop()
        self._running = True
        self._closed = None
        self._lost = asyncio.Future(loop=loop)

        self._inprog  = {} # in progress method calls we made.  {sn:Future()}
        self._signals = [] # registered signal matches we might receive.  [SignalQueue()]
        
        from .proxy import MethodDispatch
        self._methods = MethodDispatch(self)
        # delegate some of our methods to dispatcher
        self.attach = self._methods.attach
        self.detach = self._methods.detach

        # keep track of match expressions registered with the daemon
        self._match_lock = asyncio.Lock(loop=loop)
        self._matches = {} # {'match=expr':[Interested]}

        self._nextsn = 1 #TODO: randomize?
        self._RX = self._loop.create_task(self._recv())

        # my primary bus name, and the set of well known names I have acquired
        self._name, self._names = None, set()

        # special-ness here since we don't have to call AddMatch to get daemon messages
        self._bus_signals = self.new_queue(qsize=20)
        C = Condition(remove=False, sender=DBUS, path=DBUS_PATH, interface=DBUS)
        self._bus_signals._cond.append(C)

        self._signals.append(self._bus_signals)

        self._SIGS = self._loop.create_task(self._bus_sig())

    def close(self):
        """close out connection.

        Immediately starts shutdown process and
        returns a Future which completes after the connection is closed,
        and all resulting notifications are delivered.
        """
        self.log.debug("Closing")
        if self._closed is None:

            # non-blocking parts of shutdown

            if self._running:
                self._W.close()
            self._running = False

            self._RX.cancel()

            self._cancel_pending()

            # start blocking parts of shudown
            self._closed = ensure_future(self._close(), loop=self._loop)

            if not self._lost.done():
                self._lost.set_result(None)

        return self._closed

    def _cancel_pending(self):
        # fail pending method calls
        for act in self._inprog.values():
            if not act.done():
                act.set_exception(NoReply())

        self._inprog.clear()

    @asyncio.coroutine
    def _close(self):

        # join receiver task, unless we are called from it
        if asyncio.Task.current_task() is not self._RX:
            yield from self._RX

        self._W.close()

        self._W, self._R = None, None

        # notify signal listeners
        F = list([M.close() for M in self._signals])

        # wait for notification to be delivered
        yield from asyncio.gather(*F, loop=self._loop, return_exceptions=True)

        # join daemon signal task
        yield from self._SIGS

        # paranoia, wait for all currently pending callbacks to be run
        # intended to help with a clean shutdown when used
        # like 'loop.run_until_complete(conn.close())'
        yield from _loop_sync(self._loop)
        self.log.debug("Closed")

    @property
    def name(self):
        'My primary bus name'
        return self._name

    @property
    def names(self):
        'All my bus names'
        return self._names

    @property
    def running(self):
        'Connected?'
        return self._running

    @property
    def loop(self):
        return self._loop

    @asyncio.coroutine
    def AddMatch(self, obj, expr):
        '''Register match expression with dbus daemon and associate it with *obj*.
        
        A match expression will not be removed until every associated *obj*
        is passed to RemoveMatch().
        '''
        if not self._running:
            raise ConnectionClosed()
        with (yield from self._match_lock):            
            try:
                self._matches[expr].add(obj)
            except KeyError:
                yield from self.daemon.AddMatch(expr)
                I = self._matches[expr] = set([obj])

    @asyncio.coroutine
    def RemoveMatch(self, obj, expr):
        '''Remove match expression association
        '''
        if not self._running:
            return
        with (yield from self._match_lock):            
            try:
                I = self._matches[expr]
                I.remove(obj)
            except (KeyError, ValueError):
                raise RuntimeError("Object not registered with match %s %s"%(expr, obj))
            else:
                if len(I)==0:
                    del self._matches[expr]
                    yield from self.daemon.RemoveMatch(expr)

    def new_queue(self, **kws):
        '''Create are return a new :py:class:`.SignalQueue`.
        '''
        Q = SignalQueue(self, **kws)
        self._signals.append(Q)
        return Q

    def proxy(self, **kws):
        '''A coroutine yielding a new client proxy object
        '''
        from .proxy import createProxy
        return createProxy(self, **kws)

    def get_sn(self):
        SN = self._nextsn
        self._nextsn = (SN+1)&0xffffffff
        return SN

    def _send(self, header, body):
        M = len(header)%8
        pad = b'\0'*(8-M) if M else b''
        S = [header, pad, body]
        # seems that with python 3.4.2 underlying .write() can't fail
        # other than OoM
        # TCP half-closed isn't supported.
        # we only find out about close from read side.
        self._W.writelines(S)
        if self.debug_net:
            self.log.debug("send message serialized %s", S)
 
    def call(self, *, path=None, interface=None, member=None, destination=None, sig=None, body=None,
             future=None):
        '''Call remote method
        
        :returns: A Future which completes with the result value
        :throws: RemoteError if call results in an Error response.
        '''
        assert path is not None, "Method calls require path="
        assert member is not None, "Method calls require member="
        assert sig is None or isinstance(sig, str), "Signature must be str (or None)"

        ret = asyncio.Future(loop=self._loop)

        if not self._running:
            ret.set_exception(NoReplyError())
            return ret
        elif self._closed is not None:
            raise ConnectionClosed()

        self.log.debug('call %s', (path, interface, member, destination, sig, body))

        opts = [
            (1, Object(path)),
            (3, member),
        ]
        if interface is not None:
            opts.append((2, interface))
        if destination is not None:
            opts.append((6, destination))

        if sig is not None:
            bodystr = encode(sig.encode('ascii'), body)
            opts.append((8, Signature(sig)))
        else:
            bodystr = b''

        SN = self.get_sn()
        req = (ord(_sys_L), METHOD_CALL, 0, 1,   len(bodystr), SN,   opts)
        self.log.debug("call message %s %s", req, bodystr)
        header = encode(b'yyyyuua(yv)', req)

        ret = future or asyncio.Future(loop=self._loop)
        self._inprog[SN] = ret
        self._send(header, bodystr)
        return ret

    def signal(self, *, path=None, interface=None, member=None, destination=None, sig=None, body=None):
        '''Emit a signal
        '''
        if not self._running:
            return # silently drop when not conected
        self.log.debug('signal %s', (path, interface, member, destination, sig, body))

        opts = [
            (1, Object(path)),
            (2, interface),
            (3, member),
        ]
        if destination is not None:
            opts.append((6, destination))

        if sig is not None:
            bodystr = encode(sig.encode('ascii'), body)
            opts.append((8, Signature(sig)))
        else:
            bodystr = b''

        req = (ord(_sys_L), SIGNAL, 0, 1,  len(bodystr), self.get_sn(),   opts)
        self.log.debug("signal message %s %s", req, bodystr)
        header = encode(b'yyyyuua(yv)', req)
        self._send(header, bodystr)


    def _method_return(self, event, sig, body):
        self.log.debug("return %s %s %s", event, sig, body)
        if not self._running:
            return # silently drop when not conected
        opts = [
            (5, Variant(b'u', event.serial)),
            (6, event.sender), # destination
        ]
        if body is not None:
            if sig is None:
                raise ValueError("body w/o sig")
            opts.append((8, Signature(sig)))
            bodystr = encode(sig.encode('ascii'), body)
        else:
            bodystr = b''
        if not self._running:
            return

        msg = (ord(_sys_L), METHOD_RETURN, 0, 1,   len(bodystr), self.get_sn(),   opts)
        self.log.debug("return message %s %s", msg, bodystr)
        header = encode(b'yyyyuua(yv)', msg)
        self._send(header, bodystr)

    def _error(self, event, name, msg):
        self.log.debug("error %s %s %s", event, name, msg)
        if not self._running:
            return # silently drop when not conected
        if not is_interface(name):
            self.log.warn('Invalid error name "%s"', name)
            name = 'dbucket.InvalidErrorName'
        msg = str(msg or name)
        opts = [
            (4, str(name)), # error name
            (5, Variant(b'u', event.serial)),
            (6, event.sender), # destination
            (8, Signature('s')),
        ]
        if not self._running:
            return

        body = encode(b's', msg)
        msg = (ord(_sys_L), ERROR, 0, 1,   len(body), self.get_sn(),   opts)
        self.log.debug("error message %s %s", msg, body)
        header = encode(b'yyyyuua(yv)', msg)
        self._send(header, body)

    @asyncio.coroutine
    def _recv_msg(self):
        '''Receive one dbus message
        '''
        # full message spec is
        #   yyyyuua(yv) ...body...
        # Treat the first part as
        #   yyyyuuu
        # to get body and header array sizes to compute the size of the complete message
        head = yield from self._R.readexactly(16)
        #self.log.debug("Header %s", repr(head))

        # validate byte order and version
        if head[0] not in (ord(b'l'), ord(b'B')) or head[3]!=1:
            raise RuntimeError('Invalid header %s'%head)

        mtype, flags = head[1], head[2]
        lsb = head[0]==ord(b'l')
        L = '<' if lsb else '>'

        blen, sn, hlen = struct.unpack(L+'III', head[4:])

        # dbus spec puts arbitrary upper bounds on message and header sizes
        if hlen+blen>2**27 or hlen>=2**26:
            raise RuntimeError('Message too big %s %s'%(hlen, blen))

        # header is padded so the body starts on an 8 byte boundary
        bstart = ((hlen+7)&~7)
        # no padding after body
        fullsize = bstart + blen
        #self.log.debug('Remainder hlen=%d bstart=%d blen=%d, fullsize=%d', hlen, bstart, blen, fullsize)

        rest = yield from self._R.readexactly(fullsize)
        if self.debug_net:
            self.log.debug("recv message %s", rest)
        headers, body = head+rest[:hlen], rest[bstart:]
        #self.log.debug('Raw Headers=%s body=%s', headers, body)

        # decode full header, but discard parts already handled
        fullheaders = decode(b'yyyyuua(yv)', headers, lsb=lsb)
        headers = fullheaders[-1]

        evt = BusEvent(mtype, sn, headers)

        # decode body if provided
        if len(body):
            evt.body = decode(evt.sig, body, lsb=lsb)

        self.log.debug('recv message %s %s', fullheaders, evt.body)

        return evt

    def _evt_return(self, evt, sig, F):
        try:
            val = F.result()
        except asyncio.CancelledError:
            pass
        except RemoteError as e:
            self._error(evt, e.name, repr(e))
        except Exception as e:
            self.log.exception("Error calling method %s", evt)
            name = "%s.%s"%(e.__class__.__module__, e.__class__.__name__)
            self._error(evt, name, repr(e))
        else:
            self._method_return(evt, sig, val)

    @asyncio.coroutine
    def _recv(self):
        try:
            while True:
                evt = yield from self._recv_msg()

                if evt.type==SIGNAL:
                    used = False
                    for M in self._signals:
                        used |= M._emit(evt)
                    if not used:
                        # this may happen naturally due to races with RemoveMatch
                       self.log.debug("Ignored signal %s", evt)

                elif evt.type in (METHOD_RETURN, ERROR): 
                    rsn = evt._return_sn
                    try:
                        F = self._inprog.pop(rsn)
                    except KeyError:
                        self.log.warn('Received reply/error with unknown S/N %s', rsn)
                    else:
                        if not F.cancelled():
                            if evt.type==METHOD_RETURN:
                                F.set_result(evt.body)
                            else:
                                F.set_exception(RemoteError(evt.body, name=evt._error))
                        else:
                            self.log.debug("Ignore reply to cancelled call %s", evt)

                elif evt.type==METHOD_CALL:
                    try:
                        ret, sig = self._methods.handle(evt)
                        if asyncio.iscoroutine(ret):
                            ret = ensure_future(ret)
                        if isinstance(ret, asyncio.Future):
                            ret.add_done_callback(partial(self._evt_return, evt, sig))
                            #TODO: keep track and cancel on dis-connect
                        else:
                            self._method_return(evt, sig, ret)
                    except RemoteError as e:
                        self._error(evt, e.name, repr(e))
                    except Exception as e:
                        self.log.exception("Error calling method %s", evt)
                        name = "%s.%s"%(e.__module__, e.__class__.__name__)
                        self._error(evt, name, repr(e))

                else:
                    self.log.debug('Ignoring unknown dbus message type %s', evt.type)

        except (asyncio.IncompleteReadError, asyncio.CancelledError) as e:
            if self._running:
                self.log.exception("Remote Close")
            else:
                self.log.debug("_recv closing: %s", e)

        except:
            self.log.exception('Error in Connnection RX')

        self._W.close() # closing a closed socket is a no-op
        self._running = False

        # immediatly fail local callers with NoReply
        self._cancel_pending()
        
        if not self._lost.done():
            self._lost.set_result(None)

    @asyncio.coroutine
    def _bus_sig(self):
        """Handle signals sender='org.freedesktop.DBus' (aka signals from the bus daemon)
        """
        last_state = SignalQueue.NORMAL
        while True:
            try:
                event, state = yield from self._bus_signals.recv(throw_done=False)
                if state==SignalQueue.DONE:
                    return
                elif state==SignalQueue.OFLOW:
                    if last_state==SignalQueue.NORMAL:
                        self.log.warn('Missed some dbus daemon signals')
                last_state = state

                if event.member=='NameAcquired':
                    if self._name is None:
                        self._name = event.body
                    self.log.debug("NameAcquired: %s", event.body)
                    self._names.add(event.body)

                elif event.member=='NameLost':
                    if event.body not in self._names:
                        self.log.warn("I've lost a name (%s) I didn't think I head?", event.body)
                    self._names.discard(event.body)

                else:
                    self.log.info("daemon signal %s", event)
            except GeneratorExit:
                raise
            except:
                self.log.exception("Error handling dbus daemon signal")
                yield from asyncio.sleep(10)

    @asyncio.coroutine
    def setup(self):
        '''Post connection setup.  Called by .auth.connect_bus()
        '''
        hello = yield from self.call(
            path='/org/freedesktop/DBus',
            member='Hello',
            interface='org.freedesktop.DBus',
            destination='org.freedesktop.DBus',
        )

        # at this point the 'NameAcquired' signal may already be delivered
        assert self._name in (hello, None), (self._name, hello)
        self._name = hello

        self.log = logging.getLogger(__name__+hello)

        from .proxy import createProxy
        self.daemon = yield from createProxy(self,
                               destination=DBUS,
                               path=DBUS_PATH,
                               interface=DBUS,
        )
