import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeHistory } from "./delivery.js";

test("preserves native source identity and rejects incomplete history", () => {
  const [message] = normalizeHistory([{ ts: "1700000000.000100", text: "I'll do it", user: { id: "U123", kind: "human" } }]);
  assert.equal(message.id, "1700000000.000100");
  assert.equal(message.user_id, "U123");
  assert.equal(message.is_bot, false);
  assert.throws(() => normalizeHistory([{ text: "missing identity" }]));
});
