"""Security framework: input/output/data security, RBAC, audit, compliance.

Enabled by default (see ``cockpit.config.feature_flags``). ``input_security``
and ``output_security`` are real, working Tier 1 implementations built on
``re`` only. ``data_security``, ``access_control``, ``audit_logging``,
``compliance``, and ``threat_detection`` are Tier 3 and ship as typed
skeletons — see ``docs/ARCHITECTURE.md`` for the planned design.
"""
