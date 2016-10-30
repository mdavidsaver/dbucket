
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
