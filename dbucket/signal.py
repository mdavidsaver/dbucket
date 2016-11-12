
import asyncio

from .escape import escape_match

class Condition(object):
    """Signal matching condition

    Each testable parameter may be either None (wildcard) are a string to match exactly.

    :param str|None type: 'signal' or None
    :param str|None sender: Original of the signal.  Unique bus name (eg. ':1.1') or 'org.freedesktop.DBus'
    :param str|None destination: 'signal' or None
    :param str|None interface: 'signal' or None
    :param str|None member: 'signal' or None
    :param str|None path: 'signal' or None
    :param str|None path_namespace: 'signal' or None
    """
    cattrs = set(['type', 'sender', 'interface', 'member', 'path', 'path_namespace', 'destination'])
    def __init__(self, **kws):
        from .conn import DBUS
        self._remove = kws.pop('remove', True)

        if 'sender' in kws and kws['sender']!=DBUS and kws['sender'][0]!=':':
            # signals are always delivered with sender= set to the unique bus name of the orginator,
            # except for message from the dbus daemon.
            raise ValueError("AddMatch with sender='%s' isn't meaningful with well-known names"%kws['sender'])

        if kws.get('path','').endswith('/*'):
            # magicly translate path='/A/*' -> path_namespace='/A'
            kws['path_namespace'] = kws.pop('path')[:-2]
        self._cond, expr = [], []
        for K, V in kws.items():
            if K in self.cattrs:
                expr.append("%s=%s"%(K,escape_match(V)))
                if K!='type':
                    self._cond.append((K, V))

        self.expr = ','.join(expr)

    def test(self, evt):
        for K, V in self._cond:
            if K=='path_namespace' and not evt.path.startswith(V):
                return False
            elif getattr(evt, K)!=V:
                return False
        return True

    def __repr__(self):
        return "%s(%s)"%(self.__class__.__name__, self.expr)

class SignalQueue(object):
    """Handles Signal matching condition(s) and a Queue of received signals.

    :param int qsize: Maximum capacity of signal queue.
    """
    #: Normal operation (not overflow)
    NORMAL = 0
    #: Queue overflowed.  Some signals lost before this one
    OFLOW = 1
    #: close() was called
    DONE = 2

    Condition = Condition
    def __init__(self, conn, *, qsize=4):
        self.conn, self._cond = conn, []
        self._done, self._oflow = 0, self.NORMAL
        self._Q = asyncio.Queue(maxsize=qsize, loop=conn._loop)
        if not hasattr(self._Q, 'task_done'):
            # added in python 3.4.4
            self._Q.task_done = lambda:None
        # delgate Q state info
        self.empty, self.full, self.qsize = self._Q.empty, self._Q.full, self._Q.qsize

    @asyncio.coroutine
    def add(self, **kws):
        if self._done>0:
            raise RuntimeError("close() called")
        """Add a new matching Condition
        
        :param str|None type: 'signal' or None
        :param str|None sender: Original of the signal.  Unique bus name (eg. ':1.1') or 'org.freedesktop.DBus'
        :param str|None destination: 'signal' or None
        :param str|None interface: 'signal' or None
        :param str|None member: 'signal' or None
        :param str|None path: 'signal' or None
        :param str|None path_namespace: 'signal' or None
        :returns: Condition
        """
        if self._done>0:
            raise RuntimeError("Already close()d")
        C = self.Condition(**kws)

        self._cond.append(C)

        if C._remove:
            try:
                yield from self.conn.AddMatch(C, C.expr)
            except:
                self._cond.remove(C)
                raise
        return C

    @asyncio.coroutine
    def remove(self, C):
        """Removes a Condition returned by add()
        
        :param Condition C: Condition to remove
        :throws: RuntimeError if the Condition has not been added, or has already been removed
    
        A coroutine
        """
        if self._done>0:
            return
        elif C not in self._cond:
            raise RuntimeError("Not my condition %s"%C)

        self._cond.remove(C)
        if C._remove:
            yield from self.conn.RemoveMatch(C, C.expr)

    @asyncio.coroutine
    def close(self):
        """Remove all Conditions and push DONE to the queue.

        This coroutine completes after all matches are removed
        and DONE has been pushed to the queue.
        If the queue is full, this coroutine will not complete
        until one entry has been recv() d.
        """
        if self._done>0:
            return
        self._done = 1

        # remove out matches
        conds, self._cond = self._cond, []
        yield from asyncio.gather(*[self.conn.RemoveMatch(C, C.expr) for C in conds],
                                  loop=self.conn._loop, return_exceptions=True)

        yield from self._Q.put((None, self.DONE)) # waits if _Q is full

    @asyncio.coroutine
    def recv(self, *, throw_done=True):
        """coroutine yielding the next bus event

        :param bool throw_done: If False then returns (None, DONE). If True then ConnectionClosed is thrown.
        :returns: (:py:class:`.BusEvent`, NORMAL|OFLOW|DONE)
        :throws: ConnectionClosed if throw_done=True and close() has been called.

        Returned BusEvent should not be modified
    
        A coroutine
        """
        if self._done<2:
            evt, sts = yield from self._Q.get()
            self._Q.task_done()
            if sts==self.DONE:
                self._done=2
        else:
            evt, sts = None, self.DONE
        if throw_done and sts==self.DONE:
            from .conn import ConnectionClosed
            raise ConnectionClosed()
        return evt, sts

    def poll(self, *, throw_done=True):
        """Non-blocking version of recv()
        
        :throws: asyncio.QueueEmpty
        """
        if self._done<2:
            evt, sts = self._Q.get_nowait()
            self._Q.task_done()
            if sts==self.DONE:
                self._done=2
        else:
            evt, sts = None, self.DONE
        if throw_done and sts==self.DONE:
            raise ConnectionClosed()
        return evt, sts


    def _emit(self, evt):
        if self._done>0:
            return False

        # check match conditions
        ok = False
        for C in self._cond:
            ok |= C.test(evt)
        if not ok:
            return False

        self.conn.log.debug("Match %s %s", self, evt)
        try:
            self._Q.put_nowait((evt, self._oflow))
            if self._oflow == self.OFLOW:
                self.conn.log.debug("%s %s leaves overflow state", self.__class__.__name__, self._cond)
            self._oflow = self.NORMAL
            return True
        except asyncio.QueueFull:
            if self._oflow != self.OFLOW:
                self.conn.log.debug("%s %s enters overflow state", self.__class__.__name__, self._cond)
            self._oflow = self.OFLOW
            return False

    def __repr__(self):
        return "%s(%s)"%(self.__class__.__name__, self._cond)

