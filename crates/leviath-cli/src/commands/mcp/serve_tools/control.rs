use super::*;

pub(crate) async fn cancel(shared: &Shared, args: &Args) -> CallOutcome {
    let run_id = str_arg(args, "run_id").unwrap_or_default();
    let daemon_error = match shared.daemon_ready().await {
        Ok(()) => {
            let request = ControlRequest::Cancel {
                run_id: run_id.clone(),
            };
            match shared.control.request(&request).await {
                Ok(ControlResponse::Ok { ok: true }) => {
                    return ok(
                        format!("cancelled run {run_id}"),
                        json!({ "run_id": run_id, "cancelled": true }),
                        None,
                    );
                }
                Ok(ControlResponse::Ok { ok: false }) => {
                    return fail(
                        format!("no run '{run_id}' to cancel"),
                        json!({ "run_id": run_id }),
                    );
                }
                Ok(other) => {
                    return fail(
                        format!("unexpected daemon response: {other:?}"),
                        json!({ "run_id": run_id }),
                    );
                }
                Err(e) => e.to_string(),
            }
        }
        Err(e) => e,
    };
    // The daemon is down, wedged, or too busy to answer: record the cancel on
    // disk rather than leave the host with nothing, exactly as `lev cancel`.
    use crate::runstate::ForceCancelOutcome as O;
    let dir = run_dir_in(&shared.env.runs_dir, &run_id);
    let now = leviath_core::duration::now_secs();
    match force_cancel_in(&dir, now) {
        O::Terminated => ok(
            format!("cancelled run {run_id} (on disk; daemon unavailable: {daemon_error})"),
            json!({ "run_id": run_id, "cancelled": true, "daemon_error": daemon_error }),
            None,
        ),
        O::AlreadyTerminal => fail(
            format!("run '{run_id}' is already finished"),
            json!({ "run_id": run_id }),
        ),
        O::NoSuchRun => fail(
            format!("no run '{run_id}' on this machine"),
            json!({ "run_id": run_id }),
        ),
        O::WriteFailed => fail(
            format!("failed to rewrite metadata for '{run_id}' on disk"),
            json!({ "run_id": run_id }),
        ),
    }
}

pub(crate) async fn pause(shared: &Shared, args: &Args) -> CallOutcome {
    let run_id = str_arg(args, "run_id").unwrap_or_default();
    if let Err(e) = shared.daemon_ready().await {
        return fail(
            format!("daemon unavailable: {e}"),
            json!({ "run_id": run_id, "daemon_error": e }),
        );
    }
    let request = ControlRequest::Pause {
        run_id: run_id.clone(),
    };
    match shared.control.request(&request).await {
        Ok(ControlResponse::Ok { ok: true }) => ok(
            format!("paused run {run_id}"),
            json!({ "run_id": run_id, "paused": true }),
            None,
        ),
        Ok(ControlResponse::Ok { ok: false }) => fail(
            format!("could not pause run '{run_id}' (not running or unknown)"),
            json!({ "run_id": run_id }),
        ),
        Ok(other) => fail(
            format!("unexpected daemon response: {other:?}"),
            json!({ "run_id": run_id }),
        ),
        Err(e) => fail(
            format!("control error: {e}"),
            json!({ "run_id": run_id, "error": e.to_string() }),
        ),
    }
}

pub(crate) async fn resume(shared: &Shared, args: &Args) -> CallOutcome {
    let run_id = str_arg(args, "run_id").unwrap_or_default();
    if let Err(e) = shared.daemon_ready().await {
        return fail(
            format!("daemon unavailable: {e}"),
            json!({ "run_id": run_id, "daemon_error": e }),
        );
    }
    let request = ControlRequest::Resume {
        run_id: run_id.clone(),
    };
    match shared.control.request(&request).await {
        Ok(ControlResponse::Ok { ok: true }) => ok(
            format!("resumed run {run_id}"),
            json!({ "run_id": run_id, "resumed": true }),
            None,
        ),
        Ok(ControlResponse::Ok { ok: false }) => fail(
            format!("could not resume run '{run_id}' (not paused or unknown)"),
            json!({ "run_id": run_id }),
        ),
        Ok(other) => fail(
            format!("unexpected daemon response: {other:?}"),
            json!({ "run_id": run_id }),
        ),
        Err(e) => fail(
            format!("control error: {e}"),
            json!({ "run_id": run_id, "error": e.to_string() }),
        ),
    }
}

