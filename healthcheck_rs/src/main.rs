use rouille::{router, Response, Server};
use std::env;
use std::thread;
use std::time::{Duration, Instant};
use sysinfo::{Pid, ProcessesToUpdate, System};

fn main() {
    let args: Vec<String> = env::args().collect();
    let is_sentinel = args.get(1).map_or(false, |arg| arg == "sentinel");
    start_health_check_server(is_sentinel);
}

fn start_health_check_server(is_sentinel: bool) {
    let redis_pool = get_redis_connection_pool(is_sentinel).unwrap();
    let port = env::var(if is_sentinel {
        "HEALTH_CHECK_PORT_SENTINEL"
    } else {
        "HEALTH_CHECK_PORT"
    })
    .unwrap_or_else(|_| {
        if is_sentinel {
            "8082".to_string()
        } else {
            "8081".to_string()
        }
    });

    let addr = format!("localhost:{}", port);
    let server = Server::new(addr, move |request| {
        router!(request,
            (GET) (/liveness) => { handle_health_check(is_sentinel, check_handler_liveness, &redis_pool) },
            (GET) (/readiness) => { handle_health_check(is_sentinel, check_handler_readiness, &redis_pool) },
            (GET) (/startup) => { Response::text("OK") },
            _ => Response::empty_404()
        )
    }).unwrap();

    println!("Listening on {}", server.server_addr());
    server.run();
}

fn handle_health_check<F>(
    is_sentinel: bool,
    check_fn: F,
    redis_pool: &r2d2::Pool<redis::Client>,
) -> Response
where
    F: Fn(bool, &r2d2::Pool<redis::Client>) -> Result<bool, redis::RedisError>,
{
    if env::var("SKIP_HEALTH_CHECK").as_deref() == Ok("true") {
        return Response::text("OK");
    }
    match check_fn(is_sentinel, redis_pool) {
        Ok(true) => Response::text("OK"),
        _ => Response::text("Not ready").with_status_code(500),
    }
}

fn check_handler_liveness(
    is_sentinel: bool,
    redis_pool: &r2d2::Pool<redis::Client>,
) -> Result<bool, redis::RedisError> {
    if let Ok(mut con) = redis_pool.get() {
        if is_sentinel {
            return check_sentinel(&mut con);
        }

        // Set a 5 second timeout for the PING command
        let cmd = redis::cmd("PING");
        cmd.get_packed_command(); // Ensure command is packed before setting timeout (for some clients)
        con.set_read_timeout(Some(Duration::from_secs(5))).ok();
        con.set_write_timeout(Some(Duration::from_secs(5))).ok();
        let response = match cmd.query::<String>(&mut con) {
            Ok(resp) => Ok(resp == "PONG"),
            Err(err) => {
                // Handle timeout error by calling check_redis_liveness_with_io
                if err.kind() == redis::ErrorKind::IoError {
                    if check_redis_liveness_with_disk_usage().is_ok() {
                        return Ok(true);
                    } else {
                        return Ok(false);
                    }
                } else {
                    // Reset timeouts to default (optional)
                    con.set_read_timeout(None).ok();
                    con.set_write_timeout(None).ok();
                    return Ok(false);
                }
            }
        };
        // Reset timeouts to default (optional)
        con.set_read_timeout(None).ok();
        con.set_write_timeout(None).ok();

        return response;
    } else {
        Err(redis::RedisError::from((
            redis::ErrorKind::IoError,
            "Failed to get connection from pool",
        )))
    }
}

fn check_redis_liveness_with_disk_usage() -> Result<bool, String> {
    let redis_pid = env::var("REDIS_PID")
        .map_err(|_| "REDIS_PID environment variable not set")?
        .parse::<u32>()
        .map_err(|_| "Invalid REDIS_PID value")?;
    let pid = Pid::from_u32(redis_pid);
    let duration_secs = env::var("DISK_USAGE_MONITOR_DURATION")
        .unwrap_or_else(|_| "2".to_string())
        .parse::<u64>()
        .map_err(|_| "Invalid DISK_USAGE_MONITOR_DURATION value")?;

    let mb_read = get_process_disk_read_mb_over_time(pid, duration_secs)
        .map_err(|e| format!("Failed to get disk read: {}", e))?;

    println!(
        "Redis process (PID: {}) read {} MB from disk in the last {} seconds.",
        redis_pid, mb_read, duration_secs
    );

    let threshold_mb = env::var("DISK_USAGE_THRESHOLD_MB")
        .unwrap_or_else(|_| "0.0".to_string())
        .parse::<f64>()
        .map_err(|_| "Invalid DISK_USAGE_THRESHOLD_MB value")?;

    if mb_read > threshold_mb {
        Ok(true)
    } else {
        Ok(false)
    }
}

