# Documentation

Start with the [server README](../readme.md) for installation, environment
variables, and production startup. This directory holds current explanations
that are useful beyond the code itself.

| Document | Use it for | Check against when updating |
| --- | --- | --- |
| [Architecture](development/architecture.md) | App lifecycle and data flow | App factory, controllers, services, socket handlers |
| [Feature contracts](development/feature-contracts.md) | Non-obvious behavior to preserve | Affected feature code and regression tests |
| [Database operations](operations/database.md) | Schema changes and legacy imports | Schema, Alembic environment, migration mixin |
| [YNAB review](features/ynab.md) | Setup, review workflow, and test mode | YNAB service, API, and browser UI |

Component guides stay beside their implementation:

- [ESP32 gateway](../esp32_ws/README.md): installation, transport, and diagnostics
- [Temperature firmware](../ESP32_temperature/README.md): configuration, build, and RPC
- [Agent guide](../AGENTS.md): repository working instructions

## Maintenance

Update the relevant guide in the same change as the behavior it describes.
Verify commands, defaults, and payloads against the linked implementation.
Keep setup in the server README, component details in component guides, and
cross-cutting behavior here; link rather than duplicate.

Keep a document only when it has a current audience and a clear source to check.
Remove obsolete guidance and update incoming links. Completed implementation
plans and historical experiments belong in Git history, not a parallel archive.
Do not vendor external API documentation or maintain handwritten copies of
schemas and endpoint inventories. Use the implementation and upstream sources.
