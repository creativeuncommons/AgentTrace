"""
AgentTrace Core Engine
======================
Zero-external-dependency, production-grade distributed tracing and telemetry
framework for autonomous AI agents, tool invocation graphs, and MCP IPC servers.

Standardized on W3C Distributed Trace Context and OpenTelemetry GenAI Semantic Conventions.
Pure Python standard library (json, sqlite3, dataclasses, time, uuid, threading).
"""

import os
import re
import sys
import json
import time
import uuid
import shutil
import sqlite3
import datetime
import tempfile
import threading
import contextlib
import urllib.request
import urllib.error
import urllib.parse
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple, Callable


# -----------------------------------------------------------------------------
# W3C Distributed Trace Context (RFC 00-traceparent)
# -----------------------------------------------------------------------------

def generate_trace_id() -> str:
    """Generates a 32-character hexadecimal W3C Trace ID (128-bit)."""
    return uuid.uuid4().hex


def generate_span_id() -> str:
    """Generates a 16-character hexadecimal W3C Span ID (64-bit)."""
    return uuid.uuid4().hex[:16]


def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    """Formats canonical W3C traceparent header: 00-{trace_id}-{span_id}-{flags}"""
    flags = "01" if sampled else "00"
    return f"00-{trace_id.lower():0>32}-{span_id.lower():0>16}-{flags}"


_TRACE_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_HEX_RE = re.compile(r"^[0-9a-f]{16}$")
_SAFE_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def take_bounded_slice(data: Any, max_len: int = 100_000) -> Any:
    """Bounded stream slice preventing memory exhaustion from unbounded telemetry frames."""
    if isinstance(data, (str, list, bytes)):
        return data[:max_len]
    return data


def parse_traceparent(header: str) -> Optional[Tuple[str, str, str, str]]:
    """Parses W3C traceparent header into (version, trace_id, parent_span_id, flags)."""
    if not header or not isinstance(header, str):
        return None
    parts = header.strip().split("-")
    if len(parts) == 4 and _TRACE_HEX_RE.fullmatch(parts[1].lower()) and _SPAN_HEX_RE.fullmatch(parts[2].lower()):
        return parts[0], parts[1].lower(), parts[2].lower(), parts[3]
    return None


class SpanKind(str, Enum):
    AGENT = "AGENT"           # Multi-turn autonomous agent session / loop
    ROUTER = "ROUTER"         # Inference router, cascade dispatch, budget check
    TOOL = "TOOL"             # Tool execution (stdio, MCP, API, bash)
    LLM = "LLM"               # Upstream LLM completion
    INTERNAL = "INTERNAL"     # Internal function or pipeline step


class SpanStatus(str, Enum):
    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


@dataclass
class SpanEvent:
    name: str
    timestamp_ns: int = field(default_factory=time.time_ns)
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timestamp_ns": self.timestamp_ns,
            "timestamp_iso": datetime.datetime.fromtimestamp(
                self.timestamp_ns / 1_000_000_000.0, datetime.timezone.utc
            ).isoformat(),
            "attributes": self.attributes
        }


@dataclass
class AgentSpan:
    name: str
    trace_id: str
    span_id: str = field(default_factory=generate_span_id)
    parent_span_id: Optional[str] = None
    kind: SpanKind = SpanKind.INTERNAL
    start_time_ns: int = field(default_factory=time.time_ns)
    end_time_ns: Optional[int] = None
    duration_ms: Optional[float] = None
    status: SpanStatus = SpanStatus.UNSET
    status_message: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[SpanEvent] = field(default_factory=list)

    def finish(self, status: SpanStatus = SpanStatus.OK, message: Optional[str] = None):
        if self.end_time_ns is None:
            self.end_time_ns = time.time_ns()
            self.duration_ms = (self.end_time_ns - self.start_time_ns) / 1_000_000.0
        if self.status == SpanStatus.UNSET:
            self.status = status
        elif status == SpanStatus.ERROR:
            self.status = SpanStatus.ERROR
        if message:
            self.status_message = message

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.events.append(SpanEvent(name=name, attributes=attributes or {}))

    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value

    def to_dict(self) -> Dict[str, Any]:
        start_iso = datetime.datetime.fromtimestamp(
            self.start_time_ns / 1_000_000_000.0, datetime.timezone.utc
        ).isoformat()
        end_iso = (
            datetime.datetime.fromtimestamp(
                self.end_time_ns / 1_000_000_000.0, datetime.timezone.utc
            ).isoformat()
            if self.end_time_ns else None
        )
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "kind": self.kind.value,
            "start_time_ns": self.start_time_ns,
            "end_time_ns": self.end_time_ns,
            "start_time_iso": start_iso,
            "end_time_iso": end_iso,
            "duration_ms": round(self.duration_ms, 3) if self.duration_ms is not None else None,
            "status": self.status.value,
            "status_message": self.status_message,
            "attributes": self.attributes,
            "events": [e.to_dict() for e in self.events],
        }