/// Calculates the amount of data read from disk by a specific process
/// over a given duration.
///
/// # Arguments
///
/// * `pid` - The Process ID (PID) of the process to monitor.
/// * `duration_secs` - The duration in seconds for which to monitor the disk reads.
///
/// # Returns
///
/// A `Result` which is:
/// - `Ok(f64)`: The amount of data read from disk in Megabytes (MB) during the duration.
/// - `Err(String)`: An error message if the process is not found or other issues occur.
pub fn get_process_disk_read_mb_over_time(pid: Pid, duration_secs: u64) -> Result<f64, String> {
    // Initialize the System struct. We only need process information.
    // Using `new()` and then `refresh_processes()` is more efficient than `new_all()`
    // if only process data is needed.
    let mut system = System::new();

    // Refresh processes to get initial state
    system.refresh_processes(ProcessesToUpdate::All, false);

    // Find the process by PID at the start of the monitoring period
    let initial_process = system.process(pid);

    let initial_total_read_bytes: u64;

    match initial_process {
        Some(p) => {
            initial_total_read_bytes = p.disk_usage().total_read_bytes;
        }
        None => {
            return Err(format!("Process with PID {} not found at the start.", pid));
        }
    }

    println!(
        "Monitoring PID {} for {} seconds. Initial total read: {} bytes",
        pid, duration_secs, initial_total_read_bytes
    );

    // Wait for the specified duration
    thread::sleep(Duration::from_secs(duration_secs));

    // Refresh processes again to get the final state
    system.refresh_processes(ProcessesToUpdate::All, false);

    // Find the process by PID at the end of the monitoring period
    let final_process = system.process(pid);

    let final_total_read_bytes: u64;

    match final_process {
        Some(p) => {
            final_total_read_bytes = p.disk_usage().total_read_bytes;
        }
        None => {
            // If the process exited during the monitoring, we can still report
            // the read amount up to its exit, but it's important to note.
            println!(
                "Warning: Process with PID {} exited during the monitoring period.",
                pid
            );
            // In this case, we'll use the initial read bytes and assume no further reads
            // if the process wasn't found at the end. Or, you might choose to return an error.
            // For this example, we'll return an error to indicate it didn't run for the full duration.
            return Err(format!(
                "Process with PID {} exited during the monitoring period.",
                pid
            ));
        }
    }

    // Calculate the difference in total read bytes
    let bytes_read_during_period = final_total_read_bytes.saturating_sub(initial_total_read_bytes);

    // Convert bytes to Megabytes (1 MB = 1024 * 1024 bytes)
    let mb_read: f64 = bytes_read_during_period as f64 / (1024.0 * 1024.0);

    Ok(mb_read)
}

fn check_handler_readiness(
    is_sentinel: bool,
    redis_pool: &r2d2::Pool<redis::Client>,
) -> Result<bool, redis::RedisError> {
    if let Ok(mut con) = redis_pool.get() {
        if is_sentinel {
            return check_sentinel(&mut con);
        }

        let db_info: String = redis::cmd("INFO").query(&mut con)?;
        if db_info.contains("cluster_enabled:1") {
            return get_status_from_cluster_node_readiness(&mut con);
        }
        check_node_readiness(&db_info, &mut con)
    } else {
        Err(redis::RedisError::from((
            redis::ErrorKind::IoError,
            "Failed to get connection from pool",
        )))
    }
}

