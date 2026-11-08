#!/usr/bin/env python3
"""Install the L4+R4 LED-ring toggle on an 8BitDo Ultimate 2C Wireless.

Self-contained. One file, no data files, nothing else from this repository:

    ./patch-2c-led.py info            what is connected and what it runs
    ./patch-2c-led.py update          fetch official v1.09, patch it, flash it
    ./patch-2c-led.py update IMG.dat  flash an image you already have
    ./patch-2c-led.py update --stock  put the official firmware back
    ./patch-2c-led.py verify          compare the device with the patch

The patched image is built in memory from 8BitDo's own v1.09 firmware: taken
from stock/ when tools/fetch-stock.py has already put it there, downloaded once
otherwise. The repository therefore ships no vendor firmware. The pad must be on the USB-C
cable — over the 2.4 GHz dongle the tool sees the adapter (0x301c) and this
script refuses to touch it.

Linux needs only python3. Windows/macOS need `pip install hidapi`.
"""
import glob
import json
import os
import select
import struct
import sys
import time
import urllib.request
import zlib

try:                      # Windows / macOS; Linux with /dev/hidraw needs nothing
    import hid as _hidapi
except Exception:
    _hidapi = None

VENDOR = 0x2DC8
USB_PID = 0x310A          # the pad on USB
DEV_PID = 0x301B          # what the firmware calls itself
TAG = 0x05
CMD_READ_PID = 0x0008
CMD_READ_FLASH = 0x0005
CMD_READ_CRC = 0x00C3
CMD_ERASE = 0x0004
CMD_WRITE = 0x0003
CMD_SAVE = 0x00C4
CMD_RESET = 0x0007

BLOCK = 4096
CHUNK = 46
HDR = 28                  # 28-byte .dat wrapper

# --- the patch itself --------------------------------------------------------
# Assembled TC32 (build/led_toggle.S), placed at firmware 0x18274. It gates the
# ring channel only: 3-sample debounce on L4+R4, one toggle per press, re-armed
# after both buttons are released.
CAVE_AT = 0x18274
CAVE_HEX = (
    "7065120b1b58120c2300120d00ab11c0a30211c16e4800ae10c0ae4801b6ae4003ae0bc1"
    "2e4801a466002e4000a66e40ae40038001a66e4000a6ae402e4800ae00c000a0f5975898"
    "706dc0464c34840000000006f03c8400")
# 4 bytes replacing the `tjl 0xd36c` at 0xD4F0 (the ring channel call) with a
# jump into the cave (build/hook.S).
HOOK_AT = 0xD4F0
HOOK_HEX = "0a90c09e"
# Cold-start zeroing boundary, extended by one word so the gate's state bytes
# (0x843CF0..0x843CF3) are cleared by the BSS loop at power-on.
BSS_LITERAL_AT = 0x617C
STATE = 0x843CF0

API = "https://support.8bitdo.com/restapi/link/v1/client/firmware/select"
# Cloudflare in front of support.8bitdo.com 403s non-browser user agents, and
# stalls some minimal header sets for ~30 s first. These headers keep it fast.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://web.8bitdo.com/",
    "Connection": "close",
}


def crc16(data):
    """CRC-16/ARC over the payload; writes are rejected without it."""
    crc = 0xFFFF
    for b in data:
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if (crc ^ b) & 1 else 0)
            b >>= 1
    return crc


