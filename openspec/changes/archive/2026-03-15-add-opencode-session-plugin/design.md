## Context

OpenCode uses a plugin system (JS/TS) for extending functionality. Plugins are
installed as npm packages declared in `opencode.json` config under the `"plugin"`
key. The plugin SDK (`@opencode-ai/plugin`) defines a `Plugin` type that receives
context and returns a `Hooks` object.

GITS already has a `session_map.json` mechanism: CLI hooks write
`{tmux_session}:{window_id} -> {session_id, cwd}` entries, and the JSONL monitor
polls this file to pick up new session IDs for each binding.

## POC Findings

### Plugin Loading (verified with OpenCode 1.2.26)

1. **Directory-based loading does NOT work** in v1.2.26. Files in
   `~/.config/opencode/plugins/` or `.opencode/plugins/` are not auto-loaded,
   despite what the docs say.

2. **npm-based loading works**. Plugins must be declared in `opencode.json`:
   ```json
   "plugin": ["gits-session-hook@file:/path/to/plugin/dir"]
   ```
   The `@file:` version specifier prevents opencode from looking up npm registry.
   The directory must contain `package.json` with `"main"` pointing to the plugin.

3. **Plugin cache**: opencode uses `~/.cache/opencode/node_modules/` as its
   package cache. It runs `bun add --force` to install plugins there.

4. **Server lifecycle**: opencode runs a background server process. Plugins load
   only when the server starts. Killing and restarting opencode is required to
   pick up new plugins.

### Event System (verified)

- Plugins export named functions (not default export) matching the `Plugin` type
- The `event` hook receives `{ event }` where `event.type` is a string like
  `"session.created"`, `"session.updated"`, `"message.part.delta"`, etc.
- `session.created` fires when a new session is created (e.g., via `/new` command)
- Session object: `event.properties.info` with fields:
  - `id`: string (e.g., `"ses_3104a3afcffetf5tsoPKnSjaCl"`)
  - `directory`: string (working directory)
  - `projectID`, `title`, `version`, `time`, etc.
- `execSync` and Node.js `fs` modules work inside plugins (they run in Bun)
- `process.env.TMUX_PANE` is available when opencode runs inside tmux

### Session ID Format

OpenCode uses its own session ID format (`ses_...`), different from Claude's
UUID format. The JSONL monitor's session_map reader doesn't validate format,
so this works without changes.

## Goals / Non-Goals

- **Goal**: OpenCode sessions write to `session_map.json` on creation
- **Goal**: Auto-install the plugin on engine startup
- **Non-Goal**: Publish to npm (use `file:` protocol for local install)
- **Non-Goal**: Change OpenCode's output monitoring (directory polling works)

## Decisions

### Decision 1: Use `event` hook with `session.created` filter

The plugin SDK's `Hooks` interface has a generic `event` hook (not per-event
named hooks). We filter `event.type === "session.created"` inside the handler.

### Decision 2: npm plugin with `file:` protocol

Since directory auto-loading doesn't work in v1.2.26, we install via config:
```json
"plugin": ["gits-session-hook@file:/path/to/plugin"]
```

Auto-install modifies `~/.config/opencode/opencode.json` to add this entry.

### Decision 3: Use `execSync` for tmux (not `$` shell API)

The `$` Bun shell API from context is available but `execSync` from
`child_process` is simpler and doesn't require async handling for a single
command. Both work.

### Decision 4: ESM module (`.mjs`) with named export

Plugin uses `.mjs` extension and named export (`GitsSessionHook`) per the
plugin SDK's `Plugin` type contract.

## Risks / Trade-offs

- **Risk**: `file:` path is machine-specific (absolute path to source).
  **Mitigation**: Auto-install resolves the path dynamically based on the
  installed gits package location.

- **Risk**: opencode may add directory auto-loading in future versions,
  making the `file:` approach unnecessary.
  **Mitigation**: Both approaches can coexist; we can simplify later.

- **Risk**: Plugin only fires on `/new` (explicit new session), not when
  resuming an existing session.
  **Mitigation**: `_check_opencode_binding` in JSONL monitor already handles
  session discovery via directory scanning as a fallback.