# -----------------------------------------------------------------------------
# Storage Sinks: Local Append-Only JSONL & Relational SQLite
# -----------------------------------------------------------------------------

class TraceStorage:
    """Thread-safe persistent storage for AgentTrace spans (JSONL + SQLite) with automatic disk overrun guardrails."""
    def __init__(
        self,
        base_dir: Optional[str] = None,
        max_storage_mb: float = 250.0,
        min_disk_free_gb: float = 5.0,
        retention_days: int = 7
    ):
        candidates = [
            base_dir,
            os.environ.get("AGENTTRACE_DIR"),
            os.path.join(os.getcwd(), ".agenttrace", "traces"),
            os.path.expanduser("~/.agenttrace/traces"),
        ]
        chosen = None
        for cand in candidates:
            if not cand:
                continue
            try:
                if os.path.islink(cand):
                    continue
                os.makedirs(cand, mode=0o700, exist_ok=True)
                st = os.lstat(cand)
                geteuid = getattr(os, "geteuid", None)
                if os.path.islink(cand) or (geteuid is not None and st.st_uid != geteuid()):
                    continue
                with tempfile.TemporaryFile(dir=cand) as f:
                    f.write(b"1")
                chosen = cand
                break
            except (OSError, PermissionError):
                continue

        self.base_dir = chosen or tempfile.mkdtemp(prefix="agenttrace_traces_")
        os.makedirs(self.base_dir, mode=0o700, exist_ok=True)
        self.db_path = os.path.join(self.base_dir, "agenttrace.db")
        self.max_storage_bytes = int(max_storage_mb * 1024 * 1024)
        self.min_disk_free_bytes = int(min_disk_free_gb * 1024 * 1024 * 1024)
        self.retention_seconds = int(retention_days * 86400)
        self._write_count = 0
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        self._init_sqlite()

    def _check_and_prune(self):
        """Enforces storage ceiling and host free-disk safety limits via FIFO auto-pruning."""
        # 1. Host disk space circuit breaker
        try:
            usage = shutil.disk_usage(self.base_dir)
            if usage.free < self.min_disk_free_bytes:
                print(f"[AgentTrace] Storage Warning: Host disk free space ({usage.free / 1024**3:.2f} GB) below safety threshold ({self.min_disk_free_bytes / 1024**3:.2f} GB).", file=sys.stderr)
        except Exception:
            pass

        # 2. Storage quota & age pruning
        try:
            total_size = 0
            jsonl_files = []
            now = time.time()

            for entry in os.scandir(self.base_dir):
                if entry.is_file():
                    total_size += entry.stat().st_size
                    if entry.name.endswith(".jsonl"):
                        jsonl_files.append((entry.stat().st_mtime, entry.path, entry.name[:-6]))

            # Prune if exceeding quota or older than retention
            if total_size > self.max_storage_bytes or any((now - mtime) > self.retention_seconds for mtime, _, _ in jsonl_files):
                jsonl_files.sort(key=lambda x: x[0])  # Oldest first
                pruned_trace_ids = []

                for mtime, path, trace_id in jsonl_files:
                    if total_size > (self.max_storage_bytes * 0.80) or (now - mtime) > self.retention_seconds:
                        try:
                            f_size = os.path.getsize(path)
                            os.remove(path)
                            total_size -= f_size
                            pruned_trace_ids.append(trace_id)
                        except OSError:
                            pass

                # Purge pruned spans from SQLite in bulk
                if pruned_trace_ids:
                    with self._lock:
                        conn = sqlite3.connect(self.db_path)
                        try:
                            cur = conn.cursor()
                            placeholders = ",".join("?" * len(pruned_trace_ids))
                            cur.execute(f"DELETE FROM spans WHERE trace_id IN ({placeholders})", pruned_trace_ids)
                            conn.commit()
                        finally:
                            conn.close()
        except Exception as e:
            print(f"[AgentTrace] Pruning error: {e}", file=sys.stderr)

    def _init_sqlite(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute("""
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
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans (trace_id);")
                conn.commit()
            finally:
                conn.close()

    def record_span(self, span: AgentSpan):
        span_dict = span.to_dict()

        # 1. Append to trace JSONL stream only if trace_id is safe (no path traversal)
        if _SAFE_TRACE_ID_RE.fullmatch(str(span.trace_id)):
            jsonl_path = os.path.join(self.base_dir, f"{span.trace_id}.jsonl")
            line = json.dumps(span_dict, default=str) + "\n"
            try:
                fd = os.open(jsonl_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                print(f"[AgentTrace] Warning appending JSONL: {e}", file=sys.stderr)
        else:
            print("[AgentTrace] Warning: rejected unsafe trace_id for JSONL write", file=sys.stderr)

        # 2. Insert into persistent WAL SQLite connection
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")
                cur.execute("""
                    INSERT OR REPLACE INTO spans (
                        span_id, trace_id, parent_span_id, name, kind,
                        start_time_ns, end_time_ns, duration_ms, status,
                        status_message, attributes_json, events_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    span.span_id, span.trace_id, span.parent_span_id, span.name, span.kind.value,
                    span.start_time_ns, span.end_time_ns, span.duration_ms, span.status.value,
                    span.status_message, json.dumps(span.attributes, default=str), json.dumps([e.to_dict() for e in span.events], default=str)
                ))
                self._conn.commit()
            except Exception as e:
                print(f"[AgentTrace] Warning inserting SQLite: {e}", file=sys.stderr)

        # 3. Disk safety & FIFO prune check (every 50 writes)
        self._write_count += 1
        if self._write_count % 50 == 0:
            self._check_and_prune()

    def get_trace_spans(self, trace_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time_ns ASC", (trace_id,))
                rows = cur.fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    d["attributes"] = json.loads(d.pop("attributes_json", "{}"))
                    d["events"] = json.loads(d.pop("events_json", "[]"))
                    out.append(d)
                return out
            finally:
                conn.close()

    def list_traces(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT 
                        trace_id,
                        COUNT(*) as total_spans,
                        MIN(start_time_ns) as start_time_ns,
                        MAX(end_time_ns) as end_time_ns,
                        ROUND((MAX(end_time_ns) - MIN(start_time_ns)) / 1000000.0, 2) as total_duration_ms,
                        GROUP_CONCAT(DISTINCT kind) as kinds,
                        SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) as error_count
                    FROM spans
                    GROUP BY trace_id
                    ORDER BY start_time_ns DESC
                    LIMIT ?
                """, (limit,))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def close(self):
        """Closes the underlying persistent SQLite connection."""
        with self._lock:
            if hasattr(self, "_conn") and self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None


# -----------------------------------------------------------------------------
# OpenTelemetry (OTLP) HTTP/JSON Exporter
# -----------------------------------------------------------------------------

class OTLPExporter:
    """
    Zero-dependency exporter converting AgentTrace spans into standard OpenTelemetry
    OTLP/HTTP JSON payloads (POST /v1/traces) compatible with Jaeger, Tempo, Honeycomb.
    """
    def __init__(self, endpoint_url: str = "http://localhost:4318/v1/traces", service_name: str = "agent-service"):
        self.endpoint_url = endpoint_url
        self.service_name = service_name

    def serialize_spans(self, spans: List[AgentSpan]) -> Dict[str, Any]:
        """Converts AgentSpans into the official OpenTelemetry OTLP JSON trace envelope."""
        otlp_spans = []
        for s in spans:
            otlp_attrs = []
            for k, v in s.attributes.items():
                if isinstance(v, bool):
                    val = {"boolValue": v}
                elif isinstance(v, (int, float)):
                    val = {"doubleValue": float(v)}
                else:
                    val = {"stringValue": str(v)}
                otlp_attrs.append({"key": k, "value": val})

            otlp_spans.append({
                "traceId": s.trace_id,
                "spanId": s.span_id,
                "parentSpanId": s.parent_span_id or "",
                "name": s.name,
                "kind": 1 if s.kind == SpanKind.INTERNAL else 2,
                "startTimeUnixNano": str(s.start_time_ns),
                "endTimeUnixNano": str(s.end_time_ns or s.start_time_ns),
                "attributes": otlp_attrs,
                "status": {
                    "code": 1 if s.status == SpanStatus.OK else (2 if s.status == SpanStatus.ERROR else 0),
                    "message": s.status_message or ""
                }
            })

        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.service_name}},
                            {"key": "telemetry.sdk.name", "value": {"stringValue": "AgentTrace"}},
                            {"key": "telemetry.sdk.version", "value": {"stringValue": "0.1.0"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "agenttrace.core", "version": "0.1.0"},
                            "spans": otlp_spans
                        }
                    ]
                }
            ]
        }

    def export(self, spans: List[AgentSpan], timeout: int = 5) -> bool:
        if not spans:
            return True
        parsed = urllib.parse.urlparse(self.endpoint_url)
        if parsed.scheme not in ("http", "https"):
            print(f"[AgentTrace OTLPExporter] Rejected unsupported scheme: {parsed.scheme!r}", file=sys.stderr)
            return False
        payload = self.serialize_spans(spans)
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(self.endpoint_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status in (200, 202)
        except Exception as e:
            safe_target = f"{parsed.scheme}://{parsed.hostname or ''}{f':{parsed.port}' if parsed.port else ''}{parsed.path}"
            print(f"[AgentTrace OTLPExporter] Error exporting to {safe_target}: {e}", file=sys.stderr)
            return False


# -----------------------------------------------------------------------------
# Tracer & Context Management
# -----------------------------------------------------------------------------

class AgentTracer:
    """
    Context-aware tracer managing nested span hierarchies and storage dispatch.
    """
    def __init__(
        self,
        service_name: str = "agent-service",
        storage_dir: Optional[str] = None,
        max_storage_mb: float = 250.0,
        min_disk_free_gb: float = 5.0,
        retention_days: int = 7
    ):
        self.service_name = service_name
        self.storage = TraceStorage(
            base_dir=storage_dir,
            max_storage_mb=max_storage_mb,
            min_disk_free_gb=min_disk_free_gb,
            retention_days=retention_days
        )
        self._local = threading.local()

    def _get_stack(self) -> List[AgentSpan]:
        if not hasattr(self._local, "span_stack"):
            self._local.span_stack = []
        return self._local.span_stack

    def get_current_span(self) -> Optional[AgentSpan]:
        stack = self._get_stack()
        return stack[-1] if stack else None

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None
    ):
        parent = self.get_current_span()
        resolved_trace_id = trace_id or (parent.trace_id if parent else generate_trace_id())
        resolved_parent_id = parent_span_id or (parent.span_id if parent else None)

        span_obj = AgentSpan(
            name=name,
            trace_id=resolved_trace_id,
            parent_span_id=resolved_parent_id,
            kind=kind,
            attributes=attributes or {}
        )

        stack = self._get_stack()
        stack.append(span_obj)
        try:
            yield span_obj
            span_obj.finish(status=SpanStatus.OK)
        except Exception as e:
            span_obj.finish(status=SpanStatus.ERROR, message=str(e))
            span_obj.add_event("exception", {"exception.type": type(e).__name__, "exception.message": str(e)})
            raise
        finally:
            stack.pop()
            try:
                self.storage.record_span(span_obj)
            except Exception as rec_err:
                print(f"[AgentTrace] Warning: record_span failed: {rec_err}", file=sys.stderr)

    def trace_agent_loop(self, agent_name: str, max_turns: int = 30):
        """Decorator for tracing an entire autonomous agent task loop."""
        def decorator(func: Callable):
            def wrapper(*args, **kwargs):
                trace_id = generate_trace_id()
                with self.span(f"agent.{agent_name}.loop", kind=SpanKind.AGENT, trace_id=trace_id) as s:
                    s.set_attribute("agent.name", agent_name)
                    s.set_attribute("agent.max_turns", max_turns)
                    return func(*args, **kwargs)
            return wrapper
        return decorator

    def trace_tool_call(self, tool_name: str, transport: str = "direct"):
        """Decorator for tracing discrete tool invocations."""
        def decorator(func: Callable):
            def wrapper(*args, **kwargs):
                with self.span(f"tool.{tool_name}", kind=SpanKind.TOOL) as s:
                    s.set_attribute("tool.name", tool_name)
                    s.set_attribute("tool.transport", transport)
                    t0 = time.perf_counter()
                    try:
                        res = func(*args, **kwargs)
                        s.set_attribute("tool.latency_ms", round((time.perf_counter() - t0) * 1000.0, 3))
                        return res
                    except Exception as e:
                        s.set_attribute("tool.error", str(e))
                        raise
            return wrapper
        return decorator

    def inject_trace_headers(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Injects W3C traceparent and HyperGate headers into outbound request dictionary."""
        out = dict(headers or {})
        span = self.get_current_span()
        if span:
            out["traceparent"] = format_traceparent(span.trace_id, span.span_id)
            out["x-hypergate-trace-id"] = span.trace_id
        return out

    def extract_trace_headers(self, headers: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
        """Extracts (trace_id, parent_span_id) from incoming W3C or HyperGate headers."""
        tp = headers.get("traceparent") or headers.get("Traceparent")
        if tp:
            parsed = parse_traceparent(tp)
            if parsed:
                return parsed[1], parsed[2]
        hg_id = headers.get("x-hypergate-trace-id") or headers.get("X-HyperGate-Trace-Id")
        if hg_id and _SAFE_TRACE_ID_RE.fullmatch(str(hg_id)):
            return str(hg_id), None
        return None, None

    def record_circuit_breaker_trip(
        self,
        span: AgentSpan,
        budget_limit_usd: float,
        rolling_cost_usd: float,
        reason: str
    ):
        """
        Explicit circuit-breaker fail-closed termination.
        Emits standard circuit_breaker.tripped event and sets 429 status.
        """
        span.set_attribute("circuit_tripped", True)
        span.set_attribute("circuit.limit_usd", budget_limit_usd)
        span.set_attribute("circuit.rolling_cost_usd", rolling_cost_usd)
        span.set_attribute("circuit.reason", reason)
        span.set_attribute("status_code", 429)
        span.add_event("circuit_breaker.tripped", {
            "limit_usd": budget_limit_usd,
            "rolling_cost_usd": rolling_cost_usd,
            "reason": reason
        })
        span.finish(status=SpanStatus.ERROR, message=f"Circuit Breaker Tripped (429): {reason}")

    def print_tree(self, trace_id: Optional[str] = None):
        """Ergonomic helper: Prints an ASCII execution tree directly to the terminal."""
        tid = trace_id
        if not tid:
            recent = self.storage.list_traces(limit=1)
            if recent:
                tid = recent[0]["trace_id"]
        if not tid:
            print("ℹ️ [AgentTrace] No recorded traces found to render.")
            return
        # Lazy import cli to prevent circular dependencies
        try:
            from .cli import print_trace_tree
        except ImportError:
            from cli import print_trace_tree
        print_trace_tree(self.storage, tid)

    def close(self):
        """Closes the underlying trace storage resources."""
        if hasattr(self, "storage") and self.storage is not None:
            self.storage.close()


# Global Default Tracer Instance
_GLOBAL_TRACER: Optional[AgentTracer] = None

def get_tracer(service_name: str = "agent-service", storage_dir: Optional[str] = None) -> AgentTracer:
    global _GLOBAL_TRACER
    if _GLOBAL_TRACER is None:
        _GLOBAL_TRACER = AgentTracer(service_name=service_name, storage_dir=storage_dir)
    return _GLOBAL_TRACER


# -----------------------------------------------------------------------------
# Golden-Path Trajectory Grader Algorithm
# -----------------------------------------------------------------------------

DEFAULT_VOLATILE_KEYS = {"timestamp", "time", "date", "request_id", "trace_id", "span_id", "nonce", "t_start_ns"}

def _sanitize_parameters(data: Any, volatile_keys: set) -> Any:
    if isinstance(data, dict):
        return {k: _sanitize_parameters(v, volatile_keys) for k, v in data.items() if k not in volatile_keys}
    if isinstance(data, list):
        return [_sanitize_parameters(item, volatile_keys) for item in data]
    return data

def _fingerprint_step(step: Dict[str, Any], volatile_keys: set) -> str:
    sanitized_input = _sanitize_parameters(step.get("input", {}), volatile_keys)
    canonical_json = json.dumps(sanitized_input, sort_keys=True)
    return f"{step.get('name', 'UNKNOWN')}::{canonical_json}"

def compute_structural_distance(
    actual_steps: List[Dict[str, Any]], 
    golden_steps: List[Dict[str, Any]],
    volatile_keys: set = DEFAULT_VOLATILE_KEYS
) -> Tuple[int, List[str]]:
    """
    Computes Levenshtein edit distance over canonical tool invocation tokens.
    Returns (edit_distance, human_readable_diffs).
    """
    seq1 = [_fingerprint_step(s, volatile_keys) for s in actual_steps]
    seq2 = [_fingerprint_step(s, volatile_keys) for s in golden_steps]

    # Fast paths for identical or empty trajectories
    if seq1 == seq2:
        return 0, []
    if not seq1:
        return len(seq2), [f"[+] MISSING_GOLDEN_TOOL: {s}" for s in seq2]
    if not seq2:
        return len(seq1), [f"[-] REDUNDANT_TOOL_CALL: {s}" for s in seq1]

    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1): dp[i][0] = i
    for j in range(n + 1): dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i - 1] == seq2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    diffs = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and seq1[i - 1] == seq2[j - 1]:
            i -= 1; j -= 1
        elif i > 0 and (j == 0 or dp[i][j] == dp[i - 1][j] + 1):
            diffs.append(f"[-] REDUNDANT_TOOL_CALL: {seq1[i - 1]}")
            i -= 1
        elif j > 0 and (i == 0 or dp[i][j] == dp[i][j - 1] + 1):
            diffs.append(f"[+] MISSING_GOLDEN_TOOL: {seq2[j - 1]}")
            j -= 1
        else:
            diffs.append(f"[~] PARAM_MISMATCH: Actual({seq1[i - 1]}) != Golden({seq2[j - 1]})")
            i -= 1; j -= 1

    diffs.reverse()
    return dp[m][n], diffs

def evaluate_trajectory_gate(
    actual_trace: Dict[str, Any],
    golden_trace: Dict[str, Any],
    max_edit_distance: int = 0,
    cost_tolerance_ratio: float = 1.25,
    latency_tolerance_ratio: float = 1.50
) -> Dict[str, Any]:
    """
    Composite evaluation gate for CI.
    Passes if structural edit distance is within bounds AND cost/latency do not blow out.
    """
    actual_steps = actual_trace.get("steps", [])
    golden_steps = golden_trace.get("steps", [])

    edit_dist, diffs = compute_structural_distance(actual_steps, golden_steps)

    actual_cost = actual_trace.get("total_cost_usd", 0.0)
    golden_cost = max(golden_trace.get("total_cost_usd", 0.00001), 0.00001)
    cost_ratio = actual_cost / golden_cost

    actual_latency = actual_trace.get("duration_ms", 0.0)
    golden_latency = max(golden_trace.get("duration_ms", 1.0), 1.0)
    latency_ratio = actual_latency / golden_latency

    passed = (
        edit_dist <= max_edit_distance and
        cost_ratio <= cost_tolerance_ratio and
        latency_ratio <= latency_tolerance_ratio
    )

    return {
        "passed": passed,
        "structural_edit_distance": edit_dist,
        "diffs": diffs,
        "cost_ratio": round(cost_ratio, 3),
        "latency_ratio": round(latency_ratio, 3),
        "verdict": "MERGE_APPROVED" if passed else "REGRESSION_DETECTED_BLOCKED"
    }
