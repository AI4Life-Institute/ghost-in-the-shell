"""Coding-CLI hook payload handlers shipped by ghost.

Modules here implement Claude Code (and compatible) hook entry points that
read the hook JSON from stdin and exit 0 (allow) or 2 (block). They are
invoked as managed ``command`` hooks wired into per-account ``settings.json``
by ``gits hook --install`` — see :mod:`gits.__main__`.

Keep these import-light (stdlib only): PreToolUse hooks run on *every* tool
call, so heavyweight imports (config, discord, butler) are forbidden here.
"""
