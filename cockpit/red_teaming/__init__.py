"""Red-teaming framework: adversarial, injection, and edge-case testing.

A defensive test harness. It exists so an engineer can attack their own LLM
application and find the weaknesses before an adversary does.

Three modules, three jobs:

* :mod:`~cockpit.red_teaming.prompt_injection` — a corpus of known
  prompt-injection payloads plus the adjudication logic that decides whether
  a target fell for one (including canary-token leak detection).
* :mod:`~cockpit.red_teaming.adversarial_tests` — the campaign harness that
  fires the corpus at a target and aggregates the outcomes.
* :mod:`~cockpit.red_teaming.edge_case_tests` — robustness probes for inputs
  that are nasty but not malicious (empty, oversized, unicode, control
  characters, malformed structured data).

This package is the *offense* counterpart to :mod:`cockpit.security`, which
is the defense. ``security.input_security.scan_for_prompt_injection`` asks
"does this incoming text look like an attack?"; this package asks "did the
attack land?". Detection logic lives in ``security`` and is reused from here,
never re-implemented.

The target system is always injected as a caller-supplied ``str -> str``
callable, so no module here imports an LLM SDK and the whole package is
testable offline. Campaign and runner entry points are gated behind
``feature_flags.is_enabled("red_teaming")`` and degrade to empty results when
the framework is disabled; payload generation and adjudication are pure and
always available.
"""
