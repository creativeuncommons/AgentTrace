use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum SpanKind {
    Agent,
    Router,
    Tool,
    Llm,
    Internal,
}

impl SpanKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            SpanKind::Agent => "AGENT",
            SpanKind::Router => "ROUTER",
            SpanKind::Tool => "TOOL",
            SpanKind::Llm => "LLM",
            SpanKind::Internal => "INTERNAL",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum SpanStatus {
    Unset,
    Ok,
    Error,
}

impl SpanStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            SpanStatus::Unset => "UNSET",
            SpanStatus::Ok => "OK",
            SpanStatus::Error => "ERROR",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpanEvent {
    pub name: String,
    pub timestamp_ns: u128,
    pub attributes: HashMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSpan {
    pub trace_id: String,
    pub span_id: String,
    pub parent_span_id: Option<String>,
    pub name: String,
    pub kind: SpanKind,
    pub start_time_ns: u128,
    pub end_time_ns: Option<u128>,
    pub duration_ms: Option<f64>,
    pub status: SpanStatus,
    pub status_message: String,
    pub attributes: HashMap<String, Value>,
    pub events: Vec<SpanEvent>,
}

impl AgentSpan {
    pub fn new(trace_id: &str, name: &str, kind: SpanKind, parent_span_id: Option<&str>) -> Self {
        Self {
            trace_id: trace_id.to_string(),
            span_id: generate_span_id(),
            parent_span_id: parent_span_id.map(str::to_string),
            name: name.to_string(),
            kind,
            start_time_ns: now_ns(),
            end_time_ns: None,
            duration_ms: None,
            status: SpanStatus::Unset,
            status_message: String::new(),
            attributes: HashMap::new(),
            events: Vec::new(),
        }
    }

    pub fn set_attribute(&mut self, key: &str, value: Value) {
        self.attributes.insert(key.to_string(), value);
    }

    pub fn add_event(&mut self, name: &str, attributes: HashMap<String, Value>) {
        self.events.push(SpanEvent {
            name: name.to_string(),
            timestamp_ns: now_ns(),
            attributes,
        });
    }

    pub fn finish(&mut self, status: SpanStatus, message: &str) {
        let end = now_ns();
        self.end_time_ns = Some(end);
        self.duration_ms = Some(((end.saturating_sub(self.start_time_ns)) as f64) / 1_000_000.0);
        self.status = status;
        if !message.is_empty() {
            self.status_message = message.to_string();
        }
    }
}

pub fn now_ns() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}

pub fn generate_trace_id() -> String {
    Uuid::new_v4().simple().to_string()
}

pub fn generate_span_id() -> String {
    Uuid::new_v4().simple().to_string()[..16].to_string()
}

