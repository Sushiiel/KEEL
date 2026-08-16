"""Domain pack: metro telecom transport (the original wedge vertical).

Six sites, optical spans, IP/MPLS routers, RAN clusters, per-site power feeds,
and five SLA-bearing services over primary/backup paths.
"""
from __future__ import annotations

import re

import numpy as np

from ..models import Entity, TopoEdge
from . import DomainPack, Scenario, register

NETWORK_EPOCH = 1_754_000_000.0
DAY = 86_400.0

SITES = ["CHN-01", "CHN-02", "CHN-03", "CHN-04", "CHN-05", "CHN-06"]
CORE_SITES = {"CHN-01", "CHN-02"}
SITE_POS = {"CHN-01": [0.24, 0.20], "CHN-02": [0.76, 0.16], "CHN-03": [0.14, 0.58],
            "CHN-04": [0.60, 0.50], "CHN-05": [0.36, 0.86], "CHN-06": [0.82, 0.80]}

SPANS: dict[str, tuple[str, str, float]] = {
    "OTS-CHN-1": ("CHN-01", "CHN-02", 0), "OTS-CHN-2": ("CHN-01", "CHN-03", 0),
    "OTS-CHN-3": ("CHN-02", "CHN-04", 0), "OTS-CHN-4": ("CHN-03", "CHN-05", 0),
    "OTS-CHN-5": ("CHN-04", "CHN-06", 0), "OTS-CHN-6": ("CHN-05", "CHN-06", 0),
    "OTS-CHN-7": ("CHN-03", "CHN-04", 0),
    "OTS-CHN-8": ("CHN-01", "CHN-04", 35),   # added mid-history (bi-temporal demo)
    "OTS-CHN-9": ("CHN-02", "CHN-03", 0),
}

RAN_CLUSTERS = {"GNB-CHN-03-A": "CHN-03", "GNB-CHN-04-A": "CHN-04",
                "GNB-CHN-05-A": "CHN-05", "GNB-CHN-06-A": "CHN-06"}

SERVICES: dict[str, tuple[list[str], list[str], int, str]] = {
    "SVC:mobile-backhaul-7": (
        ["GNB-CHN-03-A", "PE-CHN-03", "OTS-CHN-7", "PE-CHN-04", "P-CHN-02"],
        ["GNB-CHN-03-A", "PE-CHN-03", "OTS-CHN-9", "P-CHN-02"], 184_000, "gold"),
    "SVC:mobile-backhaul-5": (
        ["GNB-CHN-05-A", "PE-CHN-05", "OTS-CHN-4", "PE-CHN-03", "OTS-CHN-2", "P-CHN-01"],
        ["GNB-CHN-05-A", "PE-CHN-05", "OTS-CHN-6", "PE-CHN-06", "OTS-CHN-5", "PE-CHN-04"],
        121_000, "gold"),
    "SVC:mobile-backhaul-4": (
        ["GNB-CHN-04-A", "PE-CHN-04", "OTS-CHN-3", "P-CHN-02"],
        ["GNB-CHN-04-A", "PE-CHN-04", "OTS-CHN-8", "P-CHN-01"], 98_000, "gold"),
    "SVC:ent-vpn-12": (
        ["PE-CHN-04", "OTS-CHN-5", "PE-CHN-06"],
        ["PE-CHN-04", "OTS-CHN-3", "P-CHN-02", "OTS-CHN-1", "P-CHN-01", "OTS-CHN-2",
         "PE-CHN-03", "OTS-CHN-4", "PE-CHN-05", "OTS-CHN-6", "PE-CHN-06"], 340, "platinum"),
    "SVC:broadband-6": (
        ["PE-CHN-06", "OTS-CHN-5", "PE-CHN-04", "OTS-CHN-3", "P-CHN-02"],
        ["PE-CHN-06", "OTS-CHN-6", "PE-CHN-05", "OTS-CHN-4", "PE-CHN-03", "OTS-CHN-2",
         "P-CHN-01"], 56_000, "silver"),
}

VENDORS = {"optical": ("Ciena", "6500"), "ip": ("Cisco", "ASR-9906"),
           "ran": ("Ericsson", "AIR-6449"), "power": ("Vertiv", "NetSure")}


def router_id(site: str) -> str:
    return ("P-" if site in CORE_SITES else "PE-") + site


