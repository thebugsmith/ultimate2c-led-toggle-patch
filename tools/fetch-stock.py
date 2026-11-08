#!/usr/bin/env python3
"""Fetch the official Ultimate 2C Wireless firmware so you have a clean base.

    ./fetch-stock.py                 # v1.09 -> stock/ (skipped if already there)
    ./fetch-stock.py --force         # download again anyway
    ./fetch-stock.py --list          # every build the server offers
    ./fetch-stock.py --version 1.06  # a specific build

Uses 8BitDo's own update API: no browser, no third-party packages.

Two things this handles, learned the hard way:

* support.8bitdo.com sits behind Cloudflare and **403s non-browser user
  agents**. Sending a browser-like header set is what makes the download fast;
  a bare `Python-urllib` agent gets rejected, and some requests stall ~30 s
  before failing, which looks like a slow connection.
* The file is only 74,780 bytes. If it is already on disk at the right size it
  is not downloaded again.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://support.8bitdo.com/restapi/link/v1/client/firmware/select"
FIRMWARE_TYPE = 75          # Ultimate 2C Wireless
ATTEMPTS = 3

# Cloudflare rejects "Python-urllib" and stalls some minimal header sets.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://web.8bitdo.com/",
    "Connection": "close",
}


def entries(beta=0):
    """Ask the API which builds exist for this firmware type."""
    body = json.dumps({"firmware_type": FIRMWARE_TYPE, "beta": beta}).encode()
    headers = dict(HEADERS, **{"Content-Type": "application/json;charset=utf-8"})
    req = urllib.request.Request(API, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode()).get("data") or []
    return sorted(data, key=lambda e: float(e.get("file_version", 0) or 0),
                  reverse=True)


def fetch(entry, dest):
    """Download with progress, size check and retries; write via a .part file."""
    want = int(entry["fileSize"])
    tmp = dest + ".part"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(entry["fileURL"], headers=HEADERS)
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=20) as r, open(tmp, "wb") as f:
                got = 0
                last = 0.0
                while True:
                    chunk = r.read(16384)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    if now - last > 0.25:
                        last = now
                        pct = got * 100 // want if want else 0
                        sys.stderr.write(f"\r  {pct:3d}%  {got}/{want} B  "
                                         f"{got / max(now - t0, 1e-6) / 1024:.0f} KB/s")
                        sys.stderr.flush()
            sys.stderr.write("\r" + " " * 56 + "\r")
            if got != want:
                raise IOError(f"size mismatch: got {got}, expected {want}")
            os.replace(tmp, dest)
            print(f"wrote {dest} ({got} bytes in {time.time() - t0:.1f}s)")
            return
        except urllib.error.HTTPError as e:
            why = (f"HTTP {e.code}: Cloudflare rejected the request"
                   if e.code == 403 else f"HTTP {e.code} {e.reason}")
        except (urllib.error.URLError, OSError, IOError) as e:
            why = str(getattr(e, "reason", e))
        if os.path.exists(tmp):
            os.unlink(tmp)
        if attempt == ATTEMPTS:
            raise SystemExit(
                f"download failed ({why}).\n"
                "The image can also be had from the official updater at\n"
                "https://web.8bitdo.com — save it as "
                f"{os.path.basename(dest)} in {os.path.dirname(dest) or '.'}/")
        print(f"  attempt {attempt} failed ({why}); retrying…")
        time.sleep(2 * attempt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list builds")
    ap.add_argument("--version", help="pick a specific build, e.g. 1.06")
    ap.add_argument("--beta", action="store_true", help="include beta channel")
    ap.add_argument("--force", action="store_true", help="download even if cached")
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
    dest = os.path.join(
        args.outdir, f"Ultimate 2C_v{float(pick['file_version']):.2f}_75.dat")

    if (not args.force and os.path.isfile(dest)
            and os.path.getsize(dest) == int(pick["fileSize"])):
        print(f"already have {dest} ({os.path.getsize(dest)} bytes) — "
              "use --force to download again")
    else:
        fetch(pick, dest)

    print(f"build a patch from it:  ./build/build_patch.py {dest} my-patch.dat")


if __name__ == "__main__":
    main()
