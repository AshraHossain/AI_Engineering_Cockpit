"""
CLI tool for managing secrets in AI Engineering Cockpit.

Usage:
    python -m cockpit.config.secrets_cli encrypt      # Encrypt .env to .env.gpg
    python -m cockpit.config.secrets_cli verify       # Check required secrets
    python -m cockpit.config.secrets_cli show         # Show loaded secrets (masked)
    python -m cockpit.config.secrets_cli init         # Interactive setup
"""

import sys
import argparse
from pathlib import Path
from typing import Optional
import getpass
import logging

from cockpit.security.secrets_manager import SecretsManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def cmd_encrypt(args) -> int:
    """Encrypt .env file with GPG."""
    manager = SecretsManager()

    if not manager.env_path or not manager.env_path.exists():
        logger.error(f"No .env file found at {manager.env_path}")
        return 1

    logger.info(f"Encrypting {manager.env_path} to {manager.env_path.with_suffix('.env.gpg')}")
    logger.info("You will be prompted for a GPG passphrase...")

    if manager.encrypt_env_file():
        logger.info("✓ Encryption successful")
        logger.warning("Next: Delete the plaintext .env and update your workflow to use .env.gpg")
        return 0
    else:
        logger.error("✗ Encryption failed")
        return 1


def cmd_verify(args) -> int:
    """Verify required secrets are loaded."""
    manager = SecretsManager()

    required_keys = [
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ]

    # At least one API key is required
    found_any = any(manager.get(key) for key in required_keys)

    if not found_any:
        logger.error("✗ No API keys found. Set one of:")
        for key in required_keys:
            logger.error(f"    {key}")
        logger.error("\nEdit .env or set environment variables")
        return 1

    logger.info("✓ API keys loaded successfully")

    # Show which ones are set (masked)
    for key in required_keys:
        if manager.get(key):
            masked = mask_secret(manager.get(key))
            logger.info(f"  {key}: {masked}")

    return 0


def cmd_show(args) -> int:
    """Show loaded secrets (masked for safety)."""
    manager = SecretsManager()
    manager.load()

    secrets = manager.get_all()

    if not secrets:
        logger.info("No secrets loaded")
        return 0

    logger.info("Loaded secrets (masked for safety):")
    for key in sorted(secrets.keys()):
        if key.upper() in ["GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
            value = mask_secret(secrets[key])
            logger.info(f"  {key}: {value}")
        else:
            logger.info(f"  {key}: {secrets[key]}")

    return 0


def cmd_init(args) -> int:
    """Interactive setup of secrets."""
    logger.info("AI Engineering Cockpit - Secrets Setup")
    logger.info("=" * 40)
    logger.info("Enter your API keys below. Leave blank to skip.")
    logger.info("Secrets will be saved to .env (plaintext for now)")
    logger.info()

    env_path = Path(".env")
    secrets = {}

    # Collect API keys
    gemini_key = getpass.getpass("GEMINI_API_KEY (https://ai.google.dev): ").strip()
    if gemini_key:
        secrets["GEMINI_API_KEY"] = gemini_key

    openai_key = getpass.getpass("OPENAI_API_KEY (https://platform.openai.com): ").strip()
    if openai_key:
        secrets["OPENAI_API_KEY"] = openai_key

    anthropic_key = getpass.getpass("ANTHROPIC_API_KEY (https://console.anthropic.com): ").strip()
    if anthropic_key:
        secrets["ANTHROPIC_API_KEY"] = anthropic_key

    # Collect optional config
    ollama_host = input("OLLAMA_HOST [http://localhost:11434]: ").strip()
    if ollama_host:
        secrets["OLLAMA_HOST"] = ollama_host

    use_case = input("COCKPIT_USE_CASE [enterprise]: ").strip()
    if use_case:
        secrets["COCKPIT_USE_CASE"] = use_case

    log_level = input("LOG_LEVEL [INFO]: ").strip()
    if log_level:
        secrets["LOG_LEVEL"] = log_level

    if not secrets:
        logger.info("No secrets entered. Exiting.")
        return 1

    # Write to .env
    try:
        with open(env_path, "w") as f:
            for key, value in secrets.items():
                f.write(f"{key}={value}\n")
        logger.info(f"✓ Saved {len(secrets)} secrets to {env_path}")

        # Offer to encrypt
        encrypt_now = input("\nEncrypt .env with GPG? (y/n) [n]: ").strip().lower() == "y"
        if encrypt_now:
            manager = SecretsManager(env_path)
            if manager.encrypt_env_file():
                logger.info("✓ Encryption successful")
                logger.warning("You can now delete the plaintext .env file")
            else:
                logger.warning("Encryption failed, but .env is saved (plaintext)")
        return 0
    except Exception as e:
        logger.error(f"✗ Failed to write .env: {e}")
        return 1


def mask_secret(value: str, show_chars: int = 4) -> str:
    """Mask a secret for display (show first/last N chars)."""
    if not value or len(value) <= show_chars * 2 + 2:
        return "***"
    return f"{value[:show_chars]}***{value[-show_chars:]}"


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Manage secrets for AI Engineering Cockpit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m cockpit.config.secrets_cli init       # Interactive setup
  python -m cockpit.config.secrets_cli verify     # Check secrets are loaded
  python -m cockpit.config.secrets_cli show       # Show loaded secrets (masked)
  python -m cockpit.config.secrets_cli encrypt    # Encrypt .env with GPG

For production: Use encrypted .env.gpg or external secrets manager.
See docs/SECRETS.md for detailed guidance.
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("init", help="Interactive secrets setup")
    subparsers.add_parser("encrypt", help="Encrypt .env file with GPG")
    subparsers.add_parser("verify", help="Verify required secrets are loaded")
    subparsers.add_parser("show", help="Show loaded secrets (masked)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "init": cmd_init,
        "encrypt": cmd_encrypt,
        "verify": cmd_verify,
        "show": cmd_show,
    }

    handler = commands.get(args.command)
    if not handler:
        logger.error(f"Unknown command: {args.command}")
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
