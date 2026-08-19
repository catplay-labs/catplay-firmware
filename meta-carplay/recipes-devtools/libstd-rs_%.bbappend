include time64-${TCLIBC}.inc

FILESEXTRAPATHS:prepend := "${THISDIR}/libstd-rs:"

# Rust 1.90 vendors libc 0.2.x.  Use the same pinned libc alpha as Catplay so
# the target standard library also gets the current riscv32/musl definitions.
LIBC_ALPHA_VERSION = "1.0.0-alpha.4"
LIBC_ALPHA_SRCREV = "a1fcda25057b609984288ba2a280011367a493c1"
LIBC_ALPHA_ENABLED = "${@'1' if d.getVar('PV') == '1.90.0' and d.getVar('TARGET_ARCH') == 'riscv32' and d.getVar('TCLIBC') == 'musl' else '0'}"

SRC_URI:append = "${@' git://github.com/rust-lang/libc.git;protocol=https;nobranch=1;destsuffix=libc-alpha file://0001-libstd-riscv32-musl-libc-alpha-compat.patch;patchdir=../..' if d.getVar('LIBC_ALPHA_ENABLED') == '1' else ''}"
CARGO_LOCK_PATH = "${@os.path.join(d.getVar('RUSTSRC'), 'library', 'Cargo.lock') if d.getVar('LIBC_ALPHA_ENABLED') == '1' else os.path.join(os.path.dirname(d.getVar('CARGO_MANIFEST_PATH')), 'Cargo.lock')}"

python __anonymous () {
    if d.getVar("LIBC_ALPHA_ENABLED") == "1":
        d.setVar("SRCREV", d.getVar("LIBC_ALPHA_SRCREV"))
}

python prepare_libc_alpha () {
    if d.getVar("LIBC_ALPHA_ENABLED") != "1":
        return

    import pathlib
    import re
    import shutil

    rustsrc = pathlib.Path(d.getVar("RUSTSRC"))
    libc_src = pathlib.Path(d.getVar("WORKDIR")) / "libc-alpha"
    libc_dst = rustsrc / "library" / "libc-alpha"

    if libc_dst.exists():
        shutil.rmtree(libc_dst)
    shutil.copytree(libc_src, libc_dst, symlinks=True)

    libc_manifest = libc_dst / "Cargo.toml"
    libc_manifest_text = libc_manifest.read_text()
    libc_manifest_text, workspace_replacements = re.subn(
        r'\n\[workspace\]\n.*\Z',
        '\n',
        libc_manifest_text,
        flags=re.DOTALL,
    )
    if workspace_replacements != 1:
        bb.fatal("libstd-rs: expected one nested workspace section in %s" % libc_manifest)
    libc_manifest.write_text(libc_manifest_text)

    std_manifest = rustsrc / "library" / "std" / "Cargo.toml"
    manifest_text = std_manifest.read_text()
    old_dependency = 'libc = { version = "0.2.172", default-features = false, features = ['
    new_dependency = 'libc = { path = "../libc-alpha", default-features = false, features = ['
    if manifest_text.count(old_dependency) == 1:
        manifest_text = manifest_text.replace(old_dependency, new_dependency)
        std_manifest.write_text(manifest_text)
    elif manifest_text.count(new_dependency) != 1:
        bb.fatal("libstd-rs: expected exactly one vendored libc dependency in %s" % std_manifest)

}

do_configure[prefuncs] += "${@'prepare_libc_alpha' if d.getVar('LIBC_ALPHA_ENABLED') == '1' else ''}"

finalize_libc_alpha_lock () {
    cargo metadata \
        --offline \
        --format-version 1 \
        --manifest-path "${CARGO_MANIFEST_PATH}" \
        > /dev/null
}

do_configure[postfuncs] += "${@'finalize_libc_alpha_lock' if d.getVar('LIBC_ALPHA_ENABLED') == '1' else ''}"
