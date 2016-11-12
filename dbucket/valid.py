
import re

__all__ = [
    'is_interface',
]

_interface = re.compile(r'^[A-Za-z0-9_]+\.(?:[A-Za-z0-9_]+\.)*[A-Za-z0-9_]+$')

def is_interface(s):
    """
    
    >>> is_interface("a.b")
    True
    >>> is_interface("aa.bb.cc")
    True
    >>> is_interface("a")
    False
    >>> is_interface(".a.b")
    False
    >>> is_interface("a.b.")
    False
    >>> is_interface("")
    False
    >>> is_interface(None)
    False
    """
    return isinstance(s, str) and _interface.match(s) is not None
