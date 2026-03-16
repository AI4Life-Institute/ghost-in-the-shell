## ADDED Requirements

### Requirement: Data View as Project File Tree
The system SHALL provide a Data mode whose left panel is a file tree rooted at
`~/myproject/data/`. All agent outputs, skill outputs, and user-imported files live under
this directory — the same project mono-repo as everything else.

The user sees the actual files and folders. This is intentional: the target persona (AI-native
productivity user) is comfortable with terms like "database", "table", "CSV". Exposing the
file system gives them ground truth and full control. The AI handles the complexity of reading
and presenting the data.

**File type handling in the tree:**
- `📁` Folder — expandable; shows children
- `🗄` SQLite `.db` file — expandable; shows its tables as child nodes with row counts
- `📄` CSV / JSON file — leaf node; clicking opens it directly as a table

The right panel shows a sortable, filterable table when a SQLite table or CSV/JSON file is
selected. The user can switch between Table and Cards views.

#### Scenario: User opens Data mode
- **WHEN** the user navigates to Data mode
- **THEN** the left panel shows `~/myproject/data/` as a file tree
- **AND** folders and `.db` files are expandable (click to toggle open/closed)
- **AND** `.db` files show their tables as child items with row counts

#### Scenario: User selects a SQLite table
- **WHEN** the user clicks a table name under a `.db` file in the tree
- **THEN** the right panel shows that table's rows in a sortable, filterable table
- **AND** the header shows the table name

#### Scenario: User selects a CSV file
- **WHEN** the user clicks a `.csv` file in the tree
- **THEN** the right panel shows the CSV contents as a table directly
- **AND** the first row is treated as the header

### Requirement: Table Presentation
The system SHALL display tabular data (SQLite tables, CSV files) in a sortable, filterable
grid. The user can always override the view format.

Data is shown at face value — column names, row counts, and values as stored. The AI can be
asked to render the data differently (chart, pivot, summary) from the Build chat using `/data`.

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