pub fn is_safe_trace_id(trace_id: &str) -> bool {
    !trace_id.is_empty()
        && trace_id.len() <= 64
        && trace_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn is_hex(s: &str, expected_len: usize) -> bool {
    s.len() == expected_len && s.bytes().all(|b| b.is_ascii_hexdigit())
}

pub fn format_traceparent(trace_id: &str, span_id: &str, sampled: bool) -> String {
    let flags = if sampled { "01" } else { "00" };
    format!("00-{:0>32}-{:0>16}-{}", trace_id.to_lowercase(), span_id.to_lowercase(), flags)
}

pub fn parse_traceparent(header: &str) -> Option<(String, String, String, String)> {
    let parts: Vec<&str> = header.trim().split('-').collect();
    if parts.len() == 4 && is_hex(parts[1], 32) && is_hex(parts[2], 16) {
        Some((
            parts[0].to_string(),
            parts[1].to_lowercase(),
            parts[2].to_lowercase(),
            parts[3].to_string(),
        ))
    } else {
        None
    }
}

pub fn extract_trace_headers(headers: &HashMap<String, String>) -> (Option<String>, Option<String>) {
    if let Some(tp) = headers.get("traceparent").or_else(|| headers.get("Traceparent")) {
        if let Some((_, tid, sid, _)) = parse_traceparent(tp) {
            return (Some(tid), Some(sid));
        }
    }
    if let Some(hg) = headers
        .get("x-hypergate-trace-id")
        .or_else(|| headers.get("X-HyperGate-Trace-Id"))
    {
        if is_safe_trace_id(hg) {
            return (Some(hg.clone()), None);
        }
    }
    (None, None)
}

pub struct TraceStorage {
    pub base_dir: PathBuf,
    pub db_path: PathBuf,
    conn: Arc<Mutex<Connection>>,
}

impl TraceStorage {
    pub fn new(base_dir: Option<&Path>) -> Result<Self, Box<dyn std::error::Error>> {
        let dir = match base_dir {
            Some(p) => p.to_path_buf(),
            None => {
                if let Ok(env_dir) = std::env::var("AGENTTRACE_DIR") {
                    PathBuf::from(env_dir)
                } else {
                    std::env::current_dir()?.join(".agenttrace").join("traces")
                }
            }
        };

        fs::create_dir_all(&dir)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&dir, fs::Permissions::from_mode(0o700));
        }

        let db_path = dir.join("agenttrace.db");
        let conn = Connection::open(&db_path)?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             CREATE TABLE IF NOT EXISTS spans (
                 span_id TEXT PRIMARY KEY,
                 trace_id TEXT NOT NULL,
                 parent_span_id TEXT,
                 name TEXT NOT NULL,
                 kind TEXT NOT NULL,
                 start_time_ns INTEGER NOT NULL,
                 end_time_ns INTEGER,
                 duration_ms REAL,
                 status TEXT NOT NULL,
                 status_message TEXT,
                 attributes_json TEXT,
                 events_json TEXT
             );
             CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans (trace_id, start_time_ns);",
        )?;

        Ok(Self {
            base_dir: dir,
            db_path,
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    pub fn record_span(&self, span: &AgentSpan) -> Result<(), Box<dyn std::error::Error>> {
        if !is_safe_trace_id(&span.trace_id) {
            return Err("Rejected unsafe trace_id".into());
        }

        let jsonl_path = self.base_dir.join(format!("{}.jsonl", span.trace_id));
        let line = format!("{}\n", serde_json::to_string(span)?);
        let mut opts = OpenOptions::new();
        opts.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        if let Ok(mut f) = opts.open(&jsonl_path) {
            let _ = f.write_all(line.as_bytes());
        }

        let conn = self.conn.lock().map_err(|_| "Mutex poisoned")?;
        conn.execute(
            "INSERT OR REPLACE INTO spans (
                span_id, trace_id, parent_span_id, name, kind,
                start_time_ns, end_time_ns, duration_ms, status,
                status_message, attributes_json, events_json
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                span.span_id,
                span.trace_id,
                span.parent_span_id,
                span.name,
                span.kind.as_str(),
                span.start_time_ns as i64,
                span.end_time_ns.map(|v| v as i64),
                span.duration_ms,
                span.status.as_str(),
                span.status_message,
                serde_json::to_string(&span.attributes)?,
                serde_json::to_string(&span.events)?,
            ],
        )?;
        Ok(())
    }

    pub fn list_traces(&self, limit: usize) -> Result<Vec<(String, usize, f64)>, Box<dyn std::error::Error>> {
        let conn = self.conn.lock().map_err(|_| "Mutex poisoned")?;
        let mut stmt = conn.prepare(
            "SELECT trace_id, COUNT(*), COALESCE(MAX(duration_ms), 0.0)
             FROM spans
             GROUP BY trace_id
             ORDER BY MAX(start_time_ns) DESC
             LIMIT ?1",
        )?;
        let rows = stmt.query_map(params![limit as i64], |r| {
            Ok((r.get(0)?, r.get::<_, i64>(1)? as usize, r.get(2)?))
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    pub fn get_trace_spans(&self, trace_id: &str) -> Result<Vec<(String, Option<String>, String, String, String, f64)>, Box<dyn std::error::Error>> {
        let conn = self.conn.lock().map_err(|_| "Mutex poisoned")?;
        let mut stmt = conn.prepare(
            "SELECT span_id, parent_span_id, name, kind, status, COALESCE(duration_ms, 0.0)
             FROM spans WHERE trace_id = ?1 ORDER BY start_time_ns ASC",
        )?;
        let rows = stmt.query_map(params![trace_id], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?))
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }
}

pub struct AgentTracer {
    pub service_name: String,
    pub storage: TraceStorage,
}

impl AgentTracer {
    pub fn new(service_name: &str, base_dir: Option<&Path>) -> Result<Self, Box<dyn std::error::Error>> {
        Ok(Self {
            service_name: service_name.to_string(),
            storage: TraceStorage::new(base_dir)?,
        })
    }

    pub fn finish_and_record(&self, span: &mut AgentSpan, status: SpanStatus, message: &str) {
        span.finish(status, message);
        let _ = self.storage.record_span(span);
    }

    pub fn record_circuit_breaker_trip(
        &self,
        span: &mut AgentSpan,
        budget_limit_usd: f64,
        rolling_cost_usd: f64,
        reason: &str,
    ) {
        let mut attrs = HashMap::new();
        attrs.insert("hypergate.budget_limit_usd".to_string(), json!(budget_limit_usd));
        attrs.insert("hypergate.rolling_cost_usd".to_string(), json!(rolling_cost_usd));
        attrs.insert("hypergate.reason".to_string(), json!(reason));
        span.add_event("hypergate.circuit_breaker_tripped", attrs);
        span.set_attribute("hypergate.circuit_tripped", json!(true));
    }
}

pub fn evaluate_trajectory_gate(expected: &[&str], actual: &[&str], max_edit_distance: usize) -> (bool, usize) {
    let m = expected.len();
    let n = actual.len();
    if m.abs_diff(n) > max_edit_distance {
        return (false, m.abs_diff(n));
    }
    if max_edit_distance == 0 {
        let eq = expected == actual;
        return (eq, if eq { 0 } else { 1 });
    }
    let mut prev: Vec<usize> = (0..=n).collect();
    let mut curr: Vec<usize> = vec![0; n + 1];
    for i in 1..=m {
        curr[0] = i;
        for j in 1..=n {
            let cost = if expected[i - 1] == actual[j - 1] { 0 } else { 1 };
            curr[j] = (prev[j] + 1).min(curr[j - 1] + 1).min(prev[j - 1] + cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    let dist = prev[n];
    (dist <= max_edit_distance, dist)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_traceparent_rejects_traversal() {
        assert!(parse_traceparent("00-../../../../etc/passwd00000000-1234567890abcdef-01").is_none());
        let valid = parse_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01");
        assert!(valid.is_some());
    }

    #[test]
    fn test_storage_and_trajectory_gate() {
        let tmp = std::env::temp_dir().join(format!("agenttrace_rs_test_{}", generate_trace_id()));
        let tracer = AgentTracer::new("test-svc", Some(&tmp)).unwrap();
        let tid = generate_trace_id();
        let mut span = AgentSpan::new(&tid, "agent.step", SpanKind::Agent, None);
        tracer.finish_and_record(&mut span, SpanStatus::Ok, "");
        let spans = tracer.storage.get_trace_spans(&tid).unwrap();
        assert_eq!(spans.len(), 1);
        assert_eq!(evaluate_trajectory_gate(&["a", "b"], &["a", "b"], 0), (true, 0));
        assert_eq!(evaluate_trajectory_gate(&["a", "b", "c", "d"], &["a"], 1), (false, 3));
        let _ = fs::remove_dir_all(&tmp);
    }
}
