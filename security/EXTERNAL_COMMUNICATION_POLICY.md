# ARGOS External Communication Policy

## Default

`EXTERNAL_SEND_ENABLED=false`

ARGOS may:
- read incoming external messages;
- analyze requests;
- prepare drafts;
- generate technical summaries.

ARGOS must not automatically:
- send emails;
- reply to support tickets;
- contact companies, press, or communities;
- publish announcements.

## Approval gate

Any external send requires explicit owner approval recorded in the action log.

## FastAPI Cloud

Do not send direct support requests automatically. Use only approved channels and provide a minimal reproducible example when requested.