fn get_redis_connection_pool(
    is_sentinel: bool,
) -> Result<r2d2::Pool<redis::Client>, redis::RedisError> {
    let password = get_redis_password();
    let node_port = get_node_port(is_sentinel);
    let redis_url = get_redis_url(&password, &node_port);

    let client = redis::Client::open(redis_url).map_err(|err| {
        eprintln!("Failed to create Redis client: {}", err);
        err
    })?;

    let mut retries = 5;
    let retry_delay = Duration::from_secs(2);

    while retries > 0 {
        match r2d2::Pool::builder().max_size(1).build(client.clone()) {
            Ok(pool) => return Ok(pool),
            Err(err) => {
                eprintln!(
                    "Failed to create Redis connection pool: {}. Retries left: {}",
                    err,
                    retries - 1
                );
                retries -= 1;
                std::thread::sleep(retry_delay);
            }
        }
    }

    Err(redis::RedisError::from((
        redis::ErrorKind::IoError,
        "Failed to create connection pool after retries",
    )))
}

fn check_node_readiness(
    db_info: &str,
    con: &mut redis::Connection,
) -> Result<bool, redis::RedisError> {
    match get_redis_role(db_info)? {
        "master" => get_status_from_master_readiness(db_info, con),
        _ => get_status_from_slave_readiness(db_info, con),
    }
}

fn get_redis_password() -> String {
    env::var("ADMIN_PASSWORD").unwrap_or_else(|_| {
        std::fs::read_to_string("/run/secrets/adminpassword")
            .map(|s| s.trim().to_string())
            .unwrap_or_default()
    })
}

fn get_node_port(is_sentinel: bool) -> String {
    env::var(if is_sentinel {
        "SENTINEL_PORT"
    } else {
        "NODE_PORT"
    })
    .unwrap_or_else(|_| {
        if is_sentinel {
            "26379".to_string()
        } else {
            "6379".to_string()
        }
    })
}

fn get_redis_url(password: &str, node_port: &str) -> String {
    let tls = env::var("TLS").unwrap_or_default();
    let host = env::var("NODE_HOST").unwrap_or_else(|_| "localhost".to_string());

    if tls == "true" {
        resolve_host(&host);
        let node_port = env::var("RANDOM_NODE_PORT").unwrap_or_else(|_| node_port.to_string());
        format!("rediss://:{}@{}:{}", password, host, node_port)
    } else {
        format!("redis://:{}@localhost:{}", password, node_port)
    }
}

fn check_sentinel(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    Ok(redis::cmd("PING").query::<String>(con)? == "PONG")
}

fn get_redis_role(db_info: &str) -> Result<&str, redis::RedisError> {
    let role_regex = regex::Regex::new(r"role:(\w+)").unwrap();
    role_regex
        .captures(db_info)
        .and_then(|caps| caps.get(1).map(|m| m.as_str()))
        .ok_or_else(|| redis::RedisError::from((redis::ErrorKind::ResponseError, "Role not found")))
}

fn get_status_from_cluster_node_readiness(
    con: &mut redis::Connection,
) -> Result<bool, redis::RedisError> {
    Ok(redis::cmd("CLUSTER")
        .arg("INFO")
        .query::<String>(con)?
        .contains("cluster_state:ok"))
}

fn get_status_from_master_readiness(
    db_info: &str,
    con: &mut redis::Connection,
) -> Result<bool, redis::RedisError> {
    Ok(redis::cmd("PING").query::<String>(con)?.contains("PONG") && db_info.contains("loading:0"))
}

fn get_status_from_slave_readiness(
    db_info: &str,
    con: &mut redis::Connection,
) -> Result<bool, redis::RedisError> {
    Ok(redis::cmd("PING").query::<String>(con)?.contains("PONG")
        && db_info.contains("loading:0")
        && db_info.contains("master_link_status:up")
        && db_info.contains("master_sync_in_progress:0"))
}

fn resolve_host(host: &str) {
    let timeout = Duration::from_secs(300);
    let start_time = Instant::now();

    while start_time.elapsed() < timeout {
        if dns_lookup::lookup_host(host).is_ok() {
            return;
        }
        println!("Host not resolved yet!");
        std::thread::sleep(Duration::from_secs(1));
    }

    panic!("Failed to resolve host: {}", host);
}
