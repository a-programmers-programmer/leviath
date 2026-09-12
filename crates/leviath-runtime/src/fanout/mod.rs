//! The fan-out engine: many sub-agents at once, and their results together.
//!
//! # One engine, two entry points
//!
//! Both start at `begin_fan_out`, which parks the parent in [`FanOutWaiting`].
//! `fan_out_collect` then starts one worker per item - bounded by
//! `max_workers` - through the daemon-installed [`FanOutSpawner`], tracks them
//! as the parent's `SubAgentChildren`, and once every worker is terminal applies
//! the failure policy and builds the consolidated report.
//!
//! What differs is only how that report is delivered, which is exactly what
//! [`FanOutOrigin`] records:
//!
//! - **The [`FAN_OUT_TOOL`] tool**, callable from any stage that grants it. The
//!   dispatcher reads the call (`parse_fan_out_call`), hands it over as
//!   `PendingFanOut`, and `start_pending_fan_outs` begins it. The report
//!   comes back as that call's tool result, routed by the stage's
//!   `tool_routing` like any other, and the agent carries on where it was.
//! - **A `mode = "fan_out"` stage** (see
//!   [`leviath_core::blueprint::StageMode::FanOut`]), which is sugar for
//!   granting the same tool: its report goes to the config's `results_region`
//!   and the stage transitions to its `merge_stage`.
//!
//! Because both park the same way, both survive a daemon restart through
//! `fanout.json` (see [`FanOutState`]).
//!
//! # What lives elsewhere
//!
//! The runtime only **starts and tracks** workers; resolving *which* blueprint a
//! worker runs (self-at-worker-stage, a named agent, or a capability query) is
//! the CLI's job, encapsulated behind the [`FanOutSpawner`] it installs.
//!
//! A single sub-agent is `spawn_agent`, not a fan-out of one.
//!
//! [`FAN_OUT_TOOL`]: leviath_core::blueprint::FAN_OUT_TOOL
mod report;
mod worker_sources;
use report::*;
use worker_sources::merge_worker_sources;
mod framing;
mod authoritative;
mod requests;
mod collect;
use framing::*;
use authoritative::*;
use requests::*;
use collect::*;
pub(crate) use framing::frame_split_round;
pub(crate) use authoritative::{
    AuthoritativeFanOutPending, prepare_authoritative_fanouts, start_authoritative_fanouts,
};

use std::collections::{HashSet, VecDeque};
use std::sync::Arc;

use bevy_ecs::prelude::*;
use leviath_core::blueprint::{FanOutConfig, StageMode, WorkerFailurePolicy};

use crate::components::{
    AgentState, AgentStatus, ContextWindow, InferenceResult, ParentRef, SubAgentChildren,
};
use crate::pipeline::{AgentBlueprint, ResolveTransition, StageCursor};

/// Depth cap for fan-out workers when the parent's blueprint doesn't set one.
const DEFAULT_FANOUT_DEPTH: usize = 3;

/// Starts one worker for a fan-out work item. The implementor resolves the
/// worker's blueprint (per `config`'s `worker_stage` / `worker_agent` /
/// `worker_query`), spawns it into `world` seeded with the work item, and returns
/// the child entity. Parent/child linking is done by `fan_out_collect`, not the
/// spawner.
pub trait FanOutSpawner: Send + Sync {
    /// Spawn one worker under `parent` for the given work item, or `Err` with a
    /// human-readable reason (recorded as that item's failure).
    fn spawn_worker(
        &self,
        world: &mut World,
        parent: Entity,
        config: &FanOutConfig,
        item_id: &str,
        item_context: &serde_json::Value,
    ) -> Result<Entity, String>;
}

/// The installed [`FanOutSpawner`], as a world resource. Absent in a pure-runtime
/// world (then every fan-out item fails with "no fan-out spawner installed").
#[derive(Resource, Clone)]
pub struct FanOutSpawnerRes(pub Arc<dyn FanOutSpawner>);

/// A currently-running fan-out worker: its work-item id, its live entity, and
/// its run-id (kept so the waiting state can be persisted/restored without a
/// cross-entity lookup - see [`FanOutState`]).
pub(super) struct ActiveWorker {
    item_id: String,
    entity: Entity,
    run_id: String,
}

/// How a fan-out was started, and so how its results come back.
///
/// One engine, two entry points. The workers, the concurrency cap, the failure
/// policy and the merged report are identical either way; only the last step
/// differs, and this is the whole of that difference.
#[derive(Debug, Clone, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub enum FanOutOrigin {
    /// A `mode = "fan_out"` stage. The report goes to the config's
    /// `results_region` and the stage transitions to its `merge_stage`.
    ///
    /// The default so a `fanout.json` written before the tool existed still
    /// loads, as the only thing it could have been.
    #[default]
    Stage,
    /// A `fan_out` tool call from an ordinary stage. The report comes back as
    /// that call's result - routed by the stage's `tool_routing` like any other,
    /// so the blueprint decides where it lands or whether it lands at all - and
    /// the agent carries on where it left off.
    Tool {
        /// The tool call this fan-out is the result of.
        call_id: String,
    },
}

/// A parent parked while its fan-out workers run. Holds the not-yet-started
/// `pending` items, the currently-`active` workers, and the accumulated results.
#[derive(Component)]
pub struct FanOutWaiting {
    config: FanOutConfig,
    max_workers: usize,
    pending: VecDeque<WorkItem>,
    active: Vec<ActiveWorker>,
    summaries: Vec<(String, String)>,
    failures: Vec<(String, String)>,
    /// Set when the user pauses this parent. Its own status has to stay
    /// `Waiting` - the merge poll reads it - so the pause lives here instead,
    /// and holds back the one thing a parked parent still does on its own:
    /// starting the next queued worker. Without it, pausing a fan-out would
    /// pause the running children and immediately launch their replacements.
    paused: bool,
    /// Which entry point started this, and so how its report is delivered.
    origin: FanOutOrigin,
}

/// The serializable form of [`FanOutWaiting`], written to `<run_dir>/fanout.json`
/// so a parent interrupted mid-split resumes its merge after a restart. `active`
/// carries worker **run-ids** (not entities); recovery maps them back to the
/// reloaded worker entities.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct FanOutState {
    /// The fan-out configuration.
    pub config: FanOutConfig,
    /// The concurrency cap; `usize::MAX` for a stage with `max_workers = 0`.
    pub max_workers: usize,
    /// Work items not yet started.
    pub pending: Vec<WorkItem>,
    /// In-flight workers as `(item_id, run_id)`.
    pub active: Vec<(String, String)>,
    /// Completed worker results as `(item_id, summary)`.
    pub summaries: Vec<(String, String)>,
    /// Failed worker results as `(item_id, message)`.
    pub failures: Vec<(String, String)>,
    /// Whether the fan-out was paused. `default` so a state written by an older
    /// build still loads, as an un-paused one.
    #[serde(default)]
    pub paused: bool,
    /// Which entry point started this. `default` (a stage) for the same reason.
    #[serde(default)]
    pub origin: FanOutOrigin,
}

impl FanOutWaiting {
    /// Workers this parent is still parked on: in-flight plus not-yet-started.
    ///
    /// Surfaced by `lev ps` so "waiting" on a fan-out parent reads as progress
    /// against a known denominator rather than an unexplained stall.
    pub(crate) fn outstanding(&self) -> usize {
        self.active.len() + self.pending.len()
    }

    /// Whether this parent's fan-out is paused (see the field).
    #[cfg(test)]
    pub(crate) fn is_paused(&self) -> bool {
        self.paused
    }

    /// Latch or release the fan-out. Returns whether this changed anything, so
    /// a caller can tell a real pause from a repeat.
    pub(crate) fn set_paused(&mut self, paused: bool) -> bool {
        let changed = self.paused != paused;
        self.paused = paused;
        changed
    }

    /// Project to the serializable [`FanOutState`] (workers by run-id).
    pub(crate) fn to_state(&self) -> FanOutState {
        FanOutState {
            config: self.config.clone(),
            origin: self.origin.clone(),
            max_workers: self.max_workers,
            pending: self.pending.iter().cloned().collect(),
            active: self
                .active
                .iter()
                .map(|w| (w.item_id.clone(), w.run_id.clone()))
                .collect(),
            summaries: self.summaries.clone(),
            failures: self.failures.clone(),
            paused: self.paused,
        }
    }
}

/// Rebuild a parent's [`FanOutWaiting`] from a persisted [`FanOutState`] and
/// insert it, mapping each active worker's run-id back to its reloaded entity
/// via `resolve`. Workers whose entity didn't reload are treated as failures so
/// the merge still completes rather than waiting forever. Used by restart
/// recovery to resume an interrupted fan-out.
pub fn restore_fan_out_waiting(
    world: &mut World,
    parent: Entity,
    state: FanOutState,
    resolve: &dyn Fn(&str) -> Option<Entity>,
) {
    let mut active = Vec::new();
    let mut failures = state.failures;
    for (item_id, run_id) in state.active {
        match resolve(&run_id) {
            Some(entity) => active.push(ActiveWorker {
                item_id,
                entity,
                run_id,
            }),
            None => failures.push((item_id, "worker did not reload after restart".to_string())),
        }
    }
    world.entity_mut(parent).insert(FanOutWaiting {
        origin: state.origin.clone(),
        config: state.config,
        max_workers: state.max_workers,
        pending: state.pending.into_iter().collect(),
        active,
        summaries: state.summaries,
        failures,
        paused: state.paused,
    });
}

/// Set on an agent that has started a fan-out in its current stage.
///
/// Cleared on stage entry, unlike [`PreviousWorkItems`], because the two answer
/// different questions: "has this entry fanned out yet" gates the nudge, and
/// "what did the last round cover" is what the next round is told.
#[derive(Component, Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct FannedOut;

/// The ids a fan-out stage's last split handed out, so a later split of the same
/// stage can be told what has already been researched.
///
/// Set on every successful split and read only on a re-entry. Absent means this
/// stage has not split before.
#[derive(Component, Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct PreviousWorkItems(pub Vec<String>);

/// One unit of work produced by a fan-out call.
#[derive(serde::Serialize, serde::Deserialize, Debug, Clone, Default, PartialEq, Eq)]
pub struct WorkItem {
    /// Stable id (used to label the worker in the consolidated report).
    #[serde(default)]
    pub id: String,
    /// Free-form context handed to the worker (seeded into its pinned context).
    #[serde(default)]
    pub context: serde_json::Value,
}

/// Read a fan-out split supplied by an authoritative context region.
///
/// This path deliberately does not reuse [`parse_fan_out_call`]. The model's
/// tool arguments are an untrusted trigger; the region is the run's canonical
/// work inventory. Exactly one region entry must contain one JSON array, each
/// item must contain exactly `id` and `context`, and all ids must be unique.
/// Any ambiguity is an error instead of a best-effort split or a truncation.
pub(super) fn authoritative_items(
    window: &ContextWindow,
    region_name: &str,
    max_items: Option<usize>,
) -> Result<Vec<WorkItem>, String> {
    let region = window
        .get_region(region_name)
        .ok_or_else(|| format!("fan_out items_region '{region_name}' does not exist"))?;
    if region.content.len() != 1 {
        return Err(format!(
            "fan_out items_region '{region_name}' must contain exactly one JSON array entry"
        ));
    }
    let value: serde_json::Value = serde_json::from_str(&region.content[0].content)
        .map_err(|e| format!("fan_out items_region '{region_name}' is not valid JSON: {e}"))?;
    let array = value
        .as_array()
        .ok_or_else(|| format!("fan_out items_region '{region_name}' must contain a JSON array"))?;
    if let Some(cap) = max_items.filter(|cap| array.len() > *cap) {
        return Err(format!(
            "fan_out items_region '{region_name}' contains {} items, over max_items {cap}",
            array.len()
        ));
    }

    let mut ids = HashSet::with_capacity(array.len());
    let mut items = Vec::with_capacity(array.len());
    for (index, value) in array.iter().enumerate() {
        let object = value.as_object().ok_or_else(|| {
            format!("fan_out items_region '{region_name}' item {index} must be an object")
        })?;
        if object.len() != 2 || !object.contains_key("id") || !object.contains_key("context") {
            return Err(format!(
                "fan_out items_region '{region_name}' item {index} must contain exactly id and context"
            ));
        }
        let id = object
            .get("id")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| {
                format!(
                    "fan_out items_region '{region_name}' item {index} id must be a non-empty string"
                )
            })?;
        if id.is_empty() || id.trim() != id {
            return Err(format!(
                "fan_out items_region '{region_name}' item {index} id must be a non-empty string without surrounding whitespace"
            ));
        }
        if !ids.insert(id.to_string()) {
            return Err(format!(
                "fan_out items_region '{region_name}' contains duplicate id '{id}'"
            ));
        }
        items.push(WorkItem {
            id: id.to_string(),
            context: object.get("context").expect("validated above").clone(),
        });
    }
    Ok(items)
}

