# OpenSBI is the FIT firmware; U-Boot loads the kernel for its next stage.
do_compile[depends] += "virtual/opensbi:do_deploy"

python do_compile:append() {
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
