
import asyncio, logging
from collections import defaultdict

from .conn import ConnectionClosed, DBUS, DBUS_PATH
from .auth import connect_bus

class PersistentConnection(object):
    def __init__(self, infofn, *, loop=None, name=None):
        self._infofn = infofn
        self._log = logging.getLogger(name or __name__)
        self._loop = loop or asyncio.get_event_loop()
        self._conn = None
        self._connect_F = asyncio.Future(loop=self._loop)
        self._disconnect_F = asyncio.Future(loop=self._loop)
        self._disconnect_F.set_result(self)

        self._connnect_T = asyncio.async(self._connect_task(), loop=self._loop)

        self._close_F = None

        self._call_Q = []

        self.daemon = None

        self._signals = {}

    @asyncio.coroutine
    def _connect_task(self):
        conn = None
        try:
            retry = 0.1
            while self._close_F is None:
                try:
                    self._log.debug("Connecting")
                    conn = yield from connect_bus(self._infofn(), loop=self._loop)
                    self.damon = conn.daemon
                    # daemon calls will not be queued
                except:
                    self._log.exception("Error while (re)connecting")
                else:
                    retry = 0.1
                    self._log.debug("Connected")

                    # mark ourselves as connected
                    self._disconnect_F = asyncio.Future(loop=self._loop)
                    self._connect_F.set_result(self)
                    self._conn = conn

                    # issue method calls queued while not connected
                    Fs = []
                    for kws in self._call_Q:
                        try:
                            Fs.append(conn.call(**kws))
                        except:
                            self._log.exception("Error calling queued method %s", kws)

                    self._call_Q = []

                    self._log.debug("Issue queued method calls")
                    yield from asyncio.gather(*Fs,
                                            loop=self._loop,
                                            return_exceptions=True)
                    self._log.debug("queued method calls complete")

                    try:
                        self._log.debug("Wait for dis-connect")
                        yield from conn._lost
                        self._log.debug("Dis-connect")
                        yield from conn.close()
                        self._log.debug("Closed")
                    except:
                        self._log.exception("Error while waiting for disconnect")
                    finally:
                        conn = self._conn = None
                        self.damon = None
                        self._connect_F = asyncio.Future(loop=self._loop)
                        self._disconnect_F.set_result(self)

                if self._close_F is not None:
                    break

                self._log.debug("Retry wait %s", retry)
                yield from asyncio.sleep(retry)
                if retry<15.0:
                    retry*=1.5
        except:
            self._log.exception("Unhandled error in (re)connect task")
            if not self._disconnect_F.done():
                self._disconnect_F.set_result(self)
            if self._connect_F.done():
                self._connect_F = asyncio.Future(loop=self._loop)
        finally:
            self.daemon = None
            if conn:
                yield from conn.close()

    def close(self):
        if self._close_F is None:
            self._log.debug("Closing")

            self._connnect_T.cancel()
            
            for C in self._call_Q:
                if not C.done():
                    C.set_result(ConnectionClosed())

            self._close_F = asyncio.async(self._close_task(), loop=self._loop)
        return self._close_F

    @asyncio.coroutine
    def _close_task(self):
        yield from self._connnect_T
        self._log.debug("Closed")

    @property
    def name(self):
        'My primary bus name'
        if self._conn is not None:
            return self._conn._name
        else:
            return '<dis-connected>'

    @property
    def names(self):
        'All my bus names'
        if self._conn is not None:
            return self._conn._names
        else:
            return []

    @property
    def running(self):
        'Connected?'
        return self._conn is not None and self._close_F is None

    @property
    def loop(self):
        return self._loop

    @property
    def connect(self):
        return self._connect_F

    @property
    def disconnect(self):
        return self._disconnect_F

    def proxy(self, **kws):
        '''A coroutine yielding a new client proxy object
        '''
        from .proxy import createProxy
        return createProxy(self, **kws)

    def call(self, **kws):
        if self._close_F is not None:
            raise ConnectionClosed()

        elif self._conn is None:
            F = asyncio.Future(loop=self._loop)
            K = {'future':F}
            K.update(kws)
            self._call_Q.append(K)
            return F

        else:
            return self._conn.call(**kws)

    def signal(self, **kws):
        if self._conn is not None:
            return self._conn.signal(**kws)

    def new_queue(self, **kws):
        '''Create are return a new :py:class:`.SignalQueue`.
        '''
        Q = SignalQueue(self, **kws)
        self._signals.append(Q)
        if self._conn is not None:
            self._conn._add_queue(Q)
        return Q

    @asyncio.coroutine
    def AddMatch(self, obj, expr):
        if self._close_F is not None:
            raise ConnectionClosed()
        if self._conn is not None:
            yield from self._conn.AddMatch(obj, expr)
        self._signals[expr].add(obj)

    @asyncio.coroutine
    def RemoveMatch(self, obj, expr):
        if self._close_F is not None:
            return
        S = self._signals[expr]
        S.remove(obj)
        if len(S)==0:
            del self._signals[expr]
        if self._conn is not None:
            yield from self._conn.AddMatch(obj, expr)
