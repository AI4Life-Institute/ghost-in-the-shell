# CLI Approval Detection

## ADDED Requirements

### Requirement: Codex approval UI detection
The terminal parser MUST detect Codex approval prompts matching `Would you like to run the following command?` (top) and `Press enter to confirm or esc to cancel` (bottom), extracting numbered options like `› 1. Yes, proceed (y)`.

#### Scenario: Codex command approval detected
Given terminal output containing "Would you like to run the following command?" and numbered options
When `extract_interactive_content()` is called
Then a `CodexApproval` UIContent is returned with the prompt region

#### Scenario: Codex options extracted
Given a detected CodexApproval region with `› 1. Yes, proceed (y)` style options
When `extract_prompt_options()` is called
Then options are returned with correct numbers and labels

### Requirement: OpenCode permission UI detection
The terminal parser MUST detect OpenCode permission prompts matching `△ Permission required` (top) and `Allow once   Allow always   Reject` (bottom).

#### Scenario: OpenCode permission prompt detected
Given terminal output containing "△ Permission required" and "Allow once   Allow always   Reject"
When `extract_interactive_content()` is called
Then an `OpenCodePermission` UIContent is returned

## MODIFIED Requirements

### Requirement: Option regex supports multiple CLI markers
The option extraction regex MUST match both `❯` (Claude) and `›` (Codex) as option markers.

#### Scenario: Codex option marker matched
Given a line `› 1. Yes, proceed (y)`
When the option regex is applied
Then option number 1 with label "Yes, proceed (y)" is extracted
