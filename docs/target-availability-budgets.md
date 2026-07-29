# Target Availability budgets

These values are domain constants only and are not injected into runtime configuration:

| Budget | V2-1 value |
|---|---:|
| username lookup | 2,500 ms |
| Followers entry | 4,000 ms |
| retry | 1 |
| navigation | 8,000 ms |
| total Availability | 10,000 ms |

No V2-1 hook performs an additional lookup, Followers entry, retry or navigation. Future activation must enforce the total wall-clock ceiling and always permit `skip -> next target`.
