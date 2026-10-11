// Narrow official OpenAI JavaScript SDK compatibility smoke (CI/offline).
//
// Proves black-box compatibility against a running AetherGate gateway using the
// **official** `openai` Node SDK (never raw fetch): Models listing, Chat
// Completions non-stream + stream, and Responses non-stream + stream, plus the
// explicit unsupported-feature 400 for Responses tools.
//
// Configuration is read from the environment (never argv):
//   AETHERGATE_SDK_BASE_URL  e.g. http://127.0.0.1:8080/v1
//   AETHERGATE_SDK_API_KEY   an inference `agk_...` credential
//   AETHERGATE_SDK_MODEL     public model alias (default "golden-model")
//   AETHERGATE_SDK_STREAMING set to "1" to also exercise streaming surfaces
//
// Prints one safe JSON result line. It never prints the key or any prompt /
// completion content.

import OpenAI from "openai";

const baseURL = process.env.AETHERGATE_SDK_BASE_URL;
const apiKey = process.env.AETHERGATE_SDK_API_KEY;
const model = process.env.AETHERGATE_SDK_MODEL || "golden-model";
const withStreaming = process.env.AETHERGATE_SDK_STREAMING === "1";

function fail(message) {
  process.stderr.write(`[js-sdk-smoke] FAIL: ${message}\n`);
  process.exit(1);
}

if (!baseURL || !apiKey) {
  fail("AETHERGATE_SDK_BASE_URL and AETHERGATE_SDK_API_KEY must be set");
}

const client = new OpenAI({ baseURL, apiKey, maxRetries: 0, timeout: 60_000 });

const result = {};

// Models
const models = await client.models.list();
if (!Array.isArray(models.data) || models.data.length === 0) {
  fail("models list was empty");
}
if (!models.data.some((m) => m.id === model)) {
  fail(`model ${model} not present in models list`);
}
result.models = "ok";

// Chat non-stream
const chat = await client.chat.completions.create({
  model,
  messages: [{ role: "user", content: "js sdk smoke" }],
});
if (!chat.choices?.[0]?.message?.content) {
  fail("chat non-stream produced empty content");
}
result.chat_non_stream = "ok";

// Responses non-stream
const response = await client.responses.create({
  model,
  input: "js sdk smoke",
});
if (typeof response.output_text !== "string" || response.output_text.length === 0) {
  fail("responses non-stream produced empty output_text");
}
if (response.object !== "response" || response.status !== "completed") {
  fail("responses non-stream had unexpected object/status");
}
result.responses_non_stream = "ok";

// Responses unsupported tools -> explicit 400
try {
  await client.responses.create({
    model,
    input: "js sdk smoke",
    tools: [{ type: "function", name: "x", parameters: {} }],
  });
  fail("responses tools request unexpectedly succeeded");
} catch (err) {
  if (err?.status !== 400) {
    fail(`responses tools returned unexpected status ${err?.status}`);
  }
  result.responses_tools_400 = "ok";
}

if (withStreaming) {
  // Chat stream
  const chatStream = await client.chat.completions.create({
    model,
    messages: [{ role: "user", content: "js sdk smoke" }],
    stream: true,
  });
  let chatText = "";
  for await (const chunk of chatStream) {
    chatText += chunk.choices?.[0]?.delta?.content || "";
  }
  if (!chatText) {
    fail("chat stream produced no content");
  }
  result.chat_stream = "ok";

  // Responses stream
  const responseStream = await client.responses.create({
    model,
    input: "js sdk smoke",
    stream: true,
  });
  let respText = "";
  let completed = false;
  for await (const event of responseStream) {
    if (event.type === "response.output_text.delta") {
      respText += event.delta;
    }
    if (event.type === "response.completed") {
      if (event.response?.status !== "completed") {
        fail("responses stream completed event had non-completed status");
      }
      completed = true;
    }
  }
  if (!respText) {
    fail("responses stream produced no text");
  }
  if (!completed) {
    fail("responses stream did not emit response.completed");
  }
  result.responses_stream = "ok";
}

process.stdout.write(`[js-sdk-smoke] ${JSON.stringify(result)}\n`);
