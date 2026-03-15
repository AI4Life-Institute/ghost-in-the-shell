## ADDED Requirements

### Requirement: Data View as Structured Output Explorer
The system SHALL provide a Data mode that displays structured data produced by Tasks (browser
agent extractions), Skills (script outputs), and Code sessions (AI-generated datasets) in a
clean, browseable interface. Users do not need to know the underlying storage is SQLite.

Data mode is the "output tray" of the AI fleet — everything the agents save ends up here,
organised and explorable.

#### Scenario: User opens Data mode
- **WHEN** the user navigates to Data mode
- **THEN** the left panel shows a list of data collections (named sets of rows), grouped by source:
  - Tasks (e.g. "BTC prices", "HN top stories")
  - Skills (e.g. "AAPL daily data", "Weekly report")
  - Manual (user-imported CSV or JSON)
- **AND** each collection shows row count, last updated time, and source icon

#### Scenario: User selects a data collection
- **WHEN** the user clicks a collection
- **THEN** the right panel displays the data in the AI-selected presentation format
- **AND** the user can switch presentation format using tabs: Table / Cards / (Chart if applicable)

### Requirement: AI-Selected Data Presentation
The system SHALL automatically select the most appropriate presentation format for each data
collection based on its shape and content. The user can always override the choice.

The underlying storage format (SQLite tables, JSON files) SHALL NOT be exposed in the UI.
Column names, row counts, and data values are shown; CREATE TABLE statements, file paths,
and migration history are not.

#### Scenario: Tabular data is shown as a sortable table
- **WHEN** a collection has uniform rows with named columns (e.g. stock prices with date, open,
  close, volume)
- **THEN** the default presentation is a sortable, filterable table with sticky column headers
- **AND** the user can click any column header to sort ascending/descending
- **AND** a filter input at the top narrows rows by any column value

#### Scenario: Key-value data is shown as cards
- **WHEN** a collection has few rows with many fields (e.g. a single company's profile with 20 fields)
- **THEN** the default presentation is a card layout, one card per row, fields shown as label:value pairs
- **AND** long text fields are truncated with a "Show more" toggle

#### Scenario: User asks AI to render data differently
- **WHEN** the user types a request in the Data mode chat input
  (e.g. "Show this as a bar chart of price by date")
- **THEN** the AI generates a visualisation (chart, pivot, summary) and renders it inline
- **AND** the generated view can be saved as a named view on that collection

### Requirement: Row Detail and Export
The system SHALL allow users to inspect individual rows in full detail and export collections
to common formats.

#### Scenario: User inspects a row
- **WHEN** the user clicks any row in the table view
- **THEN** a detail drawer slides in from the right showing all fields of that row in full,
  with long text un-truncated and JSON values syntax-highlighted

#### Scenario: User exports a collection
- **WHEN** the user clicks the export button on a collection
- **THEN** they can choose CSV or JSON format
- **AND** the file downloads immediately with a meaningful filename (collection name + date)

### Requirement: Data Collection Linked to Source
The system SHALL display the source of each data collection — which Task, Skill, or session
produced it — and provide a one-click link to navigate to that source.

#### Scenario: User traces data back to source
- **WHEN** the user views a data collection detail panel
- **THEN** the header shows "Source: Task — [task goal]" or "Source: Skill — [skill name]"
- **AND** clicking the source link navigates to that Task's step log or Skill's run history
