use rouille::{router, Response, Server};
use std::env;
use std::time::{Duration, Instant};

fn main() {
    let args: Vec<String> = env::args().collect();
    let is_sentinel = args.get(1).map_or(false, |arg| arg == "sentinel");
    start_health_check_server(is_sentinel);
}

fn start_health_check_server(is_sentinel: bool) {
    let redis_pool = get_redis_connection_pool(is_sentinel).unwrap();
    let port = env::var(if is_sentinel { "HEALTH_CHECK_PORT_SENTINEL" } else { "HEALTH_CHECK_PORT" })
        .unwrap_or_else(|_| if is_sentinel { "8082".to_string() } else { "8081".to_string() });

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

fn handle_health_check<F>(is_sentinel: bool, check_fn: F, redis_pool: &r2d2::Pool<redis::Client>) -> Response
where
    F: Fn(bool,&r2d2::Pool<redis::Client>) -> Result<bool, redis::RedisError>,
{
    if env::var("SKIP_HEALTH_CHECK").as_deref() == Ok("true") {
        return Response::text("OK");
    }
    match check_fn(is_sentinel,redis_pool) {
        Ok(true) => Response::text("OK"),
        _ => Response::text("Not ready").with_status_code(500),
    }
}

fn check_handler_liveness(is_sentinel: bool,redis_pool: &r2d2::Pool<redis::Client>) -> Result<bool, redis::RedisError> {
    if let Ok(mut con) = redis_pool.get(){
        if is_sentinel {
            return check_sentinel(&mut con);
        }
    
        let db_info: String = redis::cmd("INFO").query(&mut con)?;
        if db_info.contains("cluster_enabled:1") {
            return get_status_from_cluster_node_liveness(&mut con);
        }
        check_node_liveness(&db_info, &mut con)
    } else {
        Err(redis::RedisError::from((redis::ErrorKind::IoError, "Failed to get connection from pool")))
    }
}

fn check_handler_readiness(is_sentinel: bool,redis_pool: &r2d2::Pool<redis::Client>) -> Result<bool, redis::RedisError> {
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
        Err(redis::RedisError::from((redis::ErrorKind::IoError, "Failed to get connection from pool")))
    }
    
}

fn get_redis_connection_pool(is_sentinel: bool) -> Result<r2d2::Pool<redis::Client>, redis::RedisError> {
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
                    err, retries - 1
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

fn check_node_liveness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    match get_redis_role(db_info)? {
        "master" => get_status_from_master_liveness(con),
        _ => get_status_from_slave_liveness(con),
    }
}

fn check_node_readiness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
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
    Ok(redis::cmd("PING").query::<String>(con)? == "PONG")
}

fn get_redis_role(db_info: &str) -> Result<&str, redis::RedisError> {
    let role_regex = regex::Regex::new(r"role:(\w+)").unwrap();
    role_regex.captures(db_info)
        .and_then(|caps| caps.get(1).map(|m| m.as_str()))
        .ok_or_else(|| redis::RedisError::from((redis::ErrorKind::ResponseError, "Role not found")))
}

fn get_status_from_cluster_node_liveness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    match redis::cmd("CLUSTER").arg("INFO").query::<String>(con) {
        Ok(result) if result.contains("cluster_state:ok") => Ok(true),
        Err(err) if err.kind() == redis::ErrorKind::BusyLoadingError => Ok(true),
        _ => Ok(false),
    }
}

fn get_status_from_cluster_node_readiness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    Ok(redis::cmd("CLUSTER").arg("INFO").query::<String>(con)?.contains("cluster_state:ok"))
}

fn get_status_from_master_liveness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    match redis::cmd("PING").query::<String>(con) {
        Ok(result) if result.contains("PONG") => Ok(true),
        Err(err) if err.kind() == redis::ErrorKind::BusyLoadingError => Ok(true),
        _ => Ok(false),
    }
}

fn get_status_from_master_readiness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    Ok(redis::cmd("PING").query::<String>(con)?.contains("PONG") && db_info.contains("loading:0"))
}

fn get_status_from_slave_liveness(con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
    match redis::cmd("PING").query::<String>(con) {
        Ok(result) if result.contains("PONG") => Ok(true),
        Err(err) if err.kind() == redis::ErrorKind::BusyLoadingError || err.kind() == redis::ErrorKind::MasterDown => Ok(true),
        _ => Ok(false),
    }
}

fn get_status_from_slave_readiness(db_info: &str, con: &mut redis::Connection) -> Result<bool, redis::RedisError> {
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
