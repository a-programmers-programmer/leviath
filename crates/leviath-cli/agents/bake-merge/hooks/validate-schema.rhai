// validate-schema.rhai — text-based GraphQL sanity checks for stage hooks.
//
// When attached as on_stage_exit, validates whichever region looks like
// GraphQL SDL. Finds duplicate type definitions, unbalanced braces, and
// orphan union-member lines using only the text in the context window.
//
// Returns cancel with reason when issues are found, () when clear.
// NOTE: the runtime does not honour retry from on_stage_exit or
// on_completion, so we cancel with a reason. The real parse authority
// is the Python graphql-core gate in the runner.

fn on_stage_exit(ctx) {
    let text = find_schema(ctx.regions);
    if text == "" { return (); }
    let issues = check(text);
    if issues.len() > 0 {
        return #{ action: "cancel",
                  reason: "Schema invalid: " + issues.join("; ") };
    }
    ();
}

fn find_schema(regions) {
    for name in regions.keys() {
        let t = regions[name];
        if contains(t, "type ") || contains(t, "enum ")
           || contains(t, "input ") || contains(t, "union ") {
            return t;
        }
    }
    ""
}

fn check(text) {
    let issues = [];
    let lines = split(text, "\n");

    // 1. brace balance
    let brace = 0;
    for i in 0..lines.len() {
        brace += count_char(lines[i], "{");
        brace -= count_char(lines[i], "}");
    }
    if brace != 0 {
        issues.push("unbalanced braces (" + brace.to_string() + ")");
    }

    // 2. duplicate definitions
    let seen = #{};
    for i in 0..lines.len() {
        let t = trim(lines[i]);
        if t == "" || starts_with(t, "#")
           || starts_with(t, "\"\"\"") || starts_with(t, "'''") { continue; }
        let kind = detect_kind(t);
        if kind != "" {
            let key = kind + " " + extract_name(t, kind);
            if key in seen {
                issues.push("duplicate " + key);
            }
            seen[key] = true;
        }
    }

    // 3. orphan | lines
    let in_union = false;
    for i in 0..lines.len() {
        let t = trim(lines[i]);
        if t == "" { continue; }
        if starts_with(t, "union ") { in_union = true; continue; }
        if starts_with(t, "|") {
            if !in_union {
                issues.push("orphan '|' at line " + (i+1).to_string());
            }
            continue;
        }
        if starts_with(t, "type ") || starts_with(t, "enum ")
           || starts_with(t, "input ") || starts_with(t, "interface ")
           || starts_with(t, "scalar ") || starts_with(t, "schema ") {
            in_union = false;
        }
    }

    issues
}

fn count_char(s, ch) {
    let n = 0;
    let i = 0;
    while i < s.len() {
        if s.sub_string(i, i+1) == ch { n += 1; }
        i += 1;
    }
    n
}

fn detect_kind(line) {
    if starts_with(line, "type ")      { "type" }
    else if starts_with(line, "enum ")      { "enum" }
    else if starts_with(line, "input ")     { "input" }
    else if starts_with(line, "union ")     { "union" }
    else if starts_with(line, "interface ") { "interface" }
    else if starts_with(line, "scalar ")    { "scalar" }
    else if starts_with(line, "schema ")    { "schema" }
    else                                    { "" }
}

fn extract_name(line, kind) {
    // Skip "kind "
    let rest = line;
    let idx = rest.find(" ");
    if idx < 0 { return ""; }
    let rest = trim(rest.sub_string(idx));
    let name = "";
    let i = 0;
    while i < rest.len() {
        let ch = rest.sub_string(i, i+1);
        if ch == " " || ch == "{" || ch == "(" || ch == ":"
           || ch == "=" || ch == "!" { break; }
        name = name + ch;
        i = i + 1;
    }
    name
}