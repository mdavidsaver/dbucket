
import logging
_log = logging.getLogger(__name__)

import sys, struct
from functools import partial
import asyncio

from .xcode import encode, decode, Object, Signature

# Name and Interface for DBUS daemon
DBUS='org.freedesktop.DBus'
# Path for DBUS daemon
DBUS_PATH='/org/freedesktop/DBus'

METHOD_CALL = 1
METHOD_RETURN = 2
ERROR = 3
SIGNAL = 4

_sys_lsb = sys.byteorder=='little'
_sys_L   = b'l' if _sys_lsb else b'B'

class ConnectionClosed(asyncio.CancelledError):
    def __init__(self):
        asyncio.CancelledError.__init__(self, 'Connection closed')

class RemoteError(RuntimeError):
    def __init__(self, msg, name):
        RuntimeError.__init__(self, msg)
        self.name = name

def _loop_sync(loop):
    '''Synchronize loop callback queue.
    Returns after all presently pending callbacks have run
    '''
    F=asyncio.Future(loop=loop)
    loop.call_soon(partial(F.set_result, None))
    return F

class BusEvent(object):
    def __init__(self, sn, headers, body):
        self.serial = sn
        self.body = body
        self.path = headers[1]
        self.interface = headers[2]
        self.member = headers[3]
        self.destination = headers[6]
        self.sender = headers[7]
    def __repr__(self):
        return "%s(%s)"%(self.__class__.__name__, self.__dict__)

class Match(object):
    """Queue of received bus events (METHOD_CALL or SIGNAL)

    Match(connection,
          sender = None|'name',
          interface = None|'name',
          member = None|'name',
          path = None|Object('name'),
          destination = None|'name',
    )
    """
    NORMAL = 0
    OFLOW = 1
    DONE = 2
    sender=interface=member=path=destination=None
    def __init__(self, conn, **kws):
        self._state = self.NORMAL
        # item Q'd (headers, body, status)
        self._Q = asyncio.Queue(maxsize=kws.get('qsize', 4), loop=conn._loop)
        if not hasattr(self._Q, 'task_done'):
            # added in python 3.4.4
            self._Q.task_done = lambda:None
        self.conn = conn

        # build match expression (eg. "interface='foo.bar',member='baz'")
        match = self._conds = []
        for name in ('sender', 'interface', 'member', 'path', 'destination'):
            V = kws.get(name)
            setattr(self, name, V)
            if V is not None:
                match.append((name, str(V)))
        #TODO: escape "'" and "\'
        self._expr = ','.join(["%s='%s'"%(K,V) for K,V in match])

    @asyncio.coroutine
    def recv(self):
        """coroutine returning the next bus event
        
        returns (BusEvent, state)
        
        Returned BusEvent should not be modified
        """
        if self._expr is None:
            raise ConnectionClosed()
        R = yield from self._Q.get()
        self._Q.task_done()
        return R

    def _close(self):
        pass

    @asyncio.coroutine
    def close(self):
        """Stop receiving for this Match.
        
        A coroutine which completes after delivering queued events
        """
        if self._expr is None:
            return
        self._expr = None
        self._state = self.DONE

        self._close()

        self.conn._signals.remove(self)
        yield from self._Q.put((None, self.DONE))

        yield from self._Q.join()

    def _emit(self, event):
        if self._expr is None:
            return False
        assert self._state is not self.DONE, self._state

        # check match conditions
        for N in ('sender', 'interface', 'member', 'path', 'destination'):
            M = getattr(self, N)
            if M is not None and M!=getattr(event, N):
                return False

        try:
            self._Q.put_nowait((event, self._state))
            if self._state == self.OFLOW:
                _log.debug("%s %s leaves overflow state", self.__class__.__name__, self._conds)
            self._state = self.NORMAL
            return True
        except asyncio.QueueFull:
            if self._state != self.OFLOW:
                _log.debug("%s %s enters overflow state", self.__class__.__name__, self._conds)
            self._state = self.OFLOW
            return False

class SignalMatch(object):
    def _close(self):
        if self.conn._running:
            _log.debug('RemoveMatch: %s', self._expr)

            try:
                yield from self.call(interface='org.freedesktop.DBus',
                                    member='RemoveMatch',
                                    destination='org.freedesktop.DBus',
                                    sig=b's',
                                    body=self._expr)
            except:
                _log.exception("Error while RemoveMatch %s", self._conds)

