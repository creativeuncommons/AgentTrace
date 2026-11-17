#!/usr/bin/env python3
"""
AgentTrace OpenTelemetry (OTLP) Exporter Tests
==============================================
Verifies compliance of serialized JSON spans with standard OTel specification.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_DIR = os.path.join(PROJECT_ROOT, "agenttrace-py")

if PY_DIR not in sys.path:
    sys.path.insert(0, PY_DIR)

from agenttrace import (
    OTLPExporter,
    AgentSpan,
    SpanKind,
    SpanStatus,
    generate_trace_id
)


class TestOTLPExporter(unittest.TestCase):
    def setUp(self):
        self.exporter = OTLPExporter(service_name="hypergate-agent")

    def test_otlp_serialization_schema(self):
        trace_id = generate_trace_id()
        span = AgentSpan(
            name="llm.generate",
            trace_id=trace_id,
            kind=SpanKind.LLM,
            attributes={
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4o-mini",
                "gen_ai.usage.prompt_tokens": 150,
                "is_cached": False,
                "cost_usd": 0.00015
            }
        )
        span.finish(status=SpanStatus.OK)

        payload = self.exporter.serialize_spans([span])

        self.assertIn("resourceSpans", payload)
        res_spans = payload["resourceSpans"]
        self.assertEqual(len(res_spans), 1)

        # Verify resource attributes
        res_attrs = res_spans[0]["resource"]["attributes"]
        attr_map = {a["key"]: a["value"] for a in res_attrs}
        self.assertEqual(attr_map["service.name"]["stringValue"], "hypergate-agent")

        # Verify scope spans
        scope_spans = res_spans[0]["scopeSpans"][0]["spans"]
        self.assertEqual(len(scope_spans), 1)
        otlp_span = scope_spans[0]
        self.assertEqual(otlp_span["traceId"], trace_id)
        self.assertEqual(otlp_span["name"], "llm.generate")

        # Verify attributes conversion
        span_attrs = {a["key"]: a["value"] for a in otlp_span["attributes"]}
        self.assertEqual(span_attrs["gen_ai.system"]["stringValue"], "openai")
        self.assertEqual(span_attrs["is_cached"]["boolValue"], False)
        self.assertEqual(span_attrs["gen_ai.usage.prompt_tokens"]["doubleValue"], 150.0)


if __name__ == "__main__":
    unittest.main()
