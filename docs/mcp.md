# Ravix MCP — connect an AI assistant to your site

Ravix can act as an **MCP server** (Model Context Protocol) so an assistant such as Claude can work with your site. It is **off by default**.
Stage M1 ships the foundation: the OAuth 2.1 sign-in, the "Connected apps" pages, and one read-only tool, `get_site_status`.

## Requirements
- A **public HTTPS** address (a real domain or subdomain; the root of the domain is best). Set `SITE_URL` to it.
- The assistant's servers must be able to reach your site (no IP allow-lists that block them).
- Ravix must not run in multisite mode (MCP is switched off there).

## Turn it on (step by step)
1. Sign in as a **super administrator**.
2. Panel sidebar → **System administration** → **AI assistants (MCP)** (`/panel/mcp/`).
3. Tick **Enable MCP** and save. The page now shows the connector address: `https://<your-domain>/mcp/`.
4. Optional: choose which scopes managers/administrators may grant, and whether unknown apps are allowed (warning) or only trusted hosts.

## Connect Claude
1. In claude.ai: **Settings → Connectors → Add custom connector**.
2. Name: anything. Address: `https://<your-domain>/mcp/` (copy it from the panel page). Leave the OAuth fields empty.
3. Claude opens your site's sign-in, then the Ravix **consent page**. Check the address it will return to, then press **Allow**.
4. Ask Claude, for example: "Check my website's status." It calls `get_site_status`.

## Safety model
- Tokens are random, stored only as SHA-256 hashes; access tokens last 30 minutes, refresh tokens rotate and are revoked on reuse.
- What a connection can do = what the person granted ∩ what the administrator allows that role ∩ what the user's role can do right now.
- Changing the password, deactivating the user or pressing **End connection** (My account → Connected apps, or the administrator's list) stops it immediately.
- Every tool call is written to the audit log. Requests are rate-limited per connection.

## Developer notes
- Endpoint: `POST /mcp/` (stateless; no session). Both protocol eras are served: 2026-07-28 and 2025-11-25 / 2025-06-18 / 2025-03-26.
- Discovery: `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`. Sub-folder installs cannot serve the root `.well-known` paths; use a domain or subdomain.
- Tests: `python manage.py test apps.mcp`; end-to-end with the official SDK: `scripts/e2e_mcp.py` (needs `pip install "mcp>=2,<3" playwright`).
