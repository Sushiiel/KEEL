"""Domain pack: automotive gigafactory (four halls, stamping → assembly).

Material flow is the dependency graph: presses feed conveyors feed weld cells
feed paint feed assembly. Compressed-air plants are the shared infrastructure —
a silent pressure loss e-stops 'unrelated' robots across a hall, the
latent-confounder class of Industry 4.0.
"""
from __future__ import annotations

import re

import numpy as np

from ..models import Entity, TopoEdge
from . import DomainPack, Scenario, register

EPOCH = 1_754_000_000.0
HALLS = {"HALL-A": [0.12, 0.24], "HALL-B": [0.42, 0.52], "HALL-C": [0.68, 0.26],
         "HALL-D": [0.86, 0.66]}
STATIONS = {
    "PRESS-A-1": ("HALL-A", "stamping"), "PRESS-A-2": ("HALL-A", "stamping"),
    "WELD-B-1": ("HALL-B", "welding"), "WELD-B-2": ("HALL-B", "welding"),
    "WELD-B-3": ("HALL-B", "welding"), "PAINT-C-1": ("HALL-C", "paint"),
    "ASSY-D-1": ("HALL-D", "assembly"), "ASSY-D-2": ("HALL-D", "assembly"),
    "AGV-FLEET-1": ("HALL-D", "logistics"),
}
CONVEYORS = {"CONV-A-B": ("HALL-A", "HALL-B"), "CONV-B-C": ("HALL-B", "HALL-C"),
             "CONV-C-D": ("HALL-C", "HALL-D")}
# material flow: provider -> consumers
FLOW = {
    "PRESS-A-1": ["CONV-A-B"], "PRESS-A-2": ["CONV-A-B"],
    "CONV-A-B": ["WELD-B-1", "WELD-B-2", "WELD-B-3"],
    "WELD-B-1": ["CONV-B-C"], "WELD-B-2": ["CONV-B-C"], "WELD-B-3": ["CONV-B-C"],
    "CONV-B-C": ["PAINT-C-1"], "PAINT-C-1": ["CONV-C-D"],
    "CONV-C-D": ["ASSY-D-1", "ASSY-D-2"],
    "AGV-FLEET-1": ["ASSY-D-1", "ASSY-D-2"],
    "PLC-A": ["PRESS-A-1", "PRESS-A-2"], "PLC-B": ["WELD-B-1", "WELD-B-2", "WELD-B-3"],
    "PLC-C": ["PAINT-C-1"], "PLC-D": ["ASSY-D-1", "ASSY-D-2", "AGV-FLEET-1"],
}
SERVICES = {
    "SVC:model-x-line": (
        ["PRESS-A-1", "CONV-A-B", "WELD-B-1", "WELD-B-2", "CONV-B-C", "PAINT-C-1",
         "CONV-C-D", "ASSY-D-1"],
        ["PRESS-A-2", "CONV-A-B", "WELD-B-3", "CONV-B-C", "PAINT-C-1", "CONV-C-D",
         "ASSY-D-2"], 1150, "gold"),
    "SVC:battery-line": (
        ["PRESS-A-2", "CONV-A-B", "WELD-B-3", "CONV-B-C", "CONV-C-D", "ASSY-D-2"],
        ["PRESS-A-1", "CONV-A-B", "WELD-B-1", "CONV-B-C", "CONV-C-D", "ASSY-D-1"],
        640, "gold"),
    "SVC:paint-quality": (
        ["PAINT-C-1", "PLC-C"], ["PAINT-C-1", "PLC-C"], 1790, "platinum"),
}


