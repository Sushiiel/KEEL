"""Domain pack: cloud-native SaaS (e-commerce platform on Kubernetes, 3 AZs).

Kubernetes nodes are the shared infrastructure (the latent-confounder class:
a silently degrading node takes down 'unrelated' pods). Dependencies follow
provider→consumer: databases and caches carry the pods that use them, pods
carry the gateways, everything maps onto customer journeys with SLOs.
"""
from __future__ import annotations

import re

import numpy as np

from ..models import Entity, TopoEdge
from . import DomainPack, Scenario, register

EPOCH = 1_754_000_000.0
ZONES = {"AZ-1A": [0.18, 0.30], "AZ-1B": [0.62, 0.18], "AZ-1C": [0.55, 0.78]}

NODES = {"NODE-1A-1": "AZ-1A", "NODE-1A-2": "AZ-1A", "NODE-1B-1": "AZ-1B",
         "NODE-1B-2": "AZ-1B", "NODE-1C-1": "AZ-1C"}
PODS = {"POD-CHECKOUT-1A": ("AZ-1A", "NODE-1A-1"), "POD-CHECKOUT-1B": ("AZ-1B", "NODE-1B-1"),
        "POD-CART-1A": ("AZ-1A", "NODE-1A-2"), "POD-CART-1B": ("AZ-1B", "NODE-1B-2"),
        "POD-SEARCH-1B": ("AZ-1B", "NODE-1B-1"), "POD-SEARCH-1C": ("AZ-1C", "NODE-1C-1"),
        "POD-AUTH-1A": ("AZ-1A", "NODE-1A-1"), "POD-PAY-1B": ("AZ-1B", "NODE-1B-2")}
DATA = {"PG-ORDERS-1A": ("AZ-1A", "NODE-1A-2"), "PG-ORDERS-1B": ("AZ-1B", "NODE-1B-2"),
        "REDIS-CACHE-1B": ("AZ-1B", "NODE-1B-1"), "KAFKA-EVENTS-1C": ("AZ-1C", "NODE-1C-1")}
GATEWAYS = {"GW-EDGE-1A": "AZ-1A", "GW-EDGE-1B": "AZ-1B"}
LINKS = {"LNK-1A-1B": ("AZ-1A", "AZ-1B"), "LNK-1B-1C": ("AZ-1B", "AZ-1C"),
         "LNK-1A-1C": ("AZ-1A", "AZ-1C")}

# provider -> consumers
DEPENDS = {
    "PG-ORDERS-1A": ["POD-CHECKOUT-1A", "POD-CHECKOUT-1B", "POD-CART-1A", "POD-CART-1B"],
    "PG-ORDERS-1B": ["POD-SEARCH-1B", "POD-SEARCH-1C"],
    "REDIS-CACHE-1B": ["POD-CART-1A", "POD-CART-1B", "POD-SEARCH-1B"],
    "KAFKA-EVENTS-1C": ["POD-PAY-1B", "POD-CHECKOUT-1A", "POD-CHECKOUT-1B"],
    "POD-AUTH-1A": ["POD-CHECKOUT-1A", "POD-CHECKOUT-1B", "POD-PAY-1B"],
    "POD-CHECKOUT-1A": ["GW-EDGE-1A"], "POD-CHECKOUT-1B": ["GW-EDGE-1B"],
    "POD-CART-1A": ["GW-EDGE-1A"], "POD-CART-1B": ["GW-EDGE-1B"],
    "POD-SEARCH-1B": ["GW-EDGE-1B"], "POD-SEARCH-1C": ["GW-EDGE-1A"],
    "POD-PAY-1B": ["GW-EDGE-1A", "GW-EDGE-1B"],
    "LNK-1A-1B": ["PG-ORDERS-1B", "GW-EDGE-1A"],
    "LNK-1B-1C": ["KAFKA-EVENTS-1C"],
    "LNK-1A-1C": ["POD-SEARCH-1C"],
}

