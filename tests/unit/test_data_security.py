"""Unit tests for cockpit/security/data_security.py.

Behavioral tests for real cryptographic code: the password hash must
actually verify, the salt must actually be random, authenticated encryption
must actually reject tampering, and a Secret must actually stay out of a log
line. Each of these is a property an attacker probes directly, so each gets
an assertion rather than a smoke test.
"""

from __future__ import annotations

import base64
import pickle

import pytest

from cockpit.security.data_security import (
    ENCRYPTION_ALGORITHM,
    HANDLING_POLICIES,
    REDACTION_PLACEHOLDER,
    DataClassification,
    DecryptionError,
    EncryptedPayload,
    InvalidKeyError,
    Secret,
    SecretStore,
    SecurityDisabledError,
    classify_data,
    decrypt_bytes,
    decrypt_field,
    decrypt_text,
    derive_key_from_passphrase,
    encrypt_bytes,
    encrypt_field,
    encrypt_text,
    generate_key,
    get_default_secret_store,
    get_handling_policy,
    hash_password,
    needs_rehash,
    rotate_key,
    verify_password,
)

PLAINTEXT_PASSWORD = "correct horse battery staple"


class TestPasswordHashing:
    """Tests for hash_password / verify_password / needs_rehash."""

    def test_correct_password_verifies(self) -> None:
        stored = hash_password(PLAINTEXT_PASSWORD)
        assert verify_password(PLAINTEXT_PASSWORD, stored) is True

    def test_wrong_password_fails(self) -> None:
        stored = hash_password(PLAINTEXT_PASSWORD)
        assert verify_password("correct horse battery stapl", stored) is False
        assert verify_password("", stored) is False

    def test_same_password_hashes_to_different_stored_values(self) -> None:
        # Random per-password salt: identical passwords must not collide, or
        # a single cracked hash cracks every account that reused it.
        first = hash_password(PLAINTEXT_PASSWORD)
        second = hash_password(PLAINTEXT_PASSWORD)
        assert first != second
        assert verify_password(PLAINTEXT_PASSWORD, first) is True
        assert verify_password(PLAINTEXT_PASSWORD, second) is True

    def test_stored_format_is_self_describing(self) -> None:
        stored = hash_password(PLAINTEXT_PASSWORD)
        algorithm, params, salt_b64, digest_b64 = stored.split("$")
        assert algorithm == "scrypt"
        assert "n=" in params and "r=" in params and "p=" in params and "dklen=" in params
        # Both trailing fields must be real base64 of the advertised lengths.
        assert len(base64.b64decode(salt_b64, validate=True)) == 16
        assert len(base64.b64decode(digest_b64, validate=True)) == 32

    def test_explicit_salt_is_reproducible(self) -> None:
        salt = b"0123456789abcdef"
        assert hash_password(PLAINTEXT_PASSWORD, salt=salt) == hash_password(
            PLAINTEXT_PASSWORD, salt=salt
        )

    @pytest.mark.parametrize(
        "stored",
        [
            "",
            "not-a-hash",
            "scrypt$n=16384,r=8,p=1,dklen=32$onlythreefields",
            "bcrypt$n=16384,r=8,p=1,dklen=32$c2FsdA==$aGFzaA==",
            "scrypt$n=abc,r=8,p=1,dklen=32$c2FsdA==$aGFzaA==",
            "scrypt$r=8,p=1,dklen=32$c2FsdA==$aGFzaA==",
            "scrypt$n=16384,r=8,p=1,dklen=32$!!!notbase64!!!$aGFzaA==",
            "scrypt$n=1,r=8,p=1,dklen=32$c2FsdA==$aGFzaA==",
        ],
    )
    def test_malformed_stored_hash_returns_false_without_crashing(self, stored: str) -> None:
        assert verify_password(PLAINTEXT_PASSWORD, stored) is False

    def test_tampered_digest_fails(self) -> None:
        stored = hash_password(PLAINTEXT_PASSWORD)
        algorithm, params, salt_b64, digest_b64 = stored.split("$")
        raw = bytearray(base64.b64decode(digest_b64))
        raw[0] ^= 0xFF
        tampered = "$".join(
            (algorithm, params, salt_b64, base64.b64encode(bytes(raw)).decode("ascii"))
        )
        assert verify_password(PLAINTEXT_PASSWORD, tampered) is False

    def test_tampered_salt_fails(self) -> None:
        stored = hash_password(PLAINTEXT_PASSWORD)
        algorithm, params, _salt_b64, digest_b64 = stored.split("$")
        other_salt = base64.b64encode(b"fedcba9876543210").decode("ascii")
        assert (
            verify_password(
                PLAINTEXT_PASSWORD, "$".join((algorithm, params, other_salt, digest_b64))
            )
            is False
        )

    def test_non_string_password_rejected(self) -> None:
        with pytest.raises(TypeError):
            hash_password(12345)  # type: ignore[arg-type]

    def test_empty_password_rejected(self) -> None:
        with pytest.raises(ValueError):
            hash_password("")

    def test_short_salt_rejected(self) -> None:
        with pytest.raises(ValueError):
            hash_password(PLAINTEXT_PASSWORD, salt=b"abc")

    def test_needs_rehash_false_for_current_params(self) -> None:
        assert needs_rehash(hash_password(PLAINTEXT_PASSWORD)) is False

    def test_needs_rehash_true_for_weak_or_broken_hash(self) -> None:
        weak = "scrypt$n=1024,r=8,p=1,dklen=32$" + "$".join(
            (base64.b64encode(b"0123456789abcdef").decode(), base64.b64encode(b"x" * 32).decode())
        )
        assert needs_rehash(weak) is True
        assert needs_rehash("garbage") is True


