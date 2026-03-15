# Tasks

## Phase 1: Core Multi-CLI (Prototype — DONE)

- [x] 1. Add resume templates for codex, copilot, opencode in `launcher.py`
- [x] 2. Implement `_discover_codex_sessions()` — scan `~/.codex/sessions/` JSONL files, match by `payload.cwd`
- [x] 3. Implement `_discover_copilot_sessions()` — scan `~/.copilot/session-state/` with `workspace.yaml` + `events.jsonl`
- [x] 4. Implement `_discover_opencode_sessions()` — scan `~/.local/share/opencode/storage/` project→session→message JSON
- [x] 5. Add CLI type dropdown to `/bind` command in Discord adapter
- [x] 6. Pass `cli` parameter through `handle_bind()` → `_create_bind()`
- [x] 7. Add `_append_permission_flag()` for per-CLI permission mode mapping
- [x] 8. Add Codex/Copilot JSONL file finders in `jsonl_monitor.py`
- [x] 9. Extend `extract_assistant_content()` to parse Codex JSONL format (`response_item/output_text`)
- [x] 10. Add `CodexApproval` UI pattern to `terminal_parser.py`
- [x] 11. Add `OpenCodePermission` UI pattern to `terminal_parser.py`
- [x] 12. Extend option regex to match Codex `›` marker
- [x] 13. Extend question regex for Codex/OpenCode approval markers
- [x] 14. Add unit tests for codex/opencode session discovery
- [x] 15. Add unit tests for copilot resume commands
- [x] 16. Validate all 242 tests pass

## Phase 2: Real-World Validation (DONE)

- [x] 17. Configure Codex CLI to use Azure OpenAI provider
- [x] 18. Configure OpenCode to use Azure model (`azure/gpt-5.4`)
- [x] 19. Verify Codex interactive mode works (Escape+Enter submit)
- [x] 20. Capture and validate Codex approval UI pattern from real terminal
- [x] 21. Capture and validate OpenCode permission UI pattern from real terminal
- [x] 22. Verify Codex JSONL assistant message extraction against real data
- [x] 23. Install Copilot CLI (`@github/copilot` v1.0.5) — uses GitHub token, no Azure needed
- [x] 24. Test Copilot interactive mode — submit via Escape+Enter, UI same as Claude Code (❯, ────)
- [x] 25. Copilot approval UI uses same `❯ 1. Yes` pattern as Claude — existing PermissionPrompt regex works
- [x] 26. Verify Copilot session discovery — `workspace.yaml` has `cwd:` field, `events.jsonl` has `user.message`/`assistant.message`
- [x] 27. Add Copilot JSONL parsing (`assistant.message` → `data.content`)
- [x] 28. Investigate OpenCode APIs — has REST API (`opencode serve`), `--format json` streaming, official SDK (`@opencode-ai/sdk`)

## Phase 3: OpenCode Output Monitoring (DONE)

- [x] 29. Implement directory-polling monitor for OpenCode `part/<msgID>/*.json` (track known files, detect new `type=text`)
- [x] 30. Parse OpenCode part JSON files for assistant text content
- [x] 31. Wire OpenCode dir-poll monitor into Engine polling loop (`_check_opencode_binding`)
- [ ] 32. Test end-to-end: send message via Discord → OpenCode processes → output appears in Discord
- [ ] 32a. (Optional) Add SDK/REST API enrichment alongside dir-polling for richer tool call data

## Phase 4: Polish (DONE)

- [x] 33. Handle Codex/Copilot input forwarding — `_submit_keys_for_cli()` + `send_text(submit_keys="Escape Enter")`
- [x] 34. OpenCode TUI uses Enter (standard) — no special handling needed
- [x] 35. Add hook installation for Copilot — `gits hook --install-copilot` writes `~/.copilot/hooks/hooks.json`
- [x] 36. Update project.md external dependencies section