def build_world(store) -> None:
    ents: list[Entity] = []
    edges: list[TopoEdge] = []
    for site in SITES:
        ents.append(Entity(entity_id=site, kind="site", layer="service", site=site,
                           attrs={"pos": SITE_POS[site]}))
        pwr = f"PWR-{site}"
        v, m = VENDORS["power"]
        ents.append(Entity(entity_id=pwr, kind="power", layer="power", vendor=v,
                           model=m, site=site))
        r = router_id(site)
        v, m = VENDORS["ip"]
        ents.append(Entity(entity_id=r, kind="ne", layer="ip", vendor=v, model=m,
                           site=site, attrs={"role": "core" if site in CORE_SITES else "edge"}))
        edges.append(TopoEdge(src=pwr, dst=r, relation="feeds", valid_from=NETWORK_EPOCH))

    v, m = VENDORS["optical"]
    for span, (a, b, added_days) in SPANS.items():
        t = NETWORK_EPOCH + added_days * DAY
        ents.append(Entity(entity_id=span, kind="link", layer="optical", vendor=v,
                           model=m, site=a, attrs={"between": [a, b],
                                                   "km": 18 + 7 * (hash(span) % 5)}))
        for site in (a, b):
            edges.append(TopoEdge(src=span, dst=router_id(site), relation="carries", valid_from=t))
            edges.append(TopoEdge(src=f"PWR-{site}", dst=span, relation="feeds", valid_from=t))
        edges.append(TopoEdge(src=router_id(a), dst=router_id(b), relation="peers", valid_from=t))
        edges.append(TopoEdge(src=router_id(b), dst=router_id(a), relation="peers", valid_from=t))

    v, m = VENDORS["ran"]
    for gnb, site in RAN_CLUSTERS.items():
        ents.append(Entity(entity_id=gnb, kind="ne", layer="ran", vendor=v, model=m, site=site))
        edges.append(TopoEdge(src=router_id(site), dst=gnb, relation="carries",
                              valid_from=NETWORK_EPOCH))
        edges.append(TopoEdge(src=f"PWR-{site}", dst=gnb, relation="feeds",
                              valid_from=NETWORK_EPOCH))

    for svc, (primary, backup, customers, sla) in SERVICES.items():
        ents.append(Entity(entity_id=svc, kind="service", layer="service",
                           attrs={"paths": [primary, backup], "customers": customers,
                                  "sla_class": sla}))
        for element in set(primary) | set(backup):
            edges.append(TopoEdge(src=element, dst=svc, relation="serves",
                                  valid_from=NETWORK_EPOCH))
    for e in ents:
        store.put_entity(e)
    for e in edges:
        store.put_topo_edge(e)


TRUE_RULES = [
    ("power.feed_fail",     "hw.power_loss",       "fed",        0.95,   1,   6),
    ("hw.power_loss",       "port.link_down",      "same",       0.90,   2,   8),
    ("hw.power_loss",       "ran.cell_outage",     "same_kind:GNB-", 0.90, 2,  8),
    ("optical.amp_degrade", "optical.ber_high",    "same",       0.95,   5,  30),
    ("optical.amp_degrade", "optical.los",         "same",       0.75,  60, 240),
    ("optical.los",         "port.link_down",      "carried",    0.95,   1,   4),
    ("port.link_down",      "ldp.session_down",    "same",       0.85,   2,  10),
    ("port.link_down",      "isis.adjacency_down", "same",       0.85,   2,  10),
    ("ldp.session_down",    "mpls.lsp_down",       "same",       0.80,   3,  12),
    ("isis.adjacency_down", "isis.spf_churn",      "peers",      0.75,   5,  20),
    ("isis.spf_churn",      "bgp.flap",            "same",       0.35,  10,  40),
    ("bgp.flap",            "route.blackhole",     "same",       0.25,   5,  30),
    ("cfg.push",            "bgp.flap",            "same",       0.05,  20,  90),
    ("congestion.high_util","svc.latency_high",    "services",   0.80,  30, 120),
    ("svc.latency_high",    "svc.sla_breach",      "same",       0.60,  60, 300),
    ("route.blackhole",     "svc.sla_breach",      "services",   0.85,  10,  60),
    ("mpls.lsp_down",       "svc.impact",          "services",   0.90,   5,  25),
    ("ran.cell_outage",     "svc.sla_breach",      "services",   0.70,  10,  60),
]

