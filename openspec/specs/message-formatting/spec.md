# message-formatting Specification

## Purpose
TBD - created by archiving change add-phase2-discord-interactivity. Update Purpose after archive.
## Requirements
### Requirement: Message Chunking
The system SHALL split messages exceeding Discord's 2000-character limit into multiple messages.

#### Scenario: Long text split
- **WHEN** output text exceeds 2000 characters
- **THEN** the system SHALL split it into multiple messages, each under 2000 characters, preferring line boundaries

#### Scenario: Code fence awareness
- **WHEN** a split point falls inside an open code fence
- **THEN** the system SHALL close the fence at the end of the current chunk and reopen it at the start of the next chunk

#### Scenario: Short text unchanged
- **WHEN** output text is under 2000 characters
- **THEN** the system SHALL send it as a single message

### Requirement: Tool Use Formatting
The system SHALL format tool_use events with a recognizable prefix.

#### Scenario: Tool call displayed
- **WHEN** a tool_use event is received from JSONL monitoring
- **THEN** the system SHALL format it as "🔧 Using `<tool_name>`..." with optional summary

### Requirement: Streaming Message Edits
The system SHALL update existing Discord messages with new content instead of sending new messages, using debounced edits.

#### Scenario: Output appended to existing message
- **WHEN** new output arrives within the debounce window (300ms) of a previous edit
- **THEN** the system SHALL batch the updates and edit the existing message once

#### Scenario: Rate limit hit
- **WHEN** a Discord 429 rate limit response is received
- **THEN** the system SHALL retry with exponential backoff

#### Scenario: Edit exceeds limit
- **WHEN** editing would cause the message to exceed 2000 characters
- **THEN** the system SHALL send a new message with the overflow content

