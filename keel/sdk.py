"""KEEL SDK — three lines to put any agent behind the trust gateway.

Framework-agnostic (LangChain, OpenAI SDK, CrewAI, AutoGen, plain Python):
wrap the functions your agent calls as tools; KEEL checks BEFORE execution
and learns from the outcome AFTER. Zero dependencies beyond stdlib.

    from keel.sdk import KeelGuard, GuardRejected

    guard = KeelGuard("http://127.0.0.1:8347", agent_id="support-bot")
    guard.register(name="Support Bot", action_classes={
        "issue_refund": {"risk": "high", "budget_per_day": 500,
                         "requires_evidence": True},
        "send_reply":   {"risk": "low"},
    })

    @guard.protect("issue_refund",
                   cost=lambda amount, **kw: amount,
                   targets=lambda customer_id, **kw: [customer_id])
    def issue_refund(customer_id: str, amount: float):
        ...  # only runs if the gateway ALLOWs (or a human approves)
"""
from __future__ import annotations

import functools
import os
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional


class GuardRejected(RuntimeError):
    def __init__(self, decision: dict[str, Any]):
        self.decision = decision
        super().__init__(f"KEEL {decision.get('decision')}: "
                         + "; ".join(decision.get("reasons", [])[:2]))


class KeelGuard:
    def __init__(self, base_url: str, agent_id: str, timeout: float = 15.0,
                 wait_for_approval_s: float = 0.0, api_key: str = ""):
        self.base = base_url.rstrip("/")
        self.agent_id = agent_id
        self.timeout = timeout
        self.wait_for_approval_s = wait_for_approval_s
        # account API key (keel_ak_…) — required when the server enforces auth
        self.api_key = api_key or os.environ.get("KEEL_API_KEY", "")

    # ── raw API ──────────────────────────────────────────────────────────────
    def _call(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def register(self, name: str, action_classes: dict[str, dict[str, Any]],
                 owner: str = "", framework: str = "custom",
                 shadow_mode: bool = False) -> dict[str, Any]:
        return self._call("POST", "/api/gateway/agents", {
            "agent_id": self.agent_id, "name": name, "owner": owner,
            "framework": framework, "shadow_mode": shadow_mode,
            "action_classes": {k: {"name": k, **v}
                               for k, v in action_classes.items()}})

    def check(self, action_class: str, intent: str = "",
              payload: Optional[dict[str, Any]] = None,
              targets: Optional[list[str]] = None,
              claims: Optional[list[dict[str, Any]]] = None,
              evidence: Optional[list[dict[str, Any]]] = None,
              cost: float = 0.0, reversible: bool = True,
              idempotency_key: str = "") -> dict[str, Any]:
        return self._call("POST", "/api/gateway/check", {
            "agent_id": self.agent_id, "action_class": action_class,
            "intent": intent, "payload": payload or {},
            "targets": targets or [], "claims": claims or [],
            "evidence": evidence or [], "cost": cost,
            "reversible": reversible, "idempotency_key": idempotency_key})

    def outcome(self, request_id: str, success: bool, detail: str = "",
                harm: bool = False) -> dict[str, Any]:
        return self._call("POST", "/api/gateway/outcome", {
            "request_id": request_id, "success": success,
            "detail": detail, "harm": harm})

    def _await_approval(self, request_id: str) -> dict[str, Any] | None:
        deadline = time.time() + self.wait_for_approval_s
        while time.time() < deadline:
            time.sleep(min(3.0, max(0.5, deadline - time.time())))
            for d in self._call("GET", "/api/gateway/decisions?limit=100"):
                if d["request_id"] == request_id and d["decision"] in ("ALLOW", "BLOCK"):
                    return d
        return None

    # ── the drop-in wrapper ──────────────────────────────────────────────────
    def protect(self, action_class: str,
                intent: Optional[Callable[..., str]] = None,
                cost: Optional[Callable[..., float]] = None,
                targets: Optional[Callable[..., list[str]]] = None,
                reversible: bool = True):
        """Decorator: the wrapped function only executes on ALLOW (or human
        approval, if wait_for_approval_s > 0). Outcome is auto-reported:
        normal return = success, exception = failure."""
        def wrap(fn: Callable):
            @functools.wraps(fn)
            def inner(*args, **kwargs):
                dec = self.check(
                    action_class,
                    intent=(intent(*args, **kwargs) if intent
                            else f"{fn.__name__}({', '.join(map(repr, args))})"[:280]),
                    payload={"args": [repr(a)[:200] for a in args],
                             "kwargs": {k: repr(v)[:200] for k, v in kwargs.items()}},
                    targets=(targets(*args, **kwargs) if targets else []),
                    cost=(cost(*args, **kwargs) if cost else 0.0),
                    reversible=reversible)
                if dec["decision"] == "ESCALATE" and self.wait_for_approval_s > 0:
                    dec = self._await_approval(dec["request_id"]) or dec
                if dec["decision"] not in ("ALLOW", "SHADOW"):
                    raise GuardRejected(dec)
                try:
                    result = fn(*args, **kwargs)
                except Exception as e:
                    self.outcome(dec["request_id"], success=False,
                                 detail=f"{type(e).__name__}: {e}"[:300])
                    raise
                self.outcome(dec["request_id"], success=True)
                return result
            return inner
        return wrap
