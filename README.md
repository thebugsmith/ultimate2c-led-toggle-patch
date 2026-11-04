# Ultimate 2C - toggle the LED ring with L4 + R4

Hold L4 + R4 to toggle the LED ring on/off. 

Tested on and only work with **Ultimate 2C Wireless**. The patch is a byte-level modification of the official firmware  v1.09, and is not compatible with other 2C variants. 


## Build it first

```console
$ python tools/fetch-stock.py                        # pulls the official image from 8BitDo
$ python build/build_patch.py "./stock/Ultimate 2C_v1.09_75.dat" ./patches/mine.dat
$ python build/verify_patch.py "./stock/Ultimate 2C_v1.09_75.dat" ./patches/mine.dat
PASS: exact rebuild, length, diff scope, both CRCs, cold BSS initialization
PASS: startup-held pair, 3-sample hold, long hold, full-release rearm, ...
PASS: single keys, other combos, normal LED states, 5,000 randomized ticks
```

## Install the patch

Connect the controller via USB cable, disconnect the adapter.

For linux only: add a udev rule to allow non-root access to the device.
```
{ cat <<EOF
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", MODE="0666", GROUP="users", TAG+="uaccess"
EOF
} | sudo tee /etc/udev/rules.d/8-bitdo-controller-beta1.rules && sudo udevadm control --reload-rules && sudo udevadm trigger
```
Now install it.
```console
# 1. check if the pad is connected
$ python tools/8bitdo-fw.py info        

# 2. flash the included image
$ python tools/8bitdo-fw.py update \
      patches/Ultimate_2C_v1.09_L4_R4_LED_Toggle.dat --force -y

# 3. prove it landed
$ python tools/8bitdo-fw.py verify \
      patches/Ultimate_2C_v1.09_L4_R4_LED_Toggle.dat
```

### Rollback

```console
$ ./tools/fetch-stock.py                        # official v1.09 -> stock/
$ python tools/8bitdo-fw.py update "stock/Ultimate 2C_v1.09_75.dat" --force -y
```

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
