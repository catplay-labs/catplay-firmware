use core::time::Duration;

use crate::{SystemUtil, dmesg, nostd::SmallFd};

pub struct Watchdog {
    fd: SmallFd,
}

impl Watchdog {
    pub fn open() -> Result<Self, &'static str> {
        SmallFd::open_writeonly("/dev/watchdog0").map(|fd| Self { fd })
    }

    pub fn feed(&self) -> Result<(), &'static str> {
        // Any nonempty write feeds the Linux watchdog. Avoid the magic-close byte 'V'.
        loop {
            match self.fd.write(b"\0") {
                Ok(1) => return Ok(()),
                Err(_) if unsafe { *libc::__errno_location() } == libc::EINTR => continue,
                _ => return Err("failed to feed /dev/watchdog0"),
            }
        }
    }

    /// Feed immediately, then transfer ownership to a child feeding once per second.
    pub fn run_forked(self) -> Result<libc::pid_t, &'static str> {
        if let Err(err) = self.feed() {
            dmesg!("<4>[watchdog] WARN: {err}");
        }

        let pid = unsafe { libc::fork() };
        if pid < 0 {
            return Err("failed to fork watchdog feeder");
        }
        if pid == 0 {
            loop {
                SystemUtil::sleep(Duration::from_secs(1));
                if let Err(err) = self.feed() {
                    dmesg!("<4>[watchdog] WARN: {err}");
                }
            }
        }

        // The child retains the open file; close the parent's copy before later forks.
        Ok(pid)
    }

    pub fn start_best_effort() {
        match Self::open() {
            Ok(watchdog) => {
                dmesg!("[watchdog] Opened /dev/watchdog0");
                if let Err(err) = watchdog.run_forked() {
                    dmesg!("<4>[watchdog] WARN: {err}");
                }
            }
            Err(_) => {
                let errno = unsafe { *libc::__errno_location() };
                dmesg!("<4>[watchdog] WARN: cannot open /dev/watchdog0 (errno={errno})");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nostd::FdGuard;

    struct FeederGuard(libc::pid_t);

    impl Drop for FeederGuard {
        fn drop(&mut self) {
            unsafe {
                libc::kill(self.0, libc::SIGKILL);
                libc::waitpid(self.0, core::ptr::null_mut(), 0);
            }
        }
    }

    #[test]
    fn feeds_immediately_and_continues_in_child() {
        // Use a pipe instead of touching a real hardware watchdog.
        let mut pipe = [-1; 2];
        assert_eq!(unsafe { libc::pipe(pipe.as_mut_ptr()) }, 0);
        let reader = FdGuard::new(pipe[0]);
        let writer = FdGuard::new(pipe[1]);
        let path = heapless::format!(64; "/proc/self/fd/{}", writer.fd).unwrap();
        let watchdog = Watchdog {
            fd: SmallFd::open_writeonly(&path).unwrap(),
        };
        drop(writer);
        let child = FeederGuard(watchdog.run_forked().unwrap());
        assert!(child.0 > 0);

        for timeout in [0, 3000, 3000] {
            let mut pollfd = libc::pollfd {
                fd: reader.fd,
                events: libc::POLLIN,
                revents: 0,
            };
            assert_eq!(unsafe { libc::poll(&mut pollfd, 1, timeout) }, 1);
            let mut byte = 255u8;
            assert_eq!(
                unsafe { libc::read(reader.fd, (&mut byte as *mut u8).cast(), 1) },
                1
            );
            assert_eq!(byte, 0);
        }
    }
}
