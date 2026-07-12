"""tv6q3n — isolated ghost ⇄ builder-os integration harness (codex gate #8).

Drives the *real* ghost builder chain (registry → event monitor → renderer →
response adapter → engine disposer) against the *real* builder-os CLI on a
throwaway git repo, with only the Discord transport and the unavoidable external
providers (github / Eva-LLM / reviewer-LLM) swapped for offline doubles.
"""
