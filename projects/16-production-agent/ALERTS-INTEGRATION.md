# Alerts Integration

This directory includes tools to monitor and visualize alerts from production runs:

## Dashboard

**File:** `outputs/alerts-dashboard.html`

A self-contained web page that reads `alerts.jsonl` and renders a timeline of alert events.

### Features
- Load alerts from a file (or auto-detect from `outputs/alerts.jsonl`)
- Timeline view with firing/resolved events
- Stats: total events, firing count, resolved count, versions affected
- Event details: rule, version, value vs threshold, request ID, timestamp

### Usage
1. Run a live or dry-run scenario: `python src/main.py --requests 20 --phoenix`
2. Alerts are written to `outputs/alerts.jsonl`
3. Open `outputs/alerts-dashboard.html` in a browser
4. Click "Load Alerts" and select the `alerts.jsonl` file, or the page auto-loads if it finds it

### Design
- Vanilla JavaScript (no build, no dependencies)
- Dark theme matching the CLI
- Responsive layout

---

## Slack Integration

**File:** `scripts/post-alerts-to-slack.py`

Reads `alerts.jsonl` and POSTs each new alert event to a Slack webhook.

### Setup
1. Create or get a Slack webhook URL:
   - Go to your Slack workspace → Settings → Apps & integrations → Incoming Webhooks
   - Create a new webhook (or copy an existing one)
   - Copy the URL

2. Export the webhook:
   ```bash
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
   ```

3. Run the poster:
   ```bash
   python scripts/post-alerts-to-slack.py outputs/alerts.jsonl
   ```

### How it works
- Reads `alerts.jsonl` line by line
- Tracks seen alerts in `.alerts.jsonl.state` (to avoid duplicates on re-runs)
- POSTs each new alert as a Slack attachment
- Fires → red 🚨, Resolved → green ✅
- Shows value, threshold, version, request ID, timestamp

### Example Slack message
```
🚨 LOOP_DETECTED
*FIRED* for v2

Value: 1
Threshold: 0
Request ID: req-0062
Time: 2026-01-01T00:12:30
```

### Testing (no Slack account needed)
```bash
python scripts/post-alerts-to-slack.py outputs/alerts.jsonl  # Requires SLACK_WEBHOOK_URL set
# Error: Failed to post (expected, webhook is a test)
```

---

## Integration with Live Runs

1. **Dry run with dashboard:**
   ```bash
   python src/main.py --dry-run --scenario late-regression
   open outputs/alerts-dashboard.html
   ```

2. **Live run with Slack:**
   ```bash
   export SLACK_WEBHOOK_URL="..."
   python src/main.py --requests 60 --canary-percent 50 --phoenix &
   # In another terminal:
   watch -n 5 "python scripts/post-alerts-to-slack.py outputs/alerts.jsonl"
   ```

3. **Live run with both:**
   - Dashboard for local inspection
   - Slack for team notifications
   - Phoenix for trace details

---

## Architecture

- **alerts.jsonl format:**
  ```json
  {"timestamp": "2026-01-01T00:12:30", "version": "v2", "rule": "loop_detected", "state": "fired", "value": 1, "threshold": 0, "request_id": "req-0062"}
  ```

- **Dashboard:** pure HTML/JS, no server needed
- **Poster:** stdlib Python (urllib), idempotent (tracks state in `.alerts.jsonl.state`)

---

## Known Limitations

- Slack poster uses simple attachment format (no interactive buttons)
- Dashboard doesn't auto-refresh (reload browser to see new events)
- State file (`.alerts.jsonl.state`) is local; different machines maintain separate state

To upgrade: extend poster to use Slack BlockKit, or add a polling server for the dashboard.
