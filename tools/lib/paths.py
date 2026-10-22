"""Path classes shared by the checks and the tests.

METHOD_DIR is the name of the folder under an experiment that holds the
experiment's method and code. Every check reads the name from here.
RUNNER_ROOT matches a file under an experiment's runner folder (inside the
method folder), where the code that builds the isolated environment for a live
run lives.
METHOD_FILE matches a file under an experiment's method folder and captures
the experiment id.
RECORDED_FILE matches a file under an experiment's recorded/ folder and
captures the experiment id: the recorded/ root; its admission class and
exemptions are decided separately.
RUN_FILE matches a file inside one run folder, recorded/runs/<run>/, and
captures the experiment id and the run name.
SYNTHESIS_FILE matches a synthesis file, syntheses/SYN-NNNN.md, and captures
the synthesis id.
EXPERIMENT_FILE splits a path under experiments/ into the experiment folder
and the rest of the path.
EXPERIMENT_ID is the shape of an experiment folder name.
TARGET_FILE matches a file in an experiment's target/ folder, which holds text
taken from the subject under study, and captures the experiment id.
"""

from __future__ import annotations

import re

METHOD_DIR = "code"
RUNNER_ROOT = re.compile(r"^experiments/[^/]+/" + re.escape(METHOD_DIR) + r"/runner/")
METHOD_FILE = re.compile(r"^experiments/(EXP-[0-9]{4})/" + re.escape(METHOD_DIR) + r"/.+$")
RECORDED_FILE = re.compile(r"^experiments/(EXP-[0-9]{4})/recorded/.+$")
RUN_FILE = re.compile(r"^experiments/(EXP-[0-9]{4})/recorded/runs/([^/]+)/.+$")
SYNTHESIS_FILE = re.compile(r"^syntheses/(SYN-[0-9]{4})\.md$")
EXPERIMENT_FILE = re.compile(r"^experiments/([^/]+)/(.+)$")
EXPERIMENT_ID = re.compile(r"^EXP-[0-9]{4}$")
TARGET_FILE = re.compile(r"^experiments/(EXP-[0-9]{4})/target/.+$")
