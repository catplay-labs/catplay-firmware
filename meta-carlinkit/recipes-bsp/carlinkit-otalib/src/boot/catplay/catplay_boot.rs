use crate::{SystemUtil, boot::catplay::CatplayConfig, dmesg};
use heapless::Vec;

pub struct CatPlay(());

impl CatPlay {
    pub fn start(
        params: &CatplayConfig<'_>,
        rust_log_level: Option<&str>,
        tracing: bool,
    ) -> Result<(), &'static str> {
        let _ = SystemUtil::unlink_if_exists("/etc/catplay/catplay.conf");
        let _ = SystemUtil::mkdir_if_missing("/etc/catplay");

        let cfg = params
            .format()
            .map_err(|_| "failed to format catplay config")?;

        let _ = SystemUtil::write_file("/etc/catplay/catplay.conf", cfg.as_str());

        dmesg!("[dmesg] CatPlay forking now");
        let mut env = Vec::<(&str, &str), 2>::new();
        if let Some(level) = rust_log_level {
            env.push(("RUST_LOG", level))
                .map_err(|_| "too many catplay environment variables")?;
        }
        if tracing {
            env.push(("CATPLAY_TRACING", "1"))
                .map_err(|_| "too many catplay environment variables")?;
        }
        SystemUtil::run_shell_with_env("/etc/init.d/catplay start", env.as_slice())
            .map_err(|_| "failed to start catplay service")
    }
}
