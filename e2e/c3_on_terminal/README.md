Run the agent to completion.
Expect one `c3-terminal status=complete` line.
Run it again, then cancel it with `lev cancel`.
Expect one `c3-terminal status=cancelled` line.
Never expect two lines for one run.
The fixture uses the configured OpenRouter model.
