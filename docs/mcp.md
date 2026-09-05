# OpenRadiology MCP server

OpenRadiology speaks the [Model Context Protocol](https://modelcontextprotocol.io). Any MCP client
(Claude Desktop, Cursor, Windsurf, Claude Code, a local-model front-end) can render, measure, audit
and de-identify DICOM studies through one server that runs entirely on your machine. No pixel ever
leaves the workstation unless you return it to the model as an image; nothing is diagnosed by the
server itself.

## 30-second setup

```bash
pip install openradiology            # or: pip install -e . in a checkout
openrad doctor                       # dependencies, decoders, writable paths
openrad mcp --install claude-desktop --write --repo ~/radiology-workspace
```

Restart Claude Desktop. Type:

> Review the CT study in `/data/studies/2026-03-chest` and explain the result for the patient.

The model calls `openrad_doctor`, reads the thoracic CT checklist resource, opens a session, renders
every native slice in a 3x3 grid, looks at each page through `page_view`, zooms and measures the
candidates, fills the evidence ledger and finishes with two documents. Every claim in those documents
carries a `SOPInstanceUID`, native pixel coordinates and the page the model actually received.

Other clients:

| Client | Command |
|---|---|
| Cursor | `openrad mcp --install cursor --write` |
| Windsurf | `openrad mcp --install windsurf --write` |
| Claude Code | `openrad mcp --print --client claude-code` prints the `claude mcp add …` line |
| Anything else | `openrad mcp --print --client generic` prints the JSON `mcpServers` entry |

`--write` merges the entry into the client's config (a `.bak` copy is kept). Without `--write` the
snippet is printed. The entry uses the absolute Python interpreter (`python -m openrad mcp`), because
GUI clients rarely inherit a shell PATH; `--entrypoint` switches to the `openrad` executable. Add
`--lang tr` (or any locale) to pin the report language, and export `OPENRAD_PSEUDONYM_SALT` in the
client's environment block if you want the de-identification tool available.

The `--repo` directory is the review workspace: sessions live in `<repo>/.cache/create-report/`,
reports in `<repo>/reports/`. It does not have to be the source checkout; `OPENRAD_DOCS_ROOT` (set
automatically by the installer) tells the server where the checklists and references are.

## What the model gets

### Tools (21)

| Tool | Purpose |
|---|---|
| `openrad_doctor`, `openrad_config` | environment self-check; effective configuration with sources |
| `study_inventory` | series table with technical hints (tilt, multi-frame, PET units/decay/weight, thick sections); identifiers redacted |
| `session_open`, `session_list`, `session_status`, `session_plan` | evidence ledger lifecycle; progress dashboard with **technical alerts** (never anatomy); the reading plan as data |
| `render_ct`, `render_pet`, `render_mr` | contact sheets fitted to the model's vision budget; inline pages plus `resource_link`s for the rest |
| `zoom` | multi-slice magnification with native pixel grid, second plane; returned inline |
| `measure` | mm / HU / SUVbw / extents / region growing with guideline diameter; write-once evidence with SHA-256 |
| `session_register`, `page_view` | register renders; **viewing a page is the only way to mark it reviewed** |
| `session_set_series`, `session_set_region`, `session_add_claim`, `session_set_meta` | ledger writes with immediate, object-scoped validation feedback |
| `session_check`, `session_finish` | full validation; the two Markdown documents returned as text and written to disk |
| `anonymize` | PS3.15 de-identification; the salt is taken from the server environment, never from the model |

Tool errors come back as results with `isError: true` and the engine's exit code, so the model can
reason about them (`4` geometry → re-render with `allow_tilt`, `6` quantitation → ask for the weight).

### Resources

```
openrad://checklist/{ct_thorax|pet_ct|brain_mri|lessons|blind_audit}
openrad://references                 guidelines and DOIs behind every default
openrad://skill                      the agent protocol
openrad://template/{lang}            report and evidence specification
openrad://schema/session             JSON Schema of the ledger
openrad://config                     effective settings
openrad://sessions                   sessions in the cache
openrad://session/{id}               ledger JSON
openrad://session/{id}/status        progress, alerts, validation summary
openrad://session/{id}/page/{name}   rendered PNG (blob)
openrad://session/{id}/evidence/{f}  measurement evidence
```

### Prompts

`review_study`, `compare_studies`, `explain_for_patient`, `blinded_audit` put the model into the
protocol with the three rules and the concrete tool order. In Claude Desktop they appear in the
prompt picker; in Cursor as slash-style templates.

## How the protocol enforces the review discipline

* **Only `page_view` marks a page reviewed.** The tool returns the PNG as an MCP image block and
  flips `reviewed: true` in the same step, after verifying the file hash. There is no other setter.
  `session_check` refuses to finish until every slice of every read series appears on a viewed page.
* **Immediate feedback.** `session_set_region` and `session_add_claim` run the full validator and
  return only the errors that concern the object just written (missing refs, page from another
  study, measurement hash mismatch). The model fixes evidence while the image is still in context.
* **Technical alerts, never diagnoses.** `session_status` reports tilt, unsupported series, SUV
  warnings, unrendered or unviewed slices and a pending comparison verdict. It has no opinion on
  anatomy; that stays with the reader.
* **Numbers only from `measure`.** With a session, every measurement becomes a write-once JSON
  evidence file; the tool returns its SHA-256 and a ready-to-fill `measurement_template` for the claim.
* **The CLI is the implementation.** Each tool builds an `openrad` argv and runs it in-process with
  stdout captured (stdout is the transport). Whatever a human gets from the shell, the model gets
  through the protocol, byte for byte.
* **Engine progress becomes MCP logging.** Renderer and loader messages are forwarded as
  `notifications/message`, so a client can show what is happening during a long render.

## Image delivery

Sheets are never down-sampled; the grid shrinks to fit the vision budget (`render.vision_profile`
or `--vision`). `render_*` returns `inline` pages as base64 PNG (default 1, max 6, total budget
6 MB) and the remaining pages as `resource_link`s that `page_view` turns into images one at a time.
This keeps the conversation responsive while still forcing the model to look at every page before
it can claim coverage.

## Running without a client

```bash
openrad mcp --list-tools                 # catalogue
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | openrad mcp
```

The server is dependency-free (JSON-RPC over newline-delimited stdio, protocol version
`2025-06-18`, also accepting `2025-03-26` and `2024-11-05`). It runs on Python 3.9+ and inside
restricted clinical environments. The transport-agnostic core (`McpServer.handle(dict) -> dict`) is
what the synthetic test suite drives (`tests/test_mcp.py`).

## Security notes

* The server accesses the local file system with the launching user's rights. Run it under the
  account that is allowed to see the studies, and point `--repo` at a workspace with appropriate
  retention rules.
* Identifiers are redacted from inventories by default (`privacy.redact_identifiers`). Pixels leave
  the machine only inside image blocks you let the client send to a model; use `anonymize` first
  when the model is remote and policy requires it.
* The pseudonymisation salt is read from `OPENRAD_PSEUDONYM_SALT` in the server process only; no
  tool argument can supply it.
* Nothing is written outside the session work directory, the configured output directory, an
  explicit `output` argument or the client config you asked `--write` to edit.
