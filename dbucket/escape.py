
import re

_re = re.compile(r"([^']+)|(')")

def escape_match(s):
    if s.find("'")==-1:
        return "'%s'"%s
    ret = []
    for S, Q in _re.findall(s):
        if len(S):
            ret.append("'%s'"%S)
        else:
            ret.append(r"\'")
    return ''.join(ret)
