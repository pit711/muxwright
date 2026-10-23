# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['iio', 'paramiko']
hiddenimports += collect_submodules('scipy.signal')
# Server-/Runner-Code wird zur Laufzeit aus pc\ geladen: dort benutzte Standardmodule mitnehmen
hiddenimports += ['fractions', 'unicodedata', 'struct', 'xml.etree.ElementTree', 'glob', 'select', 'random', 'math', 'tempfile',
                  'signal', 'multiprocessing', 'posixpath', 'errno', 'hashlib', 'datetime', 'csv', 'zipfile', 'io', 'urllib.request',
                  'http.server', 'mimetypes', 'shlex', 'queue', 'copy', 'traceback', 'argparse', 'atexit', 'collections', 'json']
# große Pakete der Miniconda-Umgebung, die Muxwright nicht braucht (sonst > 3 GB)
EXCLUDES = ['torch', 'torchvision', 'torchaudio', 'onnxruntime', 'pandas', 'matplotlib', 'pyarrow', 'numba', 'llvmlite', 'grpc',
            'google', 'gi', 'lxml', 'openpyxl', 'pytest', 'av', 'PIL', 'tensorflow', 'sklearn', 'transformers', 'IPython', 'jupyter',
            'notebook', 'sympy', 'cv2', 'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'pydantic', 'fsspec', 'h5py', 'numexpr',
            'dask', 'distributed', 'boto3', 'botocore', 'sqlalchemy', 'jinja2', 'babel', 'docutils', 'sphinx', 'playwright',
            'win32com', 'pythoncom', 'pywintypes', 'psutil', 'anyio', 'requests', 'urllib3', 'yt_dlp', 'imageio', 'skimage']


a = Analysis(
    ['..\\dablab_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DABLab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
