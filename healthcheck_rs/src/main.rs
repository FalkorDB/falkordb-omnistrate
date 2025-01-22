use rouille::router;
use rouille::Response;
use rouille::Server;
use std::env;
use std::env::args;
use std::time::{Duration, Instant};

fn main() {
    let args: Vec<String> = args().collect();

    if args.len() > 1 && args[1] == "sentinel" {
        start_health_check_server(true);
    } else {
        start_health_check_server(false);
    }
}

/// Starts the health check server
/// The server listens on the port specified by the `HEALTH_CHECK_PORT` environment variable.
/// If the `HEALTH_CHECK_PORT` environment variable is not set, the server listens on port 8081.
/// The server responds to the `/healthcheck` endpoint.
/// The server returns a 200 status code with the body "OK" if the health check is successful.
/// The server returns a 500 status code with the body "Not ready" if the health check is unsuccessful.
/// The health check is successful if the Redis server is ready to accept connections.
/// 
/// # Arguments
/// 
/// * `is_sentinel` - A boolean that indicates whether the health check server is for a Redis Sentinel instance.
fn start_health_check_server(is_sentinel: bool) {
    let port = if is_sentinel {
        env::var("HEALTH_CHECK_PORT_SENTINEL").unwrap_or_else(|_| "8082".to_string())
    } else {
        env::var("HEALTH_CHECK_PORT").unwrap_or_else(|_| "8081".to_string())
    };

    let addr = format!("localhost:{port}");

    let server = Server::new(addr, move |request| {
        router!(request,
            (GET) (/healthcheck) => {
                let health = health_check_handler(is_sentinel).unwrap();

                if health {
                    Response::text("OK")
                } else {
                    Response::text("Not ready").with_status_code(500)
                }
            },
            _ => Response::empty_404()
        )
    })
    .unwrap();
    println!("Listening on {}", server.server_addr());
    server.run();
}


/// Checks the health of the Redis server.
/// The function connects to the Redis server using the `ADMIN_PASSWORD`, `NODE_HOST`, `NODE_PORT`, and `TLS` environment variables.
/// If the `ADMIN_PASSWORD` environment variable is not set, the function reads the password from the `/run/secrets/adminpassword` file.
/// If the `TLS` environment variable is set to "true", the function connects to the Redis server using the rediss:// scheme.
/// The function sends a PING command to the Redis server to check if it is ready to accept connections.
/// If the Redis server is a Sentinel instance, the function sends a PING command to the Sentinel instance.
/// If the Redis server is a cluster node, the function sends a CLUSTER INFO command to the Redis server.
/// 
/// # Arguments
/// 
/// * `is_sentinel` - A boolean that indicates whether the Redis server is a Sentinel instance.
/// 
/// # Returns
/// 
/// A boolean value that indicates whether the Redis server is ready to accept connections.
/// 
/// # Errors
/// 
/// The function returns a RedisError if there is an error connecting to the Redis server.
fn health_check_handler(is_sentinel: bool) -> Result<bool, redis::RedisError> {
    let password = match env::var("ADMIN_PASSWORD") {
        Ok(password) => password,
        Err(_) => {
            let path = "/run/secrets/adminpassword";
            std::fs::read_to_string(path)
                .map(|s| s.trim().to_string())
                .unwrap_or_else(|_| String::new())
        }
    };

    let node_port = if is_sentinel {
        env::var("SENTINEL_PORT").unwrap_or_else(|_| "26379".to_string())
    } else {
        env::var("NODE_PORT").unwrap_or_else(|_| "6379".to_string())
    };

    let redis_url = match env::var("TLS") {
        Ok(tls) => {
            if tls == "true" {
                let url: String = env::var("NODE_HOST").unwrap();
                resolve_host(&url);
                format!("rediss://:{password}@{url}:{node_port}")
            } else {
                format!("redis://:{password}@localhost:{node_port}")
            }
        }
        Err(_) => format!("redis://:{password}@localhost:{node_port}"),
    };

    let client: redis::Client = redis::Client::open(redis_url)?;

    let mut con = client.get_connection()?;

    if is_sentinel {
        let sentinel_info: String = redis::cmd("PING").query(&mut con)?;
        return Ok(sentinel_info == "PONG");
    }

    let db_info: String = redis::cmd("INFO").query(&mut con)?;
    let is_cluster = db_info.contains("cluster_enabled:1");

    if is_cluster {
        return get_status_from_cluster_node(db_info, &mut con);
    }

    let role_regex = regex::Regex::new(r"role:(\w+)").unwrap();
    let role_matches = role_regex.captures(&db_info);

    if role_matches.is_none() {
        return Ok(false);
    }

    let role = role_matches.unwrap().get(1).unwrap().as_str();

    if role == "master" {
        get_status_from_master(&db_info)
    } else {
        get_status_from_slave(&db_info)
    }
}

