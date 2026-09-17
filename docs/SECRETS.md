# Secrets Management

Secure, production-ready secrets handling for AI Engineering Cockpit.

---

## Quick Start

### Development (Single Machine)

```bash
# Interactive setup
python -m cockpit.config.secrets_cli init

# Verify secrets are loaded
python -m cockpit.config.secrets_cli verify

# Encrypt your .env file (recommended)
python -m cockpit.config.secrets_cli encrypt
```

### Production (Team/Shared Environments)

```bash
# Use encrypted .env.gpg + GPG passphrase
# OR integrate with your secrets manager (see below)
```

---

## How It Works

The `SecretsManager` loads secrets from multiple sources with fallback:

1. **Environment variables** (highest priority)
   ```bash
   export GEMINI_API_KEY="sk-..."  # pragma: allowlist secret
   python src/main.py
   ```

2. **Encrypted .env.gpg** (requires GPG)
   ```bash
   python -m cockpit.config.secrets_cli encrypt
   # Your .env is now encrypted; plaintext deleted
   ```

3. **Plaintext .env** (development only)
   ```bash
   # Automatically loaded if .env.gpg not available
   # WARNING: Never commit .env to git
   ```

4. **External vaults** (extensible)
   - 1Password CLI (framework in place, not yet wired)
   - AWS Secrets Manager, HashiCorp Vault, etc. (add your own)

**All APIs can access secrets the same way:**
```python
from cockpit.security.secrets_manager import get_secret

api_key = get_secret("GEMINI_API_KEY")
```

---

## Using Secrets in Your Projects

### Method 1: Automatic (Settings)

The `cockpit/config/settings.py` loads all secrets automatically:

```python
from cockpit.config.settings import get_settings

settings = get_settings()
print(settings.gemini_api_key)  # Loaded from secrets manager
```

**All example projects use this.**

### Method 2: Direct SecretsManager

For custom secret names or logic:

```python
from cockpit.security.secrets_manager import get_secrets_manager

manager = get_secrets_manager()
my_secret = manager.get("MY_CUSTOM_KEY")

# Verify required secrets before running
if not manager.verify(["GEMINI_API_KEY", "MY_CUSTOM_KEY"]):
    raise RuntimeError("Missing required secrets")
```

### Method 3: Environment Variables

Secrets manager checks env vars first:

```bash
export GEMINI_API_KEY="sk-..."  # pragma: allowlist secret
python src/main.py
```

This works on any platform and is useful for CI/CD:

```yaml
# GitHub Actions
env:
  GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

---

## Encryption: .env.gpg

### Setup

**Prerequisite:** GPG installed
```bash
# Ubuntu/Debian:
sudo apt-get install gnupg

# Mac:
brew install gnupg

# Windows:
# Download from https://gnupg.org/
```

### Encrypt Your .env

```bash
python -m cockpit.config.secrets_cli encrypt
# Prompts for GPG passphrase (remember this!)
# Creates .env.gpg (encrypted)
# Delete the plaintext .env afterward
```

### Decrypt at Runtime

Automatic. The `SecretsManager` decrypts .env.gpg when needed:

```python
from cockpit.security.secrets_manager import get_secrets_manager
manager = get_secrets_manager()
# Prompts for passphrase once per process
api_key = manager.get("GEMINI_API_KEY")
```

### Team Setup

For a team, **git-track .env.gpg, not .env**:

```bash
# .gitignore
.env
!.env.gpg         # Track encrypted version only
```

Team members:
1. Clone the repo (get `.env.gpg`)
2. Run: `python -m cockpit.config.secrets_cli init`
3. Enter their own API keys
4. Encrypt: `python -m cockpit.config.secrets_cli encrypt`
5. Commit their `.env.gpg` to a private branch or shared vault

---

## Using External Secrets Managers

### AWS Secrets Manager

```python
import boto3
from cockpit.security.secrets_manager import SecretsManager

class AWSSecretsManager(SecretsManager):
    def _load_aws(self) -> bool:
        """Load secrets from AWS Secrets Manager."""
        try:
            client = boto3.client('secretsmanager')
            response = client.get_secret_value(SecretId='cockpit/prod')
            secret = json.loads(response['SecretString'])
            self._cache.update(secret)
            return True
        except Exception as e:
            logger.warning(f"AWS load failed: {e}")
            return False

# In your app
manager = AWSSecretsManager()
manager.load()
```

### HashiCorp Vault

```python
import hvac
from cockpit.security.secrets_manager import SecretsManager

class VaultSecretsManager(SecretsManager):
    def _load_vault(self) -> bool:
        """Load secrets from HashiCorp Vault."""
        try:
            client = hvac.Client(url=os.getenv('VAULT_ADDR'))
            client.auth.approle.login(
                role_id=os.getenv('VAULT_ROLE_ID'),
                secret_id=os.getenv('VAULT_SECRET_ID'),
            )
            secret = client.secrets.kv.v2.read_secret_version(path='cockpit')
            self._cache.update(secret['data']['data'])
            return True
        except Exception as e:
            logger.warning(f"Vault load failed: {e}")
            return False

# In your app
manager = VaultSecretsManager()
manager.load()
```

### 1Password CLI

```python
# Framework is in place; here's how to wire it up:
from cockpit.security.secrets_manager import SecretsManager

