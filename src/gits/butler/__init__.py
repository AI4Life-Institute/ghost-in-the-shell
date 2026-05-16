"""butler — vault-PM Discord dispatch CLI, ported from vault's Tools/butler.

Split across two argparse subcommand groups in ``gits.__main__``:

- ``ghost discord <verb>`` — raw Discord REST primitives (transport-only).
- ``ghost butler <verb>``  — PM semantics (identity, bind, dispatch, prefix
  decoration); calls into the same low-level helpers as ``ghost discord``.

The vault-side bot (``gits.adapters.discord.bot``) also imports the shared
prefix regex from :mod:`gits.butler.prefix` — so encoder (CLI) and decoder
(bot) can never drift.
"""