class TestSymmetricEncryption:
    """Tests for the Fernet-backed encrypt/decrypt helpers."""

    def test_bytes_roundtrip(self) -> None:
        key = generate_key()
        data = b"\x00\x01binary payload\xff"
        assert decrypt_bytes(encrypt_bytes(data, key), key) == data

    def test_text_roundtrip(self) -> None:
        key = generate_key()
        text = "sensitive value with unicode: café"
        token = encrypt_text(text, key)
        assert isinstance(token, str)
        assert text not in token
        assert decrypt_text(token, key) == text

    def test_ciphertext_differs_across_calls(self) -> None:
        key = generate_key()
        assert encrypt_text("same", key) != encrypt_text("same", key)

    def test_wrong_key_raises_decryption_error(self) -> None:
        token = encrypt_text("secret value", generate_key())
        with pytest.raises(DecryptionError):
            decrypt_text(token, generate_key())

    def test_tampered_ciphertext_is_rejected(self) -> None:
        # The entire point of an authenticated construction: a modified
        # token must fail rather than decrypt to attacker-chosen garbage.
        key = generate_key()
        token = bytearray(encrypt_bytes(b"transfer 100 to alice", key))
        token[-1] ^= 0x01
        with pytest.raises(DecryptionError):
            decrypt_bytes(bytes(token), key)

    def test_truncated_ciphertext_is_rejected(self) -> None:
        key = generate_key()
        token = encrypt_bytes(b"payload", key)
        with pytest.raises(DecryptionError):
            decrypt_bytes(token[:-10], key)

    def test_malformed_key_raises_invalid_key_error(self) -> None:
        with pytest.raises(InvalidKeyError):
            encrypt_text("value", b"too-short")
        with pytest.raises(InvalidKeyError):
            decrypt_text("anything", "not-a-key")

    def test_non_bytes_payload_rejected(self) -> None:
        with pytest.raises(TypeError):
            encrypt_bytes("a string", generate_key())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            encrypt_text(b"bytes", generate_key())  # type: ignore[arg-type]

    def test_non_utf8_plaintext_surfaces_as_decryption_error(self) -> None:
        key = generate_key()
        token = encrypt_bytes(b"\xff\xfe not utf-8", key)
        with pytest.raises(DecryptionError):
            decrypt_text(token, key)


class TestKeyDerivation:
    """Tests for derive_key_from_passphrase."""

    def test_derived_key_is_usable_and_deterministic(self) -> None:
        salt = b"0123456789abcdef"
        key = derive_key_from_passphrase("a decent passphrase", salt)
        assert derive_key_from_passphrase("a decent passphrase", salt) == key
        assert decrypt_text(encrypt_text("hello", key), key) == "hello"

    def test_different_salt_gives_different_key(self) -> None:
        first = derive_key_from_passphrase("same phrase", b"0123456789abcdef")
        second = derive_key_from_passphrase("same phrase", b"fedcba9876543210")
        assert first != second

    def test_derived_key_has_fernet_shape(self) -> None:
        key = derive_key_from_passphrase("phrase", b"0123456789abcdef")
        assert len(base64.urlsafe_b64decode(key)) == 32

    def test_short_salt_and_empty_passphrase_rejected(self) -> None:
        with pytest.raises(ValueError):
            derive_key_from_passphrase("phrase", b"short")
        with pytest.raises(ValueError):
            derive_key_from_passphrase("", b"0123456789abcdef")


