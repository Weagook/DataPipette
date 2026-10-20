import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const schemaPath = resolve(packageRoot, "../../docs/openapi.json");
const committedPath = resolve(packageRoot, "src/generated/schema.ts");
const check = process.argv.includes("--check");
const temporaryDirectory = check ? mkdtempSync(join(tmpdir(), "datapipette-client-")) : null;
const outputPath = temporaryDirectory ? join(temporaryDirectory, "schema.ts") : committedPath;
const cliPath = resolve(packageRoot, "node_modules/openapi-typescript/bin/cli.js");

try {
  const generated = spawnSync(
    process.execPath,
    [cliPath, schemaPath, "--output", outputPath, "--alphabetize"],
    { cwd: packageRoot, encoding: "utf8" },
  );
  if (generated.status !== 0) {
    process.stderr.write(generated.stderr || generated.stdout);
    process.exit(generated.status ?? 1);
  }
  if (check && readFileSync(outputPath, "utf8") !== readFileSync(committedPath, "utf8")) {
    process.stderr.write(
      "Generated TypeScript schema is stale. Run `npm run generate --prefix clients/typescript`.\n",
    );
    process.exit(1);
  }
} finally {
  if (temporaryDirectory) {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
}
