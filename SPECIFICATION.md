# AgentTrace Protocol & Architecture Specification

## 1. Scope & Objective
AgentTrace defines a zero-overhead, distributed telemetry and execution graph protocol specifically optimized for autonomous AI agents, tool dispatch runtimes (including ZeroMCP), and intelligent inference routers (HyperGate).

---

## 2. Distributed Trace Context Alignment (W3C)

AgentTrace enforces the **W3C Trace Context Specification**:
* Canonical wire header: `traceparent`
* Format: `version-trace_id-parent_id-trace_flags`
  * `version`: `00`
  * `trace_id`: 32 hexadecimal characters (16 bytes)
  * `parent_id`: 16 hexadecimal characters (8 bytes)
  * `trace_flags`: `01` (recorded / sampled), `00` (unsampled)

```text
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

---

## 3. Span Hierarchy & Kind Classification

| Span Kind | Semantic Definition | Typical Parent | Attributes Captured |
| :--- | :--- | :--- | :--- |
| **`AGENT`** | High-level autonomous agent loop / multi-turn session | Root (`None`) | `agent.name`, `agent.goal`, `agent.max_turns` |
| **`ROUTER`** | Gateways, fast-path classifiers, budget checks | `AGENT` | `tier`, `cost_usd`, `budget_remaining`, `overhead_ms` |
| **`TOOL`** | Discrete tool invocation (MCP, stdio, HTTP, CLI) | `AGENT` | `tool.name`, `tool.transport`, `tool.exit_code`, `tool.latency_ms` |
| **`LLM`** | Upstream inference completion request | `AGENT` or `ROUTER` | `gen_ai.system`, `gen_ai.request.model`, `prompt_tokens`, `completion_tokens` |
| **`INTERNAL`** | Sub-step pipeline operation | Any | Custom attributes |

---

## 4. OpenTelemetry (OTel) Semantic Mapping

AgentTrace maps span attributes directly to the OpenTelemetry GenAI Semantic Conventions:
* `gen_ai.system`: `"openai"`, `"anthropic"`, `"google"`, `"hypergate"`
* `gen_ai.request.model`: Model identifier (`"gpt-4o"`, `"o3-mini"`)
* `gen_ai.usage.prompt_tokens`: Input token count
* `gen_ai.usage.completion_tokens`: Output token count
* `gen_ai.client.token.cost`: Exact dollar cost calculated deterministically
* `tool.name`: Name of tool executed
* `tool.transport`: Protocol (`"zeromcp_stdio"`, `"http"`, `"direct"`)

---

## 5. Storage Layout & Persistence Invariant
* **Raw Stream**: `~/.agenttrace/traces/{trace_id}.jsonl` (append-only JSON Lines, sub-millisecond flushes).
* **Relational Ledger**: `~/.agenttrace/traces/agenttrace.db` (SQLite with `idx_spans_trace`).
