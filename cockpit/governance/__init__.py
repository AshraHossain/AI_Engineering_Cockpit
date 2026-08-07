"""Governance framework: model versioning, approvals, and a decision trail.

Answers "who changed what, when, and who signed off":

* :mod:`~cockpit.governance.model_versioning` -- a registry of model
  versions with stage promotion governed by an explicit transition map, and
  the invariant that at most one version per model serves production.
* :mod:`~cockpit.governance.approval_workflow` -- N-of-M human approval that
  refuses self-approval, counts one vote per principal, and treats rejection
  as final.
* :mod:`~cockpit.governance.audit_trail` -- a hash-chained record of those
  decisions, distinct from the security audit log in
  :mod:`cockpit.security.audit_logging`.

Gated by ``feature_flags.is_enabled("governance")`` at the mutating entry
points. Lookups and queries remain available with the flag off, so turning
governance off does not also blind you while investigating.
"""
