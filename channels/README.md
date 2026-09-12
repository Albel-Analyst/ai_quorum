# CopilotKit Channels ingress for the existing Python core

This is an optional **direct Slack adapter**, using the same Slack installation
and tokens as the existing code. It is not the managed Slack onboarding flow.
The package pair matches the official hackathon starter: Channels 0.9.2,
Runtime 1.70.3, AG-UI client 0.0.59. Dependencies are locked.

Channels owns the one Socket Mode connection, mention/message dispatch,
`Thread.getMessages()` and thread subscription. Its stable direct conversation
identity (`channel::threadTimestamp`, verified in the pinned SDK) is bound to
the persisted OpenLoop. The Python engine remains responsible for structured
extraction, authorization, reduction, persistence, tool calls and scheduling.

The existing Slack Web API renderer updates the canonical card across deliveries
and after restarts. Public Bolt middleware on the direct adapter forwards its
Block Kit buttons and modals to the same Python approval handlers. There are
no invented Channels APIs, persisted managed MessageRefs or model-exposed writes.
SDK-native interrupt/resume approval flows are not implemented.

In `.env`, configure the existing Slack/OpenAI keys plus:

```dotenv
CHANNELS_ENABLED=true
CHANNEL_CODE=your-project-channel-code
INTELLIGENCE_API_KEY=your-project-api-key
QUORUM_BRIDGE_TOKEN=a-long-random-local-secret
QUORUM_BRIDGE_PORT=8765
```

Use the Channel Code and project key from the intended CopilotKit Intelligence
project. The SDK verifies startup is actually `online`; `setup_required` is not
treated as success. No live connection has yet been demonstrated without these
credentials. Do not switch an already managed Slack installation to this direct
adapter; this path exists to reuse this repository's existing direct installation.

```bash
# From repo root, terminal 1: Python core + authenticated loopback bridge + scheduler
make run

# Terminal 2: the ONLY Socket Mode ingress
npm ci --prefix channels --ignore-scripts
npm start --prefix channels

# Offline checks
npm run check --prefix channels
npm test --prefix channels
```

Without Channels credentials, leave `CHANNELS_ENABLED=false` and `make run`
uses the existing Python Bolt transport. This fallback demonstrates Slack,
but does not establish CopilotKit sponsor usage.

Sources:
- [Official starter Channel](https://github.com/CopilotKit/agents-everywhere-starter-kit/blob/main/apps/channel/src/channel.tsx)
- [Official runtime bootstrap](https://github.com/CopilotKit/agents-everywhere-starter-kit/blob/main/apps/channel/src/server.ts)
- [Thread API and managed-reference restrictions](https://docs.copilotkit.ai/reference/channels/classes/Thread)

Limits: one core process; no persisted SDK action continuations; Python storage
is the subscription authority after restart. Only direct Slack keys are accepted
by the bridge. Its token is checked before parsing any payload, and it listens
only on loopback. The public runtime listener is also bound to loopback.