SERVICES = {
    "SVC:checkout-flow": (
        ["GW-EDGE-1A", "POD-AUTH-1A", "POD-CHECKOUT-1A", "PG-ORDERS-1A"],
        ["GW-EDGE-1B", "POD-AUTH-1A", "POD-CHECKOUT-1B", "PG-ORDERS-1A"], 240_000, "gold"),
    "SVC:search-flow": (
        ["GW-EDGE-1B", "POD-SEARCH-1B", "REDIS-CACHE-1B", "PG-ORDERS-1B"],
        ["GW-EDGE-1A", "POD-SEARCH-1C", "PG-ORDERS-1B"], 310_000, "silver"),
    "SVC:payments-api": (
        ["GW-EDGE-1A", "POD-AUTH-1A", "POD-PAY-1B", "KAFKA-EVENTS-1C"],
        ["GW-EDGE-1B", "POD-AUTH-1A", "POD-PAY-1B", "PG-ORDERS-1A"], 88_000, "platinum"),
    "SVC:storefront": (
        ["GW-EDGE-1A", "POD-CART-1A", "REDIS-CACHE-1B"],
        ["GW-EDGE-1B", "POD-CART-1B", "PG-ORDERS-1A"], 420_000, "gold"),
}

_LAYER = {"NODE": "infra", "POD": "app", "PG": "data", "REDIS": "data",
          "KAFKA": "data", "GW": "edge", "LNK": "net"}


def build_world(store) -> None:
    for zone, pos in ZONES.items():
        store.put_entity(Entity(entity_id=zone, kind="site", layer="service",
                                site=zone, attrs={"pos": pos}))
    for node, zone in NODES.items():
        store.put_entity(Entity(entity_id=node, kind="power", layer="infra",
                                vendor="AWS", model="m6i.4xlarge", site=zone))
    for pod, (zone, node) in {**PODS, **DATA}.items():
        layer = _LAYER[pod.split("-")[0]]
        store.put_entity(Entity(entity_id=pod, kind="ne", layer=layer,
                                vendor="k8s", model="deployment", site=zone))
        store.put_topo_edge(TopoEdge(src=node, dst=pod, relation="feeds", valid_from=EPOCH))
    for gw, zone in GATEWAYS.items():
        store.put_entity(Entity(entity_id=gw, kind="ne", layer="edge",
                                vendor="Envoy", model="gateway", site=zone))
    for lnk, (a, b) in LINKS.items():
        store.put_entity(Entity(entity_id=lnk, kind="link", layer="net", site=a,
                                attrs={"between": [a, b]}))
    for provider, consumers in DEPENDS.items():
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
    ("node.failure",        "pod.crashloop",       "fed",      0.90,   2,  10),
    ("pod.oom_kill",        "pod.crashloop",       "same",     0.80,   5,  30),
    ("pod.crashloop",       "http.error_rate_high","carried",  0.85,   5,  20),
    ("db.conn_exhausted",   "db.latency_high",     "same",     0.90,   5,  15),
    ("db.latency_high",     "http.latency_high",   "carried",  0.85,  10,  40),
    ("cache.evictions_high","http.latency_high",   "carried",  0.75,  20,  60),
    ("queue.backlog",       "http.latency_high",   "carried",  0.70,  30, 120),
    ("net.packet_loss",     "http.error_rate_high","carried",  0.70,   5,  25),
    ("cert.expired",        "http.error_rate_high","same",     0.95,   1,   5),
    ("cfg.push",            "pod.crashloop",       "same",     0.05,  20,  90),
    ("http.error_rate_high","svc.impact",          "services", 0.90,   5,  25),
    ("http.latency_high",   "svc.latency_high",    "services", 0.80,  30, 120),
    ("svc.latency_high",    "svc.sla_breach",      "same",     0.60,  60, 300),
]

SEVERITY = {"node.failure": 1, "pod.oom_kill": 2, "pod.crashloop": 1,
            "db.conn_exhausted": 1, "db.latency_high": 2, "cache.evictions_high": 3,
            "queue.backlog": 3, "net.packet_loss": 2, "cert.expired": 1,
            "cfg.push": 4, "http.error_rate_high": 1, "http.latency_high": 2,
            "svc.latency_high": 2, "svc.sla_breach": 1, "svc.failover": 3}

