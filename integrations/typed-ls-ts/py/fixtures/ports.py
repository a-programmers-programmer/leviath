from __future__ import annotations


class OAuthRefreshTokenInput(WorkflowStepInput):
    token: Confirmation[Confidential, "STEP_RUNNER"]

    def refresh(self, token: Confirmation[Confidential, "STEP_RUNNER"]) -> WorkflowRun:
        return token


class OnlyInPython:
    pass