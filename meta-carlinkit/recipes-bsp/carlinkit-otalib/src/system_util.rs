use core::{cmp, time::Duration};

use heapless::{CString, Vec};

use crate::{
    modprobe_util::{ModprobeError, ModprobeUtil},
    nostd::SmallFd,
    println,
};

pub struct SystemUtil;

pub struct ForkGuard {
    exit_code: libc::c_int,
}

impl ForkGuard {
    pub fn new(exit_code: libc::c_int) -> Self {
        Self { exit_code }
    }
}

impl Drop for ForkGuard {
    fn drop(&mut self) {
        unsafe { libc::_exit(self.exit_code) }
    }
}

impl SystemUtil {
    pub fn write_file(path: &str, value: &str) -> Result<(), &'static str> {
        let fd = SmallFd::open_writeonly_or_create(path)?;
        let mut written = 0usize;
        let bytes = value.as_bytes();

        while written < bytes.len() {
            let n = fd.write(&bytes[written..])?;
            if n == 0 {
                return Err("failed to fully write to fd");
            }
            written += n;
        }
        Ok(())
    }

    pub fn run_shell(command: &str) -> Result<(), &'static str> {
        let mut cmd_c = CString::<256>::new();
        if cmd_c.extend_from_bytes(command.as_bytes()).is_err() {
            return Err("command too long");
        }

        let shell = c"/bin/sh";
        let dash_c = c"-c";
        let devnull = c"/dev/null";

        unsafe {
            let pid = libc::fork();
            if pid < 0 {
                return Err("fork failed");
            }

            if pid == 0 {
                let fd = libc::open(devnull.as_ptr(), libc::O_RDWR);

                if fd >= 0 {
                    libc::dup2(fd, 0);
                    libc::dup2(fd, 1);
                    libc::dup2(fd, 2);

                    if fd > 2 {
                        libc::close(fd);
                    }
                }

                let argv = [
                    shell.as_ptr(),
                    dash_c.as_ptr(),
                    cmd_c.as_ptr(),
                    core::ptr::null(),
                ];

                let path = c"PATH=/usr/sbin:/usr/bin:/sbin:/bin";
                let home = c"HOME=/";
                let envp = [
                    path.as_ptr(),
                    home.as_ptr(),
                    core::ptr::null(),
                ];

                libc::execve(
                    shell.as_ptr() as *const _,
                    argv.as_ptr() as *const _,
                    core::ptr::null(),
                );

                libc::_exit(127);
            }

            let mut status: libc::c_int = 0;
            if libc::waitpid(pid, &mut status, 0) < 0 {
                return Err("waitpid failed");
            }

            if libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0 {
                Ok(())
            } else {
                // dmesg!("cmd status: {status}");
                Err("command failed")
            }
        }
    }

    pub fn exec(path: &str, args: &[&str]) -> Result<(), &'static str> {
        let path_c = Self::to_cstring::<256>(path)?;
        let mut args_c = Vec::<CString<256>, 16>::new();
        for arg in args {
            args_c.push(Self::to_cstring::<256>(arg)?).map_err(|_| "too many arguments")?;
        }

        let mut argv = Vec::<*const libc::c_char, 18>::new();
        argv.push(path_c.as_ptr()).map_err(|_| "too many arguments")?;
        for arg in &args_c {
            argv.push(arg.as_ptr()).map_err(|_| "too many arguments")?;
        }
        argv.push(core::ptr::null()).map_err(|_| "too many arguments")?;

        unsafe {
            let pid = libc::fork();
            if pid < 0 {
                return Err("fork failed");
            }

            if pid == 0 {
                libc::execve(path_c.as_ptr() as *const _, argv.as_ptr() as *const _, core::ptr::null());
                libc::_exit(127);
            }

            let mut status: libc::c_int = 0;
            if libc::waitpid(pid, &mut status, 0) < 0 {
                return Err("waitpid failed");
            }

            if libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0 {
                Ok(())
            } else {
                Err("command failed")
            }
        }
    }

    pub fn mkdir_if_missing(path: &str) -> Result<(), &'static str> {
        let c_path = Self::to_cstring::<256>(path)?;
        let ret = unsafe { libc::mkdir(c_path.as_ptr(), libc::S_IRWXU) };
        if ret == 0 {
            return Ok(());
        }

        let errno = unsafe { *libc::__errno_location() };
        if errno == libc::EEXIST { Ok(()) } else { Err("mkdir failed") }
    }

    pub fn path_exists(path: &str) -> Result<bool, &'static str> {
        let c_path = Self::to_cstring::<256>(path)?;
        let ret = unsafe { libc::access(c_path.as_ptr(), libc::F_OK) };
        Ok(ret == 0)
    }

    pub fn ensure_symlink(target: &str, linkpath: &str) -> Result<(), &'static str> {
        Self::unlink_if_exists(linkpath)?;

        let c_target = Self::to_cstring::<256>(target)?;
        let c_link = Self::to_cstring::<256>(linkpath)?;
        let ret = unsafe { libc::symlink(c_target.as_ptr(), c_link.as_ptr()) };
        if ret < 0 {
            return Err("symlink failed");
        }

        Ok(())
    }

    pub fn unlink_if_exists(path: &str) -> Result<(), &'static str> {
        let c_path = Self::to_cstring::<256>(path)?;
        let ret = unsafe { libc::unlink(c_path.as_ptr()) };
        if ret == 0 {
            return Ok(());
        }

        let errno = unsafe { *libc::__errno_location() };
        if errno == libc::ENOENT { Ok(()) } else { Err("unlink failed") }
    }

    pub fn rmdir_if_exists(path: &str) -> Result<(), &'static str> {
        let c_path = Self::to_cstring::<256>(path)?;
        let ret = unsafe { libc::rmdir(c_path.as_ptr()) };
        if ret == 0 {
            return Ok(());
        }

        let errno = unsafe { *libc::__errno_location() };
        if errno == libc::ENOENT { Ok(()) } else { Err("rmdir failed") }
    }

    pub fn path_umount(path: &str) -> Result<(), &'static str> {
        let c_path = Self::to_cstring::<256>(path)?;
        let _ = unsafe { libc::umount2(c_path.as_ptr(), 0) };
        Ok(())
    }

    fn to_cstring<const N: usize>(s: &str) -> Result<CString<N>, &'static str> {
        let mut c = CString::<N>::new();
        if c.extend_from_bytes(s.as_bytes()).is_err() {
            return Err("path too long");
        }
        Ok(c)
    }

    pub fn modprobe(module: &str) -> Result<(), ModprobeError> {
        ModprobeUtil::modprobe(module)
    }

    pub fn modprobe_with_params(module: &str, params: &str) -> Result<(), ModprobeError> {
        ModprobeUtil::modprobe_with_params(module, params)
    }

    pub fn wait_file(path: &str, timeout_secs: u32) -> Result<(), &'static str> {
        let start_time = unsafe { libc::time(core::ptr::null_mut()) };
        if start_time < 0 {
            return Err("time failed");
        }

        println!("Waiting for file: {path}");

        loop {
            if Self::path_exists(path)? {
                println!("File arrived: {path}");
                return Ok(());
            }

            if timeout_secs > 0 {
                let now = unsafe { libc::time(core::ptr::null_mut()) };
                if now < 0 {
                    return Err("time failed");
                }

                let elapsed = now - start_time;
                if elapsed >= timeout_secs as libc::time_t {
                    println!("Timed out waiting for: {path}");
                    return Err("wait_file timeout");
                }
            }

            Self::sleep(Duration::from_millis(50));
        }
    }

    #[allow(clippy::field_reassign_with_default)]
    pub fn sleep(duration: Duration) {
        let ms = cmp::min(duration.as_millis(), libc::c_int::MAX as u128) as libc::c_int;
        unsafe {
            libc::poll(core::ptr::null_mut(), 0, ms);
        }
    }

    /// Perform fork; returns true for child and false for parent
    pub fn fork() -> bool {
        let pid = unsafe { libc::fork() };
        match pid {
            0 => true,
            n if n > 0 => false,
            _ => panic!("fork failed"),
        }
    }

    /// Perform fork; returns a guard in child and None in parent.
    /// Dropping the guard exits the child process to avoid falling back into PID1 boot flow.
    pub fn fork_guard() -> Option<ForkGuard> {
        let pid = unsafe { libc::fork() };
        match pid {
            0 => Some(ForkGuard::new(0)),
            n if n > 0 => None,
            _ => panic!("fork failed"),
        }
    }

    pub fn readahead(p: &str) -> bool {
        let fd = SmallFd::open_readonly(p);
        if let Ok(fd) = fd
            && let Ok(stat) = fd.stat()
        {
            unsafe { libc::posix_fadvise(fd.raw_fd(), 0, 0, libc::POSIX_FADV_WILLNEED) };
            let chunk: i64 = 1 << 20;
            let mut off: i64 = 0;
            while off < stat.st_size {
                let len = cmp::min(chunk, stat.st_size - off);
                unsafe { libc::syscall(libc::SYS_readahead as _, fd.raw_fd(), off, len) };
                off += len;
            }
            true
        } else {
            false
        }
    }

    pub fn set_sched_idle() {
        let mut sp: libc::sched_param = unsafe { core::mem::zeroed() };
        sp.sched_priority = 0;
        unsafe { libc::sched_setscheduler(0, libc::SCHED_IDLE, &sp) };
        let _ = unsafe { libc::setpriority(libc::PRIO_PROCESS, 0, 19) };
    }

    pub fn getpid() -> libc::pid_t {
        unsafe { libc::getpid() }
    }
}
