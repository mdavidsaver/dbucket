
import asyncio
import unittest

from .util import DaemonRunner, inloop

class TestRunner(unittest.TestCase):
    def test_start(self):
        with DaemonRunner() as run:
            self.assertRegex(run.addr, "dbus-test-")

    @inloop
    @asyncio.coroutine
    def test_restart(self):
        with DaemonRunner() as run:
            abefore, pbefore = run.addr, run.proc.pid
            yield from run.restart()
            aafter, pafter = run.addr, run.proc.pid
        self.assertRegex(aafter, "dbus-test-")
        self.assertEqual(abefore, aafter)
        self.assertNotEqual(pbefore, pafter)
