"""Test package setup, imported by both unittest and pytest.

Puts the tools directory on the import path and blocks network access for the
whole test process.
"""

import os
import socket
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)


def _network_disabled(*args, **kwargs):
    raise OSError("network disabled in tests")


socket.socket.connect = _network_disabled
socket.socket.connect_ex = _network_disabled
socket.create_connection = _network_disabled
