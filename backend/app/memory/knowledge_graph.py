"""Spacecraft Ontological Knowledge Graph for ASTRIX-AI.

Links:
  Telemetry Channels <-> Subsystem Components <-> Failure Modes <-> Recovery Procedures <-> Safety Rules <-> Historical Lessons

Provides graph-guided retrieval and causal reasoning across spacecraft subsystems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str  # "channel", "component", "subsystem", "failure_mode", "action", "rule"
    label: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str  # "PART_OF", "MONITORS", "MANIFESTS_IN", "CORRELATED_WITH", "RESOLVES", "CONSTRAINED_BY"
    weight: float = 1.0


class SpacecraftKnowledgeGraph:
    """In-memory ontology graph for spacecraft subsystem topology and fault causation."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._adj: dict[str, list[GraphEdge]] = {}
        self._rev_adj: dict[str, list[GraphEdge]] = {}
        self._seed_ontology()

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node
        self._adj.setdefault(node.id, [])
        self._rev_adj.setdefault(node.id, [])

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)
        self._adj.setdefault(edge.source, []).append(edge)
        self._rev_adj.setdefault(edge.target, []).append(edge)

    def _seed_ontology(self) -> None:
        # Subsystems
        subs = ["ADCS", "POWER", "THERMAL", "COMMS", "CDH", "PROP"]
        for s in subs:
            self.add_node(GraphNode(id=f"sub:{s}", kind="subsystem", label=f"{s} Subsystem"))

        # Components
        comps = [
            ("comp:wheel_1", "Reaction Wheel 1", "sub:ADCS"),
            ("comp:wheel_2", "Reaction Wheel 2", "sub:ADCS"),
            ("comp:wheel_3", "Reaction Wheel 3", "sub:ADCS"),
            ("comp:wheel_4", "Reaction Wheel 4", "sub:ADCS"),
            ("comp:gyro", "3-Axis Fiber Optic Gyroscope", "sub:ADCS"),
            ("comp:star_tracker", "Autonomous Star Tracker", "sub:ADCS"),
            ("comp:battery", "Li-Ion 8S Battery Pack", "sub:POWER"),
            ("comp:solar_array", "Gallium Arsenide Solar Arrays", "sub:POWER"),
            ("comp:temp_sensor_pri", "Primary Radiator RTD Sensor", "sub:THERMAL"),
            ("comp:temp_sensor_sec", "Redundant Radiator RTD Sensor", "sub:THERMAL"),
            ("comp:obc_cpu", "Radiation-Hardened OBC CPU", "sub:CDH"),
            ("comp:transceiver", "S-Band High-Rate Transceiver", "sub:COMMS"),
        ]
        for cid, label, sub_id in comps:
            self.add_node(GraphNode(id=cid, kind="component", label=label))
            self.add_edge(GraphEdge(source=cid, target=sub_id, relation="PART_OF"))

        # Telemetry channels
        channels = [
            ("chan:w1_vib", "wheel_1_vibration", "comp:wheel_1"),
            ("chan:w2_vib", "wheel_2_vibration", "comp:wheel_2"),
            ("chan:w3_vib", "wheel_3_vibration", "comp:wheel_3"),
            ("chan:w4_vib", "wheel_4_vibration", "comp:wheel_4"),
            ("chan:w3_curr", "wheel_3_current", "comp:wheel_3"),
            ("chan:att_err", "attitude_error_deg", "comp:gyro"),
            ("chan:gyro_x", "gyro_x", "comp:gyro"),
            ("chan:gyro_z", "gyro_z", "comp:gyro"),
            ("chan:bat_v", "battery_voltage", "comp:battery"),
            ("chan:soc", "state_of_charge", "comp:battery"),
            ("chan:solar_p", "solar_power", "comp:solar_array"),
            ("chan:temp_pri", "temperature", "comp:temp_sensor_pri"),
            ("chan:temp_sec", "temperature_secondary", "comp:temp_sensor_sec"),
            ("chan:cpu_load", "cpu_load", "comp:obc_cpu"),
            ("chan:comm_sig", "communication_signal", "comp:transceiver"),
            ("chan:pkt_loss", "packet_loss", "comp:transceiver"),
        ]
        for ch_id, label, comp_id in channels:
            self.add_node(GraphNode(id=ch_id, kind="channel", label=label))
            self.add_edge(GraphEdge(source=ch_id, target=comp_id, relation="MONITORS"))

        # Failure modes
        failures = [
            ("fail:wheel_bearing_wear", "Reaction wheel mechanical bearing degradation", [
                "chan:w3_vib", "chan:w3_curr", "chan:att_err"
            ], "act:isolate_wheel_3"),
            ("fail:battery_degradation", "Lithium-ion cell internal resistance & capacity loss", [
                "chan:bat_v", "chan:soc"
            ], "act:enter_power_save"),
            ("fail:thermal_excursion", "Thermal excursion driven by sustained compute & solar dissipation", [
                "chan:temp_pri", "chan:temp_sec", "chan:cpu_load"
            ], "act:switch_data_processing_mode"),
            ("fail:thermal_sensor_bias", "Single-sensor RTD instrumentation bias", [
                "chan:temp_pri"
            ], "act:switch_to_redundant_sensor"),
            ("fail:gyro_drift", "Rate-sensor bias drift corruption", [
                "chan:gyro_x", "chan:gyro_z", "chan:att_err"
            ], "act:recalibrate_gyro_bias"),
            ("fail:comms_degradation", "Transponder RF signal degradation & link margin sag", [
                "chan:comm_sig", "chan:pkt_loss"
            ], "act:reduce_downlink_rate"),
        ]
        for fid, label, ch_list, act_id in failures:
            self.add_node(GraphNode(id=fid, kind="failure_mode", label=label))
            for ch in ch_list:
                self.add_edge(GraphEdge(source=fid, target=ch, relation="MANIFESTS_IN"))
            self.add_edge(GraphEdge(source=act_id, target=fid, relation="RESOLVES"))

        # Actions & Safety Rules
        actions = [
            ("act:isolate_wheel_3", "isolate_wheel_3", "rule:SR-005"),
            ("act:enter_power_save", "enter_power_save", "rule:SR-001"),
            ("act:switch_data_processing_mode", "switch_data_processing_mode", "rule:SR-008"),
            ("act:switch_to_redundant_sensor", "switch_to_redundant_sensor", "rule:SR-004"),
            ("act:recalibrate_gyro_bias", "recalibrate_gyro_bias", "rule:SR-002"),
            ("act:reduce_downlink_rate", "reduce_downlink_rate", "rule:SR-009"),
        ]
        for aid, label, rule_id in actions:
            self.add_node(GraphNode(id=aid, kind="action", label=label))
            self.add_node(GraphNode(id=rule_id, kind="rule", label=f"Safety Rule {rule_id.split(':')[-1]}"))
            self.add_edge(GraphEdge(source=aid, target=rule_id, relation="CONSTRAINED_BY"))

    def query_cross_correlations(self, channel_names: list[str]) -> list[dict[str, Any]]:
        """Find components and failure modes linked to multiple observed channels."""
        matched_channels = [
            nid for nid, node in self.nodes.items()
            if node.kind == "channel" and node.label in channel_names
        ]
        results = []
        for fid, node in self.nodes.items():
            if node.kind != "failure_mode":
                continue
            edges = [e for e in self._adj.get(fid, []) if e.relation == "MANIFESTS_IN"]
            manifested_in = {e.target for e in edges}
            intersection = manifested_in.intersection(matched_channels)
            if intersection:
                match_pct = len(intersection) / len(manifested_in)
                results.append({
                    "failure_mode": node.label,
                    "matched_channels": [self.nodes[c].label for c in intersection],
                    "total_expected_channels": len(manifested_in),
                    "match_ratio": round(match_pct, 2),
                })
        results.sort(key=lambda x: x["match_ratio"], reverse=True)
        return results

    def explain_anomaly(self, subsystem_name: str, signals: list[str]) -> str:
        """Produce an ontologically-grounded explanation for the operator."""
        correlations = self.query_cross_correlations(signals)
        if not correlations:
            return f"Subsystem {subsystem_name}: Multi-channel anomaly detected across {', '.join(signals)}."
        best = correlations[0]
        return (
            f"Knowledge Graph Correlation: {best['failure_mode']} matches observed signals "
            f"({', '.join(best['matched_channels'])}) with {best['match_ratio']:.0%} confidence. "
            f"Directly affects {subsystem_name} subsystem."
        )
