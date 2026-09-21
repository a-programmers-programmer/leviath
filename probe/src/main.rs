//! GQL-24 probe: host the old LifeOps PoC schema on a REAL GraphQL engine.
//!
//! The PoC hand-rolled a tokenizer/parser/executor. This probe proves the same
//! schema (Query.runs/recentRun/context, Mutation.spawnRun/submitProposal,
//! Run{run_id,status,stage,tool_calls,iteration}, Proposal{...}) can be served
//! by async-graphql with NO hand-written parser. Field names are kept
//! snake_case so the PoC's own queries still parse.

use async_graphql::{EmptySubscription, InputObject, Object, Request, Schema};

// ── Types (mirror the PoC's Run / Proposal) ──────────────────────────────────

struct Run {
    run_id: String,
    status: String,
    stage: String,
    tool_calls: i32,
    iteration: i32,
}

#[Object(name = "Run")]
impl Run {
    #[graphql(name = "run_id")]
    async fn run_id(&self) -> &str {
        &self.run_id
    }
    async fn status(&self) -> &str {
        &self.status
    }
    async fn stage(&self) -> &str {
        &self.stage
    }
    #[graphql(name = "tool_calls")]
    async fn tool_calls(&self) -> i32 {
        self.tool_calls
    }
    async fn iteration(&self) -> i32 {
        self.iteration
    }
}

struct Proposal {
    proposal_id: String,
    run_id: String,
    proposal: String,
    status: String,
    created_at: String,
}

#[Object(name = "Proposal")]
impl Proposal {
    #[graphql(name = "proposal_id")]
    async fn proposal_id(&self) -> &str {
        &self.proposal_id
    }
    #[graphql(name = "run_id")]
    async fn run_id(&self) -> &str {
        &self.run_id
    }
    async fn proposal(&self) -> &str {
        &self.proposal
    }
    async fn status(&self) -> &str {
        &self.status
    }
    #[graphql(name = "created_at")]
    async fn created_at(&self) -> &str {
        &self.created_at
    }
}

// ── Query (mirror Query.runs / recentRun / context) ──────────────────────────

struct Query;

#[Object(name = "Query")]
impl Query {
    /// Same shape as the PoC: runs(status, limit) -> [Run!]!
    async fn runs(&self, status: Option<String>, limit: Option<i32>) -> Vec<Run> {
        let all = vec![
            Run {
                run_id: "worker-1786334598-280e5a35a0b7".into(),
                status: "Complete".into(),
                stage: "verify".into(),
                tool_calls: 20,
                iteration: 16,
            },
            Run {
                run_id: "worker-1786380598-653514d07535".into(),
                status: "Active".into(),
                stage: "act".into(),
                tool_calls: 3,
                iteration: 2,
            },
        ];
        let filtered: Vec<Run> = match &status {
            Some(s) => all.into_iter().filter(|r| &r.status == s).collect(),
            None => all,
        };
        let cap = limit.unwrap_or(20).clamp(0, 200) as usize;
        filtered.into_iter().take(cap).collect()
    }

    /// recentRun -> Run (the newest run).
    #[graphql(name = "recentRun")]
    async fn recent_run(&self) -> Run {
        Run {
            run_id: "worker-1786380598-653514d07535".into(),
            status: "Active".into(),
            stage: "act".into(),
            tool_calls: 3,
            iteration: 2,
        }
    }

    /// context(run_id) -> String (trace JSON, capped at 40_000 chars tail).
    async fn context(&self, #[graphql(name = "run_id")] run_id: String) -> String {
        let body = format!("{{\"run_id\":\"{run_id}\",\"trace\":[...]}}");
        if body.len() > 40_000 {
            body[body.len() - 40_000..].to_string()
        } else {
            body
        }
    }
}

// ── Mutation (mirror Mutation.spawnRun / submitProposal) ─────────────────────

#[derive(InputObject)]
struct SpawnRunInput {
    task: String,
    blueprint: Option<String>,
}

struct Mutation;

#[Object(name = "Mutation")]
impl Mutation {
    #[graphql(name = "spawnRun")]
    async fn spawn_run(&self, task: String, blueprint: Option<String>) -> Run {
        let _ = (task, blueprint);
        Run {
            run_id: "worker-spawned-by-graphql".into(),
            status: "Active".into(),
            stage: "spawn".into(),
            tool_calls: 0,
            iteration: 0,
        }
    }

    #[graphql(name = "submitProposal")]
    async fn submit_proposal(
        &self,
        #[graphql(name = "run_id")] run_id: String,
        proposal: String,
    ) -> Proposal {
        Proposal {
            proposal_id: "prop-20260810095057-8fcc90".into(),
            run_id,
            proposal,
            status: "submitted".into(),
            created_at: "2026-08-10T16:50:57Z".into(),
        }
    }
}

#[tokio::main]
async fn main() {
    let schema = Schema::new(Query, Mutation, EmptySubscription);

    // 1. The PoC's own query, verbatim, must parse and resolve.
    let poc_query = r#"{
        runs(limit: 3) { run_id status stage tool_calls iteration }
        recentRun { run_id status }
        context(run_id: "x")
    }"#;
    let r = schema.execute(Request::new(poc_query)).await;
    println!("POC_QUERY_OK={}", serde_json::to_string(&r).unwrap());

    // 2. Introspection must walk (schema is self-discoverable).
    let intro = schema
        .execute(Request::new(
            "{ __schema { queryType { name } mutationType { name } types { name kind } } }",
        ))
        .await;
    let intro_json = serde_json::to_string(&intro).unwrap();
    println!(
        "INTROSPECTION_OK={}",
        intro_json.contains("\"Query\"") && intro_json.contains("\"Mutation\"")
    );

    // 3. A mutation round-trips.
    let m = schema
        .execute(Request::new(
            r#"mutation { spawnRun(task: "t") { run_id status } submitProposal(run_id: "r", proposal: "p") { proposal_id run_id status created_at } }"#,
        ))
        .await;
    println!("MUTATION_OK={}", serde_json::to_string(&m).unwrap());

    // 4. Bad query yields a GraphQL error, not a crash (proper semantics).
    let bad = schema.execute(Request::new("{ nope }")).await;
    println!("ERROR_OK={}", serde_json::to_string(&bad).unwrap());
}