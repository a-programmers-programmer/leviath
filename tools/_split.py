#!/usr/bin/env python3
"""Mechanical, text-preserving split of the two over-limit files.

No Rust toolchain on this box, so the split is done by exact byte-range moves:
each item (doc comments + attributes + body) is cut whole from the original and
pasted into a sibling file. The only additions are `mod`/`use` lines in the
parent and `use super::*;` in the children, plus the narrowest visibility
keyword (`pub(super)`) on a moved item that was private -- a child's items are
not visible to its parent otherwise.
"""
import re
import sys

ITEM = re.compile(
    r'^(pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?'
    r'(fn|const|static|struct|enum|trait|type|impl|union)\b'
)


def parse_items(lines):
    items = []
    i, n = 0, len(lines)
    while i < n:
        m = ITEM.match(lines[i])
        if not m:
            i += 1
            continue
        s = i
        while s - 1 >= 0 and (lines[s - 1].startswith("///") or lines[s - 1].startswith("#[")):
            s -= 1
        code = lines[i]
        if code.rstrip().endswith(";"):
            e = i
        else:
            depth, e = 0, i
            while e < n:
                depth += lines[e].count("{") - lines[e].count("}")
                st = lines[e].rstrip()
                if depth <= 0 and (st.endswith("}") or st.endswith(");") or st.endswith("];")):
                    break
                e += 1
        kind = m.group(2)
        if kind == "impl":
            mm = re.match(
                r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_:<>, \'\[\];]+?\s+for\s+)?([A-Za-z_]\w*)',
                code,
            )
            name = "impl:" + (mm.group(1) if mm else "?")
        else:
            mm = re.match(
                r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?'
                r'(?:fn|const|static|struct|enum|trait|type|union)\s+([A-Za-z_]\w*)',
                code,
            )
            name = mm.group(1) if mm else "?"
        if code.startswith("pub(crate)"):
            vis = "pub(crate)"
        elif code.startswith("pub(super)"):
            vis = "pub(super)"
        elif code.startswith("pub"):
            vis = "pub"
        else:
            vis = "priv"
        items.append(dict(name=name, kind=kind, vis=vis, s=s, e=e, code=i))
        i = e + 1
    return items


def item_text(lines, it):
    return "\n".join(lines[it["s"]:it["e"] + 1]) + "\n"


def bump(lines, it):
    """Return the item's lines with the narrowest visibility keyword added."""
    body = lines[it["s"]:it["e"] + 1]
    if it["vis"] == "priv":
        k = it["code"] - it["s"]
        body = list(body)
        body[k] = "pub(super) " + body[k]
    return body


def split(path, children, external, anchor_re, header):
    src = open(path).read()
    lines = src.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    items = parse_items(lines)
    by_name = {it["name"]: it for it in items}

    planned = [n for group in children.values() for n in group]
    missing = [n for n in planned if n not in by_name]
    if missing:
        sys.exit(f"{path}: planned items not found: {missing}")

    # sanity: spans ordered and non-overlapping
    prev = 0
    for it in items:
        assert it["s"] >= prev, (path, it)
        prev = it["e"] + 1

    moved_names = set(planned)
    moved_spans = sorted((by_name[n]["s"], by_name[n]["e"]) for n in planned)

    # ---- write the children ----
    for child, names in children.items():
        out = [f"//! {header[child]}", "use super::*;", ""]
        for n in names:
            out.extend(bump(lines, by_name[n]))
            out.append("")
        while out and out[-1] == "":
            out.pop()
        open(f"{path.rsplit('/', 1)[0]}/{child}.rs", "w").write("\n".join(out) + "\n")

    # ---- rewrite the parent as mod.rs ----
    keep = []
    skip = set()
    for s, e in moved_spans:
        for k in range(s, e + 1):
            skip.add(k)
    for idx, line in enumerate(lines):
        if idx not in skip:
            keep.append(line)

    decls = []
    for child in children:
        decls.append(f"mod {child};")
    for child in children:
        decls.append(f"use {child}::*;")
    for child, names in external.items():
        if not names:
            continue
        if len(names) == 1:
            decls.append(f"pub(crate) use {child}::{names[0]};")
        else:
            decls.append(f"pub(crate) use {child}::{{")
            decls.append("    " + ", ".join(names) + ",")
            decls.append("};")

    anchor = next(i for i, l in enumerate(keep) if re.search(anchor_re, l))
    keep = keep[:anchor + 1] + decls + keep[anchor + 1:]

    # collapse runs of blank lines the removals left behind
    out, blanks = [], 0
    for line in keep:
        if line.strip() == "":
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        out.append(line)
    while out and out[-1] == "":
        out.pop()

    parent_dir = path.rsplit("/", 1)[0]
    name = path.rsplit("/", 1)[1][:-3]
    open(f"{parent_dir}/{name}/mod.rs", "w").write("\n".join(out) + "\n")

    print(f"{path}: moved {len(planned)} items, {sum(e - s + 1 for s, e in moved_spans)} lines")
    for n in planned:
        it = by_name[n]
        tag = "PUBSUPER" if it["vis"] == "priv" else it["vis"]
        print(f"    L{it['s']+1:5d}-{it['e']+1:<5d} {tag:9} {n}")


