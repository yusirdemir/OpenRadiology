# OpenRadiology Desktop

A single-window application for macOS and Windows that puts the engine behind a
viewer, without changing what the engine does or what it refuses to do.

The engine does not interpret images. It renders faithfully, measures with
native calibration, and declines to finish a report whose claims do not cite a
slice. The desktop application adds three things: a viewer fast enough to
actually look with, an interface where a finding and its address and its
measured number are one object, and the one guarantee a command line cannot
give — that a page marked reviewed was really put on a screen.

---

## Running it

### From a checkout

```bash
pip install -e ".[codecs]"        # the engine and the compressed-DICOM decoders
cd app && npm install
npm run tauri dev                 # spawns the sidecar, opens the window
```

`npm run tauri dev` starts the Python sidecar itself. To work on the front end
in a plain browser instead, run the sidecar by hand and let the Vite dev server
proxy to it:

```bash
python -m openrad.server          # prints {"port": ..., "token": ...}
cd app && npm run dev             # http://127.0.0.1:5273
```

The dev server finds the sidecar through the same handshake file the MCP bridge
uses, and attaches the Authorization header itself, so the token never reaches
the page.

### Building an installer

```bash
python -m pip install pyinstaller
pyinstaller --noconfirm packaging/openrad-server.spec \
            --distpath packaging/dist --workpath packaging/build
cp -R packaging/dist/openrad-server app/src-tauri/resources/engine
cd app && npm run tauri build
```

The engine is bundled as a PyInstaller one-dir tree under `Resources`, not as a
one-file binary: one-file re-extracts about eighty megabytes on every launch,
which shows up as several seconds of dead time before the first window.

---

## Architecture

```
┌─ Tauri v2 (Rust) ─────────────────────────────────────────────┐
│  window · native dialogs · sidecar supervision · handshake     │
│                                                                │
│  ┌─ WebView (React + TypeScript + WebGL2) ─┐                   │
│  │   HTTP/JSON  ·  SSE  ·  binary slices   │                   │
│  └───────────────┬─────────────────────────┘                   │
└──────────────────┼─────────────────────────────────────────────┘
                   │  127.0.0.1:<OS-assigned>  ·  bearer token
        ┌──────────▼───────────────────────────────────┐
        │  openrad-server                              │
        │  VolumeCache · measure · create_report ·     │
        │  renders as child processes · MCP endpoint   │
        └──────────────────────────────────────────────┘
```

### Why a resident sidecar

The expensive object is the decoded volume. A 376-slice thoracic CT is about
400 MB of float32, and takes roughly half a second to decode. A design that ran
a command per interaction would pay that on every click. The sidecar decodes
once and keeps volumes resident under an LRU byte budget (`--budget-mb`,
default 4 GB).

Measured on a real 376-slice study: 495 ms to decode, 10.6 ms median per slice
request, 11 ms for a coronal reformat, 12 ms for a 10 mm slab MIP.

### Why the numbers stay in Python

Rescale slope and intercept, gantry-tilt validation, SUV conversion and region
growing live in one place. A second implementation in Rust or JavaScript would
eventually disagree with the evidence files the engine writes, and the
disagreement would be silent. So the application never computes a clinical
number: measurement requests go through `measure.build_parser` and
`measure.run`, exactly as the command line does, and the interface displays
what comes back together with the SHA-256 of the evidence file it was written
to.

The one number the interface reads for itself is the value under the cursor,
which is a readout of the very samples the sidecar sent, labelled as such and
never written to the ledger.

### Why windowing happens on the GPU

A slice arrives once as raw calibrated samples — Hounsfield units, SUV, MR
signal — and is uploaded to a single-channel float texture. Window width and
level are shader uniforms, so dragging them costs one uniform write per frame:
no decode, no request, no copy.

Slices travel as `int16` where the rescale is integral (the usual CT case) and
`float32` otherwise. The choice is lossless either way and is announced in the
`X-Dtype` header; the viewer never quantises silently.

