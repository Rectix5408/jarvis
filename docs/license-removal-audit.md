# Product License Removal Audit

Modified fork: Reinhold-Jesse/jarvis, 2026-10-04.
Baseline: `6ffef81322cd7b65e2949e7f6b5a743c48dfa0c2`.
Pre-edit backup: `/tmp/jarvis-before-license-removal-6ffef813.tar` (tracked
source only; credentials and runtime data deliberately excluded).

## Scope and Source Licenses

The repository's `LICENSE` and `NOTICE` identify the first-party work as
Apache-2.0, Copyright 2026 Andreas Bender. Section 2 permits derivative works;
section 4 requires preservation of notices and marking modified files.
Reference: https://www.apache.org/licenses/LICENSE-2.0

All source and test files below were inspected for separate license/copyright
restrictions before modification. No separate proprietary license notice was
found in these first-party files. Original notices are retained; each modified
file carries a fork modification notice. `LICENSE`, `NOTICE`, `NOTICE.md`,
third-party/vendor files, and third-party dependencies are not relicensed.

Excluded: the license issuer `license-manager/` is absent and excluded by Git;
no issuer, private key or external premium implementation is acquired.
`NOTICE.md` specifically excludes Microsoft's Clippy imagery from the MIT
source-code license, and identifies SAP `hdbcli`/`pyrfc` and the SAP RFC SDK as
requiring separate authorization. These assets/dependencies remain subject to
their original terms. Removing a local quantity gate does not grant rights to
SAP, Microsoft assets, hosted models or other external services.

## Original Flow

1. `backend/license.py` reads `data/license.json` and accepts
   `JARVIS-LIC-1` payload/signature/certificate tokens. Ed25519 verifies the
   issuer certificate against `backend/license_root.pub`, then the payload.
2. `hwid()` hashes machine-id, root filesystem UUID/device and physical MAC
   into an H1 identifier. Hardware matching requires two of three components.
3. `_abrufen()` fetches a signed status list from
   `https://raw.githubusercontent.com/dev-core-busy/jarvis-licenses/main/status.json`
   (overridable through `JARVIS_LICENSE_URL`, ETag, 15-second timeout).
   UUID hashes identify records. Hardware binding, revocation, tier and expiry
   come from the signed record. No outbound activation POST was found.
4. Status failures fall back to FREE. A cached successful status is accepted
   for 14 days without network access. A 30-day introduction grace temporarily
   removes enforcement; expiration of grace enables restrictions.
5. FREE: no updates, one profile, five active skills, five active users over
   30 days, 50 knowledge files, no automatic updates or location sync.
   BASIC: manual updates, one profile, five skills, ten users, 100 files,
   no automatic updates or location sync. ENTERPRISE: unlimited quantity
   counters, manual/automatic updates and location sync. No PRO/Premium tier
   is defined; broad keyword matches also include unrelated German 'pro'.
6. `startup_license()` checks after 20 seconds and then daily. Every check
   invokes enforcement, which can disable recently enabled skills (including
   associated services) and remove `system_auto_update`. It never deletes
   existing profiles, knowledge files or user accounts.

## Original Enforcement Inventory

Line numbers refer to the baseline, before removal.

