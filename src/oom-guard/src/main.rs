use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, SystemTime};

/// How we read cgroup memory (v1 vs v2 paths differ).
enum CgroupVersion {
    V2 { base: PathBuf },
    V1 { base: PathBuf },
}

/// Write mode for dump files.
enum DumpMode {
    /// Overwrite the file each time (70%).
    Overwrite,
    /// Append with separator (80%).
    Append,
}

fn main() {
    eprintln!("[oom-guard] starting — dump thresholds: 70%, 80%");

    let mut memory_limit: Option<u64> = None;
    let mut cgroup: Option<CgroupVersion> = None;
    let mut cached_pid: Option<u32> = None;
    let mut dump_70_done = false;
    let mut dump_80_done = false;

    loop {
        // 1. Adjust OOM scores for redis-server and healthcheck processes.
        adjust_oom_scores();

        // 2. Discover redis-server PID (cache it, re-discover if /proc/<pid> vanishes).
        if cached_pid
            .map_or(true, |pid| !Path::new(&format!("/proc/{}", pid)).exists())
        {
            cached_pid = discover_redis_pid();
            // Reset cgroup info when PID changes — the cgroup path is PID-dependent.
            memory_limit = None;
            cgroup = None;
            dump_70_done = false;
            dump_80_done = false;
        }

        if let Some(redis_pid) = cached_pid {
            // 3. Discover cgroup paths on first iteration (or after PID change).
            if cgroup.is_none() {
                cgroup = discover_cgroup(redis_pid);
            }

            if let Some(ref cg) = cgroup {
                // Read limit once and cache.
                if memory_limit.is_none() {
                    memory_limit = read_memory_limit(cg);
                    if let Some(limit) = memory_limit {
                        eprintln!(
                            "[oom-guard] detected container memory limit: {} bytes ({:.0} MiB)",
                            limit,
                            limit as f64 / 1048576.0
                        );
                    }
                }

                if let (Some(limit), Some(current)) = (memory_limit, read_memory_current(cg)) {
                    let usage_pct = current as f64 / limit as f64 * 100.0;

                    // 80% — append (keeps history across multiple spikes)
                    if !dump_80_done && usage_pct >= 80.0 {
                        let msg = format!(
                            "[{}] OOM_WARNING: {:.1}% — {} / {} bytes",
                            format_timestamp(), usage_pct, current, limit,
                        );
                        eprintln!("{}", msg);
                        dump_redis_info(&msg, "/data/oom_dump_80.log", DumpMode::Append);
                        dump_80_done = true;
                    }

                    // 70% — overwrite (latest snapshot only)
                    if !dump_70_done && usage_pct >= 70.0 {
                        let msg = format!(
                            "[{}] OOM_INFO: {:.1}% — {} / {} bytes",
                            format_timestamp(), usage_pct, current, limit,
                        );
                        eprintln!("{}", msg);
                        dump_redis_info(&msg, "/data/oom_dump_70.log", DumpMode::Overwrite);
                        dump_70_done = true;
                    }

                    // Reset flags if memory drops back below threshold
                    // (allows re-dump on next spike)
                    if usage_pct < 70.0 {
                        dump_70_done = false;
                    }
                    if usage_pct < 80.0 {
                        dump_80_done = false;
                    }
                }
            }
        }

        thread::sleep(Duration::from_secs(1));
    }
}

// ---------------------------------------------------------------------------
// OOM score adjustment
// ---------------------------------------------------------------------------

