## Context
MVP supports send-to-tmux and manual screenshots. Users have no way to see output without `/screenshot`. Phase 2 adds the reverse data path: tmux → Discord, with intelligent formatting and interactive UI bridging.

## Goals / Non-Goals
- Goals:
  - Auto-push coding CLI output to Discord in near-realtime
  - Surface interactive prompts as Discord buttons
  - Handle Discord's 2000-char message limit gracefully
  - Avoid message flooding with debounced edits
- Non-Goals:
  - Telegram adapter (Phase 3)
  - Multi-user permission management (Phase 4)
  - Perfect real-time streaming (polling is acceptable at 2s intervals)

## Decisions
- **Dual-channel monitoring** (JSONL + pane polling): JSONL provides structured data (assistant text, tool calls); pane polling provides real-time terminal state and UI detection. Both are needed — JSONL alone misses interactive prompts, pane alone misses structured output.
  - Alternatives: SDK streaming (rejected — violates tmux-first principle), inotify/fsevents (overkill for MVP, platform-specific)
- **Polling intervals**: 2s default for both channels (configurable via PANE_POLL_INTERVAL / JSONL_POLL_INTERVAL). Balances responsiveness vs. resource usage.
- **Message editing over flooding**: Use Discord message edit to update in-progress output. New message only when topic changes (new tool call, user prompt detected).
  - Debounce: 300ms minimum between edits (Discord rate limit friendly)
- **Screenshot-based UI bridge**: When interactive prompt detected, send screenshot + button grid. Simpler than parsing prompt text into Discord-native UI, and preserves full terminal visual context.
- **Code fence-aware chunking**: When splitting at 2000 chars, track open code fences and auto-close/reopen across chunks. Mirrors claude-on-discord's chunker.ts approach.

## Risks / Trade-offs
- **Polling latency**: 2s delay is noticeable but acceptable. Mitigation: users can `/screenshot` for instant view.
- **JSONL format coupling**: Parsing Claude Code's JSONL format ties us to its schema. Mitigation: JSONL poller is isolated; pane polling works for any CLI regardless.
- **Discord rate limits**: Rapid edits can hit 429. Mitigation: exponential backoff + debounce.
- **Screenshot rendering cost**: Each UI detection triggers a PNG render (~50-100ms). Mitigation: already runs in thread pool via `asyncio.to_thread()`.

## Open Questions
- Should JSONL polling be opt-in (only for Claude Code) or always-on?
- Should we support configurable debounce intervals?
