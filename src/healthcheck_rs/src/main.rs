use k8s_openapi::api::core::v1::ConfigMap;
use kube::{Api, Client};
use once_cell::sync::Lazy;
use std::collections::HashMap;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex, RwLock};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;
use std::time::{Duration, Instant};

// Example ConfigMap structure for health checks
/*
apiVersion: v1
kind: ConfigMap
metadata:
  name: health-config
  namespace: default
data:
  # Individual health check skip flags
  skip_all: "false"
  skip_liveness: "false"
  skip_readiness: "false"
  skip_startup: "true"

  # Alternative: JSON format (if you prefer structured config)
  config.json: |
    {
      "skip_all": false,
      "skip_liveness": false,
      "skip_readiness": false,
      "skip_startup": true                  
    }
*/

// Example command to create a ConfigMap in Kubernetes
/*
kubectl create configmap health-config \
  --from-literal=skip_all="false" \
  --from-literal=skip_liveness="false" \
  --from-literal=skip_readiness="false" \
  --from-literal=skip_startup="true" \
  --namespace=default
 */

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
struct HealthConfig {
    skip_all: Option<bool>,
    skip_liveness: Option<bool>,
    skip_readiness: Option<bool>,
    skip_startup: Option<bool>,
}
static TOKIO_RT: Lazy<tokio::runtime::Runtime> = Lazy::new(|| {
    // Must be multi_thread (even with 1 worker) so that hyper/kube background
    // tasks (connection-pool keep-alive, idle-connection eviction, TLS session
    // management, etc.) are driven continuously by the worker thread between
    // block_on calls.  With new_current_thread those tasks only run *inside*
    // block_on; between probe invocations they queue up and never drain, which
    // was the root cause of the observed memory leak.
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(1)
        .enable_all()
        .build()
        .expect("failed to build tokio runtime")
});

// Persistent kube client — keeps the connection warm and avoids a new TLS
// handshake on every cache miss.  This is safe because the multi_thread runtime
// above continuously drains the client's internal cleanup tasks.
static K8S_CLIENT: Lazy<RwLock<Option<kube::Client>>> = Lazy::new(|| RwLock::new(None));

// Single persistent Redis connection — all probes share exactly one TCP
// connection to Redis.  Protected by a Mutex so only one probe issues a Redis
// command at a time.  If the connection is broken the error path drops it so
// the next probe re-establishes it transparently via get_connection().
static REDIS_CONN: Lazy<Mutex<Option<redis::Connection>>> = Lazy::new(|| Mutex::new(None));

/// Runs `f` against the single shared Redis connection, reconnecting
/// automatically if the connection is broken.
fn with_redis_conn<T, F>(client: &redis::Client, f: F) -> Result<T, redis::RedisError>
where
    F: FnOnce(&mut redis::Connection) -> Result<T, redis::RedisError>,
{
    let mut guard = REDIS_CONN.lock().unwrap_or_else(|e| e.into_inner());
    if guard.is_none() {
        match client.get_connection() {
            Ok(conn) => *guard = Some(conn),
            Err(e) => {
                eprintln!("healthcheck: failed to connect to Redis: {:?}", e);
                return Err(e);
            }
        }
    }
    let conn = guard.as_mut().unwrap();
    match f(conn) {
        Ok(v) => Ok(v),
        Err(e) => {
            // Drop the connection so the next probe reconnects cleanly.
            *guard = None;
            Err(e)
        }
    }
}

static HEALTH_CONFIG_CACHE: Lazy<RwLock<Option<(Instant, HealthConfig)>>> =
    Lazy::new(|| RwLock::new(None));

// Compiled once at first use; regex::Regex::new is expensive and must not be
// called on every probe invocation.
static REDIS_ROLE_REGEX: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"role:(\w+)").expect("invalid role regex"));

const HEALTH_CONFIG_TTL: Duration = Duration::from_secs(5);

