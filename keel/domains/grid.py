"""Domain pack: power distribution grid (five substations, coastal region).

Feeders are the links, transformers the elements, SCADA RTUs the eyes. The
shared infrastructure is the RTU communications hub — when it fails silently,
protection relays misoperate 'for no reason': the latent-confounder class of
this industry. EU-AI-Act-style high-risk territory: exactly where "the agent
decided" is legally insufficient and a signed certificate is not optional.
"""
from __future__ import annotations

import re

import numpy as np

from ..models import Entity, TopoEdge
from . import DomainPack, Scenario, register

EPOCH = 1_754_000_000.0
SUBS = {"SUB-N": [0.48, 0.12], "SUB-E": [0.85, 0.42], "SUB-C": [0.48, 0.50],
        "SUB-W": [0.10, 0.46], "SUB-S": [0.44, 0.88]}
TX = {"TX-N-1": "SUB-N", "TX-E-1": "SUB-E", "TX-W-1": "SUB-W", "TX-S-1": "SUB-S",
      "TX-C-1": "SUB-C", "TX-C-2": "SUB-C"}
FEEDERS = {"FDR-N-C": ("SUB-N", "SUB-C"), "FDR-E-C": ("SUB-E", "SUB-C"),
           "FDR-W-C": ("SUB-W", "SUB-C"), "FDR-S-C": ("SUB-S", "SUB-C"),
           "FDR-N-E": ("SUB-N", "SUB-E"), "FDR-S-W": ("SUB-S", "SUB-W")}

SERVICES = {
    "SVC:hospital-district": (["FDR-N-C", "TX-N-1"], ["FDR-N-E", "TX-E-1"], 12_400, "platinum"),
    "SVC:industrial-park": (["FDR-E-C", "TX-E-1"], ["FDR-N-E", "TX-N-1"], 310, "gold"),
    "SVC:residential-west": (["FDR-W-C", "TX-W-1"], ["FDR-S-W", "TX-S-1"], 41_000, "silver"),
    "SVC:residential-south": (["FDR-S-C", "TX-S-1"], ["FDR-S-W", "TX-W-1"], 38_500, "silver"),
    "SVC:downtown-core": (["TX-C-1"], ["TX-C-2"], 64_000, "gold"),
}


def build_world(store) -> None:
    for sub, pos in SUBS.items():
        store.put_entity(Entity(entity_id=sub, kind="site", layer="service",
                                site=sub, attrs={"pos": pos}))
        com = f"COM-{sub.split('-')[1]}"
        store.put_entity(Entity(entity_id=com, kind="power", layer="comms",
                                vendor="GE", model="MDS-Orbit", site=sub))
        rtu = f"RTU-{sub.split('-')[1]}"
        store.put_entity(Entity(entity_id=rtu, kind="ne", layer="scada",
                                vendor="SEL", model="RTAC-3555", site=sub))
        store.put_topo_edge(TopoEdge(src=com, dst=rtu, relation="feeds", valid_from=EPOCH))
    for tx, sub in TX.items():
        store.put_entity(Entity(entity_id=tx, kind="ne", layer="primary",
                                vendor="ABB", model="TrafoStar-63MVA", site=sub))
        rtu = f"RTU-{sub.split('-')[1]}"
        store.put_topo_edge(TopoEdge(src=rtu, dst=tx, relation="carries", valid_from=EPOCH))
        store.put_topo_edge(TopoEdge(src=f"COM-{sub.split('-')[1]}", dst=tx,
                                     relation="feeds", valid_from=EPOCH))
    for fdr, (a, b) in FEEDERS.items():
        store.put_entity(Entity(entity_id=fdr, kind="link", layer="feeder",
                                vendor="", model="11kV", site=a,
                                attrs={"between": [a, b]}))
        for sub in (a, b):
            for tx, s2 in TX.items():
                if s2 == sub:
                    store.put_topo_edge(TopoEdge(src=fdr, dst=tx, relation="carries",
                                                 valid_from=EPOCH))
    store.put_entity(Entity(entity_id="DER-W-SOLAR", kind="ne", layer="der",
                            vendor="SMA", model="SunnyCentral", site="SUB-W"))
    store.put_topo_edge(TopoEdge(src="DER-W-SOLAR", dst="TX-W-1", relation="carries",
                                 valid_from=EPOCH))
    for svc, (primary, backup, customers, sla) in SERVICES.items():
        store.put_entity(Entity(entity_id=svc, kind="service", layer="service",
                                attrs={"paths": [primary, backup],
                                       "customers": customers, "sla_class": sla}))
        for el in set(primary) | set(backup):
            store.put_topo_edge(TopoEdge(src=el, dst=svc, relation="serves",
                                         valid_from=EPOCH))


