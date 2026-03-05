/// Healthcheck HTTP stress tool
///
/// Fires concurrent GET requests at /liveness, /readiness, and /startup in a
/// round-robin.  No memory monitoring — watch that in Grafana.
///
/// Usage
/// -----
///   cargo build --release --bin stress
///
///   # Basic — 20 req/s with 10 concurrent threads against localhost:8081
///   ./target/release/stress
///
///   # Custom target and load
///   ./target/release/stress --host 10.0.0.5 --port 8081 --concurrency 20 --rate 50
///
///   # Unlimited rate (as fast as possible)
///   ./target/release/stress --rate 0
///
/// Flags
/// -----
///   --host          Target host            (default: localhost)
///   --port          Target port            (default: 8081)
///   --concurrency   Worker threads         (default: 10)
///   --rate          Total req/s, 0=max     (default: 20)
///   --endpoint      One of: liveness, readiness, startup, all  (default: all)

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

fn main() {
    let cfg = parse_args();

    println!("╔══════════════════════════════════════════════════════╗");
    println!("║           Healthcheck HTTP stress tool               ║");
    println!("╠══════════════════════════════════════════════════════╣");
    println!("║  Target      : http://{}:{:<26} ║", cfg.host, cfg.port);
    println!("║  Endpoint(s) : {:<38} ║", cfg.endpoint_label());
    println!("║  Concurrency : {:<3} worker threads                   ║", cfg.concurrency);
    if cfg.rate == 0 {
    println!("║  Rate        : unlimited                             ║");
    } else {
    println!("║  Rate        : {:<3} req/s total                       ║", cfg.rate);
    }
    println!("╚══════════════════════════════════════════════════════╝");
    println!();
    println!("Press Ctrl-C to stop.\n");
    println!("{:<8}  {:>12}  {:>10}  {:>10}", "Time(s)", "TotalReqs", "Req/s", "Errors");
    println!("{}", "─".repeat(48));

    let total_reqs = Arc::new(AtomicU64::new(0));
    let total_errors = Arc::new(AtomicU64::new(0));

    // Spawn worker threads.
    for i in 0..cfg.concurrency {
        let host = cfg.host.clone();
        let port = cfg.port;
        let endpoints = cfg.endpoints.clone();
        let rate = cfg.rate;
        let concurrency = cfg.concurrency;
        let reqs = total_reqs.clone();
        let errs = total_errors.clone();

        std::thread::spawn(move || {
            // Spread the per-thread sleep so workers don't all fire at once.
            let sleep_ms = if rate == 0 {
                0
            } else {
                // Each worker is responsible for rate/concurrency req/s.
                // sleep = 1000ms * concurrency / rate
                1000 * concurrency as u64 / rate
            };

            // Stagger startup so workers don't all send their first request at t=0.
            if rate > 0 {
                std::thread::sleep(Duration::from_millis(
                    (i as u64 * 1000) / rate.max(1),
                ));
            }

            let client = ureq::AgentBuilder::new()
                .timeout_connect(Duration::from_secs(2))
                .timeout_read(Duration::from_secs(2))
                .build();

            let mut idx: usize = i; // offset so workers hit different endpoints
            loop {
                let endpoint = &endpoints[idx % endpoints.len()];
                let url = format!("http://{}:{}{}", host, port, endpoint);
                let t = Instant::now();

                match client.get(&url).call() {
                    Ok(_) => { reqs.fetch_add(1, Ordering::Relaxed); }
                    Err(_) => { errs.fetch_add(1, Ordering::Relaxed); }
                }
                idx += 1;

                if sleep_ms > 0 {
                    let elapsed = t.elapsed();
                    let target = Duration::from_millis(sleep_ms);
                    if elapsed < target {
                        std::thread::sleep(target - elapsed);
                    }
                }
            }
        });
    }

    // Stats printer — runs on the main thread.
    let start = Instant::now();
    let mut last_reqs: u64 = 0;
    loop {
        std::thread::sleep(Duration::from_secs(1));
        let elapsed = start.elapsed().as_secs();
        let cur_reqs = total_reqs.load(Ordering::Relaxed);
        let cur_errs = total_errors.load(Ordering::Relaxed);
        let rps = cur_reqs - last_reqs;
        last_reqs = cur_reqs;
        println!("{:<8}  {:>12}  {:>10}  {:>10}", elapsed, cur_reqs, rps, cur_errs);
    }
}

// ── Config ────────────────────────────────────────────────────────────────────

struct Config {
    host: String,
    port: u16,
    concurrency: usize,
    /// Desired total req/s across all workers.  0 = unlimited.
    rate: u64,
    /// The subset of endpoints to hit.
    endpoints: Vec<String>,
}

impl Config {
    fn endpoint_label(&self) -> String {
        self.endpoints.join(", ")
    }
}

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().collect();

    let get = |flag: &str, default: &str| -> String {
        args.windows(2)
            .find(|w| w[0] == flag)
            .map(|w| w[1].clone())
            .unwrap_or_else(|| default.to_string())
    };

    let endpoint_arg = get("--endpoint", "all");
    let endpoints: Vec<String> = match endpoint_arg.as_str() {
        "liveness"  => vec!["/liveness".into()],
        "readiness" => vec!["/readiness".into()],
        "startup"   => vec!["/startup".into()],
        _           => vec!["/liveness".into(), "/readiness".into(), "/startup".into()],
    };

    Config {
        host: get("--host", "localhost"),
        port: get("--port", "8081").parse().unwrap_or(8081),
        concurrency: get("--concurrency", "10").parse().unwrap_or(10),
        rate: get("--rate", "20").parse().unwrap_or(20),
        endpoints,
    }
}
