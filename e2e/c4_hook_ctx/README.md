Run the agent to completion with `lev run e2e/c4_hook_ctx/agent.leviath`.
Expect `c4 iter=...` lines from before-inference and terminal hooks.
The iteration and attempt values should increase across inference calls.
The terminal hook should report a cost above zero after billed inference.
The fixture uses the configured OpenRouter model.
