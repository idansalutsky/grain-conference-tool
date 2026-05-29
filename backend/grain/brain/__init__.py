"""Grain Brain — a LangGraph-orchestrated agent subsystem.

The doctrine (why this package exists), in four moves:

  1. CAPTURE   unstructured reality (a voice/text note about a person met) into
               structured data.
  2. FILTER    before ANYTHING enters memory — a quality gate. This is the most
               important step. The prior data layer overflowed with noise
               because nothing gated for fit. Here, `gate_node` rejects
               competitors, off-ICP events, and already-known items.
  3. COMPRESS  accepted facts into evolving memory "spaces" — rolling summaries,
               NOT raw transcripts. "Don't overflow the brain."
  4. SURFACE   / research over the structured memory (the query path).

LangGraph maps cleanly onto this: nodes = steps/agents, edges = control flow,
and a `classify` node routes each input to the right subgraph + tool.

Public surface:
    from grain.brain import spaces            # long-term memory
    from grain.brain.graphs import build_brain, run_brain, resume_brain
"""
from __future__ import annotations

from . import spaces  # noqa: F401  (re-export for convenience)

__all__ = ["spaces"]
