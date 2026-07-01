// kanpan 背景層：代 content script 打本機 api(繞過網頁對 loopback 的 Private Network Access 封鎖)
// 背景 service worker 有 host_permissions(127.0.0.1:8771),fetch 本機不受公開站 PNA 限制。
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === "kp-fetch") {
    fetch(msg.url)
      .then((r) => r.json())
      .then((d) => sendResponse({ ok: true, data: d }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // 非同步回應
  }
  if (msg.type === "kp-post") {
    fetch(msg.url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(msg.body),
    })
      .then((r) => r.json())
      .then((d) => sendResponse({ ok: true, data: d }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
});
