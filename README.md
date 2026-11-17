# 🌲 AgentTrace: Zero-Dependency Distributed Tracing for AI Agents & Tool IPC

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Zero External Dependencies](https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen.svg)](agenttrace-py/)
[![OpenTelemetry](https://img.shields.io/badge/standard-OTel%20%2B%20W3C%20Traceparent-purple.svg)](SPECIFICATION.md)

**AgentTrace** is an ultra-lean, zero-external-dependency distributed tracing and telemetry framework designed specifically for autonomous AI agents, multi-turn task loops, tool execution trees, and Model Context Protocol (MCP) IPC servers.

---

## ⚡ Why AgentTrace?

Existing AI observability tools (LangSmith, Langfuse, Phoenix) are heavy, pull in massive dependency trees (`pydantic`, `requests`, `opentelemetry-sdk`, `grpc`), and focus on individual prompt/completion calls rather than **agent loops, tool IPC framing, and circuit-breaker tripwires**.

AgentTrace solves this with:
1. **Strictly Zero Dependencies**: Pure Python standard library (`json`, `sqlite3`, `time`, `dataclasses`, `uuid`, `urllib.request`).
2. **W3C Distributed Trace Context**: Full `traceparent` support (`00-{trace_id}-{span_id}-01`) for end-to-end correlation across multi-agent systems and MCP tool servers.
3. **OpenTelemetry GenAI Semantic Alignment**: Native OTel attribute mapping (`gen_ai.system`, `gen_ai.request.model`, `tool.transport`).
4. **Local Append-Only JSONL & SQLite Sinks**: Sub-millisecond persistence without sending agent thoughts to third-party clouds.
5. **Pure Python OTLP HTTP/JSON Exporter**: Pipes directly into Jaeger, Grafana Tempo, Honeycomb, or Datadog without heavy SDK bloat.

---

## 📦 Project Structure

```text
AgentTrace/
├── README.md                 # Public open-source README & quickstart
├── SPECIFICATION.md          # W3C trace context & OTel semantic mapping
├── USAGE.md                  # Integration guide, decorators, & OTLP export
├── CHANGELOG.md              # Semantic version history (v0.1.0)
├── LICENSE                   # Apache 2.0 License
├── agenttrace-py/            # Zero-dependency Python SDK
│   ├── __init__.py
│   ├── agenttrace.py         # Tracer, AgentSpan, TraceStorage, OTLPExporter
│   └── cli.py                # Visual ASCII execution tree renderer
├── tests/                    # Conformance & OTLP serialization tests
│   ├── test_tracer_core.py
│   └── test_otlp_export.py
└── scripts/
    └── promote_to_standalone.py # Standalone export pipeline
```

---

## 🚀 Quick Example

```python
from agenttrace import get_tracer, SpanKind

tracer = get_tracer(service_name="analyst-agent")

with tracer.span("agent.loop", kind=SpanKind.AGENT) as root:
    root.set_attribute("agent.goal", "Process financial filings")

    with tracer.span("tool.mcp_query", kind=SpanKind.TOOL) as tool:
        tool.set_attribute("tool.name", "fetch_sec_filing")
        tool.set_attribute("tool.transport", "zeromcp_stdio")
        tool.add_event("filing.parsed", {"ticker": "AAPL", "pages": 84})
```

### Render Execution Tree via CLI

```bash
python3 -m agenttrace --tree <trace_id>
```

```text
🌲 AgentTrace Execution Tree: c1a8e932b10a44f798e24c2918fa2201
================================================================================
└── ✅ [AGENT] agent.loop (14.20 ms)
    ├── 🏷️ agent.goal: Process financial filings
    └── ✅ [TOOL] tool.mcp_query (8.40 ms)
        ├── 🏷️ tool.name: fetch_sec_filing
        ├── 🏷️ tool.transport: zeromcp_stdio
        └── ⚡ Event: filing.parsed
================================================================================
```

### Golden-Path CI Trajectory Grader

Assert agent trajectory conformance in CI pipelines:
```bash
python3 scripts/agenttrace-check --actual actual.json --golden golden.json --max-dist 0
```

---

## 📄 License

Distributed under the [Apache License 2.0](LICENSE).
