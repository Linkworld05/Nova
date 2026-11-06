# Community roadmap

## Good first contributions

- Improve clean Windows setup instructions and actionable missing-model errors.
- Add synthetic regressions for clipboard, manual revisions and per-chat state.
- Improve keyboard access and readability without changing stored data formats.
- Add deterministic timer/callback tests for generated Tk apps.

## Agent reliability

- Separate app launch from managed process lifetime and actual behavior checking.
- Reduce repeated tool loops and make failure recovery state explicit.
- Cover Python/Tk and Node cases without claiming visual/audio/browser approval.
- Test partial failure, cancellation, stale evidence and Undo conflicts.
- Strengthen test-adequacy checks; a model reviewing its own family's output is
  fallible even in a separate context.

## Portability and maintenance

- Split the large UI and agent orchestration modules behind stable interfaces.
- Add reproducible optional voice environments and dependency license inventories.
- Consider per-user data directories with explicit migration and rollback tests.
- Validate installation on clean Windows machines and several hardware sizes.

Open an issue with concrete acceptance criteria before implementing large items.
There are no promised completion dates or automatic model-training jobs.
