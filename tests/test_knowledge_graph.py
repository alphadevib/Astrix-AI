"""Tests for the Spacecraft Ontological Knowledge Graph."""

from __future__ import annotations

from backend.app.memory.knowledge_graph import SpacecraftKnowledgeGraph


def test_knowledge_graph_topology():
    kg = SpacecraftKnowledgeGraph()
    assert len(kg.nodes) > 15
    assert len(kg.edges) > 15

    # Check component topology
    assert "comp:wheel_3" in kg.nodes
    assert "sub:ADCS" in kg.nodes


def test_cross_correlation_query():
    kg = SpacecraftKnowledgeGraph()
    results = kg.query_cross_correlations(["wheel_3_vibration", "wheel_3_current"])
    assert len(results) > 0
    top = results[0]
    assert "Reaction wheel" in top["failure_mode"]
    assert top["match_ratio"] >= 0.5


def test_anomaly_explanation():
    kg = SpacecraftKnowledgeGraph()
    explanation = kg.explain_anomaly("ADCS", ["wheel_3_vibration", "wheel_3_current"])
    assert "Reaction wheel" in explanation
    assert "ADCS" in explanation