LAYER_OF_TYPE = {"node.failure": "infra", "pod.oom_kill": "app", "pod.crashloop": "app",
                 "db.conn_exhausted": "data", "db.latency_high": "data",
                 "cache.evictions_high": "data", "queue.backlog": "data",
                 "net.packet_loss": "net", "cert.expired": "edge", "cfg.push": "app",
                 "http.error_rate_high": "edge", "http.latency_high": "edge",
                 "svc.latency_high": "service", "svc.sla_breach": "service",
                 "svc.failover": "service"}


def raw_names(entity_id: str) -> list[str]:
    e = entity_id
    out = [e]
    low = e.lower()
    if e.startswith("POD-"):
        _, name, zone = e.split("-", 2)
        out += [f"{name.lower()}-7f9c4-{zone.lower()}", f"deploy/{name.lower()}@{zone.lower()}",
                f"{name.lower()}.svc.cluster.local"]
    elif e.startswith("NODE-"):
        z = e.split("-", 1)[1].lower()
        out += [f"ip-10-0-{ord(z[-1]) - 96}{z[0]}-{ord(z[-1])}.ec2.internal", f"node/{z}"]
    elif e.startswith("PG-"):
        out += [f"orders-db-{'primary' if e.endswith('1A') else 'replica'}",
                low.replace("-", ".")]
    elif e.startswith("REDIS-"):
        out += ["cache-master-1b", "redis.cache.1b"]
    elif e.startswith("KAFKA-"):
        out += ["events-broker-1c", "kafka.events.1c"]
    elif e.startswith("GW-"):
        z = e.rsplit("-", 1)[1].lower()
        out += [f"edge-gw-{z}", f"ingress/{z}"]
    elif e.startswith("LNK-"):
        out += [low.replace("lnk-", "azlink-")]
    elif e.startswith("SVC:"):
        out += [e.replace("SVC:", "slo/"), e.replace("SVC:", "").upper()]
    return out


def _pod(rng: np.random.Generator) -> str:
    return str(rng.choice(list(PODS)))


SCENARIOS = {
    "bad_deploy": Scenario("bad_deploy",
        lambda e: f"P1: SLO burn-rate alert after deploy to {e}",
        "P1", "cfg.push", _pod, config_error=True),
    "db_conn_storm": Scenario("db_conn_storm",
        lambda e: f"P1: Connection pool exhaustion on {e}",
        "P1", "db.conn_exhausted", lambda rng: str(rng.choice(["PG-ORDERS-1A", "PG-ORDERS-1B"]))),
    "cache_stampede": Scenario("cache_stampede",
        lambda e: f"P2: Cache eviction storm on {e}",
        "P2", "cache.evictions_high", lambda rng: "REDIS-CACHE-1B"),
    "node_failure": Scenario("node_failure",
        lambda e: f"P1: Kubernetes node failure {e}",
        "P1", "node.failure", lambda rng: str(rng.choice(list(NODES)))),
    "silent_node": Scenario("silent_node",
        lambda e: f"P1: Correlated pod crashloops in {NODES.get(e, '?')} (no node alert)",
        "P1", "node.failure", lambda rng: str(rng.choice(list(NODES))),
        hidden_root=True),
    "cert_expiry": Scenario("cert_expiry",
        lambda e: f"P1: TLS certificate expired on {e}",
        "P1", "cert.expired", lambda rng: str(rng.choice(list(GATEWAYS)))),
    "zone_link": Scenario("zone_link",
        lambda e: f"P2: Cross-AZ packet loss on {e}",
        "P2", "net.packet_loss", lambda rng: str(rng.choice(list(LINKS)))),
    "queue_backlog": Scenario("queue_backlog",
        lambda e: f"P3: Consumer lag climbing on {e}",
        "P3", "queue.backlog", lambda rng: "KAFKA-EVENTS-1C"),
    "novel_storm": Scenario("novel_storm",
        lambda e: "P1: Uncharacterized multi-layer alert storm",
        "P1", "net.packet_loss", lambda rng: str(rng.choice(list(LINKS))), noise=(60, 90)),
}