Nearest-neighbour filtering is used only past four screen pixels per image
pixel, where a reader is deliberately inspecting individual samples. Below
that it is actively harmful: a thin-slice low-dose CT carries real noise around
70 HU standard deviation in soft tissue, and replicating each sample into a
1.65 × 1.65 block turns that noise into a coarse speckle that buries
low-contrast findings.

---

## View attestation

`skills/openrad/SKILL.md` states the limitation of the terminal workflow
plainly:

> Marking a page `reviewed: true` without opening it is falsification;
> `openrad check` cannot detect it, so you must not do it.

A command-line tool cannot detect it because it never draws the pixels. A
desktop application does draw them.

Every image the renderer puts on screen produces a view event. An event becomes
an attestation only if all three hold:

| Condition | Default | Why |
|---|---|---|
| uninterrupted time as the displayed image | 400 ms | long enough to exclude a scroll blur, short enough not to punish a fast reader |
| screen pixels per image pixel | ≥ 1.0 | below one the image is downsampled and a small finding may not have survived to the screen |
| window focused and document visible | required | "on screen while I read mail" is not looking |

Attestations are appended to `attestations.jsonl` beside the session.
`session.json` keeps schema version 2 untouched, so `openrad check` behaves
identically whether a session was driven from the terminal or the window, and
the two can share a session.

The server sets `reviewed` — the interface never does — and clears any
`reviewed` flag no attestation covers, reporting each refusal so the reader is
told which page still needs looking at.

**What an attestation means, exactly:** the application displayed this image
while the window had focus. It does not mean the viewer understood it, and it
is not a substitute for competence. It removes one specific failure: claiming
to have looked without looking.

---

## The MCP bridge

The application carries no language model and asks for no API key. Instead the
reader's existing MCP client drives the session they are looking at.

```bash
openrad-server --bridge --status     # what the bridge can see
```

Configure the client to run `openrad-server --bridge` (or, from a checkout,
`python -m openrad.server --bridge`). The shim reads the handshake file, proxies
JSON-RPC to the running window, and falls back to the ordinary in-process
server when no window is running — so a configured client never breaks, it just
loses the gate.

### The display gate

Through the bridge, `page_view` is intercepted:

1. the request is handed to the window, which shows the sheet at natural
   resolution — never fitted, since a shrunken contact sheet attests nothing;
2. the reader looks at it, or declines;
3. the tool runs only once an attestation covering that page exists.

A hidden window is an error the agent is told to ask about. A sheet that was
opened but not dwelled on is refused. The reader sees every tool call in the
agent panel as it happens.

Handshake file, owner-readable only, removed when the application exits:

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/OpenRadiology/bridge.json` |
| Windows | `%APPDATA%\OpenRadiology\bridge.json` |
| Linux | `$XDG_STATE_HOME/openradiology/bridge.json` |

---

## Security and privacy

- The sidecar binds `127.0.0.1` on an OS-assigned port and opens no outbound
  connection. There is no network feature to disable.
- Every request carries a bearer token, fresh per launch, compared in constant
  time. Origins are restricted to the Tauri and loopback dev origins.
- File serving is confined to the folders opened in the session, by real-path
  containment.
- The token is injected into the web view as an initialisation script before
  any page code runs, so there is no command that hands it out, and images are
  fetched as authenticated blobs rather than through URLs that would carry the
  token into history.
- Review sessions are written to the application's own workspace, never inside
  the DICOM archive.
- The frozen binary is also the CLI (`openrad-server --cli ...`) and the bridge
  (`--bridge`), so background jobs run byte-identical commands to a terminal.

---

## Layout

```
openrad/            engine, unchanged
openrad/server/     sidecar: caches, jobs, attestation ledger, MCP endpoint
app/src/            React front end
app/src/gl/         WebGL2 renderer and the millimetre-based view transform
app/src-tauri/      Rust shell
packaging/          PyInstaller specification for the frozen engine
```

## Keyboard

| Key | Action |
|---|---|
| wheel / ↑ ↓ | previous and next slice |
| Page Up / Page Down | ten slices |
| shift + wheel | zoom about the cursor |
| right-drag, or alt-drag | window width and level |
| 1 / 2 / 3 | lung, soft tissue, bone |
| g | pixel grid |
| Esc | cancel the measurement in progress |
