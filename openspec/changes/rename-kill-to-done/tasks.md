## 1. Fix `archive_thread` in `bot.py`

- [x] 1.1 Change `thread.edit(archived=True)` → `thread.edit(archived=True, locked=True)`
      at `src/gits/adapters/discord/bot.py:227`

## 2. Fix reply ordering in `handle_kill` / rename to `handle_done`

- [x] 2.1 Move `_reply(interaction, ...)` to before the `_kill_single` calls in
      `src/gits/core/engine.py` (currently lines 1004-1009 come after kill at 999/1002)
- [x] 2.2 Rename `handle_kill` → `handle_done` in `engine.py`
- [x] 2.3 Update docstring: "Handle /done — end work session, archive and lock thread"

## 3. Rename `/kill` command to `/done` in `bot.py`

- [x] 3.1 Rename `cmd_kill` → `cmd_done` at `src/gits/adapters/discord/bot.py:648`
- [x] 3.2 Change `name="kill"` → `name="done"` and update description to
      "End the work session and close this thread"
- [x] 3.3 Update the `handle_kill(...)` call → `handle_done(...)`

## 4. Update kill_wt button callbacks

- [x] 4.1 The worktree confirmation buttons use `callback_data="kill_wt_yes:{channel_id}"`
      and `"kill_wt_no:{channel_id}"` — kept these callback IDs unchanged (internal,
      not user-visible). Updated `handle_kill` → `handle_done` in the button handler
      at `engine.py:1373` and updated the status message text.

## 5. Update tests

- [x] 5.1 Rename `test_handle_kill` → `test_handle_done` in `tests/test_engine.py`
- [x] 5.2 Add assertion: `archive_thread` is called before `_reply` in the done flow
      (`test_done_reply_before_archive`)
- [x] 5.3 Add assertion: `archive_thread` is called with `locked=True`
      (`tests/test_bot.py::TestArchiveThread::test_archive_thread_locks`)
