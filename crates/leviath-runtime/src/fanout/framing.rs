//! Split-round framing: telling a re-entered fan-out stage it has been here before.
use super::*;

/// How many previous work-item ids the re-entry framing lists before it stops.
///
/// A fan-out can legitimately be thirty items wide, and thirty slugs at the top
/// of a prompt is a wall the instruction after it has to compete with.
pub(super) const FRAMED_PREVIOUS_ITEMS: usize = 12;

/// What [`frame_split_round`] selects.
///
/// `&'static` is bevy's `WorldQuery` convention, not a claim about lifetimes:
/// the borrow is bound when the query is fetched.
pub(super) type FrameSplitRoundQuery = (
    Entity,
    &'static AgentBlueprint,
    &'static StageCursor,
    &'static crate::pipeline::VisitCounts,
    &'static mut ContextWindow,
    Option<&'static PreviousWorkItems>,
);

/// Tell a fan-out stage it has been here before.
///
/// The failure this exists for: a `deep-researcher` run finished its fan-out,
/// ran `analyze`, routed back through `gather`, and re-entered the same fan-out
/// stage. The split prompt was byte for byte the one it had already answered,
/// while `conversation` still carried the first split, the workers'
/// consolidated report and the analysis built on it. The model read all that and
/// answered "I have completed the research", which is true and is not a list of
/// work items. Two corrections later the run was dead.
///
/// So the second split is asked a different question from the first, and told
/// that an empty list is a real answer to it. The stage's own `split_prompt` is
/// unchanged, and a first entry is not touched at all - no framing, no extra
/// tokens, no behaviour change for the run that splits once.
pub(crate) fn frame_split_round(
    mut agents: Query<FrameSplitRoundQuery, With<crate::pipeline::StageJustEntered>>,
) {
    crate::tick_scope::clear();
    for (entity, bp, cursor, visits, mut window, previous) in agents.iter_mut() {
        crate::tick_scope::enter(entity);
        let stage = &bp.0.stages[cursor.index];
        if !matches!(stage.mode, StageMode::FanOut { .. }) {
            continue;
        }
        // `enter_stage` bumps the count before this runs, so a first entry reads
        // as 1 and there is nothing to say.
        let round = visits.0.get(&stage.name).copied().unwrap_or(1);
        if round < 2 {
            continue;
        }
        crate::pipeline::inject_system_nudge(
            &mut window,
            &split_round_framing(round, previous.map_or(&[], |p| p.0.as_slice())),
        );
    }
}

/// What a re-entered fan-out stage is told before it splits again.
pub(super) fn split_round_framing(round: usize, previous: &[String]) -> String {
    let tool = leviath_core::blueprint::FAN_OUT_TOOL;
    let already = match previous.is_empty() {
        // A previous round whose ids were lost - a daemon restart between the
        // two entries drops the component - still gets the framing, because the
        // part that matters is "you have been here before", not the list.
        true => "Work has already been handed out from this stage once".to_string(),
        false => format!(
            "These work items have already been researched, and their findings are \
             in this run's context:\n{}{}",
            previous
                .iter()
                .take(FRAMED_PREVIOUS_ITEMS)
                .map(|id| format!("  - {id}\n"))
                .collect::<String>(),
            match previous.len() > FRAMED_PREVIOUS_ITEMS {
                true => format!("  ...and {} more\n", previous.len() - FRAMED_PREVIOUS_ITEMS),
                false => String::new(),
            }
        ),
    };
    format!(
        "This is split round {round} of this stage. {already}.\n\nName ONLY \
         sub-questions that are still unanswered - do not hand out work that has \
         already been done, and do not restate the previous round. If nothing is \
         left to hand out, call `{tool}` with an empty `items` array: the run then \
         moves on to the next stage, which is the right outcome when the work is \
         finished. Answering that in prose is not."
    )
}
