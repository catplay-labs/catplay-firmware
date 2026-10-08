# Carlinkit Mini Ultra (X1600)

### Introduction

This page covers the AX1800M revision of the Wooboobox M11, also sold as Carlinkit Mini Ultra. It pairs an Ingenic X1600EN with an AIC8800 radio and has 16 MiB of NOR flash.

The older AX1800 revision has NAND flash and is not supported by this flashing package. Carlinkit has also sold devices under the Mini Ultra name with different hardware. Check the hardware and original firmware before flashing; the product name alone is not enough.

For the original firmware path below, the device should broadcast a `VehiConn_*` hotspot. Some newer units use different firmware and are not supported by that path.

## Flashing wizard

> [!NOTE]
> Keep the dongle connected to the computer's USB port throughout flashing. The wizard saves a backup of the current flash contents in the current directory before writing the new image.

### Install dependencies

```console
$ python3 -m pip install pyusb paramiko cryptography
```

On Debian or Ubuntu, install the USB library as well:

```console
$ sudo apt install libusb-1.0-0
```

On Windows, install the Ingenic USB boot driver before uploading recovery:

1. Download and extract Creality's [Ingenic driver package](https://github.com/CrealityOfficial/K1_Series_Annex/releases/download/V1.0.0/cloner-2.5.18-windows_alpha.zip). The driver is in the `cloner-win32-driver` directory; you do not need to run the included flashing tool.
2. Connect the dongle with a USB cable that carries data and put it in X1600 USB boot mode. If the first wizard run brought it into that mode but stopped because the driver was missing, leave it connected.
3. In Windows Device Manager, find `Ingenic USB BOOT DEVICE` (it may have a warning icon). Right-click it, choose **Update driver** → **Browse my computer for drivers**, select the extracted `cloner-win32-driver` directory, and complete the installation.
4. If the dongle is still in USB boot mode, resume with `python wizard.py --already-fel` from the bundle's `tools` directory.

If the device does not appear in Device Manager, check that the USB cable supports data transfer.

### Ensure the right directory

Unpack the `clk-mini-ultra-nor` firmware bundle and run the wizard from its `tools` directory. The default firmware and recovery paths are relative to that directory.

```console
$ cd tools/
```

#### Flash a device already running CatPlay (refresh)

```console
$ python3 wizard.py --refresh
```

This enters USB boot mode through a USB vendor request; no preset or connection to the original firmware hotspot is needed.

#### Flash a device already in USB boot mode

```console
$ python3 wizard.py --already-fel
```

Use this when the device is already visible to the host in X1600 USB boot mode. The option is named `--already-fel` to match the V821 wizard.

#### Flash a device already running CatPlay recovery

```console
$ python3 wizard.py --already-recov
```

This skips entry into USB boot mode and recovery startup, then flashes the image over the recovery USB network.

#### Flash over original firmware

Connect the computer to the device's `VehiConn_*` hotspot (password `88888888` or `12345678`) while leaving the dongle connected over USB. Then choose the preset matching the original device:

| Preset | Original firmware IP |
| --- | --- |
| `wooboobox` | `192.168.1.101` |
| `carlinkit` | `192.168.50.100` |

```console
$ python3 wizard.py --preset wooboobox
```

The wizard enters USB boot mode, starts CatPlay recovery and flashes `../clk-mini-ultra-nor.c2aflash`. Recovery is reached at `192.168.51.2` over USB. On Unix, the wizard uses `sudo` for the USB stages when needed; on Windows it runs those stages without `sudo`.

You can select a different image with `--fw PATH`. Use `--no-flash` to stop after starting recovery.

### After flashing

The device reboots into CatPlay. Its Wi-Fi networks are `C2A_AP` and `C2A_P2P`; the password is `lovec@ts`. SSH is available at `root@192.168.50.2` over the device Wi-Fi. You will need to pair your phone again.

## Capturing logs

If CatPlay does not work with your car, reproduce the issue and collect `/var/log/catplay.log` and `dmesg` from the device. You can save the latter with:

```console
$ dmesg > /var/log/kernel.log
```

Include both logs and the car model when reporting the problem.