SEVERITY = {
    "power.feed_fail": 1, "hw.power_loss": 1, "optical.los": 1, "optical.amp_degrade": 2,
    "optical.ber_high": 2, "port.link_down": 1, "ldp.session_down": 2,
    "isis.adjacency_down": 2, "isis.spf_churn": 3, "mpls.lsp_down": 2, "bgp.flap": 2,
    "route.blackhole": 1, "cfg.push": 4, "congestion.high_util": 3,
    "svc.latency_high": 2, "svc.sla_breach": 1, "svc.path_switch": 3, "ran.cell_outage": 1,
}

LAYER_OF_TYPE = {
    "power.feed_fail": "power", "hw.power_loss": "power",
    "optical.amp_degrade": "optical", "optical.ber_high": "optical", "optical.los": "optical",
    "port.link_down": "ip", "ldp.session_down": "mpls", "isis.adjacency_down": "ip",
    "isis.spf_churn": "ip", "mpls.lsp_down": "mpls", "bgp.flap": "ip",
    "route.blackhole": "ip", "cfg.push": "ip", "congestion.high_util": "ip",
    "svc.latency_high": "service", "svc.sla_breach": "service",
    "svc.path_switch": "service", "ran.cell_outage": "ran",
}


def raw_names(entity_id: str) -> list[str]:
    e = entity_id
    out = [e]
    if e.startswith(("PE-", "P-")):
        pre, site = e.split("-", 1)
        num = site.split("-")[1]
        out += [f"chennai{num}-{pre.lower()}01", f"{site.replace('-', '')}/{pre}-1",
                f"{pre.lower()}1.chn{int(num)}.net.example"]
    elif e.startswith("OTS-"):
        n = e.rsplit("-", 1)[1]
        a, b, _ = SPANS[e]
        out += [f"OTS/{a[-2:]}-{b[-2:]}/{n}", f"span-chn-{n}", f"ots_chn_{n}"]
    elif e.startswith("PWR-"):
        site = e.split("-", 1)[1]
        out += [f"dcpower.{site.lower()}", f"{site}/RECT-A"]
    elif e.startswith("GNB-"):
        parts = e.split("-")
        out += [f"gnodeb-{parts[1].lower()}{parts[2]}-{parts[3].lower()}",
                f"{parts[1]}-{parts[2]}/GNB/{parts[3]}"]
    elif e.startswith("SVC:"):
        out += [e.replace("SVC:", "service/"), e.replace("SVC:", "").upper()]
    return out


def _span(rng: np.random.Generator) -> str:
    return str(rng.choice(list(SPANS)))


def _edge_router(rng: np.random.Generator) -> str:
    return router_id(str(rng.choice(["CHN-03", "CHN-04", "CHN-05", "CHN-06"])))


SCENARIOS = {
    "amplifier_degradation": Scenario(
        "amplifier_degradation",
        lambda e: f"P1: 4G data degradation via optical amp degradation on {e}",
        "P1", "optical.amp_degrade", _span),
    "fiber_cut": Scenario(
        "fiber_cut", lambda e: f"P1: Loss of signal — suspected fiber cut on {e}",
        "P1", "optical.los", _span),
    "power_feed_failure": Scenario(
        "power_feed_failure", lambda e: f"P1: Site power feed failure at {e.split('-', 1)[1]}",
        "P1", "power.feed_fail",
        lambda rng: f"PWR-{rng.choice(['CHN-03', 'CHN-04', 'CHN-05', 'CHN-06'])}"),
    "config_error": Scenario(
        "config_error", lambda e: f"P2: Post-change routing instability on {e}",
        "P2", "cfg.push", _edge_router, config_error=True),
    "isis_churn": Scenario(
        "isis_churn", lambda e: f"P2: IS-IS SPF churn originating at {e}",
        "P2", "isis.spf_churn", _edge_router),
    "congestion": Scenario(
        "congestion", lambda e: f"P3: Sustained congestion on {e}",
        "P3", "congestion.high_util", _span),
    "ran_outage": Scenario(
        "ran_outage", lambda e: f"P1: RAN cluster outage {e}",
        "P1", "ran.cell_outage", lambda rng: str(rng.choice(list(RAN_CLUSTERS)))),
    "flapping_link": Scenario(
        "flapping_link", lambda e: f"P3: Intermittent BER bursts on {e} (unclear origin)",
        "P3", "optical.ber_high", _span, noise=(10, 16)),
    "silent_power": Scenario(
        "silent_power",
        lambda e: f"P1: Correlated element failures at {e.split('-', 1)[1]} (no power alarm)",
        "P1", "power.feed_fail",
        lambda rng: f"PWR-{rng.choice(['CHN-03', 'CHN-04', 'CHN-05', 'CHN-06'])}",
        hidden_root=True),
    "novel_storm": Scenario(
        "novel_storm", lambda e: "P1: Uncharacterized multi-layer alarm storm",
        "P1", "optical.ber_high", _span, noise=(60, 90)),
}

