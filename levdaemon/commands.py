"""Parse the ``lev`` command catalogue from ``lev --help``.

Real fact (verified live): this build has NO ``lev commands`` subcommand.
``lev commands --json`` exits rc=2. The only machine-readable catalogue is the
grouped ``lev --help`` text, so code parses that text here.

Group headings look like ``Running agents:`` and each command line looks like
``  ps            List agent runs in the shared-world daemon``.
"""

from __future__ import annotations

import re
from typing import List

from .models_runs import Command, CommandCatalogue

GROUP_LINE = re.compile(r"^([A-Z][A-Za-z /&-]*):\s*$")
COMMAND_LINE = re.compile(r"^([a-z][a-z0-9-]*)\s{2,}(\S.*)$")
OPTION_LINE = re.compile(r"^\s*-")


def parse_help(text: str) -> CommandCatalogue:
    """Turn ``lev --help`` text into a typed :class:`CommandCatalogue`."""
    order: List[str] = []
    commands: List[Command] = []
    current = None
    for line in text.splitlines():
        if OPTION_LINE.match(line):
            continue
        m = GROUP_LINE.match(line.strip())
        if m:
            current = m.group(1)
            if current not in order:
                order.append(current)
            continue
        if current is None:
            continue
        c = COMMAND_LINE.match(line.strip())
        if c:
            commands.append(Command(name=c.group(1), group=current, summary=c.group(2).strip()))
    # A heading with no command under it (e.g. "Options:") is not a group.
    used = {c.group for c in commands}
    groups = [g for g in order if g in used]
    return CommandCatalogue(groups=groups, commands=commands)
