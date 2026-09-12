//! Parsing [stages.<name>] mode and the sub-tables a given mode reads.
use super::*;

/// Apply `[stages.<name>] mode`, along with the sub-tables a given mode reads
/// (`interaction_points` for `interactive_points`, the fan-out block for
/// `fan_out`). A stage that names no mode keeps the constructor's default.
pub(super) fn apply_stage_mode(
    stage: Stage,
    stage_name: &str,
    stage_value: &toml::Value,
) -> Result<Stage> {
    let Some(mode_str) = str_of(stage_value, "mode") else {
        return Ok(stage);
    };
    Ok(match mode_str {
        "interactive" => stage.with_mode(StageMode::Interactive),
        "interactive_points" => {
            let mut points = Vec::new();
            if let Some(pts_arr) = array_of(stage_value, "interaction_points") {
                for pt in pts_arr {
                    let pt_name = str_of(pt, "name").unwrap_or("").to_string();
                    let pt_prompt = str_of(pt, "prompt").unwrap_or("").to_string();
                    let pt_required = bool_of(pt, "required").unwrap_or(true);
                    // What the point does when nobody is watching.
                    // Absent means auto-approve, the behaviour every
                    // `--yolo` run has had; `"ask"` opts a genuine
                    // human checkpoint out of that. A misspelling
                    // here would silently un-gate the checkpoint, so
                    // it is an error rather than a fallback.
                    let pt_unattended = match str_of(pt, "unattended") {
                        None | Some("auto_approve") => {
                            crate::blueprint::UnattendedPolicy::AutoApprove
                        }
                        Some("ask") => crate::blueprint::UnattendedPolicy::Ask,
                        Some(other) => {
                            return Err(Error::Other(format!(
                                "stage '{stage_name}': interaction point '{pt_name}' \
                                 has unattended = \"{other}\" - expected \"ask\" or \
                                 \"auto_approve\""
                            )));
                        }
                    };
                    let pt_style = match str_of(pt, "style") {
                        Some("multiple_choice") => {
                            crate::blueprint::InteractionStyle::MultipleChoice
                        }
                        Some("confirm") => crate::blueprint::InteractionStyle::Confirm,
                        Some("free_text") | None => crate::blueprint::InteractionStyle::FreeText,
                        // Its neighbour `unattended` has always rejected an
                        // unknown value; this arm quietly turned a mistyped
                        // `confirm` into a free-text question with the options
                        // still listed and nothing enforcing them.
                        Some(other) => {
                            return Err(Error::Other(format!(
                                "stage '{stage_name}': interaction point style \
                                 \"{other}\" is not valid (valid: free_text, \
                                 multiple_choice, confirm)"
                            )));
                        }
                    };
                    // Accept either "options" or "choices" key
                    let pt_options: Vec<String> = pt
                        .get("options")
                        .or_else(|| pt.get("choices"))
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default();
                    // Per-option directives, keyed by option label:
                    // [stages.<name>.interaction_points.directives]
                    // "Revise - I'll describe changes" = "Call ask_user_text ..."
                    // `followups` is accepted as a backward-compat alias.
                    let pt_directives: std::collections::HashMap<String, String> = pt
                        .get("directives")
                        .or_else(|| pt.get("followups"))
                        .and_then(|v| v.as_table())
                        .map(|tbl| {
                            tbl.iter()
                                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                                .collect()
                        })
                        .unwrap_or_default();
                    // Options that immediately abort the run:
                    // abort_options = ["Abort - cancel this run"]
                    let pt_abort_options: Vec<String> = array_of(pt, "abort_options")
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default();
                    // Options that open the last output for direct editing:
                    // edit_options = ["Add detail - expand a section"]
                    let pt_edit_options: Vec<String> = array_of(pt, "edit_options")
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default();
                    // Pinned region that holds the authoritative
                    // document: document_region = "plan"
                    let pt_document_region: Option<String> =
                        str_of(pt, "document_region").map(|s| s.to_string());
                    points.push(crate::blueprint::InteractionPoint {
                        name: pt_name,
                        prompt: pt_prompt,
                        required: pt_required,
                        unattended: pt_unattended,
                        style: pt_style,
                        options: pt_options,
                        directives: pt_directives,
                        abort_options: pt_abort_options,
                        edit_options: pt_edit_options,
                        document_region: pt_document_region,
                    });
                }
            }
            stage.with_mode(StageMode::InteractivePoints { points })
        }
        "fan_out" => {
            let str_field = |key: &str| {
                stage_value
                    .get(key)
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            };
            let items_region = match stage_value.get("items_region") {
                None => None,
                Some(value) => {
                    let name = value.as_str().ok_or_else(|| {
                        Error::Other(format!(
                            "stage '{stage_name}': items_region must be a region name"
                        ))
                    })?;
                    let name = name.trim();
                    if name.is_empty() {
                        return Err(Error::Other(format!(
                            "stage '{stage_name}': items_region cannot be empty"
                        )));
                    }
                    Some(name.to_string())
                }
            };
            let on_worker_failure = match str_of(stage_value, "on_worker_failure") {
                Some("fail_all") => crate::blueprint::WorkerFailurePolicy::FailAll,
                Some("continue") | None => crate::blueprint::WorkerFailurePolicy::Continue,
                // Refused rather than folded into continue: a misspelled
                // `fail_all` would let a fan-out swallow every worker failure -
                // the opposite of what was written, and invisible in a run that
                // then merged nothing.
                Some(other) => {
                    return Err(Error::Other(format!(
                        "stage '{stage_name}': on_worker_failure = \"{other}\" \
                         is not a policy (valid: continue, fail_all)"
                    )));
                }
            };
            let config = crate::blueprint::FanOutConfig {
                worker_agent: str_field("worker_agent"),
                worker_stage: str_field("worker_stage"),
                worker_query: str_field("worker_query"),
                merge_stage: str_field("merge_stage"),
                max_workers: fan_out_number(stage_value, stage_name, "max_workers", "unlimited")?
                    .unwrap_or(crate::blueprint::DEFAULT_MAX_WORKERS),
                on_worker_failure,
                split_prompt: str_field("split_prompt").unwrap_or_default(),
                items_region,
                results_region: str_field("results_region"),
                max_items: fan_out_number(stage_value, stage_name, "max_items", "unlimited")?
                    .filter(|n| *n > 0),
                max_attempts: fan_out_number(
                    stage_value,
                    stage_name,
                    "max_attempts",
                    "do not ask again",
                )?,
            };
            stage.with_mode(StageMode::FanOut { config })
        }
        "output" => stage.with_mode(StageMode::Output),
        "autonomous" => stage.with_mode(StageMode::Autonomous),
        // Refused rather than folded into autonomous: `mode =
        // "outupt"` would produce a stage that ran normally and
        // never asked for the output it was written to produce.
        // Region kinds reject an unknown `kind` for the same
        // reason. Any manifest this refuses is not doing what it
        // says.
        unknown => {
            return Err(Error::Other(format!(
                "stage '{stage_name}': unknown mode \"{unknown}\" (valid modes: \
                 autonomous, interactive, interactive_points, fan_out, output)"
            )));
        }
    })
}

/// Read a fan-out stage's `max_workers`, `max_items` or `max_attempts`.
///
/// `Ok(None)` when the key is absent, so the caller picks the default;
/// `Ok(Some(n))` for a non-negative integer. Anything else is an error rather
/// than a silent fallback: `max_workers = -1` would wrap to the largest
/// `usize` and so run unbounded, while `max_items = "twelve"` would read as no
/// cap at all - both of which show up as an unexpectedly wide fan-out, the
/// wrong place to first hear about a typo. `zero_means` is what `0` does, per key.
pub(super) fn fan_out_number(
    stage_value: &toml::Value,
    stage_name: &str,
    key: &str,
    zero_means: &str,
) -> Result<Option<usize>> {
    let Some(value) = stage_value.get(key) else {
        return Ok(None);
    };
    let n = value.as_integer().ok_or_else(|| {
        Error::Other(format!(
            "stage '{stage_name}': {key} must be a whole number (0 means {zero_means})"
        ))
    })?;
    let n = usize::try_from(n).map_err(|_| {
        Error::Other(format!(
            "stage '{stage_name}': {key} must not be negative (got {n}; 0 means {zero_means})"
        ))
    })?;
    Ok(Some(n))
}