pub(crate) async fn message(shared: &Shared, args: &Args) -> CallOutcome {
    let run_id = str_arg(args, "run_id").unwrap_or_default();
    let content = str_arg(args, "content").unwrap_or_default();
    let target_region = str_arg(args, "target_region");
    if let Err(e) = shared.daemon_ready().await {
        return fail(
            format!("daemon unavailable: {e}"),
            json!({ "run_id": run_id, "daemon_error": e }),
        );
    }
    let request = ControlRequest::Message {
        agent_id: run_id.clone(),
        content,
        target_region,
    };
    match shared.control.request(&request).await {
        Ok(ControlResponse::Ok { ok: true }) => ok(
            format!("delivered message to {run_id}"),
            json!({ "run_id": run_id, "delivered": true }),
            None,
        ),
        Ok(ControlResponse::Ok { ok: false }) => fail(
            format!("no active run '{run_id}' to message"),
            json!({ "run_id": run_id }),
        ),
        Ok(other) => fail(
            format!("unexpected daemon response: {other:?}"),
            json!({ "run_id": run_id }),
        ),
        Err(e) => fail(
            format!("control error: {e}"),
            json!({ "run_id": run_id, "error": e.to_string() }),
        ),
    }
}

pub(crate) fn build_interaction_response(
    request_id: &str,
    args: &Args,
) -> Result<InteractionResponse, String> {
    let value = str_arg(args, "value");
    let choice_index = uint_arg(args, "choice_index").map(|n| usize::try_from(n).unwrap_or(0));
    let approved = bool_arg(args, "approved");
    let feedback = str_arg(args, "feedback");
    let scope = str_arg(args, "scope")
        .and_then(|raw| match raw.as_str() {
            "once" => Some(ApprovalScope::Once),
            "stage" => Some(ApprovalScope::Stage),
            "session" | "run" => Some(ApprovalScope::Run),
            _ => None,
        })
        .or(Some(ApprovalScope::Once));
    let response = InteractionResponse {
        request_id: request_id.to_string(),
        value,
        choice_index,
        approved,
        scope,
        feedback,
    };
    if response.value.is_none()
        && response.choice_index.is_none()
        && response.approved.is_none()
        && response.feedback.is_none()
    {
        return Err(
            "at least one of value, choice_index, approved, or feedback must be provided to answer an interaction".to_string(),
        );
    }
    Ok(response)
}

pub(crate) async fn respond(shared: &Shared, args: &Args) -> CallOutcome {
    let request_id = str_arg(args, "request_id");

    if let Err(e) = shared.daemon_ready().await {
        return fail(
            format!("daemon unavailable: {e}"),
            json!({ "daemon_error": e }),
        );
    }

    let Some(request_id) = request_id else {
        // List open interactions when no request_id was supplied.
        let request = ControlRequest::ListInteractions;
        return match shared.control.request(&request).await {
            Ok(ControlResponse::Interactions { interactions }) => {
                if interactions.is_empty() {
                    ok(
                        "no runs are waiting on an interaction".to_string(),
                        json!({ "interactions": [] }),
                        None,
                    )
                } else {
                    let text = interactions
                        .iter()
                        .map(|(agent_id, i)| {
                            format!(
                                "request_id {}: agent {}, kind '{:?}'\n  {}",
                                i.id,
                                agent_id,
                                i.kind,
                                i.prompt.replace('\n', "\n  ")
                            )
                        })
                        .collect::<Vec<_>>()
                        .join("\n\n");
                    ok(text, json!({ "interactions": interactions }), None)
                }
            }
            Ok(other) => fail(
                format!("unexpected daemon response: {other:?}"),
                json!({}),
            ),
            Err(e) => fail(
                format!("control error: {e}"),
                json!({ "error": e.to_string() }),
            ),
        };
    };

    let response = match build_interaction_response(&request_id, args) {
        Ok(r) => r,
        Err(e) => return fail(e, json!({ "request_id": request_id })),
    };

    let request = ControlRequest::AnswerInteraction { response };
    match shared.control.request(&request).await {
        Ok(ControlResponse::Ok { ok: true }) => ok(
            format!("answered interaction {request_id}"),
            json!({ "request_id": request_id, "answered": true }),
            None,
        ),
        Ok(ControlResponse::Ok { ok: false }) => fail(
            format!("no open interaction '{request_id}'"),
            json!({ "request_id": request_id }),
        ),
        Ok(other) => fail(
            format!("unexpected daemon response: {other:?}"),
            json!({ "request_id": request_id }),
        ),
        Err(e) => fail(
            format!("control error: {e}"),
            json!({ "request_id": request_id, "error": e.to_string() }),
        ),
    }
}
