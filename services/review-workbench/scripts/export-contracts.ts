import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const environment = resolve(root, process.env.UV_PROJECT_ENVIRONMENT || "services/research-platform/.venv");
const python = resolve(environment, process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
execFileSync(python, ["-m", "hrs_platform.export_openapi"], {
  cwd: root,
  stdio: "inherit",
});
execFileSync(
  process.execPath,
  [
    resolve(
      root,
      "services/review-workbench/tooling/openapi/node_modules/openapi-typescript/bin/cli.js",
    ),
    resolve(root, "services/research-platform/openapi.json"),
    "-o",
    resolve(root, "services/review-workbench/src/client/schema.d.ts"),
  ],
  { cwd: root, stdio: "inherit" },
);
