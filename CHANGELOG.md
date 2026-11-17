# Changelog

All notable changes to **AgentTrace** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-11-10

### Initial Release: Distributed Tracing & Telemetry for AI Agents & Tool IPC

#### ⚡ Core Capabilities
- **Zero External Dependencies**:
  - Pure Python standard library (`json`, `sqlite3`, `time`, `dataclasses`, `uuid`, `urllib.request`).
  - Strict zero-bloat architecture without pulling in heavy OTel/Pydantic/Requests dependency trees.
- **W3C Distributed Trace Context**:
  - Full conformance with RFC `traceparent` (`00-{trace_id}-{span_id}-{flags}`).
  - Bi-directional formatting and parsing for cross-service, cross-agent, and tool IPC correlation.
- **Agentic Workflow Spans & Event Graphs**:
  - First-class support for `SpanKind.AGENT`, `SpanKind.ROUTER`, `SpanKind.TOOL`, `SpanKind.LLM`, and `SpanKind.INTERNAL`.
  - Nested parent-child span execution trees.
  - Discrete span events with millisecond timestamps.
- **Dual Persistent Storage Sinks**:
  - Append-only local JSONL streams (`~/.agenttrace/traces/{trace_id}.jsonl`).
  - Relational SQLite event store (`~/.agenttrace/traces/agenttrace.db`) for instant local querying.
- **Pure Python OTLP HTTP/JSON Exporter**:
  - Directly serializes spans to the official OpenTelemetry Protocol (`/v1/traces`) JSON schema.
  - Plugs natively into Jaeger, Grafana Tempo, Honeycomb, or any OTel collector without installing `opentelemetry-sdk`.
- **CLI Tree Visualizer (`agenttrace-cli`)**:
  - Visual ASCII execution tree renderer (`agenttrace --tree <trace_id>`).
  - Workflow summary, error auditing, and trace listing.
