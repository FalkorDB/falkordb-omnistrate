use k8s_openapi::api::core::v1::ConfigMap;
use kube::{Api, Client}; // Removed unused Config import
use rouille::{router, Response, Server};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::env;
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

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
struct HealthConfig {
    skip_all: Option<bool>,
    skip_liveness: Option<bool>,
    skip_readiness: Option<bool>,
    skip_startup: Option<bool>,
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let is_sentinel = args.get(1).map_or(false, |arg| arg == "sentinel");
    start_health_check_server(is_sentinel);
}

fn start_health_check_server(is_sentinel: bool) {
    let redis_client = get_redis_client(is_sentinel).unwrap();
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
            (GET) (/liveness) => { handle_health_check_with_config(is_sentinel, check_handler_liveness, &redis_client, "liveness") },
            (GET) (/readiness) => { handle_health_check_with_config(is_sentinel, check_handler_readiness, &redis_client, "readiness") },
            (GET) (/startup) => { handle_health_check_with_config(is_sentinel, |_, _| Ok(true), &redis_client, "startup") },
            _ => Response::empty_404()
        )
    }).unwrap();

    println!("Listening on {}", server.server_addr());
    server.run();
}

fn handle_health_check_with_config<F>(
    is_sentinel: bool,
    check_fn: F,
    redis_pool: &redis::Client,
    check_type: &str,
) -> Response
where
    F: Fn(bool, &redis::Client) -> Result<bool, redis::RedisError>,
{
    // Check legacy environment variable first
    if env::var("SKIP_HEALTH_CHECK").as_deref() == Ok("true") {
        return Response::text("OK");
    }

    // Check configmap overrides
    if let Ok(config) = get_health_config_from_configmap() {
        if config.skip_all.unwrap_or(false) {
            return Response::text("OK");
        }

        match check_type {
            "liveness" if config.skip_liveness.unwrap_or(false) => return Response::text("OK"),
            "readiness" if config.skip_readiness.unwrap_or(false) => return Response::text("OK"),
            "startup" if config.skip_startup.unwrap_or(false) => return Response::text("OK"),
            _ => {}
        }
    }

    match check_fn(is_sentinel, redis_pool) {
        Ok(true) => Response::text("OK"),
        _ => Response::text("Not ready").with_status_code(500),
    }
}

fn get_health_config_from_configmap() -> Result<HealthConfig, Box<dyn std::error::Error>> {
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async {
        let config_name =
            env::var("HEALTH_CONFIG_NAME").unwrap_or_else(|_| "health-config".to_string());
        let namespace = get_namespace()?;

        let client = match Client::try_default().await {
            Ok(client) => client,
            Err(_) => return Err("Failed to create Kubernetes client".into()),
        };

        let configmaps: Api<ConfigMap> = Api::namespaced(client, &namespace);

        match configmaps.get(&config_name).await {
            Ok(configmap) => {
                if let Some(data) = configmap.data {
                    parse_health_config_from_data(data)
                } else {
                    Ok(HealthConfig::default())
                }
            }
            Err(_) => {
                // ConfigMap doesn't exist, return default config
                Ok(HealthConfig::default())
            }
        }
    })
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

    // Alternatively, if you want to support JSON format in the configmap:
    if let Some(json_config) = data.get("config.json") {
        if let Ok(parsed_config) = serde_json::from_str::<HealthConfig>(json_config) {
            return Ok(parsed_config);
        }
    }

    Ok(config)
}

fn check_handler_liveness(_: bool, redis_pool: &redis::Client) -> Result<bool, redis::RedisError> {
    let connection = redis_pool.get_connection();

    match connection {
        Ok(mut conn) => {
            let response: redis::RedisResult<String> = redis::cmd("PING").query(&mut conn);

            if response.is_err() {
                let error = response.err().unwrap();

                if error.kind() == redis::ErrorKind::BusyLoadingError {
                    eprintln!("Redis is busy loading data, returning true for liveness check.");
                    return Ok(true);
                }

                eprintln!("Failed to send PING command: {:?}", error);
                return Err(redis::RedisError::from((
                    redis::ErrorKind::IoError,
                    "Failed to send PING command",
                )));
            }

            let value = response.as_ref().unwrap();

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
        Err(err) => {
            eprintln!("Failed to get connection: {:?}", err);
            Err(redis::RedisError::from((
                redis::ErrorKind::IoError,
                "Failed to get connection",
            )))
        }
    }
}

fn check_handler_readiness(
    is_sentinel: bool,
    redis_pool: &redis::Client,
) -> Result<bool, redis::RedisError> {
    if let Ok(mut con) = redis_pool.get_connection() {
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
            "Failed to get connection",
        )))
    }
}

fn get_redis_client(is_sentinel: bool) -> Result<redis::Client, redis::RedisError> {
    let password = get_redis_password();
    let node_port = get_node_port(is_sentinel);
    let redis_url = get_redis_url(&password, &node_port);

    let client = redis::Client::open(redis_url).map_err(|err| {
        eprintln!("Failed to create Redis client: {}", err);
        err
    })?;

    return Ok(client);
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
