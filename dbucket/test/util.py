
import logging
_log = logging.getLogger(__name__)
import asyncio, functools, logging

import os, tempfile
import subprocess as SP
from collections import defaultdict

def inloop(fn):
    """Decorator assumes wrapping method of object with .loop and maybe .timeout
    """
    @functools.wraps(fn)
    def testmethod(self):
        F = fn(self)
        if not hasattr(self, 'loop'):
            self.loop = asyncio.get_event_loop()
            self.loop.set_debug(True)
        timeout = getattr(self, 'timeout', None)
        if timeout is not None:
            F = asyncio.wait_for(F, timeout, loop=self.loop)
        self.loop.run_until_complete(F)
    return testmethod

class FakeConnection(object):
    def __init__(self):
        self._loop = asyncio.get_event_loop()
        self._running = False
        self._signals = []
        self.log = logging.getLogger(__name__)

        self._results = defaultdict(list)

    def prep_call(self, ret, *, interface=None, path='/', destination=None, member=None):
        self._results[(interface, path, destination, member)].append(ret)

    @asyncio.coroutine
    def call(self, *, interface=None, path='/', destination=None, member=None, sig=None, body=None):
        try:
            return self._results[(interface, path, destination, member)].pop(0)
        except:
            raise RuntimeError('Unexpected call: %s'%((interface, path, destination, member),))

    def signal(self, **kws):
        from ..conn import BusEvent, SIGNAL
        self._signals.append(BusEvent.build(SIGNAL, 1, **kws))

class DaemonRunner(object):
    daemon = 'dbus-daemon'
    def __init__(self, *, loop=None):
        from distutils.spawn import find_executable
        self.exe = find_executable(self.daemon)
        # this is an abstract socket, so the file never actually exists
        self.addr = tempfile.mktemp(prefix='dbus-test-')
        self.proc = None

        self.loop = loop
        self.F = asyncio.Future(loop=loop)

    def get_info(self):
        return [{'unix:abstract':self.addr}]

    def start(self):
        if self.proc is not None:
            raise RuntimeError("Already running")

        args = [self.exe, '--nofork', '--address=unix:abstract=%s'%self.addr, '--session']
        _log.debug("Launching daemon with: %s",
                   ' '.join(map(repr, args)))
        P = SP.Popen(args, executable=self.exe, shell=False,
                        stdin=SP.DEVNULL, pass_fds=(1,2))

        try:
            self.proc = P
            _log.info("Test dbus-daemon started")
            self.F.set_result(self.addr)
        except:
            P.kill()
            self.proc = None
            raise

    def stop(self):
        if self.proc is None:
            raise RuntimeError("Not running")
        self.proc.kill()
        self.proc = None
        self.F = asyncio.Future(loop=self.loop)
        _log.info("Test dbus-daemon stopped")

    @asyncio.coroutine
    def restart(self, wait=0.01):
        _log.info("Test dbus-daemon restarting")
        self.stop()
        yield from asyncio.sleep(wait)
        self.start()
        _log.info("Test dbus-daemon restarted")

    def __enter__(self):
        if self.proc is None:
            self.start()
        return self

    def __exit__(self, A,B,C):
        if self.proc is not None:
            self.stop()

    def __repr__(self):
        return 'DaemonRunner(%s)'%self.addr
    __str__ = __repr__

_testbus=[None]

def _close_test_bus():
    if _testbus[0] is not None:
        _testbus[0].stop()
        _testbus[0] = None

def test_bus():
    """Helper to start a process-wide unique dbus-daemon
    which will be automatically stopped on process exit.
    """
    if _testbus[0] is None:
        import atexit
        atexit.register(_close_test_bus)
        R = _testbus[0] = DaemonRunner(loop=None)
        R.start()
    return _testbus[0]

def test_bus_info():
    return test_bus().get_info()