def build_world(store) -> None:
    for hall, pos in HALLS.items():
        store.put_entity(Entity(entity_id=hall, kind="site", layer="service",
                                site=hall, attrs={"pos": pos}))
        h = hall.split("-")[1]
        store.put_entity(Entity(entity_id=f"PNEU-{h}", kind="power", layer="utilities",
                                vendor="Atlas Copco", model="ZR-160", site=hall))
        store.put_entity(Entity(entity_id=f"PLC-{h}", kind="ne", layer="control",
                                vendor="Siemens", model="S7-1500", site=hall))
        store.put_topo_edge(TopoEdge(src=f"PNEU-{h}", dst=f"PLC-{h}",
                                     relation="feeds", valid_from=EPOCH))
    for st, (hall, layer) in STATIONS.items():
        vendor = {"stamping": "Schuler", "welding": "KUKA", "paint": "Dürr",
                  "assembly": "FANUC", "logistics": "MiR"}[layer]
        store.put_entity(Entity(entity_id=st, kind="ne", layer=layer, vendor=vendor,
                                model="", site=hall))
        h = hall.split("-")[1]
        store.put_topo_edge(TopoEdge(src=f"PNEU-{h}", dst=st, relation="feeds",
                                     valid_from=EPOCH))
    for conv, (a, b) in CONVEYORS.items():
        store.put_entity(Entity(entity_id=conv, kind="link", layer="conveyance",
                                vendor="Interroll", model="", site=a,
                                attrs={"between": [a, b]}))
    for provider, consumers in FLOW.items():
        for c in consumers:
            store.put_topo_edge(TopoEdge(src=provider, dst=c, relation="carries",
                                         valid_from=EPOCH))
    for svc, (primary, backup, customers, sla) in SERVICES.items():
        store.put_entity(Entity(entity_id=svc, kind="service", layer="service",
                                attrs={"paths": [primary, backup],
                                       "customers": customers, "sla_class": sla}))
        for el in set(primary) | set(backup):
            store.put_topo_edge(TopoEdge(src=el, dst=svc, relation="serves",
                                         valid_from=EPOCH))


TRUE_RULES = [
    ("pneu.pressure_loss", "robot.estop",        "fed",      0.85,   5,  30),
    ("pneu.pressure_loss", "press.fault",        "fed",      0.70,   5,  40),
    ("press.tool_wear",    "press.fault",        "same",     0.60, 300, 900),
    ("plc.fault",          "robot.estop",        "carried",  0.80,   2,  10),
    ("press.fault",        "flow.starved",       "carried",  0.85,  60, 240),
    ("robot.estop",        "flow.starved",       "carried",  0.80,  30,  90),
    ("conveyor.jam",       "flow.starved",       "carried",  0.90,  20,  60),
    ("agv.blocked",        "flow.starved",       "carried",  0.60,  60, 180),
    ("flow.starved",       "flow.starved",       "carried",  0.70,  60, 180),
    ("cfg.push",           "weld.quality_drift", "same",     0.05,  60, 240),
    ("weld.quality_drift", "svc.impact",         "services", 0.70, 120, 400),
    ("paint.viscosity_high","svc.impact",        "services", 0.60, 120, 300),
    ("flow.starved",       "svc.impact",         "services", 0.85,  30, 120),
    ("svc.throughput_low", "svc.line_stop",      "same",     0.40, 300, 900),
]

SEVERITY = {"pneu.pressure_loss": 1, "press.tool_wear": 3, "press.fault": 1,
            "plc.fault": 1, "robot.estop": 1, "conveyor.jam": 1, "agv.blocked": 2,
            "flow.starved": 2, "cfg.push": 4, "weld.quality_drift": 2,
            "paint.viscosity_high": 2, "svc.throughput_low": 2, "svc.line_stop": 1,
            "svc.rebalanced": 3}

LAYER_OF_TYPE = {"pneu.pressure_loss": "utilities", "press.tool_wear": "stamping",
                 "press.fault": "stamping", "plc.fault": "control",
                 "robot.estop": "welding", "conveyor.jam": "conveyance",
                 "agv.blocked": "logistics", "flow.starved": "conveyance",
                 "cfg.push": "control", "weld.quality_drift": "welding",
                 "paint.viscosity_high": "paint", "svc.throughput_low": "service",
                 "svc.line_stop": "service", "svc.rebalanced": "service"}


