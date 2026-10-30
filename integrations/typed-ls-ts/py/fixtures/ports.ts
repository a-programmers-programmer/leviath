export class OAuthRefreshTokenInput implements WorkflowStepInput {
  token: Confirmation<Confidential, "STEP_RUNNER">;

  refresh(token: Confirmation<Confidential, "STEP_RUNNER">): WorkflowRun {
    return token;
  }
}

export class OnlyInTypescript {
  declared(name: string): void {}
}