"""
Secrets manager for AI Engineering Cockpit.

Supports multiple backends:
  - Environment variables (ENV)
  - Plaintext .env file (DEV only)
  - Encrypted .env.gpg file (RECOMMENDED)
  - 1Password CLI (if available)
  - External vaults (extensible)

Usage:
    from cockpit.security.secrets_manager import SecretsManager

    secrets = SecretsManager()
    api_key = secrets.get("GEMINI_API_KEY")
    all_secrets = secrets.get_all()
"""

import os
import json
import subprocess
from pathlib import Path
from typing import Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)


class SecretsManager:
    """
    Load secrets from multiple backends with fallback chain.

    Load order (first match wins):
    1. Environment variables
    2. Encrypted .env.gpg (if gpg available)
    3. Plaintext .env (development only)
    4. 1Password CLI (if installed)
    """

    def __init__(self, env_path: Optional[Path] = None):
        """
        Initialize secrets manager.

        Args:
            env_path: Path to .env file. Defaults to repo root or CWD.
        """
        self.env_path = env_path or self._find_env_file()
        self.encrypted_path = self.env_path.with_suffix(".env.gpg") if self.env_path else None
        self._cache: Dict[str, str] = {}
        self._loaded = False

    def _find_env_file(self) -> Optional[Path]:
        """Find .env file in repo root or current directory."""
        # Try common locations
        locations = [
            Path.cwd() / ".env",
            Path.cwd().parent / ".env",
            Path(__file__).parent.parent.parent / ".env",
        ]
        for path in locations:
            if path.exists():
                return path
        return Path.cwd() / ".env"

    def load(self) -> None:
        """Load secrets from first available backend."""
        if self._loaded:
            return

        # Try encrypted .env.gpg first
        if self.encrypted_path and self.encrypted_path.exists():
            if self._load_gpg():
                self._loaded = True
                logger.info(f"Loaded secrets from {self.encrypted_path}")
                return

        # Fall back to plaintext .env
        if self.env_path and self.env_path.exists():
            self._load_env_file()
            self._loaded = True
            logger.info(f"Loaded secrets from {self.env_path} (plaintext)")
            return

        # Fall back to 1Password CLI
        if self._load_1password():
            self._loaded = True
            logger.info("Loaded secrets from 1Password CLI")
            return

        logger.warning("No secrets found. Using environment variables only.")
        self._loaded = True

    def _load_env_file(self) -> None:
        """Load from plaintext .env file (development only)."""
        if not self.env_path or not self.env_path.exists():
            return

        with open(self.env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    self._cache[key.strip()] = value.strip()

    def _load_gpg(self) -> bool:
        """Decrypt .env.gpg and load secrets. Returns True if successful."""
        if not self.encrypted_path or not self.encrypted_path.exists():
            return False

        try:
            result = subprocess.run(
                ["gpg", "--quiet", "--decrypt", str(self.encrypted_path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.warning(f"GPG decryption failed: {result.stderr}")
                return False

            for line in result.stdout.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    self._cache[key.strip()] = value.strip()
            return True
        except FileNotFoundError:
            logger.debug("GPG not available; skipping encrypted .env")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("GPG decryption timed out")
            return False

    def _load_1password(self) -> bool:
        """Load secrets from 1Password CLI. Returns True if successful."""
        try:
            # Check if 1Password CLI is available
            result = subprocess.run(
                ["op", "whoami"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False

            # Try to load from a vault (requires 1Password account)
            # This is extensible: add your 1Password logic here
            logger.debug("1Password CLI detected but not fully wired")
            return False
        except FileNotFoundError:
            return False
        except subprocess.TimeoutExpired:
            return False

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get a secret value.

        Checks (in order):
        1. Environment variables (highest priority)
        2. Loaded secrets from file/vault
        3. Default value
        """
        self.load()

        # Environment variables always win
        if key in os.environ:
            return os.environ[key]

        # Check cache
        return self._cache.get(key, default)

    def get_all(self) -> Dict[str, str]:
        """Get all loaded secrets as dict."""
        self.load()

        result = dict(self._cache)
        # Add environment variables (they override cache)
        for key in self._cache:
            if key in os.environ:
                result[key] = os.environ[key]
        return result

    def encrypt_env_file(self, output_path: Optional[Path] = None) -> bool:
        """
        Encrypt the plaintext .env file to .env.gpg.

        Requires: gpg command-line tool

        Args:
            output_path: Where to save encrypted file. Defaults to .env.gpg

        Returns:
            True if encryption successful
        """
        if not self.env_path or not self.env_path.exists():
            logger.error(f".env file not found at {self.env_path}")
            return False

        output_path = output_path or self.env_path.with_suffix(".env.gpg")

        try:
            result = subprocess.run(
                ["gpg", "--symmetric", "--output", str(output_path), str(self.env_path)],
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"Encrypted .env to {output_path}")
                logger.warning("You can now safely delete the plaintext .env file")
                return True
            else:
                logger.error("GPG encryption failed")
                return False
        except FileNotFoundError:
            logger.error("GPG not found. Install with: apt-get install gnupg (Linux) or brew install gnupg (Mac)")
            return False
        except subprocess.TimeoutExpired:
            logger.error("GPG encryption timed out")
            return False

    def verify(self, required_keys: Optional[list] = None) -> bool:
        """
        Verify that required secrets are loaded.

        Args:
            required_keys: List of keys that must be present (e.g., ["GEMINI_API_KEY"])

        Returns:
            True if all required keys are present
        """
        self.load()

        if not required_keys:
            return True

        missing = []
        for key in required_keys:
            if self.get(key) is None:
                missing.append(key)

        if missing:
            logger.error(f"Missing required secrets: {', '.join(missing)}")
            return False

        return True

    def as_env_dict(self) -> Dict[str, str]:
        """Return secrets as dict suitable for subprocess env parameter."""
        secrets = self.get_all()
        # Merge with current environment (subprocess requires all env vars)
        env = dict(os.environ)
        env.update(secrets)
        return env


# Singleton instance (lazy-loaded)
_instance: Optional[SecretsManager] = None


def get_secrets_manager() -> SecretsManager:
    """Get the global secrets manager instance."""
    global _instance
    if _instance is None:
        _instance = SecretsManager()
    return _instance


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Convenience function to get a secret."""
    return get_secrets_manager().get(key, default)


def verify_secrets(required_keys: list) -> bool:
    """Convenience function to verify required secrets."""
    return get_secrets_manager().verify(required_keys)
