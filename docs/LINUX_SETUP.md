# Linux Setup & Configuration

Comprehensive guide for deploying AI Engineering Cockpit on Linux distributions.

---

## Supported Distributions

| Distribution | Package Manager | Setup Script | Status |
|---|---|---|---|
| Ubuntu 20.04+ | `apt-get` | ✅ `setup-linux.sh` | Fully tested |
| Debian 11+ | `apt-get` | ✅ `setup-linux.sh` | Fully tested |
| Fedora 36+ | `dnf` | ✅ `setup-linux.sh` | Fully tested |
| RHEL 8+ | `dnf` | ✅ `setup-linux.sh` | Fully tested |
| Arch Linux | `pacman` | ✅ `setup-linux.sh` | Fully tested |
| Alpine 3.16+ | `apk` | ✅ `setup-linux.sh` | Tested |
| Other (Debian-based) | `apt-get` | ✅ `setup-linux.sh` | Should work |
| Other (RPM-based) | `dnf` | ✅ `setup-linux.sh` | Should work |

All distributions can run cloud-only mode (Gemini/OpenAI/Anthropic APIs).
GPU acceleration requires additional setup (see below).

---

## Quick Setup

```bash
git clone https://github.com/AshraHossain/AI_Engineering_Cockpit.git
cd AI_Engineering_Cockpit
bash scripts/setup-linux.sh
```

The script will:
1. Install `uv` (Python package manager)
2. Install Python 3.11 toolchain
3. Detect your package manager (apt, dnf, pacman, apk)
4. Offer to install Ollama (for local models)
5. Sync all dependencies
6. Create `.env` and run tests

---

## Distribution-Specific Notes

### Ubuntu / Debian (apt)

```bash
# If setup-linux.sh needs sudo for Ollama install:
sudo -v  # Prompt for password once
bash scripts/setup-linux.sh
```

**Additional dependencies** (if you run into issues):
```bash
sudo apt-get update
sudo apt-get install -y build-essential curl git python3-dev
```

### Fedora / RHEL (dnf)

```bash
bash scripts/setup-linux.sh
# May prompt for sudo during Ollama install
```

**If Ollama install fails:**
```bash
# Try manual install
curl -fsSL https://ollama.ai/install.sh | sh
# Then start the service
sudo systemctl start ollama
sudo systemctl enable ollama  # Auto-start on boot
```

### Arch Linux (pacman)

```bash
bash scripts/setup-linux.sh
# Requires sudo for pacman (you'll be prompted)
```

**Or install Ollama manually:**
```bash
yay -S ollama  # Or: sudo pacman -S ollama (if in official repos)
systemctl --user start ollama
systemctl --user enable ollama
```

### Alpine (apk)

```bash
bash scripts/setup-linux.sh
```

**Note:** Alpine uses musl libc instead of glibc. Some Python packages may need compilation. If you hit issues:
```bash
apk add build-base python3-dev
bash scripts/setup-linux.sh
```

---

## GPU Acceleration (Optional)

### NVIDIA GPU (CUDA)

1. **Install NVIDIA drivers:**
   ```bash
   # Ubuntu/Debian:
   sudo apt-get install -y nvidia-driver-XXX nvidia-utils
   # (replace XXX with your driver version, e.g., 535)
   
   # Fedora/RHEL:
   sudo dnf install -y gcc-c++ kernel-devel akmod-nvidia
   ```

2. **Verify driver installation:**
   ```bash
   nvidia-smi
   # Should show GPU info, CUDA version, driver version
   ```

3. **Ollama will detect CUDA automatically** once drivers are installed. No additional config needed.

4. **Test local inference:**
   ```bash
   ollama pull llama2
   ollama run llama2 "Write a haiku about programming"
   ```

### AMD GPU (ROCm)

1. **Install ROCm runtime:**
   ```bash
   # Ubuntu/Debian (ROCm 5.7):
   sudo apt-get install -y rocm-hip-runtime rocm-opencl-runtime
   
   # Fedora/RHEL:
   sudo dnf install -y rocm-hip rocm-opencl
   ```

