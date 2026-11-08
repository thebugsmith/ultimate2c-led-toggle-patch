# Ultimate 2C - toggle the LED ring with L4 + R4

Hold L4 + R4 to toggle the LED ring on/off. 

Tested on and only work with **Ultimate 2C Wireless**. The patch is a byte-level modification of the official firmware  v1.09, and is not compatible with other 2C variants. 


## Install it

One script does everything: `tools/patch-2c-led.py`. It needs no other file from
this repository — it takes the official v1.09 image (from `stock/` if
`tools/fetch-stock.py` already put it there, otherwise it downloads it once and
caches it), applies the patch in memory, and flashes it.

```console
$ python tools/fetch-stock.py        # optional: cache the official image in stock/
$ python tools/patch-2c-led.py info      # what is connected, patch installed or not
$ python tools/patch-2c-led.py update    # build + flash the patch
$ python tools/patch-2c-led.py verify    # compare the device with the patch
```

Connect the controller with the USB-C **cable** and unplug the 2.4 GHz adapter —
over the dongle the tool sees the adapter (`0x301c`) and refuses.

`update` is safe to re-run: it compares block CRCs first, skips identical blocks,
and reports "already matches" instead of rewriting. `--force` rewrites anyway.

### Rollback

```console
$ python tools/patch-2c-led.py update --stock --force    # official v1.09 back
```

## Rebuilding the patch from source

Only needed if you want to check the image rather than trust the script:

```console
$ python build/build_patch.py "./stock/Ultimate 2C_v1.09_75.dat" ./patches/mine.dat
$ python build/verify_patch.py "./stock/Ultimate 2C_v1.09_75.dat" ./patches/mine.dat
PASS: exact rebuild, length, diff scope, both CRCs, cold BSS initialization
PASS: startup-held pair, 3-sample hold, long hold, full-release rearm, ...
PASS: single keys, other combos, normal LED states, 5,000 randomized ticks
```

The image `patch-2c-led.py` builds in memory is byte-identical to this one
(SHA-256 `4ae90a7dd27b5e4e…`).

If a bad flash ever leaves the pad unresponsive: hold **LB + RB** while plugging
it in, which brings up its bootloader (`2DC8:3208`), and use the official web
updater at <https://web.8bitdo.com>.


`build_patch.py` refuses any base image other than stock v1.09 (it asserts the
version, link base, length and PID), so a silent vendor update cannot quietly
produce a different patch. `verify_patch.py` interprets the emitted TC32 rather
than diffing bytes: it runs the real gate logic, the real cave and the real
cold-start BSS loop against thousands of tick sequences.

## How it works, in one paragraph

The firmware's LED tick at `0xD4D0` calls one driver (`0xD36C`) once per light -
ring `0xD4F0`, mapping `0xD4FC`, battery `0xD50A`. The patch redirects the ring's
call to 40 bytes of injected TC32 that checks the raw button word at
`0x0084344C`, and when L4+R4 are both held (`0x06000000`) it flips a flag byte
and passes state `0` instead of the firmware's state. Full detail, including the
button-trace evidence chain and the two checksums, is in
[`docs/patch-spec.md`](docs/patch-spec.md) and

## Licence and provenance

The scripts and documentation here are MIT. The `.dat` images are byte-patches
of 8BitDo's own firmware and therefore embed their code. They are shared for
personal, interoperability use only; 8BitDo's firmware remains 8BitDo's. No
affiliation with 8BitDo. Flash at your own risk: a failed flash is recoverable
via the LB+RB bootloader, but you are still writing to your own hardware.
