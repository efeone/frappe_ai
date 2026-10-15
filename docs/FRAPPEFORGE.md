# Connecting your site to FrappeForge

FrappeForge is an AI engineering workspace for Frappe and ERPNext. It can study your live site and prepare changes such as custom fields, scripts, print formats and workflows. With `frappe_ai` installed, **your site decides what FrappeForge may do**:

- FrappeForge gets its own user on your site. It can read customizations and **propose** releases, nothing else.
- A System Manager reviews each proposal in Desk, record by record, with every script shown in full.
- Approved releases are applied by your site, as the approving user, with a change log entry per record.
- Any change can be rolled back from that log.
- Connections expire, can be pinned to FrappeForge's IP addresses, and can be revoked at any moment.

FrappeForge's own workspaces are temporary and treated as untrusted. None of the controls above depend on them.

## Connect

1. Install the app: `bench get-app https://github.com/vyogotech/frappe_ai`, then `bench --site <site> install-app frappe_ai`.
2. In Desk, open **AI Assistant Settings** and click **Connect to FrappeForge**. You can also use **FrappeForge Connection → Connect FrappeForge**.
3. Fill in the dialog:
   - **Label**: who is connecting, for example the FrappeForge workspace name.
   - **Valid for (days)**: 1 to 365, default 30.
   - **Allowed IP addresses**: FrappeForge's published egress addresses. Requests from anywhere else are refused.
   - **Additional read roles** (optional): roles such as *Sales User* if FrappeForge should read business documents. Writes stay blocked whatever these roles allow.
4. Copy the snippet into FrappeForge's **Connect live site** dialog. It holds the site URL, an API key and secret, and an OAuth access token; the secrets are shown once. The access token lasts one day (or until the connection ends, if sooner). To renew it, or if the secrets are lost, open the connection, click **Rotate key**, and paste the new snippet into FrappeForge again.

The access token is for your site's own `frappe-mcp-server`, if it has one. Enter its address in **AI Assistant Settings → MCP Server URL** and the snippet includes it, so FrappeForge reads live data through it. That server holds no credential: it checks the token against your site and forwards it on every call, so everything below applies to it too.

## Review and approve a release

When FrappeForge proposes a change it appears under **FrappeForge Proposed Release** with status *Pending*.

- **What This Release Changes** is built by your site from the uploaded package itself. Each record shows whether it is created or updated, and the fields that change.
- A release can contain Custom Fields, Property Setters, Client and Server Scripts, Print Formats, Notifications, Web Forms, Web Pages, Web Templates, Website Themes, Workspaces and custom DocTypes. Workflows and Reports, which packages cannot carry, arrive alongside the package and are listed separately. Anything else is refused when the release is proposed.
- Records marked **runs code** (Server and Client Scripts, Web Forms, Notifications, Web Templates, Website Themes) and the Workflow and Report records are shown in full. Read them before approving.
- **Agent's Description** is written by FrappeForge. Treat it as a claim, not as the content.
- **Approve and apply** runs the import in the background. The status moves from *Approved* to *Applying*, then to *Applied* or *Failed* with the error. A failed import leaves nothing behind.
- **Reject** records your reason. FrappeForge sees it the next time it checks.

Proposals nobody answers expire after 14 days.

## Let FrappeForge apply changes itself

By default every change waits for you. In **AI Assistant Settings → Vyogo Forge AI**, set **Forge Autonomy** to *Apply what the skills cover* to let routine changes through:

- A proposal applies itself only when every record in it is one of the **Record Types Applied Without Approval**, and none of it runs code. The default list is what the assistant's writes skills govern: Custom Field, Property Setter, Print Format, Letter Head, Workspace, Lead, CRM Lead and FCRM Note.
- Records that run code (Client and Server Scripts, Web Forms, Notifications, Web Templates, Website Themes) always wait, and so do Workflows, Reports and anything outside the list. The setting refuses those types, and Desk-only types such as User and Role.
- **Approve As** is a System Manager who answers for these changes. The change is made with that user's permissions, so Frappe's own permission checks still apply. If they stop being a System Manager or are disabled, everything waits again.
- A change that applied itself is marked **Approved Automatically** and logged and rolled back like any other. When autonomy is on but a change waits, **Autonomy Note** says why.

## Roll back

Every record an applied release touched has a **FrappeForge Change Log** entry holding the version before and after.

- Open the entry and click **Roll back**. A created record is deleted, and an updated record gets its previous version back. The rollback is itself logged.
- FrappeForge can ask for a rollback but cannot perform one. Such entries show *FrappeForge asked for this change to be rolled back*.

Change log entries cannot be edited or deleted, and FrappeForge's user cannot read them.

When FrappeForge's access stops working (revoked, replaced by Rotate key, or the one-day access token ran out), FrappeForge tells the user in plain words and offers **Reconnect live site**. Rotate the key here and paste the new snippet.

## Revoke

Open the connection and click **Revoke**. The agent user is disabled, its key deleted and its OAuth token revoked, effective on the next request. Connections also stop working at their expiry date, checked on every request and cleaned up hourly.

## What FrappeForge's user can and cannot do

