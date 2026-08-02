"""Data-at-rest and data-in-transit security (Tier 3 — not yet implemented).

Planned scope: encryption of sensitive data fields, key management/rotation,
and data classification tagging. Gated by
``feature_flags.is_enabled("security")`` at the framework level, but every
function here is a typed skeleton pending Tier 3 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DataClassification(StrEnum):
    """Sensitivity tiers used to decide handling/storage policy.

    Attributes:
        PUBLIC: No handling restrictions.
        INTERNAL: Internal-only, low sensitivity.
        CONFIDENTIAL: Restricted; requires encryption at rest.
        RESTRICTED: Highest sensitivity (e.g. regulated PII); requires
            encryption at rest and in transit plus access auditing.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class EncryptedPayload:
    """Container for an encrypted value and the metadata needed to decrypt it.

    Attributes:
        ciphertext: The encrypted bytes.
        key_id: Identifier of the key used, for key-rotation lookups.
        algorithm: Name of the encryption algorithm used (e.g. "AES-256-GCM").
        nonce: Algorithm-specific nonce/IV, if applicable.
    """

    ciphertext: bytes
    key_id: str
    algorithm: str
    nonce: bytes | None = None


def encrypt_field(
    plaintext: str, classification: DataClassification, key_id: str
) -> EncryptedPayload:
    """Encrypt a single field value for storage.

    Args:
        plaintext: The raw value to encrypt.
        classification: Sensitivity tier, which determines the required
            algorithm/key strength policy.
        key_id: Identifier of the encryption key to use.

    Returns:
        An :class:`EncryptedPayload` containing the ciphertext and
        decryption metadata.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def decrypt_field(payload: EncryptedPayload) -> str:
    """Decrypt a field previously encrypted with :func:`encrypt_field`.

    Args:
        payload: The encrypted payload to decrypt.

    Returns:
        The recovered plaintext value.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def rotate_key(old_key_id: str, new_key_id: str) -> int:
    """Re-encrypt all data under an old key with a new key.

    Args:
        old_key_id: Identifier of the key being retired.
        new_key_id: Identifier of the replacement key.

    Returns:
        The number of records re-encrypted.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def classify_data(value: str) -> DataClassification:
    """Infer a data classification tier for a given value.

    Args:
        value: The raw value to classify (e.g. a field about to be stored
            or logged).

    Returns:
        The inferred :class:`DataClassification`.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