// --- Connection model (pre-threaded TCP server) ---
// We spin up exactly max_connections() OS threads, each blocking on accept()
// on a shared Arc<TcpListener>.  A connection is only accepted when a thread
// is free to handle it.  This means:
//
//   • At most max_connections() connections ever exist inside the process.
//   • Additional connections queue in the OS kernel backlog (default ~128).
//   • Beyond that the kernel issues TCP RST — the connection is rejected
//     before our process even knows it existed.
//   • There is NO extra in-process accept queue (unlike rouille + tiny_http
//     which runs a background accept loop that eagerly ingests all connections
//     into an unbounded work queue, causing the memory spike you observed).
//
// Known callers and their connection budgets:
//   - kubelet liveness / readiness / startup probes  → up to 3 connections
//   - monitor sidecar                                → up to 3 connections
//   - operational / debug tools                      → occasional 1-2 more
// Total expected max: ~10.  Default is set accordingly.
//
// Sizing for the default 30Mi container memory limit:
//   - Idle base:           ~7Mi
//   - 10 threads resident: ~0.5Mi
//   - Active work (10 simultaneous Redis/k8s calls): ~1.2Mi
//   - Total peak:          ~8.7Mi = ~29% of 30Mi  → safe headroom
//
// Override with MAX_CONNECTIONS env var if needed.
fn max_connections() -> usize {
    env::var("MAX_CONNECTIONS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(10)
}

// Application-layer concurrency cap — an additional guard that fires *before*
// handler logic runs (Redis / k8s calls).  Defaults to max_connections() since
// there is no benefit allowing more in-flight work than we have threads.
// Override with MAX_IN_FLIGHT env var if needed.
fn max_in_flight() -> usize {
    env::var("MAX_IN_FLIGHT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or_else(max_connections)
}

static IN_FLIGHT: AtomicUsize = AtomicUsize::new(0);

/// RAII guard: increments IN_FLIGHT on creation, always decrements on drop.
struct InFlightGuard;
impl Drop for InFlightGuard {
    fn drop(&mut self) {
        IN_FLIGHT.fetch_sub(1, Ordering::Relaxed);
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let is_sentinel = args.get(1).map_or(false, |arg| arg == "sentinel");
    start_health_check_server(is_sentinel);
}

fn start_health_check_server(is_sentinel: bool) {
    let redis_client = Arc::new(get_redis_client(is_sentinel).unwrap());
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
    let listener = Arc::new(TcpListener::bind(&addr).expect("failed to bind port"));
    println!(
        "Listening on {} (max_connections={}, max_in_flight={})",
        addr,
        max_connections(),
        max_in_flight()
    );

    // Spawn exactly max_connections() threads; each blocks on accept().
    // A new connection is only dequeued from the OS backlog when a thread is
    // free — no unbounded in-process queue, no memory accumulation.
    let handles: Vec<_> = (0..max_connections())
        .map(|_| {
            let listener = listener.clone();
            let redis_client = redis_client.clone();
            thread::spawn(move || loop {
                match listener.accept() {
                    Ok((stream, _)) => serve_connection(stream, is_sentinel, &redis_client),
                    Err(e) => eprintln!("healthcheck: accept error: {e}"),
                }
            })
        })
        .collect();

    for h in handles {
        let _ = h.join();
    }
}

/// Handle one TCP connection: parse the HTTP request line, dispatch to the
/// appropriate health-check handler, and write a single HTTP/1.1 response.
/// Always sets Connection: close so the thread is freed immediately after.
fn serve_connection(mut stream: TcpStream, is_sentinel: bool, redis_client: &redis::Client) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));

    // BufReader borrows stream mutably; the inner block limits the borrow so
    // we can write to stream afterwards.
    let path = {
        let mut reader = BufReader::new(&mut stream);
        let mut request_line = String::new();
        if reader.read_line(&mut request_line).unwrap_or(0) == 0 {
            return;
        }
        // Drain headers until the blank line separating headers from body.
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => return,
                Ok(_) if line == "\r\n" || line == "\n" => break,
                _ => {}
            }
        }
        // Extract path from "GET /path HTTP/1.x"
        request_line
            .splitn(3, ' ')
            .nth(1)
            .unwrap_or("")
            .to_owned()
    };

    let (status, body) = match path.as_str() {
        "/liveness" => handle_health_check_with_config(
            is_sentinel, check_handler_liveness, redis_client, "liveness",
        ),
        "/readiness" => handle_health_check_with_config(
            is_sentinel, check_handler_readiness, redis_client, "readiness",
        ),
        "/startup" => handle_health_check_with_config(
            is_sentinel, |_, _| Ok(true), redis_client, "startup",
        ),
        _ => (404u16, "Not Found"),
    };

    let reason = match status {
        200 => "OK",
        503 => "Service Unavailable",
        404 => "Not Found",
        _ => "Internal Server Error",
    };
    let _ = write!(
        stream,
        "HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
}

