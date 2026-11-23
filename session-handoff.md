# Session handoff

## Frontend demo — 2026-11-17
- Added buildless Vietnamese UI under `electrical_design/web/`: project upload/
  reopen, per-room questionnaire, WebSocket activity/cancel, bbox SVG preview,
  proposal table/JSON, global validation, explicit human approval and IFC download.
- Static shell/assets are public so token entry is reachable; project data,
  diagnostics and geometry remain protected by existing API authentication.
- Added browser WebSocket token subprotocol (base64url, no token in URL); header
  clients remain supported. Tokens stay in tab memory only.
- Added `python -m electrical_design preview --port 8001`: UI-only app without
  credentials or write routes, with clearly labeled sample data and disabled writes.
- Manual form preserves other rooms/building requirements, stable answer IDs,
  unknown power, and advanced multi-answer data. Checklist projects are read-only
  in this basic form to avoid replacing their richer submission format.
- Final verification: 6 JavaScript unit tests passed; local Python suite excluding
  the unchanged large IFC tests passed 40 (4 live tests skipped, 2 IFC deselected).
  Ruff lint/format passed (77 Python files). Wheel built and HTML/CSS/JS assets
  verified inside it. HTML has 54 unique IDs; all 47 static JS ID references resolve.
- No required implementation work remains for this basic frontend. Browser visual
  verification remains unavailable for the permission reason below.
- Preview CLI was tested with a missing .env file; it starts the UI-only app
  without consulting credentials. CLI Vietnamese help now prints as UTF-8 even
  in redirected Windows terminals.
- Browser QA was attempted via CUA but permission to visit localhost was declined.
  Do not retry via another browser surface, CLI browser, or screenshot workaround.
  No visual browser verification has been claimed. Continue code/API checks only.
- No Supabase writes, live LLM requests or remote deployment done for this feature.
- See `docs/frontend-demo.md` for operation and explicit 2D/preview limitations.

## Upload incident follow-up — 2026-11-17
- User's `POST /projects` returned 500; curl then attempted to JSON-parse plain
  `Internal Server Error`. No successful live upload has been claimed.
- Read-only Supabase probes with current `.env`: projects/project_files columns
  readable; placement revisions return 42501; questionnaire revisions return
  PGRST205; bucket lookup returns Bucket not found. Bucket/schema absence should
  be rechecked with a privileged key because the current key is publishable.
- Confirmed locally (without printing it) SUPABASE_KEY is `sb_publishable_*`.
  User must replace it with a backend `sb_secret_*` or legacy service_role key.
  Do not grant anon/public broader rights as a workaround.
- Added server-key format checking before default API startup; standalone
  read-only Playbook clients retain public-key compatibility.
- Added `python -m electrical_design check-backend`: read-only schema/bucket
  checks, no credentials/records printed and no remote mutations.
- Added structured HTTP 503 responses for database/storage/network errors and
  preserved original upload errors if failed-status cleanup also fails.
- Verified 23 focused tests (new readiness tests + existing API/writer regressions)
  passed; Ruff lint and format passed (74 Python files).
- Required user action: update local SUPABASE_KEY, run check-backend, apply only
  missing migrations/create private bucket if necessary, restart backend and retry.
- No remote bucket, permission policy or migration was changed during diagnosis.

## Final status — 2026-11-17
- Device-placement implementation and verification are complete for the documented MVP.
- Read `docs/backend-pipeline.md` for startup/contracts/limits and
  `docs/verification-report.md` for exact evidence.
- 32 test cases covered: final fast run `pytest -q --live -m 'not ifc'`
  passed 30, plus 2 real IFC tests passed. Previous full run passed 31 before
  adding the upload regression. Source `ctr.ifc` remained unchanged.
- Ruff lint passed; 72 Python files formatted; compile checks and wheel build passed.
- `docs/tool-schemas.json` contains 30 exported schemas; IfcMCP is optional and
  privileged approval/apply/mutation operations are excluded from the agent.
- No implementation work remains within the documented device-placement scope.
- Deployment prerequisites: set LLM_MODEL and apply Supabase migrations. No live
  LLM proposal or live PostgreSQL migration execution has been claimed.