class OnePasswordSecretsManager(SecretsManager):
    def _load_1password(self) -> bool:
        """Load secrets from 1Password CLI."""
        try:
            # Requires: op account add, op signin
            result = subprocess.run(
                ["op", "item", "get", "cockpit", "--format", "json"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for field in data.get('fields', []):
                    if field['label'].isupper():
                        self._cache[field['label']] = field['value']
                return True
        except Exception as e:
            logger.warning(f"1Password load failed: {e}")
            return False
```

---

## CLI Reference

### `init` — Interactive Setup

```bash
python -m cockpit.config.secrets_cli init
```

Prompts for:
- `GEMINI_API_KEY` (optional)
- `OPENAI_API_KEY` (optional)
- `ANTHROPIC_API_KEY` (optional)
- `OLLAMA_HOST` (optional, defaults to localhost:11434)
- `COCKPIT_USE_CASE` (optional, defaults to enterprise)
- `LOG_LEVEL` (optional, defaults to INFO)

Saves to `.env` (plaintext). Optionally encrypts to `.env.gpg`.

### `encrypt` — Encrypt .env with GPG

```bash
python -m cockpit.config.secrets_cli encrypt
```

Requires GPG installed. Prompts for passphrase.
Creates `.env.gpg`, keeps plaintext `.env` (you delete manually).

### `verify` — Check Secrets Are Loaded

```bash
python -m cockpit.config.secrets_cli verify
```

Checks for at least one of:
- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

Exit code 0 if found, 1 if missing.

### `show` — Display Loaded Secrets (Masked)

```bash
python -m cockpit.config.secrets_cli show
```

Shows all loaded secrets with first/last 4 chars visible, middle masked:
```
GEMINI_API_KEY: sk-X***YZAB
OPENAI_API_KEY: sk-A***CDEF
OLLAMA_HOST: http://localhost:11434
```

---

## Best Practices

### Development

1. **Use .env for local work:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Encrypt before committing:**
   ```bash
   python -m cockpit.config.secrets_cli encrypt
   # Now only .env.gpg is on disk (or delete .env)
   ```

3. **Never commit plaintext .env:**
   ```bash
   # .gitignore already has this, but check:
   git status  # Should not show .env
   ```

### Team/Staging

1. **Use encrypted .env.gpg:**
   ```bash
   # Each team member has their own .env.gpg
   # Encrypted with their own GPG key or team key
   ```

2. **Or use env vars:**
   ```bash
   # CI/CD systems (GitHub Actions, GitLab CI, etc.)
   # Set secrets as encrypted env vars in the platform
   ```

3. **Document the flow:**
   - How do new team members add their API keys?
   - Who has access to the secrets vault?
   - How often are secrets rotated?

### Production

1. **Use external secrets manager:**
   - AWS Secrets Manager
   - HashiCorp Vault
   - Azure Key Vault
   - Google Secret Manager

2. **Never store secrets in code or .env files:**
   ```python
   # BAD
   GEMINI_API_KEY = "sk-..."  # Don't do this  # pragma: allowlist secret
   
   # GOOD
   from cockpit.security.secrets_manager import get_secret
   api_key = get_secret("GEMINI_API_KEY")  # Loaded from vault
   ```

3. **Rotate secrets regularly:**
   - Set up automated key rotation with your secrets manager
   - Monitor audit logs for unauthorized access

4. **Use environment variables for secrets:**
   ```bash
   # Docker
   docker run -e GEMINI_API_KEY=$KEY app
   
   # Kubernetes
   kubectl create secret generic cockpit-secrets --from-literal=GEMINI_API_KEY=$KEY
   
   # Systemd
   EnvironmentFile=/etc/cockpit/secrets
   ```

---

## Troubleshooting

### "GPG not found"

```bash
# Ubuntu/Debian:
sudo apt-get install gnupg

# Mac:
brew install gnupg

# Windows:
# Download: https://gnupg.org/download/
```

### "Permission denied" on .env.gpg

```bash
# Make sure you own the file
ls -la .env.gpg
chmod 600 .env.gpg  # Owner can read/write, others cannot
```

### "Passphrase required but no terminal"

When running in CI/CD or non-interactive:
- Use env vars instead of `.env.gpg`
- Or provide passphrase via `--passphrase-fd` (advanced)
- Or use a keyring-backed key (systemd, macOS Keychain)

### "Secrets not loading"

```bash
# Debug: Check what SecretsManager sees
python -c "from cockpit.security.secrets_manager import get_secrets_manager; m = get_secrets_manager(); m.load(); print(m.get_all())"

# Verify CLI
python -m cockpit.config.secrets_cli verify
python -m cockpit.config.secrets_cli show
```

### "Different secrets on each machine"

This is expected and good. Each machine/team member:
1. Has their own `.env` (dev) or `.env.gpg` (encrypted)
2. Their own API keys
3. Git ignore prevents accidental commits

---

## Examples

### Run a project with secrets

```bash
cd projects/01-hello-world
python -m cockpit.config.secrets_cli init  # Set up secrets
uv sync
uv run python src/main.py
```

### Verify secrets before running

```python
from cockpit.security.secrets_manager import get_secrets_manager

manager = get_secrets_manager()
if not manager.verify(["GEMINI_API_KEY"]):
    print("ERROR: GEMINI_API_KEY not found")
    sys.exit(1)

# Safe to proceed
from cockpit.config.settings import get_settings
settings = get_settings()
client = genai.Client(api_key=settings.gemini_api_key)
```

### Automated rotation (production)

```python
# Example: Rotate secrets weekly
from datetime import datetime, timedelta
from pathlib import Path

last_rotation_file = Path("/etc/cockpit/.last_rotation")
if last_rotation_file.exists():
    last_rotation = datetime.fromtimestamp(last_rotation_file.stat().st_mtime)
    if datetime.now() - last_rotation > timedelta(days=7):
        # Fetch new secrets from vault
        # Update .env or environment
        last_rotation_file.touch()
```

---

## See Also

- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — System design
- [cockpit/security/](../cockpit/security/) — Security framework
- [cockpit/config/settings.py](../cockpit/config/settings.py) — Settings loader
- [DEPLOYMENT.md](DEPLOYMENT.md) — Production deployment checklist
