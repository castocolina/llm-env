const B = "http://127.0.0.1:20128";
let r = await fetch(B + "/api/keys", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "spike" }) });
const kj = await r.json(); const key = kj.key || kj.apiKey || kj.data?.key;
console.log("key create", r.status, key ? "ok" : JSON.stringify(kj).slice(0, 200));
for (const model of ["spike-custom", "spike-prio"]) {
  r = await fetch(B + "/v1/chat/completions", { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer " + key }, body: JSON.stringify({ model, messages: [{ role: "user", content: "hi" }] }) });
  console.log(model, r.status, (await r.text()).slice(0, 250));
}
