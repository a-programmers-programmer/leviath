//! Parsing fan-out requests and starting pending fan-outs.
use super::*;

/// A `fan_out` call the dispatcher has read but not yet started.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FanOutRequest {
    /// The agent to run for every item, when the caller named one. `None` inside
    /// a fan-out stage, whose blueprint names the worker instead.
    pub agent: Option<String>,
    /// The work, one entry per worker.
    pub items: Vec<WorkItem>,
    /// A per-call concurrency cap, when the caller asked for one.
    pub max_workers: Option<usize>,
}

/// Whether a tool call is the fan-out tool.
pub(crate) fn is_fan_out_tool(name: &str) -> bool {
    name == leviath_core::blueprint::FAN_OUT_TOOL
}

/// Read a `fan_out` call's arguments.
///
/// Strict, unlike the free-text parser it replaced: the arguments came through a
/// schema the provider enforced, so a shape that does not fit is a real mistake
/// and the model is told so rather than guessed at. The refusal is an `[error]`
/// tool result, which the model corrects on its next turn like any other.
pub(crate) fn parse_fan_out_call(arguments: &serde_json::Value) -> Result<FanOutRequest, String> {
    let object = arguments
        .as_object()
        .ok_or_else(|| "fan_out arguments must be an object".to_string())?;
    let items = match object.get("items") {
        Some(serde_json::Value::Array(items)) => items,
        Some(_) => return Err("fan_out `items` must be an array".to_string()),
        None => return Err("fan_out requires an `items` array".to_string()),
    };
    let items: Vec<WorkItem> = items
        .iter()
        .map(|item| {
            serde_json::from_value(item.clone())
                .map_err(|e| format!("fan_out item is not {{id, context}}: {e}"))
        })
        .collect::<Result<_, _>>()?;
    let agent = object
        .get("agent")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .filter(|a| !a.trim().is_empty());
    let max_workers = object
        .get("max_workers")
        .and_then(serde_json::Value::as_u64)
        .map(|n| n as usize);
    Ok(FanOutRequest {
        agent,
        items,
        max_workers,
    })
}

/// Turn a request into the config the engine runs it under.
///
/// A stage's `[stages.x]` fan-out keys are the starting point when there are any;
/// a call from an ordinary stage has none, so it gets the engine defaults and
/// names its worker in the call. Either way the result is one `FanOutConfig`, so
/// everything downstream - the cap, the failure policy, the report - is the same
/// code for both entry points.
pub(crate) fn config_for(request: &FanOutRequest, stage: Option<&FanOutConfig>) -> FanOutConfig {
    let mut config = stage.cloned().unwrap_or_else(|| FanOutConfig {
        worker_agent: None,
        worker_stage: None,
        worker_query: None,
        merge_stage: None,
        max_workers: leviath_core::blueprint::DEFAULT_MAX_WORKERS,
        on_worker_failure: WorkerFailurePolicy::Continue,
        split_prompt: String::new(),
        items_region: None,
        results_region: None,
        max_items: None,
        max_attempts: None,
    });
    // An authoritative items region makes the complete fan-out configuration
    // blueprint-owned. Model arguments remain a trigger only and cannot swap
    // the worker or concurrency cap beneath the region's inventory.
    if config.items_region.is_none() {
        if let Some(agent) = &request.agent {
            config.worker_agent = Some(agent.clone());
            config.worker_stage = None;
            config.worker_query = None;
        }
        if let Some(max_workers) = request.max_workers {
            config.max_workers = max_workers;
        }
    }
    config
}

/// A `fan_out` call the dispatcher accepted, waiting for a tick with world
/// access to start it.
///
/// The hand-off exists because starting a fan-out is world work - it resolves
/// the worker blueprint through the injected spawner - and `dispatch_tools` is
/// an ordinary system. The same shape the interaction and gate-prompt lanes use.
#[derive(Component, Debug, Clone)]
pub(crate) struct PendingFanOut {
    /// The tool call whose result this fan-out will be.
    pub call_id: String,
    /// What the model asked for.
    pub request: FanOutRequest,
}