# --- obtaining the firmware --------------------------------------------------
def fetch_stock(version="1.09"):
    """Download the official image from 8BitDo's update API (no auth)."""
    body = json.dumps({"firmware_type": 75, "beta": 0}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers=dict(HEADERS, **{"Content-Type": "application/json;charset=utf-8"}))
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read().decode()).get("data") or []
    for e in rows:
        if str(e.get("file_version")) == version:
            req = urllib.request.Request(e["fileURL"], headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if len(data) != int(e["fileSize"]):
                raise SystemExit("download size mismatch — try again")
            return bytearray(data)
    raise SystemExit(f"8BitDo's server does not offer v{version} for type 75 — "
                     "the patch offsets are tied to that version")


def build_patch(stock):
    """Apply the cave, the hook, the BSS bump and both checksums."""
    data = bytearray(stock)
    if len(data) != 0x1241C:
        raise SystemExit("unexpected firmware size — is this v1.09 for the 2C Wireless?")
    if struct.unpack_from("<4I", data) != (109, 0x6000, 0x12400, DEV_PID):
        raise SystemExit("unexpected firmware header — offsets are tied to v1.09")
    length = struct.unpack_from("<I", data, HDR + 0x18)[0]
    if length != 0x12274:
        raise SystemExit("unexpected image length — refusing to guess")

    def off(fw_addr):
        return fw_addr - 0x6000 + HDR

    cave = bytes.fromhex(CAVE_HEX)
    hook = bytes.fromhex(HOOK_HEX)
    if any(data[off(CAVE_AT):off(CAVE_AT) + len(cave)]):
        raise SystemExit("cave area is not empty in this image — refusing")
    if data[off(HOOK_AT):off(HOOK_AT) + 4] != bytes.fromhex("ff973c9f"):
        raise SystemExit("hook site does not hold the expected call — refusing")

    struct.pack_into("<I", data, off(BSS_LITERAL_AT), STATE + 4)
    data[off(HOOK_AT):off(HOOK_AT) + 4] = hook
    data[off(CAVE_AT):off(CAVE_AT) + len(cave)] = cave

    # two self-checks: the Telink image CRC, then the whole-payload residue
    inner = HDR + length - 4
    struct.pack_into("<I", data, inner, ~zlib.crc32(data[HDR:inner]) & 0xFFFFFFFF)
    struct.pack_into("<I", data, len(data) - 4,
                     ~zlib.crc32(data[HDR:-4]) & 0xFFFFFFFF)
    return data


def load_dat(path):
    """(version, length, payload) from a .dat on disk."""
    raw = open(path, "rb").read()
    version, _addr, length, pid = struct.unpack_from("<IIII", raw, 0)
    if pid != DEV_PID:
        raise SystemExit(f"{path}: firmware for pid {pid:#06x}; this script only "
                         f"handles {DEV_PID:#06x} (Ultimate 2C Wireless)")
    payload = raw[HDR:HDR + length]
    if len(payload) != length:
        raise SystemExit(f"{path}: truncated image")
    return version, length, payload


def repo_stock():
    """Path of a stock v1.09 image already on disk, or None.

    tools/fetch-stock.py drops it in stock/ so nothing has to be downloaded
    twice; this script only falls back to the network when it is missing.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [os.path.join(here, "stock"), os.path.join(here, "..", "stock"),
            os.path.join(here, ".."), "stock", "."]
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*.dat"))):
            try:
                with open(f, "rb") as fh:
                    head = fh.read(16)
            except OSError:
                continue
            if len(head) < 16:
                continue
            if struct.unpack_from("<4I", head) == (109, 0x6000, 0x12400, DEV_PID):
                return os.path.normpath(f)
    return None


def stock_image():
    """Official v1.09 bytes: from stock/ if present, else downloaded once."""
    path = repo_stock()
    if path:
        return bytearray(open(path, "rb").read())
    data = fetch_stock()
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "stock")
    try:
        os.makedirs(out, exist_ok=True)
        dest = os.path.join(out, "Ultimate 2C_v1.09_75.dat")
        with open(dest, "wb") as fh:
            fh.write(data)
        print(f"(saved stock image to {os.path.normpath(dest)} — reused next time)")
    except OSError:
        pass                      # read-only checkout: just keep it in memory
    return data


def get_image(path, stock_only=False):
    """(description, version, length, payload) from a file, or built in memory."""
    if path:
        version, length, payload = load_dat(path)
        return path, version, length, payload
    official = stock_image()
    version, _addr, length = struct.unpack_from("<III", official, 0)
    if stock_only:
        return ("official v1.09 (unpatched)", version, length,
                bytes(official[HDR:HDR + length]))
    patched = build_patch(official)
    version, _addr, length = struct.unpack_from("<III", patched, 0)
    return ("patched in memory from official v1.09", version, length,
            bytes(patched[HDR:HDR + length]))


# --- finding and opening the device ------------------------------------------
def parse_ids(desc):
    """(input report id, output report id) from a HID report descriptor."""
    rid, in_id, out_id = 0, None, None
    i = 0
    while i < len(desc):
        b = desc[i]
        size = (0, 1, 2, 4)[b & 3]
        tag = b & 0xFC
        if tag == 0x84:
            rid = desc[i + 1]
        elif tag == 0x80 and in_id is None:
            in_id = rid
        elif tag == 0x90 and out_id is None:
            out_id = rid
        i += 1 + size
    return in_id, out_id


def hidraw_nodes():
    """[(path, pid, output_report_id)] for every 8BitDo hidraw node."""
    out = []
    for name in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(name, "device", "uevent")) as f:
                props = dict(l.strip().split("=", 1) for l in f if "=" in l)
            _bus, vid, pid = props.get("HID_ID", "").split(":")
            if int(vid, 16) != VENDOR:
                continue
            with open(os.path.join(name, "device", "report_descriptor"), "rb") as f:
                desc = f.read()
        except (OSError, ValueError):
            continue
        out.append(("/dev/" + os.path.basename(name), int(pid, 16),
                    parse_ids(desc)[1]))
    return out


class Device:
    def __init__(self, path, out_id, usb_pid, timeout=3.0):
        self.path = path
        self.out_id = out_id or 0
        self.usb_pid = usb_pid
        self.timeout = timeout
        self.fd = None
        self.handle = None
        if _hidapi is not None and not path.startswith("/dev/"):
            self.handle = _hidapi.device()
            self.handle.open_path(path)
            self.handle.set_nonblocking(0)
            return
        try:
            self.fd = os.open(path, os.O_RDWR)
        except PermissionError:
            raise SystemExit(
                f"{path}: permission denied.\n\n"
                '  echo \'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", '
                'MODE="0666", TAG+="uaccess"\' \\\n'
                "    | sudo tee /etc/udev/rules.d/99-8bitdo-hidraw.rules\n"
                "  sudo udevadm control --reload-rules && "
                "sudo udevadm trigger --subsystem-match=hidraw\n\n"
                "then replug the controller, or run this script with sudo.")

    def close(self):
        if self.handle is not None:
            try:
                self.handle.close()
            except Exception:
                pass
        elif self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass

    def _write(self, payload):
        data = payload if self.out_id == 0 else bytes([self.out_id]) + payload
        if self.handle is not None:
            self.handle.write(data + bytes(max(0, 64 - len(data))))
        elif self.fd is not None:
            os.write(self.fd, data)

    def _read(self, timeout):
        if self.handle is not None:
            d = self.handle.read(128, max(1, int(timeout * 1000)))
            return (bytes(d) if not isinstance(d, bytes) else d) if d else None
        if self.fd is None:
            return None
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], max(0, end - time.time()))
            if r:
                d = os.read(self.fd, 128)
                if d:
                    return d
        return None

    def xfer(self, cmd, data=b"", length=None, total=0, offset=0, timeout=None):
        p = bytearray(63)
        p[0] = TAG
        struct.pack_into("<HHHHII", p, 1, cmd, 0,
                         len(data) if length is None else length,
                         crc16(data) if data else 0, total, offset)
        p[17:17 + len(data)] = data
        self._write(bytes(p))
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        while True:
            raw = self._read(max(0.05, deadline - time.time()))
            if raw is None:
                raise TimeoutError(f"no reply to cmd {cmd:#06x} at {offset:#x}")
            body = raw[1:]
            if len(body) < 17:
                raise IOError(f"short reply: {raw.hex()}")
            status, echo, ln = struct.unpack_from("<HHH", body, 1)
            if echo == cmd:        # ignore a late reply from an earlier request
                break
            if time.time() >= deadline:
                raise IOError(f"desynced reply: wanted {cmd:#06x}, got {echo:#06x}")
        if status != 0:
            raise IOError(f"device returned status {status} for cmd {cmd:#06x}")
        return body[17:17 + ln] if ln else body[17:]

    def read_pid(self):
        return struct.unpack_from("<H", self.xfer(CMD_READ_PID, total=4))[0]

    def read_flash(self, offset, length):
        out = bytearray()
        while len(out) < length:
            n = min(CHUNK, length - len(out))
            d = self.xfer(CMD_READ_FLASH, length=n, total=n,
                          offset=offset + len(out))
            if not d:
                raise IOError(f"empty read at {offset + len(out):#x}")
            out += d[:n]
        return bytes(out)

    def version(self):
        return struct.unpack_from("<H", self.read_flash(0x6000, 16), 2)[0]

    def block_crc(self, address, length):
        payload = struct.pack("<III", address, length, BLOCK)
        return struct.unpack_from(
            "<H", self.xfer(CMD_READ_CRC, data=payload, length=12,
                            total=12, offset=address))[0]

    def erase(self, address, length):
        self.xfer(CMD_ERASE, total=length, offset=address, timeout=10.0)

    def write(self, blob, address, total):
        for n in range(0, len(blob), CHUNK):
            self.xfer(CMD_WRITE, data=blob[n:n + CHUNK], total=total,
                      offset=address + n, timeout=5.0)

    def save(self, length, version):
        self.xfer(CMD_SAVE, data=struct.pack("<II", length, version), length=8)

    def reset(self):
        p = bytearray(63)
        p[0] = TAG
        struct.pack_into("<HHHHII", p, 1, CMD_RESET, 0, 0, 0, 0, 0)
        self._write(bytes(p))
        self._read(1.0)


def open_pad():
    """Open the interface that speaks the firmware protocol."""
    if _hidapi is not None and not os.path.isdir("/sys/class/hidraw"):
        cands = [d["path"] for d in _hidapi.enumerate(VENDOR)
                 if d["product_id"] == USB_PID]
        if not cands:
            raise SystemExit("no Ultimate 2C Wireless on USB — plug it in with the "
                             "USB-C cable (not the 2.4 GHz dongle)")
        for path in cands:
            dev = Device(path, 0x81, USB_PID)
            try:
                if dev.read_pid() == DEV_PID:
                    return dev
            except (TimeoutError, IOError):
                pass
            dev.close()
        raise SystemExit("the pad is on USB but did not answer — is Steam "
                         "holding the controller?")
    nodes = hidraw_nodes()
    for path, pid, out_id in nodes:
        if pid != USB_PID or out_id != 0x81:
            continue
        dev = Device(path, out_id, pid)
        try:
            if dev.read_pid() == DEV_PID:
                return dev
        except (TimeoutError, IOError):
            pass
        dev.close()
    if any(pid == 0x301C for _p, pid, _o in nodes):
        raise SystemExit(
            "only the 2.4 GHz dongle (0x301c) is connected.\n"
            "Plug the controller in with the USB-C cable — the dongle carries the\n"
            "adapter's firmware, not the pad's.")
    raise SystemExit("no Ultimate 2C Wireless found (USB 2dc8:310a)")


# --- commands ----------------------------------------------------------------
def _matches(dev, payload):
    for addr in range(0, len(payload), BLOCK):
        block = payload[addr:addr + BLOCK]
        try:
            if dev.block_crc(addr, len(block)) != crc16(block):
                return False
        except (TimeoutError, IOError):
            return False
    return True


def cmd_info(args):
    d = open_pad()
    try:
        v = d.version()
        print(f"device    : 8BitDo Ultimate 2C Wireless (usb {USB_PID:#06x}, "
              f"device {d.read_pid():#06x})")
        print(f"interface : {d.path}")
        print(f"firmware  : {v // 100}.{v % 100:02d} (raw {v})")
        if v != 109:
            print("LED patch : needs v1.09 — this pad runs a different version")
            return 0
        try:
            _desc, _ver, _len, payload = get_image(None)
        except (SystemExit, OSError) as e:
            print(f"LED patch : cannot build the image to compare ({e})")
            return 0
        print("LED patch : " + ("installed" if _matches(d, payload)
                                else "not installed"))
    finally:
        d.close()
    return 0


def cmd_verify(args):
    desc, _version, length, payload = get_image(args.file, args.stock)
    d = open_pad()
    bad = 0
    try:
        for addr in range(0, length, BLOCK):
            block = payload[addr:addr + BLOCK]
            want = crc16(block)
            have = d.block_crc(addr, len(block))
            if want != have:
                bad += 1
                print(f"  {addr:#08x}  {len(block):5d}B  want {want:#06x}  "
                      f"device {have:#06x}  DIFF")
        print(f"{desc}: " + ("identical to device flash" if not bad
                             else f"{bad} block(s) differ"))
    finally:
        d.close()
    return 0 if not bad else 1


def cmd_update(args):
    desc, version, length, payload = get_image(args.file, args.stock)
    d = open_pad()
    try:
        if not args.force and d.version() == version and _matches(d, payload):
            print(f"device already matches ({desc}) — nothing to do; "
                  "--force to rewrite")
            return 0
        print(f"flashing {desc}\n  v{version // 100}.{version % 100:02d}  "
              f"{length} bytes")
        for addr in range(0, length, BLOCK):
            block = payload[addr:addr + BLOCK]
            want = crc16(block)
            if not args.force and d.block_crc(addr, len(block)) == want:
                continue
            d.erase(addr, len(block))
            d.write(block, addr, length)
            got = d.block_crc(addr, len(block))
            if got != want:
                raise SystemExit(f"\nblock {addr:#x} did not verify "
                                 f"({got:#06x} != {want:#06x}) — re-run update")
            sys.stderr.write(f"\r  {addr // BLOCK + 1:3d} block(s) written  ")
        sys.stderr.write("\r" + " " * 32 + "\r")
        d.save(length, version)
        d.reset()
        print("done — the pad reboots itself; test L4+R4")
    finally:
        d.close()
    return 0


def main(argv):
    import argparse
    if os.name == "nt":
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(
        description="Install the L4+R4 LED-ring toggle on an Ultimate 2C Wireless")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="what is connected and what it runs")
    u = sub.add_parser("update", help="flash the patch (or a given image)")
    u.add_argument("file", nargs="?", help="a .dat to flash instead")
    u.add_argument("-f", "--force", action="store_true",
                   help="rewrite even if the device already matches")
    u.add_argument("--stock", action="store_true",
                   help="flash the official unpatched v1.09 (undo)")
    v = sub.add_parser("verify", help="compare the device with the patch")
    v.add_argument("file", nargs="?")
    v.add_argument("--stock", action="store_true",
                   help="compare against the official unpatched image")
    args = ap.parse_args(argv)
    return {"info": cmd_info, "update": cmd_update,
            "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
