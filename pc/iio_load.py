"""Load pylibiio on Windows with the bundled libiio DLL.

`import iio` alone fails (find_library returns None). We add the DLL folder and patch
ctypes.util.find_library so that "iio" resolves to libiio.dll. We also silence the noisy
exceptions that iio.py raises from __del__ during interpreter shutdown.
"""
import os
import sys
import ctypes.util

_HERE = os.path.dirname(os.path.abspath(__file__))
# search order: env override, bundled pc/libiio (shipped with Muxwright.exe)
_CANDIDATES = [os.environ.get("DABLAB_LIBIIO_DIR"), os.path.join(_HERE, "libiio")]
IIO_DIR = next((d for d in _CANDIDATES if d and os.path.isfile(os.path.join(d, "libiio.dll"))), _CANDIDATES[-1])


def _quiet_unraisable(hook):
    def _h(u):
        try:
            obj = repr(u.object) if u.object is not None else ""
            tb = u.exc_traceback
            fname = ""
            while tb is not None:
                fname = tb.tb_frame.f_code.co_filename
                tb = tb.tb_next
            if "iio" in obj or fname.endswith("iio.py"):
                return
        except Exception:
            pass
        hook(u)
    return _h


def load():
    """Return the iio module or raise ImportError."""
    if os.path.isdir(IIO_DIR):
        os.environ["PATH"] = IIO_DIR + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(IIO_DIR)
        except (AttributeError, OSError):
            pass
        orig = ctypes.util.find_library
        dll = os.path.join(IIO_DIR, "libiio.dll")
        if not getattr(ctypes.util.find_library, "_dablab", False):
            def fl(name):
                if "iio" in name and os.path.exists(dll):
                    return dll
                return orig(name)
            fl._dablab = True
            ctypes.util.find_library = fl
    import iio  # noqa: E402
    sys.unraisablehook = _quiet_unraisable(sys.unraisablehook)
    # wrap __del__ of buffer/context so shutdown never prints tracebacks
    for cls_name in ("Buffer", "Context"):
        cls = getattr(iio, cls_name, None)
        d = getattr(cls, "__del__", None) if cls else None
        if d and not getattr(d, "_dablab", False):
            def mk(f):
                def _del(self):
                    try:
                        f(self)
                    except Exception:
                        pass
                _del._dablab = True
                return _del
            cls.__del__ = mk(d)
    return iio
