// Protect gate arguments, review provenance, routing, and final output. Normal
// authoring stages retain host tools; this is workflow control, not OS isolation.
fn json_value(text) {
    if text.trim() == "" || !is_json(text) { throw "Missing or malformed gate/review JSON"; }
    parse_json(text)
}

fn on_stage_enter(ctx) {
    let edits = #{ conversation: "", tool_results: "", final_output: "" };
    if ctx.stage == "check" || ctx.stage == "accept" { edits.gate_result = ""; }
    if ctx.stage == "check" { edits.review = ""; }
    if ctx.stage == "review" { edits.review = ""; }
    #{ action: "modify", value: edits }
}

fn on_tool_call(ctx) {
    if ctx.stage == "check" || ctx.stage == "accept" {
        if ctx.tool_calls.len() != 1 || ctx.tool_calls[0].name != "graphql_contract_gate" {
            throw "Call only graphql_contract_gate, once";
        }
        if ctx.regions.gate_result.trim() != "" { throw "Gate already ran; finish stage"; }
        let request = #{ op: ctx.stage, root: "graphql-contract" };
        if ctx.stage == "accept" { request.review = json_value(ctx.regions.review); }
        return #{ action: "modify", value: [#{ name: "graphql_contract_gate", arguments: #{ request: request } }] };
    }
    if ctx.stage == "review" {
        for tool_call in ctx.tool_calls {
            if tool_call.name == "context_write" {
                if tool_call.arguments.region != "review" || tool_call.arguments.key != () {
                    throw "Reviewer may write only the complete review region";
                }
                let review = json_value(tool_call.arguments.content);
                let checked = json_value(ctx.regions.checked);
                if review.fingerprint != checked.fingerprint { throw "Review fingerprint mismatch"; }
            } else if tool_call.name != "read_file" && tool_call.name != "list_dir" && tool_call.name != "context_read" && tool_call.name != "graphql_contract_guide" {
                throw "Reviewer has read-only artifact access";
            }
        }
    }
    if ctx.stage == "done" || ctx.stage == "blocked" || ctx.stage == "error" {
        if ctx.tool_calls.len() != 1 || ctx.tool_calls[0].name != "submit_output" { throw "Submit only the canonical report"; }
        let result = if ctx.stage == "done" { json_value(ctx.regions.gate_result) }
            else { #{ status: "blocked", gate: ctx.regions.gate_result, review: ctx.regions.review, errors: ctx.regions.error_report } };
        if ctx.stage == "done" && result.status != "accepted" { throw "No accepted contract"; }
        return #{ action: "modify", value: [#{ name: "submit_output", arguments: #{ content: to_json(result) } }] };
    }
    true
}

fn on_stage_exit(ctx) {
    if ctx.stage == "check" || ctx.stage == "accept" {
        let result = json_value(ctx.regions.gate_result);
        let route = "revise";
        if ctx.stage == "check" && result.status == "checked" { route = "review"; }
        if ctx.stage == "accept" && result.status == "accepted" { route = "done"; }
        let edits = #{ route: route };
        if ctx.stage == "check" { edits.checked = to_json(result); }
        return #{ action: "modify", value: edits };
    }
    if ctx.stage == "review" { json_value(ctx.regions.review); }
    true
}
