"""Puts the EXP-0001 code folders on the import path for the ported tests.

Import it as ``from tests import exp0001_paths`` before importing any module of
the experiment's code.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.path.join(ROOT, "experiments", "EXP-0001", "code")
RUNNER = os.path.join(CODE, "runner")
RUN = os.path.join(RUNNER, "run.py")

for _folder in (CODE, RUNNER):
    if _folder not in sys.path:
        sys.path.insert(0, _folder)


def make_plugin_repo(folder):
    """Create a small git repository under folder that holds a plugin's hooks, and return (path, commit id).

    The tests export the plugin from it in place of the pinned ref, which does not exist in this repository.
    """
    import json
    import subprocess

    repo = os.path.join(folder, "plugin-repo")
    os.makedirs(os.path.join(repo, "hooks"))
    os.makedirs(os.path.join(repo, "skills", "preserve"))
    hooks = {"hooks": {
        "SessionStart": [{"matcher": "startup|clear|compact", "hooks": [{"type": "command", "command": "run start"}]}],
        "PreCompact": [{"hooks": [{"type": "command", "command": "run save"}]}],
    }}
    with open(os.path.join(repo, "hooks", "hooks.json"), "w", encoding="utf-8") as handle:
        json.dump(hooks, handle)
    with open(os.path.join(repo, "skills", "preserve", "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("# preserve\n")
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(git + ["add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "plugin"], cwd=repo, check=True, capture_output=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                            text=True).stdout.strip()
    return repo, commit
