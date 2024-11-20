use dns_lookup;
use rouille::router;
use rouille::Response;
use rouille::Server;
use std::env;
use std::env::args;
use std::time;


fn main() {
    let args: Vec<String> = args().collect();

    if args.len() > 1 && args[1] == "sentinel" {
        start_health_check_server(true);
    } else {
        start_health_check_server(false);
    }
}

fn start_health_check_server(is_sentinel: bool) {
    let port = if is_sentinel {
        match env::var("HEALTH_CHECK_PORT_SENTINEL") {
            Ok(port) => port,
            Err(_) => "8082".to_string(),
        }
    } else {
        match env::var("HEALTH_CHECK_PORT") {
            Ok(port) => port,
            Err(_) => "8081".to_string(),
        }
    };

    let addr = format!("localhost:{}", port);

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

fn health_check_handler(is_sentinel: bool) -> Result<bool, redis::RedisError> {
    let password = match env::var("ADMIN_PASSWORD") {
        Ok(password) => password,
        Err(_) => {
            let path = "/run/secrets/adminpassword";
            std::fs::read_to_string(path).map(|s| s.trim().to_string()).unwrap_or_else(|_| "".to_string())
        },
    };

    let node_port = if is_sentinel {
        match env::var("SENTINEL_PORT") {
            Ok(port) => port,
            Err(_) => "26379".to_string(),
        }
    } else {
        match env::var("NODE_PORT") {
            Ok(port) => port,
            Err(_) => "6379".to_string(),
        }
    };

    let redis_url = match env::var("TLS") {
        Ok(tls) => {
            if tls == "true" {
                let url: String = env::var("NODE_HOST").unwrap();
                resolve_host(&url);
                format!("rediss://:{}@{}:{}", password, url, node_port)
            } else {
                format!("redis://:{}@localhost:{}", password, node_port)
            }
        }
        Err(_) => format!("redis://:{}@localhost:{}", password, node_port),
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
        get_status_from_master(db_info)
    } else {
        get_status_from_slave(db_info)
    }
}

fn get_status_from_cluster_node(
    db_info: String,
    con: &mut redis::Connection,
) -> Result<bool, redis::RedisError> {
    let cluster_info: String = redis::cmd("CLUSTER").arg("INFO").query(con)?;

    Ok(cluster_info.contains("cluster_state:ok"))
}

fn get_status_from_master(_db_info: String) -> Result<bool, redis::RedisError> {
    Ok(true)
}

fn get_status_from_slave(db_info: String) -> Result<bool, redis::RedisError> {
    if !db_info.contains("master_link_status:up") || db_info.contains("master_sync_in_progress:1") {
        return Ok(false);
    }

    Ok(true)
}

fn resolve_host(host: &str) {
    let mut resolved = false;
    let timeout = std::time::Duration::from_secs(300); // Total timeout: 150 seconds
    let start_time = std::time::Instant::now();

    while !resolved && start_time.elapsed() < timeout {
        match dns_lookup::lookup_host(host) {
            Ok(_) => resolved = true,
            Err(_) => {
                std::thread::sleep(std::time::Duration::from_secs(2));
            }
        }
    }

    if !resolved {
        panic!("Failed to resolve host: {}", host);
    }
}
