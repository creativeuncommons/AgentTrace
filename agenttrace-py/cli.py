#!/usr/bin/env python3
"""
AgentTrace CLI Inspection & Tree Visualizer
===========================================
Displays ASCII execution graphs of agent workflow loops, tool execution trees,
and OTel telemetry streams.
"""

import os
import sys
import argparse
import json

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from agenttrace import TraceStorage, OTLPExporter, AgentSpan, SpanKind, SpanStatus


def print_trace_tree(storage: TraceStorage, trace_id: str):
    spans = storage.get_trace_spans(trace_id)
    if not spans:
        print(f"❌ No spans found for Trace ID: {trace_id}")
        return

    print(f"\n================================================================================")
    print(f"🌲 AgentTrace Execution Tree: {trace_id}")
    print(f"================================================================================")

    # Build parent -> children map
    children_map = {}
    root_spans = []
    for s in spans:
        pid = s.get("parent_span_id")
        if not pid or pid == "":
            root_spans.append(s)
        else:
            children_map.setdefault(pid, []).append(s)

    def render_node(span, prefix="", is_last=True):
        connector = "└── " if is_last else "├── "
        status_icon = "✅" if span.get("status") == "OK" else "🛑"
        kind_badge = f"[{span.get('kind')}]"
        duration_str = f"({span.get('duration_ms', 0):.2f} ms)" if span.get("duration_ms") else ""

        print(f"{prefix}{connector}{status_icon} {kind_badge} {span.get('name')} {duration_str}")

        # Print salient attributes
        attrs = span.get("attributes", {})
        attr_prefix = prefix + ("    " if is_last else "│   ")
        if attrs:
            for k in ["tool.name", "tool.transport", "agent.name", "model", "gen_ai.request.model", "tier"]:
                if k in attrs:
                    print(f"{attr_prefix}  🏷️ {k}: {attrs[k]}")

        # Print events
        events = span.get("events", [])
        for e in events:
            print(f"{attr_prefix}  ⚡ Event: {e.get('name')}")

        children = children_map.get(span.get("span_id"), [])
        for i, child in enumerate(children):
            render_node(child, prefix + ("    " if is_last else "│   "), i == len(children) - 1)

    for i, root in enumerate(root_spans):
        render_node(root, is_last=(i == len(root_spans) - 1))

    print("=" * 80 + "\n")


def print_traces_list(storage: TraceStorage, limit: int = 50):
    traces = storage.list_traces(limit=limit)
    if not traces:
        print("ℹ️ No agent traces recorded yet.")
        return

    print(f"\n================================================================================")
    print(f"📋 AgentTrace Workflows (Showing last {len(traces)})")
    print(f"================================================================================")
    print(f"{'Trace ID':<34} | {'Spans':<6} | {'Duration (ms)':<14} | {'Kinds Present':<20} | {'Errors'}")
    print("-" * 86)

    for t in traces:
        err_str = f"🛑 {t.get('error_count')} err" if t.get("error_count", 0) > 0 else "✅ None"
        print(
            f"{t.get('trace_id'):<34} | "
            f"{t.get('total_spans'):<6} | "
            f"{t.get('total_duration_ms', 0.0):<14} | "
            f"{t.get('kinds', 'N/A')[:20]:<20} | "
            f"{err_str}"
        )
    print("=" * 86 + "\n")


def main():
    parser = argparse.ArgumentParser(description="AgentTrace CLI Inspection Tool")
    parser.add_argument("--trace", "-t", help="Trace ID to view execution tree")
    parser.add_argument("--tree", action="store_true", help="Display ASCII tree view")
    parser.add_argument("--list", "-l", action="store_true", help="List recent agent traces")
    parser.add_argument("--storage-dir", help="Custom trace directory")
    parser.add_argument("--limit", type=int, default=50, help="Max traces to list")

    args = parser.parse_args()
    storage = TraceStorage(base_dir=args.storage_dir)

    if args.trace:
        print_trace_tree(storage, args.trace)
    elif args.list:
        print_traces_list(storage, limit=args.limit)
    else:
        print_traces_list(storage, limit=10)


if __name__ == "__main__":
    main()