/// How many agents one run may create, sub-agents included, or `0` for no limit.
///
/// Read at every fan-out spawn. A run at its ceiling stops widening and finishes
/// on what it has, rather than failing: the work already done is worth keeping,
/// and a run that stopped early is a cheaper answer, not a broken one.
///
/// The operator's number rather than the blueprint's. Cost per agent is stable
/// (measured $5.37 to $9.05 across four runs) while the count is not (10 to 42),
/// so this is the knob that decides what a run costs - and whose account it
/// costs it to is not something a blueprint author can know.
#[derive(bevy_ecs::prelude::Resource, Clone, Copy, Debug, Default)]
pub struct FanOutBudget(pub usize);

/// Every agent in this run's tree, counted from its root.
///
/// Walked from the root rather than the spawning parent: a depth-2 worker asking
/// "how many of us are there" must not answer with the size of its own branch.
fn run_tree_size(world: &World, entity: Entity) -> usize {
    let mut root = entity;
    while let Some(parent) = world.get::<ParentRef>(root) {
        root = parent.parent_entity;
    }
    fn count(world: &World, at: Entity) -> usize {
        1 + world
            .get::<SubAgentChildren>(at)
            .map(|kids| {
                kids.children
                    .iter()
                    .map(|c| count(world, *c))
                    .sum::<usize>()
            })
            .unwrap_or(0)
    }
    count(world, root)
}

/// The item ceiling this blueprint declares on any fan-out stage it has.
///
/// `None` when it declares none, which leaves a tool-driven split unbounded, as
/// it has always been. Read from the blueprint rather than the current stage on
/// purpose: the tool is called from ordinary stages, which is the whole reason
/// the ceiling was being missed.
pub(super) fn blueprint_fan_out_max_items(world: &World, entity: Entity) -> Option<usize> {
    world
        .get::<AgentBlueprint>(entity)?
        .0
        .stages
        .iter()
        .find_map(|stage| match &stage.mode {
            StageMode::FanOut { config } => config.max_items,
            _ => None,
        })
}

/// Start one worker and link it to `parent` (`ParentRef` + `SubAgentChildren`),
/// enforcing the parent blueprint's child-depth cap. Returns the child entity.
pub(super) fn start_worker(
    world: &mut World,
    parent: Entity,
    config: &FanOutConfig,
    item: &WorkItem,
) -> Result<Entity, String> {
    let max_depth = world
        .get::<SubAgentChildren>(parent)
        .map(|k| k.max_child_depth)
        .or_else(|| {
            world
                .get::<AgentBlueprint>(parent)
                .and_then(|bp| bp.0.max_child_depth)
        })
        .unwrap_or(DEFAULT_FANOUT_DEPTH);
    let parent_depth = world.get::<ParentRef>(parent).map_or(0, |p| p.depth);
    let child_depth = parent_depth + 1;
    if child_depth > max_depth {
        return Err(format!(
            "fan-out worker depth limit ({max_depth}) reached; not spawning"
        ));
    }
    // The run's own ceiling, beside the depth one. Refusing here rather than at
    // the split means the items already started keep running and the merge still
    // happens on what came back: a run that stopped widening is a cheaper answer,
    // not a failure.
    let budget = world.get_resource::<FanOutBudget>().map_or(0, |b| b.0);
    let live = run_tree_size(world, parent);
    if budget > 0 && live >= budget {
        return Err(format!(
            "this run already has {live} agents and its ceiling is {budget} \
             ([limits] max_agents_per_run); not spawning another"
        ));
    }

    let spawner = world
        .get_resource::<FanOutSpawnerRes>()
        .map(|r| r.0.clone())
        .ok_or_else(|| "no fan-out spawner installed".to_string())?;
    let child = spawner.spawn_worker(world, parent, config, &item.id, &item.context)?;

    let parent_agent_id = world
        .get::<AgentState>(parent)
        .map(|s| s.agent_id.clone())
        .unwrap_or_default();
    world.entity_mut(child).insert(ParentRef {
        parent_entity: parent,
        parent_agent_id,
        depth: child_depth,
    });
    match world.get_mut::<SubAgentChildren>(parent) {
        Some(mut kids) => kids.children.push(child),
        None => {
            world.entity_mut(parent).insert(SubAgentChildren {
                children: vec![child],
                max_child_depth: max_depth,
            });
        }
    }
    // Record the worker's run-id on the parent's serializable state so the tree
    // (fan-out workers included) is persisted for a deterministic restart rebuild.
    // A freshly spawned worker always has run metadata; its parent always has state.
    let worker_id = world
        .get::<crate::persistence::RunMetadata>(child)
        .expect("a fan-out worker always has run metadata")
        .run_id
        .clone();
    world
        .get_mut::<AgentState>(parent)
        .expect("a fan-out parent always has AgentState")
        .spawned_children_ids
        .push(worker_id);
    // Seed the worker's context from the parent per any declared blueprint
    // context transform (when a fan-out worker runs a different blueprint).
    crate::context_transform::apply_context_transforms(
        world,
        crate::world::AgentId::in_world(world, parent),
        crate::world::AgentId::in_world(world, child),
    );
    Ok(child)
}

/// The one way a finished child's answer is read out of the world.
///
/// Precedence, unchanged since this was inline in [`worker_terminal_result`]:
/// the child's explicit `submit_output` ([`FinalOutput`](crate::persistence::FinalOutput))
/// always wins; failing that, its last non-empty `conversation` entry (a real
/// assistant/analysis message on tool-call-ending runs); failing that, its last
/// [`InferenceResult`]'s response. `None` only when the child holds none of the
/// three.
///
/// Shared with the host's sub-agent `Check` op so a child run answers its parent
/// the same way however it was started. That divergence was the bug: the fan-out
/// collector resolved this chain while the host read `FinalOutput` alone, so a
/// `spawn_agent` child that did its work in text - or whose submission never
/// landed as a component - handed its parent nothing at all, and the parent
/// reported an empty result over a child that had in fact finished.
///
/// Deliberately status-agnostic: callers decide whether a child is finished
/// enough to be read (`worker_terminal_result` only asks on `Complete`).
pub(crate) fn child_output_content(world: &World, child: Entity) -> Option<String> {
    // Explicit submit_output always wins.
    if let Some(content) = world
        .get::<crate::persistence::FinalOutput>(child)
        .map(|o| o.0.content.clone())
    {
        return Some(content);
    }

    // No explicit submit_output: fall back to the child's last non-empty
    // conversation text (a real assistant/analysis message on tool-call-ending
    // runs), then to InferenceResult.response.
    world
        .get::<ContextWindow>(child)
        .and_then(|w| w.get_region("conversation"))
        .and_then(|region| {
            region.content.iter().rev().find_map(|entry| {
                let text = entry.content.trim();
                (!text.is_empty()).then(|| text.to_owned())
            })
        })
        .or_else(|| {
            world
                .get::<InferenceResult>(child)
                .map(|r| r.response.clone())
        })
}

/// A worker's terminal result: `Some(Ok(deliverable))` if complete,
/// `Some(Err(reason))` if it errored/was cancelled/vanished, `None` if still
/// running.
///
/// A worker that called `submit_output` contributes exactly what it submitted.
/// Otherwise this falls back to the text of its last assistant message, which is
/// usually wrong: a worker whose final turn was a tool call has no trailing
/// text, so the merge stage gets an empty string, silently indistinguishable
/// from a worker that had nothing to say.
///
/// The fallback stays because it costs nothing and a blueprint that happens to
/// end on a text turn keeps working. A blueprint that wants the guarantee sets
/// `require_output` on its worker stage.
///
/// A worker whose stage set `require_output` and that finished without one is
/// reported as a **failure**, not as a success with empty content. It reached
/// `Complete` either way - the enforcement loop proceeds rather than stranding
/// the run, and a worker that burns its iterations against a validator it cannot
/// satisfy ends the same way. Counting that as success is how a fan-out reports
/// "10 succeeded, 0 failed" over ten empty sections, which is worse than an
/// error: the merge stage cannot tell an empty answer from a missing one, so it
/// writes a confident merge of nothing.
pub(super) fn worker_terminal_result(world: &World, worker: Entity) -> Option<Result<String, String>> {
    match agent_status(world, worker) {
        None => Some(Err("worker vanished".to_string())),
        Some(AgentStatus::Complete) => {
            // The shared resolution: submit_output, else last conversation text,
            // else the last inference response.
            let fallback = child_output_content(world, worker).unwrap_or_default();

            // If the stage requires an output and even the fallback is empty,
            // that is a real failure: the worker had nothing to say.  But a
            // worker that produced real content (just never called
            // submit_output) still relays it — the require_output guard catches
            // genuinely-empty workers, not workers that used the wrong delivery
            // channel.
            if worker_requires_output(world, worker) && fallback.is_empty() {
                return Some(Err(
                    "worker finished without the final output its stage requires".to_string(),
                ));
            }

            Some(Ok(fallback))
        }
        Some(AgentStatus::Error { message }) => Some(Err(message)),
        Some(AgentStatus::Cancelled) => Some(Err("worker cancelled".to_string())),
        Some(_) => None,
    }
}

