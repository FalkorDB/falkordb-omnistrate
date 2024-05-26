use rouille::router;
use rouille::Response;
use rouille::Server;
use std::env;

fn main() {
    start_health_check_server();
}

fn start_health_check_server() {
    let port = match env::var("HEALTH_CHECK_PORT") {
        Ok(port) => port,
        Err(_) => "8081".to_string(),
    };
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

    let node_port = match env::var("NODE_PORT") {
        Ok(port) => port,
        Err(_) => "6379".to_string(),
    };

    let redis_url = match env::var("TLS") {
        Ok(tls) => {
            if tls == "true" {
                let url = env::var("NODE_EXTERNAL_DNS").unwrap();
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

    let role_regex = regex::Regex::new(r"role:(\w+)").unwrap();
    let role_matches = role_regex.captures(&db_info);

    if role_matches.is_none() {
        return Ok(false);
    }

    let role = role_regex.captures(&db_info).unwrap().get(1).unwrap().as_str();

    if role == "master" {
        return Ok(true);
    }

    // If role:slave, check if master_sync_in_progress:0
    let master_sync_in_progress_regex = regex::Regex::new(r"master_sync_in_progress:(\d+)").unwrap();
    let sync_matches = master_sync_in_progress_regex.captures(&db_info);

    if sync_matches.is_none() {
        return Ok(false);
    }

    let master_sync_in_progress = sync_matches.unwrap().get(1).unwrap().as_str();

    if master_sync_in_progress == "0" {
        return Ok(true);
    }

    return Ok(false);

}