2. **Add user to video group** (required for GPU access):
   ```bash
   sudo usermod -a -G video $USER
   sudo usermod -a -G render $USER
   # Log out and back in for group membership to take effect
   ```

3. **Verify ROCm installation:**
   ```bash
   rocminfo
   # Should show your GPU hardware
   ```

4. **Ollama with ROCm:**
   ```bash
   # Set environment variable before running Ollama
   export HSA_OVERRIDE_GFX_VERSION=gfx906  # Example for Radeon VII
   ollama pull llama2
   ollama run llama2 "Test prompt"
   ```

5. **For automatic ROCm setup**, add to your `~/.bashrc` or `~/.zshrc`:
   ```bash
   export PATH="/opt/rocm/bin:$PATH"
   export LD_LIBRARY_PATH="/opt/rocm/lib:$LD_LIBRARY_PATH"
   export HSA_OVERRIDE_GFX_VERSION=gfx906  # Adjust for your GPU
   ```

### Intel GPU (oneAPI)

Currently, Ollama has limited Intel GPU support. For now:
- Use cloud APIs (Gemini/OpenAI/Anthropic)
- Or use CPU-only Ollama (slower but functional)

Intel Arc support in Ollama is actively being developed; check
[ollama.ai](https://ollama.ai) for latest updates.

---

## Managing Ollama on Linux

### Start/Stop Ollama

**If installed via package manager (systemd):**
```bash
sudo systemctl start ollama      # Start service
sudo systemctl stop ollama       # Stop service
sudo systemctl enable ollama     # Auto-start on boot
sudo systemctl status ollama     # Check status
```

**If installed via curl script (no systemd):**
```bash
ollama serve                     # Start in foreground
# Or, in background:
nohup ollama serve > ollama.log 2>&1 &
```

### Download Models

```bash
# List available models:
ollama list

# Download a model:
ollama pull llama2
ollama pull mistral
ollama pull neural-chat

# Remove a model:
ollama rm llama2
```

### Check Running Models

```bash
curl http://localhost:11434/api/tags | python3 -m json.tool
```

### Performance Monitoring

```bash
# Watch GPU usage (NVIDIA):
watch -n1 nvidia-smi

# Watch CPU/memory (all platforms):
watch -n1 "free -h; echo; top -b -n1 | head -20"
```

---

## Configuration

### Environment Variables

**In your shell** (or in `.env` for the project):
```bash
# Cloud API keys
GEMINI_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here

# Local Ollama
OLLAMA_HOST=http://localhost:11434  # Default

# Logging
LOG_LEVEL=INFO  # or DEBUG, WARNING, ERROR
```

**For persistent environment variables** across sessions, add to `~/.bashrc` or `~/.zshrc`:
```bash
export GEMINI_API_KEY="your-key-here"
export OLLAMA_HOST="http://localhost:11434"
```

### Firewall (if running Ollama on a remote server)

If you're running Ollama on a different Linux machine and want to access it from another system:

```bash
# Expose Ollama on all interfaces (careful: no authentication)
OLLAMA_HOST=0.0.0.0:11434 ollama serve

# In your project's .env:
OLLAMA_HOST=http://remote-machine-ip:11434
```

**Security note:** Ollama has no built-in authentication. Use a firewall or reverse proxy (nginx, HAProxy) to gate access in production.

---

## Troubleshooting

### "uv: command not found" after install

```bash
# Add to PATH:
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Or, permanently, add to ~/.bashrc:
echo 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Ollama install fails

The setup script gracefully skips Ollama if the install fails — cloud-only mode works fine.

**Manual Ollama install:**
```bash
curl -fsSL https://ollama.ai/install.sh | sh
# Then start:
sudo systemctl start ollama
sudo systemctl enable ollama
```

### "Permission denied" when running Ollama

```bash
# If Ollama service won't start:
sudo systemctl status ollama -l  # Check logs
sudo journalctl -u ollama -n 50  # View recent logs
```

**If GPU access denied:**
```bash
# NVIDIA:
sudo usermod -a -G video $USER
# Reboot or: newgrp video

# AMD:
sudo usermod -a -G video $USER
sudo usermod -a -G render $USER
# Reboot for changes to take effect
```

### Test suite fails with import errors

```bash
# Refresh virtual environment:
uv sync --fresh --all-groups

# Re-run tests:
uv run pytest -c config/pytest.ini --rootdir=. -v
```

### Python version mismatch

```bash
# Check installed Python:
uv python list

# Install/switch to Python 3.11:
uv python install 3.11
uv python pin 3.11

# Re-sync:
uv sync --all-groups
```

### "curl: command not found" or "git: command not found"

```bash
# Ubuntu/Debian:
sudo apt-get install -y curl git

# Fedora/RHEL:
sudo dnf install -y curl git

# Arch:
sudo pacman -S curl git

# Alpine:
sudo apk add curl git
```

---

## Performance Tips

### Optimize for your hardware

**CPU-only (no GPU):**
- Use smaller models: `phi`, `neural-chat`, `orca-mini`
- Reduce batch sizes in examples
- Enable cloud APIs for larger tasks

**NVIDIA GPU (CUDA):**
- Use mid-to-large models: `llama2`, `mistral`, `orca`
- Monitor VRAM: `nvidia-smi` (watch for OOM)
- Consider quantized models if VRAM is tight

**AMD GPU (ROCm):**
- Start with smaller models; ROCm support is still maturing
- Monitor performance with `rocm-smi`

**Multi-GPU:**
```bash
# Ollama uses all GPUs by default
# To use specific GPUs:
CUDA_VISIBLE_DEVICES=0,1 ollama serve
```

### Rate Limiting & Quotas

See `cockpit/utils/rate_limiting.py` for built-in rate limiting.
For production, also set provider-side rate limits:

- **Gemini**: [https://ai.google.dev](https://ai.google.dev) → Quota & Limits
- **OpenAI**: [https://platform.openai.com](https://platform.openai.com) → Billing → Usage Limits
- **Anthropic**: [https://console.anthropic.com](https://console.anthropic.com) → Limits

---

## Running Example Projects

Once setup is complete:

```bash
cd ~/AI_Engineering_Cockpit
cd projects/01-hello-world
cp .env.example .env
# Edit .env and add GEMINI_API_KEY

uv sync
uv run python src/main.py
```

**For hybrid (local + cloud) inference** (Mac/Linux):
```bash
cd projects/05-hybrid-orchestrator
# ... same .env setup ...
uv run python src/main.py --dry-run  # Test without calling models
uv run python src/main.py             # Run with real calls
```

---

## Deployment on Linux Servers

### Cloud VMs (AWS EC2, GCP Compute, etc.)

1. **Launch an Ubuntu 22.04 LTS instance**
   - t3.medium or larger for cloud-only
   - g4dn.xlarge or larger for GPU acceleration (NVIDIA)

2. **SSH in and run setup:**
   ```bash
   sudo apt-get update
   git clone <repo-url>
   cd AI_Engineering_Cockpit
   bash scripts/setup-linux.sh
   ```

3. **Set up API keys securely:**
   ```bash
   # Use your cloud provider's secrets manager, not .env
   # Example (AWS):
   aws secretsmanager create-secret --name cockpit-api-keys \
     --secret-string '{"GEMINI_API_KEY":"...", "OPENAI_API_KEY":"..."}'
   
   # In your code, load from secrets manager instead of .env
   ```

### Docker (Optional)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN /root/.local/bin/uv sync --frozen --no-dev

CMD ["/root/.local/bin/uv", "run", "python", "projects/01-hello-world/src/main.py"]
```

Build and run:
```bash
docker build -t cockpit .
docker run --env GEMINI_API_KEY=$GEMINI_API_KEY cockpit
```

---

## Need Help?

- Check [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues across all platforms
- See [SETUP.md](../SETUP.md) for general setup guidance
- Open an issue on GitHub with your distribution, error message, and output of:
  ```bash
  uname -a
  lsb_release -a  # (if available)
  bash --version
  ```
