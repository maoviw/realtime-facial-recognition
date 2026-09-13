import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
if (args.length !== 0 && (args.length !== 2 || args[0] !== "--port" || !/^\d{2,5}$/.test(args[1]) || Number(args[1]) > 65535)) {
  throw new Error("Usage: npm run dev:video -- [--port 3100]");
}
const child = spawn(process.execPath, [
  path.join(frontend, "node_modules/next/dist/bin/next"), "dev",
  "--hostname", "127.0.0.1", "--port", args[1] ?? "3100",
], {
  cwd: frontend,
  stdio: "inherit",
  env: { ...process.env, HLS_PILOT_ROOT: path.resolve(frontend, "../backend/data/phase0-pilots") },
});
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("error", () => { process.exitCode = 1; });
child.on("exit", (code) => { process.exitCode = code ?? 1; });