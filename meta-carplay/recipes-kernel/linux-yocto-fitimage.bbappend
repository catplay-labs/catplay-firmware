INSANE_SKIP:${PN} += "buildpaths"

# V821's FIT is usable by both bootm recovery and the future SPL OS boot path.
do_compile[depends] += "${@'virtual/opensbi:do_deploy' if 'v821' in d.getVar('MACHINEOVERRIDES').split(':') else ''}"

python do_compile:append:v821() {
    from pathlib import Path
    import subprocess
    fit = str(Path(d.getVar('B')) / 'fitImage')
    configurations = subprocess.check_output(['fdtget', '-l', fit, '/configurations'], text=True).split()
    for config in configurations:
        path = '/configurations/' + config
        kernel = subprocess.check_output(['fdtget', '-t', 's', fit, path, 'kernel'], text=True).strip()
        subprocess.run(['fdtput', '-t', 's', fit, path, 'firmware', 'opensbi'], check=True)
        subprocess.run(['fdtput', '-t', 's', fit, path, 'loadables', kernel], check=True)
}
