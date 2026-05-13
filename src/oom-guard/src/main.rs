use nix::sys::signal::{self, Signal};
use nix::unistd::Pid;
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

struct ProcessInfo {
    redis_pid: u32,
    entrypoint_pid: u32,
}

fn main() {
    let threshold: f64 = env::var("OOM_PREEMPT_THRESHOLD")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(0.90);

    eprintln!(
        "[oom-guard] starting — threshold={:.0}%",
        threshold * 100.0
    );

    let mut memory_limit: Option<u64> = None;
    let mut cgroup: Option<CgroupVersion> = None;
    let mut cached_proc: Option<ProcessInfo> = None;

    loop {
        // 1. Adjust OOM scores for redis-server and healthcheck processes.
        adjust_oom_scores();

        // 2. Discover redis-server PID (cache it, re-discover if /proc/<pid> vanishes).
        if cached_proc
            .as_ref()
            .map_or(true, |p| !Path::new(&format!("/proc/{}", p.redis_pid)).exists())
        {
            cached_proc = discover_redis_process();
            // Reset cgroup info when PID changes — the cgroup path is PID-dependent.
            memory_limit = None;
            cgroup = None;
        }

        if let Some(ref proc_info) = cached_proc {
            // 3. Discover cgroup paths on first iteration (or after PID change).
            if cgroup.is_none() {
                cgroup = discover_cgroup(proc_info.redis_pid);
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
                    let usage_ratio = current as f64 / limit as f64;

                    if usage_ratio >= threshold {
                        let timestamp = format_timestamp();
                        let msg = format!(
                            "[{}] OOM_PREEMPT: memory usage {} bytes ({:.1}%) exceeds threshold ({:.0}% of {} bytes). \
                             Dumping debug info and sending SIGTERM to entrypoint (pid {}).",
                            timestamp,
                            current,
                            usage_ratio * 100.0,
                            threshold * 100.0,
                            limit,
                            proc_info.entrypoint_pid,
                        );
                        eprintln!("{}", msg);

                        // Dump Redis debug info to file.
                        dump_redis_info(&msg);

                        // Send SIGTERM to entrypoint (parent of redis-server).
                        if let Err(e) = signal::kill(
                            Pid::from_raw(proc_info.entrypoint_pid as i32),
                            Signal::SIGTERM,
                        ) {
                            eprintln!(
                                "[oom-guard] failed to send SIGTERM to pid {}: {}",
                                proc_info.entrypoint_pid, e
                            );
                        } else {
                            eprintln!(
                                "[oom-guard] SIGTERM sent to entrypoint pid {}",
                                proc_info.entrypoint_pid
                            );
                        }

                        // Exit to avoid repeated signals.
                        std::process::exit(0);
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

/// Find the redis-server PID and its parent (entrypoint) PID.
fn discover_redis_process() -> Option<ProcessInfo> {
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

        // Read PPid from /proc/<pid>/status to find the entrypoint.
        let entrypoint_pid = read_ppid(redis_pid).unwrap_or(1);

        eprintln!(
            "[oom-guard] discovered redis-server pid={}, entrypoint pid={}",
            redis_pid, entrypoint_pid
        );

        return Some(ProcessInfo {
            redis_pid,
            entrypoint_pid,
        });
    }

    None
}

/// Read PPid from /proc/<pid>/status.
fn read_ppid(pid: u32) -> Option<u32> {
    let status = fs::read_to_string(format!("/proc/{}/status", pid)).ok()?;
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("PPid:") {
            return rest.trim().parse().ok();
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Cgroup discovery and reading
// ---------------------------------------------------------------------------

/// Discover the cgroup path for the redis-server container.
///
/// Strategy 1: Read /proc/<pid>/cgroup to learn the container's cgroup path,
///   then access it from the sidecar's own /sys/fs/cgroup mount.  This works
///   in shared-PID-namespace pods without needing SYS_PTRACE.
/// Strategy 2: Access via /proc/<pid>/root/sys/fs/cgroup/ (needs SYS_PTRACE).
/// Strategy 3: Fall back to the sidecar's own cgroup (pod-level).
fn discover_cgroup(redis_pid: u32) -> Option<CgroupVersion> {
    // --- Strategy 1: parse /proc/<pid>/cgroup ---------------------------------
    if let Some(cg) = discover_cgroup_via_proc(redis_pid) {
        return Some(cg);
    }

    // --- Strategy 2: /proc/<pid>/root/ traversal ------------------------------
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

    // --- Strategy 3: sidecar's own cgroup (pod-level fallback) ----------------
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

/// Read /proc/<pid>/cgroup and resolve the container's cgroup path under
/// the sidecar's /sys/fs/cgroup mount.
fn discover_cgroup_via_proc(redis_pid: u32) -> Option<CgroupVersion> {
    let cgroup_file = format!("/proc/{}/cgroup", redis_pid);
    let content = fs::read_to_string(&cgroup_file).ok()?;

    for line in content.lines() {
        // Format: "hierarchy-ID:controller-list:cgroup-path"
        //   v2: "0::/kubepods/burstable/pod<uid>/<cid>"
        //   v1: "6:memory:/kubepods/burstable/pod<uid>/<cid>"
        let parts: Vec<&str> = line.splitn(3, ':').collect();
        if parts.len() != 3 {
            continue;
        }

        // cgroup v2 unified hierarchy
        if parts[0] == "0" && parts[1].is_empty() {
            let rel = parts[2].trim_start_matches('/');
            let base = if rel.is_empty() {
                PathBuf::from("/sys/fs/cgroup")
            } else {
                PathBuf::from(format!("/sys/fs/cgroup/{}", rel))
            };
            if base.join("memory.current").exists() {
                eprintln!(
                    "[oom-guard] using cgroup v2 via /proc/{}/cgroup -> {}",
                    redis_pid,
                    base.display()
                );
                return Some(CgroupVersion::V2 { base });
            }
        }

        // cgroup v1 memory controller
        if parts[1] == "memory" {
            let rel = parts[2].trim_start_matches('/');
            let base = if rel.is_empty() {
                PathBuf::from("/sys/fs/cgroup/memory")
            } else {
                PathBuf::from(format!("/sys/fs/cgroup/memory/{}", rel))
            };
            if base.join("memory.usage_in_bytes").exists() {
                eprintln!(
                    "[oom-guard] using cgroup v1 via /proc/{}/cgroup -> {}",
                    redis_pid,
                    base.display()
                );
                return Some(CgroupVersion::V1 { base });
            }
        }
    }

    eprintln!(
        "[oom-guard] /proc/{}/cgroup did not resolve to accessible memory files (content: {:?})",
        redis_pid,
        content.trim()
    );
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

/// Connect to Redis and dump diagnostic info to /data/oom_dump_info.log.
fn dump_redis_info(trigger_msg: &str) {
    let node_port = env::var("NODE_PORT").unwrap_or_else(|_| "6379".to_string());
    let password = get_redis_password();
    let tls = env::var("TLS").unwrap_or_default();

    let redis_url = if tls == "true" {
        format!("rediss://:{}@localhost:{}", password, node_port)
    } else {
        format!("redis://:{}@localhost:{}", password, node_port)
    };

    let mut output = String::with_capacity(64 * 1024);
    output.push_str("=== OOM PREEMPT DUMP ===\n");
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

    output.push_str("=== END OOM PREEMPT DUMP ===\n");

    // Write to /data/oom_dump_info.log.
    let dump_path = "/data/oom_dump_info.log";
    match fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dump_path)
    {
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
