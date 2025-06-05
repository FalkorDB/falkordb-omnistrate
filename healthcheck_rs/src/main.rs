use rouille::{router, Response, Server};
use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    let is_sentinel = args.get(1).map_or(false, |arg| arg == "sentinel");
    start_health_check_server(is_sentinel);
}

fn start_health_check_server(is_sentinel: bool) {
    let port = env::var(if is_sentinel { "HEALTH_CHECK_PORT_SENTINEL" } else { "HEALTH_CHECK_PORT" })
        .unwrap_or_else(|_| if is_sentinel { "8082".to_string() } else { "8081".to_string() });

    let addr = format!("localhost:{}", port);
    let server = Server::new(addr, move |request| {
        router!(request,
            (GET) (/liveness) => { Response::text("OK") },
            (GET) (/readiness) => { Response::text("OK") },
            (GET) (/startup) => { Response::text("OK") },
            _ => Response::empty_404()
        )
    }).unwrap();

    println!("Listening on {}", server.server_addr());
    server.run();
}
