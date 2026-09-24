# CarPlay Dongle Compatibility Guide

**CatPlay Version:** 0.4.0 (released 2026-09-09)
**Last Updated:** 2026-09-13
**Sources:** catplay-firmware repo (code, issues, docs), ludwig-v/wireless-carplay-dongle-reverse-engineering repo (issues)

---

## Summary

| SoC | Arch | Radio (confirmed) | Kernel | CatPlay Status |
|---|---|---|---|---|
| **Ingenic X1600EN** | MIPS32 | AIC8800-family (SDIO) | 6.12.x | ✅ Released (v0.1.0+) |
| **Allwinner V821B** | RISC-V32 | RTL8733BS | 5.4.x | ✅ Released (v0.4.0) |
| **Allwinner V821B** | RISC-V32 | AIC8800D80 | 5.4.x | ❌ No build exists |
| **iMX6ULL (C2A)** | ARMv7 | RTL8822BS/CS, BCM4335/4358 | 4.14.x | ⚠️ Legacy, low priority |
| **Anyka AK3918AV130** | ARMv5 | AIC8800D80 (SDIO) | 4.4.x vendor / 7.2 port | 🚧 In development, unreleased |
| **NXP iMX6 (CPC200-CCPA)** | ARMv7 | IW416 (latest gen) | Unknown | ❌ Not supported |

---

## Detailed Profiles

### ✅ Carlinkit Mini Ultra / AX1800M (Ingenic X1600EN)

