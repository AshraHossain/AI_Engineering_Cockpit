"""Data-at-rest protection: password hashing, encryption, secrets, classification.

Four concerns, deliberately kept in one module because they share the same
threat model -- "the value must survive being stored, logged, or crash-dumped
without becoming a disclosure":

1. **Password hashing** -- :func:`hash_password` / :func:`verify_password`,
   built on ``hashlib.scrypt`` (memory-hard, stdlib, no third-party KDF to
   audit). Hashes are stored in a self-describing string so the cost
   parameters can be raised later without invalidating existing hashes.
2. **Symmetric encryption** -- ``cryptography.fernet.Fernet``, which is
   AES-128-CBC with an HMAC-SHA256 authentication tag. Authenticated: a
   tampered ciphertext is *rejected*, not silently decrypted to garbage.
3. **Secret handling** -- :class:`Secret` keeps its value encrypted in
   memory and redacts itself in ``repr``/``str``/f-strings/pickle, so a
   credential cannot reach a log line or a traceback by accident.
4. **Data classification** -- :func:`classify_data` and the
   :data:`HANDLING_POLICIES` table, which say what may be done with a value
   at each sensitivity tier.

No cryptography is invented here. Every primitive is a standard, reviewed
implementation from the stdlib or from ``cryptography``; this module only
composes them and fixes the parameters.

**Failure direction.** The top-level field entry points are gated on
``feature_flags.is_enabled("security")``, but unlike
:func:`cockpit.security.input_security.validate_input` they do *not*
degrade to a no-op. A disabled encrypt call that returned plaintext would
turn a feature flag into a data breach, so they fail closed by raising
:class:`SecurityDisabledError`, and :func:`classify_data` returns the most
restrictive tier. The pure crypto helpers below are never flag-gated: a
caller that already holds a key is doing explicit, safe work.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass
from enum import StrEnum

from cryptography.fernet import Fernet, InvalidToken

from cockpit.config.feature_flags import is_enabled
from cockpit.security.output_security import scan_for_pii
from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class DataSecurityError(Exception):
    """Base class for every failure raised by this module."""


class InvalidKeyError(DataSecurityError, ValueError):
    """Raised when a supplied key is not a well-formed Fernet key."""


class EncryptionError(DataSecurityError):
    """Raised when a value cannot be encrypted."""


class DecryptionError(DataSecurityError):
    """Raised when a ciphertext cannot be authenticated and decrypted.

    This is the important one. ``Fernet`` reports a wrong key and a tampered
    ciphertext identically (``InvalidToken``), and both mean the same thing
    to a caller: *do not trust this data*. Callers must be able to catch that
    condition explicitly rather than have a third-party exception type leak
    through, so it is translated here.
    """


class SecurityDisabledError(DataSecurityError, RuntimeError):
    """Raised when a protective entry point is called with security disabled.

    Fails closed on purpose -- see the module docstring.
    """


# --------------------------------------------------------------------------
# Data classification
# --------------------------------------------------------------------------


class DataClassification(StrEnum):
    """Sensitivity tiers used to decide handling/storage policy.

    Attributes:
        PUBLIC: No handling restrictions.
        INTERNAL: Internal-only, low sensitivity.
        CONFIDENTIAL: Restricted; requires encryption at rest.
        RESTRICTED: Highest sensitivity (e.g. regulated PII or credentials);
            requires encryption at rest and in transit plus access auditing.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class HandlingPolicy:
    """What a given sensitivity tier permits.

    Attributes:
        classification: The tier this policy applies to.
        encrypt_at_rest: Value must be encrypted before being persisted.
        encrypt_in_transit: Value must only cross a network over TLS.
        audit_access: Every read must produce an audit record.
        allow_in_logs: Value may appear verbatim in application logs.
        allow_in_prompts: Value may be sent to a third-party model provider.
            This is the tier's most consequential field on an AI platform --
            a prompt is an exfiltration channel that leaves the trust
            boundary.
        max_retention_days: Retention ceiling, or ``None`` for unlimited.
    """

    classification: DataClassification
    encrypt_at_rest: bool
    encrypt_in_transit: bool
    audit_access: bool
    allow_in_logs: bool
    allow_in_prompts: bool
    max_retention_days: int | None


