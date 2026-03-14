## 1. Output Monitor — Pane Polling
- [ ] 1.1 Implement `PanePoller` in `src/gits/core/monitor.py` — periodic `tmux capture-pane` diffing, detect new output lines
- [ ] 1.2 Detect status line changes (spinner animation, working state text)
- [ ] 1.3 Callback system to notify engine of pane changes
- [ ] 1.4 Integrate into engine lifecycle (start/stop with bind/unbind)
- [ ] 1.5 Tests for pane polling and diff detection

## 2. Output Monitor — JSONL Polling
- [ ] 2.1 Implement `JsonlPoller` in `src/gits/core/monitor.py` — watch `~/.claude/projects/<hash>/*.jsonl` with byte-offset tracking
- [ ] 2.2 Parse JSONL events: assistant.text, tool_use, tool_result
- [ ] 2.3 mtime cache to skip unchanged files
- [ ] 2.4 Callback to engine with structured output events
- [ ] 2.5 Tests for JSONL parsing and incremental reads

## 3. Terminal UI Detection
- [ ] 3.1 Implement `TerminalParser` in `src/gits/core/terminal_parser.py` — regex matching for Claude Code interactive prompts
- [ ] 3.2 Detect: PermissionPrompt, AskUserQuestion, BashApproval, ExitPlanMode, RestoreCheckpoint
- [ ] 3.3 Detect idle/busy/waiting states from status line
- [ ] 3.4 Tests for each prompt type detection

## 4. Terminal UI Bridge
- [ ] 4.1 Implement `UIBridge` in `src/gits/core/ui_bridge.py` — convert detected prompts to button layouts
- [ ] 4.2 On prompt detection: auto-screenshot + push screenshot with navigation keyboard to Discord
- [ ] 4.3 Handle button clicks → send corresponding keys to tmux → re-screenshot after 500ms
- [ ] 4.4 Tests for button mapping and click handling

## 5. Message Formatting
- [ ] 5.1 Implement `MessageChunker` in `src/gits/adapters/discord/formatter.py` — split at 2000 chars
- [ ] 5.2 Code fence awareness: auto-close/reopen fenced code blocks across chunks
- [ ] 5.3 Tool use formatting: "🔧 Using `<tool>`..." prefix
- [ ] 5.4 Tests for chunking edge cases (mid-codeblock, mid-line, empty)

## 6. Discord Buttons & Navigation
- [ ] 6.1 Implement screenshot navigation keyboard in `src/gits/adapters/discord/buttons.py` — Esc, arrows, Enter, Ctrl-C, Space, Tab, Refresh
- [ ] 6.2 Interrupt/Abort buttons on output messages
- [ ] 6.3 Wire button interactions in DiscordAdapter → engine → tmux
- [ ] 6.4 Refresh button: re-screenshot without sending keys

## 7. Streaming Message Updates
- [ ] 7.1 Implement debounced message editing in DiscordAdapter — batch output updates, edit existing message instead of sending new ones
- [ ] 7.2 Rate limit handling (Discord 429) with exponential backoff
- [ ] 7.3 Fallback to new message if edit fails or content exceeds limit

## 8. Claude Code Hook Enhancement
- [ ] 8.1 Enhance `gits hook` to read session info from stdin JSON (Claude Code passes context)
- [ ] 8.2 Map TMUX_PANE → session_id in `~/.gits/session_map.json`
- [ ] 8.3 Notify running bot process via file watch or IPC
- [ ] 8.4 Engine updates binding with discovered session ID and notifies Discord channel

## 9. Integration
- [ ] 9.1 Wire OutputMonitor into Engine — start monitors on bind, stop on unbind/kill
- [ ] 9.2 Route monitor events through formatter → Discord adapter
- [ ] 9.3 End-to-end manual testing with real tmux + Discord
