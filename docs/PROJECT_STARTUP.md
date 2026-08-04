# Repository startup: mandatory Phase 0

Every newly bootstrapped repository must complete setup steps 1–5 before the harmless Bootstrap acceptance exercise. Successful completion of the acceptance exercise in step 6 is the final Phase 0 gate. The first product Issue, product `/claude-run`, or product implementation request may start only after that final gate passes.

The coordinating ChatGPT/agent performs every check available through connected tools first. It asks the owner only for settings that require an authenticated provider UI, local authenticated CLI, MFA, CAPTCHA, hardware key, or another operation for which no callable connector/API exists.

Pre-PR Phase 0 guidance is a narrowly scoped exception to the runtime GitHub human-notice mechanism: it is delivered directly in the project-start conversation for the exact repository before GitHub orchestration starts. It does not call `human_only_notice()`. It does not publish an automated GitHub notice. It does not create a new runtime reason code. It does not require an Issue/PR destination. It may contain only the exact non-secret UI navigation or local command, the reason it is necessary, and the automatic-resumption condition. The coordinator records completion later in the non-secret Bootstrap acceptance evidence and does not repeat the guidance unless connected evidence shows the prerequisite became unusable.

## 1. Connect the exact repository

- Connect the GitHub account to ChatGPT/Codex.
- Authorize the exact target repository in the GitHub app or connector configuration.
- Confirm the repository is visible to the connected tools after any indexing delay.

## 2. Create the exact-repository Codex environment

- Create a Codex environment for the exact target repository.
- Confirm that the repository is selectable and usable in Codex.

Provider replies such as `To use Codex here, create an environment for this repo` or `To use Codex here, create a Codex account and connect to github` mean the setup prerequisites are incomplete. They are not review evidence and must not trigger repeated implementation or review requests.

## 3. Install the Claude credential when OAuth is used

- Generate the token locally with `claude setup-token`.
- Store the value only as the repository Actions Secret named exactly `CLAUDE_CODE_OAUTH_TOKEN`.
- Never paste, print, commit, log, or include the token value in an Issue, Pull Request, workflow, document, or chat transcript.

## 4. Configure GitHub Actions and Workflow permissions

In the exact target repository, open:

`Settings` → `Actions` → `General` → `Workflow permissions`

Set and save both required options:

1. select **Read and write permissions**;
2. enable **Allow GitHub Actions to create and approve pull requests**.

Also confirm:

- GitHub Actions are enabled for the repository;
- the Foundation workflows exist on the default branch;
- `AUTOMATION_OWNER` is set only when the repository owner is not the intended trusted actor.

These Workflow permissions are mandatory because the Foundation must be able to create or update branches and Pull Requests, post comments and labels, update review/readiness state, and complete the bounded merge orchestration. If those operations repeatedly fail, inspect this setting before retrying workflows or asking the owner to repost commands.

The coordinator must verify the repository setting through connected repository-settings/API tools when a callable endpoint exists. When no such endpoint is available, use the narrowly scoped pre-PR guidance defined above: give the exact navigation once, never request a value or screenshot containing credentials, and resume automatically after the owner confirms the setting is saved.

## 5. Validate the installed Foundation

Run the Foundation checks or the generated-target equivalent:

```bash
python scripts/public_export_guard.py .
python scripts/validate_repository.py
python -m unittest discover -s tests
```

## 6. Complete Bootstrap acceptance and finish Phase 0

After steps 1–5 pass, prove that the exact repository can complete one harmless bounded candidate:

- create a dedicated branch and Pull Request;
- receive native CI and Unit Tests on the exact remote head SHA;
- receive the required review response instead of a provider onboarding error;
- confirm automation can perform the required bounded write operations without a Workflow-permissions failure;
- confirm expected-head protection is used for the merge decision.

Successful acceptance completes Phase 0 and unlocks product work.

## Phase 0 evidence

Record only non-secret evidence:

- exact repository name;
- date of acceptance;
- GitHub/Codex repository access confirmed;
- Codex environment confirmed;
- Secret **name** confirmed, never its value;
- GitHub Actions enabled;
- Workflow permissions set to **Read and write permissions**;
- **Allow GitHub Actions to create and approve pull requests** enabled;
- Bootstrap acceptance Issue/PR, exact head SHA, and successful checks.

After the exact repository passes Phase 0, **Do not request these steps again** unless connected evidence shows that the repository authorization, environment, credential, Actions availability, or Workflow permissions are no longer usable. Resume orchestration automatically after the missing UI-only prerequisite is completed; the owner must not be asked to repost `/claude-run`, copy a Codex prompt, create a Pull Request, press Retry, or repeat routine instructions.
