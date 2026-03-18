## ADDED Requirements

### Requirement: Session Summary in Info Output
The `/info` command SHALL display the human-readable session summary alongside the raw session ID, using the same summary text that appears as the label in the `/bind` dropdown, so users can identify the correct session when re-binding.

#### Scenario: Session summary shown when available
- **WHEN** the user runs `/info` on a bound channel
- **AND** `binding.cli_session_id` is set
- **AND** a matching `CLISession` can be found by the launcher's session scanner
- **THEN** the info output includes a **Session summary** line containing the session's human-readable summary (e.g. `Session summary: "Add dark mode toggle"`)
- **AND** the summary text matches exactly what would appear as the label in the `/bind` session picker dropdown

#### Scenario: Session summary unavailable — graceful fallback
- **WHEN** the user runs `/info` on a bound channel
- **AND** `binding.cli_session_id` is set
- **BUT** no matching `CLISession` is found (session file deleted or not yet scanned)
- **THEN** the **Session summary** line is omitted (no error shown)