# The policy matrix is data, not branching logic, so a reviewer can read the
# whole rule set at once and a change is a one-line diff.
HANDLING_POLICIES: dict[DataClassification, HandlingPolicy] = {
    DataClassification.PUBLIC: HandlingPolicy(
        classification=DataClassification.PUBLIC,
        encrypt_at_rest=False,
        encrypt_in_transit=False,
        audit_access=False,
        allow_in_logs=True,
        allow_in_prompts=True,
        max_retention_days=None,
    ),
    DataClassification.INTERNAL: HandlingPolicy(
        classification=DataClassification.INTERNAL,
        encrypt_at_rest=False,
        encrypt_in_transit=True,
        audit_access=False,
        allow_in_logs=True,
        allow_in_prompts=True,
        max_retention_days=730,
    ),
    DataClassification.CONFIDENTIAL: HandlingPolicy(
        classification=DataClassification.CONFIDENTIAL,
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        audit_access=True,
        allow_in_logs=False,
        allow_in_prompts=False,
        max_retention_days=365,
    ),
    DataClassification.RESTRICTED: HandlingPolicy(
        classification=DataClassification.RESTRICTED,
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        audit_access=True,
        allow_in_logs=False,
        allow_in_prompts=False,
        max_retention_days=90,
    ),
}

# Credential shapes that make a value RESTRICTED regardless of PII content.
# Narrow on purpose: a false "this is a live credential" is cheap to handle,
# a false "this is ordinary text" is a leak.
_CREDENTIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
    "assigned_credential": re.compile(
        r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
}

# PII categories from output_security, mapped to the tier they force.
_PII_CATEGORY_TIERS: dict[str, DataClassification] = {
    "ssn": DataClassification.RESTRICTED,
    "credit_card": DataClassification.RESTRICTED,
    "email": DataClassification.CONFIDENTIAL,
    "phone": DataClassification.CONFIDENTIAL,
    "ip_address": DataClassification.CONFIDENTIAL,
}

_TIER_ORDER: dict[DataClassification, int] = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.RESTRICTED: 3,
}


def get_handling_policy(classification: DataClassification | str) -> HandlingPolicy:
    """Look up the handling rules for a sensitivity tier.

    Args:
        classification: A :class:`DataClassification`, or its string value.

    Returns:
        The :class:`HandlingPolicy` for that tier. An unrecognized tier
        resolves to the ``RESTRICTED`` policy rather than raising, because a
        typo in a caller's config must not quietly relax handling rules.
    """
    try:
        tier = DataClassification(classification)
    except ValueError:
        _logger.warning(
            "Unknown data classification %r; applying the RESTRICTED policy.", classification
        )
        tier = DataClassification.RESTRICTED
    return HANDLING_POLICIES[tier]


def classify_data(value: str) -> DataClassification:
    """Infer a data classification tier for a given value.

    Heuristic, and deliberately biased upward: it returns the *highest* tier
    any signal in the value justifies, and it never infers ``PUBLIC``.
    Declaring something public is an assertion a human makes, not something
    a regex can conclude from the absence of evidence.

    Args:
        value: The raw value to classify (e.g. a field about to be stored
            or logged).

    Returns:
        The inferred :class:`DataClassification`. ``INTERNAL`` is the floor
        for any value with no sensitive signal in it.

    Raises:
        TypeError: If ``value`` is not a string.
    """
    if not isinstance(value, str):
        raise TypeError(f"value must be a str, got {type(value).__name__}.")

    if not is_enabled("security"):
        # Fail closed: with the framework off there is nothing doing the
        # classifying, so callers must assume the worst rather than be told
        # a sensitive value is ordinary.
        _logger.debug("Security framework disabled; classifying as RESTRICTED by default.")
        return DataClassification.RESTRICTED

    tier = DataClassification.INTERNAL

    for name, pattern in _CREDENTIAL_PATTERNS.items():
        if pattern.search(value):
            _logger.warning("Credential-shaped content detected (%s); classified RESTRICTED.", name)
            return DataClassification.RESTRICTED

    for finding in scan_for_pii(value).findings:
        candidate = _PII_CATEGORY_TIERS.get(finding.category, DataClassification.CONFIDENTIAL)
        if _TIER_ORDER[candidate] > _TIER_ORDER[tier]:
            tier = candidate

    return tier


# --------------------------------------------------------------------------
# Password hashing (scrypt)
# --------------------------------------------------------------------------

