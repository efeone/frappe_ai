# AI Sidebar

[![CI](https://github.com/vyogotech/frappe_ai/actions/workflows/ci.yml/badge.svg)](https://github.com/vyogotech/frappe_ai/actions/workflows/ci.yml)

**AI Sidebar** by [Vyogo Technologies](https://vyogo.com) is an in-desk AI assistant for Frappe and ERPNext. It streams answers from the Vyogo AI agent over HTTP and renders markdown prose plus structured KPI / chart / table blocks in a chat sidebar. The repository and Frappe app module are named `frappe_ai`.

## Features

- Chat sidebar embedded in the Frappe desk, toggled from the navbar or a custom keyboard shortcut
- Streamed responses relayed through `frappe.realtime` (no separate SSE proxy in the browser)
- Per-user conversation history persisted as `AI Chat Session` / `AI Chat Message` doctypes
- Page-context forwarding: the agent sees the user's current route / doctype / docname for grounded answers
- Authentication via the user's existing `sid` cookie — no extra OAuth client to provision
- Multi-site aware: every request to the agent names the site it came from, so one agent can serve many sites on a shared bench

## Installation

```bash
bench get-app https://github.com/vyogotech/frappe_ai
bench --site your-site install-app frappe_ai
bench restart
```

Then set the agent URL in `sites/your-site/site_config.json`:

```json
{
  "frappe_ai_agent_url": "http://localhost:8484"
}
```

And open `/app/ai-assistant-settings` to toggle **Enabled**.

Or install the released package with [FPM](https://github.com/vyogotech/fpm), which ships the compiled desk bundle and needs no build step on the bench:

```bash
fpm repo add vyogo https://fpm.vyogo.tech --username <user>   # or: fpm repo add ghcr ghcr.io/vyogotech/fpm --type oci
fpm install vyogotech/frappe_ai --bench-path /path/to/bench --site your-site
```

See [QUICKSTART.md](QUICKSTART.md) for a 5-minute walkthrough or [INSTALLATION.md](INSTALLATION.md) for the full guide.

## Connect to FrappeForge

This app also lets a site owner connect the site to FrappeForge, the AI engineering workspace for Frappe. FrappeForge can then read customizations and propose releases that a System Manager approves, with a change log and one-click rollback. See [docs/FRAPPEFORGE.md](docs/FRAPPEFORGE.md).

## Usage

Click the AI button in the navbar (or press your configured shortcut) and type a query. Examples:

- "Show me all open projects"
- "What are the top customers by revenue?"
- "List pending sales orders"

## Configuration

| Where | Key | Purpose |
| --- | --- | --- |
| `site_config.json` | `frappe_ai_agent_url` | Agent endpoint (authoritative) |
| AI Assistant Settings | `enabled` | Master switch |
| AI Assistant Settings | `timeout` | Per-request timeout (1–300s) |
| AI Assistant Settings | `sidebar_width` | Sidebar width in px (300–600) |
| AI Assistant Settings | `keyboard_shortcut` | Toggle combo (e.g. `Alt+/`) |

## License

MIT
