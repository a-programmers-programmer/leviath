//! Collecting fan-out workers: reaping, merging, and delivering results.
use super::*;

/// How many workers one pass of [`fan_out_collect`] will start before
/// handing the tick back. A bound on how long one fan-out can hold the driver
/// thread; the queue drains over the following passes of the same wake.
pub(crate) const MAX_WORKER_STARTS_PER_PASS: usize = 4;

/// Fan-out collect system (exclusive): drive each [`FanOutWaiting`] parent - reap
/// finished workers, start pending ones up to `max_workers`, and once none remain
/// running apply the failure policy, inject the consolidated report, and
/// transition to the merge stage (or resolve the stage's own transition).
pub(crate) fn fan_out_collect(world: &mut World) {
    crate::tick_scope::clear();
    let parents: Vec<Entity> = {
        let mut q = world.query_filtered::<Entity, With<FanOutWaiting>>();
        q.iter(world).collect()
    };

    for parent in parents {
        crate::tick_scope::enter(parent);
        // A cancelled/errored parent abandons the fan-out; its workers are reaped
        // by the host's cascade cancel (which walks SubAgentChildren).
        if !matches!(agent_status(world, parent), Some(AgentStatus::Waiting)) {
            world.entity_mut(parent).remove::<FanOutWaiting>();
            continue;
        }
        // A `Waiting` parent from the query above still holds its `FanOutWaiting`
        // (only this system removes it, and each entity appears once per pass).
        let mut w = world
            .entity_mut(parent)
            .take::<FanOutWaiting>()
            .expect("a Waiting fan-out parent still holds FanOutWaiting");

        // 1. Reap workers that have reached a terminal state. A consumed
        // worker's result now lives in `w.summaries`/`w.failures`, so its heavy
        // components are dead weight - mark it for `slim_merged_workers`, which
        // drops them once the terminal snapshot has reached the persistence
        // lane. The entity itself stays (the host only despawns it when the
        // parent goes terminal), but without its context window, which would
        // otherwise stay resident for the whole remainder of the parent's run.
        let mut still_active = Vec::with_capacity(w.active.len());
        for aw in std::mem::take(&mut w.active) {
            match worker_terminal_result(world, aw.entity) {
                Some(result) => {
                    // Before the marker below hands this worker to
                    // `slim_merged_workers`, which drops its context window:
                    // after that its bibliography is only on disk.
                    merge_worker_sources(world, parent, aw.entity, &aw.item_id);
                    match result {
                        Ok(content) => w.summaries.push((aw.item_id, content)),
                        Err(message) => w.failures.push((aw.item_id, message)),
                    }
                    world.entity_mut(aw.entity).insert(MergedWorker);
                }
                None => still_active.push(aw),
            }
        }
        w.active = still_active;

        // 2. Start pending workers up to the concurrency cap - unless the
        // fan-out is paused, in which case the queue stays where it is. Reaping
        // above still runs: a worker that finished before the pause landed has a
        // result worth keeping.
        //
        // At most `MAX_WORKER_STARTS_PER_PASS` per pass. Starting a worker
        // means reading and checking its blueprint, compiling its script
        // tools and building its sandbox, on the driver thread, with every
        // other run frozen; the rest of the queue starts on the next pass of
        // the same wake, after the other systems have had their turn.
        let mut started_this_pass = 0usize;
        while !w.paused && w.active.len() < w.max_workers {
            if started_this_pass >= MAX_WORKER_STARTS_PER_PASS {
                break;
            }
            let Some(item) = w.pending.pop_front() else {
                break;
            };
            let started_at = std::time::Instant::now();
            match start_worker(world, parent, &w.config, &item) {
                Ok(child) => {
                    started_this_pass += 1;
                    tracing::info!(
                        item = %item.id,
                        spawn_ms = started_at.elapsed().as_millis() as u64,
                        "fan-out worker started"
                    );
                    // Capture the worker's run-id so the waiting state persists.
                    let run_id = world
                        .get::<crate::persistence::RunMetadata>(child)
                        .map(|m| m.run_id.clone())
                        .unwrap_or_default();
                    w.active.push(ActiveWorker {
                        item_id: item.id,
                        entity: child,
                        run_id,
                    });
                }
                Err(message) => w.failures.push((item.id, message)),
            }
        }

        // 3. Finished when nothing is running or queued.
        if w.active.is_empty() && w.pending.is_empty() {
            finish_fan_out(world, parent, w);
        } else {
            world.entity_mut(parent).insert(w);
        }
    }
}

/// A fan-out worker whose terminal result the parent has already consumed.
/// Set by [`fan_out_collect`]; consumed by [`slim_merged_workers`].
#[derive(Component)]
pub(crate) struct MergedWorker;

/// Drop a merged worker's heavy components once its terminal snapshot has
/// reached the persistence lane.
///
/// Ordering makes this safe on both sides: the marker is only set after the
/// parent consumed the worker's result (so the merge no longer reads the
/// worker), and the watermark gate (`PersistWatermark::persisted_status`)
/// holds the slim back until the terminal state is on its way to disk (so
/// nothing readable is lost - the entity's remaining metadata still identifies
/// the run, and its full final state is in the run dir).
pub(crate) fn slim_merged_workers(
    workers: Query<(Entity, &crate::pipeline::PersistWatermark), With<MergedWorker>>,
    mut commands: Commands,
) {
    crate::tick_scope::clear();
    for (entity, watermark) in workers.iter() {
        crate::tick_scope::enter(entity);
        let terminal_persisted = matches!(
            watermark.persisted_status(),
            Some(
                leviath_core::run_meta::RunStatus::Complete
                    | leviath_core::run_meta::RunStatus::Error
                    | leviath_core::run_meta::RunStatus::Cancelled
            )
        );
        if !terminal_persisted {
            continue; // the terminal snapshot has not been dispatched yet
        }
        commands.entity(entity).remove::<(
            ContextWindow,
            InferenceResult,
            crate::pipeline::StageInferences,
            crate::pipeline::StageSetups,
            AgentBlueprint,
            MergedWorker,
        )>();
    }
}

