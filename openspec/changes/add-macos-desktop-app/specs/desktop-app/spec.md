## ADDED Requirements

### Requirement: macOS Desktop Application
The system SHALL provide a macOS desktop application (.app bundle, Apple Silicon only) that packages everything needed to run and requires no technical setup — users just download, open, and start using it.

#### Scenario: First launch on a fresh Mac
- **WHEN** user opens GITS.app for the first time on a Mac with no prior setup
- **THEN** the app launches and presents a friendly welcome wizard
- **AND** no additional software installation is required (no Homebrew, no command-line tools)

#### Scenario: Everything is included in the download
- **WHEN** the app is downloaded as a .dmg or installed via Homebrew Cask
- **THEN** all required components are included inside the app
- **AND** the total download size is under 150MB

### Requirement: Pure CSS Frosted Glass UI
The system SHALL render its user interface with a frosted glass aesthetic — translucent panels, soft blurs, and subtle depth — implemented entirely in pure CSS for future cross-platform portability. No macOS-specific visual APIs are used.

#### Scenario: App displays frosted glass styling
- **WHEN** the app window is open
- **THEN** panels, cards, and chat bubbles show a frosted glass look with blur, transparency, and soft edge highlights
- **AND** the visual style is consistent and elegant throughout the app

#### Scenario: Appearance follows system Light/Dark mode
- **WHEN** the user switches between Light and Dark mode in System Settings
- **THEN** the app automatically adapts its color palette (light glass tints in Light mode, darker tints in Dark mode)

#### Scenario: UI works identically on other platforms
- **WHEN** the same frontend is used on a different operating system in the future
- **THEN** the frosted glass styling renders the same without platform-specific changes

### Requirement: Activity Dashboard
The system SHALL display a dashboard showing the user's active workspaces, AI assistant status, and Discord connection — presented in a clear, at-a-glance layout.

#### Scenario: Dashboard shows active workspaces
- **WHEN** the user has active workspaces (local or Discord-connected)
- **THEN** the dashboard lists each workspace with its project folder, AI assistant type (Claude, Codex, etc.), and current status (working/idle/error)

#### Scenario: Dashboard shows Discord status
- **WHEN** Discord is connected
- **THEN** the dashboard shows a green dot with the bot name and connected server count
- **WHEN** Discord is not set up
- **THEN** the dashboard shows "Discord: not connected" with a "Set up" link

### Requirement: Auto-Update
The system SHALL automatically check for updates and let users install new versions with one click.

#### Scenario: New version available
- **WHEN** a new version is released
- **AND** the app checks for updates (at launch or periodically)
- **THEN** the user sees a friendly notification with what's new
- **AND** can update with a single "Install Update" button

### Requirement: Advanced Mode (Terminal View)
The system SHALL provide an optional "Advanced Mode" toggle in Settings that reveals the underlying terminal session (via embedded xterm.js), giving power users direct visibility into the tmux layer. This mode is hidden by default and not mentioned in the welcome wizard.

#### Scenario: Power user enables advanced mode
- **WHEN** the user opens Settings → Advanced and enables "Show Terminal"
- **THEN** a "Terminal" tab appears in the sidebar
- **AND** clicking it shows a live terminal view of the active workspace's tmux session

#### Scenario: Advanced mode is hidden by default
- **WHEN** the user has not enabled advanced mode
- **THEN** no terminal-related UI is visible anywhere in the app
- **AND** the app behaves as a pure chat interface

### Requirement: Menu Bar Icon
The system SHALL show an icon in the macOS menu bar so the app can run quietly in the background and be quickly accessed.

#### Scenario: App stays running when window is closed
- **WHEN** the user closes the main window
- **THEN** the app continues running with a small icon in the menu bar
- **AND** clicking the icon shows a quick menu with status info and common actions (open app, start/stop AI, quit)
