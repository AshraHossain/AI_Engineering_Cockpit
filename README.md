# AI Engineering Cockpit

A **production-grade AIDevOps platform**: a modular framework for testing,
evaluating, red-teaming, securing, monitoring, and governing AI systems —
plus a set of 14 runnable example projects that demonstrate each framework
in real-world scenarios.

**Platform:** Windows, Mac (Intel & Apple Silicon), Linux (Ubuntu/Debian/Fedora/Arch/Alpine)  
**Setup time:** ~5 minutes per machine  
**All three tiers complete.** Every framework ships with real, working logic and is enabled by default. Nothing here is a placeholder.

---

## 🚀 Quick start (5 minutes)

### Windows (PowerShell)
```powershell
git clone https://github.com/AshraHossain/AI_Engineering_Cockpit.git AI_Engineering_Cockpit
cd AI_Engineering_Cockpit
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1

cp .env.example .env   # then add your GEMINI_API_KEY

cd projects/01-hello-world
cp .env.example .env
uv sync
uv run python src/main.py
```

### Mac (Bash)
```bash
git clone https://github.com/AshraHossain/AI_Engineering_Cockpit.git AI_Engineering_Cockpit
cd AI_Engineering_Cockpit
bash scripts/setup-mac.sh

cp .env.example .env   # then add your GEMINI_API_KEY

cd projects/01-hello-world
cp .env.example .env
uv sync
uv run python src/main.py
```

### Linux (Bash)
```bash
git clone https://github.com/AshraHossain/AI_Engineering_Cockpit.git AI_Engineering_Cockpit
cd AI_Engineering_Cockpit
bash scripts/setup-linux.sh

cp .env.example .env   # then add your GEMINI_API_KEY

cd projects/01-hello-world
cp .env.example .env
uv sync
uv run python src/main.py
```