def raw_names(entity_id: str) -> list[str]:
    e = entity_id
    out = [e]
    low = e.lower()
    if e.startswith(("PRESS-", "WELD-", "ASSY-")):
        kind, h, n = e.split("-")
        out += [f"{kind.lower()}_{h.lower()}{n}", f"{kind.lower()}/{h}/{n.zfill(2)}"]
    elif e.startswith("PAINT-"):
        out += ["paintshop_c1", "durr/C/01"]
    elif e.startswith("CONV-"):
        _, a, b = e.split("-")
        out += [f"conv_{a.lower()}{b.lower()}", f"conveyor-{a.lower()}2{b.lower()}"]
    elif e.startswith("PLC-"):
        h = e.split("-")[1]
        out += [f"plc.hall-{h.lower()}", f"s7-1500/{h}"]
    elif e.startswith("PNEU-"):
        h = e.split("-")[1]
        out += [f"aircomp-{h.lower()}", f"pneumatic/{h}"]
    elif e.startswith("AGV-"):
        out += ["agv-fleet1", "mir/fleet/1"]
    elif e.startswith("SVC:"):
        out += [e.replace("SVC:", "line/"), e.replace("SVC:", "").upper()]
    return out


SCENARIOS = {
    "tool_wear": Scenario("tool_wear",
        lambda e: f"P2: Press tool wear beyond limit on {e}",
        "P2", "press.tool_wear", lambda rng: str(rng.choice(["PRESS-A-1", "PRESS-A-2"]))),
    "robot_estop": Scenario("robot_estop",
        lambda e: f"P1: Weld cell emergency stop {e}",
        "P1", "robot.estop", lambda rng: str(rng.choice(["WELD-B-1", "WELD-B-2", "WELD-B-3"]))),
    "conveyor_jam": Scenario("conveyor_jam",
        lambda e: f"P1: Conveyor jam on {e}",
        "P1", "conveyor.jam", lambda rng: str(rng.choice(list(CONVEYORS)))),
    "bad_recipe": Scenario("bad_recipe",
        lambda e: f"P2: Weld quality drift after recipe change on {e}",
        "P2", "cfg.push", lambda rng: str(rng.choice(["WELD-B-1", "WELD-B-2", "WELD-B-3"])),
        config_error=True),
    "plc_fault": Scenario("plc_fault",
        lambda e: f"P1: PLC fault in {e.split('-')[1]} hall",
        "P1", "plc.fault", lambda rng: f"PLC-{rng.choice(['A', 'B', 'C', 'D'])}"),
    "pneumatic_loss": Scenario("pneumatic_loss",
        lambda e: f"P1: Compressed-air pressure loss, hall {e.split('-')[1]}",
        "P1", "pneu.pressure_loss", lambda rng: f"PNEU-{rng.choice(['A', 'B', 'C', 'D'])}"),
    "silent_pneumatics": Scenario("silent_pneumatics",
        lambda e: f"P1: Multiple cells e-stopped in hall {e.split('-')[1]} (no utility alarm)",
        "P1", "pneu.pressure_loss", lambda rng: f"PNEU-{rng.choice(['A', 'B', 'D'])}",
        hidden_root=True),
    "agv_gridlock": Scenario("agv_gridlock",
        lambda e: f"P3: AGV routing gridlock, {e}",
        "P3", "agv.blocked", lambda rng: "AGV-FLEET-1"),
    "novel_storm": Scenario("novel_storm",
        lambda e: "P1: Uncharacterized multi-hall alarm storm",
        "P1", "flow.starved", lambda rng: str(rng.choice(list(CONVEYORS))), noise=(60, 90)),
}

