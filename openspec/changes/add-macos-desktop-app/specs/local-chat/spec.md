## ADDED Requirements

### Requirement: Built-in Chat
The system SHALL provide a built-in chat interface where users can talk to their AI assistant directly — no Discord required. This is the primary way most users will interact with the app.

#### Scenario: User chats without Discord
- **WHEN** the user hasn't set up Discord (or doesn't want to use it)
- **AND** types a message in the chat
- **THEN** the message is sent to the AI assistant
- **AND** the response appears in the conversation with proper formatting (rich text, code blocks with syntax colors)

#### Scenario: User starts a new workspace
- **WHEN** the user clicks "New Chat" or "+" in the app
- **AND** picks a project folder
- **THEN** a new AI workspace starts in that folder
- **AND** the chat is immediately ready for conversation

#### Scenario: Chat works alongside Discord
- **WHEN** the user has both the app and Discord connected to the same workspace
- **THEN** they can send messages from either place
- **AND** responses from both show up in the app's chat view

### Requirement: Beautiful Conversation View
The system SHALL display conversations in an elegant, modern chat layout with frosted glass styling, smooth animations, and clean typography — designed to feel premium and easy to read, not like a code terminal.

#### Scenario: AI responses look polished
- **WHEN** the AI responds
- **THEN** the message appears in a frosted glass bubble with a gentle fade-in animation
- **AND** code snippets show syntax highlighting with a one-click "Copy" button
- **AND** formatting like headings, bullet lists, bold text, and links all render beautifully

#### Scenario: Message input feels natural
- **WHEN** the user clicks the input area
- **THEN** it shows a clean text field with a subtle hint ("Ask anything...")
- **AND** pressing Enter sends the message, Shift+Enter adds a new line
- **AND** files can be dragged and dropped into the input area

### Requirement: Workspace Management
The system SHALL let users create, switch between, resume, and close workspaces entirely from the app — Discord is completely optional.

#### Scenario: User manages workspaces in the app
- **WHEN** the user opens the "Workspaces" section in the sidebar
- **THEN** they see all their workspaces with project name, AI assistant type, and when it was last active
- **AND** can start new workspaces, switch to existing ones, resume previous conversations, or close workspaces they're done with
