#!/usr/bin/env python3
"""Stand-in for an agent CLI in tests.

Reads the prompt from stdin and its behaviour from `.reviewloop/fake-agent.json` in the working directory (the
sandbox), because the adapter scrubs the environment. Finds its output path in argv (`.reviewloop/out/<step>.json`).
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

GRANDCHILD = "import time; time.sleep(30)"


def spawn_grandchild() -> None:
    child = subprocess.Popen([sys.executable, "-c", GRANDCHILD])  # noqa: S603
    print(json.dumps({"grandchild": child.pid}), flush=True)


def main() -> int:
    prompt = sys.stdin.read()
    match = re.search(r"\.reviewloop/out/[\w.-]+\.json", " ".join(sys.argv[1:]))
    if match is None:
        print("fake agent: no output path in argv", file=sys.stderr)
        return 3
    out = Path(match.group(0))
    config = json.loads(Path(".reviewloop/fake-agent.json").read_text())
    mode = config.get("mode", "good")
    print(json.dumps({"type": "system", "subtype": "init", "mode": mode}))
    good = json.dumps(config.get("output", {}))
    if mode == "good":
        out.write_text(good)
    elif mode == "bad":
        out.write_text(config.get("raw", "not json"))
    elif mode == "bad_then_good":
        out.write_text(good if "failed validation" in prompt else "not json")
    elif mode == "extra_write":
        out.write_text(good)
        Path(config.get("extra_path", "planted.txt")).write_text("x")
    elif mode == "prompt":
        out.write_text(json.dumps({"prompt": prompt}))
    elif mode == "env":
        home = Path(os.environ["HOME"])
        skills_dir = home / ".claude" / "skills"
        skills = sorted(p.name for p in skills_dir.iterdir()) if skills_dir.is_dir() else []
        dump = {"env": sorted(os.environ), "home": str(home), "skills": skills, "path": os.environ["PATH"]}
        out.write_text(json.dumps(dump))
    elif mode == "shell_path":
        script = 'PATH=/usr/bin:/bin; . "$CLAUDE_ENV_FILE"; command -v git'
        probe = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, check=False)  # noqa: S603
        out.write_text(json.dumps({"prompt": probe.stdout.strip()}))
    elif mode == "git":
        probe = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)  # noqa: S607
        out.write_text(json.dumps({"message": probe.stderr.strip(), "count": probe.returncode}))
    elif mode == "settings":
        settings = Path(os.environ["HOME"]) / ".claude" / "settings.json"
        out.write_text(json.dumps({"settings": json.loads(settings.read_text())}))
    elif mode == "none":
        pass
    elif mode == "sleep":
        time.sleep(30)
    elif mode == "spawn":
        spawn_grandchild()
        time.sleep(30)
    elif mode == "spawn_exit":
        out.write_text(good)
        spawn_grandchild()
    elif mode == "exit1":
        print("fake agent failure", file=sys.stderr)
        return 1
    print(json.dumps({"type": "result", "subtype": "success"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
