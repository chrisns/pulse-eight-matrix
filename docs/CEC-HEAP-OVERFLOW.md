# Raw CEC parser heap overflow: finding, primitive, and exploitability limit

Review dates: 29–30 October 2026. Target: FF88, MatrixOS F8 3.10.5-1.
Firmware bases: application `0x1B000000`, secondary MCU `0x1A000000`, bootloader `0x1A000000` (BOOT.BIN).

## Result

The raw CEC transmit parser contains an unauthenticated, fully attacker-controlled heap
buffer overflow. It is a genuine memory-corruption primitive, demonstrated by executing the
vendor's own parser offline and confirmed live on the device.

It is **not** remotely weaponisable into the controlled IR-blast call this investigation
wanted, and the reason is specific and verified rather than assumed: weaponisation needs the
runtime address of the overflowed buffer, that address is only obtainable from a captured
crash report, and the one crash class that produces a captured report cannot be induced on
demand. Every candidate route was tested to destruction; the negative results are recorded
here so the work is not repeated.

```text
POST /cec/tx/{Input|Output}/{bay}   {"Command":"<hex bytes>"}   (no auth)
        │
        └─> raw hex parser 0x1B0508EC
                 ├─ malloc(0x1C)  -> 28-byte heap buffer (buf)
                 ├─ per byte: strb r0,[r6,r3]   @0x1B050984   r3 = [buf+0x18], never bounded
                 └─> controlled relative write:  buf + chosen offset  <- arbitrary byte run
```

## The defect

| Item | Detail |
| --- | --- |
| Entry | `0x1B0508EC`, reached from the CEC tx handler `0x1B049EB4` via the API table at `0x1B06C9C8` |
| Allocation | `bl 0x1B020B5C` with `r1 = 0x1C` (28 bytes). `0x1B020B5C` rounds the size up and tail-calls the real allocator `0x1B054B58` |
| Store | `0x1B050984  strb r0,[r6,r3]`, `r6` = buf, `r0` = the parsed byte |
| Counter | `r3` is loaded from `[r6,#0x18]` and written back incremented at `0x1B050982`. It is **never compared against the allocation size** |
| Usable region | `buf[0x00..0x17]` (24 bytes). The 32-bit count word occupies `buf[0x18..0x1B]` |
| Input | Colon-separated hex bytes; a pipe-separated form recurses (see below). Every byte value and the overall length are attacker-chosen |

This integration limits frames to the CEC maximum of 16 bytes, and the firmware's own CEC
append is bounded. Neither constrains this parser: the bound exists only in well-behaved
clients, so a direct request is unrestricted.

## The primitive

Because the counter lives *inside* the buffer it indexes, the overflow is not merely linear:

