#!/usr/bin/env python3
"""Fetch the official Ultimate 2C Wireless firmware so you have a clean base.

    ./fetch-stock.py                 # latest v1.09 build -> stock/
    ./fetch-stock.py --list          # show every build the server offers
    ./fetch-stock.py --version 106   # a specific build

Uses 8BitDo's own update API, no browser and no third-party packages.
Verification is by size: the `md5` field the API returns is the same across
different builds, so it is not a trustworthy digest.
"""
import argparse
import json
import os
import urllib.request

API = "https://support.8bitdo.com/restapi/link/v1/client/firmware/select"
UA = "Mozilla/5.0 (X11; Linux x86_64)"
FIRMWARE_TYPE = 75          # Ultimate 2C Wireless
PID = 0x301B


def entries(beta=0):
    """Ask the API which builds exist for this firmware type."""
    body = json.dumps({"firmware_type": FIRMWARE_TYPE, "beta": beta}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json;charset=utf-8", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode()).get("data") or []
    return sorted(data, key=lambda e: float(e.get("file_version", 0) or 0), reverse=True)


def fetch(entry, dest):
    req = urllib.request.Request(entry["fileURL"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    size = os.path.getsize(dest)
    if size != int(entry["fileSize"]):
        os.unlink(dest)
        raise SystemExit(f"size mismatch: got {size}, expected {entry['fileSize']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list builds")
    ap.add_argument("--version", help="pick a specific build, e.g. 1.06")
    ap.add_argument("--beta", action="store_true", help="include beta channel")
    ap.add_argument("-o", "--outdir", default="stock")
    args = ap.parse_args()

    rows = entries(1 if args.beta else 0)
    if not rows:
        raise SystemExit("the API returned no builds for firmware type 75")

    if args.list:
        for e in rows:
            print(f"v{e['file_version']}  {int(e['fileSize']):>7}B  {e['fileURL']}")
        return

    if args.version:
        want = float(args.version)
        rows = [e for e in rows if float(e["file_version"]) == want] or rows[:1]
    pick = rows[0]

    os.makedirs(args.outdir, exist_ok=True)
    dest = os.path.join(args.outdir, f"Ultimate 2C_v{float(pick['file_version']):.2f}_75.dat")
    fetch(pick, dest)
    print(f"wrote {dest} ({os.path.getsize(dest)} bytes)")
    print(f"build a patch from it:  ./build/build_patch.py {dest} my-patch.dat")


if __name__ == "__main__":
    main()
