use rouille::{router, Response, Server};
use std::env;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn main() {
    let args: Vec<String> = env::args().collect();
    let is_sentinel = args.get(1).map_or(false, |arg| arg == "sentinel");
    println!("Starting health check server. Is sentinel: {}", is_sentinel);
    start_health_check_server(is_sentinel);
}

fn start_health_check_server(is_sentinel: bool) {
    let port = env::var(if is_sentinel { "HEALTH_CHECK_PORT_SENTINEL" } else { "HEALTH_CHECK_PORT" })
        .unwrap_or_else(|_| if is_sentinel { "8082".to_string() } else { "8081".to_string() });

    let addr = format!("localhost:{}", port);
    println!("Server address: {}", addr);
    let redis_connection = Arc::new(Mutex::new(get_redis_connection(is_sentinel).unwrap()));

    let server = Server::new(addr, move |request| {
        let redis_connection = Arc::clone(&redis_connection);
        router!(request,
            (GET) (/liveness) => { 
                println!("Received liveness check request");
                let response = handle_health_check(is_sentinel, check_handler_liveness, &redis_connection);
                println!("Response: {:?}", response);
                response
            },
            (GET) (/readiness) => { 
                println!("Received readiness check request");
                let response = handle_health_check(is_sentinel, check_handler_readiness, &redis_connection);
                println!("Response: {:?}", response);
                response
            },
            (GET) (/startup) => { 
                println!("Received startup check request");
                let response = Response::text("OK");
                println!("Response: {:?}", response);
                response
            },
            _ => {
                let response = Response::empty_404();
                println!("Response: {:?}", response);
                response
            }
        )
    }).unwrap();

    println!("Listening on {}", server.server_addr());
    server.run();
}

fn handle_health_check<F>(is_sentinel: bool, check_fn: F, redis_connection: &Arc<Mutex<redis::Connection>>) -> Response
where
    F: Fn(bool, &Arc<Mutex<redis::Connection>>) -> Result<bool, redis::RedisError>,
{
    match check_fn(is_sentinel, redis_connection) {
        Ok(true) => {
            println!("Health check passed");
            let response = Response::text("OK");
            println!("Response: {:?}", response);
            response
        },
        _ => {
            println!("Health check failed");
            let response = Response::text("Not ready").with_status_code(500);
            println!("Response: {:?}", response);
            response
        },
    }
}

fn check_handler_liveness(is_sentinel: bool, redis_connection: &Arc<Mutex<redis::Connection>>) -> Result<bool, redis::RedisError> {
    println!("Checking liveness. Is sentinel: {}", is_sentinel);
    let mut con = redis_connection.lock().unwrap();
    if is_sentinel {
        return check_sentinel(&mut con);
    }

    let db_info: String = redis::cmd("INFO").query(&mut *con)?;
    println!("DB Info: {}", db_info);
    if db_info.contains("cluster_enabled:1") {
        return get_status_from_cluster_node_liveness(&mut *con);
    }
    check_node_liveness(&db_info, &mut *con)
}

fn check_handler_readiness(is_sentinel: bool, redis_connection: &Arc<Mutex<redis::Connection>>) -> Result<bool, redis::RedisError> {
    println!("Checking readiness. Is sentinel: {}", is_sentinel);
    let mut con = redis_connection.lock().unwrap();
    if is_sentinel {
        return check_sentinel(&mut con);
    }

    let db_info: String = redis::cmd("INFO").query(&mut *con)?;
    println!("DB Info: {}", db_info);
    if db_info.contains("cluster_enabled:1") {
        return get_status_from_cluster_node_readiness(&mut *con);
    }
    check_node_readiness(&db_info, &mut *con)
}

fn get_redis_connection(is_sentinel: bool) -> Result<redis::Connection, redis::RedisError> {
    let password = get_redis_password();
    let node_port = get_node_port(is_sentinel);
    let redis_url = get_redis_url(&password, &node_port);

    println!("Connecting to Redis at URL: {}", redis_url);
    let client = redis::Client::open(redis_url)?;
    client.get_connection()
}

fn check_node_liveness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking node liveness");
    match get_redis_role(db_info)? {
        "master" => get_status_from_master_liveness(con),
        _ => get_status_from_slave_liveness(con),
    }
}

