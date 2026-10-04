"""Local secret storage and process locks; never includes secrets in source files."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading
import time
import uuid

DATA = Path(__file__).resolve().parent / 'data'
LOCK = threading.RLock()


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(raw, decode=False):
    """The binary format remains compatible with existing Windows finder.dpapi files."""
    if os.name != 'nt':
        raise ValueError('Windows 加密文件不能在其他系统解密，请重新配置密钥')
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decode else ctypes.windll.crypt32.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError('本机加密配置无法读取或保存，请使用原 Windows 用户或重新配置')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(output.data)


def prepare():
    DATA.mkdir(exist_ok=True)
    if os.name != 'nt':
        DATA.chmod(0o700)


def load(name, default):
    encrypted, plain = DATA / f'{name}.dpapi', DATA / f'{name}.private.json'
    path = encrypted if os.name == 'nt' else plain
    if not path.exists():
        if os.name != 'nt' and encrypted.exists():
            raise ValueError('检测到旧 Windows 加密配置，请在本机重新配置并删除旧加密副本')
        return dict(default)
    raw = path.read_bytes()
    return json.loads(protect(raw, True) if os.name == 'nt' else raw)


def save(name, config):
    prepare()
    path = DATA / (f'{name}.dpapi' if os.name == 'nt' else f'{name}.private.json')
    raw = json.dumps(config, ensure_ascii=False).encode('utf-8')
    if os.name == 'nt':
        raw = protect(raw)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def locked(name, timeout=20):
    """Serialize configuration writes with mail submission in other processes."""
    prepare()
    with LOCK, (DATA / f'{name}.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        until = time.monotonic() + timeout
        if os.name == 'nt':
            import msvcrt
        else:
            import fcntl
        while True:
            try:
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise ValueError('邮件配置或提交正在处理，请稍后再试')
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
