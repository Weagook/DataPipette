import { readFileSync, readdirSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const assets = join(dist, "assets");
const html = readFileSync(join(dist, "index.html"), "utf8");
const files = readdirSync(assets);

const budgets = [
  {
    label: "application entry JavaScript",
    files: matchHtmlAsset(/<script[^>]+src="\/assets\/([^"]+\.js)"/, html),
    maxKiB: 86,
  },
  {
    label: "shared application CSS",
    files: matchHtmlAssets(/<link[^>]+href="\/assets\/([^"]+\.css)"/g, html),
    maxKiB: 20,
  },
  {
    label: "largest ordinary lazy route",
    files: files.filter((name) => /Page-[^.]+\.js$/.test(name)),
    maxKiB: 12,
    largestOnly: true,
  },
  {
    label: "shared query runtime",
    files: files.filter((name) => /^query-[^.]+\.js$/.test(name)),
    maxKiB: 32,
  },
  {
    label: "isolated canvas authoring runtime",
    files: files.filter((name) => /^(canvas-runtime|DatasetCanvasBuilder)-[^.]+\.js$/.test(name)),
    maxKiB: 70,
  },
  {
    label: "isolated visualization runtime",
    files: files.filter((name) => /^ChartView-[^.]+\.js$/.test(name)),
    maxKiB: 200,
  },
];

let failed = false;
for (const budget of budgets) {
  if (budget.files.length === 0) {
    console.error(`Missing build artifact for ${budget.label}.`);
    failed = true;
    continue;
  }
  const measured = budget.files.map((name) => ({ name, bytes: gzipSize(join(assets, name)) }));
  const selected = budget.largestOnly
    ? [measured.sort((left, right) => right.bytes - left.bytes)[0]]
    : measured;
  const bytes = selected.reduce((total, asset) => total + asset.bytes, 0);
  const actualKiB = bytes / 1024;
  const status = actualKiB <= budget.maxKiB ? "PASS" : "FAIL";
  console.log(`${status} ${budget.label}: ${actualKiB.toFixed(2)} KiB gzip / ${budget.maxKiB} KiB`);
  if (actualKiB > budget.maxKiB) failed = true;
}

if (failed) process.exitCode = 1;

function gzipSize(path) {
  return gzipSync(readFileSync(path), { level: 9 }).byteLength;
}

function matchHtmlAsset(pattern, source) {
  const match = source.match(pattern);
  return match?.[1] ? [match[1]] : [];
}

function matchHtmlAssets(pattern, source) {
  return [...source.matchAll(pattern)].flatMap((match) => match[1] ? [match[1]] : []);
}
