//! The region-backed fan-out path: a stage whose items come from a context region.
use super::*;

/// A deterministic fan-out stage waiting for its entry hooks to finish before
/// the authoritative item region is consumed. This marker is installed before
/// normal inference dispatch so a region-backed split never spends a model
/// turn on the old splitter prompt.
#[derive(Component, Debug, Clone, Copy, Default)]
pub(crate) struct AuthoritativeFanOutPending;

/// Hold a region-backed fan-out stage out of ordinary inference dispatch.
///
/// This runs before `run_before_inference_hooks`/`dispatch_inference`. The
/// second half, [`start_authoritative_fanouts`], runs after stage-entry hooks
/// and current tool resolution, so hooks can prepare the region while the
/// model still receives no splitter request.
pub(crate) fn prepare_authoritative_fanouts(
    mut agents: Query<
        (Entity, &'static AgentBlueprint, &'static StageCursor),
        With<crate::pipeline::ReadyToInfer>,
    >,
    mut commands: Commands,
) {
    crate::tick_scope::clear();
    for (entity, blueprint, cursor) in agents.iter_mut() {
        let Some(stage) = blueprint.0.stages.get(cursor.index) else {
            continue;
        };
        let deterministic = matches!(
            &stage.mode,
            StageMode::FanOut { config } if config.items_region.is_some()
        );
        if !deterministic {
            continue;
        }
        crate::tick_scope::enter(entity);
        commands
            .entity(entity)
            .remove::<crate::pipeline::ReadyToInfer>()
            .insert(AuthoritativeFanOutPending);
    }
}

/// Consume deterministic fan-out inputs after stage-entry hooks and current
/// tool resolution have run. This validates the authoritative region, then
/// enters the ordinary tool-dispatch path so stage grants, taint checks, and
/// consent still apply before workers are launched.
pub(crate) fn start_authoritative_fanouts(world: &mut World) {
    crate::tick_scope::clear();
    let candidates: Vec<Entity> = {
        let mut query = world.query_filtered::<(
            Entity,
            &AgentBlueprint,
            &StageCursor,
            &AgentState,
            &ContextWindow,
        ), With<AuthoritativeFanOutPending>>();
        query
            .iter(world)
            .filter_map(|(entity, _, _, state, _)| {
                matches!(state.status, AgentStatus::Active).then_some(entity)
            })
            .collect()
    };
    for entity in candidates {
        crate::tick_scope::enter(entity);
        let Some((_config, items_result)) = (|| {
            let blueprint = world.get::<AgentBlueprint>(entity)?;
            let cursor = world.get::<StageCursor>(entity)?;
            let stage = blueprint.0.stages.get(cursor.index)?;
            let StageMode::FanOut { config } = &stage.mode else {
                return None;
            };
            let window = world.get::<ContextWindow>(entity)?;
            let region = config.items_region.as_deref()?;
            let items = authoritative_items(window, region, config.max_items);
            Some((config.clone(), items))
        })() else {
            world
                .entity_mut(entity)
                .remove::<AuthoritativeFanOutPending>();
            continue;
        };
        world
            .entity_mut(entity)
            .remove::<AuthoritativeFanOutPending>()
            .remove::<crate::pipeline::ReadyToInfer>()
            .remove::<crate::pipeline::ProcessResponse>();
        match items_result {
            Ok(_) => {}
            Err(message) => {
                crate::pipeline::fail_stage_world(world, entity, message);
                continue;
            }
        }
        // Feed the normal tool-dispatch path. In particular, do not call
        // `begin_fan_out` here: available tools are only an advertisement,
        // while the dispatcher owns stage grants, taint checks, and consent.
        // The authoritative items have already been validated; the empty
        // request tells `start_pending_fan_outs` to read them again from the
        // approved region without allowing a model-shaped override.
        world.entity_mut(entity).insert((
            InferenceResult {
                response: String::new(),
                tool_calls: vec![crate::components::ToolCall {
                    tool_id: format!(
                        "authoritative-fan-out-{}-{}",
                        entity.to_bits(),
                        std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_nanos()
                    ),
                    name: leviath_core::blueprint::FAN_OUT_TOOL.to_string(),
                    arguments: serde_json::json!({ "items": [] }),
                    thought_signature: None,
                }],
                tokens_used: 0,
                cut_off_at: None,
                reasoning: None,
                // Upstream added `parts` to InferenceResult (the mime the model
                // produced, already in the run's store). This synthesised result
                // comes from the fan-out path, not a model reply, so it produces
                // no parts. Dropped by the upstream sync merge; restored here.
                parts: Vec::new(),
            },
            crate::pipeline::ReadyForTools,
        ));
    }
}
