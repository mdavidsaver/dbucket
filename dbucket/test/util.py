import asyncio, functools

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

