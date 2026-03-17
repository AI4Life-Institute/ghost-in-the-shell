## MODIFIED Requirements

### Requirement: Navigation Modes
The desktop UI sidebar SHALL provide exactly three navigation modes: **Code**, **Agents**, and **Library**.

- Code mode opens the terminal pane view (tmux sessions); behavior is unchanged.
- Agents mode opens the Agent Dashboard view.
- Library mode opens the Library view with Skills and Data sub-tabs.
- The previous four-mode layout (Code / Agent / Skill / Data) is replaced by this three-mode layout.
- Skill and Data are no longer top-level sidebar modes.

#### Scenario: User switches to Agents mode
- **WHEN** the user clicks the Agents sidebar entry
- **THEN** the Agent Dashboard view is displayed with the agent list on the left and the selected agent's widget grid on the right

#### Scenario: User switches to Library mode
- **WHEN** the user clicks the Library sidebar entry
- **THEN** the Library view is displayed showing the Skills sub-tab by default

#### Scenario: User switches between Library sub-tabs
- **WHEN** the user clicks the Data sub-tab inside Library
- **THEN** the existing Data view (file tree + table) is displayed without navigation away from Library mode

## ADDED Requirements

### Requirement: Library View
The desktop UI SHALL provide a Library view that consolidates Skill and Data content under a single tab switcher.

- The Library view SHALL contain two sub-tabs: **Skills** and **Data**
- The Skills sub-tab SHALL render the existing skill list and detail panel (no functional change)
- The Data sub-tab SHALL render the existing data file tree and table panel (no functional change)
- Widget "View all →" links in agent dashboards SHALL navigate to the relevant Library sub-tab

#### Scenario: Widget links to Library
- **WHEN** the user clicks "View all rows →" in a chart widget
- **THEN** the app navigates to Library → Data sub-tab with that table selected
