#!/usr/bin/env python3
"""
AgentTrace Core Tracer & Hierarchy Tests
========================================
Verifies span lifecycle, W3C traceparent formatting/parsing, and nested parent-child correlation.
"""

import os
import sys
import unittest
import shutil
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    parse_traceparent
)


class TestAgentTracer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agenttrace_test_")
        self.tracer = AgentTracer(service_name="test-agent", storage_dir=self.test_dir)

    def tearDown(self):
        if hasattr(self, "tracer") and self.tracer is not None:
            self.tracer.close()
        if hasattr(self, "test_dir") and os.path.exists(self.test_dir):
            try:
                shutil.rmtree(self.test_dir)
            except Exception:
                pass

    def test_w3c_traceparent(self):
        t_id = generate_trace_id()
        s_id = generate_span_id()
        header = format_traceparent(t_id, s_id, sampled=True)
        self.assertTrue(header.startswith("00-"))
        self.assertTrue(header.endswith("-01"))

        parsed = parse_traceparent(header)
        self.assertIsNotNone(parsed)
        ver, pt_id, ps_id, flags = parsed
        self.assertEqual(ver, "00")
        self.assertEqual(pt_id, t_id)
        self.assertEqual(ps_id, s_id)
        self.assertEqual(flags, "01")

    def test_nested_span_hierarchy(self):
        trace_id = generate_trace_id()

        with self.tracer.span("agent.research_task", kind=SpanKind.AGENT, trace_id=trace_id) as root:
            root.set_attribute("agent.goal", "Analyze dataset")

            with self.tracer.span("router.classify", kind=SpanKind.ROUTER) as router_span:
                router_span.set_attribute("tier", "TIER_1_FAST")

            with self.tracer.span("tool.execute_query", kind=SpanKind.TOOL) as tool_span:
                tool_span.set_attribute("tool.name", "sql_query")
                tool_span.add_event("query.executed", {"rows": 42})

        # Fetch persisted spans from storage
        spans = self.tracer.storage.get_trace_spans(trace_id)
        self.assertEqual(len(spans), 3)

        # Map by name
        by_name = {s["name"]: s for s in spans}
        root = by_name["agent.research_task"]
        router = by_name["router.classify"]
        tool = by_name["tool.execute_query"]

        # Check hierarchy
        self.assertIsNone(root["parent_span_id"])
        self.assertEqual(router["parent_span_id"], root["span_id"])
        self.assertEqual(tool["parent_span_id"], root["span_id"])
        self.assertEqual(root["attributes"]["agent.goal"], "Analyze dataset")
        self.assertEqual(len(tool["events"]), 1)
        self.assertEqual(tool["events"][0]["name"], "query.executed")

    def test_exception_span_capture(self):
        trace_id = generate_trace_id()

        try:
            with self.tracer.span("tool.failing_api", kind=SpanKind.TOOL, trace_id=trace_id) as s:
                raise ValueError("Connection timeout")
        except ValueError:
            pass

        spans = self.tracer.storage.get_trace_spans(trace_id)
        self.assertEqual(len(spans), 1)
        failing = spans[0]
        self.assertEqual(failing["status"], "ERROR")
        self.assertIn("Connection timeout", failing["status_message"])

    def test_circuit_breaker_fail_closed_event(self):
        trace_id = generate_trace_id()
        with self.tracer.span("router.check_budget", kind=SpanKind.ROUTER, trace_id=trace_id) as s:
            self.tracer.record_circuit_breaker_trip(
                span=s,
                budget_limit_usd=0.50,
                rolling_cost_usd=0.502,
                reason="Budget ceiling exceeded"
            )

        spans = self.tracer.storage.get_trace_spans(trace_id)
        self.assertEqual(len(spans), 1)
        cb_span = spans[0]
        self.assertEqual(cb_span["status"], "ERROR")
        self.assertTrue(cb_span["attributes"]["circuit_tripped"])
        self.assertEqual(cb_span["attributes"]["status_code"], 429)
        self.assertEqual(cb_span["events"][0]["name"], "circuit_breaker.tripped")

    def test_header_propagation_injection_extraction(self):
        with self.tracer.span("agent.root_task", kind=SpanKind.AGENT) as root:
            headers = self.tracer.inject_trace_headers()
            self.assertIn("traceparent", headers)
            self.assertIn("x-hypergate-trace-id", headers)

            extracted_trace, extracted_span = self.tracer.extract_trace_headers(headers)
            self.assertEqual(extracted_trace, root.trace_id)
            self.assertEqual(extracted_span, root.span_id)


if __name__ == "__main__":
    unittest.main()
