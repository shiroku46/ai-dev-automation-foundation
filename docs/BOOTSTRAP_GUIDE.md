# Bootstrap guide

The generator copies a reviewed allowlist only. It does not copy Git history, Issues, Pull Requests, Actions logs, Secrets, repository settings, or external documents.

After generation:

1. inspect `INSTALL_CHECKLIST.md`;
2. run the export guard, repository validator, and tests;
3. create a protected-change Issue before enabling write-capable workflows;
4. configure any required provider credential through the provider/GitHub UI;
5. run a disposable E2E repository before production use.
