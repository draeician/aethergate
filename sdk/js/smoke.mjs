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
const visionModel = process.env.AETHERGATE_SDK_VISION_MODEL || "golden-vision-model";
const withStreaming = process.env.AETHERGATE_SDK_STREAMING === "1";

// Tiny deterministic 16x16 solid-red PNG (same fixture as the Python golden
// path). A real decodable image so it works against a live vision model too;
// never a real photo. Used only to prove inline vision input.
const TINY_PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAFklEQVR42mP4oKBAEmIY" +
  "1TCqYfhqAABuXjAQDOMSYwAAAABJRU5ErkJggg==";
const pngDataUrl = `data:image/png;base64,${TINY_PNG_B64}`;

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

// Chat vision non-stream (inline data URL, image-capable alias).
const chatVision = await client.chat.completions.create({
  model: visionModel,
  messages: [
    {
      role: "user",
      content: [
        { type: "text", text: "js sdk vision" },
        { type: "image_url", image_url: { url: pngDataUrl } },
      ],
    },
  ],
});
if (!chatVision.choices?.[0]?.message?.content) {
  fail("chat vision non-stream produced empty content");
}
result.chat_vision_non_stream = "ok";

// Responses vision non-stream.
const responseVision = await client.responses.create({
  model: visionModel,
  input: [
    {
      role: "user",
      content: [
        { type: "input_text", text: "js sdk vision" },
        { type: "input_image", image_url: pngDataUrl },
      ],
    },
  ],
});
if (typeof responseVision.output_text !== "string" || responseVision.output_text.length === 0) {
  fail("responses vision non-stream produced empty output_text");
}
result.responses_vision_non_stream = "ok";

// Remote image URL -> explicit SDK-visible 400 (never fetched).
try {
  await client.chat.completions.create({
    model: visionModel,
    messages: [
      {
        role: "user",
        content: [
          { type: "image_url", image_url: { url: "https://example.invalid/x.png" } },
        ],
      },
    ],
  });
  fail("remote image URL request unexpectedly succeeded");
} catch (err) {
  if (err?.status !== 400) {
    fail(`remote image URL returned unexpected status ${err?.status}`);
  }
  result.chat_vision_remote_url_400 = "ok";
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

  // Chat vision stream.
  const chatVisionStream = await client.chat.completions.create({
    model: visionModel,
    messages: [
      {
        role: "user",
        content: [
          { type: "text", text: "js sdk vision" },
          { type: "image_url", image_url: { url: pngDataUrl } },
        ],
      },
    ],
    stream: true,
  });
  let chatVisionText = "";
  for await (const chunk of chatVisionStream) {
    chatVisionText += chunk.choices?.[0]?.delta?.content || "";
  }
  if (!chatVisionText) {
    fail("chat vision stream produced no content");
  }
  result.chat_vision_stream = "ok";

  // Responses vision stream.
  const responseVisionStream = await client.responses.create({
    model: visionModel,
    input: [
      {
        role: "user",
        content: [
          { type: "input_text", text: "js sdk vision" },
          { type: "input_image", image_url: pngDataUrl },
        ],
      },
    ],
    stream: true,
  });
  let respVisionText = "";
  let respVisionCompleted = false;
  for await (const event of responseVisionStream) {
    if (event.type === "response.output_text.delta") {
      respVisionText += event.delta;
    }
    if (event.type === "response.completed") {
      respVisionCompleted = true;
    }
  }
  if (!respVisionText) {
    fail("responses vision stream produced no text");
  }
  if (!respVisionCompleted) {
    fail("responses vision stream did not emit response.completed");
  }
  result.responses_vision_stream = "ok";
}

process.stdout.write(`[js-sdk-smoke] ${JSON.stringify(result)}\n`);
