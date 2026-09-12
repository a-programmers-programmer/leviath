#!/usr/bin/env python3
"""Mechanical text-preserving split of fanout.rs and stage.rs.

Moves each item (doc comments + attributes + body) byte-for-byte from the
original into a sibling file. The only additions are `mod`/`use` lines in the
parent and `use super::*;` in the children, plus `pub(super)` on moved items
that were private (so the parent can still see them through `use child::*;`).
"""
import re, os, sys

def parse_items(lines):
    """Return list of {name,vis,s,e,code_line} for every top-level item."""
    ITEM = re.compile(
        r'^(pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?'
        r'(fn|const|static|struct|enum|trait|type|impl|union)\b'
    )
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
        items.append(dict(name=name, kind=kind, vis=vis, s=s, e=e, code_line=i))
        i = e + 1
    return items

def item_text(lines, it):
    return "\n".join(lines[it["s"]:it["e"] + 1]) + "\n"

def bump_visibility(lines, it):
    """Return the item's lines with pub(super) added if it was private."""
    body = lines[it["s"]:it["e"] + 1]
    if it["vis"] == "priv":
        k = it["code_line"] - it["s"]
        body = list(body)
        body[k] = "pub(super) " + body[k]
    return body

def split_file(path, groups, reexports, anchor_re, header_map):
    """Split `path` into a directory module.

    groups: dict child_name -> list of item names to move
    reexports: dict child_name -> list of item names to re-export as pub(crate)
    anchor_re: regex to find insertion point for mod/use lines
    header_map: dict child_name -> header comment line
    """
    src = open(path).read()
    lines = src.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]

    items = parse_items(lines)
    by_name = {it["name"]: it for it in items}

    planned = [n for group in groups.values() for n in group]
    missing = [n for n in planned if n not in by_name]
    if missing:
        sys.exit(f"{path}: planned items not found: {missing}")

    moved_names = set(planned)
    moved_spans = sorted((by_name[n]["s"], by_name[n]["e"]) for n in planned)

    parent_dir = os.path.dirname(path)
    mod_name = os.path.basename(path)[:-3]  # strip .rs
    out_dir = os.path.join(parent_dir, mod_name)
    os.makedirs(out_dir, exist_ok=True)

    # ---- write children ----
    for child, names in groups.items():
        out_lines = []
        if child in header_map:
            out_lines.append(f"//! {header_map[child]}")
        out_lines.extend(["use super::*;", ""])
        for n in names:
            body = bump_visibility(lines, by_name[n])
            out_lines.extend(body)
            out_lines.append("")
        while out_lines and out_lines[-1] == "":
            out_lines.pop()
        with open(os.path.join(out_dir, f"{child}.rs"), "w") as f:
            f.write("\n".join(out_lines) + "\n")

    # ---- rewrite parent as mod.rs ----
    skip = set()
    for s, e in moved_spans:
        for k in range(s, e + 1):
            skip.add(k)
    keep = [line for idx, line in enumerate(lines) if idx not in skip]

    decls = []
    for child in groups:
        decls.append(f"mod {child};")
    for child in groups:
        decls.append(f"use {child}::*;")
    for child, names in reexports.items():
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

    # collapse runs of blank lines
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

    mod_rs = os.path.join(out_dir, "mod.rs")
    with open(mod_rs, "w") as f:
        f.write("\n".join(out) + "\n")

    print(f"{path}: moved {len(planned)} items, {sum(e - s + 1 for s, e in moved_spans)} lines")
    for n in planned:
        it = by_name[n]
        tag = "PUBSUPER" if it["vis"] == "priv" else it["vis"]
        print(f"    L{it['s']+1:5d}-{it['e']+1:<5d} {tag:9} {n}")
    print(f"    -> {out_dir}/")

# ── fanout.rs ──────────────────────────────────────────────────────────────

FANOUT = "crates/leviath-runtime/src/fanout.rs"

split_file(
    FANOUT,
    {
        "framing": ["FRAMED_PREVIOUS_ITEMS", "FrameSplitRoundQuery", "frame_split_round",
                    "split_round_framing"],
        "authoritative": ["AuthoritativeFanOutPending", "prepare_authoritative_fanouts",
                          "start_authoritative_fanouts"],
        "requests": ["FanOutRequest", "is_fan_out_tool", "parse_fan_out_call", "config_for",
                     "PendingFanOut", "start_pending_fan_outs", "begin_fan_out"],
        "collect": ["MAX_WORKER_STARTS_PER_PASS", "fan_out_collect", "MergedWorker",
                    "slim_merged_workers", "finish_fan_out", "finish_stage_fan_out",
                    "finish_tool_fan_out", "leave_fan_out"],
    },
    {
        "framing": ["frame_split_round"],
        "authoritative": ["AuthoritativeFanOutPending", "prepare_authoritative_fanouts",
                          "start_authoritative_fanouts"],
        "requests": [],
        "collect": [],
    },
    r'^use worker_sources::merge_worker_sources;',
    {
        "framing": "Split-round framing: telling a re-entered fan-out stage it has been here before.",
        "authoritative": "The region-backed fan-out path: a stage whose items come from a context region.",
        "requests": "Parsing fan-out requests and starting pending fan-outs.",
        "collect": "Collecting fan-out workers: reaping, merging, and delivering results.",
    },
)

# ── stage.rs ───────────────────────────────────────────────────────────────

STAGE = "crates/leviath-core/src/manifest/stage.rs"

split_file(
    STAGE,
    {
        "transitions": ["parse_transitions", "parse_transition_gate", "parse_stuck_config"],
        "mode": ["apply_stage_mode", "fan_out_number"],
    },
    {
        "transitions": [],
        "mode": [],
    },
    r'^use super::\*;$',
    {
        "transitions": "Parsing [stages.<name>.transitions]: edges, gates, and stuck detection.",
        "mode": "Parsing [stages.<name>] mode and the sub-tables a given mode reads.",
    },
)