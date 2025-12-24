# Minimal stub for the websocket module used in tests.
# The real implementation is provided by the 'websocket-client' package.
# For the purposes of unit tests, we only need the module to exist.

class DummyWebSocket:
    def __init__(self, *args, **kwargs):
        pass

# Expose a dummy create_connection function.
def create_connection(*args, **kwargs):
    return DummyWebSocket()

# Mimic the API surface minimally.
__all__ = ['create_connection', 'DummyWebSocket']