/// Checks the status of the Redis cluster node.
/// The function checks the `cluster_state` field in the Redis CLUSTER INFO output.
/// If the `cluster_state` field is "ok", the function returns true.
/// Otherwise, the function returns false.
/// 
/// # Arguments
/// 
/// * `db_info` - A string slice that represents the Redis CLUSTER INFO output.
/// * `con` - A mutable reference to a Redis connection.
/// 
/// # Returns
/// 
/// A boolean value that indicates whether the Redis cluster node is ready
/// 
/// # Errors
/// 
/// The function returns a RedisError if there is an error querying the Redis server.
fn get_status_from_cluster_node(
    _db_info: String,
    con: &mut redis::Connection,
) -> Result<bool, redis::RedisError> {
    let cluster_info: String = redis::cmd("CLUSTER").arg("INFO").query(con)?;

    Ok(cluster_info.contains("cluster_state:ok"))
}

/// Checks the status of the Redis master.
/// The function checks the `role` field in the Redis INFO output.
/// If the `role` field is "master", the function returns true.
/// Otherwise, the function returns false.
/// 
/// # Arguments
/// 
/// * `db_info` - A string slice that represents the Redis INFO output.
/// 
/// # Returns
/// 
/// A boolean value that indicates whether the Redis master is ready
fn get_status_from_master(_db_info: &str) -> Result<bool, redis::RedisError> {
    Ok(true)
}

/// Checks the status of the Redis slave.
/// The function checks the `master_link_status` and `master_sync_in_progress` fields in the Redis INFO output.
/// If the `master_link_status` field is not "up" or the `master_sync_in_progress` field is "1", the function returns false.
/// Otherwise, the function returns true.
/// 
/// # Arguments
/// 
/// * `db_info` - A string slice that represents the Redis INFO output.
/// 
/// # Returns
/// 
/// A boolean value that indicates whether the Redis slave is ready
fn get_status_from_slave(db_info: &str) -> Result<bool, redis::RedisError> {
    if !db_info.contains("master_link_status:up") || db_info.contains("master_sync_in_progress:1") {
        return Ok(false);
    }

    Ok(true)
}

/// Resolves the host using the dns_lookup crate.
/// The function retries resolving the host every second until the host is resolved or the total timeout of 300 seconds is reached.
/// 
/// # Arguments
/// 
/// * `host` - A string slice that represents the host to resolve.
/// 
/// # Panics
/// 
/// The function panics if the host is not resolved within 300 seconds.
fn resolve_host(host: &str) {
    let mut resolved = false;
    let timeout = Duration::from_secs(300); // Total timeout: 300 seconds
    let start_time = Instant::now();

    while !resolved && start_time.elapsed() < timeout {
        match dns_lookup::lookup_host(host) {
            Ok(_) => resolved = true,
            Err(_) => {
                println!("Host not resolved yet!");
                std::thread::sleep(Duration::from_secs(1));
            }
        }
    }

    assert!(resolved, "Failed to resolve host: {host}");
}