class TestFieldEncryption:
    """Tests for encrypt_field / decrypt_field / rotate_key."""

    def test_field_roundtrip_preserves_classification(self) -> None:
        key = generate_key()
        payload = encrypt_field("123-45-6789", DataClassification.RESTRICTED, key, key_id="k1")
        assert payload.key_id == "k1"
        assert payload.algorithm == ENCRYPTION_ALGORITHM
        assert payload.nonce is None
        assert payload.classification is DataClassification.RESTRICTED
        assert b"123-45-6789" not in payload.ciphertext
        assert decrypt_field(payload, key) == "123-45-6789"

    def test_field_decrypt_with_wrong_key_raises(self) -> None:
        payload = encrypt_field("value", DataClassification.CONFIDENTIAL, generate_key())
        with pytest.raises(DecryptionError):
            decrypt_field(payload, generate_key())

    def test_unsupported_algorithm_is_refused(self) -> None:
        payload = EncryptedPayload(ciphertext=b"xyz", key_id="k1", algorithm="rot13")
        with pytest.raises(DecryptionError):
            decrypt_field(payload, generate_key())

    def test_non_string_plaintext_rejected(self) -> None:
        with pytest.raises(TypeError):
            encrypt_field(b"bytes", DataClassification.INTERNAL, generate_key())  # type: ignore[arg-type]

    def test_rotate_key_reencrypts_under_new_key(self) -> None:
        old_key, new_key = generate_key(), generate_key()
        payloads = [
            encrypt_field(value, DataClassification.CONFIDENTIAL, old_key, key_id="old")
            for value in ("alpha", "beta")
        ]
        rotated = rotate_key(payloads, old_key, new_key, new_key_id="new")

        assert [p.key_id for p in rotated] == ["new", "new"]
        assert [decrypt_field(p, new_key) for p in rotated] == ["alpha", "beta"]
        for payload in rotated:
            with pytest.raises(DecryptionError):
                decrypt_field(payload, old_key)

    def test_rotate_key_aborts_when_a_payload_does_not_decrypt(self) -> None:
        old_key, new_key = generate_key(), generate_key()
        good = encrypt_field("alpha", DataClassification.INTERNAL, old_key)
        foreign = encrypt_field("beta", DataClassification.INTERNAL, generate_key())
        with pytest.raises(DecryptionError):
            rotate_key([good, foreign], old_key, new_key, new_key_id="new")

    @pytest.mark.parametrize("entry_point", ["encrypt", "decrypt", "rotate"])
    def test_entry_points_fail_closed_when_security_disabled(
        self, entry_point: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unlike validate_input, these must NOT degrade to a no-op: returning
        # plaintext because a flag is off would be the leak itself.
        key = generate_key()
        payload = encrypt_field("value", DataClassification.INTERNAL, key)
        monkeypatch.setattr("cockpit.security.data_security.is_enabled", lambda _f: False)

        with pytest.raises(SecurityDisabledError):
            if entry_point == "encrypt":
                encrypt_field("value", DataClassification.INTERNAL, key)
            elif entry_point == "decrypt":
                decrypt_field(payload, key)
            else:
                rotate_key([payload], key, generate_key(), new_key_id="new")


class TestSecretRedaction:
    """Tests that a Secret cannot reach a log line, traceback, or pickle."""

    VALUE = "sk-live-DO-NOT-LOG-THIS-VALUE"

    def test_reveal_returns_the_value(self) -> None:
        assert Secret(self.VALUE, label="api_key").reveal() == self.VALUE

    def test_bytes_value_roundtrips(self) -> None:
        assert Secret(b"\x00\xffraw").reveal_bytes() == b"\x00\xffraw"

    @pytest.mark.parametrize(
        "render",
        [
            repr,
            str,
            lambda s: f"{s}",
            lambda s: f"{s!s}",
            lambda s: f"{s!r}",
            lambda s: f"{s:>40}",
            lambda s: "{}".format(s),  # noqa: UP032 -- exercising str.format explicitly
            lambda s: "%s" % (s,),  # noqa: UP031 -- percent format is the path under test
            lambda s: "%r" % (s,),  # noqa: UP031 -- percent format is the path under test
        ],
    )
    def test_no_rendering_path_leaks_the_plaintext(self, render) -> None:
        rendered = render(Secret(self.VALUE, label="api_key"))
        assert self.VALUE not in rendered
        assert REDACTION_PLACEHOLDER in rendered
        assert "api_key" in rendered

    def test_format_spec_cannot_slice_the_value_out(self) -> None:
        # f"{secret:.5}" must not return the first five plaintext characters.
        assert self.VALUE[:5] not in f"{Secret(self.VALUE):.5}"

    def test_container_repr_does_not_leak(self) -> None:
        # Logging a dict of config is the realistic accident.
        assert self.VALUE not in repr({"api_key": Secret(self.VALUE, label="api_key")})

    def test_logging_the_secret_does_not_leak(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        caplog.set_level(logging.INFO)
        logging.getLogger("test_secret").info("key=%s value=%r", Secret(self.VALUE), Secret("x"))
        assert self.VALUE not in caplog.text

    def test_pickling_is_blocked(self) -> None:
        with pytest.raises(TypeError):
            pickle.dumps(Secret(self.VALUE))

    def test_equality_is_by_value_and_only_against_secrets(self) -> None:
        assert Secret("abc") == Secret("abc")
        assert Secret("abc") != Secret("abd")
        assert Secret("abc") != "abc"

    def test_secret_is_unhashable(self) -> None:
        with pytest.raises(TypeError):
            {Secret("abc")}

    def test_non_utf8_reveal_raises(self) -> None:
        with pytest.raises(DecryptionError):
            Secret(b"\xff\xfe").reveal()

    def test_unsupported_value_type_rejected(self) -> None:
        with pytest.raises(TypeError):
            Secret(1234)  # type: ignore[arg-type]


class TestSecretStore:
    """Tests for SecretStore."""

    def test_put_get_reveal_roundtrip(self) -> None:
        store = SecretStore()
        store.put("api_key", "value-123")
        assert isinstance(store.get("api_key"), Secret)
        assert store.reveal("api_key") == "value-123"

    def test_store_repr_lists_names_but_never_values(self) -> None:
        store = SecretStore()
        store.put("api_key", "value-123")
        store.put("db_password", "hunter2")
        rendered = repr(store)
        assert "value-123" not in rendered
        assert "hunter2" not in rendered
        assert "api_key" in rendered
        assert REDACTION_PLACEHOLDER in rendered

    def test_missing_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            SecretStore().get("absent")

    def test_delete_and_membership(self) -> None:
        store = SecretStore()
        store.put("a", "1")
        assert "a" in store
        assert len(store) == 1
        assert store.delete("a") is True
        assert store.delete("a") is False
        assert "a" not in store

    def test_names_are_sorted_and_clear_empties(self) -> None:
        store = SecretStore()
        store.put("zulu", "1")
        store.put("alpha", "2")
        assert store.names() == ["alpha", "zulu"]
        store.clear()
        assert len(store) == 0

    def test_existing_secret_can_be_stored_directly(self) -> None:
        store = SecretStore()
        store.put("token", Secret("abc", label="token"))
        assert store.reveal("token") == "abc"

    def test_invalid_name_rejected(self) -> None:
        store = SecretStore()
        with pytest.raises(ValueError):
            store.put("", "value")
        with pytest.raises(TypeError):
            store.put(1, "value")  # type: ignore[arg-type]

    def test_default_store_is_shared(self) -> None:
        assert get_default_secret_store() is get_default_secret_store()


class TestDataClassification:
    """Tests for classify_data and the handling-policy table."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("SSN on file: 123-45-6789", DataClassification.RESTRICTED),
            ("Card 4111111111111111 charged", DataClassification.RESTRICTED),
            ("-----BEGIN RSA PRIVATE KEY-----", DataClassification.RESTRICTED),
            ("api_key = abcdef123456", DataClassification.RESTRICTED),
            ("AKIAIOSFODNN7EXAMPLE", DataClassification.RESTRICTED),
            ("mail me at jane.doe@example.com", DataClassification.CONFIDENTIAL),
            ("server at 192.168.1.10", DataClassification.CONFIDENTIAL),
            ("The quick brown fox jumps.", DataClassification.INTERNAL),
        ],
    )
    def test_classification_tiers(self, value: str, expected: DataClassification) -> None:
        assert classify_data(value) is expected

    def test_public_is_never_inferred(self) -> None:
        assert classify_data("") is not DataClassification.PUBLIC

    def test_highest_matching_tier_wins(self) -> None:
        mixed = "jane@example.com and SSN 123-45-6789"
        assert classify_data(mixed) is DataClassification.RESTRICTED

    def test_non_string_rejected(self) -> None:
        with pytest.raises(TypeError):
            classify_data(None)  # type: ignore[arg-type]

    def test_classification_fails_closed_when_security_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("cockpit.security.data_security.is_enabled", lambda _f: False)
        assert classify_data("harmless text") is DataClassification.RESTRICTED

    def test_every_tier_has_a_policy(self) -> None:
        assert set(HANDLING_POLICIES) == set(DataClassification)

    def test_sensitive_tiers_forbid_logs_and_prompts(self) -> None:
        for tier in (DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED):
            policy = get_handling_policy(tier)
            assert policy.encrypt_at_rest is True
            assert policy.encrypt_in_transit is True
            assert policy.audit_access is True
            assert policy.allow_in_logs is False
            assert policy.allow_in_prompts is False

    def test_unknown_classification_falls_back_to_restricted(self) -> None:
        policy = get_handling_policy("top-secret-ish")
        assert policy.classification is DataClassification.RESTRICTED

    def test_policy_lookup_accepts_the_string_value(self) -> None:
        assert get_handling_policy("public").classification is DataClassification.PUBLIC