- **SoC:** Ingenic X1600EN, MIPS32
- **Radio:** AIC8800-family, SDIO. Boot code references `AIC8800D80` firmware paths; one reported unit's chip marking reads AIC8800D40 (issue #8) — unresolved discrepancy.
- **RAM/Flash:** 128 MB RAM / 16 MB NOR (AX1800M revision). Earlier AX1800 revision (128 MB NAND, A/B OTA) is scarce.
- **Kernel:** Linux 6.12.x
- **CatPlay Status:** ✅ Released since v0.1.0; primary reference platform
- **Flashing:** `tools/exploit.py` (cross-platform, Windows/macOS/Linux) — RCE in VehiConn firmware, reboots into Ingenic USB recovery (`a108:eaef`), with an interactive Wi-Fi/IP-discovery assistant
- **Exploit status:** Working as of firmware `system_version 20260612170300CA` (2026-09-11, confirmed by three independent testers)
- **Open issue:** No CarPlay activation on some Stellantis NAC head units (2018–2024) — fake-iPhone not detected, no BT pairing. Suspected USB role-switch timing issue, unresolved (issue #1)

---

### ✅ Wooboobox B12 / Allwinner V821B + RTL8733BS

- **SoC:** Allwinner V821B (`sun300iw1p1`), single-core RISC-V32
- **Radio:** RTL8733BS (SDIO ID `0xb733`, confirmed in [`radio.rs`](../meta-carlinkit/recipes-bsp/carlinkit-otalib/src/radio.rs))
- **RAM/Flash:** 64 MB RAM / 16 MB SPI-NOR
- **Kernel:** Linux 5.4.220
- **CatPlay Status:** ✅ Officially released in v0.4.0 ("Released V821+RTL8733BS port")
- **Vendor firmware ID:** `ly_project=ly5166`, "cpbox-abroad" family
- **Flashing:** `tools/wizard.py --preset wooboobox`
- **Note:** Carlinkit sells the identical PCB in a different enclosure under names like "Mini Ultra" — see bait-and-switch note below.

---

### ❌ Allwinner V821B + AIC8800D80 — no build exists

`docs/V821.md`:

> There is no V821B+AIC8800D80 build available because of no access to reference device to confirm power sequence and SDIO sample tuning.

Known as a hardware variant (photographed inside a "Carlinkit Mini Ultra 3"), but not flashable.

A separate unit sold as "Carlinkit Mini Ultra" (`ly_project=ly5190`) was found with a radio identifying over Bluetooth as **RTL8723FS** — not RTL8733BS, not AIC8800D80, and not present in the `Radio` enum. Support status unconfirmed; may be a third, undocumented variant (issue #4).

---

### ⚠️ Bait-and-switch note

Carlinkit has shipped different internal hardware under the same product name/packaging more than once (e.g. AIC8800D80 silently replaced by RTL8733BS in "Mini Ultra"-branded units; a Wooboobox-firmware purchase arriving as AK3918/cpbox-abroad instead). Product name is not a reliable predictor of compatibility — check `getversion.cgi` / `platform:` after purchase.

---

### 🚧 In development: Carlinkit Mini Ultra (AK3918 variant)

- **SoC:** Anyka AK3918AV130, ARMv5 (ARM926EJ-S), single-core, ~960 MHz
- **Radio:** AIC8800D80, SDIO (issue #2)
- **RAM/Flash:** 64 MB RAM / 16 MB SPI-NOR (`zb25vq128`)
- **Vendor Kernel:** Linux 4.4.302-cip94 (Buildroot 2018.02.7)
- **Vendor firmware ID:** `platform:AK3918AN`, `product_Model:v851se-yunlian-cp`
- **CatPlay Status:** 🚧 Active port, not part of v0.4.0. Last status (2026-08-27): core subsystems ported and functional, not yet car-tested. No flashing tool published.
- **Root access (current):** command-injection RCE in `submition.cgi`, not a bootloader exploit. `ultra_exploit` (Ingenic reboot trick) does not apply — no `/proc/jz/reset/` on this SoC.

---

### ⚠️ Legacy: Carlinkit 3.0 / 4.0 / 5.0 (iMX6ULL family)

- **SoC:** NXP i.MX6 UltraLite, ARMv7 Cortex-A7
- **Radio:** RTL8822BS, RTL8822CS, BCM4335, BCM4358 (in `Radio` enum / `boot_radio.rs`). Other units in the wild reportedly use Fn-Link L287B-SR or LGX8354S, not present in CatPlay's radio detection.
- **RAM/Flash:** 128 MB RAM / 16 MB NOR
- **CatPlay Status:** ⚠️ Bitbake machine configs exist (`imx6ul-c2a-bcm4335`, `-bcm4358`, `-rtl8822cs`); repo README describes this port as "being sunset as a low priority port." No documented exploit against current vendor firmware for installing CatPlay.
- **Recommendation:** Treat as unsupported in practice.

---

### ❌ CPC200-CCPA

- **SoC:** NXP i.MX6 (exact variant unidentified) — **not** Allwinner V821. `docs/V821.md` currently lists it under the V821 family; issue #8 (2026-09-13) disputes this and identifies it as NXP-based, manufactured by DongGuan HeWei Communication Technologies Co. Ltd. `docs/V821.md` has not yet been updated to reflect this.
- **Radio:** IW416 (NXP) in latest generation
- **CatPlay Status:** ❌ Not supported — no bitbake recipes, no known exploit or recovery path

---

### ❌ Insufficient information

- **Carlinkit 1.0 / 2.0 (iMX6ULL):** documented only in ludwig-v repo (firmware dumps, Dropbear rooting); no CatPlay involvement
- **AutoKit:** referenced in ludwig-v issue titles (#21, #120) as a firmware/branding variant; hardware undocumented

---

## Flashing Methods

| Dongle | Method | Confidence |
|---|---|---|
| Carlinkit Mini Ultra (X1600EN) | `tools/exploit.py` | High |
| Wooboobox B12 (V821B/RTL8733BS) | `tools/wizard.py --preset wooboobox` | High |
| V821B/AIC8800D80 | — | N/A, no build |
| AK3918 variant | — | N/A, unreleased |
| iMX6ULL (3.0/4.0/5.0) | — | No known method |
| CPC200-CCPA | — | No known method |

---

## Contributing Device Reports

Include when reporting a new/unclear device:
- Full `getversion.cgi` output, including `platform:`
- `/proc/cpuinfo` and `dmesg` if shell access exists
- Exact `ly_project` ID (cpbox-abroad firmware)
- Photos of chip markings — PCB silkscreens have been shown to mismatch actual silicon (issue #8: chip marked "3959"/CP2.0C identified as CP3.0)
- Purchase source/date — hardware has changed mid-run under the same product name

---

## References

- [catplay-labs/catplay-firmware](https://github.com/catplay-labs/catplay-firmware) — issues #1, #2, #4, #5, #8
- [ludwig-v/wireless-carplay-dongle-reverse-engineering](https://github.com/ludwig-v/wireless-carplay-dongle-reverse-engineering)
- [`docs/V821.md`](V821.md), [`docs/Carlinkit Mini Ultra.md`](Carlinkit%20Mini%20Ultra.md)
- [`meta-carlinkit/recipes-bsp/carlinkit-otalib/src/radio.rs`](../meta-carlinkit/recipes-bsp/carlinkit-otalib/src/radio.rs), [`.../src/boot/boot_platform.rs`](../meta-carlinkit/recipes-bsp/carlinkit-otalib/src/boot/boot_platform.rs)
- [v0.4.0 release notes](https://github.com/catplay-labs/catplay/releases/tag/v0.4.0)