/// Apply the failure policy, inject the consolidated report, and transition.
pub(super) fn finish_fan_out(world: &mut World, parent: Entity, w: FanOutWaiting) {
    if !w.failures.is_empty() && w.config.on_worker_failure == WorkerFailurePolicy::FailAll {
        // Down the stage's `error` edge, which is what `WorkerFailurePolicy::FailAll`
        // has always been documented as doing. Writing the status alone made it a
        // dead run instead, so a blueprint with an `error_recovery` stage got the
        // recovery it declared only for provider failures, never for this.
        crate::pipeline::fail_stage_world(
            world,
            parent,
            format!(
                "fan_out: {} worker(s) failed (on_worker_failure = fail_all)",
                w.failures.len()
            ),
        );
        return;
    }

    // Everything above this line is common to both entry points; everything
    // below is the one thing that differs between them.
    match &w.origin {
        FanOutOrigin::Stage => finish_stage_fan_out(world, parent, &w),
        FanOutOrigin::Tool { call_id } => {
            let call_id = call_id.clone();
            finish_tool_fan_out(world, parent, &w, &call_id);
        }
    }
}

/// A `mode = "fan_out"` stage: the report goes to the region the blueprint named
/// and the stage moves on to its `merge_stage`.
pub(super) fn finish_stage_fan_out(world: &mut World, parent: Entity, w: &FanOutWaiting) {
    // Where the results land, and how much room they have there. A blueprint
    // that names a region of its own gets that region's budget to divide; the
    // default is the conversation region, which is also carrying the message
    // history.
    let region = w
        .config
        .results_region
        .clone()
        .unwrap_or_else(|| "conversation".to_string());
    let budget = world
        .get::<ContextWindow>(parent)
        .and_then(|window| window.get_region(&region).map(|r| r.max_tokens));
    let report = build_report(&w.summaries, &w.failures, budget);
    inject_results(world, parent, &region, &report);

    leave_fan_out(world, parent, &w.config);
}

/// A `fan_out` tool call: the report is that call's result, and the agent picks
/// up its stage where it left off.
///
/// Routed through the same path every other tool result takes, so the stage's
/// `tool_routing` decides where it lands - a region of its own, the conversation,
/// or (for a blueprint whose workers write files and whose parent does not need
/// to read their prose) somewhere it is cheaply dropped. That flexibility is not
/// a fan-out feature; it is the one every tool already has.
pub(super) fn finish_tool_fan_out(
    world: &mut World,
    parent: Entity,
    w: &FanOutWaiting,
    call_id: &str,
) {
    let routing = world
        .get::<crate::components::ToolResultRoutingComponent>(parent)
        .map(|r| r.routing.clone());
    let region = routing
        .as_ref()
        .map(|r| {
            r.tool_overrides
                .iter()
                .find(|(k, _)| {
                    leviath_tools::canonical_tool_name(k) == leviath_core::blueprint::FAN_OUT_TOOL
                })
                .map(|(_, v)| v.clone())
                .unwrap_or_else(|| r.default_region.clone())
        })
        .unwrap_or_else(|| "conversation".to_string());
    // Sized against the region the result is actually routed to, so a report
    // headed for a big `sub_findings` is not trimmed to fit a conversation it
    // never enters.
    let budget = world
        .get::<ContextWindow>(parent)
        .and_then(|window| window.get_region(&region).map(|r| r.max_tokens));
    let report = build_report(&w.summaries, &w.failures, budget);
    let sensitivities = world
        .get::<crate::pipeline::ToolSensitivities>(parent)
        .map(|s| s.0.clone());
    if let Some(mut window) = world.get_mut::<ContextWindow>(parent) {
        crate::pipeline::apply_one_tool_result(
            &mut window,
            leviath_core::blueprint::FAN_OUT_TOOL,
            call_id,
            report.into(),
            routing.as_ref(),
            sensitivities.as_ref(),
        );
    }
    set_status(world, parent, AgentStatus::Active);
    world
        .entity_mut(parent)
        .insert(crate::pipeline::ReadyToInfer);
}

/// Ready the parent to run again and move it on: to the `merge_stage` when the
/// config names one, otherwise letting the fan-out stage's own transition
/// resolve.
///
/// Shared by the normal completion and by the never-terminal split failure, so
/// both leave the stage by the same door.
pub(super) fn leave_fan_out(world: &mut World, parent: Entity, config: &FanOutConfig) {
    set_status(world, parent, AgentStatus::Active);
    match config.merge_stage.as_deref().and_then(|name| {
        world
            .get::<AgentBlueprint>(parent)
            .and_then(|bp| bp.0.stages.iter().position(|s| s.name == name))
    }) {
        Some(idx) => crate::pipeline::force_transition(
            world,
            crate::world::AgentId::in_world(world, parent),
            idx,
        ),
        None => {
            world.entity_mut(parent).insert(ResolveTransition);
        }
    }
}
