"""Domain-pack integrity: every industry must respect the canonical schema
and produce verifiable cascades, or the universal engine has nothing to hold."""
import os
os.environ.setdefault("KEEL_SANDBOX", "1")
import tempfile
import time

import numpy as np
import pytest

os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-test-"))

from keel.domains import all_packs
from keel.store import Store
from keel.substrate.simulator import simulate_incident

PACKS = list(all_packs().values())


@pytest.fixture(scope="module")
def stores():
    out = {}
    for pack in PACKS:
        st = Store(path=os.path.join(tempfile.mkdtemp(), f"{pack.key}.db"))
        pack.build_world(st)
        out[pack.key] = st
    return out


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.key)
def test_canonical_impact_schema(pack):
    for t in pack.outage_types | pack.degradation_types:
        assert t.startswith("svc."), f"{pack.key}: impact type {t} must be svc.*"
    assert pack.impact_outage_type in pack.outage_types
    # change events use the canonical name so the orientation prior holds
    assert all(dst != "cfg.push" for _, dst, *_ in pack.true_rules)


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.key)
def test_world_builds_with_services_and_shared_infra(pack, stores):
    st = stores[pack.key]
    ents = st.entities()
    kinds = {e.kind for e in ents}
    assert {"site", "ne", "service"} <= kinds
    assert "power" in kinds, f"{pack.key}: no shared-infra entities (latent class)"
    for e in ents:
        if e.kind == "service":
            assert e.entity_id.startswith("SVC:")
            assert e.attrs.get("paths"), f"{pack.key}: {e.entity_id} has no paths"
    sites = [e for e in ents if e.kind == "site"]
    assert all(e.attrs.get("pos") for e in sites), f"{pack.key}: sites need map positions"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.key)
def test_canonical_incident_produces_customer_impact(pack, stores):
    st = stores[pack.key]
    c = pack.canonical
    rng = np.random.default_rng(c["seed"])
    inc, events = simulate_incident(st, pack, c["scenario"], time.time() - 660,
                                    rng, "T-CANON", root_entity=c["root_entity"])
    impact = pack.outage_types | pack.degradation_types
    assert any(e.event_type in impact for e in events), \
        f"{pack.key}: canonical incident has nothing to explain"
    assert inc.ground_truth == f"{c['root_entity']}|{pack.scenarios[c['scenario']].root_type}"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.key)
def test_every_scenario_root_has_a_runbook(pack):
    for key, sc in pack.scenarios.items():
        if key == "novel_storm":
            continue
        assert sc.root_type in pack.runbooks or sc.root_type in pack.severity, \
            f"{pack.key}: scenario {key} root {sc.root_type} unmapped"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.key)
def test_resolver_precision_on_pack_aliases(pack, stores):
    from keel.substrate.resolver import EntityResolver
    st = stores[pack.key]
    truth = {}
    for e in st.entities():
        for raw in pack.raw_namer(e.entity_id):
            truth[raw] = e.entity_id
    metrics = EntityResolver(st, pack).audit(truth)
    assert metrics["precision"] >= 0.98, f"{pack.key}: precision {metrics}"
    assert metrics["recall"] >= 0.70, f"{pack.key}: recall {metrics}"
