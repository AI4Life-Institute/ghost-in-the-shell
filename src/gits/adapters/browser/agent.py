"""Browser Agent — think-act loop for autonomous browser tasks.

Uses Claude (via the anthropic SDK) to decide the next browser action,
executes it via openclaw primitives, stores progress in SQLite, and
notifies a caller-supplied callback after each step.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Coroutine

from anthropic import AsyncAnthropic

from ...storage.sqlite import ArtifactRepo, GitsDB, MemoryRepo, StepRepo, TaskRepo
from . import openclaw

logger = logging.getLogger(__name__)

# Type alias for the optional notification callback.
NotifyCallback = Callable[
    [str, str, dict[str, Any]],
    Coroutine[Any, Any, None],
]

_SYSTEM_PROMPT_TEMPLATE = """\
You are a browser automation agent. Your goal: {goal}

Current page: {current_url}

Visible elements (ref = clickable identifier):
{snapshot_text}

Working memory:
{memory_items}

Choose the SINGLE best next action. Respond with JSON only:
{{
  "action": "navigate|click|type|evaluate|extract|done|ask_user|save_artifact",
  "params": {{}},
  "reasoning": "brief explanation"
}}

Actions:
- navigate: {{"url": "..."}}
- click: {{"ref": "e12", "label": "..."}}
- type: {{"ref": "e5", "text": "..."}}
- evaluate: {{"js": "document.title", "store_as": "optional_key"}}
- extract: {{}} — extract full page text as artifact
- done: {{"summary": "what was accomplished"}}
- ask_user: {{"message": "what do you need to know"}}
- save_artifact: {{"type": "csv|text|pdf", "filename": "...", "content": "..."}}
"""


class BrowserAgent:
    """Think-act loop for a single browser automation task."""

    def __init__(
        self,
        db: GitsDB,
        profile: str,
        notify_cb: NotifyCallback | None = None,
    ) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Export it before starting the browser agent."
            )
        self._client = AsyncAnthropic(api_key=api_key)
        self._db = db
        self._profile = profile
        self._notify_cb = notify_cb

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, task_id: str, goal: str, max_steps: int = 30) -> dict:
        """Run the think-act loop for *goal* and return a final summary dict."""
        tasks = TaskRepo(self._db.conn)
        steps = StepRepo(self._db.conn)
        memory = MemoryRepo(self._db.conn)

        # Mark as running.
        await tasks.update_status(task_id, "running")

        seq = 0
        current_url = "about:blank"
        final_summary: dict[str, Any] = {}

        try:
            while seq < max_steps:
                # ── 1. Snapshot ─────────────────────────────────────────
                try:
                    elements = await openclaw.snapshot(self._profile)
                    snapshot_text = "\n".join(
                        f"- {el.role} \"{el.label}\" [ref={el.ref}]"
                        for el in elements
                    ) or "(no interactive elements)"
                except openclaw.OpenClawError as exc:
                    logger.warning("snapshot failed: %s", exc)
                    snapshot_text = "(snapshot unavailable)"

                # ── 2. Working memory ────────────────────────────────────
                # Retrieve stored current_url from memory if available.
                stored_url = await memory.get(task_id, "current_url")
                if stored_url:
                    current_url = stored_url

                # Collect all non-sensitive observations as memory items.
                memory_items = await self._load_memory(task_id, memory)

                # ── 3. Build prompt ──────────────────────────────────────
                prompt = _SYSTEM_PROMPT_TEMPLATE.format(
                    goal=goal,
                    current_url=current_url,
                    snapshot_text=snapshot_text,
                    memory_items=memory_items or "(empty)",
                )

                # ── 4. Call Claude ───────────────────────────────────────
                decision = await self._call_claude(prompt)

                action = decision.get("action", "")
                params = decision.get("params", {})
                reasoning = decision.get("reasoning", "")

                logger.info(
                    "task=%s seq=%d action=%s reasoning=%s",
                    task_id, seq, action, reasoning,
                )

                # ── 5. Store step ────────────────────────────────────────
                await steps.add(
                    task_id=task_id,
                    seq=seq,
                    action=action,
                    input_data=params,
                    output_data={"reasoning": reasoning},
                )

                # ── 6. Notify caller ─────────────────────────────────────
                await self._notify(task_id, "step", {
                    "seq": seq,
                    "action": action,
                    "reasoning": reasoning,
                })

                # ── 7. Execute action ────────────────────────────────────
                if action == "done":
                    summary = params.get("summary", "Task completed.")
                    await tasks.update_status(task_id, "done", summary=summary)
                    final_summary = {"status": "done", "summary": summary}
                    await self._notify(task_id, "done", {"summary": summary})
                    return final_summary

                elif action == "ask_user":
                    message = params.get("message", "Agent needs input.")
                    await memory.set(task_id, "hitl_message", message)
                    await tasks.update_status(task_id, "needs_review")
                    await self._notify(task_id, "ask_user", {"message": message})
                    return {"status": "needs_review", "message": message}

                elif action == "navigate":
                    url = params.get("url", "")
                    try:
                        result = await openclaw.navigate(self._profile, url)
                        current_url = result.url
                        await memory.set(task_id, "current_url", current_url)
                    except openclaw.OpenClawError as exc:
                        logger.warning("navigate failed: %s", exc)

                elif action == "click":
                    ref = params.get("ref", "")
                    try:
                        await openclaw.click(self._profile, ref)
                    except openclaw.OpenClawError as exc:
                        logger.warning("click failed: %s", exc)

                elif action == "type":
                    ref = params.get("ref", "")
                    text = params.get("text", "")
                    try:
                        await openclaw.type_text(self._profile, ref, text)
                    except openclaw.OpenClawError as exc:
                        logger.warning("type_text failed: %s", exc)

                elif action == "evaluate":
                    js = params.get("js", "")
                    store_as = params.get("store_as")
                    try:
                        result_str = await openclaw.evaluate(self._profile, js)
                        if store_as:
                            await memory.set(task_id, store_as, result_str)
                    except openclaw.OpenClawError as exc:
                        logger.warning("evaluate failed: %s", exc)

                elif action == "extract":
                    try:
                        text_content = await openclaw.extract_text(self._profile)
                        await self._save_text_artifact(
                            task_id=task_id,
                            filename=f"extract_{seq}.txt",
                            content=text_content,
                        )
                    except openclaw.OpenClawError as exc:
                        logger.warning("extract failed: %s", exc)

                elif action == "save_artifact":
                    await self._handle_save_artifact(task_id, params)

                else:
                    logger.warning("unknown action %r — skipping", action)

                seq += 1

            # max_steps reached without done/ask_user.
            await tasks.update_status(task_id, "needs_review", summary="Max steps reached.")
            final_summary = {"status": "needs_review", "reason": "max_steps"}
            await self._notify(task_id, "failed", {"error": "max steps reached"})
            return final_summary

        except Exception as exc:
            error_msg = str(exc)
            logger.exception("BrowserAgent run failed for task %s", task_id)
            try:
                await tasks.update_status(task_id, "failed", summary=error_msg)
            except Exception:
                pass
            await self._notify(task_id, "failed", {"error": error_msg})
            return {"status": "failed", "error": error_msg}

    # ------------------------------------------------------------------
    # Claude API
    # ------------------------------------------------------------------

    async def _call_claude(self, prompt: str) -> dict[str, Any]:
        """Call Claude and parse a JSON decision from the response."""
        response = await self._client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown code fences if present.
        if raw.startswith("```"):
            lines = raw.splitlines()
            # Remove first and last fence lines.
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Claude returned non-JSON: %s", raw[:200])
            return {"action": "ask_user", "params": {"message": "Agent produced invalid JSON."}, "reasoning": "parse error"}

    # ------------------------------------------------------------------
    # Artifact helpers
    # ------------------------------------------------------------------

    async def _save_text_artifact(
        self, task_id: str, filename: str, content: str
    ) -> None:
        artifact_dir = Path.home() / ".gits" / "artifacts" / task_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        out_path = artifact_dir / filename
        out_path.write_text(content, encoding="utf-8")
        artifacts = ArtifactRepo(self._db.conn)
        await artifacts.add(
            task_id=task_id,
            type_="text",
            filename=filename,
            path=str(out_path),
            size_bytes=len(content.encode("utf-8")),
        )
        logger.info("Saved text artifact %s", out_path)

    async def _handle_save_artifact(self, task_id: str, params: dict[str, Any]) -> None:
        """Handle save_artifact action: write file and record in DB."""
        type_ = params.get("type", "text")
        filename = params.get("filename", "artifact.txt")
        content = params.get("content", "")

        artifact_dir = Path.home() / ".gits" / "artifacts" / task_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        out_path = artifact_dir / filename

        if isinstance(content, str):
            # Try to detect base64-encoded binary.
            try:
                binary = base64.b64decode(content, validate=True)
                out_path.write_bytes(binary)
                size = len(binary)
            except Exception:
                out_path.write_text(content, encoding="utf-8")
                size = len(content.encode("utf-8"))
        else:
            encoded = json.dumps(content)
            out_path.write_text(encoded, encoding="utf-8")
            size = len(encoded.encode("utf-8"))

        artifacts = ArtifactRepo(self._db.conn)
        await artifacts.add(
            task_id=task_id,
            type_=type_,
            filename=filename,
            path=str(out_path),
            size_bytes=size,
        )
        logger.info("Saved artifact %s (%s bytes)", out_path, size)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_memory(self, task_id: str, memory: MemoryRepo) -> str:
        """Return a formatted string of current task observations."""
        # We query the raw DB to list all observations for this task.
        cursor = await self._db.conn.execute(
            "SELECT key, value FROM observations WHERE task_id = ? AND sensitive = 0",
            (task_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return ""
        return "\n".join(f"  {r[0]}: {r[1]}" for r in rows)

    async def _notify(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        if self._notify_cb is None:
            return
        try:
            await self._notify_cb(task_id, event, data)
        except Exception:
            logger.exception("notify_cb raised for event %s", event)
