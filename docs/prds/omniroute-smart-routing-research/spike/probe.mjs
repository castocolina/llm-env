const B = "http://127.0.0.1:20128";
const j = async (p, init) => { const r = await fetch(B + p, { headers: { "content-type": "application/json" }, ...init }); return [r.status, (await r.text()).slice(0, 300)]; };
const mk = (name, strategy) => j("/api/combos", { method: "POST", body: JSON.stringify({ name, strategy, models: ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5", "groq/llama-3.1-8b-instant"] }) });
console.log("create custom  ", await mk("spike-custom", "spike-reverse"));
console.log("create bogus   ", await mk("spike-bogus", "not-a-strategy"));
console.log("create builtin ", await mk("spike-prio", "priority"));
console.log("chat via custom", await j("/v1/chat/completions", { method: "POST", body: JSON.stringify({ model: "spike-custom", messages: [{ role: "user", content: "hi" }] }) }));
