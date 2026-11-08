#!/usr/bin/env python3
"""Simulate the Windows/macOS transport without a Windows or macOS machine.

hidapi returns byte-string device paths there (`\\\\?\\hid#vid_2dc8&pid_310a`),
while hidraw returns `/dev/hidrawN`. That difference used to raise TypeError in
Device.__init__; this test pins the behaviour so it cannot come back.

Run: python tools/../tests/test_windows_path.py   (from the repo root)
"""
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "tools", "patch-2c-led.py")
WIN_PATH = b"\\\\?\\hid#vid_2dc8&pid_310a&col02#7&2f5b1c3&0&0001"


def reply(tag, status, cmd, payload, total=0, off=0):
    return b"\x81" + struct.pack("<BHHHHII", tag, status, cmd, len(payload), 0,
                                 total, off) + payload


class FakeHandle:
    def __init__(self):
        self.writes = []
        self.closed = False
        self.nonblocking = None
        self.replies = []

    def open_path(self, path):
        self.path = path

    def set_nonblocking(self, v):
        self.nonblocking = v

    def write(self, data):
        self.writes.append(bytes(data))
        # hidapi writes include the report id, so the packet starts at offset 1
        cmd = struct.unpack_from("<H", data, 2)[0]
        ln = struct.unpack_from("<H", data, 6)[0]
        total = struct.unpack_from("<I", data, 10)[0]
        off = struct.unpack_from("<I", data, 14)[0]
        if cmd == 0x0008:                       # read PID
            self.replies.append(reply(0x05, 0, cmd, struct.pack("<H", 0x301B), 4, 0))
        elif cmd == 0x0005:                     # read flash
            self.replies.append(reply(0x05, 0, cmd, bytes(ln), total, off))
        elif cmd == 0x00C3:                     # block CRC
            self.replies.append(reply(0x05, 0, cmd, struct.pack("<H", 0xABCD), 12, off))
        else:
            self.replies.append(reply(0x05, 0, cmd, b"", total, off))
        return len(data)

    def read(self, size, timeout_ms):
        return self.replies.pop(0) if self.replies else b""

    def close(self):
        self.closed = True


class FakeHid:
    def __init__(self):
        self.handles = []

    def enumerate(self, vid):
        assert vid == 0x2DC8
        return [dict(path=WIN_PATH, vendor_id=0x2DC8, product_id=0x310A,
                     product_string="8BitDo Ultimate 2C Wireless Controller",
                     usage_page=0xFF7A, usage=0x0001, interface_number=2)]

    def device(self):
        h = FakeHandle()
        self.handles.append(h)
        return h


def load():
    spec = importlib.util.spec_from_file_location("patch2c", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["patch2c"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load()
    fake = FakeHid()
    mod._hidapi = fake  # type: ignore[attr-defined]

    # Pretend this is Windows: no /sys/class/hidraw.
    real_isdir = mod.os.path.isdir
    mod.os.path.isdir = lambda p: False if p == "/sys/class/hidraw" else real_isdir(p)
    try:
        assert mod.use_hidapi() is True
        print("PASS: transport selects hidapi when /sys/class/hidraw is absent")

        dev = mod.open_pad()
        print(f"PASS: open_pad() found the device through a bytes path ({dev.path!r})")
        assert dev.handle is not None and dev.fd is None

        assert dev.read_pid() == 0x301B
        print("PASS: read_pid() over hidapi -> 0x301b")

        assert len(dev.read_flash(0x6000, 16)) == 16
        print("PASS: read_flash() chunk loop")

        assert dev.block_crc(0x0000, 4096) == 0xABCD
        print("PASS: block CRC query parsed")

        w = fake.handles[-1].writes[-1]
        assert len(w) == 64 and w[0] == 0x81, (len(w), w[0])
        print("PASS: report is 64 bytes with id 0x81 (Windows rejects short ones)")

        shown = dev.path.decode("utf-8", "replace")
        assert shown.startswith("\\\\?\\hid#"), shown
        print("PASS: path renders as text for the info header")

        dev.close()
        assert fake.handles[-1].closed
        print("PASS: close() released the handle")
    finally:
        mod.os.path.isdir = real_isdir

    # And the Linux branch must still be chosen when /sys is present.
    assert mod.use_hidapi() is False
    print("PASS: on Linux the hidraw transport is still selected")


if __name__ == "__main__":
    main()