register(DomainPack(
    key="manufacturing",
    name="Manufacturing · gigafactory",
    tenant="helios-gigafactory",
    world_title="Plant — Helios gigafactory, 4 halls",
    icon="⚙",
    build_world=build_world,
    true_rules=TRUE_RULES,
    severity=SEVERITY,
    layer_of_type=LAYER_OF_TYPE,
    noise_types=["sensor.drift", "hmi.session_timeout", "badge.reader_offline",
                 "lighting.ballast_warn", "scale.recal_due"],
    scenarios=SCENARIOS,
    scenario_weights={"tool_wear": 0.14, "robot_estop": 0.16, "conveyor_jam": 0.16,
                      "bad_recipe": 0.14, "plc_fault": 0.12, "pneumatic_loss": 0.11,
                      "silent_pneumatics": 0.05, "agv_gridlock": 0.12},
    canonical={"incident_id": "INC-2026-08-10-0509", "scenario": "conveyor_jam",
               "root_entity": "CONV-B-C", "seed": 71,
               "title": "P1: Model-X takt collapse — body-to-paint conveyor"},
    outage_types={"svc.line_stop"},
    degradation_types={"svc.throughput_low"},
    impact_protected_type="svc.throughput_low",
    impact_outage_type="svc.line_stop",
    runbooks={
        "press.tool_wear": ("swap_tooling", "Swap the worn die set; recalibrate the press"),
        "press.fault": ("swap_tooling", "Clear the press fault; swap tooling if worn"),
        "conveyor.jam": ("clear_restart", "Clear the jam, inspect the section, restart"),
        "robot.estop": ("reset_cell", "Reset the cell after safety check; resume program"),
        "weld.quality_drift": ("rollback_recipe", "Roll back the weld parameter recipe"),
        "cfg.push": ("rollback_recipe", "Roll back the recipe/parameter change"),
        "plc.fault": ("restart_plc", "Warm-restart the PLC from the last good image"),
        "pneu.pressure_loss": ("isolate_leak", "Isolate the leaking segment; bring backup compressor online"),
        "agv.blocked": ("reroute_agv", "Re-route the AGV fleet around the blockage"),
        "flow.starved": ("rebalance_line", "Rebalance takt across parallel stations"),
        "paint.viscosity_high": ("rollback_recipe", "Correct paint mix; purge the line"),
    },
    action_dynamics={
        "swap_tooling": (25.0, 8.0, 0, False), "clear_restart": (7.0, 2.5, 90, True),
        "reset_cell": (4.0, 1.5, 60, True), "rollback_recipe": (5.0, 2.0, 45, True),
        "restart_plc": (3.0, 1.0, 30, True), "isolate_leak": (20.0, 7.0, 0, False),
        "reroute_agv": (6.0, 2.0, 60, True), "rebalance_line": (8.0, 3.0, 120, True),
    },
    hard_down_types={"press.fault", "robot.estop", "conveyor.jam", "plc.fault"},
    confounder_types={"pneu.pressure_loss"},
    raw_namer=raw_names,
    resolver_hints={"press": ("PRESS-",), "stamping": ("PRESS-",), "weld": ("WELD-",),
                    "kuka": ("WELD-",), "paint": ("PAINT-",), "durr": ("PAINT-",),
                    "assy": ("ASSY-",), "conv": ("CONV-",), "plc": ("PLC-",),
                    "s7-": ("PLC-",), "pneu": ("PNEU-",), "aircomp": ("PNEU-",),
                    "agv": ("AGV-",), "mir": ("AGV-",), "line/": ("SVC:",)},
    site_token=lambda s: set(re.findall(
        r"(?:hall[-_/.]?|[-_/.])(a|b|c|d)(?=[-_/.\d]|$)", s.lower())),
    expert_pins=[
        {"src": "weld.quality_drift", "dst": "svc.line_stop", "action": "pin",
         "by": "quality-lead", "reason": "sustained weld drift stops the line at the "
         "quality gate; drift events are too rare in 90d for significance"},
        {"src": "svc.throughput_low", "dst": "svc.line_stop", "action": "pin",
         "by": "production-lead", "reason": "takt loss starves downstream buffers into "
         "a full stop within the shift"},
    ],
))
