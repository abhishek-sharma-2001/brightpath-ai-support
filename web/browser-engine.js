// Hosted build only: runs the demo's Python backend (demo/*.py) in the browser with Pyodide,
// so no server is needed and every visitor gets their own fresh demo state.
(() => {
  const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";

  const style = document.createElement("style");
  style.textContent = `
    #engine-loading { position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center;
      background: var(--bg, #f4f6fb); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
    #engine-loading .box { max-width: 420px; padding: 28px 32px; border-radius: 14px; background: #fff;
      box-shadow: 0 10px 40px rgba(29, 36, 51, 0.12); text-align: center; color: var(--ink, #1d2433); }
    #engine-loading p { margin: 8px 0 0; color: var(--muted, #6b7489); }
    #engine-loading code { background: var(--brand-soft, #ecebfd); padding: 1px 6px; border-radius: 4px; }
    #engine-loading .spinner { width: 34px; height: 34px; margin: 0 auto 14px; border-radius: 50%;
      border: 3px solid var(--line, #e3e7f0); border-top-color: var(--brand, #4b3fe0);
      animation: engine-spin 0.8s linear infinite; }
    @keyframes engine-spin { to { transform: rotate(360deg); } }`;
  document.head.appendChild(style);

  const overlay = document.createElement("div");
  overlay.id = "engine-loading";
  overlay.innerHTML = `<div class="box"><div class="spinner"></div><b>Starting the demo...</b>
    <p>The assistant's Python backend runs entirely in your browser, so the first load takes a few seconds.</p></div>`;
  document.body.appendChild(overlay);

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`Could not load ${src}`));
      document.head.appendChild(s);
    });
  }

  async function fetchText(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`Could not load ${path} (${res.status})`);
    return res.text();
  }

  const ready = (async () => {
    await loadScript(PYODIDE_URL + "pyodide.js");
    const pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
    const files = JSON.parse(await fetchText("/py/files.json"));
    const sources = await Promise.all(files.map((name) => fetchText(`/py/${name}`)));
    pyodide.FS.mkdirTree("/app");
    files.forEach((name, i) => pyodide.FS.writeFile(`/app/${name}`, sources[i]));
    pyodide.runPython("import sys; sys.path.insert(0, '/app')");
    return pyodide.pyimport("api").handle_browser;
  })();

  ready.then(
    () => overlay.remove(),
    (err) => {
      console.error(err);
      overlay.querySelector(".box").innerHTML = `<b>The demo could not start.</b><p>${err.message}</p>
        <p>Please refresh the page, or run it locally with <code>python demo/server.py</code>.</p>`;
    },
  );

  window.localApi = async (path, body) => {
    const handle = await ready;
    const method = body === undefined ? "GET" : "POST";
    const res = JSON.parse(handle(method, path, body === undefined ? "" : JSON.stringify(body)));
    if (res.status >= 400) throw new Error(res.body.error || `Request failed (${res.status})`);
    if (path === "/api/config") res.body.engine = "Rule-based, running in your browser";
    return res.body;
  };
})();
