# Deployment Checklist

Complete, step-by-step checklist for deploying AI Engineering Cockpit across Windows, Mac, and Linux.

---

## Pre-Deployment (All Platforms)

- [ ] Clone repository from GitHub
  ```bash
  git clone https://github.com/AshraHossain/AI_Engineering_Cockpit.git
  cd AI_Engineering_Cockpit
  ```

- [ ] Review platform support
  - Windows: Cloud-only (Gemini/OpenAI/Anthropic)
  - Mac (Intel): Hybrid (cloud + CPU Ollama)
  - Mac (Apple Silicon): Hybrid (cloud + GPU Ollama)
  - Linux: Hybrid (cloud + GPU Ollama)

- [ ] Verify prerequisites
  - Git installed: `git --version`
  - For Homebrew/package managers: `brew --version` (Mac), `apt --version` (Linux)
  - No Python install needed (uv handles it)

- [ ] Obtain API keys (at least one)
  - [ ] Gemini: https://ai.google.dev
  - [ ] OpenAI: https://platform.openai.com
  - [ ] Anthropic: https://console.anthropic.com

---

## Windows Deployment

### Setup Phase
- [ ] Run PowerShell setup script
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
  ```

- [ ] Verify uv installation
  ```powershell
  uv --version
  ```

- [ ] Verify Python 3.11+
  ```powershell
  uv run python --version
  ```

### Secrets Phase
- [ ] Initialize secrets (interactive)
  ```powershell
  python -m cockpit.config.secrets_cli init
  ```

- [ ] Verify secrets loaded
  ```powershell
  python -m cockpit.config.secrets_cli verify
  ```

### Testing Phase
- [ ] Run root test suite
  ```powershell
  uv run pytest -c config/pytest.ini --rootdir=. -q
  ```

- [ ] Run first project
  ```powershell
  cd projects/01-hello-world
  cp .env.example .env
  uv sync
  uv run python src/main.py
  ```

### Production Phase (if applicable)
- [ ] Set up CI/CD environment variables
- [ ] Configure provider-side rate limits (Gemini, OpenAI, Anthropic)
- [ ] Set up budget alerts with each provider
- [ ] Document team access to secrets (no hardcoding)

---

## Mac Deployment (Intel & Apple Silicon)

### Setup Phase
- [ ] Run Bash setup script
  ```bash
  bash scripts/setup-mac.sh
  ```

- [ ] Verify uv installation
  ```bash
  uv --version
  ```

- [ ] Verify Python 3.11+
  ```bash
  uv run python --version
  ```

### Ollama Phase (Optional, but Recommended)
- [ ] Check Ollama status
  ```bash
  ollama --version
  # If not found, setup script should have installed it
  ```

- [ ] (Intel iMac only) Pull smaller models for CPU-only
  ```bash
  ollama pull phi
  ollama pull neural-chat
  ```

- [ ] (Apple Silicon only) Pull larger models to leverage GPU
  ```bash
  ollama pull llama2
  ollama pull mistral
  ollama pull neural-chat
  ```

- [ ] Verify Ollama running
  ```bash
  curl http://localhost:11434/api/tags
  ```

### Secrets Phase
- [ ] Initialize secrets (interactive)
  ```bash
  python -m cockpit.config.secrets_cli init
  ```

- [ ] Verify secrets loaded
  ```bash
  python -m cockpit.config.secrets_cli verify
  ```

- [ ] (Recommended) Encrypt secrets with GPG
  ```bash
  python -m cockpit.config.secrets_cli encrypt
  ```

- [ ] Verify encrypted .env.gpg exists
  ```bash
  ls -la .env.gpg
  ```

### Testing Phase
- [ ] Run root test suite
  ```bash
  uv run pytest -c config/pytest.ini --rootdir=. -q
  ```

- [ ] Run first project (hello-world)
  ```bash
  cd projects/01-hello-world
  uv sync
  uv run python src/main.py
  ```

- [ ] Test hybrid mode (if Ollama installed)
  ```bash
  cd projects/05-hybrid-orchestrator
  uv sync
  uv run python src/main.py --dry-run
  ```

### Production Phase (if applicable)
- [ ] Set up encrypted secrets sharing (share .env.gpg, not .env)
- [ ] Configure provider-side rate limits
- [ ] Set up budget alerts with each provider
- [ ] Document Ollama startup (systemd, launchd, manual)
- [ ] Test failover (cloud APIs when Ollama unavailable)

---

## Linux Deployment (Ubuntu/Debian/Fedora/Arch/Alpine)

### Setup Phase
- [ ] Run Bash setup script
  ```bash
  bash scripts/setup-linux.sh
  ```

- [ ] Verify uv installation
  ```bash
  uv --version
  ```

- [ ] Verify Python 3.11+
  ```bash
  uv run python --version
  ```

### Package Manager Detection
- [ ] Script auto-detects and uses: apt, dnf, pacman, or apk
- [ ] Verify your distribution is supported
  - Ubuntu/Debian: `apt --version`
  - Fedora/RHEL: `dnf --version`
  - Arch: `pacman --version`
  - Alpine: `apk --version`

### GPU Setup (Optional, but Recommended for Performance)

#### NVIDIA GPU (CUDA)
- [ ] Install NVIDIA drivers
  ```bash
  # Ubuntu/Debian:
  sudo apt-get install -y nvidia-driver-XXX
  
  # Fedora/RHEL:
  sudo dnf install -y nvidia-driver
  ```

- [ ] Verify NVIDIA drivers
  ```bash
  nvidia-smi
  ```

- [ ] Ollama will auto-detect CUDA (no additional config)

#### AMD GPU (ROCm)
- [ ] Install ROCm runtime
  ```bash
  # Ubuntu/Debian:
  sudo apt-get install -y rocm-hip-runtime rocm-opencl-runtime
  
  # Fedora/RHEL:
  sudo dnf install -y rocm-hip rocm-opencl
  ```

- [ ] Add user to video group
  ```bash
  sudo usermod -a -G video $USER
  sudo usermod -a -G render $USER
  # Log out and back in
  ```

- [ ] Verify ROCm
  ```bash
  rocminfo
  ```

#### Intel GPU
- [ ] Intel GPU support in Ollama is in development
- [ ] Use cloud APIs for now

### Ollama Phase (Optional)
- [ ] Check Ollama status
  ```bash
  ollama --version
  ```

- [ ] Start Ollama service
  ```bash
  sudo systemctl start ollama
  sudo systemctl enable ollama  # Auto-start on boot
  ```

- [ ] Pull models based on your hardware
  ```bash
  ollama pull neural-chat
  # For GPU: ollama pull llama2 mistral orca
  ```

- [ ] Verify Ollama running
  ```bash
  curl http://localhost:11434/api/tags
  ```

### Secrets Phase
- [ ] Initialize secrets (interactive)
  ```bash
  python -m cockpit.config.secrets_cli init
  ```

- [ ] Verify secrets loaded
  ```bash
  python -m cockpit.config.secrets_cli verify
  ```

- [ ] (Recommended) Encrypt secrets with GPG
  ```bash
  # If GPG not installed: sudo apt-get install gnupg (or dnf, pacman, apk)
  python -m cockpit.config.secrets_cli encrypt
  ```

### Testing Phase
- [ ] Run root test suite
  ```bash
  uv run pytest -c config/pytest.ini --rootdir=. -q
  ```

- [ ] Run first project
  ```bash
  cd projects/01-hello-world
  uv sync
  uv run python src/main.py
  ```

- [ ] Test hybrid mode (if GPU available)
  ```bash
  cd projects/05-hybrid-orchestrator
  uv sync
  uv run python src/main.py --dry-run
  ```

### Production Phase (if applicable)
- [ ] Set up systemd service for Ollama (if using)
  ```bash
  sudo systemctl enable ollama
  sudo systemctl status ollama
  ```

- [ ] Configure firewall (if exposing Ollama to network)
  ```bash
  sudo ufw allow 11434/tcp  # (if using ufw)
  ```

- [ ] Set up encrypted secrets (share .env.gpg, not .env)
- [ ] Configure provider-side rate limits
- [ ] Set up budget alerts with each provider
- [ ] Monitor GPU/CPU usage (nvidia-smi, rocm-smi, top)

---

## Multi-Machine Setup (Team/Distributed)

### Initial Machine
- [ ] Complete full deployment on primary machine
- [ ] Set up secrets with encryption
  ```bash
  python -m cockpit.config.secrets_cli encrypt
  ```

- [ ] Commit encrypted .env.gpg to shared repo (or vault)
  ```bash
  git add .env.gpg
  git commit -m "Add encrypted secrets"
  git push
  ```

### Secondary Machines
- [ ] Clone repository
  ```bash
  git clone https://github.com/AshraHossain/AI_Engineering_Cockpit.git
  cd AI_Engineering_Cockpit
  ```

- [ ] Get encrypted secrets from primary machine
  ```bash
  git pull  # Gets .env.gpg
  ```

- [ ] Each team member initializes their own secrets
  ```bash
  python -m cockpit.config.secrets_cli init
  # Enter their own API keys (can be different from primary)
  ```

- [ ] Verify secrets loaded
  ```bash
  python -m cockpit.config.secrets_cli verify
  ```

- [ ] (Optional) Encrypt their own .env
  ```bash
  python -m cockpit.config.secrets_cli encrypt
  ```

---

## Post-Deployment Verification

### All Platforms

- [ ] Verify Python and uv
  ```bash
  uv run python --version
  uv --version
  ```

- [ ] Verify secrets
  ```bash
  python -m cockpit.config.secrets_cli verify
  python -m cockpit.config.secrets_cli show  # Masked output
  ```

- [ ] Run test suite
  ```bash
  bash scripts/run-tests.sh
  # Or: uv run pytest -c config/pytest.ini --rootdir=. -v
  ```

- [ ] Test all example projects
  ```bash
  for dir in projects/0*-*/; do
    echo "Testing $dir"
    cd "$dir"
    uv sync
    cd - > /dev/null
  done
  ```

- [ ] Verify no secrets in logs or output
  ```bash
  # Check recent logs for API keys
  grep -r "sk-" logs/ 2>/dev/null || echo "✓ No API keys in logs"
  ```

### Mac Specific

- [ ] Verify Ollama (if installed)
  ```bash
  ollama --version
  curl http://localhost:11434/api/tags | python3 -m json.tool
  ```

- [ ] Test hybrid routing
  ```bash
  cd projects/05-hybrid-orchestrator
  uv run python src/main.py --dry-run
  ```

### Linux Specific

- [ ] Verify GPU support (if applicable)
  ```bash
  # NVIDIA:
  nvidia-smi
  
  # AMD:
  rocm-smi
  ```

- [ ] Verify Ollama service
  ```bash
  sudo systemctl status ollama
  ```

- [ ] Check resource usage
  ```bash
  free -h  # Memory
  df -h    # Disk
  top -b -n1 | head -20  # CPU
  ```

---

## Troubleshooting During Deployment

| Issue | Solution |
|---|---|
| `uv: command not found` | Add to PATH: `export PATH="$HOME/.local/bin:$PATH"` |
| `Python 3.11 not found` | Run: `uv python install 3.11` |
| `Secrets not loading` | Check: `python -m cockpit.config.secrets_cli verify` |
| `Ollama not found` | Install manually: https://ollama.ai or run setup script again with sudo |
| `GPU not detected` | Install drivers (NVIDIA/AMD) and verify: `nvidia-smi` or `rocm-smi` |
| `Tests fail` | Run: `uv sync --fresh --all-groups` |
| `API key errors` | Run: `python -m cockpit.config.secrets_cli init` |

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for more.

---

## Production Deployment

### Pre-Production
- [ ] All tests passing locally
- [ ] All secrets encrypted (.env.gpg)
- [ ] No hardcoded API keys in code
- [ ] Rate limits configured with providers
- [ ] Budget alerts enabled
- [ ] Audit logging enabled (`LOG_LEVEL=INFO`)

### Infrastructure
- [ ] Container image built and tested (if using Docker/Kubernetes)
- [ ] Environment variables configured (not .env files)
- [ ] Secrets injected from external vault (AWS, Vault, etc.)
- [ ] CI/CD pipeline green (GitHub Actions)
- [ ] Rollback plan documented

### Monitoring
- [ ] Cost tracking enabled (`cockpit/monitoring/`)
- [ ] Error logging configured
- [ ] Performance metrics collected
- [ ] Alerts set up for failures/budget overruns

### Compliance (if needed)
- [ ] Security audit passed
- [ ] GDPR/HIPAA/SOX compliance verified (use `cockpit/security/compliance.py`)
- [ ] Audit trail enabled
- [ ] Data retention policy implemented

---

## Rollback Plan

If deployment fails:

1. **Development Machine (Local)**
   ```bash
   git reset --hard HEAD~1
   uv sync --fresh
   python -m cockpit.config.secrets_cli verify
   ```

2. **Staged Environment**
   ```bash
   git checkout stable-branch
   bash scripts/setup-mac.sh  # or setup-linux.sh
   uv sync --all-groups
   ```

3. **Production**
   ```bash
   # Via CI/CD: Revert commit or redeploy previous version
   git revert HEAD
   git push
   # CI/CD automatically redeploys
   ```

---

## Success Criteria

✅ **Deployment is successful when:**

- [ ] Setup script completes without errors
- [ ] All tests pass (`bash scripts/run-tests.sh`)
- [ ] Secrets load securely (encrypted or env vars)
- [ ] At least one project runs end-to-end
- [ ] No API keys in logs or output
- [ ] Rate limits and budgets configured with providers
- [ ] Documentation reviewed and understood
- [ ] Team has access to encrypted secrets
- [ ] Rollback plan documented and tested

---

## Next Steps After Deployment

1. **Explore frameworks** in `cockpit/` (testing, security, evaluation, etc.)
2. **Run advanced projects** (06-14 show frameworks in action)
3. **Set up CI/CD** with GitHub Actions (see `.github/workflows/`)
4. **Configure monitoring** (cost tracking, performance metrics)
5. **Plan production deployment** following [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

---

## Support & Resources

- **Setup issues?** See [SETUP.md](SETUP.md)
- **Secrets/security?** See [docs/SECRETS.md](docs/SECRETS.md)
- **Platform-specific?** See [docs/LINUX_SETUP.md](docs/LINUX_SETUP.md)
- **Troubleshooting?** See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- **Production deployment?** See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- **Security issues?** See [SECURITY.md](SECURITY.md)