## Scope and decisions
- Finish the device-placement backend; the LLM proposes positions. Do not expose or implement `engine_plan_placements`.
- Read `docs/architecture-consultation.md` and `docs/tools_list.md`.
- Keep approval and IFC application outside LLM tools. Preserve the original `ctr.ifc`; test with temporary copies.
- Existing untracked user files: `docs/architecture-consultation.md`, `docs/tools_list.md`, `test/`. Preserve them.

## Completed: initial architecture audit
- Read existing IFC reader/writer, validation, questionnaire, providers and persistence contracts.
- Found empty orchestrator, agent loop, context, API, memory and utility modules.
- Found missing placement-revision migration, missing LLM settings, incomplete package/dependency declarations.
- Existing tests rely on a live Supabase and real IFC; add isolated regression coverage alongside explicit live checks.

## In progress / next
1. Establish repeatable test and formatting tooling.
2. Complete IFC reading and tool registry with detailed descriptions and project-bound paths.
3. Implement persistent orchestrator, bounded agent loop and review/apply API.
4. Repair write/validation contracts and add database migration.
5. Run tests, IFC round trips, formatter/linter and document operation and remaining limitations.

## Completed: core implementation (awaiting regression verification)
- Added schema-validated tool registry; project-bound native IFC inspection and optional official IfcMCP read adapter.
- Added durable SQLite state/chat, bounded agent loop, phase prompts, context builder and Supabase-bound runtime.
- Added FastAPI upload/state/download, agent REST/WebSocket and exact-version human approval API.
- Added project/file schema and placement-revision migration with database-side serialization and immutable proposals.
- Fixed missing LLM settings, Groq assistant history, IFC room storey lookup, orientation mismatch.
- Approval and application now rerun validation rather than trusting client-supplied results.
- Next: verify tests and live integrations; finish remaining utility modules and docs; format/lint all source.

## Environment
- Windows PowerShell; repo `D:\AI AGENT\MVP\MVP`.
- Python 3.12 in `.venv\Scripts\python.exe`; IFCOpenShell and FastAPI installed.
- Never print `.env` or secrets.

## Completed: regression coverage and real IFC integration
- Isolated tests now exercise questionnaire revision identity, demand/no-double-counting,
  project-bound tool registry, native IFC reads, all 10 IfcMCP queries, REST/WebSocket,
  exact-version approval, create/update/remove, replay, forged validation and upload failure.
- A scripted LLM drives the REAL PlacementBackend → Orchestrator → tools → IFC writer
  pipeline successfully (remote storage is a double).
- Real `ctr.ifc` round-trip test passed in the full suite; no changes to the source IFC.
- Installed official IfcMCP from commit `7ab6fca86733a5cda959114e56244eee0a4ec822`.
  Distribution name is `ifcopenshell-mcp`, Python import is `ifcmcp`; optional extra and lockfile added.
- Four Supabase read-only tests passed before pagination changes; final suite exposed
  that 3 Playbook tables have semantic primary keys rather than `id`. Pagination now
  orders by the correct keys; rerun live tests before final delivery.
- All Python source formatted with Ruff. Lint passed before the latest small edits.
- Local Windows sandbox hangs on asyncio's internal sockets. Run async/API tests with
  approved escalation. Do not mistake sandbox hangs for an agent-loop defect.
- PostgreSQL migrations have not been applied/executed: Docker daemon is unavailable.
- `.env` has provider keys but no LLM_MODEL. No paid live LLM call has been made.

## Completed: final integration and delivery
- Corrected pagination to use each real Playbook table's primary key; all four
  Supabase reads passed again.
- Updated `docs/tools_list.md` and exported schema JSON from the actual registry.
- Added upload/download regression; invalid IFC headers now return HTTP 422.
- Added CLI `serve` and schema-export entry point; completed previously empty modules.
- Built and inspected the wheel; secrets, IFC files and cache are excluded.
- Stopped the three sandbox-only test processes that hung on Windows async sockets.
- Future panel/routing phases remain intentionally unavailable; preview utilities
  are implemented but never presented as verified electrical design.