register(DomainPack(
    key="telecom",
    name="Telecom · metro transport",
    tenant="chennai-south-metro",
    world_title="Network — Chennai-South metro",
    icon="⌁",
    build_world=build_world,
    true_rules=TRUE_RULES,
    severity=SEVERITY,
    layer_of_type=LAYER_OF_TYPE,
    noise_types=["env.temp_high", "fan.warn", "snmp.timeout", "ntp.drift", "disk.usage_high"],
    scenarios=SCENARIOS,
    scenario_weights={"amplifier_degradation": 0.18, "fiber_cut": 0.13,
                      "power_feed_failure": 0.11, "config_error": 0.14,
                      "isis_churn": 0.12, "congestion": 0.12, "ran_outage": 0.10,
                      "flapping_link": 0.05, "silent_power": 0.05},
    canonical={"incident_id": "INC-2026-08-09-0417", "scenario": "amplifier_degradation",
               "root_entity": "OTS-CHN-7", "seed": 287,
               "title": "P1: 4G data degradation, Chennai-South cluster"},
    outage_types={"svc.sla_breach"},
    degradation_types={"svc.latency_high"},
    impact_protected_type="svc.path_switch",
    impact_outage_type="svc.sla_breach",
    runbooks={
        "optical.amp_degrade": ("reroute_drain", "Reroute λ-path away from degraded span, then drain for repair"),
        "optical.los": ("reroute_drain", "Reroute λ-path away from cut span, drain for splicing"),
        "optical.ber_high": ("reroute_drain", "Precautionary reroute off the degrading span"),
        "cfg.push": ("rollback_config", "Roll back the offending configuration push"),
        "isis.spf_churn": ("restart_protocol", "Graceful IS-IS process restart with LSP flush"),
        "bgp.flap": ("restart_protocol", "Reset BGP session with route dampening"),
        "route.blackhole": ("rollback_config", "Withdraw the blackholed prefix and roll back"),
        "congestion.high_util": ("traffic_engineer", "Shift traffic to alternate LSPs (TE)"),
        "power.feed_fail": ("dispatch_power", "Dispatch field team; transfer to redundant feed"),
        "hw.power_loss": ("dispatch_power", "Dispatch field team; verify rectifier bank"),
        "ran.cell_outage": ("ran_recover", "Remote gNB soft-restart; escalate to dispatch"),
    },
    action_dynamics={
        "reroute_drain": (4.2, 1.6, 90, True),
        "rollback_config": (3.0, 1.2, 45, True),
        "restart_protocol": (2.2, 1.0, 60, True),
        "traffic_engineer": (6.0, 2.5, 120, True),
        "dispatch_power": (38.0, 15.0, 0, False),
        "ran_recover": (9.0, 4.0, 180, True),
    },
    hard_down_types={"optical.los", "hw.power_loss", "route.blackhole", "ran.cell_outage"},
    confounder_types={"power.feed_fail", "hw.power_loss"},
    raw_namer=raw_names,
    resolver_hints={"pe": ("PE-",), "ots": ("OTS-",), "span": ("OTS-",),
                    "re:(?:^|[^a-z])p0?1|/p-1": ("P-",),
                    "gnb": ("GNB-",), "gnodeb": ("GNB-",), "pwr": ("PWR-",),
                    "dcpower": ("PWR-",), "rect": ("PWR-",), "svc": ("SVC:",),
                    "service": ("SVC:",)},
    site_token=lambda s: set(re.findall(r"(?:chn|chennai)[-/]?0?(\d)", s.lower())),
    expert_pins=[
        {"src": "route.blackhole", "dst": "svc.sla_breach", "action": "pin",
         "by": "noc-lead", "reason": "a blackholed PE always breaches the SLAs it "
         "serves; too rare in 90d to reach significance"},
        {"src": "ran.cell_outage", "dst": "svc.sla_breach", "action": "pin",
         "by": "noc-lead", "reason": "cell cluster outage is customer-visible by "
         "definition on mobile-backhaul services"},
        {"src": "svc.latency_high", "dst": "svc.sla_breach", "action": "pin",
         "by": "noc-lead", "reason": "sustained latency matures into an SLA breach "
         "under the gold-tier contract"},
    ],
))
