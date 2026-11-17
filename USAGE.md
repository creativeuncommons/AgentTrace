# AgentTrace Integration Manual

AgentTrace is a zero-dependency, OpenTelemetry-compatible tracing engine for autonomous AI agents and tool execution pipelines.

---

## 🏛️ Technical Architecture & Protocol Contract

AgentTrace is built around three core architectural pillars:
* **Zero Runtime Bloat**: Pure Python standard library (`sqlite3`, `json`, `uuid`, `time`, `contextvars`, `dataclasses`).
* **W3C Trace Context Conformance**: Seamless cross-service distributed traceparent propagation (`00-{trace_id}-{span_id}-{flags}`).
* **OTLP Wire Compatibility**: In-memory span batches serialize to OpenTelemetry standard JSON payloads ready for ingestion into Honeycomb, Datadog, Jaeger, or SigNoz.

---

## 1. Quickstart

No external packages needed (`pip install` nothing). Pure standard library.

```python
from agenttrace import get_tracer, SpanKind

tracer = get_tracer(service_name="researcher-agent")

# Trace an entire agent execution graph
with tracer.span("agent.research_loop", kind=SpanKind.AGENT) as root:
    root.set_attribute("agent.goal", "Analyze dataset")

    # Trace a tool call
    with tracer.span("tool.query_sql", kind=SpanKind.TOOL) as tool_span:
        tool_span.set_attribute("tool.name", "query_sql")
        # Run tool...
        tool_span.add_event("data.fetched", {"row_count": 140})

    # Trace model routing / completion
    with tracer.span("router.dispatch", kind=SpanKind.ROUTER) as router_span:
        router_span.set_attribute("tier", "TIER_1_FAST")
        router_span.set_attribute("cost_usd", 0.00015)
```

---

## 2. Using Decorators

```python
from agenttrace import get_tracer

tracer = get_tracer()

@tracer.trace_agent_loop("data_analyst", max_turns=20)
def run_agent():
    fetch_data()
    analyze_metrics()

@tracer.trace_tool_call("fetch_data", transport="zeromcp_stdio")
def fetch_data():
    return {"status": "ok"}

@tracer.trace_tool_call("analyze_metrics", transport="direct")
def analyze_metrics():
    return {"mean": 42.0}

run_agent()
```

---

## 3. Visualizing Execution Graphs with CLI

```bash
# List recent agent traces
python3 -m agenttrace --list

# Render ASCII execution tree
python3 -m agenttrace --tree <trace_id>
```

Example output:
```text
🌲 AgentTrace Execution Tree: c1a8e932b10a44f798e24c2918fa2201
================================================================================
└── ✅ [AGENT] agent.data_analyst.loop (12.40 ms)
    ├── 🏷️ agent.name: data_analyst
    ├── ✅ [TOOL] tool.fetch_data (3.10 ms)
    │   ├── 🏷️ tool.name: fetch_data
    │   └── 🏷️ tool.transport: zeromcp_stdio
    └── ✅ [TOOL] tool.analyze_metrics (1.80 ms)
        └── 🏷️ tool.name: analyze_metrics
================================================================================
```

---

## 4. Exporting to OpenTelemetry Collectors (Jaeger, Tempo, Honeycomb)

```python
from agenttrace import get_tracer, OTLPExporter

tracer = get_tracer()
storage = tracer.storage

# Retrieve spans for a trace
spans = [
    # ... AgentSpans
]

# Export to any standard OpenTelemetry endpoint over HTTP
exporter = OTLPExporter(endpoint_url="http://localhost:4318/v1/traces", service_name="my-agent")
success = exporter.export(spans)
print("Exported to OTel:", success)
```

---

## 5. Distributed Header Contract (W3C traceparent & HyperGate)

AgentTrace natively injects and extracts distributed trace headers across HTTP and MCP tool boundaries:

| Header Name | Wire Format | Description |
| :--- | :--- | :--- |
| `traceparent` | `00-{trace_id}-{span_id}-{flags}` | Standard W3C Distributed Trace Context header |
| `x-hypergate-trace-id` | 32-character hexadecimal UUID | HyperGate inference gateway session correlation |

```python
# Automatic header injection for outbound agent calls
headers = tracer.inject_trace_headers({"User-Agent": "MyAgent/1.0"})
# Returns: {'User-Agent': '...', 'traceparent': '00-...', 'x-hypergate-trace-id': '...'}

# Automatic header extraction on inbound server/tool calls
trace_id, parent_id = tracer.extract_trace_headers(inbound_headers)
```

---

## 6. Automated CI Trajectory Grader (`agenttrace-check`)

AgentTrace includes a zero-dependency golden-path trajectory grader for CI/CD merge gates to ensure agent tool execution does not regress in cost, latency, or execution order:

```bash
python3 scripts/agenttrace-check \
  --actual .agenttrace/traces/trace_actual.json \
  --golden tests/fixtures/golden_trace.json \
  --max-dist 0 \
  --cost-tol 1.25 \
  --lat-tol 1.50
```

### Metrics Evaluated:
* **Structural Edit Distance**: Asserts exact tool calling sequence alignment against golden path.
* **Cost Inflation Ratio**: Enforces dollar expenditure remains within tolerance (default $\le 1.25\times$).
* **Latency Inflation Ratio**: Enforces wall-clock execution latency stays bounded (default $\le 1.50\times$).
