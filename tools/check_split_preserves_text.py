#!/usr/bin/env python3
"""Verify that the split is text-preserving.

For each split file, extracts every top-level item from the original and
verifies it appears byte-for-byte in exactly one of the new files (child or
mod.rs). The only allowed difference: formerly-private items may have
`pub(super) ` prepended to their declaration line.
"""
import re, sys, os

ITEM_RE = re.compile(
    r'^(pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?'
    r'(fn|const|static|struct|enum|trait|type|impl|union)\b'
)

def parse_items(lines):
    """Return list of {name,vis,s,e,code_line} for every top-level item."""
    items = []
    i, n = 0, len(lines)
    while i < n:
        m = ITEM_RE.match(lines[i])
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
        items.append(dict(name=name, vis=vis, s=s, e=e, code_line=i))
        i = e + 1
    return items

def item_text(lines, it):
    return "\n".join(lines[it["s"]:it["e"] + 1])

def find_item(lines, name):
    """Find an item by name. Returns (text, item_dict) or (None, None)."""
    items = parse_items(lines)
    for it in items:
        if it["name"] == name:
            return item_text(lines, it), it
    return None, None

def strip_pub_super_at_decl(text, code_idx):
    """Strip pub(super) from the declaration line (at code_idx within the item
    text). code_idx is the line number of the declaration within the item."""
    lines = text.split("\n")
    if code_idx < len(lines) and lines[code_idx].startswith("pub(super) "):
        lines[code_idx] = lines[code_idx][len("pub(super) "):]
    return "\n".join(lines)

def verify(original_path, new_dir, children, originally_private_names):
    """Verify every original item appears byte-for-byte in the new files."""

    with open(original_path) as f:
        orig_text = f.read()
    orig_lines = orig_text.split("\n")
    if orig_lines and orig_lines[-1] == "":
        orig_lines = orig_lines[:-1]

    orig_items = parse_items(orig_lines)
    print(f"  Original: {len(orig_items)} items in {original_path}")

    # Read all new files
    new_files = {}
    for child in children:
        child_path = os.path.join(new_dir, f"{child}.rs")
        with open(child_path) as f:
            cl = f.read().split("\n")
            if cl and cl[-1] == "":
                cl = cl[:-1]
            new_files[child] = cl
    mod_path = os.path.join(new_dir, "mod.rs")
    with open(mod_path) as f:
        ml = f.read().split("\n")
        if ml and ml[-1] == "":
            ml = ml[:-1]
        new_files["mod"] = ml

    all_ok = True
    found_in = {}
    orig_names = {it["name"] for it in orig_items}

    for it in orig_items:
        name = it["name"]
        orig_txt = item_text(orig_lines, it)
        was_private = it["vis"] == "priv"

        found = False
        for fname, flines in new_files.items():
            new_txt, new_it = find_item(flines, name)
            if new_it is not None:
                cmp_txt = new_txt
                if was_private:
                    # Allow pub(super) addition on the declaration line only.
                    # The declaration is at new_it["code_line"] - new_it["s"]
                    # within the item text.
                    decl_idx = new_it["code_line"] - new_it["s"]
                    cmp_txt = strip_pub_super_at_decl(cmp_txt, decl_idx)
                if orig_txt == cmp_txt:
                    found = True
                    found_in[name] = fname
                    if was_private and new_it["vis"] != "pub(super)":
                        print(f"  WARN: {name} was priv, now {new_it['vis']} (not pub(super))")
                    break
                else:
                    print(f"  MISMATCH: {name} in {fname}.rs differs from original")
                    ol = orig_txt.split("\n")
                    nl = cmp_txt.split("\n")
                    for j in range(min(len(ol), len(nl))):
                        if ol[j] != nl[j]:
                            print(f"    Line {j}:")
                            print(f"      orig: {ol[j]!r}")
                            print(f"      new:  {nl[j]!r}")
                            break
                    else:
                        print(f"    Lengths differ: orig={len(ol)} new={len(nl)}")
                    found = True   # found it, just differs
                    all_ok = False
                    break

        if not found:
            print(f"  MISSING: {name} not found in any new file")
            all_ok = False

    # Verify no extra items
    new_items = []
    for fname, flines in new_files.items():
        for it in parse_items(flines):
            new_items.append((it["name"], fname))

    for name, fname in new_items:
        if name not in orig_names:
            print(f"  EXTRA: {name} in {fname}.rs was not in original")
            all_ok = False

    if all_ok:
        print(f"  PASS: all {len(orig_items)} items preserved byte-for-byte")
    else:
        print(f"  FAIL")

    return all_ok

def main():
    all_ok = True

    fanout_private = {
        "FRAMED_PREVIOUS_ITEMS", "FrameSplitRoundQuery", "split_round_framing",
        "finish_fan_out", "finish_stage_fan_out", "finish_tool_fan_out", "leave_fan_out",
    }

    all_ok &= verify(
        "crates/leviath-runtime/src/fanout.rs",
        "crates/leviath-runtime/src/fanout",
        ["framing", "authoritative", "requests", "collect"],
        fanout_private,
    )

    stage_private = {
        "fan_out_number",
    }

    all_ok &= verify(
        "crates/leviath-core/src/manifest/stage.rs",
        "crates/leviath-core/src/manifest/stage",
        ["transitions", "mode"],
        stage_private,
    )

    if all_ok:
        print("\nAll checks passed: split is text-preserving.")
    else:
        print("\nSOME CHECKS FAILED: split is NOT text-preserving.")
        sys.exit(1)

if __name__ == "__main__":
    main()