use dns_lookup;
use rouille::router;
use rouille::Response;
use rouille::Server;
use std::env;
use std::env::args;

fn main() {
    start_health_check_server();
}

fn start_health_check_server() {
    let args: Vec<String> = env::args().collect();
    if args.len() > 1 && args[1] == "sentinel" {
        let port = match env::var("HEALTH_CHECK_PORT_SENTINEL") {
            Ok(port) => port,
            Err(_) => "8082".to_string(),
        };
    } else {
        let port = match env::var("HEALTH_CHECK_PORT") {
            Ok(port) => port,
            Err(_) => "8081".to_string(),
        };
    }

    let addr = format!("localhost:{}", port);

    let server = Server::new(addr, |request| {
        router!(request,
            (GET) (/healthcheck) => {
              let health = health_check_handler().unwrap();

                if health.eq(&true) {
                    Response::text("OK")
                } else {
                    Response::text("Not ready").with_status_code(500)
                }
            },
            _ => Response::empty_404()
        )
    })
    .unwrap();
    println!("Listening on {:?}", server.server_addr());
    server.run();
}

fn health_check_handler() -> Result<bool, redis::RedisError> {
    let password = match env::var("ADMIN_PASSWORD") {
        Ok(password) => password,
        Err(_) => "".to_string(),
    };
    if args.len() > 1 && args[1] == "sentinel" {
        let node_port = match env::var("SENTINEL_PORT") {
            Ok(port) => port,
            Err(_) => "26379".to_string(),
        };
    } else {
        let node_port = match env::var("NODE_PORT") {
            Ok(port) => port,
            Err(_) => "6379".to_string(),
        };
    }
    let redis_url = match env::var("TLS") {
        Ok(tls) => {
            if tls == "true" {
                let url = env::var("NODE_EXTERNAL_DNS").unwrap();
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

    // Get persistence info

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

    let role = role_regex
        .captures(&db_info)
        .unwrap()
        .get(1)
        .unwrap()
        .as_str();

    if role == "master" {
        return get_status_from_master(db_info);
    }

    return get_status_from_slave(db_info);
}

fn get_status_from_cluster_node(
    db_info: String,
    con: &mut redis::Connection,
) -> Result<bool, redis::RedisError> {
    let cluster_info: String = redis::cmd("CLUSTER").arg("INFO").query(con)?;

    if !cluster_info.contains("cluster_state:ok") {
        return Ok(false);
    }

    return Ok(true);
}

fn get_status_from_master(db_info: String) -> Result<bool, redis::RedisError> {
    return Ok(true);
}

fn get_status_from_slave(db_info: String) -> Result<bool, redis::RedisError> {
    if !db_info.contains("master_link_status:up") {
        return Ok(false);
    }

    if !db_info.contains("master_sync_in_progress:0") {
        return Ok(false);
    }

    return Ok(true);
}

fn resolve_host(host: &str) {
    // Wait until host is resolved to an IP
    let mut resolved = false;
    while !resolved {
        let ip = match dns_lookup::lookup_host(host) {
            Ok(ip) => ip,
            Err(_) => {
                std::thread::sleep(std::time::Duration::from_secs(1));
                continue;
            }
        };
        resolved = true;
    }
}