**Each setup script:**
1. Installs [`uv`](https://docs.astral.sh/uv/) if missing
2. Installs Python 3.11+ toolchain via uv
3. Optionally installs Ollama (local model inference)
4. Syncs all dependencies
5. Creates `.env` from `.env.example`
6. Runs the test suite (mocked, no API keys needed)

See [SETUP.md](SETUP.md) for detailed platform-specific guidance and troubleshooting.

---

## 📋 What makes this different

### Security is first-class, not bolted on
`cockpit/security/` ships real regex-based prompt-injection detection and PII
masking with Luhn validation (cuts credit-card false positives). Not a TODO.

### The red-team harness grades your own defenses
It runs 32-payload corpus through the injection detector and reports which
attacks slip past, so security blind spots are measured, not assumed. See
[docs/SECURITY_COVERAGE.md](docs/SECURITY_COVERAGE.md).

### Injection detection sees through obfuscation
Scans match against the raw input, Unicode-normalized form (zero-width
characters, letter-spacing, accented homoglyphs), and base64/hex/rot13
decodings — because an encoded instruction is still an instruction.

### Every framework is feature-flagged
`cockpit/config/feature_flags.py` gates testing/evaluation/red-teaming/security/monitoring/governance
independently, with pre-built profiles for startup, enterprise, research,
and safety-focused use cases.

### Examples are real, isolated, and tested
Each `projects/0X-*/` is its own `uv`-managed project with mocked tests —
no live API key needed to run `pytest`, but the code talks to real provider SDKs.

### Cross-platform from the start
- **Windows**: Cloud-only (Gemini, OpenAI, Anthropic APIs)
- **Mac (Intel)**: Hybrid (cloud + CPU-only Ollama)
- **Mac (Apple Silicon)**: Hybrid (cloud + GPU-accelerated Ollama)
- **Linux**: Hybrid (cloud + GPU-accelerated Ollama on NVIDIA/AMD GPUs)

See [docs/WINDOWS_VS_MAC.md](docs/WINDOWS_VS_MAC.md) and [docs/LINUX_SETUP.md](docs/LINUX_SETUP.md).

---

## 🏗️ Repository layout

```
cockpit/              Shared framework: testing, evaluation, red_teaming,
                      security, monitoring, governance, config, utils.
                      See docs/ARCHITECTURE.md.

projects/             14 standalone example projects (01-hello-world through
                      14-threat-monitor). Each has its own pyproject.toml.

models/               Model registry + Ollama/Hugging Face reference material.

docs/                 Architecture, getting started, deployment, troubleshooting,
                      API comparison, recipes, platform-specific guides.

scripts/              Setup scripts (Windows PowerShell, Mac Bash, Linux Bash),
                      test runners, formatters, project scaffolding.

config/               pytest, ruff, mypy, pre-commit, and coverage configuration.

tests/                Root-level tests covering cockpit/config/* and repo structure.

.github/              CI workflows (test, security, docs) and issue/PR templates.
```

---

## 📚 The 14 Example Projects

Projects 01-05 are standalone. Projects 06-14 import `cockpit/` frameworks.

| # | Name | Demonstrates |
|---|---|---|
| 01 | [hello-world](projects/01-hello-world) | Minimal Gemini API call |
| 02 | [rag-chatbot](projects/02-rag-chatbot) | Retrieval-augmented generation with task-asymmetric embeddings |
| 03 | [multi-model-orchestrator](projects/03-multi-model-orchestrator) | Comparing Gemini vs OpenAI in one app |
| 04 | [streaming-responses](projects/04-streaming-responses) | Streaming model output to client |
| 05 | [hybrid-orchestrator](projects/05-hybrid-orchestrator) | Local Ollama + cloud Gemini routing (Mac/Linux only) |
| 06 | [eval-harness](projects/06-eval-harness) | Scoring a pipeline against a reference set (quality, safety, cost) |
| 07 | [red-team-runner](projects/07-red-team-runner) | Attacking a target with injection corpus; reporting defense leaks |
| 08 | [cost-dashboard](projects/08-cost-dashboard) | Instrumenting model calls for spend and latency observability |
| 09 | [agent-tool-use](projects/09-agent-tool-use) | Function calling with automatic and manual authorization |
| 10 | [batch-pipeline](projects/10-batch-pipeline) | Bulk processing with rate limiting, retries, partial-failure tolerance |
| 11 | [secure-gateway](projects/11-secure-gateway) | RBAC, input/output security, tamper-evident audit chain |
| 12 | [governed-deployment](projects/12-governed-deployment) | Model promotion gated by N-of-M approvals |
| 13 | [compliance-report](projects/13-compliance-report) | GDPR/HIPAA/SOX findings with evidence |
| 14 | [threat-monitor](projects/14-threat-monitor) | Brute force, exfiltration, privilege-escalation detection |

Every project runs offline tests with no API key. Projects 06-14 support `--dry-run` to see them work before adding credentials.

---

## 🔧 The Six Core Frameworks

### 1. Testing Framework
Unit, integration, and benchmark tests. All projects include mocked test suites.

### 2. Evaluation Framework
Quality metrics (BLEU, ROUGE, custom scoring), safety evaluation (toxicity,
bias detection), and cost evaluation (per-token pricing, spend tracking).

### 3. Red Teaming Framework
Adversarial testing with 32-payload injection corpus. Tests your own defenses
and reports which attacks slip through.

### 4. Security Framework ⭐ First-Class
- **Input security**: Prompt injection detection, input validation, rate limiting
- **Output security**: PII masking (email, phone, SSN, credit card, passport, IP)
- **Data security**: Encryption at-rest/in-transit, secrets management
- **Access control**: RBAC (admin, developer, viewer, external)
- **Audit & logging**: Tamper-evident audit trail, real-time monitoring
- **Compliance**: GDPR, HIPAA, SOX findings with evidence
- **Threat detection**: Brute force, exfiltration, privilege escalation

See [docs/SECURITY_COVERAGE.md](docs/SECURITY_COVERAGE.md) for detailed coverage.

### 5. Monitoring & Governance
- Cost tracking per model/API call
- Performance metrics (latency, throughput)
- Model versioning and promotion workflows
- Approval workflows (N-of-M gating)
- Audit logging with hash chains

### 6. Configuration & Feature Flags
- Enable/disable frameworks independently
- Pre-built use-case profiles: Startup, Enterprise, Research, Safety-Focused
- Per-environment configuration (dev, staging, prod)

---

## 📊 Cross-Platform Comparison

| | Windows | Mac (Intel) | Mac (Apple Silicon) | Linux |
|---|---|---|---|---|
| **Setup time** | 5 min | 5 min | 5 min | 5 min |
| **Cloud APIs** | ✅ Gemini/OpenAI/Anthropic | ✅ | ✅ | ✅ |
| **Local Ollama** | ❌ Not supported | ✅ CPU-only (1-5 tok/s) | ✅ GPU-accel (10-50+ tok/s) | ✅ GPU-accel (NVIDIA/AMD) |
| **Recommended use** | Development, cloud-only apps | Hybrid (cost-effective) | Hybrid (fast inference) | Hybrid (GPU clusters) |
| **Setup script** | `setup-windows.ps1` | `setup-mac.sh` | `setup-mac.sh` | `setup-linux.sh` |

**All platforms run identical code.** Platform-specific behavior is opt-in via
configuration (cloud-only vs hybrid mode).

---

## 🚦 Installation & Setup

### Prerequisites (All Platforms)
- Git
- No Python install needed — `uv` handles the Python toolchain
- Optional: Homebrew (Mac), apt/dnf/pacman/apk (Linux), or curl (for manual installs)

### Install
See [SETUP.md](SETUP.md) for:
- Detailed per-platform instructions
- Troubleshooting (PATH issues, dependency conflicts, Ollama setup)
- Re-running the setup safely multiple times

### Verify
```bash
uv run python -c "import sys; print(sys.version)"   # should print 3.11.x
uv run pytest -c config/pytest.ini --rootdir=. -v    # root suite, no API keys needed
```

---

## 📖 Documentation

- **[SETUP.md](SETUP.md)** — Detailed per-platform setup guide with troubleshooting
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — System design, module overview
- **[docs/WINDOWS_VS_MAC.md](docs/WINDOWS_VS_MAC.md)** — Why platforms differ
- **[docs/LINUX_SETUP.md](docs/LINUX_SETUP.md)** — Linux-specific guidance (GPU setup, package managers)
- **[docs/SECURITY_COVERAGE.md](docs/SECURITY_COVERAGE.md)** — Prompt injection detection limits (24/32 payloads caught)
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — Containerization, secrets, production checklist
- **[docs/API_COMPARISON.md](docs/API_COMPARISON.md)** — Gemini vs OpenAI vs Anthropic
- **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** — First project walkthrough
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — Common issues and fixes

---

## 🧪 Running Tests

### Quick validation (no API keys)
```bash
cd ~/AI_Engineering_Cockpit
uv run pytest -c config/pytest.ini --rootdir=. -q
```

### Full test suite (all projects, with coverage)
```bash
bash scripts/run-tests.sh
```

### Lint and typecheck
```bash
make lint      # ruff check (no fixes)
make typecheck # mypy against cockpit/
make security  # bandit against cockpit/
```

---

## 🔐 Security & Compliance

This repo is intended for research, development, and learning. If you're
building production systems, please read:

- [docs/SECURITY_COVERAGE.md](docs/SECURITY_COVERAGE.md) — Honest gaps in injection detection
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Pre-launch checklist, secrets management, audit trail limitations
- [SECURITY.md](SECURITY.md) — How to report security issues

Key highlights:
- **Injection detector** catches 24 of 32 corpus payloads (known gap documented)
- **Audit log** is tamper-*evident* (hash chain reveals tampering) not tamper-*proof*
- **Compliance findings** are evidence-based; no verdicts (compliance is a legal determination)
- **Secrets storage** example in code; integrate with real KMS for production

---

## 📦 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 📄 License

[MIT](LICENSE)

---

## 🎯 Next Steps

1. **Clone and setup** using the quick-start above (5 minutes)
2. **Run the first example** `projects/01-hello-world` (add an API key first)
3. **Explore the frameworks** in `cockpit/` — each module is standalone and well-commented
4. **Try a demo project** that interests you (06-14 show frameworks in action)
5. **Read the architecture** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) to understand how pieces fit
6. **Deploy one project** following [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

---

**Questions?** Open an issue, check [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), or see [CONTRIBUTING.md](CONTRIBUTING.md) for how to get help.
