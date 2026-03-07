## Project constraints
- Keep the project deployable on a 2GB Linux server
- Prefer minimal migrations and minimal dependencies
- Keep CLI-first workflow
- Preserve layered architecture
- Do not introduce web frameworks
- Keep reminder/channel logic decoupled

## Coding guidance
- Prefer small safe changes
- Update README and tests together
- Validate new CLI inputs strictly

## CLI output conventions
- `list`: compact scan-friendly rows.
- `show`: key/value block.
- `history`: time-ordered readable event stream.
- For repeated state-changing actions, prefer status-aware feedback such as `already ...`.

## Deployment conventions
- Keep deployment assets simple and systemd-based.
- Prefer oneshot services + timers + shell wrappers.
- Avoid Docker/cron/webhook-only flows unless explicitly requested.
