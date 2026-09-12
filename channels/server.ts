/** Direct Channels transport for Quorum's existing credentialed Slack app.
 * The Python process must use CHANNELS_ENABLED=true (no second Socket Mode client).
 * Pinned APIs match the official starter pair; no managed MessageRef is persisted.
 */
import { createServer } from "node:http";
import { createChannel } from "@copilotkit/channels";
import { slack } from "@copilotkit/channels/slack";
import { BuiltInAgent, CopilotKitIntelligence, CopilotRuntime } from "@copilotkit/runtime/v2";
import { createCopilotNodeListener } from "@copilotkit/runtime/v2/node";
import { normalizeHistory, send } from "./delivery.js";

function required(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
if (process.env.CHANNELS_ENABLED !== "true") throw new Error("Set CHANNELS_ENABLED=true for both processes");
required("QUORUM_BRIDGE_TOKEN");
const adapter = slack({
  botToken: required("SLACK_BOT_TOKEN"), appToken: required("SLACK_APP_TOKEN"),
  assistant: false, respondTo: { directMessages: false, threadReplies: "afterBotReply" },
});

// Public Bolt middleware on the supported direct adapter preserves existing
// native Block Kit controls/modals while Channels owns the sole ingress socket.
adapter.app.use(async ({ body, ack, next }) => {
  const raw = body as Record<string, unknown>;
  const actions = raw.actions as { action_id?: string }[] | undefined;
  const view = raw.view as { callback_id?: string } | undefined;
  const ours = raw.type === "block_actions" && actions?.some(a => a.action_id?.startsWith("q:"))
    || raw.type === "view_submission" && ["confirm", "commitment", "attest", "supersede"].includes(view?.callback_id ?? "");
  if (!ours) return next();
  await ack?.();
  void send("/interaction", body).catch(error => console.error("Quorum interaction failed", error.message));
});

const channel = createChannel({
  name: required("CHANNEL_CODE"), identifyUser: "platform", adapters: [adapter],
  // Required SDK factory. Ingress handlers delegate extraction to the existing
  // Python engine; they never call runAgent or expose model-controlled writes.
  agent: threadId => {
    const agent = new BuiltInAgent({ model: `openai:${process.env.LLM_MODEL_FAST ?? "gpt-5.4-mini"}` });
    agent.threadId = threadId;
    return agent;
  },
});

channel.onMention(async ({ thread, message }) => {
  const result = await send("/delivery", {
    conversation_key: thread.conversationKey, event_id: message.eventId ?? message.operation.revisionId,
    actor_id: message.actor.id, activate: true, messages: normalizeHistory(await thread.getMessages()),
  });
  if (result.tracked) await thread.subscribe();
});
channel.onMessage(async ({ thread, message }) => {
  // The durable Python loop is the subscription authority after a restart.
  const result = await send("/delivery", {
    conversation_key: thread.conversationKey, event_id: message.eventId ?? message.operation.revisionId,
    actor_id: message.actor.id, activate: false, messages: normalizeHistory(await thread.getMessages()),
  });
  if (result.tracked && !await thread.isSubscribed()) await thread.subscribe();
});

const runtime = new CopilotRuntime({ agents: {}, channels: [channel],
  intelligence: new CopilotKitIntelligence({ apiKey: required("INTELLIGENCE_API_KEY") }) });
const listener = createCopilotNodeListener({ runtime, basePath: "/api/copilotkit" });
const server = createServer(listener);
const shutdown = async () => { await listener.channels.stop(); server.close(); };
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
await listener.channels.ready({ timeoutMs: 30_000 });
if (listener.channels.status().overall !== "online") {
  await shutdown();
  throw new Error("Channels is not online; finish the Intelligence project configuration");
}
server.listen(Number(process.env.CHANNELS_PORT ?? 3000), "127.0.0.1");
