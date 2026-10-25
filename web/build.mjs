// Builds the hosted (server-less) demo into dist/: the same static frontend plus demo/*.py,
// which browser-engine.js runs in the browser with Pyodide. Used by Vercel (see vercel.json).
import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const demo = join(root, "demo");
const out = join(root, "dist");

const pyFiles = readdirSync(demo).filter((f) => f.endsWith(".py") && f !== "server.py" && !f.startsWith("test_"));
if (!pyFiles.includes("api.py")) throw new Error("demo/api.py is missing");

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "py"), { recursive: true });
cpSync(join(demo, "static"), join(out, "static"), { recursive: true });
cpSync(join(root, "web", "browser-engine.js"), join(out, "static", "browser-engine.js"));
for (const f of pyFiles) cpSync(join(demo, f), join(out, "py", f));
writeFileSync(join(out, "py", "files.json"), JSON.stringify(pyFiles));

const appTag = '<script src="/static/app.js"></script>';
const html = readFileSync(join(demo, "static", "index.html"), "utf8");
if (!html.includes(appTag)) throw new Error("demo/static/index.html no longer loads /static/app.js");
writeFileSync(join(out, "index.html"),
  html.replace(appTag, `<script src="/static/browser-engine.js"></script>\n  ${appTag}`));

console.log(`Built hosted demo in ${out} (${pyFiles.length} Python modules)`);
