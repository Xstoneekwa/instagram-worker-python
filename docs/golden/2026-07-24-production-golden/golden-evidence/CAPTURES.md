# Capture evidence

Operator captures were reviewed during the certification sequence but are not
copied into this repository. They can expose usernames, phone identifiers,
local UI state or other operational metadata.

Sanitized conclusions retained by this checkpoint:

| Capture subject | Certified conclusion |
|---|---|
| Auto Login assigned clone | correct package and clone opened; canonical identity guard passed |
| Post-login screen | late `Save your login info?` variant identified; bounded stabilization patch covered offline |
| BotApp profile projection | target account reached `connected`; stale operator review was superseded |
| Dispatcher view | relay authenticated; dispatcher running; no queue backlog |
| Follow navigation | deployed baseline contains f93c501 navigation contract; physical replay remains a known gap |

Raw capture custody is outside this Git checkpoint. No screenshot is required
to restore or verify the code release; all executable provenance is SHA-based.