fn handle_health_check_with_config<F>(
    is_sentinel: bool,
    check_fn: F,
    redis_pool: &redis::Client,
    check_type: &str,
) -> (u16, &'static str)
where
    F: Fn(bool, &redis::Client) -> Result<bool, redis::RedisError>,
{
    // Check legacy environment variable first
    if env::var("SKIP_HEALTH_CHECK").as_deref() == Ok("true") {
        return (200, "OK");
    }

    // Concurrency cap — prevents unbounded memory growth under extreme probe
    // rates.  The guard decrements IN_FLIGHT automatically when it drops,
    // covering every return path below.
    let in_flight = IN_FLIGHT.fetch_add(1, Ordering::Relaxed);
    if in_flight >= max_in_flight() {
        IN_FLIGHT.fetch_sub(1, Ordering::Relaxed);
        eprintln!("healthcheck: overloaded ({}/{} in-flight), short-circuiting /{}", in_flight, max_in_flight(), check_type);
        // Liveness/startup: the process is alive, just busy — return OK so
        // kubelet does not kill us and make things worse.
        // Readiness: signal that we cannot serve right now.
        return match check_type {
            "readiness" => (503, "Overloaded"),
            _ => (200, "OK"),
        };
    }
    let _guard = InFlightGuard;

    // Check configmap overrides
    if let Ok(config) = get_health_config_from_configmap() {
        if config.skip_all.unwrap_or(false) {
            return (200, "OK");
        }
        match check_type {
            "liveness" if config.skip_liveness.unwrap_or(false) => return (200, "OK"),
            "readiness" if config.skip_readiness.unwrap_or(false) => return (200, "OK"),
            "startup" if config.skip_startup.unwrap_or(false) => return (200, "OK"),
            _ => {}
        }
    }

    match check_fn(is_sentinel, redis_pool) {
        Ok(true) => (200, "OK"),
        _ => (500, "Not ready"),
    }
}

fn get_health_config_from_configmap() -> Result<HealthConfig, Box<dyn std::error::Error>> {
    // Serve from cache if fresh
    if let Some((ts, cfg)) = HEALTH_CONFIG_CACHE.read().unwrap().as_ref() {
        if ts.elapsed() < HEALTH_CONFIG_TTL {
            return Ok(cfg.clone());
        }
    }

    let cfg = TOKIO_RT.block_on(async {
        let name = env::var("HEALTH_CONFIG_NAME").unwrap_or_else(|_| "health-config".into());
        let ns = get_namespace()?;

        // Bootstrap the persistent client once.
        {
            let mut guard = K8S_CLIENT.write().unwrap();
            if guard.is_none() {
                if let Ok(c) = Client::try_default().await {
                    *guard = Some(c);
                } else {
                    eprintln!("healthcheck: Failed to create Kubernetes client; using defaults.");
                    return Ok::<_, Box<dyn std::error::Error>>(HealthConfig::default());
                }
            }
        }

        let client = K8S_CLIENT.read().unwrap().as_ref().unwrap().clone();
        let api: Api<ConfigMap> = Api::namespaced(client, &ns);

        // Time-bound the GET to avoid hanging probes
        match tokio::time::timeout(Duration::from_secs(1), api.get(&name)).await {
            Ok(Ok(cm)) => {
                if let Some(data) = cm.data {
                    parse_health_config_from_data(data)
                } else {
                    Ok(HealthConfig::default())
                }
            }
            Ok(Err(e)) => {
                eprintln!("healthcheck: Kubernetes API error fetching ConfigMap \"{}\": {}; using defaults.", name, e);
                Ok(HealthConfig::default())
            }
            Err(e) => {
                eprintln!("healthcheck: timed out fetching ConfigMap \"{}\": {}; using defaults.", name, e);
                Ok(HealthConfig::default())
            }
        }
    })?;

    *HEALTH_CONFIG_CACHE.write().unwrap() = Some((Instant::now(), cfg.clone()));
    Ok(cfg)
}

fn get_namespace() -> Result<String, Box<dyn std::error::Error>> {
    // First try to read from the service account token
    match std::fs::read_to_string("/var/run/secrets/kubernetes.io/serviceaccount/namespace") {
        Ok(namespace) => Ok(namespace.trim().to_string()),
        Err(_) => {
            // Fallback to environment variable if file doesn't exist (e.g., running outside k8s)
            env::var("NAMESPACE").or_else(|_| Ok("default".to_string()))
        }
    }
}

fn parse_health_config_from_data(
    data: BTreeMap<String, String>,
) -> Result<HealthConfig, Box<dyn std::error::Error>> {
    let mut config = HealthConfig::default();

    // Parse boolean values from configmap data
    if let Some(skip_all_str) = data.get("skip_all") {
        config.skip_all = Some(skip_all_str.trim().to_lowercase() == "true");
    }

    if let Some(skip_liveness_str) = data.get("skip_liveness") {
        config.skip_liveness = Some(skip_liveness_str.trim().to_lowercase() == "true");
    }

    if let Some(skip_readiness_str) = data.get("skip_readiness") {
        config.skip_readiness = Some(skip_readiness_str.trim().to_lowercase() == "true");
    }

    if let Some(skip_startup_str) = data.get("skip_startup") {
        config.skip_startup = Some(skip_startup_str.trim().to_lowercase() == "true");
    }

    // If a JSON blob is present under "config.json" it takes full precedence
    // over the individual keys parsed above — the entire struct is replaced.
    // Use one format or the other; mixing both means individual keys are ignored.
    if let Some(json_config) = data.get("config.json") {
        match serde_json::from_str::<HealthConfig>(json_config) {
            Ok(parsed_config) => return Ok(parsed_config),
            Err(e) => eprintln!("healthcheck: failed to parse config.json, falling back to individual keys: {}", e),
        }
    }

    Ok(config)
}

fn check_handler_liveness(_: bool, redis_client: &redis::Client) -> Result<bool, redis::RedisError> {
    with_redis_conn(redis_client, |conn| {
        match redis::cmd("PING").query::<String>(conn) {
            Ok(value) => {
                if value.contains("PONG") || value.contains("BUSY") || value.contains("LOADING") {
                    Ok(true)
                } else {
                    eprintln!("Unexpected PING response: {}", value);
                    Err(redis::RedisError::from((
                        redis::ErrorKind::ResponseError,
                        "Unexpected PING response",
                    )))
                }
            }
            Err(error) => {
                if error.kind() == redis::ErrorKind::BusyLoadingError {
                    eprintln!("Redis is busy loading data, returning true for liveness check.");
                    // Return Ok so with_redis_conn keeps the connection alive.
                    return Ok(true);
                }
                eprintln!("Failed to send PING command: {:?}", error);
                Err(redis::RedisError::from((
                    redis::ErrorKind::IoError,
                    "Failed to send PING command",
                )))
            }
        }
    })
}

fn check_handler_readiness(
    is_sentinel: bool,
    redis_client: &redis::Client,
) -> Result<bool, redis::RedisError> {
    with_redis_conn(redis_client, |con| {
        if is_sentinel {
            return check_sentinel(con);
        }
        let db_info: String = redis::cmd("INFO").query(con)?;
        if db_info.contains("cluster_enabled:1") {
            return get_status_from_cluster_node_readiness(con);
        }
        check_node_readiness(&db_info, con)
    })
}

fn get_redis_client(is_sentinel: bool) -> Result<redis::Client, redis::RedisError> {
    let password = get_redis_password();
    let node_port = get_node_port(is_sentinel);
    let redis_url = get_redis_url(&password, &node_port);

    redis::Client::open(redis_url).map_err(|err| {
        eprintln!("Failed to create Redis client: {}", err);
        err
    })
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
    // required for the sentinel only pod to allow the other pods to be scheduled
    if env::var("SENTINEL_IGNORE_MASTER_CHECK").as_deref() == Ok("true") {
        return Ok(true);
    }

    // check that it has a master
    let master_info: Vec<HashMap<String, String>> = redis::cmd("SENTINEL")
        .arg("masters")
        .query(con)
        .map_err(|err| {
            eprintln!("Failed to get sentinel masters: {}", err);
            err
        })?;
    if master_info.is_empty() {
        eprintln!("No master found in sentinel");
        return Err(redis::RedisError::from((
            redis::ErrorKind::ResponseError,
            "No master found in sentinel",
        )));
    }

    Ok(true)
}

fn get_redis_role(db_info: &str) -> Result<&str, redis::RedisError> {
    REDIS_ROLE_REGEX
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
