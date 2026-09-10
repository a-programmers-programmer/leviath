// Runtime-owned regions are the authority. No stage offers context mutation.
// Model text, repository contents and worker summaries are untrusted data.
fn require_json(text, label) {
    if text.trim() == "" || !is_json(text) {
        throw `${label}: missing, truncated, or malformed JSON`;
    }
    parse_json(text)
}

fn is_controller(stage) {
    stage == "bootstrap" || stage == "control" || stage == "collect"
}

fn is_worker(stage) {
    stage == "reader" || stage == "coder" || stage == "verifier"
}

fn decision(text) {
    let d = require_json(text, "Oracle decision");
    let actions = ["REQUEST_EVIDENCE", "AUTHORIZE", "REMEDIATE", "ACCEPT", "ASK_USER", "STOP"];
    if !actions.contains(d.action) { throw "Unknown Oracle action"; }
    // The kernel checks the complete schema, snapshot, graph and receipts.
    d
}

fn on_stage_enter(ctx) {
    let edits = #{ conversation: "", tool_results: "", final_output: "" };
    if is_controller(ctx.stage) { edits.control_result = ""; }
    if ctx.stage == "oracle" {
        require_json(ctx.regions.dossier, "Oracle dossier");
        edits.decision = "";
        edits.control_result = "";
        edits.worker_results = "";
        edits.worker_receipts = "";
    }
    if is_worker(ctx.stage) { edits.worker_receipts = ""; }
    #{ action: "modify", value: edits }
}

fn before_inference(ctx) {
    if ctx.stage != "oracle" { return true; }
    require_json(ctx.regions.dossier, "Oracle dossier");
    // Stage projection hides the task, orders and ledger. These infrastructure
    // regions are always visible in the runtime, so explicitly clear them.
    // A second independent guard checks the fully assembled request at dispatch.
    #{ action: "modify", value: #{
        conversation: "", tool_results: "", final_output: ""
    } }
}

fn after_inference(ctx) {
    if ctx.stage != "oracle" { return true; }
    if ctx.truncated || ctx.tool_calls.len() != 0 {
        throw "Oracle must return one complete, tool-free decision";
    }
    decision(ctx.response);
    true
}

fn on_stage_exit(ctx) {
    if ctx.stage == "oracle" {
        // The conversation was cleared immediately before the one inference.
        // A repeated exit hook may see the already captured decision.
        let text = if ctx.regions.conversation.trim() == "" {
            ctx.regions.decision
        } else { ctx.regions.conversation };
        let d = decision(text);
        return #{ action: "modify", value: #{
            decision: to_json(d), conversation: ""
        } };
    }
    if !is_controller(ctx.stage) { return true; }
    let out = require_json(ctx.regions.control_result, "Controller receipt");
    if out.ok != true { throw `Oracle kernel refused: ${out.error}`; }
    if out.run_id == () || out.run_id == "" { throw "Missing run identity"; }
    if ctx.stage != "bootstrap" && out.run_id != ctx.regions.run_id {
        throw "Controller receipt belongs to another run";
    }
    let destinations = ["oracle", "readers", "coders", "verifiers", "report"];
    if !destinations.contains(out.route) { throw "Invalid controller route"; }
    // Strict array parsing at dispatch independently verifies the work list.
    let items = to_json(out.items);
    if !items.starts_with("[") { throw "Controller items must be an array"; }
    if out.route == "report" && (out.report == () || out.report == "") {
        throw "Terminal report is missing";
    }
    #{ action: "modify", value: #{
        run_id: out.run_id, route: out.route, items: items,
        dossier: to_json(out.dossier), report: out.report,
        conversation: "", tool_results: "", final_output: "", worker_results: ""
    } }
}

fn on_tool_call(ctx) {
    if ctx.tool_calls.len() != 1 { throw "Use exactly one tool call per turn"; }
    let tool_call = ctx.tool_calls[0];
    if ctx.stage == "report" {
        if tool_call.name != "submit_output" || ctx.regions.report.trim() == "" {
            throw "Only the recorded final report may be submitted";
        }
        return #{ action: "modify", value: [#{
            name: "submit_output", arguments: #{ content: ctx.regions.report }
        }] };
    }
    if tool_call.name != "oracle_workspace" { throw "Tool is outside this work order"; }
    let request = #{};
    if is_controller(ctx.stage) {
        if ctx.regions.control_result.trim() != "" {
            throw "Controller operation already executed; finish the stage";
        }
        request.op = ctx.stage;
        if ctx.stage == "bootstrap" {
            if ctx.regions.task.trim() == "" || ctx.regions.task.contains("truncated by leviath") {
                throw "Task must be present and fit its input region";
            }
            request.task = ctx.regions.task;
        } else {
            request.run_id = ctx.regions.run_id;
            if ctx.stage == "control" {
                request.decision = decision(ctx.regions.decision);
            } else {
                request.runtime_report = ctx.regions.worker_results;
            }
        }
    } else if is_worker(ctx.stage) {
        // The daemon creates this task from the exact deterministic fan-out item.
        // Workers cannot write it or select a different run/order identity.
        let parts = ctx.regions.task.split("\nContext: ");
        if parts.len() != 2 { throw "Missing canonical worker context"; }
        let authority = require_json(parts[1], "Worker context");
        request = tool_call.arguments.request;
        if request == () { throw "Missing workspace action request"; }
        let actions = ["inspect", "read", "check", "patch", "seal"];
        if !actions.contains(request.action) { throw "Unknown worker action"; }
        request.op = "worker";
        request.run_id = authority.run_id;
        request.order_id = authority.order_id;
    } else { throw "This stage cannot call workspace tools"; }
    #{ action: "modify", value: [#{
        name: "oracle_workspace", arguments: #{ request: request }
    }] }
}