/// Set oom_score_adj = -1000 for redis-server and healthcheck processes.
fn adjust_oom_scores() {
    let proc_dir = match fs::read_dir("/proc") {
        Ok(d) => d,
        Err(_) => return,
    };

    for entry in proc_dir.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        // Only look at numeric directories (PIDs).
        if !name_str.chars().next().map_or(false, |c| c.is_ascii_digit()) {
            continue;
        }
        let pid_path = entry.path();

        let comm = match fs::read_to_string(pid_path.join("comm")) {
            Ok(c) => c.trim().to_string(),
            Err(_) => continue,
        };

        if !comm.starts_with("redis-server") && !comm.starts_with("healthcheck") {
            continue;
        }

        let adj_path = pid_path.join("oom_score_adj");
        let current = fs::read_to_string(&adj_path)
            .unwrap_or_default()
            .trim()
            .to_string();

        if current != "-1000" {
            if fs::write(&adj_path, "-1000").is_ok() {
                eprintln!(
                    "[oom-guard] set oom_score_adj=-1000 for {} (pid {})",
                    comm, name_str
                );
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Process discovery
// ---------------------------------------------------------------------------

/// Find the redis-server PID.
fn discover_redis_pid() -> Option<u32> {
    let proc_dir = fs::read_dir("/proc").ok()?;

    for entry in proc_dir.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy().to_string();
        if !name_str.chars().next().map_or(false, |c| c.is_ascii_digit()) {
            continue;
        }

        let comm = fs::read_to_string(entry.path().join("comm"))
            .unwrap_or_default()
            .trim()
            .to_string();

        if !comm.starts_with("redis-server") {
            continue;
        }

        let redis_pid: u32 = match name_str.parse() {
            Ok(p) => p,
            Err(_) => continue,
        };

        eprintln!("[oom-guard] discovered redis-server pid={}", redis_pid);
        return Some(redis_pid);
    }

    None
}

// ---------------------------------------------------------------------------
// Cgroup discovery and reading
// ---------------------------------------------------------------------------

/// Discover the cgroup path for the redis-server container.
///
/// Strategy 1: Access via /proc/<pid>/root/sys/fs/cgroup/ (needs SYS_PTRACE).
/// Strategy 2: Fall back to the sidecar's own cgroup (pod-level).
fn discover_cgroup(redis_pid: u32) -> Option<CgroupVersion> {
    // --- Strategy 1: /proc/<pid>/root/ traversal (needs SYS_PTRACE) -----------
    let proc_root = format!("/proc/{}/root", redis_pid);

    let v2_base = PathBuf::from(format!("{}/sys/fs/cgroup", proc_root));
    if v2_base.join("memory.current").exists() {
        eprintln!("[oom-guard] using cgroup v2 via {}", v2_base.display());
        return Some(CgroupVersion::V2 { base: v2_base });
    }

    let v1_base = PathBuf::from(format!("{}/sys/fs/cgroup/memory", proc_root));
    if v1_base.join("memory.usage_in_bytes").exists() {
        eprintln!("[oom-guard] using cgroup v1 via {}", v1_base.display());
        return Some(CgroupVersion::V1 { base: v1_base });
    }

    // --- Strategy 2: sidecar's own cgroup (pod-level fallback) ----------------
    let local_v2 = PathBuf::from("/sys/fs/cgroup");
    if local_v2.join("memory.current").exists() {
        eprintln!("[oom-guard] WARNING: using local cgroup v2 (pod-level) — may reflect sidecar limits, not service container");
        return Some(CgroupVersion::V2 { base: local_v2 });
    }

    let local_v1 = PathBuf::from("/sys/fs/cgroup/memory");
    if local_v1.join("memory.usage_in_bytes").exists() {
        eprintln!("[oom-guard] WARNING: using local cgroup v1 (pod-level) — may reflect sidecar limits, not service container");
        return Some(CgroupVersion::V1 { base: local_v1 });
    }

    eprintln!("[oom-guard] WARNING: could not discover cgroup for redis-server");
    None
}

fn read_memory_limit(cg: &CgroupVersion) -> Option<u64> {
    match cg {
        CgroupVersion::V2 { base } => {
            let content = fs::read_to_string(base.join("memory.max")).ok()?;
            let trimmed = content.trim();
            if trimmed == "max" {
                // No limit set — treat as unlimited.
                None
            } else {
                trimmed.parse().ok()
            }
        }
        CgroupVersion::V1 { base } => {
            let content = fs::read_to_string(base.join("memory.limit_in_bytes")).ok()?;
            let val: u64 = content.trim().parse().ok()?;
            // cgroup v1 uses a very large number for "no limit".
            if val >= 9223372036854771712 {
                None
            } else {
                Some(val)
            }
        }
    }
}

fn read_memory_current(cg: &CgroupVersion) -> Option<u64> {
    match cg {
        CgroupVersion::V2 { base } => {
            let content = fs::read_to_string(base.join("memory.current")).ok()?;
            content.trim().parse().ok()
        }
        CgroupVersion::V1 { base } => {
            let content = fs::read_to_string(base.join("memory.usage_in_bytes")).ok()?;
            content.trim().parse().ok()
        }
    }
}

// ---------------------------------------------------------------------------
// Redis info dump
// ---------------------------------------------------------------------------

/// Connect to Redis and dump diagnostic info to the given path.
fn dump_redis_info(trigger_msg: &str, dump_path: &str, mode: DumpMode) {
    let node_port = env::var("NODE_PORT").unwrap_or_else(|_| "6379".to_string());
    let password = get_redis_password();
    let tls = env::var("TLS").unwrap_or_default();

    let redis_url = if tls == "true" {
        format!("rediss://:{}@localhost:{}", password, node_port)
    } else {
        format!("redis://:{}@localhost:{}", password, node_port)
    };

    let mut output = String::with_capacity(64 * 1024);

    // For append mode, add a separator between runs.
    if matches!(mode, DumpMode::Append) {
        output.push_str("\n------------------------------------------------------------\n");
    }

    output.push_str("=== OOM DUMP ===\n");
    output.push_str(trigger_msg);
    output.push('\n');
    output.push('\n');

    match redis::Client::open(redis_url.as_str()) {
        Ok(client) => {
            match client.get_connection() {
                Ok(mut con) => {
                    // Collect diagnostic commands.
                    for cmd_name in &[
                        "INFO ALL",
                        "CLIENT LIST",
                        "MEMORY DOCTOR",
                        "MEMORY MALLOC-STATS",
                        "DBSIZE",
                    ] {
                        output.push_str(&format!("--- {} ---\n", cmd_name));

                        let parts: Vec<&str> = cmd_name.split_whitespace().collect();
                        let result: Result<redis::Value, _> = if parts.len() == 1 {
                            redis::cmd(parts[0]).query(&mut con)
                        } else {
                            redis::cmd(parts[0]).arg(parts[1]).query(&mut con)
                        };

                        match result {
                            Ok(val) => output.push_str(&format_redis_value(&val)),
                            Err(e) => output.push_str(&format!("ERROR: {}\n", e)),
                        }
                        output.push('\n');
                    }
                }
                Err(e) => {
                    output.push_str(&format!("ERROR: failed to connect to Redis: {}\n", e));
                }
            }
        }
        Err(e) => {
            output.push_str(&format!("ERROR: failed to create Redis client: {}\n", e));
        }
    }

    output.push_str("=== END OOM DUMP ===\n");

    let file_result = match mode {
        DumpMode::Overwrite => fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(dump_path),
        DumpMode::Append => fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(dump_path),
    };

    match file_result {
        Ok(mut f) => {
            if let Err(e) = f.write_all(output.as_bytes()) {
                eprintln!("[oom-guard] failed to write dump file: {}", e);
            } else {
                eprintln!("[oom-guard] dump written to {}", dump_path);
            }
        }
        Err(e) => {
            eprintln!("[oom-guard] failed to open {}: {}", dump_path, e);
            // Fall back to stderr so the info is not lost.
            eprint!("{}", output);
        }
    }
}

/// Read the Redis password from env or secrets file.
fn get_redis_password() -> String {
    env::var("ADMIN_PASSWORD").unwrap_or_else(|_| {
        fs::read_to_string("/run/secrets/adminpassword")
            .map(|s| s.trim().to_string())
            .unwrap_or_default()
    })
}

/// Format a Redis value for human-readable output.
fn format_redis_value(val: &redis::Value) -> String {
    match val {
        redis::Value::BulkString(bytes) => String::from_utf8_lossy(bytes).into_owned(),
        redis::Value::SimpleString(s) => s.clone(),
        redis::Value::Int(i) => i.to_string(),
        redis::Value::Array(arr) => arr
            .iter()
            .map(|v| format_redis_value(v))
            .collect::<Vec<_>>()
            .join("\n"),
        redis::Value::Nil => "(nil)".to_string(),
        other => format!("{:?}", other),
    }
}

// ---------------------------------------------------------------------------
// Timestamp formatting (without pulling in chrono)
// ---------------------------------------------------------------------------

fn format_timestamp() -> String {
    match SystemTime::now().duration_since(SystemTime::UNIX_EPOCH) {
        Ok(d) => {
            let secs = d.as_secs();
            // Simple UTC timestamp: seconds since epoch.
            format!("{}Z", secs)
        }
        Err(_) => "unknown".to_string(),
    }
}