register(DomainPack(
    key="cloud",
    name="Cloud · SaaS platform",
    tenant="meridian-commerce-prod",
    world_title="Platform — meridian-commerce, 3 AZs",
    icon="☁",
    build_world=build_world,
    true_rules=TRUE_RULES,
    severity=SEVERITY,
    layer_of_type=LAYER_OF_TYPE,
    noise_types=["hpa.scale_event", "cronjob.slow", "log.volume_high",
                 "dns.slow_resolve", "image.pull_slow"],
    scenarios=SCENARIOS,
    scenario_weights={"bad_deploy": 0.20, "db_conn_storm": 0.14, "cache_stampede": 0.12,
                      "node_failure": 0.13, "silent_node": 0.05, "cert_expiry": 0.10,
                      "zone_link": 0.13, "queue_backlog": 0.13},
    canonical={"incident_id": "INC-2026-08-10-0219", "scenario": "bad_deploy",
               "root_entity": "POD-CHECKOUT-1A", "seed": 55,
               "title": "P1: Checkout SLO burn after the 02:00 deploy"},
    outage_types={"svc.sla_breach"},
    degradation_types={"svc.latency_high"},
    impact_protected_type="svc.failover",
    impact_outage_type="svc.sla_breach",
    runbooks={
        "cfg.push": ("rollback_deploy", "Roll back to the previous release revision"),
        "pod.crashloop": ("restart_pods", "Restart the crashlooping deployment with backoff reset"),
        "pod.oom_kill": ("restart_pods", "Raise memory limits and restart the deployment"),
        "node.failure": ("drain_node", "Cordon + drain the node; reschedule pods"),
        "db.conn_exhausted": ("db_failover", "Recycle the connection pool; fail over if primary is degraded"),
        "db.latency_high": ("db_failover", "Fail reads over to the replica; kill slow queries"),
        "cache.evictions_high": ("scale_cache", "Scale the cache tier and warm hot keys"),
        "queue.backlog": ("scale_consumers", "Scale out the consumer group"),
        "cert.expired": ("rotate_cert", "Rotate the TLS certificate and reload the gateway"),
        "net.packet_loss": ("reroute_traffic", "Shift traffic off the degraded AZ link"),
        "http.error_rate_high": ("restart_pods", "Restart the erroring tier behind the gateway"),
        "http.latency_high": ("scale_out", "Scale out the slow tier"),
    },
    action_dynamics={
        "rollback_deploy": (2.5, 1.0, 30, True), "restart_pods": (1.5, 0.6, 20, True),
        "drain_node": (8.0, 3.0, 120, True), "db_failover": (5.0, 2.0, 60, True),
        "scale_cache": (4.0, 1.5, 45, True), "scale_consumers": (3.0, 1.2, 30, True),
        "rotate_cert": (2.0, 0.8, 15, True), "reroute_traffic": (3.5, 1.4, 40, True),
        "scale_out": (3.0, 1.2, 30, True),
    },
    hard_down_types={"node.failure", "pod.crashloop", "cert.expired"},
    confounder_types={"node.failure"},
    raw_namer=raw_names,
    resolver_hints={"pod": ("POD-",), "deploy/": ("POD-",), "checkout": ("POD-CHECKOUT-",),
                    "cart": ("POD-CART-",), "search": ("POD-SEARCH-",),
                    "auth": ("POD-AUTH-",), "pay": ("POD-PAY-",),
                    "node": ("NODE-",), "ip-10": ("NODE-",), "orders-db": ("PG-",),
                    "pg": ("PG-",), "redis": ("REDIS-",), "cache-master": ("REDIS-",),
                    "kafka": ("KAFKA-",), "events-broker": ("KAFKA-",),
                    "gw": ("GW-",), "edge-gw": ("GW-",), "ingress": ("GW-",),
                    "lnk": ("LNK-",), "azlink": ("LNK-",), "slo/": ("SVC:",)},
    site_token=lambda s: set(re.findall(r"1[-_/.]?([abc])(?![a-z0-9])", s.lower())),
    expert_pins=[
        {"src": "cert.expired", "dst": "http.error_rate_high", "action": "pin",
         "by": "sre-lead", "reason": "an expired edge cert hard-fails every TLS "
         "handshake; certificate renewals are too rare to reach significance"},
        {"src": "svc.latency_high", "dst": "svc.sla_breach", "action": "pin",
         "by": "sre-lead", "reason": "sustained latency burns the SLO error budget "
         "into a breach by contract"},
    ],
))