fn check_node_readiness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking node readiness");
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
    env::var(if is_sentinel { "SENTINEL_PORT" } else { "NODE_PORT" })
        .unwrap_or_else(|_| if is_sentinel { "26379".to_string() } else { "6379".to_string() })
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
    println!("Checking sentinel");
    let cmd = "PING";
    match redis::cmd(cmd).query::<String>(con) {
        Ok(result) => {
            println!("Command {} succeeded", cmd);
            Ok(result == "PONG")
        },
        Err(err) => {
            println!("Command {} failed: {:?}", cmd, err);
            Err(err)
        }
    }
}

fn get_redis_role(db_info: &str) -> Result<&str, redis::RedisError> {
    let role_regex = regex::Regex::new(r"role:(\w+)").unwrap();
    role_regex.captures(db_info)
        .and_then(|caps| caps.get(1).map(|m| m.as_str()))
        .ok_or_else(|| redis::RedisError::from((redis::ErrorKind::ResponseError, "Role not found")))
}

fn get_status_from_cluster_node_liveness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking cluster node liveness");
    let cmd = "CLUSTER INFO";
    match redis::cmd("CLUSTER").arg("INFO").query::<String>(con) {
        Ok(result) => {
            println!("Command {} succeeded", cmd);
            Ok(result.contains("cluster_state:ok"))
        },
        Err(err) => {
            println!("Command {} failed: {:?}", cmd, err);
            if err.kind() == redis::ErrorKind::BusyLoadingError {
                Ok(true)
            } else {
                Ok(false)
            }
        }
    }
}

fn get_status_from_cluster_node_readiness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking cluster node readiness");
    let cmd = "CLUSTER INFO";
    match redis::cmd("CLUSTER").arg("INFO").query::<String>(con) {
        Ok(result) => {
            println!("Command {} succeeded", cmd);
            Ok(result.contains("cluster_state:ok"))
        },
        Err(err) => {
            println!("Command {} failed: {:?}", cmd, err);
            Err(err)
        }
    }
}

fn get_status_from_master_liveness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking master liveness");
    let cmd = "PING";
    match redis::cmd(cmd).query::<String>(con) {
        Ok(result) => {
            println!("Command {} succeeded", cmd);
            Ok(result.contains("PONG"))
        },
        Err(err) => {
            println!("Command {} failed: {:?}", cmd, err);
            if err.kind() == redis::ErrorKind::BusyLoadingError {
                Ok(true)
            } else {
                Ok(false)
            }
        }
    }
}

fn get_status_from_master_readiness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking master readiness");
    let cmd = "PING";
    match redis::cmd(cmd).query::<String>(con) {
        Ok(result) => {
            println!("Command {} succeeded", cmd);
            Ok(result.contains("PONG") && db_info.contains("loading:0"))
        },
        Err(err) => {
            println!("Command {} failed: {:?}", cmd, err);
            Err(err)
        }
    }
}

fn get_status_from_slave_liveness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking slave liveness");
    let cmd = "PING";
    match redis::cmd(cmd).query::<String>(con) {
        Ok(result) => {
            println!("Command {} succeeded", cmd);
            Ok(result.contains("PONG"))
        },
        Err(err) => {
            println!("Command {} failed: {:?}", cmd, err);
            if err.kind() == redis::ErrorKind::BusyLoadingError || err.kind() == redis::ErrorKind::MasterDown {
                Ok(true)
            } else {
                Ok(false)
            }
        }
    }
}

fn get_status_from_slave_readiness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    println!("Checking slave readiness");
    let cmd = "PING";
    match redis::cmd(cmd).query::<String>(con) {
        Ok(result) => {
            println!("Command {} succeeded", cmd);
            Ok(result.contains("PONG")
                && db_info.contains("loading:0")
                && db_info.contains("master_link_status:up")
                && db_info.contains("master_sync_in_progress:0"))
        },
        Err(err) => {
            println!("Command {} failed: {:?}", cmd, err);
            Err(err)
        }
    }
}

fn resolve_host(host: &str) {
    let timeout = Duration::from_secs(300);
    let start_time = Instant::now();

    while start_time.elapsed() < timeout {
        if dns_lookup::lookup_host(host).is_ok() {
            println!("Host resolved: {}", host);
            return;
        }
        println!("Host not resolved yet!");
        std::thread::sleep(Duration::from_secs(1));
    }

    panic!("Failed to resolve host: {}", host);
}
