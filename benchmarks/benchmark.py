#!/usr/bin/env python3
"""
AgentTrace Micro-Benchmark Suite
================================
Reproducible performance, latency, memory (RSS), and throughput benchmarks
measuring zero-overhead span instrumentation, W3C traceparent parsing,
and OTLP telemetry serialization.

Zero external dependencies (pure Python standard library).
"""

import os
import sys
import time
import shutil
import tempfile
import statistics
import resource
from typing import Dict, Any, List

BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BENCHMARK_DIR)
PY_DIR = os.path.join(PROJECT_ROOT, "agenttrace-py")

if PY_DIR not in sys.path:
    sys.path.insert(0, PY_DIR)

from agenttrace import (
    AgentTracer,
    SpanKind,
    SpanStatus,
    generate_trace_id,
    generate_span_id,
    format_traceparent,
    parse_traceparent,
    OTLPExporter,
)


def get_current_rss_kb() -> int:
    """Returns current process Resident Set Size (RSS) in KB."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return int(usage.ru_maxrss / 1024)
    return int(usage.ru_maxrss)


def run_benchmarks(iterations: int = 5000):
    print("============================================================")
    print(f"🛰️  AgentTrace Micro-Benchmark Suite ({iterations:,} iterations)")
    print("============================================================\n")

    initial_rss = get_current_rss_kb()
    print(f"📊 Baseline Process Memory (RSS): {initial_rss:,} KB (~{initial_rss/1024:.2f} MB)")

    temp_storage = tempfile.mkdtemp(prefix="agenttrace_bench_")

    try:
        # 1. Benchmark: W3C Traceparent Header Generation & Parsing
        trace_ids = [generate_trace_id() for _ in range(100)]
        span_ids = [generate_span_id() for _ in range(100)]
        headers = [format_traceparent(trace_ids[i % 100], span_ids[i % 100], sampled=True) for i in range(100)]

        w3c_latencies_us: List[float] = []
        for i in range(iterations):
            h = headers[i % len(headers)]
            t0 = time.perf_counter()
            parse_traceparent(h)
            t1 = time.perf_counter()
            w3c_latencies_us.append((t1 - t0) * 1_000_000)

        # 2. Benchmark: In-Memory Span Lifecycle (Start -> Attributes -> Event -> End)
        tracer = AgentTracer(service_name="bench-agent", storage_dir=temp_storage)

        span_latencies_us: List[float] = []
        for i in range(iterations):
            t0 = time.perf_counter()
            with tracer.span("tool_execution", kind=SpanKind.INTERNAL) as span:
                span.set_attribute("tool.name", "weather_lookup")
                span.set_attribute("tool.call_idx", i)
                span.add_event("cache_hit", {"cached_key": f"key_{i}"})
            t1 = time.perf_counter()
            span_latencies_us.append((t1 - t0) * 1_000_000)

        # 3. Benchmark: OTLP JSON Export Serialization
        exporter = OTLPExporter(service_name="bench-agent")
        # Collect 50 spans for batch export
        spans_to_export = []
        for i in range(50):
            with tracer.span(f"batch_span_{i}") as s:
                s.set_attribute("iteration", i)
                spans_to_export.append(s)

        otlp_latencies_us: List[float] = []
        for _ in range(iterations // 5):
            t0 = time.perf_counter()
            exporter.serialize_spans(spans_to_export)
            t1 = time.perf_counter()
            otlp_latencies_us.append((t1 - t0) * 1_000_000)

        tracer.close()

        # Metrics calculation
        w3c_p50 = statistics.median(w3c_latencies_us)
        w3c_p90 = sorted(w3c_latencies_us)[int(len(w3c_latencies_us) * 0.90)]
        w3c_p99 = sorted(w3c_latencies_us)[int(len(w3c_latencies_us) * 0.99)]
        w3c_ops = 1_000_000 / w3c_p50 if w3c_p50 > 0 else 0

        span_p50 = statistics.median(span_latencies_us)
        span_p90 = sorted(span_latencies_us)[int(len(span_latencies_us) * 0.90)]
        span_p99 = sorted(span_latencies_us)[int(len(span_latencies_us) * 0.99)]
        span_ops = 1_000_000 / span_p50 if span_p50 > 0 else 0

        otlp_p50 = statistics.median(otlp_latencies_us)
        otlp_p90 = sorted(otlp_latencies_us)[int(len(otlp_latencies_us) * 0.90)]
        otlp_p99 = sorted(otlp_latencies_us)[int(len(otlp_latencies_us) * 0.99)]
        otlp_throughput = (len(spans_to_export) * 1_000_000 / otlp_p50) if otlp_p50 > 0 else 0

        final_rss = get_current_rss_kb()

        print("------------------------------------------------------------")
        print("📈 Benchmark Results Summary")
        print("------------------------------------------------------------")
        print("Component: W3C Traceparent Header Parsing")
        print(f"  p50: {w3c_p50:.2f} µs | p90: {w3c_p90:.2f} µs | p99: {w3c_p99:.2f} µs")
        print(f"  Throughput: {w3c_ops:,.0f} headers/sec")
        print()
        print("Component: Complete Span Lifecycle (Start + Attributes + Event + End)")
        print(f"  p50: {span_p50:.2f} µs | p90: {span_p90:.2f} µs | p99: {span_p99:.2f} µs")
        print(f"  Throughput: {span_ops:,.0f} spans/sec (< 250.0 µs SQLite budget: {'✅ MET' if span_p50 < 250.0 else '❌ EXCEEDED'})")
        print()
        print(f"Component: OTLP Telemetry Export Serialization (50-span batch)")
        print(f"  p50: {otlp_p50:.2f} µs | p90: {otlp_p90:.2f} µs | p99: {otlp_p99:.2f} µs")
        print(f"  Throughput: {otlp_throughput:,.0f} spans/sec")
        print()
        print(f"🧠 Final Process Memory (RSS): {final_rss:,} KB (Delta: +{final_rss - initial_rss:,} KB)")
        print("============================================================\n")

        print("📋 Markdown Summary Table:")
        print("| Operation | p50 Latency | p90 Latency | p99 Latency | Throughput |")
        print("| :--- | :---: | :---: | :---: | :---: |")
        print(f"| **W3C Traceparent Parse** | **{w3c_p50:.2f} µs** | **{w3c_p90:.2f} µs** | **{w3c_p99:.2f} µs** | **{w3c_ops:,.0f} headers/s** |")
        print(f"| **Span Lifecycle (Persisted)** | **{span_p50:.2f} µs** | **{span_p90:.2f} µs** | **{span_p99:.2f} µs** | **{span_ops:,.0f} spans/s** |")
        print(f"| **OTLP JSON Serialization** | **{otlp_p50 / 50:.2f} µs/span** | **{otlp_p90 / 50:.2f} µs/span** | **{otlp_p99 / 50:.2f} µs/span** | **{otlp_throughput:,.0f} spans/s** |")

    finally:
        shutil.rmtree(temp_storage, ignore_errors=True)


if __name__ == "__main__":
    run_benchmarks()
