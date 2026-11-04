# Ultimate 2C Wireless — experimental L4 + R4 power-ring toggle

## Intended behavior

1. Hold L4 + R4 together until the power ring turns off.
2. Release both buttons completely.
3. Hold the same pair again to restore the ring's normal on/blinking behavior.

The setting lives in RAM and defaults to normal LED behavior when the normal cold-start BSS initialization runs. It is not saved to flash. Retention sleep/wake may keep it.

The battery and mapping LED update calls are unchanged. Controller inputs are not suppressed: L4/R4 still perform their normal mapped actions, so first test outside a game. While disabled, the power ring also cannot show pairing/disconnection blinking.

## Files

- `Ultimate_2C_v1.09_L4_R4_LED_Toggle.dat`: patched firmware.
- `stock-Ultimate_2C_v1.09_75.dat`: exact unchanged input for rollback.
- `Ultimate_2C_v1.09_L4_R4_LED_Toggle.json`: full changed-range manifest and hashes.
- `build_patch.py`: reproducible builder; refuses any different source SHA-256.
- `led_toggle.S`, `hook.S`: TC32 source.
- `led_toggle.bin`, `hook.bin`: assembled code used by the builder.
- `verify_patch.py`: limited TC32 instruction interpreter and behavioral tests.
- `verification.txt`: test results.
- `disassembly.txt`: patched call and injected code disassembly.


## Firmware analysis

- Scanner `0xC7A4` reads matrix row 1, input bits 3/4, and sets internal bits 25/26 at `0xC7D6..0xC7E8`.
- At `0xC8F4..0xC8F6`, the scanner stores that raw word at `0x0084344C` **before** calling the mapping routine `0xC3A8`. The separately mapped word is at `0x0084342C`.
- The mapping code has two special cases for bits 25/26, with default self-masks and clearing of the original bit when remapped.
- The BLE report builder at `0xB9C6..0xB9DC` maps bits 25/26 to report byte 7 bits 5/2. The [same-model patch project](https://github.com/Alan-Gomes/8bitdo-macos/blob/main/docs/firmware-patch.md) identifies those report bits as R4/L4.

This supports the **inference** that `(raw_word & 0x06000000) == 0x06000000` identifies the physical L4+R4 pair independently of mappings. Physical button assignment still needs confirmation on your controller. The mask `0x0C09` mentioned in the supplied notes is not used by this patch.

## Implementation and integrity

All addresses below are firmware addresses; original `.dat` offset = address - `0x6000` + `0x1C`.

| Change | Location | Purpose |
|---|---|---|
| Redirect existing 4-byte call | `0xD4F0` | Call injected gate instead of calling `0xD36C` directly |
| Add 88-byte routine/literals | `0x18274`, file `0x12290` | Read raw keys, maintain latch, optionally force ring state to 0, call original driver |
| Extend BSS clear boundary | literal at `0x617C` | Reserve and initialize 4 bytes at `0x00843CF0..0x00843CF3` |
| Recompute original internal CRC | file `0x1228C` | Preserve CRC over the original length-defined region |
| Recompute whole-payload suffix | file `0x12418` | Preserve `zlib.crc32(payload) == 0xFFFFFFFF`, including the injected trailer code |

The memory reservation moves the original BSS end from `0x00843CF0` to `0x00843CF4`. It leaves the stack-top literal at `0x00844000`. Static references end at the original BSS boundary, but runtime stack high-water and retention behavior are not measured.

The original wrapper and internal length are unchanged, including the unresolved wrapper field at offset `0x10`. Satisfying the two observed CRC checks is not proof of acceptance by every updater or bootloader.

The hook preserves r4-r6 and its return address. It forwards the existing pin `0x180` and counter pointer unchanged to the existing driver. When disabled it passes state 0; otherwise it passes the original state. It does not write raw/mapped input words or controller mappings.

The injected code uses zero padding beyond the original internal length. The same-model patch project uses this general technique, but execution of this particular cave on v1.09 has not been demonstrated here.

## Rebuild and verification

Python's standard library is sufficient to reproduce the supplied firmware:

```bash
python3 build_patch.py stock-Ultimate_2C_v1.09_75.dat reproduced.dat
python3 verify_patch.py stock-Ultimate_2C_v1.09_75.dat reproduced.dat
```

To reassemble, use the Linux TC32 toolchain from [flyskywhy/tc32](https://github.com/flyskywhy/tc32), revision `8ec375bb95ef63b6d0a4b18008913c03e7928710` (GNU tools report `2.01build`):

```bash
tc32-elf-as -o led_toggle.o led_toggle.S
tc32-elf-ld -Ttext=0x18274 --entry=led_gate --defsym=original_ring_driver=0xd36c -o led_toggle.elf led_toggle.o
tc32-elf-objcopy -O binary --only-section=.text led_toggle.elf led_toggle.bin
tc32-elf-as -o hook.o hook.S
tc32-elf-ld -Ttext=0xd4f0 --entry=hook --defsym=led_gate=0x18274 -o hook.elf hook.o
tc32-elf-objcopy -O binary --only-section=.text hook.elf hook.bin
```

The instruction harness executes the actual generated opcodes, including the original modified BSS-clear loop and both call branches. The LED driver itself is stubbed to record its arguments and clobber caller-saved registers. Tests cover register/stack restoration, single and unrelated keys, a held pair at startup, interrupted holds, no repeats, full-release rearming, original states 0-3, and 5,000 deterministic randomized ticks. This is not full controller emulation or physical validation.

Stock SHA-256: `87349e603bb92d6ae9ff6defb6f32b10de2f2d3252fe4e442fa7993f982f2a5a`

Patched SHA-256: `4ae90a7dd27b5e4e85c9d2677d750e0af197b42a4c5e9b9f104e49fc2d10a250`
