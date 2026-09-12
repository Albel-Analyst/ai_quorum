import type { ThreadMessage } from "@copilotkit/channels";

export function normalizeHistory(history: ThreadMessage[]) {
  return history.filter(m => !m.providerMessage?.deleted).map(m => {
    const id = m.providerMessage?.logicalMessageId ?? m.ts;
    const user = m.providerMessage?.actor ?? m.user;
    if (!id || !user?.id) throw new Error("Channels history lacks source identity");
    const timestamp = m.providerMessage?.occurredAt ?? new Date(Number(id) * 1000).toISOString();
    return {
      id, user_id: user.id, user_name: m.user?.name ?? "", text: m.text,
      at: timestamp, is_bot: m.isBot ?? user.kind !== "human",
    };
  });
}

export async function send(path: string, body: unknown) {
  if (!process.env.QUORUM_BRIDGE_TOKEN) throw new Error("QUORUM_BRIDGE_TOKEN is required");
  const response = await fetch(`http://127.0.0.1:${process.env.QUORUM_BRIDGE_PORT ?? 8765}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.QUORUM_BRIDGE_TOKEN}` },
    body: JSON.stringify(body), signal: AbortSignal.timeout(120_000),
  });
  if (!response.ok) throw new Error(`Quorum bridge: HTTP ${response.status}`);
  return response.json() as Promise<{ tracked?: boolean }>;
}