FANOUT = "crates/leviath-runtime/src/fanout.rs"
STAGE = "crates/leviath-core/src/manifest/stage.rs"

split(
    FANOUT,
    {
        "framing": ["FRAMED_PREVIOUS_ITEMS", "FrameSplitRoundQuery", "frame_split_round",
                    "split_round_framing"],
        "authoritative": ["AuthoritativeFanOutPending", "prepare_authoritative_fanouts",
                          "start_authoritative_fanouts", "authoritative_items"],
        "worker": ["DEFAULT_FANOUT_DEPTH", "run_tree_size", "blueprint_fan_out_max_items",
                   "start_worker", "child_output_content", "worker_terminal_result",
                   "worker_requires_output"],
        "finish": ["finish_fan_out", "finish_stage_fan_out", "finish_tool_fan_out",
                   "leave_fan_out"],
    },
    {
        "framing": ["frame_split_round"],
        "authoritative": ["AuthoritativeFanOutPending", "prepare_authoritative_fanouts",
                          "start_authoritative_fanouts"],
        "worker": ["child_output_content"],
        "finish": [],
    },
    r'^use worker_sources::merge_worker_sources;',
    {
        "framing": "Telling a re-entered fan-out stage it has been here before: the "
                   "framing of a second split round. Moved out of `fanout.rs` whole; "
                   "nothing here changed but the file it lives in.",
        "authoritative": "The region-backed fan-out path: a stage whose items come from a "
                         "context region rather than a model turn. Moved out of `fanout.rs` "
                         "whole; nothing here changed but the file it lives in.",
        "worker": "Starting one fan-out worker and reading its answer back out of the "
                  "world. Moved out of `fanout.rs` whole; nothing here changed but the "
                  "file it lives in.",
        "finish": "Finishing a fan-out: applying the failure policy, injecting the "
                  "consolidated report, and leaving the stage. Moved out of `fanout.rs` "
                  "whole; nothing here changed but the file it lives in.",
    },
)

split(
    STAGE,
    {
        "keys": ["INPUT_KEYS", "STAGE_KEYS", "CONTEXT_KEYS", "HOOK_KEYS", "TOOL_ROUTING_KEYS",
                 "GATE_KEYS", "EDGE_KEYS", "TOOL_POLICIES", "validate_tool_policy",
                 "reject_unknown_keys"],
        "transitions": ["parse_transitions", "parse_transition_gate", "parse_stuck_config"],
        "mode": ["apply_stage_mode", "fan_out_number"],
    },
    {
        "keys": ["STAGE_KEYS", "CONTEXT_KEYS", "HOOK_KEYS", "TOOL_ROUTING_KEYS", "GATE_KEYS",
                 "EDGE_KEYS", "TOOL_POLICIES", "validate_tool_policy", "reject_unknown_keys"],
        "transitions": [],
        "mode": [],
    },
    r'^use super::\*;$',
    {
        "keys": "The key tables a stage table is read against, and the guards that refuse a "
                "key the parser does not know. Moved out of `stage.rs` whole; nothing here "
                "changed but the file it lives in.",
        "transitions": "Parsing `[stages.<name>.transitions]`: the edges, their gates, and "
                       "the stuck detection that hangs off an edge. Moved out of `stage.rs` "
                       "whole; nothing here changed but the file it lives in.",
        "mode": "Parsing `[stages.<name>] mode` and the sub-tables a given mode reads. "
                "Moved out of `stage.rs` whole; nothing here changed but the file it "
                "lives in.",
    },
)