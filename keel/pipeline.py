"""The money path: incident in, signed Causal Certificate (or abstention) out.

`run_verification` is a generator yielding one dict per pipeline stage so the
operator UI can animate progressive certainty, and A2A callers can stream the
same lifecycle. `seed_all` builds the reference deployment: network, 90 days
of resolved incidents, learned causal graph, calibration corpus, and a small
execution history so autonomy tiers have provenance.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

import numpy as np

from .adjudicate.bounds import observational_table, tian_pearl_bounds
from .adjudicate.counterfactual import (probability_of_necessity,
                                        probability_of_sufficiency)
from .adjudicate.refute import run_refuters
from .adjudicate.scm import OUTCOME, InstanceSCM, evidence_from_events, split_var
from .calibrate.conformal import (add_calibration_example, conformal_set, corpus)
from .calibrate.drift import (check_drift, incident_features, record_features,
                              record_fidelity, widen_amount)
from .cert import authority
from .config import (ABDUCTION_SAMPLES, CONFORMAL_ALPHA, TENANT)
from .gate.blast import compute_blast, hard_failed_elements
from .gate.policy import decide, tenant_autonomy
from .gate.shield import evaluate_constraints, project_to_safe, violations
from .hypothesis.evidence import EvidencePack, gather_evidence
from .hypothesis.generator import frontier_hypotheses, generate_hypotheses
from .models import (Adjudicated, Certificate, Hypothesis, Incident, Outcome,
                     RemediationAction)
from .domains import DEFAULT_DOMAIN, DomainPack, get_pack
from .otel import span
from .store import Store, get_store
from .structure.discovery import CausalDiscovery, AdjacencyIndex
from .structure.ensemble import active_edges, current_graph, publish_graph
from .structure.hawkes import HawkesModel
from .substrate.resolver import EntityResolver
from .substrate.simulator import (seed_canonical_incident, seed_history,
                                  simulate_incident)
from .twin.surrogate import propose_remediation, rollout

SCM_VERSION = "scm-noisyor-1"
MODEL_VERSION = "keel-0.1.0"


@dataclass
class Runtime:
    store: Store
    hawkes: HawkesModel
    pack: DomainPack = None      # type: ignore[assignment]
    graph_version: str = ""
    type_edges: list[dict] = field(default_factory=list)
    corpus_types: list[tuple[set, bool]] = field(default_factory=list)

    def refresh_graph(self) -> None:
        self.graph_version, rows = current_graph(self.store)
        self.type_edges = active_edges(rows)


# ── boot & seeding ───────────────────────────────────────────────────────────

def boot(domain: str = DEFAULT_DOMAIN, seed_if_empty: bool = True) -> Runtime:
    pack = get_pack(domain)
    store = get_store(domain)
    if not store.kv_get("seeded"):
        if pack.synthetic and seed_if_empty:
            seed_all(store, pack)
        elif not pack.synthetic:
            # BYO workspace: the customer's data is the world — nothing to seed
            store.kv_set("seeded", {"at": time.time(), "mode": "byo-data"})
    rt = Runtime(store=store, hawkes=HawkesModel(), pack=pack)
    resolved = [i for i in store.incidents(limit=400) if i.status == "resolved"]
    seqs = [store.events_for(i.incident_id) for i in resolved]
    rt.hawkes.fit([s for s in seqs if s])
    rt.refresh_graph()
    impact = pack.outage_types | pack.degradation_types
    rt.corpus_types = [({e.event_type for e in evs},
                        any(e.event_type in impact for e in evs))
                       for evs in seqs if evs]
    return rt


def seed_all(store: Store, pack: DomainPack) -> dict[str, Any]:
    t_start = time.time()
    pack.build_world(store)
    incidents = seed_history(store, pack)

    # entity resolution audit against the generator's alias truth table
    truth = {}
    for e in store.entities():
        for raw in pack.raw_namer(e.entity_id):
            truth[raw] = e.entity_id
    resolver = EntityResolver(store, pack)
    er_metrics = resolver.audit(truth)
    store.kv_set("resolver_metrics", er_metrics)

    seqs = [store.events_for(i.incident_id) for i in incidents]
    seqs = [s for s in seqs if s]

    # expert-pinned structure: real mechanisms the corpus is statistically
    # underpowered to identify — operator knowledge as a first-class input
    store.kv_set("expert_pins", pack.expert_pins)

    # two graph versions: first month vs full corpus (drift & provenance story)
    disco = CausalDiscovery(AdjacencyIndex(store.topology_at(time.time())),
                            sink_types=pack.impact_types,
                            exogenous_types=pack.change_types)
    early = disco.discover(seqs[: int(len(seqs) * 0.6)])
    publish_graph(store, early)
    full = disco.discover(seqs)
    version = publish_graph(store, full)

    # calibration corpus from resolved incidents (fast scoring, no refuters)
    rt = Runtime(store=store, hawkes=HawkesModel().fit(seqs), pack=pack)
    rt.refresh_graph()
    impact = pack.outage_types | pack.degradation_types
    rt.corpus_types = [({e.event_type for e in evs},
                        any(e.event_type in impact for e in evs)) for evs in seqs]
    n_cal = 0
    for inc in incidents:
        res = score_incident(rt, inc, fast=True)
        if res is None:
            continue
        add_calibration_example(store, inc.incident_id, res["stratum"],
                                res["true_score"], inc.t0)
        record_features(store, res["features"], baseline=True)
        n_cal += 1

    # execution history: certify + execute a handful of past incidents so the
    # tenant has earned an autonomy tier and the transparency log has depth
    executed = 0
    for inc in incidents[-40:]:
        sc = pack.scenarios.get(inc.scenario)
        if executed >= 12 or sc is None or sc.hidden_root:
            continue
        try:
            cert_id = _quick_certify_and_execute(rt, inc)
            executed += 1 if cert_id else 0
        except Exception:
            continue

    seed_canonical_incident(store, pack, time.time())
    store.kv_set("seeded", {"at": time.time(), "incidents": len(incidents),
                            "calibration": n_cal, "graph": version,
                            "seed_seconds": round(time.time() - t_start, 1)})
    return er_metrics


def _quick_certify_and_execute(rt: Runtime, inc: Incident) -> Optional[str]:
    """Seed-time fast path: certify a resolved incident and record its outcome."""
    final = None
    for step in run_verification(rt, inc.incident_id, fast=True):
        if step["stage"] == "certificate":
            final = step["data"]["cert_id"]
    if not final:
        return None
    cert = rt.store.certificate(final)
    if cert is None or cert.verdict != "SUPPORTED":
        return None
    execute_certificate(rt, final, approver="seed-harness", force=True)
    return final


# ── per-incident scoring (shared by pipeline, seeding, evaluation) ───────────

def _dominant_layer(pack: EvidencePack) -> str:
    counts: dict[str, int] = defaultdict(int)
    for i in pack.instances:
        if i["layer"] and i["layer"] not in ("env", "service"):
            counts[i["layer"]] += 1
    return max(counts, key=counts.get) if counts else "ip"


def _latent_flags(store: Store, pack: EvidencePack, type_edges: list[dict],
                  t0: float, confounder_types: set[str],
                  impact_types: set[str] = frozenset()) -> set[str]:
    """Variables whose adjudication must fall back to Tian-Pearl bounds.

    Trigger: >= 2 unexplained frontier failures on entities sharing a power
    feed that emitted nothing — the signature of an unobserved common cause.
    """
    parents_of: dict[str, set[str]] = defaultdict(set)
    for r in type_edges:
        parents_of[r["dst_type"]].add(r["src_type"])
    power_types = confounder_types
    seen_types: set[str] = set()
    frontier: list[dict] = []
    for inst in pack.instances:
        if inst["type"].startswith("svc.") or inst["type"] in impact_types:
            seen_types.add(inst["type"])
            continue
        possible = parents_of.get(inst["type"], set())
        # a power-explicable orphan: the graph says power could cause this,
        # but no power event was observed before it
        if (possible & power_types) and not (possible & seen_types):
            frontier.append(inst)
        seen_types.add(inst["type"])

    feeds: dict[str, set[str]] = defaultdict(set)
    for e in store.topology_at(t0):
        if e.relation == "feeds":
            feeds[e.src].add(e.dst)
    observed_entities = {i["entity"] for i in pack.instances}
    flagged: set[str] = set()
    for pwr, fed in feeds.items():
        if pwr in observed_entities:
            continue                     # feed is monitored and visible
        cluster = [f for f in frontier if f["entity"] in fed]
        if len(cluster) >= 2:
            flagged |= {f["variable"] for f in cluster}
    return flagged


def adjudicate_hypotheses(rt: Runtime, pack: EvidencePack, incident: Incident,
                          hyps: list[Hypothesis], fast: bool
                          ) -> tuple[list[Adjudicated], InstanceSCM, dict]:
    store = rt.store
    topo = store.topology_at(incident.t0)
    events = store.events_for(incident.incident_id)
    observed = {f"{e.entity_id}|{e.event_type}" for e in events}
    scm = InstanceSCM.build(rt.type_edges, topo, observed, incident.entities,
                            outage_types=rt.pack.outage_types,
                            degradation_types=rt.pack.degradation_types)
    evidence = evidence_from_events(
        events, scm.order,
        impact_types=rt.pack.outage_types | rt.pack.degradation_types)
    n = 800 if fast else ABDUCTION_SAMPLES
    latent = _latent_flags(store, pack, rt.type_edges, incident.t0,
                           rt.pack.confounder_types, rt.pack.impact_types)

    out: list[Adjudicated] = []
    for h in hyps:
        x = h.intervention.variable
        if x not in scm.index or evidence.get(x, 0) != 1:
            continue
        adj = Adjudicated(hypothesis=h)
        if x in latent:
            _, xt = split_var(x)
            tbl = observational_table(rt.corpus_types, xt)
            lo, hi = tian_pearl_bounds(*tbl)
            adj.pn, adj.pn_lo, adj.pn_hi = None, lo, hi
            adj.point_identified = False
            adj.identification = "bounds:tian-pearl (latent confounder suspected)"
        else:
            pn, lo, hi = probability_of_necessity(scm, evidence, x, n=n)
            adj.pn, adj.pn_lo, adj.pn_hi = pn, lo, hi
            adj.point_identified = True
            adj.identification = "point:noisy-or monotone SCM, exact abduction"
            if not fast:
                ps, pslo, pshi = probability_of_sufficiency(scm, x, n=n // 2)
                adj.ps, adj.ps_lo, adj.ps_hi = ps, pslo, pshi
        if not fast:
            base_pn = adj.pn if adj.pn is not None else (adj.pn_lo + adj.pn_hi) / 2
            adj.refutations = run_refuters(scm, evidence, x, base_pn)
            adj.refutation_passed = all(r.passed for r in adj.refutations)
        out.append(adj)

    # ranking score for conformal calibration: causal strength blended with
    # prior. Refutation results are recorded and affect the VERDICT, but never
    # this score — otherwise live scores and calibration scores would come
    # from different distributions and the coverage guarantee would be void.
    for adj in out:
        core = adj.pn if adj.pn is not None else (adj.pn_lo + adj.pn_hi) / 2
        adj.score = max(1e-6, 0.85 * core + 0.15 * adj.hypothesis.prior_confidence)
    total = sum(a.score for a in out) or 1.0
    for adj in out:
        adj.score = adj.score / total
    out.sort(key=lambda a: -a.score)
    return out, scm, evidence


def score_incident(rt: Runtime, incident: Incident, fast: bool = True
                   ) -> Optional[dict[str, Any]]:
    """Fast scoring used for calibration seeding and evaluation replay."""
    events = rt.store.events_for(incident.incident_id)
    if not events:
        return None
    adj = AdjacencyIndex(rt.store.topology_at(incident.t0))
    annotated = rt.hawkes.annotate(events, adjacency=adj)
    pack = gather_evidence(rt.store, incident, annotated, domain=rt.pack.key,
                           change_types=rt.pack.change_types)
    hyps = frontier_hypotheses(pack, rt.type_edges,
                               impact_types=rt.pack.impact_types,
                               change_types=rt.pack.change_types)
    if not hyps:
        return None
    adjudicated, _, evidence = adjudicate_hypotheses(rt, pack, incident, hyps, fast)
    if not adjudicated or evidence.get(OUTCOME, 0) == 0:
        return None
    scores = {a.hypothesis.intervention.variable: a.score for a in adjudicated}
    true_score = scores.get(incident.ground_truth or "", 0.0)
    duration = incident.t1 - incident.t0
    feats = incident_features(incident.alarm_count, len(pack.layers), duration,
                              pack.suppression.get("compression", 0),
                              len(incident.entities))
    return {"scores": scores, "true_score": true_score,
            "ranked": [a.hypothesis.intervention.variable for a in adjudicated],
            "stratum": _dominant_layer(pack), "features": feats,
            "adjudicated": adjudicated}


# ── the live verification pipeline ───────────────────────────────────────────

def run_verification(rt: Runtime, incident_id: str,
                     claim_variable: Optional[str] = None,
                     claimant: str = "keel-hypothesizer",
                     fast: bool = False
                     ) -> Generator[dict[str, Any], None, None]:
    store = rt.store
    incident = store.incident(incident_id)
    if incident is None:
        yield {"stage": "error", "status": "failed",
               "detail": f"unknown incident {incident_id}"}
        return

    def stage(name: str, detail: str, data: Any = None, status: str = "done"):
        return {"stage": name, "status": status, "detail": detail,
                "data": data or {}}

    # P1 — substrate
    events = store.events_for(incident_id)
    adj = AdjacencyIndex(store.topology_at(incident.t0))
    annotated = rt.hawkes.annotate(events, adjacency=adj)
    informative = sum(1 for e in annotated if not e.suppressed)
    resolver_metrics = store.kv_get("resolver_metrics", {})
    yield stage("substrate",
                f"{len(events)} alarms → {informative} informative after Hawkes "
                f"suppression ({(1 - informative / max(len(events), 1)):.0%} compressed)",
                {"alarms": len(events), "informative": informative,
                 "resolver": resolver_metrics})

    # P2 — structure
    yield stage("structure",
                f"causal graph {rt.graph_version} · {len(rt.type_edges)} edges "
                f"(topology-constrained Hawkes + stability selection)",
                {"graph_version": rt.graph_version,
                 "edges": len(rt.type_edges)})

    # P3 — hypotheses
    pack = gather_evidence(store, incident, annotated, domain=rt.pack.key,
                           change_types=rt.pack.change_types)
    hyps, generator_name = generate_hypotheses(pack, rt.type_edges,
                                               impact_types=rt.pack.impact_types,
                                               change_types=rt.pack.change_types)
    if claim_variable:
        valid_vars = {i["variable"] for i in pack.instances}
        if claim_variable in valid_vars:
            hyps = ([h for h in hyps if h.intervention.variable != claim_variable]
                    [: len(hyps) - 1])
            hyps.insert(0, Hypothesis(
                hypothesis_id="claim", mechanism="external agent claim",
                intervention={"variable": claim_variable, "set_to": "nominal"},
                evidence_refs=[claim_variable], prior_confidence=0.5,
                source=claimant))
        else:
            yield stage("hypotheses",
                        f"external claim '{claim_variable}' references no "
                        "observed variable — rejected at the schema boundary",
                        status="failed")
            for s in _abstain_path(rt, incident, pack, [], {}, claimant,
                                   claim_variable,
                                   f"claim '{claim_variable}' references no "
                                   "variable observed in this incident",
                                   generator_name, verdict="INSUFFICIENT"):
                yield s
            return
    yield stage("hypotheses",
                f"{len(hyps)} schema-valid hypotheses from {generator_name}",
                {"hypotheses": [h.model_dump() for h in hyps],
                 "evidence": pack.as_dict()})

    if not hyps:
        for s in _abstain_path(rt, incident, pack, [], {}, claimant,
                               claim_variable, "no valid hypotheses in scope",
                               generator_name):
            yield s
        return

    # P4 — adjudication
    yield stage("adjudication", "abduction → action → prediction over the SCM",
                status="running")
    with span("keel.adjudication", {"incident": incident.incident_id,
                                    "hypotheses": len(hyps)}):
        adjudicated, scm, evidence = adjudicate_hypotheses(rt, pack, incident,
                                                           hyps, fast)
    if evidence.get(OUTCOME, 0) == 0:
        for s in _abstain_path(rt, incident, pack, adjudicated, {}, claimant,
                               claim_variable,
                               "no customer-visible outage in evidence; nothing "
                               "to explain causally", generator_name,
                               verdict="INSUFFICIENT"):
            yield s
        return
    yield stage("adjudication",
                f"{len(adjudicated)} hypotheses adjudicated "
                f"({sum(1 for a in adjudicated if a.point_identified)} point-"
                f"identified, {sum(1 for a in adjudicated if not a.point_identified)} bounded)",
                {"adjudicated": [a.model_dump() for a in adjudicated]})

    # P5 — calibration
    drift = check_drift(store)
    scores = {a.hypothesis.hypothesis_id: a.score for a in adjudicated}
    conf = conformal_set(store, scores, stratum=_dominant_layer(pack),
                         widen=widen_amount(drift))
    duration = incident.t1 - incident.t0
    feats = incident_features(incident.alarm_count, len(pack.layers), duration,
                              pack.suppression.get("compression", 0),
                              len(incident.entities))
    record_features(store, feats, baseline=False)
    yield stage("calibration",
                f"conformal set of {len(conf['set'])} at α={CONFORMAL_ALPHA} "
                f"(q̂={conf['q_hat']}, {conf['strata']}, n={conf['n']}) · drift: {drift.level}",
                {"conformal": conf, "drift": drift.model_dump()})

    # verdict
    top = adjudicated[0]
    subject = top
    if claim_variable:
        subject = next((a for a in adjudicated
                        if a.hypothesis.intervention.variable == claim_variable),
                       top)
    abstain_reason = conf.get("abstain_reason")
    if drift.level == "breach":
        abstain_reason = "drift gate breach: " + "; ".join(drift.notes)
    if abstain_reason:
        for s in _abstain_path(rt, incident, pack, adjudicated, conf, claimant,
                               claim_variable, abstain_reason, generator_name,
                               drift=drift):
            yield s
        return

    if len(conf["set"]) == 0:
        for s in _abstain_path(rt, incident, pack, adjudicated, conf, claimant,
                               claim_variable,
                               "no hypothesis cleared the conformal bar "
                               f"(q̂={conf['q_hat']}); evidence does not support "
                               "a calibrated causal claim", generator_name,
                               drift=drift):
            yield s
        return

    in_set = subject.hypothesis.hypothesis_id in conf["set"]
    placebo_ok = all(r.passed for r in subject.refutations
                     if r.refuter == "placebo_treatment")
    perturb_ok = all(r.passed for r in subject.refutations
                     if r.refuter != "placebo_treatment")
    if not placebo_ok:
        verdict = "REFUTED"          # the model credits placebos: no claim survives
    elif in_set and len(conf["set"]) == 1:
        verdict = "SUPPORTED" if perturb_ok else "AMBIGUOUS"
    elif in_set:
        verdict = "AMBIGUOUS"
    else:
        verdict = "REFUTED"

    action = twin_pred = blast = gate_result = None
    if verdict == "SUPPORTED":
        # P3' — remediation proposal
        root_var = subject.hypothesis.intervention.variable
        ent, _ = split_var(root_var)
        action = propose_remediation(rt.pack, root_var,
                                     backup_target="OTS-CHN-9"
                                     if ent == "OTS-CHN-7" else "")
        yield stage("remediation", f"runbook action: {action.description}",
                    {"action": action.model_dump()})

        # P6 — twin rollout
        with span("keel.twin_rollout", {"action_class": action.action_class}):
            twin_pred = rollout(scm, evidence, action, rt.pack, store=store)
        yield stage("twin",
                    f"T1 twin: P(resolve)={twin_pred.p_resolve:.2f}, restore "
                    f"{twin_pred.restore_minutes:.1f} min "
                    f"[{twin_pred.restore_lo:.1f}, {twin_pred.restore_hi:.1f}] · "
                    f"fidelity {twin_pred.fidelity_score:.2f}",
                    {"twin": twin_pred.model_dump()})

        # P6 — blast radius + shield + policy
        topo = store.topology_at(incident.t0)
        failed = hard_failed_elements(evidence, rt.pack.hard_down_types)
        blast = compute_blast(store, topo, action, failed)
        costs = evaluate_constraints(store, action, blast, twin_pred, failed)
        viol = violations(costs)
        projected = False
        if viol:
            proj = project_to_safe(store, topo, action, twin_pred, failed)
            if proj is not None:
                action, blast, costs = proj
                viol, projected = [], True

        draft = {"verdict": verdict, "pn_lo": subject.pn_lo,
                 "conformal": {"alpha": CONFORMAL_ALPHA},
                 "blast_radius": blast.model_dump(),
                 "twin": twin_pred.model_dump(),
                 "drift": drift.model_dump()}
        policy = decide(store, draft, action.action_class, when=incident.t1)
        if viol:
            decision_word, gate_decision = "BLOCKED", "BLOCK"
            reason = "CMDP constraints violated: " + "; ".join(viol)
        elif policy["allow"]:
            gate_decision = "SIGN"
            decision_word = (f"AUTO-EXECUTE AUTHORIZED ({policy['tier_name']})")
            reason = "shield PASS · policy PASS"
        elif policy["escalate"]:
            gate_decision, decision_word = "ESCALATE", "ESCALATED TO HUMAN"
            reason = "; ".join(policy["reasons"][:3])
        else:
            gate_decision, decision_word = "ESCALATE", "HUMAN APPROVAL REQUIRED"
            reason = "; ".join(policy["reasons"][:3])
        gate_result = {"decision": gate_decision, "reason": reason,
                       "constraints": costs, "violated": viol,
                       "projected": projected, "policy": policy,
                       "decision_word": decision_word}
        yield stage("gate",
                    f"blast {len(blast.elements)} elements / "
                    f"{blast.slas_at_risk} SLAs at risk · {decision_word}",
                    {"blast": blast.model_dump(), "gate": gate_result})

    cert = _build_certificate(rt, incident, subject, adjudicated, conf, drift,
                              pack, claimant, claim_variable, verdict, action,
                              twin_pred, blast, gate_result, generator_name)
    cert = authority.issue(store, cert)
    incident.status = "certified"
    store.put_incident(incident)
    yield stage("certificate",
                f"{cert.cert_id} · {verdict} · signed {cert.signer} · "
                f"log index {cert.log_index}",
                {"cert_id": cert.cert_id, "certificate": cert.model_dump()})
    yield stage("done", "verification complete", {"cert_id": cert.cert_id})


def _abstain_path(rt: Runtime, incident: Incident, pack, adjudicated, conf,
                  claimant, claim_variable, reason: str, generator_name: str,
                  drift=None, verdict: str = "ABSTAIN"):
    drift = drift or check_drift(rt.store)
    yield {"stage": "calibration", "status": "done",
           "detail": f"{verdict}: {reason}",
           "data": {"conformal": conf, "drift": drift.model_dump()}}
    subject = adjudicated[0] if adjudicated else None
    cert = _build_certificate(rt, incident, subject, adjudicated, conf, drift,
                              pack, claimant, claim_variable, verdict, None,
                              None, None, None, generator_name,
                              abstain_reason=reason)
    cert = authority.issue(rt.store, cert)
    incident.status = "abstained"
    rt.store.put_incident(incident)
    yield {"stage": "certificate", "status": "done",
           "detail": f"{cert.cert_id} · {verdict} — the honest answer, on the record",
           "data": {"cert_id": cert.cert_id, "certificate": cert.model_dump()}}
    yield {"stage": "done", "status": "done",
           "detail": "verification complete (abstained)",
           "data": {"cert_id": cert.cert_id}}


def _build_certificate(rt, incident, subject, adjudicated, conf, drift, pack,
                       claimant, claim_variable, verdict, action, twin_pred,
                       blast, gate_result, generator_name,
                       abstain_reason: str = "") -> Certificate:
    claim_var = (claim_variable or
                 (subject.hypothesis.intervention.variable if subject else ""))
    mechanism = subject.hypothesis.mechanism if subject else ""
    competing = [{
        "hypothesis_id": a.hypothesis.hypothesis_id,
        "variable": a.hypothesis.intervention.variable,
        "mechanism": a.hypothesis.mechanism,
        "pn": a.pn, "pn_lo": a.pn_lo, "pn_hi": a.pn_hi,
        "score": round(a.score, 4), "identified": a.point_identified,
        "refutation_passed": a.refutation_passed, "source": a.hypothesis.source,
    } for a in adjudicated]
    decision = "REPORT_ONLY"
    if gate_result:
        decision = gate_result["decision_word"]
    elif verdict == "ABSTAIN":
        decision = f"ABSTAINED — {abstain_reason}"
    elif verdict == "INSUFFICIENT":
        decision = f"INSUFFICIENT — {abstain_reason}"
    return Certificate(
        cert_id=authority.new_cert_id(), tenant=rt.pack.tenant,
        incident_id=incident.incident_id,
        claim={"root_cause": claim_var, "mechanism": mechanism,
               "generator": generator_name},
        claimant=claimant, verdict=verdict,
        pn=subject.pn if subject else None,
        pn_lo=subject.pn_lo if subject else 0.0,
        pn_hi=subject.pn_hi if subject else 1.0,
        ps=subject.ps if subject else None,
        ps_lo=subject.ps_lo if subject else 0.0,
        ps_hi=subject.ps_hi if subject else 1.0,
        point_identified=subject.point_identified if subject else False,
        identification=subject.identification if subject else "",
        competing=competing,
        evidence_summary={
            "alarms": incident.alarm_count,
            "layers": pack.layers if pack else [],
            "window_minutes": round((incident.t1 - incident.t0) / 60, 1),
            "suppression": pack.suppression if pack else {},
        },
        refutation=[r.model_dump() for r in (subject.refutations if subject else [])],
        conformal={"set": conf.get("set", []), "alpha": CONFORMAL_ALPHA,
                   "q_hat": conf.get("q_hat"), "n": conf.get("n", 0),
                   "strata": conf.get("strata", "")},
        drift=drift.model_dump(),
        action=action.model_dump() if action else None,
        twin=twin_pred.model_dump() if twin_pred else None,
        blast_radius=blast.model_dump() if blast else None,
        gate={k: v for k, v in (gate_result or {}).items() if k != "policy"} or None,
        autonomy_tier=(gate_result or {}).get("policy", {}).get("tier", 0)
        if gate_result else tenant_autonomy(rt.store)["tier"],
        decision=decision,
        graph_version=rt.graph_version, scm_version=SCM_VERSION,
        model_version=MODEL_VERSION, created_at=time.time())


# ── BYO-data workspaces: learn, label, watch ─────────────────────────────────

def learn_workspace(rt: Runtime, infer_topo_if_empty: bool = True
                    ) -> dict[str, Any]:
    """Learn structure + calibrate from whatever the customer has ingested.

    Runs: (optional) topology inference → causal discovery over all incident
    windows → graph publish → calibration scoring over labeled incidents →
    Hawkes refit. Idempotent; call again whenever new data lands.
    """
    from .substrate.ingest import detect_incidents, infer_topology
    store = rt.store
    result: dict[str, Any] = {}

    if infer_topo_if_empty and not store.topology_at(time.time()):
        result["topology"] = infer_topology(store)

    detect_incidents(store, rt.pack, horizon_s=None)   # window ALL history

    incidents = [i for i in store.incidents(limit=1000)]
    seqs = [store.events_for(i.incident_id) for i in incidents]
    seqs = [x for x in seqs if x]
    if not seqs:
        return {**result, "error": "no incident windows yet — ingest events first"}

    # profile-implied structure: the customer DECLARED these types as
    # degradation vs outage; escalation between them is domain knowledge the
    # base-rate-corrected statistics systematically under-power. Auto-pinned
    # with visible provenance — veto in the Causal Atlas if wrong.
    pins = store.kv_get("expert_pins", None)
    if pins is None:
        pins = list(rt.pack.expert_pins)
    existing = {(x["src"], x["dst"]) for x in pins}
    for d_t in sorted(rt.pack.degradation_types):
        for o_t in sorted(rt.pack.outage_types):
            if (d_t, o_t) not in existing:
                pins.append({"src": d_t, "dst": o_t, "action": "pin",
                             "by": "keel-advisor",
                             "reason": "declared degradation escalates to "
                             "declared outage (from workspace profile); veto "
                             "in the Causal Atlas if this is wrong"})
    store.kv_set("expert_pins", pins)
    disco = CausalDiscovery(AdjacencyIndex(store.topology_at(time.time())),
                            sink_types=rt.pack.impact_types,
                            exogenous_types=rt.pack.change_types)
    edges = disco.discover(seqs)
    version = publish_graph(store, edges)
    rt.refresh_graph()
    result["graph"] = {"version": version, "edges": len(rt.type_edges)}

    rt.hawkes.fit(seqs)
    impact = rt.pack.impact_types
    rt.corpus_types = [({e.event_type for e in evs},
                        any(e.event_type in impact for e in evs)) for evs in seqs]

    n_cal = 0
    for inc in incidents:
        if inc.status != "resolved" or not inc.ground_truth:
            continue
        res = score_incident(rt, inc, fast=True)
        if res is None:
            continue
        add_calibration_example(store, inc.incident_id, res["stratum"],
                                res["true_score"], inc.t0)
        record_features(store, res["features"], baseline=True)
        n_cal += 1
    result["calibration"] = {"labeled_scored": n_cal,
                             "corpus_n": len(corpus(store))}
    result["incidents"] = len(incidents)
    return result


def resolve_incident(rt: Runtime, incident_id: str, root_cause: str,
                     verified_by: str = "operator") -> dict[str, Any]:
    """Operator labels the true root cause after resolution — the moment the
    loop closes and the calibration corpus (the moat) grows."""
    store = rt.store
    inc = store.incident(incident_id)
    if inc is None:
        return {"error": "unknown incident"}
    observed = {f"{e.entity_id}|{e.event_type}"
                for e in store.events_for(incident_id)}
    if root_cause not in observed:
        return {"error": f"'{root_cause}' is not an observed variable of this "
                         "incident — labels must reference real evidence"}
    inc.ground_truth = root_cause
    inc.status = "resolved"
    store.put_incident(inc)
    res = score_incident(rt, inc, fast=True)
    if res is not None:
        add_calibration_example(store, incident_id, res["stratum"],
                                res["scores"].get(root_cause, 0.0), inc.t0)
    from .hypothesis.evidence import _incident_vector
    from .substrate.vectors import get_index
    get_index(rt.pack.key).upsert(incident_id, _incident_vector(store, inc),
                                  {"title": inc.title, "root_cause": root_cause,
                                   "scenario": inc.scenario})
    return {"labeled": True, "corpus_n": len(corpus(store)),
            "scored": res is not None}


def watch_tick(rt: Runtime) -> dict[str, Any]:
    """One autonomous pass: detect newly-closed incident windows; verify them
    if the workspace opted into auto_verify and a graph exists."""
    from .substrate.ingest import detect_incidents
    created = detect_incidents(rt.store, rt.pack)
    verified = []
    if rt.pack.auto_verify and rt.type_edges:
        for inc in created:
            try:
                for step in run_verification(rt, inc.incident_id,
                                             claimant="keel-watch"):
                    if step["stage"] == "certificate":
                        verified.append(step["data"]["cert_id"])
            except Exception:
                continue
    return {"detected": [i.incident_id for i in created], "verified": verified}


# ── execution & the closing loop ─────────────────────────────────────────────

def execute_certificate(rt: Runtime, cert_id: str, approver: str,
                        force: bool = False) -> dict[str, Any]:
    store = rt.store
    cert = store.certificate(cert_id)
    if cert is None:
        return {"error": "unknown certificate"}
    if cert.action is None or cert.verdict != "SUPPORTED":
        return {"error": f"certificate is {cert.verdict} with no signed action"}
    gate = cert.gate or {}
    if gate.get("decision") == "BLOCK" and not force:
        return {"error": "action is BLOCKED by the gate; execution refused"}

    incident = store.incident(cert.incident_id)
    rng = np.random.default_rng(abs(hash(cert_id)) % (2**32))
    truth = incident.ground_truth if incident else None
    claimed = cert.claim.get("root_cause", "")
    addressed = truth is not None and claimed == truth
    p_actual = 0.93 if addressed else 0.22
    resolved = bool(rng.random() < p_actual)
    predicted = (cert.twin or {}).get("p_resolve", 0.5)
    # Brier score: proper scoring rule — honest probabilistic predictions are
    # not punished as if they were failed point forecasts
    err = (predicted - (1.0 if resolved else 0.0)) ** 2
    record_fidelity(store, cert.action["action_class"], err, time.time())

    sla_lost = 0.0
    if incident and incident.sla_services:
        base = (cert.twin or {}).get("restore_minutes", 5.0)
        sla_lost = round(base * (1.0 if resolved else 2.5), 1)

    outcome = Outcome(
        cert_id=cert_id, true_root_cause=truth,
        action_executed=True,
        action_outcome="resolved" if resolved else "no_effect",
        sla_minutes_lost=sla_lost,
        human_agreed=addressed, verified_by=approver, verified_at=time.time())
    store.put_outcome(outcome)

    if incident:
        incident.status = "resolved"
        store.put_incident(incident)
        # the resolved incident joins the similar-incident vector index
        from .hypothesis.evidence import _incident_vector
        from .substrate.vectors import get_index
        get_index(rt.pack.key).upsert(
            incident.incident_id, _incident_vector(store, incident),
            {"title": incident.title, "root_cause": truth,
             "scenario": incident.scenario})
        # the loop closes: this incident becomes a calibration example
        res = score_incident(rt, incident, fast=True)
        if res is not None and truth:
            add_calibration_example(store, incident.incident_id, res["stratum"],
                                    res["scores"].get(truth, 0.0), incident.t0)
    return {"executed": True, "resolved": resolved,
            "outcome": outcome.model_dump(),
            "fidelity_error": round(err, 3),
            "autonomy": tenant_autonomy(store)}
