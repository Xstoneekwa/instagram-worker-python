# Locked decisions

The canonical decision list is maintained at
[`docs/LOCKED_DECISIONS.md`](../../LOCKED_DECISIONS.md). The following are the
Golden release gates:

1. preserve isolated historical 07ee Auto Login and adapter routing;
2. preserve strict package/app-instance/clone binding with no primary fallback;
3. preserve bounded late-popup stabilization and existing popup selectors;
4. preserve f93c501 list continuation and true Suggestions boundary;
5. preserve Golden Follow/Mute/Like/Return CT invariants;
6. preserve active-SAST-day warmup and minimum-of-limits resolution;
7. preserve durable dispatcher ownership and embedded scheduler;
8. preserve exact incidents, human review and no-leak notifications;
9. never promote a mutable checkout or infer live provenance from stale docs;
10. require a new GO and checkpoint for any functional change.

This file is a frozen view. Future decisions update the root canonical document
and create a new dated Golden checkpoint.
