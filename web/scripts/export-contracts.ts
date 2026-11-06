import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
execFileSync("uv", ["run", "--no-sync", "--locked", "python", "-m", "hrs", "contracts"], {
  cwd: root,
  stdio: "inherit",
});
execFileSync(
  process.execPath,
  [
    resolve(
      root,
      "web/tooling/openapi/node_modules/openapi-typescript/bin/cli.js",
    ),
    resolve(root, "web/openapi.json"),
    "-o",
    resolve(root, "web/src/client/schema.d.ts"),
  ],
  { cwd: root, stdio: "inherit" },
);
