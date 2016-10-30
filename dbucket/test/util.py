import asyncio, functools, logging

from collections import defaultdict

def inloop(fn):
    """Decorator assumes wrapping method of object with .loop and maybe .timeout
    """
    @functools.wraps(fn)
    def testmethod(self):
        F = fn(self)
        timeout = getattr(self, 'timeout', None)
        if timeout is not None:
            F = asyncio.wait_for(F, timeout)
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
