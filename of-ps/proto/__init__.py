import importlib
import sys


def _load_net_pb2():
    module = importlib.import_module(".net_pb2", __name__)
    sys.modules.setdefault("proto.OverField_pb2", module)
    return module


def __getattr__(name):
    if name in {"net_pb2", "OverField_pb2"}:
        return _load_net_pb2()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["net_pb2", "OverField_pb2"]