| Allowed | Refused (HTTP 403) |
| --- | --- |
| `GET` on `/api/resource/<DocType>[/<name>]`, `/api/v2/document/...`, `/api/v2/doctype/<DocType>/meta` | Any `POST`, `PUT`, `PATCH` or `DELETE` on documents |
| Read-only methods: `frappe.client.get`, `get_list`, `get_count`, `get_value`, report and search methods, and `frappe.integrations.oauth2.openid_profile` (how the hosted MCP server checks the token) | Every other method, including `frappe.client.insert`, `set_value`, `delete`, `upload_file`, `run_doc_method` |
| `frappe_ai.api.frappeforge.status`, `propose_release`, `request_rollback` | Desk pages, private files, `approve`, `reject`, `rollback`, connection management |

The check runs in an `auth_hooks` entry, right after the API key or OAuth token is verified, so it applies whatever roles the user has. To allow another read-only method, such as a custom search, add it to `site_config.json`:

```json
{ "frappeforge_read_methods": ["my_app.api.search"] }
```

## Settings

| `site_config.json` key | Default | Meaning |
| --- | --- | --- |
| `frappeforge_read_methods` | `[]` | Extra read-only methods FrappeForge may call |
| `frappeforge_max_release_mb` | `20` | Largest release tarball accepted |
| `frappeforge_proposal_ttl_days` | `14` | Pending proposals expire after this many days |

Built-in limits per connection: 30 proposals and 60 rollback requests an hour, and 50 pending proposals. A release may hold at most 2,000 files and 200 Workflow or Report records, and only the record types listed above. Frappe itself allows 60 new users an hour per site, so at most that many connections can be created an hour.

## For integrators: the API

All methods live under `/api/method/frappe_ai.api.frappeforge.<name>`.

| Method | Caller | Arguments | Returns |
| --- | --- | --- | --- |
| `status` (GET/POST) | agent | — | `{site_url, frappe_ai_version, connection: {name, status, expires_on}, proposals: [{name, package, version, status, approved_at, rejected_reason, error, changes: [...]}]}` |
| `propose_release` (POST, multipart) | agent | file field `release` (`<package>-<x.y.z>.tar.gz` from a Package Release); `summary`, `conversation_id`, `idempotency_key`, `extra_records` (JSON list of Workflow, Workflow State, Workflow Action Master or Report documents) | `{name, package, version, status, approve_url}` |
| `request_rollback` (POST) | agent | `change` | `{ok: true}` |
| `create_connection` (POST) | System Manager | `label`, `expires_in_days`, `allowed_ips`, `read_roles` | `{connection, user, site_url, api_key, api_secret, access_token, expires_on, snippet}` |
| `rotate_key`, `revoke` (POST) | System Manager | `connection_name` | new key, secret, token and snippet / `{ok: true}` |
| `approve`, `reject` (POST) | System Manager | `release` (+ `reason`) | proposal summary / `{ok: true}` |
| `rollback` (POST) | System Manager | `change` | `{rollback: <new change log entry>}` |

Sending the same `idempotency_key` again from the same connection returns the first proposal, so retries after a timeout are safe. Keys are scoped to the connection, so tenants cannot collide.

Example from a FrappeForge workspace:

```bash
curl -s -H "Authorization: token $KEY:$SECRET" \
  -F release=@sales-rules-0.0.3.tar.gz \
  -F summary="PO required above 50,000" \
  -F idempotency_key="conv-42-sales-rules-0.0.3" \
  https://erp.example.com/api/method/frappe_ai.api.frappeforge.propose_release
```

## How it fits together

```text
FrappeForge workspace (temporary)          Your site (frappe_ai)
──────────────────────────────────          ─────────────────────────────────────────────
reads schema, scripts, workflows  ──GET──▶  guard: agent user may read and propose only
builds + tests a Frappe Package
on its own dev site
propose_release(tarball)          ──POST─▶  FrappeForge Proposed Release (Pending)
                                            System Manager reviews and approves in Desk
                                            background job, as the approver:
                                              Package Import + workflow records
                                              FrappeForge Change Log (before / after)
status()                          ──GET──▶  Applied / Failed / Rejected
request_rollback(change)          ──POST─▶  flag on the change log entry
                                            System Manager: Roll back
```

Code: `frappe_ai/frappeforge/` (guard, connections, release reader, deploy) and `frappe_ai/api/frappeforge.py`.

## Developing and testing

`compose.yml` runs an SNE container (`docker.io/vyogo/erpnext:sne-version-16`) with this repository mounted as an app:

```bash
podman compose up -d
podman compose exec frappe bash -lc 'cd /home/frappe/frappe-bench && bench --site dev.localhost install-app frappe_ai'
podman compose exec frappe bash -lc 'cd /home/frappe/frappe-bench && bench --site dev.localhost set-config allow_tests true'
podman compose exec frappe bash -lc 'cd /home/frappe/frappe-bench && bench --site dev.localhost run-tests --app frappe_ai'
```

The guard and API tests also make real HTTP calls through the container's web server with a real agent key. They are skipped when no server answers on port 8000.

Known Frappe 16.33 issue: once a package has a release, `Package Release.set_version` replaces a minor or patch of 0 with the column name, producing names like `pkg-0.minor.5`. Set `major`, `minor` and `patch` explicitly, each at least 1 (for example 1.1.1, then 1.1.2).