# scrypt cost parameters. n is the CPU/memory cost (must be a power of two),
# r the block size, p the parallelisation factor. n=2**14 with r=8 needs
# 128 * n * r = 16 MiB per hash, which is the point: it prices GPU/ASIC
# cracking far above what a plain SHA family hash costs an attacker.
_KDF_NAME = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

# OpenSSL refuses to allocate past maxmem; leave headroom over 128*n*r so a
# future parameter bump does not fail with an opaque error.
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * _SCRYPT_R * 2

# Stored form: "scrypt$n=16384,r=8,p=1,dklen=32$<salt_b64>$<hash_b64>".
# Self-describing on purpose -- the parameters travel with the hash, so they
# can be raised for new passwords while existing hashes still verify.
_FIELD_SEPARATOR = "$"
_STORED_FIELD_COUNT = 4


def _derive_scrypt(password: bytes, salt: bytes, *, n: int, r: int, p: int, dklen: int) -> bytes:
    """Run scrypt with an explicit memory ceiling.

    Args:
        password: The password bytes.
        salt: The per-password salt.
        n: CPU/memory cost parameter (a power of two).
        r: Block-size parameter.
        p: Parallelisation parameter.
        dklen: Derived-key length in bytes.

    Returns:
        The derived key.

    Raises:
        ValueError: If the parameters are invalid or exceed the memory
            ceiling (propagated from ``hashlib.scrypt``).
    """
    return hashlib.scrypt(
        password,
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=max(_SCRYPT_MAXMEM, 128 * n * r * 2),
    )


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Hash a password for storage using scrypt.

    Args:
        password: The plaintext password. Must be a non-empty string.
        salt: Optional explicit salt, for tests and for re-deriving a known
            hash. Production callers should leave this ``None`` so a fresh
            cryptographically random salt is generated per password -- that
            is what makes two identical passwords hash to different stored
            values and defeats precomputed (rainbow) tables.

    Returns:
        A self-describing stored hash string of the form
        ``"scrypt$n=...,r=...,p=...,dklen=...$<salt_b64>$<hash_b64>"``.

    Raises:
        TypeError: If ``password`` is not a string.
        ValueError: If ``password`` is empty or ``salt`` is too short.
    """
    if not isinstance(password, str):
        raise TypeError(f"password must be a str, got {type(password).__name__}.")
    if not password:
        raise ValueError("password must not be empty.")

    if salt is None:
        salt = secrets.token_bytes(_SALT_BYTES)
    elif len(salt) < 8:
        raise ValueError("salt must be at least 8 bytes.")

    digest = _derive_scrypt(
        password.encode("utf-8"),
        salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    params = f"n={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P},dklen={_SCRYPT_DKLEN}"
    return _FIELD_SEPARATOR.join(
        (
            _KDF_NAME,
            params,
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def _parse_stored_hash(stored: str) -> tuple[dict[str, int], bytes, bytes] | None:
    """Parse a stored hash string into its parameters, salt, and digest.

    Args:
        stored: The stored hash string produced by :func:`hash_password`.

    Returns:
        A ``(params, salt, digest)`` triple, or ``None`` if the string is
        not a well-formed hash of a supported algorithm. Returning ``None``
        rather than raising keeps a corrupted database row from turning a
        failed login into a 500.
    """
    # mypy calls this unreachable because the annotation says str. Annotations
    # are not enforced at runtime and this reads whatever a datastore handed
    # back, so the guard stays: a corrupted row should fail verification, not
    # raise AttributeError somewhere further down.
    if not isinstance(stored, str):
        return None  # type: ignore[unreachable]

    parts = stored.split(_FIELD_SEPARATOR)
    if len(parts) != _STORED_FIELD_COUNT:
        return None

    algorithm, param_text, salt_b64, digest_b64 = parts
    if algorithm != _KDF_NAME:
        return None

    params: dict[str, int] = {}
    for item in param_text.split(","):
        name, separator, raw = item.partition("=")
        if not separator or not raw.lstrip("-").isdigit():
            return None
        params[name] = int(raw)

    if not {"n", "r", "p", "dklen"} <= params.keys():
        return None
    if params["n"] < 2 or params["r"] < 1 or params["p"] < 1 or params["dklen"] < 16:
        return None

    try:
        salt = base64.b64decode(salt_b64, validate=True)
        digest = base64.b64decode(digest_b64, validate=True)
    except (binascii.Error, ValueError):
        return None

    if not salt or len(digest) != params["dklen"]:
        return None
    return params, salt, digest


def verify_password(password: str, stored: str) -> bool:
    """Check a plaintext password against a stored scrypt hash.

    Args:
        password: The plaintext password supplied by the caller.
        stored: The stored hash string from :func:`hash_password`.

    Returns:
        True if the password matches. A malformed, truncated, or
        unknown-algorithm ``stored`` value returns False -- an
        unauthenticated caller must never be able to turn a corrupt record
        into an exception trace.
    """
    if not isinstance(password, str) or not password:
        return False

    parsed = _parse_stored_hash(stored)
    if parsed is None:
        _logger.warning("Rejecting a malformed or unsupported stored password hash.")
        return False

    params, salt, expected = parsed
    try:
        candidate = _derive_scrypt(
            password.encode("utf-8"),
            salt,
            n=params["n"],
            r=params["r"],
            p=params["p"],
            dklen=params["dklen"],
        )
    except ValueError:
        # Parameters parsed but scrypt rejected them (e.g. n not a power of
        # two, or a cost that exceeds the memory ceiling).
        _logger.warning("Stored password hash carries scrypt parameters that cannot be replayed.")
        return False

    # hmac.compare_digest, never ==. Python's bytes comparison short-circuits
    # on the first differing byte, so the time it takes leaks how many
    # leading bytes an attacker guessed correctly -- enough to reconstruct a
    # digest byte by byte. compare_digest runs in time independent of where
    # the mismatch is. This module exists to prevent exactly this class of
    # bug, so it does not get to commit it.
    return hmac.compare_digest(candidate, expected)


def needs_rehash(stored: str) -> bool:
    """Report whether a stored hash was made with weaker-than-current params.

    Lets a login path transparently upgrade old hashes: verify with the
    stored parameters, then re-hash with the current ones.

    Args:
        stored: The stored hash string to inspect.

    Returns:
        True if the hash is malformed, uses another algorithm, or was
        derived with cost parameters below the current defaults.
    """
    parsed = _parse_stored_hash(stored)
    if parsed is None:
        return True
    params, _salt, _digest = parsed
    return (
        params["n"] < _SCRYPT_N
        or params["r"] < _SCRYPT_R
        or params["p"] < _SCRYPT_P
        or params["dklen"] < _SCRYPT_DKLEN
    )


# --------------------------------------------------------------------------
# Symmetric encryption (Fernet)
# --------------------------------------------------------------------------

# Fernet is AES-128-CBC for confidentiality plus HMAC-SHA256 for integrity,
# over a 32-byte key split into two 16-byte halves. Recorded here so the
# stored metadata does not claim something the implementation is not.
ENCRYPTION_ALGORITHM = "fernet-aes128-cbc-hmac-sha256"

_FERNET_KEY_BYTES = 32


def generate_key() -> bytes:
    """Generate a fresh random Fernet key.

    Returns:
        A 32-byte key encoded as url-safe base64 (44 ASCII bytes), the form
        ``Fernet`` expects.
    """
    return Fernet.generate_key()


def derive_key_from_passphrase(
    passphrase: str,
    salt: bytes,
    *,
    n: int = _SCRYPT_N,
    r: int = _SCRYPT_R,
    p: int = _SCRYPT_P,
) -> bytes:
    """Derive a Fernet key from a human-chosen passphrase.

    A passphrase is not a key: it is low-entropy and the wrong length. This
    stretches it with scrypt and encodes the result the way ``Fernet``
    requires. The salt is *not* secret, but it must be stored alongside the
    ciphertext and must differ per passphrase, or two users who picked the
    same passphrase share a key.

    Args:
        passphrase: The passphrase to stretch. Must be non-empty.
        salt: A per-passphrase salt of at least 16 bytes. Generate with
            ``secrets.token_bytes(16)`` and persist it.
        n: scrypt CPU/memory cost (a power of two).
        r: scrypt block-size parameter.
        p: scrypt parallelisation parameter.

    Returns:
        A url-safe base64 key accepted by :func:`encrypt_bytes` and friends.

    Raises:
        TypeError: If ``passphrase`` is not a string.
        ValueError: If the passphrase is empty or the salt is under 16 bytes.
    """
    if not isinstance(passphrase, str):
        raise TypeError(f"passphrase must be a str, got {type(passphrase).__name__}.")
    if not passphrase:
        raise ValueError("passphrase must not be empty.")
    if len(salt) < _SALT_BYTES:
        raise ValueError(f"salt must be at least {_SALT_BYTES} bytes.")

    derived = _derive_scrypt(
        passphrase.encode("utf-8"), salt, n=n, r=r, p=p, dklen=_FERNET_KEY_BYTES
    )
    return base64.urlsafe_b64encode(derived)


def _build_fernet(key: bytes | str) -> Fernet:
    """Construct a ``Fernet`` instance, translating key-format failures.

    Args:
        key: A url-safe base64 key of 32 decoded bytes.

    Returns:
        A configured ``Fernet``.

    Raises:
        InvalidKeyError: If the key is not a well-formed Fernet key.
    """
    try:
        return Fernet(key)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise InvalidKeyError(
            "Key is not a valid Fernet key (expected 32 url-safe base64-encoded bytes)."
        ) from exc


def encrypt_bytes(data: bytes, key: bytes | str) -> bytes:
    """Encrypt bytes with authenticated symmetric encryption.

    Args:
        data: The plaintext bytes.
        key: A Fernet key from :func:`generate_key` or
            :func:`derive_key_from_passphrase`.

    Returns:
        The Fernet token (ciphertext plus IV, timestamp, and HMAC tag).

    Raises:
        TypeError: If ``data`` is not ``bytes``.
        InvalidKeyError: If ``key`` is not a valid Fernet key.
        EncryptionError: If the underlying primitive refuses the input.
    """
    if not isinstance(data, bytes | bytearray):
        raise TypeError(f"data must be bytes, got {type(data).__name__}.")
    fernet = _build_fernet(key)
    try:
        return fernet.encrypt(bytes(data))
    except (ValueError, TypeError) as exc:  # pragma: no cover -- defensive
        raise EncryptionError("Failed to encrypt the supplied value.") from exc


def decrypt_bytes(token: bytes | str, key: bytes | str) -> bytes:
    """Decrypt and authenticate a Fernet token.

    Args:
        token: The token returned by :func:`encrypt_bytes`.
        key: The key the token was encrypted with.

    Returns:
        The recovered plaintext bytes.

    Raises:
        InvalidKeyError: If ``key`` is not a valid Fernet key.
        DecryptionError: If the token fails authentication. That means the
            ciphertext was tampered with, truncated, or encrypted under a
            different key -- indistinguishable to the recipient, and all
            three mean the same thing: reject the data.
    """
    fernet = _build_fernet(key)
    try:
        return fernet.decrypt(token if isinstance(token, bytes) else str(token).encode("utf-8"))
    except InvalidToken as exc:
        # Deliberately not echoing the token or the key into the message.
        raise DecryptionError(
            "Ciphertext failed authentication: wrong key or the data was modified."
        ) from exc
    except (TypeError, ValueError) as exc:
        raise DecryptionError("Ciphertext is not a well-formed token.") from exc


def encrypt_text(plaintext: str, key: bytes | str) -> str:
    """Encrypt a string, returning an ASCII-safe token.

    Args:
        plaintext: The string to encrypt.
        key: A Fernet key.

    Returns:
        The token as an ASCII string, safe to store in a text column.

    Raises:
        TypeError: If ``plaintext`` is not a string.
        InvalidKeyError: If ``key`` is not a valid Fernet key.
    """
    if not isinstance(plaintext, str):
        raise TypeError(f"plaintext must be a str, got {type(plaintext).__name__}.")
    return encrypt_bytes(plaintext.encode("utf-8"), key).decode("ascii")


def decrypt_text(token: bytes | str, key: bytes | str) -> str:
    """Decrypt a token produced by :func:`encrypt_text`.

    Args:
        token: The token to decrypt.
        key: The key the token was encrypted with.

    Returns:
        The recovered string.

    Raises:
        InvalidKeyError: If ``key`` is not a valid Fernet key.
        DecryptionError: If the token fails authentication or the plaintext
            is not valid UTF-8.
    """
    raw = decrypt_bytes(token, key)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecryptionError(
            "Decrypted value is not valid UTF-8; use decrypt_bytes for binary data."
        ) from exc


# --------------------------------------------------------------------------
# Field-level encryption
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EncryptedPayload:
    """Container for an encrypted value and the metadata needed to decrypt it.

    Attributes:
        ciphertext: The encrypted bytes (a Fernet token).
        key_id: Identifier of the key used, for key-rotation lookups. Only
            an identifier -- never the key material itself.
        algorithm: Name of the encryption algorithm used.
        nonce: Algorithm-specific nonce/IV, if applicable. Always ``None``
            for Fernet, which carries its own IV inside the token; the field
            stays so a future AEAD with an external nonce fits the same
            container.
        classification: Sensitivity tier the plaintext was classified at,
            carried alongside so a downstream store can apply the right
            retention and audit policy without decrypting.
    """

    ciphertext: bytes
    key_id: str
    algorithm: str
    nonce: bytes | None = None
    classification: DataClassification | None = None


def encrypt_field(
    plaintext: str,
    classification: DataClassification,
    key: bytes | str,
    *,
    key_id: str = "default",
) -> EncryptedPayload:
    """Encrypt a single field value for storage.

    Args:
        plaintext: The raw value to encrypt.
        classification: Sensitivity tier, recorded on the payload so storage
            layers can apply the matching :class:`HandlingPolicy`.
        key: The Fernet key to encrypt under.
        key_id: Identifier of that key, for rotation lookups.

    Returns:
        An :class:`EncryptedPayload` containing the ciphertext and
        decryption metadata.

    Raises:
        TypeError: If ``plaintext`` is not a string.
        InvalidKeyError: If ``key`` is not a valid Fernet key.
        SecurityDisabledError: If the security framework is disabled. This
            entry point fails closed rather than returning plaintext.
    """
    if not is_enabled("security"):
        raise SecurityDisabledError(
            "Security framework is disabled; refusing to store a field unencrypted."
        )
    if not isinstance(plaintext, str):
        raise TypeError(f"plaintext must be a str, got {type(plaintext).__name__}.")

    return EncryptedPayload(
        ciphertext=encrypt_bytes(plaintext.encode("utf-8"), key),
        key_id=key_id,
        algorithm=ENCRYPTION_ALGORITHM,
        nonce=None,
        classification=DataClassification(classification),
    )


def decrypt_field(payload: EncryptedPayload, key: bytes | str) -> str:
    """Decrypt a field previously encrypted with :func:`encrypt_field`.

    Args:
        payload: The encrypted payload to decrypt.
        key: The Fernet key identified by ``payload.key_id``.

    Returns:
        The recovered plaintext value.

    Raises:
        InvalidKeyError: If ``key`` is not a valid Fernet key.
        DecryptionError: If the payload fails authentication, or was written
            by an algorithm this module does not implement.
        SecurityDisabledError: If the security framework is disabled.
    """
    if not is_enabled("security"):
        raise SecurityDisabledError(
            "Security framework is disabled; cannot decrypt protected fields."
        )
    if payload.algorithm != ENCRYPTION_ALGORITHM:
        raise DecryptionError(
            f"Payload was written with unsupported algorithm {payload.algorithm!r}."
        )
    return decrypt_text(payload.ciphertext, key)


def rotate_key(
    payloads: list[EncryptedPayload],
    old_key: bytes | str,
    new_key: bytes | str,
    *,
    new_key_id: str,
) -> list[EncryptedPayload]:
    """Re-encrypt payloads from an old key to a new one.

    Rotation is all-or-nothing here: if any payload fails to decrypt the
    whole batch is abandoned, because a half-rotated set of records whose
    ``key_id`` no longer says which key applies is worse than not rotating.

    Args:
        payloads: The payloads currently encrypted under ``old_key``.
        old_key: The key being retired.
        new_key: The replacement key.
        new_key_id: Identifier to stamp on the re-encrypted payloads.

    Returns:
        A new list of :class:`EncryptedPayload` objects under ``new_key``,
        in the same order as the input.

    Raises:
        InvalidKeyError: If either key is malformed.
        DecryptionError: If any payload fails to decrypt under ``old_key``.
        SecurityDisabledError: If the security framework is disabled.
    """
    if not is_enabled("security"):
        raise SecurityDisabledError("Security framework is disabled; refusing to rotate keys.")

    rotated: list[EncryptedPayload] = []
    for index, payload in enumerate(payloads):
        try:
            plaintext = decrypt_text(payload.ciphertext, old_key)
        except DecryptionError as exc:
            raise DecryptionError(
                f"Key rotation aborted: payload {index} (key_id={payload.key_id!r}) "
                "could not be decrypted with the old key."
            ) from exc
        rotated.append(
            EncryptedPayload(
                ciphertext=encrypt_bytes(plaintext.encode("utf-8"), new_key),
                key_id=new_key_id,
                algorithm=ENCRYPTION_ALGORITHM,
                nonce=None,
                classification=payload.classification,
            )
        )
    _logger.info("Rotated %d payload(s) onto key_id=%r.", len(rotated), new_key_id)
    return rotated


# --------------------------------------------------------------------------
# Secret handling
# --------------------------------------------------------------------------

REDACTION_PLACEHOLDER = "***REDACTED***"


class Secret:
    """A value that refuses to render itself.

    The plaintext is held encrypted and is reachable only through
    :meth:`reveal`. Every path by which a Python value normally reaches a
    log line, a traceback, or a serialized artifact is overridden to emit
    :data:`REDACTION_PLACEHOLDER` instead:

    * ``repr()`` -- what ``logging`` prints for ``%r``, and what appears in
      the local-variable dump of a rich traceback.
    * ``str()`` and ``format()`` -- ``f"{secret}"`` and ``"%s" % secret``.
    * pickling -- blocked outright, so a secret cannot ride along inside a
      cached or queued object.

    In-memory encryption is defence against *accidental* disclosure -- core
    dumps, heap snapshots, an ``inspect`` walk of an object graph. It is not
    defence against an attacker who already executes code in this process:
    the key lives in the same object. The redaction is the part that carries
    real weight, because accidental disclosure through a log is how
    credentials actually leak.
    """

    __slots__ = ("_key", "_label", "_token")

    def __init__(self, value: str | bytes, *, label: str = "unnamed") -> None:
        """Wrap a sensitive value.

        Args:
            value: The plaintext to protect, as text or raw bytes.
            label: A non-sensitive name used in ``repr`` for debugging, e.g.
                ``"openai_api_key"``. Never include the value here.

        Raises:
            TypeError: If ``value`` is neither ``str`` nor ``bytes``.
        """
        if isinstance(value, str):
            raw = value.encode("utf-8")
        elif isinstance(value, bytes | bytearray):
            raw = bytes(value)
        else:
            raise TypeError(f"value must be str or bytes, got {type(value).__name__}.")

        self._label = label
        self._key = Fernet.generate_key()
        self._token = Fernet(self._key).encrypt(raw)

    @property
    def label(self) -> str:
        """Non-sensitive identifier for this secret.

        Returns:
            The label supplied at construction.
        """
        return self._label

    def reveal(self) -> str:
        """Return the plaintext as text.

        The name is the point: a reader of the calling code can see exactly
        where a secret leaves its wrapper, and that line can be grepped for
        in review.

        Returns:
            The decrypted plaintext.

        Raises:
            DecryptionError: If the wrapped value is not valid UTF-8, or the
                internal state was corrupted.
        """
        raw = self.reveal_bytes()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DecryptionError(
                "Secret holds non-UTF-8 data; use reveal_bytes() instead."
            ) from exc

    def reveal_bytes(self) -> bytes:
        """Return the plaintext as raw bytes.

        Returns:
            The decrypted plaintext bytes.

        Raises:
            DecryptionError: If the internal ciphertext fails authentication.
        """
        try:
            return Fernet(self._key).decrypt(self._token)
        except InvalidToken as exc:  # pragma: no cover -- internal corruption only
            raise DecryptionError("Secret state failed authentication.") from exc

    def __repr__(self) -> str:
        """Return a redacted representation safe for logs and tracebacks.

        Returns:
            A string containing the label and the redaction placeholder only.
        """
        return f"Secret(label={self._label!r}, value={REDACTION_PLACEHOLDER})"

    def __str__(self) -> str:
        """Return the redacted representation.

        Returns:
            The same redacted string as :meth:`__repr__`.
        """
        return self.__repr__()

    def __format__(self, format_spec: str) -> str:
        """Return the redacted representation for any format spec.

        The format spec is ignored on purpose: honouring it would let
        ``f"{secret:.5}"`` slice the plaintext back out.

        Args:
            format_spec: Ignored.

        Returns:
            The redacted string.
        """
        return self.__repr__()

    def __eq__(self, other: object) -> bool:
        """Compare two secrets in constant time.

        Args:
            other: The object to compare against.

        Returns:
            True if ``other`` is a :class:`Secret` wrapping the same bytes.
            Comparison against any other type is False -- never against the
            raw plaintext, since that would be a disclosure oracle.
        """
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self.reveal_bytes(), other.reveal_bytes())

    # Defining __eq__ leaves this class unhashable, which is intentional: a
    # secret must not become a dict key or land in a set, where its digest
    # would outlive the object and its membership tests would leak timing.
    __hash__ = None  # type: ignore[assignment]

    def __getstate__(self) -> object:
        """Block pickling.

        Returns:
            Never returns.

        Raises:
            TypeError: Always. Serializing a secret would write the key and
                the ciphertext side by side into whatever cache, queue, or
                on-disk artifact the pickle lands in.
        """
        raise TypeError("Secret objects cannot be pickled or copied by serialization.")


class SecretStore:
    """A thread-safe, self-redacting registry of named secrets.

    Values are stored as :class:`Secret` objects, so nothing in the store
    renders plaintext, and reading one requires an explicit
    :meth:`reveal`/:meth:`get` plus ``.reveal()`` call.

    Thread-safe because config and credentials are commonly loaded once and
    read from worker threads.
    """

    def __init__(self) -> None:
        """Create an empty store."""
        self._lock = threading.Lock()
        self._entries: dict[str, Secret] = {}

    def put(self, name: str, value: str | bytes | Secret) -> Secret:
        """Store (or replace) a secret under a name.

        Args:
            name: Non-sensitive lookup name, e.g. ``"openai_api_key"``.
            value: The plaintext to protect, or an existing :class:`Secret`.

        Returns:
            The stored :class:`Secret`.

        Raises:
            TypeError: If ``name`` is not a string, or ``value`` has an
                unsupported type.
            ValueError: If ``name`` is empty.
        """
        if not isinstance(name, str):
            raise TypeError(f"name must be a str, got {type(name).__name__}.")
        if not name:
            raise ValueError("name must not be empty.")

        entry = value if isinstance(value, Secret) else Secret(value, label=name)
        with self._lock:
            self._entries[name] = entry
        _logger.debug("Stored secret under name %r (value not logged).", name)
        return entry

    def get(self, name: str) -> Secret:
        """Fetch a stored secret, still wrapped.

        Args:
            name: The name it was stored under.

        Returns:
            The :class:`Secret`. Call ``.reveal()`` on it to read the value.

        Raises:
            KeyError: If no secret is stored under ``name``.
        """
        with self._lock:
            try:
                return self._entries[name]
            except KeyError as exc:
                raise KeyError(f"No secret stored under {name!r}.") from exc

    def reveal(self, name: str) -> str:
        """Fetch and decrypt a stored secret in one step.

        Args:
            name: The name it was stored under.

        Returns:
            The plaintext value.

        Raises:
            KeyError: If no secret is stored under ``name``.
            DecryptionError: If the value is not valid UTF-8.
        """
        return self.get(name).reveal()

    def delete(self, name: str) -> bool:
        """Remove a secret from the store.

        Args:
            name: The name it was stored under.

        Returns:
            True if a secret was removed, False if the name was absent.
        """
        with self._lock:
            return self._entries.pop(name, None) is not None

    def names(self) -> list[str]:
        """List the names of every stored secret.

        Returns:
            Sorted names. Names are treated as non-sensitive; values are
            never exposed by this method.
        """
        with self._lock:
            return sorted(self._entries)

    def clear(self) -> None:
        """Drop every stored secret."""
        with self._lock:
            self._entries.clear()

    def __contains__(self, name: object) -> bool:
        """Check whether a name is present.

        Args:
            name: The name to look for.

        Returns:
            True if a secret is stored under that name.
        """
        with self._lock:
            return name in self._entries

    def __len__(self) -> int:
        """Count stored secrets.

        Returns:
            The number of stored secrets.
        """
        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        """Return a representation listing names only, never values.

        Returns:
            A redacted summary string.
        """
        return f"SecretStore(names={self.names()!r}, values={REDACTION_PLACEHOLDER})"


_default_store = SecretStore()


def get_default_secret_store() -> SecretStore:
    """Return the process-wide default :class:`SecretStore`.

    Returns:
        The shared store. Tests should prefer constructing their own
        :class:`SecretStore` over mutating this one.
    """
    return _default_store