/// Start every fan-out the dispatcher accepted this tick (exclusive).
///
/// Ordered before [`fan_out_collect`], so a fan-out started here has its workers
/// launched on the same tick rather than a tick later.
pub(crate) fn start_pending_fan_outs(world: &mut World) {
    crate::tick_scope::clear();
    let pending: Vec<(Entity, PendingFanOut)> = {
        let mut q = world.query::<(Entity, &PendingFanOut)>();
        q.iter(world).map(|(e, p)| (e, p.clone())).collect()
    };
    for (entity, PendingFanOut { call_id, request }) in pending {
        crate::tick_scope::enter(entity);
        world.entity_mut(entity).remove::<PendingFanOut>();
        // A fan-out stage's own keys when there are any, so a stage that set
        // `max_items` or `on_worker_failure` still gets them; nothing when an
        // ordinary stage called the tool.
        let stage_config = world
            .get::<StageCursor>(entity)
            .and_then(|cursor| {
                world.get::<AgentBlueprint>(entity).map(|bp| {
                    match &bp.0.stages[cursor.index].mode {
                        StageMode::FanOut { config } => Some(config.clone()),
                        _ => None,
                    }
                })
            })
            .flatten();
        // Which door this came through, and so how its report is delivered. A
        // `mode = "fan_out"` stage answers with the same tool call as anybody
        // else, so the call cannot tell us - only the stage can.
        //
        // Getting this wrong made `results_region` and `merge_stage` dead
        // config: a live `deep-researcher` fan-out delivered three workers'
        // findings into `conversation` as a tool result and resumed the split
        // stage, instead of writing `sub_findings` and moving to `analyze`. The
        // unit tests passed throughout, because they build the origin directly
        // and never went through this decision.
        // Which door this came through, and so how its report is delivered. A
        // `mode = "fan_out"` stage answers with the same tool call as anybody
        // else, so the call cannot tell us - only the stage can.
        //
        // Getting this wrong made `results_region` and `merge_stage` dead
        // config: a live `deep-researcher` fan-out delivered three workers'
        // findings into `conversation` as a tool result and resumed the split
        // stage, instead of writing `sub_findings` and moving to `analyze`. The
        // unit tests passed throughout, because they build the origin directly
        // and never went through this decision.
        let origin = match stage_config.is_some() {
            true => FanOutOrigin::Stage,
            false => FanOutOrigin::Tool { call_id },
        };
        if let Some(stage_config) = stage_config.as_ref()
            && let Some(region_name) = stage_config.items_region.as_deref()
        {
            // The model may request the deterministic split with `items = []`,
            // but it cannot smuggle in a second inventory or replace the
            // blueprint-owned worker/cap settings through tool arguments.
            if !request.items.is_empty() || request.agent.is_some() || request.max_workers.is_some()
            {
                crate::pipeline::fail_stage_world(
                    world,
                    entity,
                    "fan_out authoritative items_region forbids item, agent, and max_workers overrides"
                        .to_string(),
                );
                continue;
            }
            let items = match authoritative_items(
                world
                    .get::<ContextWindow>(entity)
                    .expect("fan-out parent has a context window"),
                region_name,
                stage_config.max_items,
            ) {
                Ok(items) => items,
                Err(message) => {
                    crate::pipeline::fail_stage_world(world, entity, message);
                    continue;
                }
            };
            let config = config_for(&request, Some(stage_config));
            begin_fan_out(world, entity, config, items, origin);
            continue;
        }
        let mut config = config_for(&request, stage_config.as_ref());
        // A call through the tool comes from an ordinary stage, so it carries no
        // `max_items` and creates as many workers as the model named. Where the
        // blueprint declares a fan-out stage, that stage's ceiling is the
        // author's answer to "how wide should a split of this work be", and a
        // split of this work is what this is. Measured: a blueprint saying
        // `max_items = 3` produced six-way splits through this door, and one run
        // reached 34 sub-agents where an earlier one reached 7.
        //
        // Only the ceiling is inherited. `worker_agent`, `merge_stage` and
        // `results_region` describe how a *stage* delivers its report, and
        // taking those would change where this call's result goes.
        if config.max_items.is_none() {
            config.max_items = blueprint_fan_out_max_items(world, entity);
        }
        begin_fan_out(world, entity, config, request.items, origin);
    }
}

/// Start a fan-out: park `parent` on its workers.
///
/// The single way in. Both entry points - the `fan_out` tool from any stage, and
/// a `fan_out` stage's own call - land here with a config and a list, and
/// everything after this point is [`fan_out_collect`] regardless of which it was.
///
/// An empty list is allowed and is not a special case: the parent parks with
/// nothing pending, the collector finds nothing running, and it finishes on the
/// next tick with an empty report. That is what "there is nothing to hand out"
/// has to do, and making it a separate path is how it would drift.
pub(crate) fn begin_fan_out(
    world: &mut World,
    parent: Entity,
    config: FanOutConfig,
    items: Vec<WorkItem>,
    origin: FanOutOrigin,
) {
    // Unlimited (`max_workers = 0`) is the largest cap there is, rather than a
    // separate flag: the start loop compares against it and nothing else.
    let max_workers = config.worker_cap().unwrap_or(usize::MAX);
    // A caller decides its own item count, so without a cap a model that returns
    // five hundred items spawns five hundred runs. The cap also fixes each
    // worker's share of the results region: past some number of ways to divide
    // it, every section is too small to say anything.
    let items = match config.max_items {
        Some(cap) if items.len() > cap => {
            tracing::warn!(
                produced = items.len(),
                cap,
                "fan_out produced more items than max_items; keeping the first"
            );
            items.into_iter().take(cap).collect::<Vec<_>>()
        }
        _ => items,
    };
    // Kept for the next entry into this stage, which is told what was already
    // handed out rather than being asked the same question over a context that
    // answers it.
    world
        .entity_mut(parent)
        .insert(PreviousWorkItems(
            items.iter().map(|i| i.id.clone()).collect(),
        ))
        .insert(FannedOut)
        .insert(FanOutWaiting {
            config,
            max_workers,
            pending: items.into_iter().collect(),
            active: Vec::new(),
            summaries: Vec::new(),
            failures: Vec::new(),
            paused: false,
            origin,
        });
    set_status(world, parent, AgentStatus::Waiting);
}