| File / locations | Behavior | Change |
| --- | --- | --- |
| `backend/license.py:67-117,272-428,516-881` | State, limits, hardware, signatures, polling, expiry, grace, activation | Delete product-license module |
| `backend/license_root.pub` | Product-license trust root | Delete unused public key |
| `backend/license_enforce.py:206-391` | Quantity checks, profile usability, destructive skill/update reconciliation | Delete enforcement module |
| `backend/main.py:2767-2780,7834-7842` | Login and Outlook SSO user ceilings after actual auth | Remove only quantity check |
| `backend/main.py:3120-3127,3152-3160` | Manual/automatic update restrictions | Preserve admin auth and schedule validation |
| `backend/main.py:3201-3284` | GET/POST/DELETE `/api/license`, POST `/api/license/check`, daily startup hook | Remove routes and task |
| `backend/main.py:4051-4085` | Admin portal license warning | Remove warning data |
| `backend/main.py:5292-5296,5360-5387` | Profile creation ceiling, role editor ceiling metadata | Remove ceiling and metadata |
| `backend/main.py:6268-6274` | Blocks skill enablement before dependency installation | Remove quantity check, retain admin auth |
| `backend/main.py:11363-11369,11515,11564,11619` | Sync peer create/edit/run license gate | Remove gate, retain admin/share auth |
| `backend/main.py:11482-11489` | Sync license metadata | Remove metadata |
| `backend/main.py:11647-11653,11845-11852` | Both upload routes' knowledge file ceilings | Remove quantity checks, retain editor/path/group/archive guards |
| `backend/knowledge_sync.py:35,912-922,1053-1055,1451-1453` | Blocks manual/automatic location sync | Remove license dependency, retain token, TLS fingerprint, checksum and mirror protections |
| `frontend/js/app.js:1222-1227,1333-1479` | License panel fetching and key actions | Remove functions and initialization |
| `frontend/settings.html:1672-1696,2132-2133,3335` | License panel and sync/role warnings | Remove obsolete markup |
| `frontend/portal.html:357-360,583-597` | Clickable license warning | Remove markup and handler |
| `frontend/js/agent_roles.js:286-299` | Profile ceiling warning | Remove warning |
| `frontend/js/knowledge_sync.js:77,130-144,210` | License-driven disabled add/run controls | Remove license state; retain running-state guard |
| `frontend/js/i18n.js:726,741-761,3184,3199-3219` | License translations | Remove unused messages |
| `frontend/css/style.css:7869-7877` | License warning style | Remove unused rule |
| `backend/skills/manager.py:115-124` | Activation timestamp solely for license reconciliation | Remove license-only timestamp bookkeeping |
| `requirements.txt:34` | cryptography comment attributes dependency to licensing | Correct comment; keep crypto dependency for credential encryption/TLS |
| `tests/test_license.py` | Old signature/tier/enforcement suite | Replace with unrestricted endpoint behavior/security regression tests |
| `tests/test_knowledge_sync.py`, `tests/test_knowledge_sync_ui.js`, `tests/test_agent_roles_ui.js`, `tests/test_addin_sso.py` | Expectations of removed gates/UI | Adapt to license-free behavior while retaining security assertions |

`profil_nutzbar()` was defined but had no production callers: it was not an
additional agent gate. No further product-tier check was found in config,
agent, LLM, tasks/scheduler, MCP, browser, WhatsApp, Docker, Android, Windows or
watch clients. Skill enabled/installed flags and profile/user/tool permissions
are operational/security controls and are preserved.

The broader search included license/licence, FREE/PRO/BASIC/ENTERPRISE/premium,
tier/plan/subscription/entitlement, hardware_id/license_key/activation/grace,
feature/limit/max_skills/max_users/max_profiles/knowledge_limit/updates, with
vendor bundles and dependency license metadata classified separately.

## Preserved Security and Limits

Authentication dependencies and function signatures, passwords/PAM/AD, 2FA,
SSO verification, rate limiting, account blocking, local/admin checks, profile
ACLs, masked API keys, knowledge editor/group access, sandbox deny lists, MCP
permissions, task owner privileges and tool allowlists remain intact.
Knowledge sync still requires an authorized share token and preserves TLS
fingerprints, path checks, checksums and local mirror write protection.

`user_sessions.MAX_USERS=500` trims presence-history records, not account
creation/login. `agent_roles.MAX_ROLLEN=24`, step/context/token caps, upload
resource limits, sync intervals and dependency availability are unrelated
operational safeguards; they are intentionally not removed.

`backend/telemetry.py` writes local diagnostic files. Frontend telemetry
requests target the local backend; no feature-license telemetry endpoint was
found. Product-license status fetches disappear with the license module.
Protection and Git exclusion of legacy `data/license.json` remain, as it may
contain private contract/contact information on upgraded installations.

## Deployment and Recovery

The patch affects this checkout, not an already running remote container.
Apply it to the same baseline on the server and rebuild the local Docker image.
Use the local-build Compose override from setup; an upstream registry image
does not contain these changes. Rebuild with:

```sh
sudo docker compose up -d --build --pull never
sudo docker compose ps
sudo docker compose logs --tail=80 jarvis
```

Previously disabled skills remain disabled until the administrator enables
them. Previously removed automatic-update jobs must be recreated by choosing
an update schedule. No blanket enabling or credential changes are performed.
The existing updater can pull code from the configured remote and reintroduce
upstream gating; keep that remote/branch on the modified fork. The existing
systemd-oriented update task is not redesigned into a Docker deployment tool.

Hosted APIs still need genuine provider API keys. `SECRET_KEY` is the local
HMAC authentication secret, not an OpenRouter key.