class MethodMatch(object):
    def done(self, evt, sig, body):
        self.conn._return(evt, sig, body)
    def error(self, evt, sig, body):
        self.conn._error(evt, sig, body)

class Connection(object):
    # ignore/error all calls from peers
    ignore_calls = True

    def __init__(self, W, R, info, loop=None):
        self._W, self._R, self._info, self._loop = W, R, info, loop or asyncio.get_event_loop()
        self._running = True

        self._inprog  = {} # in progress method calls
        self._signals = [] # registered signal matches
        self._calls   = [] # registered method call matches
        #TODO: index storage of Match based on one of the headers?

        self._nextsn = 1 #TODO: randomize?
        self._RX = self._loop.create_task(self._recv())
        self._name, self._names = None, set()

        self._bus_signals = Match(self, qsize=20, sender=DBUS, path=DBUS_PATH, interface=DBUS)
        self._signals.append(self._bus_signals)
        self._SIGS = self._loop.create_task(self._bus_sig())

    @asyncio.coroutine
    def close(self, sync=True):
        if not self._running:
            return
        self._W.close()
        self._running = False
        # join the receiver Task
        self._RX.cancel()

        # join receiver task
        if asyncio.Task.current_task() is not self._RX:
            yield from self._RX

        # fail pending method calls
        for act in self._inprog.values():
            if not act.done():
                act.set_exception(asyncio.CancelledError())

        # notify signal listeners
        F = list([M.close() for M in self._signals])

        # notify method listeners
        F.extend([M.close() for M in self._calls])

        # wait for notification to be delivered
        yield from asyncio.gather(*F, loop=self._loop, return_exceptions=True)

        # join daemon signal task
        yield from self._SIGS

        # paranoia, wait for all currently pending callbacks to be run
        # intended to help with a clean shutdown when used
        # like 'loop.run_until_complete(conn.close())'
        yield from _loop_sync(self._loop)

    @property
    def name(self):
        return self._name

    @asyncio.coroutine
    def AddMatch(self, **kws):
        if not self._running:
            raise ConnectionClosed()
        M = SignalMatch(self, **kws)
        self._signals.append(M)
        _log.debug('AddMatch: %s', M._expr)
        try:
            yield from self.call(interface='org.freedesktop.DBus',
                                member='AddMatch',
                                destination='org.freedesktop.DBus',
                                sig=b's',
                                body=M._expr)
        except:
            self._signals.remove(M)
            raise
        return M

    def AddCall(self, **kws):
        if not self._running:
            raise ConnectionClosed()
        M = MethodMatch(self, **kws)
        self._calls.append(M)
        _log.debug('AddCall: %s', M._expr)
        #TODO: need AddMatch w/ type='method_call' ??
        return M

    def get_sn(self):
        SN = self._nextsn
        self._nextsn = SN+1
        return SN

    def _send(self, header, body):
        try:
            _log.debug("send head %s", header)
            self._W.write(header)
            M = len(header)%8
            if M:
                self._W.write(b'\0'*(8-M))
            _log.debug("send body %s", body)
            self._W.write(body)

        except:
            # failure at this point may mean part of the message is queued,
            # so we must close the connection
            self.close() #TODO: wait for completion???
            raise

    def call(self, path=None, interface=None, member=None, destination=None, sig=None, body=None):
        assert path is not None, "Method calls require path="
        assert member is not None, "Method calls require member="
        if not self._running:
            raise ConnectionClosed()
        _log.debug('call %s', (path, interface, member, destination, sig, body))

        SN = self.get_sn()

        opts = [
            [1, Object(path)],
            [3, member],
        ]
        if interface is not None:
            opts.append([2, interface])
        if destination is not None:
            opts.append([6, destination])
        if sig is not None:
            body = encode(sig, body)
            opts.append([8, Signature(sig)])
        else:
            body = b''

        req = [ord(_sys_L), METHOD_CALL, 0, 1,   len(body), SN,   opts]
        header = encode(b'yyyyuua(yv)', req)

        ret = asyncio.Future(loop=self._loop)
        self._inprog[SN] = ret
        self._send(header, body)
        return ret

    def _method_return(self, event, sig, body):
        opts [
            (5, Variant(b'b', event.serial)),
        ]
        if body is not None:
            if not isinstance(sig, bytes):
                raise ValueError("body w/o sig")
            opts.append((8, Signature(sig)))
        else:
            body = b''
        if not self._running:
            return
        _log.debug("return %s %s %s", event, sig, body)
        
        SN = self.get_sn()

        msg = [ord(_sys_L), METHOD_RETURN, 0, 1,   len(body), SN,   opts]
        header = encode(b'yyyyuua(yv)', msg)
        self._send(header, body)

    def _error(self, event, err):
        opts [
            (5, Variant(b'b', event.serial)),
            (4, String(err)),
        ]
        if not self._running:
            return
        body = b''
        _log.debug("error %s %s %s", event, sig, body)

        msg = [ord(_sys_L), ERROR, 0, 1,   len(body), SN,   opts]
        header = encode(b'yyyyuua(yv)', msg)
        self._send(header, body)

    @asyncio.coroutine
    def _recv(self):
        try:
            while True:
                # full message spec is
                #   yyyyuua(yv) ...body...
                # Treat the first part as
                #   yyyyuuu
                # to get body and header array sizes to compute the size of the complete message
                head = yield from self._R.readexactly(16)
                _log.debug("Header %s", repr(head))

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
                _log.debug('Remainder hlen=%d bstart=%d blen=%d, fullsize=%d', hlen, bstart, blen, fullsize)

                rest = yield from self._R.readexactly(fullsize)
                headers, body = head+rest[:hlen], rest[bstart:]
                _log.debug('Raw Headers=%s body=%s', headers, body)

                # decode full header, but discard parts already handled
                headers = decode(b'yyyyuua(yv)', headers, lsb=lsb)
                headers = headers[-1]

                # transform headers into array
                H = [None]*10
                for code, val in headers:
                    if code<len(H):
                        H[code] = val
                headers = H
                _log.debug('Headers %s', headers)
                del H

                # decode body if provided
                if len(body):
                    sig = headers[8] # body signature
                    body = decode(sig, body, lsb=lsb)
                else:
                    body = None
                _log.debug('Body %s', body)

                if mtype==METHOD_CALL:
                    evt = BusEvent(sn, headers, body)
                    if not self.ignore_calls:
                        for M in self._calls:
                            if M._emit(evt):
                                evt = None # consumed
                                break
                    if evt is not None:
                        self._error(evt, "No one cared")
                        
                elif mtype in (METHOD_RETURN, ERROR): 
                    rsn = headers[5]
                    try:
                        F = self._inprog[rsn]
                    except KeyError:
                        _log.warn('Received reply/error with unknown S/N %s', rsn)
                    else:
                        if not F.cancelled():
                            if mtype==2:
                                F.set_result(body)
                            else:
                                F.set_exception(RemoteError(body or headers[4], headers[4]))
                elif mtype==SIGNAL:
                    evt = BusEvent(sn, headers, body)
                    used = False
                    for M in self._signals:
                        used |= M._emit(evt)
                    if not used:
                        # this may happen naturally due to races with RemoveMatch
                       _log.debug("Ignored signal %s %s %s %s", headers)
                else:
                    _log.debug('Ignoring unknown dbus message type %s', mtype)

        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            return # connection closed
        except:
            _log.exception('Error in Connnection RX')
            self._W.close()
            self._running = False

    @asyncio.coroutine
    def _bus_sig(self):
        """Handle signals sender='org.freedesktop.DBus' (aka signals from the bus daemon)
        """
        last_state = Match.NORMAL
        while True:
            try:
                event, state = yield from self._bus_signals.recv()
                if state==Match.DONE:
                    return
                elif state==Match.OFLOW:
                    if last_state==Match.NORMAL:
                        _log.warn('Missed some dbus daemon signals')
                last_state = state

                if event.member=='NameAcquired':
                    if self._name is None:
                        self._name = event.body
                    _log.debug("NameAcquired: %s", event.body)
                    self._names.add(event.body)
                        
                else:
                    _log.info("daemon signal %s", event)
            except:
                _log.exception("Error handling dbus daemon signal")
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
