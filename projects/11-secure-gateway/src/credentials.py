"""Hold the provider API key so it cannot reach a log line or a traceback.

A key read straight out of ``os.environ`` into a plain ``str`` is one
careless ``logger.info("config=%s", config)`` away from being in a log
aggregator forever, and one unhandled exception away from being in a
traceback. The value never needs to be *readable* by the application except
at the moment the SDK client is constructed, so it spends the rest of its
life inside a :class:`~cockpit.security.data_security.Secret`:

* ``repr()`` -- what ``logging`` prints for ``%r`` and what a rich traceback
  dumps for a local variable,
* ``str()`` and ``__format__`` -- ``f"{secret}"`` and ``"%s" % secret``,
* pickling -- blocked outright,

all render ``***REDACTED***`` instead of the key. :func:`redaction_proof`
exercises every one of those paths and returns the resulting lines, so the
CLI can print the proof and the tests can assert the key is not in it.

The wrapper is defence against *accidental* disclosure. It is not defence
against code already running in this process, which can call ``reveal()``
just as easily as the SDK builder does -- the value of ``reveal()`` is that
the one line where the secret leaves its wrapper is greppable in review.
"""

from __future__ import annotations

import os

from cockpit.security.data_security import (
    DataClassification,
    HandlingPolicy,
    Secret,
    SecretStore,
    classify_data,
    get_handling_policy,
)

# Lookup name for the provider key inside the store. A name, not a value.
PROVIDER_KEY_NAME = "gemini_api_key"  # pragma: allowlist secret

# Placeholder used by --dry-run so the redaction demo has something to hold.
# Deliberately credential-shaped, so classify_data() rates it RESTRICTED and
# the demo shows the real tier; deliberately not a working key.
PLACEHOLDER_API_KEY = "sk-dry-run-placeholder-not-a-real-key"  # pragma: allowlist secret

ENV_VAR_NAME = "GEMINI_API_KEY"


class MissingAPIKeyError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing or empty in the environment."""


def get_api_key(env: dict[str, str] | None = None) -> str:
    """Read the provider API key from the environment.

    Args:
        env: Optional mapping to read from instead of ``os.environ``
            (mainly for testing).

    Returns:
        The raw key value. Hand it straight to :func:`store_api_key` and drop
        the reference; do not keep it in a long-lived variable.

    Raises:
        MissingAPIKeyError: If the variable is unset or blank.
    """
    source = env if env is not None else os.environ
    api_key = source.get(ENV_VAR_NAME, "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            f"{ENV_VAR_NAME} is not set. Copy .env.example to .env and add your key, "
            "or run with --dry-run."
        )
    return api_key


def store_api_key(api_key: str, *, store: SecretStore | None = None) -> SecretStore:
    """Wrap the provider key in a :class:`SecretStore`.

    Args:
        api_key: The raw key value.
        store: An existing store to add to. Defaults to a fresh one -- this
            project deliberately does not touch
            :func:`~cockpit.security.data_security.get_default_secret_store`,
            because a process-wide store would make the tests order-dependent.

    Returns:
        The store, with the key filed under :data:`PROVIDER_KEY_NAME`.

    Raises:
        ValueError: If ``api_key`` is empty.
    """
    if not api_key:
        raise ValueError("api_key must not be empty.")
    target = store if store is not None else SecretStore()
    target.put(PROVIDER_KEY_NAME, api_key)
    return target


def get_secret(store: SecretStore) -> Secret:
    """Fetch the provider key, still wrapped.

    Args:
        store: The store to read from.

    Returns:
        The :class:`Secret`. Call ``.reveal()`` on it only where the value is
        actually needed, i.e. when constructing the SDK client.

    Raises:
        KeyError: If no provider key has been stored.
    """
    return store.get(PROVIDER_KEY_NAME)


def classify_credential(api_key: str) -> DataClassification:
    """Classify a raw key value with the framework's classifier.

    Args:
        api_key: The raw key value.

    Returns:
        The inferred :class:`DataClassification`. A credential-shaped value
        resolves to ``RESTRICTED``.

    Raises:
        TypeError: If ``api_key`` is not a string.
    """
    return classify_data(api_key)


def credential_handling_policy(api_key: str) -> HandlingPolicy:
    """Look up what may be done with a value of this sensitivity.

    Args:
        api_key: The raw key value.

    Returns:
        The :class:`HandlingPolicy` for its tier. For a real key that policy
        has ``allow_in_logs=False`` and ``allow_in_prompts=False`` -- which is
        the machine-readable version of "do not print this".
    """
    return get_handling_policy(classify_credential(api_key))


def redaction_proof(store: SecretStore) -> list[str]:
    """Render the secret through every path that normally leaks a value.

    Each line exercises a different disclosure route: ``repr`` (logging's
    ``%r`` and traceback dumps), ``str``, f-string interpolation, an explicit
    format spec that tries to slice the value back out, and the store's own
    ``repr``. None of them yields the key.

    Args:
        store: The store holding the provider key.

    Returns:
        One rendered line per path, safe to print.

    Raises:
        KeyError: If no provider key has been stored.
    """
    secret = get_secret(store)
    return [
        f"repr(secret)        -> {secret!r}",
        f"str(secret)         -> {secret!s}",
        f"f-string            -> {secret}",
        f"sliced f-string     -> {format(secret, '.5')}",
        f"repr(store)         -> {store!r}",
    ]