TRUE_RULES = [
    ("com.hub_fail",       "scada.comm_loss",    "fed",      0.95,   2,  10),
    ("scada.comm_loss",    "relay.misoperation", "carried",  0.35,  30, 180),
    ("relay.misoperation", "breaker.open",       "same",     0.80,   1,   5),
    ("feeder.fault",       "breaker.open",       "same",     0.95,   1,   3),
    ("breaker.open",       "load.imbalance",     "carried",  0.60,  20,  90),
    ("load.imbalance",     "tx.overtemp",        "same",     0.55, 120, 400),
    ("tx.overtemp",        "voltage.sag",        "same",     0.60,  60, 240),
    ("tx.overtemp",        "tx.trip",            "same",     0.70,  60, 300),
    ("der.overvoltage",    "relay.misoperation", "carried",  0.40,  10,  60),
    ("cfg.push",           "relay.misoperation", "same",     0.05,  20,  90),
    ("tx.trip",            "svc.impact",         "services", 0.90,   5,  20),
    ("breaker.open",       "svc.impact",         "services", 0.85,   3,  15),
    ("voltage.sag",        "svc.brownout",       "services", 0.80,  30, 120),
    ("svc.brownout",       "svc.supply_lost",    "same",     0.35, 120, 600),
]

SEVERITY = {"com.hub_fail": 1, "scada.comm_loss": 2, "relay.misoperation": 1,
            "feeder.fault": 1, "breaker.open": 1, "load.imbalance": 3,
            "tx.overtemp": 2, "voltage.sag": 2, "tx.trip": 1, "der.overvoltage": 2,
            "cfg.push": 4, "svc.brownout": 2, "svc.supply_lost": 1, "svc.rerouted": 3}

LAYER_OF_TYPE = {"com.hub_fail": "comms", "scada.comm_loss": "scada",
                 "relay.misoperation": "scada", "feeder.fault": "feeder",
                 "breaker.open": "feeder", "load.imbalance": "primary",
                 "tx.overtemp": "primary", "voltage.sag": "primary",
                 "tx.trip": "primary", "der.overvoltage": "der", "cfg.push": "scada",
                 "svc.brownout": "service", "svc.supply_lost": "service",
                 "svc.rerouted": "service"}

_DIR = {"n": "north", "s": "south", "e": "east", "w": "west", "c": "central"}


def raw_names(entity_id: str) -> list[str]:
    e = entity_id
    out = [e]
    if e.startswith("TX-"):
        _, d, n = e.split("-")
        out += [f"xfmr-{_DIR[d.lower()]}-{n}", f"TX{d}{n.zfill(2)}",
                f"transformer.{d.lower()}.{n}"]
    elif e.startswith("FDR-"):
        _, a, b = e.split("-")
        out += [f"feeder-{_DIR[a.lower()]}-{_DIR[b.lower()]}", f"11kv/{a.lower()}-{b.lower()}"]
    elif e.startswith("RTU-"):
        d = e.split("-")[1]
        out += [f"rtu_{_DIR[d.lower()]}", f"scada/{d}"]
    elif e.startswith("COM-"):
        d = e.split("-")[1]
        out += [f"comhub-{d.lower()}", f"radio.{_DIR[d.lower()]}"]
    elif e.startswith("DER-"):
        out += ["solar-farm-w", "der/west/solar"]
    elif e.startswith("SVC:"):
        out += [e.replace("SVC:", "load/"), e.replace("SVC:", "").upper()]
    return out


def _tx(rng: np.random.Generator) -> str:
    return str(rng.choice(list(TX)))


SCENARIOS = {
    "transformer_overheat": Scenario("transformer_overheat",
        lambda e: f"P1: Transformer overtemperature {e}",
        "P1", "tx.overtemp", _tx),
    "feeder_fault": Scenario("feeder_fault",
        lambda e: f"P1: Feeder fault on {e} — protection operated",
        "P1", "feeder.fault", lambda rng: str(rng.choice(list(FEEDERS)))),
    "protection_misconfig": Scenario("protection_misconfig",
        lambda e: f"P2: Relay misoperation after setting change at {e}",
        "P2", "cfg.push", lambda rng: f"RTU-{rng.choice(['N', 'S', 'E', 'W'])}",
        config_error=True),
    "der_surge": Scenario("der_surge",
        lambda e: f"P2: DER overvoltage excursion at {e}",
        "P2", "der.overvoltage", lambda rng: "DER-W-SOLAR"),
    "scada_blackout": Scenario("scada_blackout",
        lambda e: f"P2: SCADA visibility lost at {e}",
        "P2", "scada.comm_loss", lambda rng: f"RTU-{rng.choice(['N', 'S', 'E', 'W', 'C'])}"),
    "silent_comms": Scenario("silent_comms",
        lambda e: f"P1: Unexplained relay operations near {e.split('-')[1]} (comms hub silent)",
        "P1", "com.hub_fail", lambda rng: f"COM-{rng.choice(['N', 'S', 'E', 'W'])}",
        hidden_root=True),
    "brownout_creep": Scenario("brownout_creep",
        lambda e: f"P3: Voltage quality degradation at {e}",
        "P3", "voltage.sag", _tx),
    "novel_storm": Scenario("novel_storm",
        lambda e: "P1: Uncharacterized multi-station alarm storm",
        "P1", "voltage.sag", _tx, noise=(60, 90)),
}

