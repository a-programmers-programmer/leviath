Run this fixture with the usual `lev` e2e command.
Expect a `hook wait: parking 5s` log line.
The fixture stays parked and spends zero inference until cancelled.
Cancel the run after checking the parked state.
Pipeline tests cover re-arming after wait expiry.