/// Whether the stage this worker is sitting in demands a final output.
fn worker_requires_output(world: &World, worker: Entity) -> bool {
    let Some(bp) = world.get::<AgentBlueprint>(worker) else {
        return false;
    };
    let Some(cursor) = world.get::<StageCursor>(worker) else {
        return false;
    };
    bp.0.stages
        .get(cursor.index)
        .is_some_and(|s| s.require_output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::components::{InferenceConfig, ToolResultRoutingComponent};
    use crate::pipeline::{
        ProcessResponse, ReadyToInfer, StageInference, StageInferences, StageProgress, StageSetup,
        StageSetups, ToolProgress, ToolService, VisitCounts,
    };
    use leviath_core::blueprint::{ModelConfig, Stage};
    use leviath_core::layout::{ContextLayout, RegionDefinition};
    use leviath_core::{Blueprint, Region, RegionKind};
    use std::collections::HashSet;

    /// A spawner that spawns a trivial `Active` worker per item, refusing the ids
    /// in `fail`.
    struct TestSpawner {
        fail: HashSet<String>,
    }

    struct NoopToolService;

    impl ToolService for NoopToolService {
        fn exec_for(
            &self,
            _entity: Entity,
            _calls: Vec<leviath_providers::ToolCall>,
            _progress: ToolProgress,
        ) -> crate::tool_bridge::BoxedToolExec {
            Box::new(|| Box::pin(async { Vec::new() }))
        }
    }

    impl TestSpawner {
        fn ok() -> Arc<dyn FanOutSpawner> {
            Arc::new(TestSpawner {
                fail: HashSet::new(),
            })
        }
        fn refusing(ids: &[&str]) -> Arc<dyn FanOutSpawner> {
            Arc::new(TestSpawner {
                fail: ids.iter().map(|s| s.to_string()).collect(),
            })
        }
    }

    impl FanOutSpawner for TestSpawner {
        fn spawn_worker(
            &self,
            world: &mut World,
            _parent: Entity,
            _config: &FanOutConfig,
            item_id: &str,
            _item_context: &serde_json::Value,
        ) -> Result<Entity, String> {
            if self.fail.contains(item_id) {
                return Err(format!("spawn refused for '{item_id}'"));
            }
            Ok(world
                .spawn((
                    AgentState {
                        agent_id: format!("worker-{item_id}"),
                        current_stage: "w".to_string(),
                        iteration: 0,
                        status: AgentStatus::Active,
                        spawned_children_ids: vec![],
                        pending_wait: None,
                        accepts_messages: true,
                    },
                    // A real worker carries run metadata (attached by build_agent);
                    // mirror that so the parent can record the worker's run-id.
                    crate::persistence::RunMetadata {
                        run_id: format!("run-{item_id}"),
                        agent_name: "worker".to_string(),
                        agent_path: String::new(),
                        task: String::new(),
                        model: None,
                        workdir: String::new(),
                        num_stages: 1,
                        started_at: 0,
                        parent_run_id: None,
                        metadata: std::collections::HashMap::new(),
                        callback_url: None,
                        callback_secret: None,
                        title: None,
                        title_error: None,
                        unattended: false,
                        yolo_profile: None,
                        read_paths: None,
                        output_request: None,
                        model_override: None,
                    },
                ))
                .id())
        }
    }

    fn cfg(merge: Option<&str>, max_workers: usize, policy: WorkerFailurePolicy) -> FanOutConfig {
        FanOutConfig {
            worker_agent: None,
            worker_stage: Some("w".to_string()),
            worker_query: None,
            merge_stage: merge.map(String::from),
            max_workers,
            on_worker_failure: policy,
            split_prompt: "split".to_string(),
            items_region: None,
            results_region: None,
            max_items: None,
            max_attempts: None,
        }
    }

    fn window() -> ContextWindow {
        let mut w = ContextWindow::new(12_000);
        w.add_region(Region::new(
            "conversation".to_string(),
            RegionKind::Clearable,
            10_000,
        ));
        w
    }

    fn stage_inf() -> StageInference {
        StageInference {
            provider_name: "script".to_string(),
            model: "m".to_string(),
            tools: vec![],
            tool_filter: None,
            fallbacks: Vec::new(),
            output: None,
        }
    }

    fn setup() -> StageSetup {
        StageSetup {
            inference_config: InferenceConfig {
                temperature: None,
                max_output_tokens: None,
                extra_params: Default::default(),
                batch_tool_hint: false,
                shell_hint: false,
                request_timeout_secs: None,
                as_text: Vec::new(),
            },
            routing: None,
            accepts_messages: true,
            context_layout: None,
            context_hide: Vec::new(),
            context_reset: Vec::new(),
            system_prompt: None,
        }
    }

    /// A blueprint whose stage 0 is a fan-out stage and stage 1 is `merge`.
    fn fanout_blueprint(config: FanOutConfig) -> Blueprint {
        let layout = ContextLayout::new(
            vec![RegionDefinition::new(
                "conversation".to_string(),
                RegionKind::Clearable,
                10_000,
            )],
            12_000,
        );
        let mut s0 = Stage::new(
            "fan".to_string(),
            ModelConfig::new("script".to_string(), "m".to_string()),
        );
        s0.mode = StageMode::FanOut { config };
        let s1 = Stage::new(
            "merge".to_string(),
            ModelConfig::new("script".to_string(), "m".to_string()),
        );
        Blueprint::new("t".to_string(), "d".to_string(), vec![s0, s1], layout)
    }

    fn parent_state() -> AgentState {
        AgentState {
            agent_id: "parent".to_string(),
            current_stage: "fan".to_string(),
            iteration: 0,
            status: AgentStatus::Active,
            spawned_children_ids: vec![],
            pending_wait: None,
            accepts_messages: true,
        }
    }

    /// Spawn a parent sitting on `ProcessResponse` with `response` as its
    /// (split) inference output.
    fn spawn_parent(world: &mut World, bp: Blueprint, response: &str) -> Entity {
        world
            .spawn((
                AgentBlueprint(bp),
                StageCursor { index: 0 },
                parent_state(),
                StageProgress::default(),
                StageInferences(vec![stage_inf(), stage_inf()]),
                StageSetups(vec![setup(), setup()]),
                VisitCounts::default(),
                window(),
                InferenceResult {
                    response: response.to_string(),
                    tool_calls: vec![],
                    tokens_used: 0,
                    cut_off_at: None,
                    reasoning: None,
                    parts: Vec::new(),
                },
                ProcessResponse,
            ))
            .id()
    }

    fn install(world: &mut World, spawner: Arc<dyn FanOutSpawner>) {
        world.insert_resource(FanOutSpawnerRes(spawner));
    }

    fn status_of(world: &World, e: Entity) -> AgentStatus {
        world.get::<AgentState>(e).unwrap().status.clone()
    }

    /// Assert an agent is in an `Error` state (by discriminant, so no unmatched
    /// `matches!` arm is left uncovered).
    fn assert_errored(world: &World, e: Entity) {
        assert_eq!(
            std::mem::discriminant(&status_of(world, e)),
            std::mem::discriminant(&AgentStatus::Error {
                message: String::new()
            })
        );
    }

    fn conversation_text(world: &World, e: Entity) -> String {
        world
            .get::<ContextWindow>(e)
            .unwrap()
            .get_region("conversation")
            .unwrap()
            .content
            .iter()
            .map(|entry| entry.content.clone())
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Stand in for the dispatcher: take the work items the fixture put in the
    /// parent's pending response and park it, exactly as an accepted `fan_out`
    /// call does. Lets the collector's tests state their items as JSON, which is
    /// how a model states them.
    fn split(world: &mut World, e: Entity) {
        let items: Vec<WorkItem> =
            serde_json::from_str(&world.get::<InferenceResult>(e).unwrap().response)
                .expect("the fixture's response is a work-item array");
        let config = stage_config(world, e);
        world
            .entity_mut(e)
            .remove::<ProcessResponse>()
            .remove::<InferenceResult>();
        begin_fan_out(world, e, config, items, FanOutOrigin::Stage);
    }

    /// The fan-out config off the fixture's blueprint.
    ///
    /// Walked in reverse so the fixture's ordinary `merge` stage is visited
    /// first: the non-fan-out arm is then a branch the suite actually takes,
    /// rather than one that only exists to satisfy the match.
    fn stage_config(world: &World, e: Entity) -> FanOutConfig {
        world
            .get::<AgentBlueprint>(e)
            .unwrap()
            .0
            .stages
            .iter()
            .rev()
            .find_map(|stage| match &stage.mode {
                StageMode::FanOut { config } => Some(config.clone()),
                _ => None,
            })
            .expect("the fixture has a fan-out stage")
    }

    /// A work item with the given id and an empty context.
    fn item(id: &str) -> WorkItem {
        WorkItem {
            id: id.to_string(),
            context: serde_json::json!({}),
        }
    }

    fn complete_worker(world: &mut World, worker: Entity, content: &str) {
        set_status(world, worker, AgentStatus::Complete);
        world.entity_mut(worker).insert(InferenceResult {
            response: content.to_string(),
            tool_calls: vec![],
            tokens_used: 0,
            cut_off_at: None,
            reasoning: None,
            parts: Vec::new(),
        });
    }

    // ── frame_split_round ─────────────────────────────────────────────────────

    /// Run the framing system over a parent that has just entered its stage.
    fn run_framing(world: &mut World, e: Entity, visits: &[(&str, usize)]) -> String {
        let mut counts = VisitCounts::default();
        for (name, n) in visits {
            counts.0.insert((*name).to_string(), *n);
        }
        world
            .entity_mut(e)
            .insert(counts)
            .insert(crate::pipeline::StageJustEntered {
                index: 0,
                name: "fan".to_string(),
            });
        let mut schedule = Schedule::default();
        schedule.add_systems(frame_split_round);
        schedule.run(world);
        conversation_text(world, e)
    }

    /// A stage entered once has nothing to be told, and pays nothing for the
    /// mechanism: the ordinary single-fan-out run is untouched.
    #[test]
    fn a_first_split_round_is_not_framed() {
        let mut world = World::new();
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );

        let convo = run_framing(&mut world, e, &[("fan", 1)]);

        assert_eq!(convo, "", "nothing injected on a first entry");
    }

    /// The failure this exists for. On a re-entry the model is asked a different
    /// question from the one it already answered, told what came back, and told
    /// that an empty list is a real reply.
    #[test]
    fn a_repeat_split_round_is_framed_with_what_was_already_researched() {
        let mut world = World::new();
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );
        world.entity_mut(e).insert(PreviousWorkItems(vec![
            "glp1-mechanism".to_string(),
            "post-cessation-regain".to_string(),
        ]));

        let convo = run_framing(&mut world, e, &[("fan", 2)]);

        assert!(convo.contains("split round 2"), "{convo}");
        assert!(convo.contains("glp1-mechanism"), "{convo}");
        assert!(convo.contains("post-cessation-regain"), "{convo}");
        assert!(convo.contains("still unanswered"), "{convo}");
        assert!(
            convo.contains("empty `items` array"),
            "and that finishing is sayable: {convo}"
        );
    }

    /// The ids live on a component, and a daemon restart between the two entries
    /// drops it. The framing still fires, because "you have been here before" is
    /// the part that matters.
    #[test]
    fn a_repeat_round_is_framed_even_with_the_previous_ids_lost() {
        let mut world = World::new();
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );

        let convo = run_framing(&mut world, e, &[("fan", 3)]);

        assert!(convo.contains("split round 3"), "{convo}");
        assert!(convo.contains("already been handed out"), "{convo}");
    }

    /// A wide fan-out's ids are listed up to a bound, so the instruction after
    /// them is not buried under thirty slugs.
    #[test]
    fn a_wide_previous_round_lists_a_bounded_number_of_ids() {
        let mut world = World::new();
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );
        let ids: Vec<String> = (0..20).map(|i| format!("item-{i}")).collect();
        world.entity_mut(e).insert(PreviousWorkItems(ids));

        let convo = run_framing(&mut world, e, &[("fan", 2)]);

        assert!(convo.contains("item-0"), "{convo}");
        assert!(!convo.contains("item-19"), "{convo}");
        assert!(convo.contains("and 8 more"), "{convo}");
    }

    /// Only fan-out stages are framed; every other stage entry is untouched.
    #[test]
    fn a_stage_that_is_not_a_fan_out_is_not_framed() {
        let mut world = World::new();
        let mut bp = fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue));
        bp.stages[0].mode = StageMode::Autonomous;
        let e = spawn_parent(&mut world, bp, "");

        let convo = run_framing(&mut world, e, &[("fan", 2)]);

        assert_eq!(convo, "");
    }

    /// Starting a fan-out records what it handed out, which is what the next
    /// round is framed with.
    #[test]
    fn starting_a_fan_out_records_its_work_item_ids() {
        let mut world = World::new();
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );

        begin_fan_out(
            &mut world,
            e,
            cfg(None, 2, WorkerFailurePolicy::Continue),
            vec![item("a"), item("b")],
            FanOutOrigin::Stage,
        );

        assert_eq!(
            world.get::<PreviousWorkItems>(e),
            Some(&PreviousWorkItems(vec!["a".to_string(), "b".to_string()]))
        );
    }

    // ── parse_fan_out_call ────────────────────────────────────────────────────

    /// The arguments came through a schema the provider enforced, so the reader
    /// is strict: it takes the shape asked for and names anything else.
    #[test]
    fn parse_fan_out_call_reads_the_whole_shape() {
        let request = parse_fan_out_call(&serde_json::json!({
            "agent": "researcher",
            "max_workers": 4,
            "items": [
                {"id": "a", "context": {"question": "q1"}},
                {"id": "b", "context": {"question": "q2"}}
            ]
        }))
        .expect("parses");
        assert_eq!(request.agent.as_deref(), Some("researcher"));
        assert_eq!(request.max_workers, Some(4));
        assert_eq!(request.items.len(), 2);
        assert_eq!(request.items[1].context["question"], "q2");
    }

    /// An empty list is a real answer, not a malformed call: it means there is
    /// nothing to hand out.
    #[test]
    fn parse_fan_out_call_accepts_an_empty_list() {
        let request = parse_fan_out_call(&serde_json::json!({"items": []})).expect("parses");
        assert!(request.items.is_empty());
        assert_eq!(request.agent, None);
        assert_eq!(request.max_workers, None);
    }

    #[test]
    fn authoritative_items_are_strict_and_preserve_order() {
        let mut window = window();
        let mut items = Region::new("items".to_string(), RegionKind::Pinned, 10_000);
        items
            .add_entry(
                r#"[{"id":"b","context":{"n":2}},{"id":"a","context":null}]"#.to_string(),
                20,
            )
            .expect("fits");
        window.add_region(items);

        let parsed = authoritative_items(&window, "items", Some(2)).expect("strict array");
        assert_eq!(
            parsed
                .iter()
                .map(|item| item.id.as_str())
                .collect::<Vec<_>>(),
            vec!["b", "a"]
        );
        assert_eq!(parsed[0].context["n"], 2);
    }

    #[test]
    fn authoritative_items_reject_duplicates_and_overrides() {
        let mut window = window();
        let mut items = Region::new("items".to_string(), RegionKind::Pinned, 10_000);
        items
            .add_entry(
                r#"[{"id":"same","context":1},{"id":"same","context":2}]"#.to_string(),
                20,
            )
            .expect("fits");
        window.add_region(items);
        let error = authoritative_items(&window, "items", Some(2)).unwrap_err();
        assert!(error.contains("duplicate id"), "{error}");
    }

    #[test]
    fn authoritative_items_reject_unknown_fields_and_over_cap() {
        let mut window = window();
        let mut items = Region::new("items".to_string(), RegionKind::Pinned, 10_000);
        items
            .add_entry(r#"[{"id":"a","context":{},"extra":true}]"#.to_string(), 20)
            .expect("fits");
        window.add_region(items);
        let error = authoritative_items(&window, "items", Some(1)).unwrap_err();
        assert!(error.contains("exactly id and context"), "{error}");
    }

    #[test]
    fn authoritative_items_reject_surrounding_id_whitespace() {
        let mut window = window();
        let mut items = Region::new("items".to_string(), RegionKind::Pinned, 10_000);
        items
            .add_entry(r#"[{"id":" a ","context":{}}]"#.to_string(), 20)
            .expect("fits");
        window.add_region(items);
        let error = authoritative_items(&window, "items", Some(1)).unwrap_err();
        assert!(error.contains("surrounding whitespace"), "{error}");
    }

    /// A blank agent is the same as none: a fan-out stage names its worker in
    /// the blueprint, and an empty string would otherwise override it with
    /// nothing.
    #[test]
    fn parse_fan_out_call_ignores_a_blank_agent() {
        let request =
            parse_fan_out_call(&serde_json::json!({"agent": "  ", "items": []})).expect("parses");
        assert_eq!(request.agent, None);
    }

    /// Every rejection names what was wrong, because the model reads it and
    /// corrects on its next turn.
    #[test]
    fn parse_fan_out_call_names_what_was_wrong() {
        let cases = [
            (serde_json::json!("nope"), "must be an object"),
            (serde_json::json!({}), "requires an `items` array"),
            (serde_json::json!({"items": "all"}), "must be an array"),
            (
                serde_json::json!({"items": [{"id": 4}]}),
                "not {id, context}",
            ),
        ];
        for (args, expected) in cases {
            let err = parse_fan_out_call(&args).unwrap_err();
            assert!(err.contains(expected), "{args}: {err}");
        }
    }

    // ── config_for ────────────────────────────────────────────────────────────

    /// A call from an ordinary stage brings its own worker and takes engine
    /// defaults for everything else.
    #[test]
    fn config_for_a_bare_call_names_its_worker_and_defaults_the_rest() {
        let request = parse_fan_out_call(&serde_json::json!({
            "agent": "researcher", "items": []
        }))
        .unwrap();

        let config = config_for(&request, None);

        assert_eq!(config.worker_agent.as_deref(), Some("researcher"));
        assert_eq!(config.worker_stage, None);
        assert_eq!(
            config.max_workers,
            leviath_core::blueprint::DEFAULT_MAX_WORKERS
        );
        assert_eq!(config.on_worker_failure, WorkerFailurePolicy::Continue);
        assert_eq!(config.max_items, None);
    }

    /// Inside a fan-out stage the blueprint's keys are the starting point, so a
    /// stage that set `max_items` or a failure policy still gets them.
    #[test]
    fn config_for_a_stage_call_keeps_the_blueprints_keys() {
        let mut stage = cfg(Some("merge"), 3, WorkerFailurePolicy::FailAll);
        stage.max_items = Some(5);
        let request = parse_fan_out_call(&serde_json::json!({"items": []})).unwrap();

        let config = config_for(&request, Some(&stage));

        assert_eq!(config.merge_stage.as_deref(), Some("merge"));
        assert_eq!(config.max_items, Some(5));
        assert_eq!(config.on_worker_failure, WorkerFailurePolicy::FailAll);
        assert_eq!(config.max_workers, 3);
    }

    /// A worker named in the call wins, and clears the blueprint's own worker so
    /// the two cannot both be set.
    #[test]
    fn a_named_agent_overrides_the_stages_worker() {
        let stage = cfg(None, 2, WorkerFailurePolicy::Continue);
        assert!(
            stage.worker_stage.is_some(),
            "the fixture uses worker_stage"
        );
        let request =
            parse_fan_out_call(&serde_json::json!({"agent": "other", "items": []})).unwrap();

        let config = config_for(&request, Some(&stage));

        assert_eq!(config.worker_agent.as_deref(), Some("other"));
        assert_eq!(config.worker_stage, None);
        assert_eq!(config.worker_query, None);
    }

    /// A per-call cap overrides the stage's.
    #[test]
    fn a_per_call_cap_overrides_the_stages() {
        let stage = cfg(None, 2, WorkerFailurePolicy::Continue);
        let request =
            parse_fan_out_call(&serde_json::json!({"max_workers": 9, "items": []})).unwrap();

        assert_eq!(config_for(&request, Some(&stage)).max_workers, 9);
    }

    // ── begin_fan_out / start_pending_fan_outs ────────────────────────────────

    /// The one way in: the parent parks on its workers and records what it
    /// handed out.
    #[test]
    fn begin_fan_out_parks_the_parent_on_its_workers() {
        let mut world = World::new();
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );

        begin_fan_out(
            &mut world,
            e,
            cfg(None, 2, WorkerFailurePolicy::Continue),
            vec![item("a"), item("b")],
            FanOutOrigin::Stage,
        );

        assert_eq!(status_of(&world, e), AgentStatus::Waiting);
        assert_eq!(
            world.get::<FanOutWaiting>(e).expect("parked").pending.len(),
            2
        );
        assert!(world.get::<FannedOut>(e).is_some());
        assert_eq!(
            world.get::<PreviousWorkItems>(e),
            Some(&PreviousWorkItems(vec!["a".to_string(), "b".to_string()]))
        );
    }

    #[test]
    fn authoritative_fanout_starts_without_provider_inference() {
        let mut world = World::new();
        let mut config = cfg(None, 2, WorkerFailurePolicy::Continue);
        config.items_region = Some("items".to_string());
        let mut blueprint = fanout_blueprint(config);
        blueprint.stages[0]
            .available_tools
            .push(leviath_core::blueprint::FAN_OUT_TOOL.to_string());
        let entity = spawn_parent(&mut world, blueprint, "");
        world
            .get_mut::<ContextWindow>(entity)
            .expect("parent window")
            .add_region(Region::new("items".to_string(), RegionKind::Pinned, 10_000));
        world
            .get_mut::<ContextWindow>(entity)
            .expect("parent window")
            .get_region_mut("items")
            .expect("items region")
            .add_entry(r#"[{"id":"a","context":{"task":"read"}}]"#.to_string(), 20)
            .expect("items fit");
        world
            .entity_mut(entity)
            .remove::<ProcessResponse>()
            .insert((
                AuthoritativeFanOutPending,
                crate::pipeline::StageInference {
                    provider_name: "script".to_string(),
                    model: "m".to_string(),
                    tools: vec![leviath_providers::Tool {
                        name: leviath_core::blueprint::FAN_OUT_TOOL.to_string(),
                        description: String::new(),
                        parameters: serde_json::json!({}),
                    }],
                    tool_filter: None,
                    fallbacks: Vec::new(),
                    output: None,
                },
            ));

        start_authoritative_fanouts(&mut world);

        assert!(world.get::<ReadyToInfer>(entity).is_none());
        assert!(world.get::<AuthoritativeFanOutPending>(entity).is_none());
        assert!(world.get::<FanOutWaiting>(entity).is_none());
        assert!(matches!(status_of(&world, entity), AgentStatus::Active));
        assert!(world.get::<ProcessResponse>(entity).is_none());
        assert!(
            world
                .get::<crate::pipeline::ReadyForTools>(entity)
                .is_some()
        );
        let result = world
            .get::<InferenceResult>(entity)
            .expect("synthetic tool result");
        assert_eq!(result.response, "");
        assert_eq!(result.tool_calls.len(), 1);
        assert_eq!(
            result.tool_calls[0].name,
            leviath_core::blueprint::FAN_OUT_TOOL
        );
        assert_eq!(
            result.tool_calls[0].arguments,
            serde_json::json!({"items": []})
        );

        // The synthetic call still has to pass the ordinary taint/consent
        // gate. A denied call must not park the parent or launch workers.
        world
            .get_mut::<ContextWindow>(entity)
            .expect("parent window")
            .enable_taint_tracking();
        world
            .get_mut::<ContextWindow>(entity)
            .expect("parent window")
            .typed_write(
                crate::components::WriteOrigin::System,
                "conversation",
                leviath_core::EntryKind::UserMessage,
                "secret".to_string(),
                5,
                Some(leviath_core::TaintLevel::Internal),
            )
            .expect("tainted context write");
        let (jobs, _jobs_rx) = tokio::sync::mpsc::unbounded_channel();
        world.insert_resource(crate::pipeline::ToolServiceRes(Arc::new(NoopToolService)));
        world.insert_resource(crate::pipeline::ToolStage::detached(jobs));
        world
            .entity_mut(entity)
            .insert(crate::taint::TaintGate::new(leviath_core::SecurityConfig {
                taint_tracking: true,
            }));
        let mut schedule = Schedule::default();
        schedule.add_systems(crate::pipeline::dispatch_tools);
        schedule.run(&mut world);
        assert!(world.get::<FanOutWaiting>(entity).is_none());
        assert!(world.get::<crate::pipeline::ReadyToInfer>(entity).is_some());
        let expected_call_id = format!("authoritative-fan-out-{}", entity.to_bits());
        assert!(
            world
                .get::<crate::pipeline::ContextToolResults>(entity)
                .is_some_and(|results| results.0.iter().any(|(id, text)| {
                    id == &expected_call_id && text.starts_with("[blocked]")
                }))
        );
    }

    /// `max_items` is a ceiling on the work, not just on concurrency.
    #[test]
    fn begin_fan_out_keeps_only_the_first_max_items() {
        let mut world = World::new();
        let mut config = cfg(None, 2, WorkerFailurePolicy::Continue);
        config.max_items = Some(3);
        let e = spawn_parent(&mut world, fanout_blueprint(config.clone()), "");

        let items: Vec<WorkItem> = (0..10).map(|i| item(&format!("w{i}"))).collect();
        begin_fan_out(&mut world, e, config, items, FanOutOrigin::Stage);

        let w = world.get::<FanOutWaiting>(e).expect("parked");
        let kept: Vec<&str> = w.pending.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(kept, ["w0", "w1", "w2"]);
    }

    /// An empty fan-out is not a special case: it parks with nothing pending and
    /// the collector finishes it on the next tick.
    #[test]
    fn an_empty_fan_out_finishes_through_the_ordinary_path() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            "",
        );

        begin_fan_out(
            &mut world,
            e,
            cfg(Some("merge"), 2, WorkerFailurePolicy::Continue),
            Vec::new(),
            FanOutOrigin::Stage,
        );
        fan_out_collect(&mut world);

        assert_eq!(status_of(&world, e), AgentStatus::Active);
        assert_eq!(
            world.get::<StageCursor>(e).map(|c| c.index),
            Some(1),
            "straight through to the merge stage"
        );
    }

    /// The dispatcher hands the call over as a component; this is the tick that
    /// turns it into running workers.
    #[test]
    fn start_pending_fan_outs_starts_what_the_dispatcher_accepted() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        // An ordinary stage, so this is the tool door: a fan-out stage's own
        // call is a stage fan-out and is covered separately.
        let mut bp = fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue));
        bp.stages[0].mode = StageMode::Autonomous;
        let e = spawn_parent(&mut world, bp, "");
        world.entity_mut(e).insert(PendingFanOut {
            call_id: "call-1".to_string(),
            request: parse_fan_out_call(&serde_json::json!({
                "agent": "researcher",
                "items": [{"id": "a", "context": {}}]
            }))
            .unwrap(),
        });

        start_pending_fan_outs(&mut world);

        assert!(world.get::<PendingFanOut>(e).is_none(), "consumed");
        let w = world.get::<FanOutWaiting>(e).expect("parked");
        assert_eq!(w.pending.len(), 1);
        assert_eq!(
            w.origin,
            FanOutOrigin::Tool {
                call_id: "call-1".to_string()
            }
        );
        assert_eq!(
            w.config.worker_agent.as_deref(),
            Some("researcher"),
            "the call named its worker"
        );
    }

    /// A `mode = "fan_out"` stage's call comes back through the STAGE door, not
    /// the tool one: its report goes to `results_region` and it moves on to
    /// `merge_stage`.
    ///
    /// The call itself is identical either way, so only the stage can say which
    /// this is - and getting it wrong made `results_region` and `merge_stage`
    /// dead config. A live `deep-researcher` fan-out delivered three workers'
    /// findings into `conversation` as a tool result and resumed the split stage
    /// instead of writing `sub_findings` and moving to `analyze`. Every unit test
    /// passed, because they all built the origin by hand and never came through
    /// `start_pending_fan_outs`.
    #[test]
    fn a_fan_out_stages_call_is_delivered_as_a_stage_not_a_tool_result() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let mut config = cfg(Some("merge"), 2, WorkerFailurePolicy::Continue);
        config.results_region = Some("sub_findings".to_string());
        let e = spawn_parent(&mut world, fanout_blueprint(config), "");
        world
            .get_mut::<ContextWindow>(e)
            .unwrap()
            .add_region(Region::new(
                "sub_findings".to_string(),
                RegionKind::Pinned,
                4_000,
            ));
        world.entity_mut(e).insert(PendingFanOut {
            call_id: "call-1".to_string(),
            request: parse_fan_out_call(&serde_json::json!({
                "items": [{"id": "a", "context": {}}]
            }))
            .unwrap(),
        });

        start_pending_fan_outs(&mut world);
        assert_eq!(
            world.get::<FanOutWaiting>(e).expect("parked").origin,
            FanOutOrigin::Stage,
            "a fan-out stage's own call is a stage fan-out"
        );

        fan_out_collect(&mut world);
        let worker = world.get::<SubAgentChildren>(e).expect("linked").children[0];
        complete_worker(&mut world, worker, "what the worker found");
        fan_out_collect(&mut world);

        let findings = world
            .get::<ContextWindow>(e)
            .unwrap()
            .get_region("sub_findings")
            .expect("the results region")
            .content
            .iter()
            .map(|entry| entry.content.clone())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(
            findings.contains("what the worker found"),
            "the report goes to results_region: {findings}"
        );
        assert_eq!(
            world.get::<StageCursor>(e).map(|c| c.index),
            Some(1),
            "and the stage moves on to its merge stage"
        );
    }

    /// A tool call made inside a fan-out stage still picks up that stage's own
    /// keys, so `mode = \"fan_out\"` really is sugar over the same engine.
    #[test]
    fn a_call_inside_a_fan_out_stage_inherits_the_stages_keys() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let mut config = cfg(Some("merge"), 7, WorkerFailurePolicy::Continue);
        config.max_items = Some(2);
        let e = spawn_parent(&mut world, fanout_blueprint(config), "");
        world.entity_mut(e).insert(PendingFanOut {
            call_id: "call-1".to_string(),
            request: parse_fan_out_call(&serde_json::json!({
                "items": [{"id": "a", "context": {}}]
            }))
            .unwrap(),
        });

        start_pending_fan_outs(&mut world);

        let w = world.get::<FanOutWaiting>(e).expect("parked");
        assert_eq!(w.config.merge_stage.as_deref(), Some("merge"));
        assert_eq!(w.config.max_items, Some(2));
        assert_eq!(w.max_workers, 7);
    }

    /// The tool is recognised by name and nothing else is.
    #[test]
    fn the_fan_out_tool_is_recognised_by_name() {
        assert!(is_fan_out_tool("fan_out"));
        assert!(!is_fan_out_tool("spawn_agent"));
    }

    /// The headline case: an ordinary stage grants the tool and fans out in the
    /// middle of its own work. There is no stage config to inherit, so the call
    /// brings everything.
    #[test]
    fn an_ordinary_stage_can_fan_out_mid_work() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let mut bp = fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue));
        bp.stages[0].mode = StageMode::Autonomous;
        let e = spawn_parent(&mut world, bp, "");
        world.entity_mut(e).insert(PendingFanOut {
            call_id: "call-1".to_string(),
            request: parse_fan_out_call(&serde_json::json!({
                "agent": "researcher",
                "max_workers": 3,
                "items": [{"id": "a", "context": {}}]
            }))
            .unwrap(),
        });

        start_pending_fan_outs(&mut world);

        let w = world.get::<FanOutWaiting>(e).expect("parked");
        assert_eq!(w.config.worker_agent.as_deref(), Some("researcher"));
        assert_eq!(w.max_workers, 3);
        assert_eq!(
            w.config.merge_stage, None,
            "an ordinary stage has no merge stage to fall into"
        );
    }

    /// A stage that names a `results_region` gets its report there, not in the
    /// conversation. This is the region the merge stage is told to read, so a
    /// report that misses it is a fan-out whose findings nobody sees.
    #[test]
    fn a_stage_fan_out_writes_to_its_results_region() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let mut config = cfg(Some("merge"), 2, WorkerFailurePolicy::Continue);
        config.results_region = Some("sub_findings".to_string());
        let e = spawn_parent(&mut world, fanout_blueprint(config.clone()), "");
        world
            .get_mut::<ContextWindow>(e)
            .unwrap()
            .add_region(Region::new(
                "sub_findings".to_string(),
                RegionKind::Pinned,
                4_000,
            ));
        begin_fan_out(&mut world, e, config, vec![item("a")], FanOutOrigin::Stage);

        fan_out_collect(&mut world);
        let worker = world.get::<SubAgentChildren>(e).expect("linked").children[0];
        complete_worker(&mut world, worker, "what the worker found");
        fan_out_collect(&mut world);

        let region = world
            .get::<ContextWindow>(e)
            .unwrap()
            .get_region("sub_findings")
            .expect("the results region")
            .content
            .iter()
            .map(|entry| entry.content.clone())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(region.contains("what the worker found"), "{region}");
    }

    /// A fan-out whose stage has already spent its `max_iterations` must still
    /// deliver. The nudges that get a reluctant model to call the tool are
    /// inferences, so they spend the stage's budget: `deep-researcher` allows
    /// `investigate` four, a live run spent three answering in prose and the
    /// fourth calling `fan_out`, and three workers then researched for thirteen
    /// minutes. Discarding that because the split took four tries is the same
    /// failure - finished work thrown away - that this whole path exists to stop.
    #[test]
    fn a_fan_out_still_merges_when_its_stage_is_out_of_iterations() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let mut bp = fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue));
        bp.stages[0].max_iterations = Some(4);
        let e = spawn_parent(&mut world, bp, "");
        // The stage is at its cap, exactly as it is when the fan-out starts on
        // the last iteration the stage had.
        world.entity_mut(e).insert(StageProgress {
            iterations: 4,
            ..Default::default()
        });
        begin_fan_out(
            &mut world,
            e,
            cfg(Some("merge"), 2, WorkerFailurePolicy::Continue),
            vec![item("a")],
            FanOutOrigin::Stage,
        );

        fan_out_collect(&mut world); // starts the worker
        let worker = world.get::<SubAgentChildren>(e).expect("linked").children[0];
        complete_worker(&mut world, worker, "what the worker found");
        fan_out_collect(&mut world); // reaps and merges

        assert_eq!(
            world.get::<StageCursor>(e).map(|c| c.index),
            Some(1),
            "the merge stage is entered, not skipped"
        );
        let convo = conversation_text(&world, e);
        assert!(
            convo.contains("what the worker found"),
            "and the findings survive: {convo}"
        );
    }

    // ── the tool origin's delivery ────────────────────────────────────────────

    /// A fan-out started by a tool call comes back as that call's result and the
    /// agent picks its stage up where it left off - it does not transition.
    #[test]
    fn a_tool_fan_out_returns_its_report_as_the_calls_result() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            "",
        );
        begin_fan_out(
            &mut world,
            e,
            cfg(Some("merge"), 2, WorkerFailurePolicy::Continue),
            vec![item("a")],
            FanOutOrigin::Tool {
                call_id: "call-1".to_string(),
            },
        );

        fan_out_collect(&mut world); // starts the worker
        let worker = world.get::<SubAgentChildren>(e).expect("linked").children[0];
        complete_worker(&mut world, worker, "what the worker found");
        fan_out_collect(&mut world); // reaps and delivers

        assert_eq!(status_of(&world, e), AgentStatus::Active);
        assert!(
            world.get::<crate::pipeline::ReadyToInfer>(e).is_some(),
            "back in front of the model, not transitioning"
        );
        assert_eq!(
            world.get::<StageCursor>(e).map(|c| c.index),
            Some(0),
            "still in the stage that called it"
        );
        let convo = conversation_text(&world, e);
        assert!(convo.contains("what the worker found"), "{convo}");
    }

    /// No context window means nowhere to put the report, and the agent is still
    /// handed back to the model rather than left parked. The same shape
    /// `inject_results` takes for the stage origin.
    #[test]
    fn a_tool_fan_out_without_a_window_still_resumes_the_agent() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = world
            .spawn((
                AgentBlueprint(fanout_blueprint(cfg(
                    None,
                    2,
                    WorkerFailurePolicy::Continue,
                ))),
                StageCursor { index: 0 },
                parent_state(),
                StageProgress::default(),
                StageInferences(vec![stage_inf(), stage_inf()]),
                StageSetups(vec![setup(), setup()]),
                VisitCounts::default(),
            ))
            .id();
        begin_fan_out(
            &mut world,
            e,
            cfg(None, 2, WorkerFailurePolicy::Continue),
            Vec::new(),
            FanOutOrigin::Tool {
                call_id: "call-1".to_string(),
            },
        );

        fan_out_collect(&mut world);

        assert_eq!(status_of(&world, e), AgentStatus::Active);
        assert!(world.get::<crate::pipeline::ReadyToInfer>(e).is_some());
    }

    /// Routing that says nothing about `fan_out` sends the report to whatever
    /// the stage's default region is, like any other unlisted tool.
    #[test]
    fn a_tool_fan_out_falls_back_to_the_stages_default_region() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );
        world
            .get_mut::<ContextWindow>(e)
            .unwrap()
            .add_region(Region::new(
                "notes".to_string(),
                RegionKind::Clearable,
                4_000,
            ));
        world
            .entity_mut(e)
            .insert(crate::components::ToolResultRoutingComponent {
                routing: leviath_core::ToolResultRouting {
                    default_region: "notes".to_string(),
                    tool_overrides: std::collections::HashMap::from([(
                        "read_file".to_string(),
                        "sources".to_string(),
                    )]),
                    max_result_tokens: None,
                    tool_max_result_tokens: std::collections::HashMap::new(),
                    persist: true,
                },
            })
            // A declared sensitivity travels with the result, as it does for
            // every other tool.
            .insert(crate::pipeline::ToolSensitivities(
                std::collections::HashMap::from([(
                    "fan_out".to_string(),
                    leviath_core::TaintLevel::Public,
                )]),
            ));
        begin_fan_out(
            &mut world,
            e,
            cfg(None, 2, WorkerFailurePolicy::Continue),
            vec![item("a")],
            FanOutOrigin::Tool {
                call_id: "call-1".to_string(),
            },
        );

        fan_out_collect(&mut world);
        let worker = world.get::<SubAgentChildren>(e).expect("linked").children[0];
        complete_worker(&mut world, worker, "default-routed finding");
        fan_out_collect(&mut world);

        let notes = world
            .get::<ContextWindow>(e)
            .unwrap()
            .get_region("notes")
            .expect("the default region")
            .content
            .iter()
            .map(|entry| entry.content.clone())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(notes.contains("default-routed finding"), "{notes}");
    }

    /// The report is routed like any other tool result, so a blueprint that
    /// sends `fan_out` somewhere of its own gets it there.
    #[test]
    fn a_tool_fan_outs_report_follows_the_stages_routing() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            "",
        );
        // The region the routing points at has to exist for the result to land
        // in it; a routing rule to a region the stage does not carry is a
        // blueprint `lev validate` refuses.
        world
            .get_mut::<ContextWindow>(e)
            .unwrap()
            .add_region(Region::new(
                "findings".to_string(),
                RegionKind::Clearable,
                4_000,
            ));
        world
            .entity_mut(e)
            .insert(crate::components::ToolResultRoutingComponent {
                routing: leviath_core::ToolResultRouting {
                    default_region: "conversation".to_string(),
                    // A second rule that does not match, so the lookup has
                    // something to reject as well as something to find.
                    tool_overrides: std::collections::HashMap::from([
                        ("fan_out".to_string(), "findings".to_string()),
                        ("read_file".to_string(), "sources".to_string()),
                    ]),
                    max_result_tokens: None,
                    tool_max_result_tokens: std::collections::HashMap::new(),
                    persist: true,
                },
            });
        begin_fan_out(
            &mut world,
            e,
            cfg(None, 2, WorkerFailurePolicy::Continue),
            vec![item("a")],
            FanOutOrigin::Tool {
                call_id: "call-1".to_string(),
            },
        );

        fan_out_collect(&mut world);
        let worker = world.get::<SubAgentChildren>(e).expect("linked").children[0];
        complete_worker(&mut world, worker, "routed finding");
        fan_out_collect(&mut world);

        let findings = world
            .get::<ContextWindow>(e)
            .unwrap()
            .get_region("findings")
            .expect("the routed region")
            .content
            .iter()
            .map(|entry| entry.content.clone())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(findings.contains("routed finding"), "{findings}");
    }

    // ── fan_out_collect: worker lifecycle + merge ─────────────────────────────

    /// A paused fan-out does not start the next queued worker.
    ///
    /// Without the latch, pausing a parent pauses the children that are running
    /// and the collector immediately launches their replacements out of
    /// `pending` - so the run keeps spending money and the pause achieves
    /// nothing visible.
    #[test]
    fn a_paused_fan_out_starts_no_further_workers() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        // Cap of one against three items, so there is always something queued.
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 1, WorkerFailurePolicy::Continue)),
            "",
        );
        begin_fan_out(
            &mut world,
            e,
            cfg(Some("merge"), 1, WorkerFailurePolicy::Continue),
            vec![item("a"), item("b"), item("c")],
            FanOutOrigin::Stage,
        );
        fan_out_collect(&mut world);
        assert_eq!(
            world.get::<SubAgentChildren>(e).unwrap().children.len(),
            1,
            "one worker runs under a cap of one"
        );

        world
            .get_mut::<FanOutWaiting>(e)
            .expect("parked")
            .set_paused(true);
        // Finish the running worker: its slot frees, which is exactly when the
        // collector would otherwise reach into the queue.
        let running = world.get::<SubAgentChildren>(e).unwrap().children[0];
        complete_worker(&mut world, running, "done");
        fan_out_collect(&mut world);

        assert_eq!(
            world.get::<SubAgentChildren>(e).unwrap().children.len(),
            1,
            "the freed slot stays empty while the fan-out is paused"
        );
        let w = world.get::<FanOutWaiting>(e).expect("still parked");
        assert_eq!(w.pending.len(), 2, "the queue is held, not consumed");
        assert_eq!(
            w.summaries.len(),
            1,
            "the worker that finished before the pause is still reaped"
        );

        // Releasing the latch lets the queue move again.
        world
            .get_mut::<FanOutWaiting>(e)
            .expect("parked")
            .set_paused(false);
        fan_out_collect(&mut world);
        assert_eq!(
            world.get::<SubAgentChildren>(e).unwrap().children.len(),
            2,
            "resuming starts the next queued worker"
        );
    }

    /// The latch survives a daemon restart: it rides the persisted fan-out state
    /// like everything else the parent is parked on.
    #[test]
    fn the_fan_out_pause_round_trips_through_its_persisted_state() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 1, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"},{"id":"b"}]"#,
        );
        split(&mut world, e);
        world
            .get_mut::<FanOutWaiting>(e)
            .expect("parked")
            .set_paused(true);

        let state = world.get::<FanOutWaiting>(e).expect("parked").to_state();
        assert!(state.paused, "the latch is written out");

        restore_fan_out_waiting(&mut world, e, state, &|_| None);
        assert!(
            world.get::<FanOutWaiting>(e).expect("parked").is_paused(),
            "and comes back paused, so a restart does not quietly resume the fan-out"
        );
    }

    #[test]
    fn collect_starts_workers_then_merges_on_completion() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"},{"id":"b"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        // Two workers started and tracked.
        let kids = world.get::<SubAgentChildren>(e).unwrap().children.clone();
        assert_eq!(kids.len(), 2);
        assert!(world.get::<FanOutWaiting>(e).is_some());
        // Each worker got a ParentRef at depth 1.
        for k in &kids {
            assert_eq!(world.get::<ParentRef>(*k).unwrap().depth, 1);
        }

        // Complete both workers, then collect merges to the merge stage.
        for k in &kids {
            complete_worker(&mut world, *k, "fixed it");
        }
        fan_out_collect(&mut world);
        assert!(world.get::<FanOutWaiting>(e).is_none());
        assert_eq!(status_of(&world, e), AgentStatus::Active);
        assert_eq!(world.get::<StageCursor>(e).unwrap().index, 1);
        assert!(world.get::<ReadyToInfer>(e).is_some());
        // The consolidated report landed in the parent's conversation.
        assert!(
            world
                .get::<ContextWindow>(e)
                .unwrap()
                .get_region("conversation")
                .unwrap()
                .current_tokens
                > 0
        );
    }

    /// Run the slim system once over `world`.
    fn run_slim(world: &mut World) {
        let mut schedule = bevy_ecs::schedule::Schedule::default();
        schedule.add_systems(slim_merged_workers);
        schedule.run(world);
    }

    /// A merged worker keeps its heavy components until its terminal snapshot
    /// has been dispatched, then sheds them, so a finished worker does not hold
    /// a full context window resident until the parent goes terminal.
    #[test]
    fn merged_workers_are_slimmed_once_their_terminal_state_is_persisted() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        let worker = world.get::<SubAgentChildren>(e).unwrap().children[0];
        // Give the worker a context window so there is something to shed.
        world
            .entity_mut(worker)
            .insert((window(), crate::pipeline::PersistWatermark::default()));
        complete_worker(&mut world, worker, "done");
        fan_out_collect(&mut world);

        // Consumed by the merge and marked - but its terminal snapshot has not
        // been dispatched, so it keeps its state.
        assert!(world.get::<MergedWorker>(worker).is_some());
        run_slim(&mut world);
        assert!(
            world.get::<ContextWindow>(worker).is_some(),
            "unpersisted terminal state stays resident"
        );

        // Stamp the watermark terminal, and the worker sheds its heavy parts.
        let mut wm = crate::pipeline::PersistWatermark::default();
        wm.stamp_status(leviath_core::run_meta::RunStatus::Complete);
        world.entity_mut(worker).insert(wm);
        run_slim(&mut world);
        assert!(world.get::<ContextWindow>(worker).is_none());
        assert!(world.get::<MergedWorker>(worker).is_none());
        // The entity itself survives for the host's bookkeeping.
        assert!(world.get::<AgentState>(worker).is_some());
    }

    #[test]
    fn collect_respects_max_workers_and_stages_pending() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 1, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"},{"id":"b"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        // Only one worker at a time.
        assert_eq!(world.get::<SubAgentChildren>(e).unwrap().children.len(), 1);
        let first = world.get::<SubAgentChildren>(e).unwrap().children[0];
        // A collect pass while the worker is still running keeps it active and
        // starts nothing new (worker still counts against max_workers).
        fan_out_collect(&mut world);
        assert_eq!(world.get::<SubAgentChildren>(e).unwrap().children.len(), 1);
        assert!(world.get::<FanOutWaiting>(e).is_some());
        complete_worker(&mut world, first, "one");
        fan_out_collect(&mut world);
        // Second worker started after the first finished.
        assert_eq!(world.get::<SubAgentChildren>(e).unwrap().children.len(), 2);
        let second = world.get::<SubAgentChildren>(e).unwrap().children[1];
        complete_worker(&mut world, second, "two");
        fan_out_collect(&mut world);
        assert!(world.get::<FanOutWaiting>(e).is_none());
        assert_eq!(world.get::<StageCursor>(e).unwrap().index, 1);
    }

    /// `max_workers = 0` is unlimited: every item starts within the same wake
    /// (at most `MAX_WORKER_STARTS_PER_PASS` per pass, so the tick is never
    /// held for the whole queue), and the persisted state carries the cap as
    /// the largest number there is, which round-trips through JSON like any
    /// other.
    #[test]
    fn collect_with_max_workers_zero_starts_every_item_at_once() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 0, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"},{"id":"b"},{"id":"c"},{"id":"d"},{"id":"e"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        assert_eq!(
            world.get::<SubAgentChildren>(e).unwrap().children.len(),
            MAX_WORKER_STARTS_PER_PASS,
            "one pass starts a bounded number and hands the tick back"
        );
        assert_eq!(world.get::<FanOutWaiting>(e).unwrap().pending.len(), 1);
        fan_out_collect(&mut world);
        assert_eq!(world.get::<SubAgentChildren>(e).unwrap().children.len(), 5);
        let state = world.get::<FanOutWaiting>(e).unwrap().to_state();
        assert_eq!(state.max_workers, usize::MAX);
        assert!(state.pending.is_empty());
        let json = serde_json::to_string(&state).unwrap();
        let back: FanOutState = serde_json::from_str(&json).unwrap();
        assert_eq!(back.max_workers, usize::MAX);
    }

    #[test]
    fn fan_out_state_roundtrips_and_unresolved_workers_become_failures() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"},{"id":"b"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world); // starts both workers → active

        // Projecting to the serializable state captures each worker's run-id.
        let state = world.get::<FanOutWaiting>(e).unwrap().to_state();
        assert_eq!(state.active.len(), 2);
        assert!(state.active.iter().all(|(_id, run_id)| !run_id.is_empty()));

        // Restore onto a fresh parent, resolving run-ids back to entities.
        let by_run: std::collections::HashMap<String, Entity> = world
            .get::<SubAgentChildren>(e)
            .unwrap()
            .children
            .iter()
            .filter_map(|&c| {
                world
                    .get::<crate::persistence::RunMetadata>(c)
                    .map(|m| (m.run_id.clone(), c))
            })
            .collect();
        let fresh = world.spawn_empty().id();
        restore_fan_out_waiting(&mut world, fresh, state.clone(), &|rid| {
            by_run.get(rid).copied()
        });
        assert_eq!(
            world
                .get::<FanOutWaiting>(fresh)
                .unwrap()
                .to_state()
                .active
                .len(),
            2
        );

        // A resolver that can't map the workers → they become failures, so the
        // merge still completes rather than waiting forever.
        let orphaned = world.spawn_empty().id();
        restore_fan_out_waiting(&mut world, orphaned, state, &|_| None);
        let s = world.get::<FanOutWaiting>(orphaned).unwrap().to_state();
        assert!(s.active.is_empty());
        assert_eq!(s.failures.len(), 2);
    }

    #[test]
    fn collect_fail_all_marks_parent_error() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::FailAll)),
            r#"[{"id":"a"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        let worker = world.get::<SubAgentChildren>(e).unwrap().children[0];
        set_status(
            &mut world,
            worker,
            AgentStatus::Error {
                message: "boom".to_string(),
            },
        );
        fan_out_collect(&mut world);
        assert_errored(&world, e);
        assert_eq!(world.get::<StageCursor>(e).unwrap().index, 0); // no merge
    }

    #[test]
    fn collect_continue_reports_failures_and_proceeds_without_merge() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        // No merge stage ⇒ ResolveTransition (proceed) rather than force_transition.
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"},{"id":"b"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        let kids = world.get::<SubAgentChildren>(e).unwrap().children.clone();
        set_status(
            &mut world,
            kids[0],
            AgentStatus::Error {
                message: "worker a died".to_string(),
            },
        );
        complete_worker(&mut world, kids[1], "b ok");
        fan_out_collect(&mut world);
        assert!(world.get::<FanOutWaiting>(e).is_none());
        assert!(world.get::<crate::pipeline::ResolveTransition>(e).is_some());
        assert_eq!(world.get::<StageCursor>(e).unwrap().index, 0);
    }

    #[test]
    fn collect_finishes_immediately_when_there_are_no_work_items() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            "[]",
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        // No workers; straight to merge.
        assert!(world.get::<SubAgentChildren>(e).is_none());
        assert!(world.get::<FanOutWaiting>(e).is_none());
        assert_eq!(world.get::<StageCursor>(e).unwrap().index, 1);
    }

    #[test]
    fn collect_merge_stage_not_found_falls_through_to_transition() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("ghost"), 2, WorkerFailurePolicy::Continue)),
            "[]",
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        // Unknown merge stage ⇒ ResolveTransition, no stage jump.
        assert!(world.get::<crate::pipeline::ResolveTransition>(e).is_some());
        assert_eq!(world.get::<StageCursor>(e).unwrap().index, 0);
    }

    #[test]
    fn collect_abandons_a_cancelled_parent() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"}]"#,
        );
        split(&mut world, e);
        set_status(&mut world, e, AgentStatus::Cancelled);
        fan_out_collect(&mut world);
        assert!(world.get::<FanOutWaiting>(e).is_none());
        assert_eq!(status_of(&world, e), AgentStatus::Cancelled);
    }

    #[test]
    fn collect_without_a_spawner_records_failures() {
        // No FanOutSpawnerRes installed ⇒ every item fails to start.
        let mut world = World::new();
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        // Item failed to start, Continue policy ⇒ still transitions to merge.
        assert!(world.get::<FanOutWaiting>(e).is_none());
        assert_eq!(world.get::<StageCursor>(e).unwrap().index, 1);
    }

    #[test]
    fn collect_spawner_error_becomes_a_failure() {
        let mut world = World::new();
        install(&mut world, TestSpawner::refusing(&["a"]));
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::FailAll)),
            r#"[{"id":"a"}]"#,
        );
        split(&mut world, e);
        fan_out_collect(&mut world);
        // Spawn refused + FailAll ⇒ parent errors.
        assert_errored(&world, e);
    }

    // ── start_worker: depth cap + existing SubAgentChildren ───────────────────

    #[test]
    fn start_worker_enforces_depth_cap() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let mut bp = fanout_blueprint(cfg(None, 2, WorkerFailurePolicy::Continue));
        bp.max_child_depth = Some(3);
        let e = spawn_parent(&mut world, bp, r#"[{"id":"deep"}]"#);
        // Parent is itself a depth-3 sub-agent ⇒ child would be depth 4 > 3.
        world.entity_mut(e).insert(ParentRef {
            parent_entity: Entity::from_raw_u32(999)
                .expect("a small literal index is always a valid entity id"),
            parent_agent_id: "root".to_string(),
            depth: 3,
        });
        split(&mut world, e);
        fan_out_collect(&mut world);
        // No worker spawned (depth cap hit before any container is created).
        assert!(world.get::<SubAgentChildren>(e).is_none());
        assert!(world.get::<FanOutWaiting>(e).is_none());
    }

    #[test]
    fn start_worker_uses_existing_subagentchildren_cap_and_appends() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let e = spawn_parent(
            &mut world,
            fanout_blueprint(cfg(Some("merge"), 2, WorkerFailurePolicy::Continue)),
            r#"[{"id":"a"}]"#,
        );
        // Pre-existing children container with a generous cap.
        world.entity_mut(e).insert(SubAgentChildren {
            children: vec![
                Entity::from_raw_u32(1000)
                    .expect("a small literal index is always a valid entity id"),
            ],
            max_child_depth: 9,
        });
        split(&mut world, e);
        fan_out_collect(&mut world);
        let kids = world.get::<SubAgentChildren>(e).unwrap();
        assert_eq!(kids.max_child_depth, 9);
        assert_eq!(kids.children.len(), 2); // appended to the existing one
    }

    // ── worker_terminal_result / build_report / inject_conversation ───────────

    /// A submitted output beats the last assistant message. A worker whose
    /// final turn is a tool call has no trailing text, so the fallback alone
    /// hands the merge stage an empty string; `submit_output` is the channel a
    /// worker told to report what it did writes into.
    #[test]
    fn a_submitted_answer_beats_the_last_assistant_text() {
        let mut world = World::new();
        let worker = world
            .spawn((
                parent_state(),
                InferenceResult {
                    // What the last-turn fallback alone would hand the merge
                    // stage: the trailing aside, not the deliverable.
                    response: "Let me run the tests one more time.".to_string(),
                    tool_calls: vec![],
                    tokens_used: 0,
                    cut_off_at: None,
                    reasoning: None,
                    parts: Vec::new(),
                },
                crate::persistence::FinalOutput(leviath_core::output::FinalOutput::new(
                    "changed src/lib.rs; the failing test now passes",
                    None,
                    "fix_worker".to_string(),
                    0,
                )),
            ))
            .id();
        set_status(&mut world, worker, AgentStatus::Complete);
        assert_eq!(
            worker_terminal_result(&world, worker),
            Some(Ok(
                "changed src/lib.rs; the failing test now passes".to_string()
            ))
        );
    }

    /// The fallback stays, so a blueprint that happens to end on a text turn
    /// keeps working without declaring anything.
    #[test]
    fn a_worker_that_submitted_nothing_still_falls_back_to_its_text() {
        let mut world = World::new();
        let worker = world
            .spawn((
                parent_state(),
                InferenceResult {
                    response: "the old behaviour".to_string(),
                    tool_calls: vec![],
                    tokens_used: 0,
                    cut_off_at: None,
                    reasoning: None,
                    parts: Vec::new(),
                },
            ))
            .id();
        set_status(&mut world, worker, AgentStatus::Complete);
        assert_eq!(
            worker_terminal_result(&world, worker),
            Some(Ok("the old behaviour".to_string()))
        );
    }

    /// Spawn a worker sitting in a stage that demands a final output.
    fn spawn_required_output_worker(world: &mut World) -> Entity {
        let mut stage = Stage::new(
            "w".to_string(),
            ModelConfig::new("script".to_string(), "m".to_string()),
        );
        stage.require_output = true;
        let layout = ContextLayout::new(
            vec![RegionDefinition::new(
                "conversation".to_string(),
                RegionKind::Clearable,
                10_000,
            )],
            12_000,
        );
        let bp = Blueprint::new("w".to_string(), "d".to_string(), vec![stage], layout);
        let worker = world
            .spawn((parent_state(), AgentBlueprint(bp), StageCursor { index: 0 }))
            .id();
        set_status(world, worker, AgentStatus::Complete);
        worker
    }

    /// The fan-out reported "10 succeeded, 0 failed" over ten empty sections,
    /// because a worker that reached `Complete` without its required output was
    /// read as a success with nothing to say. The merge stage cannot tell those
    /// apart, so it writes a confident merge of nothing.
    ///
    /// This is the ordinary way it happens, not an edge case: a worker that
    /// cannot satisfy its validator retries until its iterations run out and
    /// leaves on the max-iterations path, which ends at `Complete`.
    #[test]
    fn a_worker_that_owes_an_output_and_has_none_is_a_failure() {
        let mut world = World::new();
        let worker = spawn_required_output_worker(&mut world);

        assert_eq!(
            worker_terminal_result(&world, worker),
            Some(Err(
                "worker finished without the final output its stage requires".to_string()
            )),
            "the merge has to be told a worker failed, and why"
        );
    }

    /// The same worker, having actually submitted: its answer is what it
    /// contributes, and the requirement is discharged.
    #[test]
    fn a_worker_that_owes_an_output_and_has_one_contributes_it() {
        let mut world = World::new();
        let worker = spawn_required_output_worker(&mut world);
        world
            .entity_mut(worker)
            .insert(crate::persistence::FinalOutput(
                leviath_core::output::FinalOutput {
                    content: "the rows".to_string(),
                    format: Some("csv".to_string()),
                    stage: "w".to_string(),
                    submitted_at: 0,
                    truncated: false,
                    artifacts: vec![],
                },
            ));

        assert_eq!(
            worker_terminal_result(&world, worker),
            Some(Ok("the rows".to_string()))
        );
    }

    /// A worker with a blueprint but no cursor cannot be placed in a stage, so
    /// there is no stage to read a requirement off. It keeps the fallback rather
    /// than being called a failure for a question that was never asked.
    #[test]
    fn a_worker_with_no_stage_to_read_owes_nothing() {
        let mut world = World::new();
        let bp = fanout_blueprint(cfg(None, 1, WorkerFailurePolicy::Continue));

        // No blueprint at all.
        let bare = world.spawn(parent_state()).id();
        assert!(!worker_requires_output(&world, bare));

        // A blueprint, but no cursor saying which stage it is in.
        let no_cursor = world.spawn((parent_state(), AgentBlueprint(bp))).id();
        assert!(!worker_requires_output(&world, no_cursor));

        // A cursor pointing past the end of the stage list.
        let past_end = world
            .spawn((
                parent_state(),
                AgentBlueprint(fanout_blueprint(cfg(
                    None,
                    1,
                    WorkerFailurePolicy::Continue,
                ))),
                StageCursor { index: 99 },
            ))
            .id();
        assert!(!worker_requires_output(&world, past_end));
    }

    /// A blueprint that never opted in keeps the last-turn fallback, empty text
    /// and all. Turning that into a failure would break every fan-out that does
    /// not declare `require_output`.
    #[test]
    fn a_worker_that_owes_nothing_keeps_the_last_turn_fallback() {
        let mut world = World::new();
        let worker = world.spawn(parent_state()).id();
        set_status(&mut world, worker, AgentStatus::Complete);

        assert_eq!(
            worker_terminal_result(&world, worker),
            Some(Ok(String::new()))
        );
    }

    #[test]
    fn worker_terminal_result_covers_every_status() {
        let mut world = World::new();
        let complete = world
            .spawn((
                parent_state(),
                InferenceResult {
                    response: "done text".to_string(),
                    tool_calls: vec![],
                    tokens_used: 0,
                    cut_off_at: None,
                    reasoning: None,
                    parts: Vec::new(),
                },
            ))
            .id();
        set_status(&mut world, complete, AgentStatus::Complete);
        assert_eq!(
            worker_terminal_result(&world, complete),
            Some(Ok("done text".to_string()))
        );

        let complete_no_infer = world.spawn(parent_state()).id();
        set_status(&mut world, complete_no_infer, AgentStatus::Complete);
        assert_eq!(
            worker_terminal_result(&world, complete_no_infer),
            Some(Ok(String::new()))
        );

        let errored = world.spawn(parent_state()).id();
        set_status(
            &mut world,
            errored,
            AgentStatus::Error {
                message: "x".to_string(),
            },
        );
        assert_eq!(
            worker_terminal_result(&world, errored),
            Some(Err("x".to_string()))
        );

        let cancelled = world.spawn(parent_state()).id();
        set_status(&mut world, cancelled, AgentStatus::Cancelled);
        assert!(worker_terminal_result(&world, cancelled).is_some_and(|r| r.is_err()));

        let running = world.spawn(parent_state()).id(); // Active
        assert_eq!(worker_terminal_result(&world, running), None);

        assert!(
            worker_terminal_result(
                &world,
                Entity::from_raw_u32(4242)
                    .expect("a small literal index is always a valid entity id")
            )
            .is_some_and(|r| r.is_err())
        );
    }

    /// The failure this bound exists for. A hundred workers answering at the
    /// size limit build a 25 MB report; `add_entry` rejects an over-budget entry
    /// rather than truncating, and the error was discarded - so the merge stage
    /// received nothing at all, silently, in exactly the case fan-out is for.
    #[test]
    fn a_huge_fan_out_still_reaches_the_merge_stage() {
        let mut world = World::new();
        let mut window = ContextWindow::new(100_000);
        window.add_region(leviath_core::Region::new(
            "conversation".to_string(),
            leviath_core::RegionKind::Clearable,
            10_000,
        ));
        let parent = world.spawn((parent_state(), window)).id();

        // A hundred workers, each answering at the per-submission cap.
        let huge = "x".repeat(leviath_core::output::MAX_FINAL_OUTPUT_BYTES);
        let summaries: Vec<(String, String)> =
            (0..100).map(|i| (format!("w{i}"), huge.clone())).collect();
        let report = build_report(&summaries, &[], Some(10_000));
        inject_results(&mut world, parent, "conversation", &report);

        let region = world
            .get::<ContextWindow>(parent)
            .expect("window")
            .get_region("conversation")
            .expect("region");
        assert!(
            !region.content.is_empty(),
            "the merge stage must receive something rather than nothing"
        );
        let landed = &region.content[0].content;
        // Every worker is still accounted for in the header, and the text says
        // it was cut rather than pretending to be whole.
        assert!(landed.contains("100 succeeded"), "header survives");
        assert!(landed.contains("truncated"), "and says it was cut");
        assert!(region.current_tokens <= region.max_tokens, "within budget");
    }

    /// Dividing the region between the workers makes the report fit an *empty*
    /// region, which is the easy case. A region already carrying something has
    /// less room than that, and the report-level trim is what keeps the write
    /// from being rejected outright: `add_entry` refuses an over-budget entry
    /// rather than shortening it, so without this the merge stage receives
    /// nothing at all.
    #[test]
    fn a_report_larger_than_what_is_left_of_the_region_is_trimmed_not_dropped() {
        const REGION_TOKENS: usize = 2_000;
        let mut world = World::new();
        let mut window = ContextWindow::new(100_000);
        window.add_region(leviath_core::Region::new(
            "worker_results".to_string(),
            leviath_core::RegionKind::Clearable,
            REGION_TOKENS,
        ));
        // Most of the region is already spoken for.
        let filler = "f".repeat(REGION_TOKENS * 4 * 8 / 10);
        let filler_tokens = leviath_core::estimate_tokens(&filler);
        window
            .add_typed_entry(
                "worker_results",
                leviath_core::EntryKind::UserMessage,
                filler,
                filler_tokens,
            )
            .expect("the filler fits");
        let parent = world.spawn((parent_state(), window)).id();

        // A report sized for the whole region, landing in what is left of it.
        let long = "x".repeat(5_000);
        let summaries: Vec<(String, String)> =
            (0..8).map(|i| (format!("w{i}"), long.clone())).collect();
        let report = build_report(&summaries, &[], Some(REGION_TOKENS));
        assert!(report.len() > REGION_TOKENS * 4 / 5, "the report is big");
        inject_results(&mut world, parent, "worker_results", &report);

        let region = world
            .get::<ContextWindow>(parent)
            .expect("window")
            .get_region("worker_results")
            .expect("region")
            .clone();
        assert_eq!(
            region.content.len(),
            2,
            "the report landed beside the filler"
        );
        let landed = &region.content[1].content;
        assert!(
            landed.contains("8 succeeded"),
            "the header survives the cut"
        );
        assert!(
            landed.contains(REPORT_TRUNCATION_MARKER.trim()),
            "and it says it was cut"
        );
        assert!(region.current_tokens <= region.max_tokens, "within budget");
    }

    /// The share is equal, so every worker appears. The first cut capped each
    /// worker at a fixed size and trimmed the finished report to fit, which gave
    /// the early workers their full allowance and cut the late ones off
    /// entirely - a hundred-way fan-out where only the first twenty were
    /// readable, with nothing saying so.
    #[test]
    fn every_worker_appears_in_a_large_fan_out() {
        // End to end: building the report and landing it in the region. The
        // unfairness was in the second half - a fixed per-worker size makes a
        // report far too big, and trimming *that* keeps the front and drops the
        // back.
        const REGION_TOKENS: usize = 40_000;
        let mut world = World::new();
        let mut window = ContextWindow::new(400_000);
        window.add_region(leviath_core::Region::new(
            "worker_results".to_string(),
            leviath_core::RegionKind::Clearable,
            REGION_TOKENS,
        ));
        let parent = world.spawn((parent_state(), window)).id();

        let long = "x".repeat(50_000);
        let summaries: Vec<(String, String)> =
            (0..100).map(|i| (format!("w{i}"), long.clone())).collect();
        let report = build_report(&summaries, &[], Some(REGION_TOKENS));
        inject_results(&mut world, parent, "worker_results", &report);

        let landed = world
            .get::<ContextWindow>(parent)
            .expect("window")
            .get_region("worker_results")
            .expect("region")
            .content[0]
            .content
            .clone();
        for i in 0..100 {
            assert!(
                landed.contains(&format!("## worker w{i}\n")),
                "worker w{i} never reached the merge stage"
            );
        }
        // And it says the sections are extracts, so the merge stage knows to go
        // to a worker's own run for the rest.
        assert!(landed.contains("read a worker's own run"));
    }

    /// Each worker gets the same room, whatever the count.
    #[test]
    fn the_share_shrinks_as_the_worker_count_grows() {
        assert!(bytes_per_worker(Some(40_000), 4) > bytes_per_worker(Some(40_000), 100));
        // A bigger region means a bigger share for the same workers.
        assert!(bytes_per_worker(Some(80_000), 10) > bytes_per_worker(Some(40_000), 10));
        // Never so small a section says nothing at all.
        assert_eq!(
            bytes_per_worker(Some(10), 10_000),
            MIN_REPORT_BYTES_PER_WORKER
        );
        // No readable budget falls back rather than dividing by nothing.
        assert_eq!(bytes_per_worker(None, 4), DEFAULT_REPORT_BYTES_PER_WORKER);
    }

    /// A blueprint can send the results somewhere other than the conversation,
    /// which is otherwise carrying the message history alongside them.
    #[test]
    fn results_go_to_the_named_region() {
        let mut world = World::new();
        let mut window = ContextWindow::new(100_000);
        window.add_region(leviath_core::Region::new(
            "conversation".to_string(),
            leviath_core::RegionKind::Clearable,
            10_000,
        ));
        window.add_region(leviath_core::Region::new(
            "worker_results".to_string(),
            leviath_core::RegionKind::Clearable,
            20_000,
        ));
        let parent = world.spawn((parent_state(), window)).id();
        inject_results(&mut world, parent, "worker_results", "the report");

        let w = world.get::<ContextWindow>(parent).expect("window");
        assert_eq!(
            w.get_region("worker_results")
                .expect("region")
                .content
                .len(),
            1
        );
        assert!(
            w.get_region("conversation")
                .expect("region")
                .content
                .is_empty(),
            "the default region is left alone"
        );
    }

    /// A named region the layout does not declare falls back rather than
    /// swallowing the whole report.
    #[test]
    fn an_unknown_results_region_falls_back_to_the_conversation() {
        let mut world = World::new();
        let mut window = ContextWindow::new(100_000);
        window.add_region(leviath_core::Region::new(
            "conversation".to_string(),
            leviath_core::RegionKind::Clearable,
            10_000,
        ));
        let parent = world.spawn((parent_state(), window)).id();
        inject_results(&mut world, parent, "typo_region", "the report");

        assert_eq!(
            world
                .get::<ContextWindow>(parent)
                .expect("window")
                .get_region("conversation")
                .expect("region")
                .content
                .len(),
            1
        );
    }

    /// A report that fits is passed through untouched, so the common case reads
    /// exactly as it did.
    #[test]
    fn a_small_fan_out_report_is_not_trimmed() {
        let mut world = World::new();
        let mut window = ContextWindow::new(100_000);
        window.add_region(leviath_core::Region::new(
            "conversation".to_string(),
            leviath_core::RegionKind::Clearable,
            10_000,
        ));
        let parent = world.spawn((parent_state(), window)).id();
        let report = build_report(
            &[("a".to_string(), "did the thing".to_string())],
            &[],
            Some(10_000),
        );
        inject_results(&mut world, parent, "conversation", &report);
        let landed = world
            .get::<ContextWindow>(parent)
            .expect("window")
            .get_region("conversation")
            .expect("region")
            .content[0]
            .content
            .clone();
        assert_eq!(landed, report);
    }

    /// A run at its ceiling stops widening.
    ///
    /// Refused at the spawn rather than at the split, so the workers already
    /// running keep going and the merge still happens on what came back. A run
    /// that stopped widening is a cheaper answer, not a failure.
    #[test]
    fn a_run_at_its_ceiling_does_not_spawn_another_worker() {
        let mut world = World::new();
        install(&mut world, TestSpawner::ok());
        let parent = world.spawn(parent_state()).id();

        let item = WorkItem {
            id: "one".to_string(),
            context: serde_json::json!({}),
        };
        let config = cfg(None, 3, WorkerFailurePolicy::Continue);

        // No ceiling: the spawn goes through, and the run now holds two agents.
        world.insert_resource(FanOutBudget(0));
        assert!(start_worker(&mut world, parent, &config, &item).is_ok());
        assert_eq!(run_tree_size(&world, parent), 2);

        // A ceiling of two, already reached.
        world.insert_resource(FanOutBudget(2));
        let refused = start_worker(&mut world, parent, &config, &item)
            .expect_err("the run is at its ceiling");
        assert!(refused.contains("ceiling is 2"), "{refused}");
        assert!(
            refused.contains("max_agents_per_run"),
            "names the knob that set it: {refused}"
        );
        assert_eq!(run_tree_size(&world, parent), 2, "and nothing was spawned");

        // Raised: it widens again, so this is a ceiling and not a latch.
        world.insert_resource(FanOutBudget(3));
        assert!(start_worker(&mut world, parent, &config, &item).is_ok());
        assert_eq!(run_tree_size(&world, parent), 3);
    }

    /// The headcount is the run's, not the branch's.
    ///
    /// A depth-2 worker asking "how many of us are there" must not answer with
    /// the size of its own subtree, or every branch gets the whole budget and
    /// the ceiling multiplies by however many branches there are.
    #[test]
    fn the_run_headcount_is_counted_from_the_root() {
        let mut world = World::new();
        let root = world.spawn_empty().id();
        let a = world.spawn_empty().id();
        let b = world.spawn_empty().id();
        let leaf = world.spawn_empty().id();

        world.entity_mut(root).insert(SubAgentChildren {
            children: vec![a, b],
            max_child_depth: 2,
        });
        for (child, depth) in [(a, 1), (b, 1)] {
            world.entity_mut(child).insert(ParentRef {
                parent_entity: root,
                parent_agent_id: "root".to_string(),
                depth,
            });
        }
        world.entity_mut(a).insert(SubAgentChildren {
            children: vec![leaf],
            max_child_depth: 2,
        });
        world.entity_mut(leaf).insert(ParentRef {
            parent_entity: a,
            parent_agent_id: "a".to_string(),
            depth: 2,
        });

        // Four agents: the root, two workers, and one grandchild.
        assert_eq!(run_tree_size(&world, root), 4, "counted from the root");
        assert_eq!(
            run_tree_size(&world, leaf),
            4,
            "and the same from the deepest leaf, which is the point"
        );
        assert_eq!(run_tree_size(&world, b), 4, "and from a childless branch");
    }

    /// A lone agent is one agent, so a run with no sub-agents is not somehow
    /// zero and does not get a free spawn past a ceiling of one.
    #[test]
    fn a_run_with_no_sub_agents_counts_itself() {
        let mut world = World::new();
        let solo = world.spawn_empty().id();
        assert_eq!(run_tree_size(&world, solo), 1);
    }

    /// A blueprint's fan-out ceiling reaches a split made through the tool.
    ///
    /// `max_items` lives on a `mode = "fan_out"` stage, and the tool is called
    /// from ordinary stages, so a tool-driven split saw no ceiling and made as
    /// many workers as the model named. Measured on a blueprint declaring
    /// `max_items = 3`: splits through this door made five and six, and one run
    /// reached 34 sub-agents where an earlier one reached 7.
    #[test]
    fn a_tool_split_takes_the_blueprints_declared_ceiling() {
        let bp = fanout_blueprint(FanOutConfig {
            max_items: Some(3),
            ..cfg(None, 3, WorkerFailurePolicy::Continue)
        });
        let mut world = World::new();
        // Cursor on the merge stage rather than the fan-out one, because that is
        // the situation: the tool is called from a stage that declares nothing.
        let e = world
            .spawn((AgentBlueprint(bp), StageCursor { index: 1 }))
            .id();

        assert_eq!(
            blueprint_fan_out_max_items(&world, e),
            Some(3),
            "the ceiling the blueprint wrote, found from a stage that does not \
             declare it"
        );
    }

    /// An author who wrote no ceiling is not given one. The fix carries a
    /// declared number to a second door; it does not invent a number.
    #[test]
    fn a_blueprint_declaring_no_ceiling_still_has_none() {
        let bp = fanout_blueprint(cfg(None, 3, WorkerFailurePolicy::Continue));
        let mut world = World::new();
        let e = world
            .spawn((AgentBlueprint(bp), StageCursor { index: 1 }))
            .id();
        assert_eq!(blueprint_fan_out_max_items(&world, e), None);

        // An entity carrying no blueprint declares nothing either, rather than
        // the lookup being an error.
        let bare = world.spawn_empty().id();
        assert_eq!(blueprint_fan_out_max_items(&world, bare), None);
    }

    #[test]
    fn build_report_lists_successes_and_failures() {
        let report = build_report(
            &[("a".to_string(), "ok-a".to_string())],
            &[("b".to_string(), "boom".to_string())],
            None,
        );
        assert!(report.contains("1 succeeded, 1 failed"));
        assert!(report.contains("## worker a\nok-a"));
        assert!(report.contains("## worker b FAILED\nboom"));
    }

    #[test]
    fn inject_conversation_is_a_noop_without_a_window() {
        let mut world = World::new();
        let has_window = world.spawn(window()).id();
        inject_results(&mut world, has_window, "conversation", "hello");
        assert!(
            world
                .get::<ContextWindow>(has_window)
                .unwrap()
                .get_region("conversation")
                .unwrap()
                .current_tokens
                > 0
        );
        // Entity without a ContextWindow: silently ignored.
        let no_window = world.spawn(parent_state()).id();
        inject_results(&mut world, no_window, "conversation", "hello");
    }

    #[test]
    fn set_status_is_a_noop_for_a_missing_agent() {
        let mut world = World::new();
        set_status(
            &mut world,
            Entity::from_raw_u32(77).expect("a small literal index is always a valid entity id"),
            AgentStatus::Complete,
        );
        assert_eq!(
            agent_status(
                &world,
                Entity::from_raw_u32(77)
                    .expect("a small literal index is always a valid entity id")
            ),
            None
        );
    }

    // ── force_transition (pipeline helper) edge cases via fan-out ─────────────

    #[test]
    fn force_transition_applies_routing_and_handles_despawn_and_overflow() {
        use crate::pipeline::force_transition;
        // Routing present on the target stage ⇒ ToolResultRoutingComponent added.
        let mut world = World::new();
        let mut setups = vec![setup(), setup()];
        setups[1].routing = Some(leviath_core::ToolResultRouting::default());
        let e = world
            .spawn((
                AgentBlueprint(fanout_blueprint(cfg(
                    Some("merge"),
                    2,
                    WorkerFailurePolicy::Continue,
                ))),
                StageCursor { index: 0 },
                parent_state(),
                StageProgress::default(),
                StageInferences(vec![stage_inf(), stage_inf()]),
                StageSetups(setups),
                VisitCounts::default(),
                window(),
            ))
            .id();
        let agent = crate::world::AgentId::in_world(&world, e);
        force_transition(&mut world, agent, 1);
        assert!(world.get::<ToolResultRoutingComponent>(e).is_some());
        assert!(world.get::<ReadyToInfer>(e).is_some());

        // Despawned entity: no panic, no effect.
        let gone = crate::world::AgentId::in_world(
            &world,
            Entity::from_raw_u32(9191).expect("a small literal index is always a valid entity id"),
        );
        force_transition(&mut world, gone, 1);
    }

    #[test]
    fn force_transition_marks_error_on_prompt_overflow() {
        use crate::pipeline::force_transition;
        // A tiny pinned region + a huge stage system prompt ⇒ overflow on entry.
        let layout = ContextLayout::new(
            vec![RegionDefinition::new(
                "task".to_string(),
                RegionKind::Pinned,
                20,
            )],
            1000,
        );
        let mut s0 = Stage::new(
            "fan".to_string(),
            ModelConfig::new("script".to_string(), "m".to_string()),
        );
        s0.mode = StageMode::FanOut {
            config: cfg(Some("merge"), 2, WorkerFailurePolicy::Continue),
        };
        let mut s1 = Stage::new(
            "merge".to_string(),
            ModelConfig::new("script".to_string(), "m".to_string()),
        );
        s1.config.insert(
            "system_prompt".to_string(),
            serde_json::Value::String("x".repeat(10_000)),
        );
        let bp = Blueprint::new("t".to_string(), "d".to_string(), vec![s0, s1], layout);

        let mut setups = vec![setup(), setup()];
        setups[1].system_prompt = Some("x".repeat(10_000));
        let mut w = ContextWindow::new(1000);
        w.add_region(Region::new("task".to_string(), RegionKind::Pinned, 20));
        let (mut world, e) = world_with(bp, setups, w);
        let agent = crate::world::AgentId::in_world(&world, e);
        force_transition(&mut world, agent, 1);
        assert_errored(&world, e);
    }

    /// Build a world with one agent carrying the given blueprint/setups/window.
    fn world_with(bp: Blueprint, setups: Vec<StageSetup>, w: ContextWindow) -> (World, Entity) {
        let mut world = World::new();
        let e = world
            .spawn((
                AgentBlueprint(bp),
                StageCursor { index: 0 },
                parent_state(),
                StageProgress::default(),
                StageInferences(vec![stage_inf(), stage_inf()]),
                StageSetups(setups),
                VisitCounts::default(),
                w,
            ))
            .id();
        (world, e)
    }
}
