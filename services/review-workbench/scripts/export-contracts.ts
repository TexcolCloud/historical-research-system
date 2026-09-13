import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const python = resolve(
  root,
  process.platform === "win32"
    ? ".cache/engineering-envs/research-platform/Scripts/python.exe"
    : ".cache/engineering-envs/research-platform/bin/python",
);
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
    resolve(root, "services/review-workbench/src/platform/schema.d.ts"),
  ],
  { cwd: root, stdio: "inherit" },
);
