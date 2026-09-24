# Yocto layer for CatPlay (Carplay2Air)
  
Yocto version: wrynose
Dependencies:
- meta-clang@wrynose
- meta-freescale@wrynose
- meta-openembedded@wrynose
- meta-sunxi@wrynose
- openembedded-core@wrynose
- bitbake@yocto-6.0.2

The repository also contains `meta-rust` and `meta-lts-mixins-rust` as git
submodules, but neither is wired into `bblayers.conf`. They are not part of the
active build. As of Yocto scarthgap (and wrynose), Rust support is integrated
directly into `openembedded-core`, making a separate `meta-rust` layer
unnecessary.

## Build

The repository contains a reproducible build entry point for the Carlinkit Mini
Ultra NOR image. It initializes the Yocto submodules, clones CatPlay into
`.sources/catplay`, creates the source bundle expected by `meta-catplay`, and
starts BitBake:

```sh
./build.sh
```

The default machine is `clk-mini-ultra-nor`, the distro is `c2a-musl`, and the
target is `c2a-system-image`. The generated artifacts are placed below
`build/tmp/deploy/images/clk-mini-ultra-nor/`. The machine-specific `.c2aflash`
image can be selected explicitly with:

```sh
./build.sh c2a-system-bundle
```

### Machines

There are two related Carlinkit Mini Ultra machines, both built from the same
shared `shared.conf`/`defconfig`:

| Machine | Purpose |
|---|---|
| `clk-mini-ultra-nor` | Normal firmware image (default) |
| `clk-mini-ultra-nor-recov` | Recovery image + host-side flashing tools |

Only `clk-mini-ultra-nor-recov` declares the extra dependency that deploys the
host-side Python tools (`exploit.py`, `flash.py`, `recov.py`, …) into the
bundle. To build the recovery image including all tools:

```sh
MACHINE=clk-mini-ultra-nor-recov ./build.sh c2a-system-bundle
```

Building `c2a-system-bundle` with the default machine alone will **not** pick
up changes to files under
`meta-carlinkit-mini/recipes-bsp/carlinkit-mini-flasher/files/`.

### Environment variables

Set any of these in the environment to override the defaults:

| Variable | Default | Description |
|---|---|---|
| `MACHINE` | `clk-mini-ultra-nor` | Yocto machine |
| `DISTRO` | `c2a-musl` | Yocto distro |
| `TARGET` | `c2a-system-image` | Default BitBake target |
| `BUILD_DIR` | `build/` | Build directory |
| `CATPLAY_DIR` | `.sources/catplay` | Local CatPlay checkout |
| `CATPLAY_REPO` | upstream GitHub URL | CatPlay repository URL |
| `MIN_BUILD_FREE_GIB` | `8` | Minimum free GiB required in `BUILD_DIR` |
| `BUILD_RETRY_ATTEMPTS` | `3` | BitBake retry attempts on recoverable errors |

### Disk space

Yocto requires substantial temporary storage. Keep at least 8 GiB free on the
volume containing `BUILD_DIR`; the default is `build/`. To retain the current
build state while moving it to a larger volume, copy it first and only remove
the original after a successful build:

```sh
rsync -aHAX --info=progress2 build/ /tmp/catplay-build/
BUILD_DIR=/tmp/catplay-build ./build.sh
```

## Tools

The `tools/` directory contains host-side Python utilities for flashing and
recovering devices. They are also deployed into the `c2a-system-bundle` when
building with `MACHINE=clk-mini-ultra-nor-recov` (see above).

| Script | Purpose |
|---|---|
| `exploit.py` | Main entry point for flashing via USB exploit |
| `flash.py` | Low-level flash writer |
| `recov.py` | Recovery / post-flash verification |
| `reboot2recovery.py` | Reboot a running device into recovery mode |
| `uploader.py` | Upload payloads and stream command output |
| `trampoline.py` | USB trampoline payload helper |
| `uimage.py` | uImage parsing utilities |
| `errors.py` | Shared error definitions |
| `fix-stale-deploy-artifacts.py` | Clears stale BitBake stamps after interrupted builds |