1. The first 24 parsed bytes fill `buf[0x00..0x17]`; the count reaches `0x18`.
2. Further bytes are written onto the count word itself.
3. Writing a non-zero byte into `buf[0x19]` (the count's second octet) makes the count jump
   to roughly `value << 8`, and writes then continue **linearly from `buf + that offset`**.

So the bytes that land on `buf[0x18..0x1B]` select the write index, and everything after
them is streamed from there. That yields a **controlled relative write-what-where** with the
heap buffer as the base, of arbitrary length and arbitrary content.

Verified steering recipes (all confirmed by executing the real parser in Unicorn):

| Goal | `Command` byte sequence |
| --- | --- |
| Clean linear run starting at `buf+0x1C` | 24 filler bytes, then `19 00 00 00`, then the payload |
| Single far write at `buf + (H<<24) + 0x1C` | 24 filler bytes, then `19 00 00 H`, then one byte |
| Large count, no further write (drives the read loop) | 24 filler bytes, then `19 00 00 H`, string ends |

Example observed in emulation: 24 bytes then `19 FF 42…` produced a count of `0xFF38` and
out-of-bounds stores at `buf+0xFF1A` onwards. Example far write: `19 00 00 10` then one byte
stored at `buf+0x1000001C`.

## Where it could lead, and the only target that matters

The IR transmitter on the secondary MCU is reachable **only** by getting command byte `0x07`
into an internal-bus request. That was established exhaustively:

- `0x1B02C54C` (the sub-MCU command engine) has exactly **18 `bl` callers and zero pointer
  references**: `refs 0x2C54C` and `refs 0x2C54D` are both empty, so there is no function
  pointer table, no computed dispatch, no indirect route.
- Every caller hardcodes its command byte (`movs r3,#imm; strb r3,[req]`): `0x08`/`0x09` IR
  receive and cleanup, `0x01` version, `0x10`/`0xFA`/`0xFC`/`0xFE` management, `0xFB`/`0xFD`/`0xEE`
  reset paths, `0x20`–`0x27` CEC. None is variable, table-indexed, or request-derived.
- The low-level bus primitives (`0x1B02C3D0` send, `0x1B02C420` transfer, `0x1B02C454` read,
  `0x1B02C4E4` error string) are only called from inside the bus layer itself.
- On the secondary image, the command-`0x07` handler `0x1A001530` has **no `bl` caller**. Its
  only reference is the command-number registry binding at `0x1A00176C`, and the waveform
  driver `0x1A000988` is called only from `0x1A00157C` inside that handler.

So no diagnostic, debug, test or raw-I²C primitive exists anywhere in the firmware that will
carry a chosen command byte to the secondary MCU. The `test` (`0x1B009C24`) and `debug`
(`0x1B049F48`) API entries were checked: `debug` only gets/sets a CEC logging level. The
`i2clib_*` helpers are HAL-internal (mux/DDC/EDID) with no network-reachable caller.

The final-stage data is nevertheless ready and verified. Internal command `0x07` with this
30-byte body drives the genuine waveform driver in emulation, frequency argument 109,
computed PWM carrier 38,028 Hz, six mark/space pairs, protocol status `0x0A`, return 0:

```text
cmd  payload (u16 divisor, u16 pairs, u8 demodulated, u8 reserved, then timing words)
07   6d 00 06 00 00 00  58 01 ac 00 15 00 15 00 15 00 40 00 15 00 15 00 15 00 40 00 15 00 15 00
```

The engine request structure is 0x1C bytes: `[0]` command, `[0x10]` payload pointer,
`[0x14]` payload length (u16), `[0x18]` next-segment pointer. The bus context pointer is
`*(u32 *)0x10003698`.

## Allocator model

Validated by driving the real allocator in Unicorn.

| Item | Detail |
| --- | --- |
| Entry points | `0x1B020B5C` → malloc `0x1B054B58` (worker `0x1B0553E0`); `0x1B020B70` → free `0x1B054D7C` (worker `0x1B055348`) |
| Behaviour | calloc-style (zeroed); returns the **tail** of its source free chunk |
| Chunk/free node | free node `{size @ +0, next @ +4}`; 4-byte size header at `ptr-4`; list head `0x1000466C`; allocator state `0x10000FA8` |
| Policy | address-ordered **first-fit from the lowest address** |
| 0x1C allocations | march **downward** in `0x20` steps |
| Determinism | alloc → free → alloc returns the **identical** address (LIFO reuse). `buf` is therefore a fixed address for a given heap state, and one leaked value would be reusable |
| Neighbour layout | a freed allocation immediately above `buf` presents as a free node at `{size @ buf+0x1C, next @ buf+0x20}` |
| Arena | external DRAM; the captured dump reports `heap: 0x2809B790-0x280C9780`. Total external DRAM is **8 MB** (`0x28000000`–`0x28800000`): the bootloader's size getter `0x1A010FCA` returns `0x800000` |

## Why it is not remotely weaponisable

Exploitation needs `buf`'s live address, to stage a fake structure at a known location and
to aim the write at a pointer. `buf` is deterministic but could not be obtained. Each route
below was tested, not assumed:

| Route | Outcome |
| --- | --- |
| Unmapped **write** (far single write, or stack-exhaustion `push`) | Buffered writes abort **imprecisely** → watchdog reset, **no crash report** |
| Transmit read-loop LOAD `0x1B04739E` walking to the top of DRAM | A precise load, but `buf` sits ~7.4 MB below the 8 MB DRAM ceiling; the walk takes long enough that the **watchdog wins** (observed ~18 s hang, no dump) |
| Parser pipe recursion `0x1B050958 bl 0x1B0508EC` (recursion continues only while the transmit boundary returns 1) | 200 `\|`-separated frames → **171 levels**, stack exhausted at `0x10005FF0` (8 KB stack `0x10006000`–`0x10008000`, ~48 bytes/frame), but the fault is the prologue **PUSH**, i.e. imprecise |
| malloc free-list poison (`size` small to skip, `next` = sentinel) | Mechanism confirmed, with a precise LOAD fault at `0x1B055436 ldr r3,[r4]`, but **unreachable**: address-ordered first-fit always satisfies the small CEC allocation from the low wilderness remainder before reaching a poisoned chunk above `buf`. Measured: after freeing a middle buffer the next allocation did **not** reuse that hole |
| `free(buf)` coalescing | With an **allocated** neighbour it touches none of the overflowed bytes and returns cleanly (hence the live "Transmitted" response). With a **freed** neighbour it reads `{size,next}` at `0x1B0553AE`/`0x1B0553B0` but only **propagates** the pointer (`0x1B0553B6 str r2,[r3,#4]`), it never dereferences it. **No fault** |
| Information-leak endpoint | None. `debug` is a logging level only; no memory/dump/peek entry exists; the error log discloses no pointers; the config/backup serialisers expose no addresses |
| Computing `buf` offline | Requires the device's live boot-heap state. Full-system boot emulation is infeasible (peripherals, PHY, network stack) |
| Pool-allocated CEC objects (original hijack plan) | **Invalid**: the CEC message object comes from a frame pool (`0x1B01A6DC`, pool pointer `[0x10004F20]`) and initialises `[msg+0xc]`. It is never heap-adjacent to `buf`, and the vtable call at `0x1B047404` dereferences a persistent driver object the overflow cannot reach |

The only artifact that yields `buf` is a **captured crash report**. `GET /System/CrashDump`
returns `crashdump.tar` (handler `0x1B017E10`); the Oct-29 capture contained `crash.txt`
(1679 bytes) and `stack.bin` (8192 bytes). The same `crash.txt` text also leaves the device in
the cloud monitoring upload's `log` field, which is how it can be collected without the device
having to serve it. The crash subsystem only populates on a **precise** fault, and the save
region is cleared by every reboot.

## The captured crash is organic, not forcible

One captured dump exists, from 29/10/2026 15:55:16 at uptime 8194 s (≈2 h 16 m):

```text
bus fault: BFARVALID, PRECISERR
R0:10007FE0  R6:FFFFFFFF  R12:00000019  SP:10007E20  LR:1B01D3BD  PC:1B01CFAE
memory: stack 10006000-10008000    heap 2809B790-280C9780
```

`0x1B01CFAE` is the entry of a network-connection teardown routine: `ldrb r3,[r0,#0x20]`,
then it releases `[r0+0xc]` through the lwIP helpers `0x1B0253xx`/`0x1B025874`. At the fault
`r0 = 0x10007FE0` is a **stack** address (a garbage connection pointer), and `R6 = 0xFFFFFFFF`
is an error marker. A failed connection allocation's error value was used as a connection
pointer during teardown.

Its system log gives the mechanism:

```text
ldetmgr: DNS ping 9.9.9.9 / netmgr: DNS ping 8.8.8.8
(W) emac: RX oom  (repeated)
(W) mx_remote: pbuf alloc failed
(W) ping: oom
(W) tcpclient: (0x280bb298) failed to transmit 4 bytes
(W) cloud: failed to transmit response: unknown error
```

**Root cause:** the gateway isolation in force at the time was **DROP**ping (silently
discarding) the matrix's outbound traffic. Its own DNS and cloud connections therefore hung
waiting for replies that never arrived, holding packet buffers until the pool was exhausted,
after which a teardown ran with a failed-allocation pointer. A **REJECT** rule fails those
connections immediately and avoids both this crash and the firmware hangs seen repeatedly
during testing.

**It could not be reproduced deliberately.** Across roughly 25 induced crashes, slowloris
holds, RST connection churn at 24/48/56/120/160 workers, connection-pool exhaustion with 90
held connections, 16 MB over-count read loops, 200-segment pipe recursion, a far write into
the peripheral window, cycled soaks up to 36 minutes (19 bursts, ~28 crashes), and a
cloud-stalling relay with the cloud link live, **not one produced a captured dump**. Every
forced crash resolved as a watchdog reset or a hang. The tension is structural: draining the
buffer pool fast enough to exhaust it hangs the device before a teardown can run, while
staying gentle enough to keep it running reproduces the original ~2 hour timescale with no
guarantee the fault lands precisely rather than as a hang.

## What is and is not established

Established:

- An unauthenticated, fully controlled heap overflow exists in the raw CEC parser, with the
  exact allocation, store instruction, missing bound, and steering behaviour recorded above.
- The primitive is a controlled relative write, demonstrated by executing the vendor parser.
- Internal command `0x07` is the **only** path to the IR transmitter, and no diagnostic or
  raw-I²C primitive exists to carry it.
- The command-`0x07` request layout and a working waveform payload, verified against the real
  secondary-MCU driver in emulation.
- The allocator's layout, policy and determinism.
- Live on the device: the overflow is reachable and corrupts memory; crafted requests reliably
  fault or reset the unit; the over-read is absorbed because the CEC append `0x1B04728C` is
  hard-bounded at 21 bytes (`cmp r2,#0x15; strbls`).
- UDP 8811 is a **broadcast** to the subnet broadcast address (confirmed by capturing the
  datagram destination address), not a unicast push.

Not established:

- Any control-flow hijack or controlled call of the command engine.
- Physical IR emission from this unit, or which jack/LED the secondary driver drives.
- `buf`'s runtime address, by leak or by computation.
- A reproducible route to a captured crash report.

## Notes

Nothing in this review wrote firmware, flash, a waveform, a mapping or a selected control
method to the device. The offline emulation scripts, disassemblies and the captured crash
report are not published. The one thing that would move this from memory corruption towards
control-flow hijack is a crash report captured while the CEC path is live, which is one reason
this integration archives the crash reports the matrix uploads.

## What to do about it

Treat any network that can reach the matrix as able to crash it, and do not let it near the
internet. See the [advisory in the README](../README.md) and
[network protection](NETWORK-SECURITY.md).
