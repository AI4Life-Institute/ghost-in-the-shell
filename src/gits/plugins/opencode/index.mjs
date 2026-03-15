// GITS session hook for OpenCode
// Listens for session.created events via the generic "event" hook
// and writes session info to ~/.gits/session_map.json so the
// GITS JSONL monitor can discover OpenCode session IDs.
//
// IMPORTANT: Must use the generic "event" hook, NOT named event hooks
// like "session.created" — named hooks are NOT dispatched by opencode.

import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { execSync } from "child_process";

const GITS_DIR = join(homedir(), ".gits");

export const GitsSessionHook = async (ctx) => {
  return {
    event: async ({ event }) => {
      if (event.type !== "session.created") return;

      const session = event.properties?.info;
      if (!session) return;

      const pane = process.env.TMUX_PANE;
      if (!pane) return;

      let key;
      try {
        key = execSync(
          'tmux display-message -t "' + pane + '" -p "#{session_name}:#{window_id}"',
          { encoding: "utf8" },
        ).trim();
      } catch {
        return;
      }
      if (!key.includes(":")) return;

      mkdirSync(GITS_DIR, { recursive: true });
      const mapFile = join(GITS_DIR, "session_map.json");

      let map = {};
      try {
        map = JSON.parse(readFileSync(mapFile, "utf8"));
      } catch {
        // file doesn't exist or bad JSON — start fresh
      }

      map[key] = {
        session_id: session.id,
        cwd: session.directory || process.cwd(),
      };

      writeFileSync(mapFile, JSON.stringify(map, null, 2) + "\n");
    },
  };
};
