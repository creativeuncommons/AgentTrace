use agenttrace::{generate_trace_id, AgentSpan, AgentTracer, SpanKind, SpanStatus};
use std::env;

fn sanitize_terminal(s: &str) -> String {
    s.chars()
        .map(|c| if c.is_control() { '?' } else { c })
        .collect()
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let tracer = match AgentTracer::new("agenttrace-cli", None) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("Failed to initialize AgentTrace: {}", e);
            std::process::exit(1);
        }
    };

    if args.iter().any(|a| a == "--demo") {
        let tid = generate_trace_id();
        let mut span = AgentSpan::new(&tid, "agent.demo.run", SpanKind::Agent, None);
        tracer.finish_and_record(&mut span, SpanStatus::Ok, "");
        println!("Recorded demo trace: {}", tid);
        return;
    }

    if let Some(pos) = args.iter().position(|a| a == "--trace") {
        if let Some(tid) = args.get(pos + 1) {
            match tracer.storage.get_trace_spans(tid) {
                Ok(spans) => {
                    for (sid, parent, name, kind, status, dur) in spans {
                        println!(
                            "{} [{}] {} ({:.2}ms) parent={}",
                            sanitize_terminal(&sid),
                            sanitize_terminal(&kind),
                            sanitize_terminal(&name),
                            dur,
                            sanitize_terminal(parent.as_deref().unwrap_or("root"))
                        );
                        let _ = status;
                    }
                }
                Err(e) => {
                    eprintln!("Error loading trace: {}", e);
                    std::process::exit(1);
                }
            }
            return;
        }
    }

    match tracer.storage.list_traces(20) {
        Ok(traces) => {
            for (tid, count, dur) in traces {
                println!(
                    "{} | spans={} | max_duration_ms={:.2}",
                    sanitize_terminal(&tid),
                    count,
                    dur
                );
            }
        }
        Err(e) => {
            eprintln!("Error listing traces: {}", e);
            std::process::exit(1);
        }
    }
}