register(DomainPack(
    key="grid",
    name="Energy · distribution grid",
    tenant="coastal-grid-south",
    world_title="Grid — coastal distribution, 5 substations",
    icon="⚡",
    build_world=build_world,
    true_rules=TRUE_RULES,
    severity=SEVERITY,
    layer_of_type=LAYER_OF_TYPE,
    noise_types=["env.humidity_high", "battery.float_warn", "door.open",
                 "gps.clock_drift", "meter.readout_gap"],
    scenarios=SCENARIOS,
    scenario_weights={"transformer_overheat": 0.16, "feeder_fault": 0.18,
                      "protection_misconfig": 0.14, "der_surge": 0.10,
                      "scada_blackout": 0.14, "silent_comms": 0.06,
                      "brownout_creep": 0.22},
    canonical={"incident_id": "INC-2026-08-10-0641", "scenario": "feeder_fault",
               "root_entity": "FDR-W-C", "seed": 8,
               "title": "P1: West feeder fault — 41k customers on backup supply"},
    outage_types={"svc.supply_lost"},
    degradation_types={"svc.brownout"},
    impact_protected_type="svc.rerouted",
    impact_outage_type="svc.supply_lost",
    runbooks={
        "tx.overtemp": ("shed_load", "Shed non-critical load off the overheating transformer"),
        "tx.trip": ("switch_transfer", "Transfer load to the alternate transformer"),
        "feeder.fault": ("sectionalize", "Sectionalize the fault; back-feed healthy sections"),
        "breaker.open": ("sectionalize", "Isolate the faulted section and restore around it"),
        "relay.misoperation": ("rollback_protection", "Roll back the protection settings group"),
        "cfg.push": ("rollback_protection", "Roll back the protection setting change"),
        "scada.comm_loss": ("dispatch_comms", "Dispatch comms crew; switch to backup channel"),
        "com.hub_fail": ("dispatch_comms", "Dispatch crew to the comms hub"),
        "der.overvoltage": ("curtail_der", "Curtail DER output; enforce volt-var curve"),
        "voltage.sag": ("tap_adjust", "Adjust the on-load tap changer"),
        "load.imbalance": ("switch_transfer", "Rebalance load across transformers"),
    },
    action_dynamics={
        "shed_load": (6.0, 2.0, 60, True), "switch_transfer": (12.0, 4.0, 120, True),
        "sectionalize": (18.0, 6.0, 300, True), "rollback_protection": (4.0, 1.5, 45, True),
        "dispatch_comms": (45.0, 15.0, 0, False), "curtail_der": (3.0, 1.0, 30, True),
        "tap_adjust": (5.0, 2.0, 60, True),
    },
    hard_down_types={"tx.trip", "breaker.open", "com.hub_fail"},
    confounder_types={"com.hub_fail", "scada.comm_loss"},
    raw_namer=raw_names,
    resolver_hints={"tx": ("TX-",), "xfmr": ("TX-",), "transformer": ("TX-",),
                    "fdr": ("FDR-",), "feeder": ("FDR-",), "11kv": ("FDR-",),
                    "rtu": ("RTU-",), "scada/": ("RTU-",), "comhub": ("COM-",),
                    "radio": ("COM-",), "solar": ("DER-",), "der": ("DER-",),
                    "load/": ("SVC:",)},
    site_token=lambda s: set(re.findall(
        r"[-_/.](n|s|e|w|c)(?:orth|outh|ast|est|entral|tr)?(?=[-_/.\d]|$)", s.lower())),
    expert_pins=[
        {"src": "breaker.open", "dst": "svc.supply_lost", "action": "pin",
         "by": "protection-engineer", "reason": "a breaker lockout on the last "
         "healthy path de-energizes the load directly; masked in the corpus by "
         "the high base rate of rerouted faults"},
        {"src": "der.overvoltage", "dst": "relay.misoperation", "action": "pin",
         "by": "protection-engineer", "reason": "inverter overvoltage excursions trip "
         "legacy relays; DER penetration is too recent for 90d significance"},
        {"src": "svc.brownout", "dst": "svc.supply_lost", "action": "pin",
         "by": "protection-engineer", "reason": "sustained undervoltage collapses into "
         "load loss under thermal protection"},
    ],
))
