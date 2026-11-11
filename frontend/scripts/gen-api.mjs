// Generates src/lib/api/schema.d.ts from the contract the backend generates from
// its routes (backend/api/openapi.json, make -C backend gen) with
// openapi-typescript, which leaves x-extensible-enum lists out: their values
// become string literal unions here, so messages.test.ts can check that every
// listed problem type and code has a message. The union names the values this
// client knows; errors.ts falls back for others. Every list in the contract must
// become a union: one openapi-typescript skipped (a new place for a list, a
// change in how it walks schemas) would widen to string and drop out of that
// check silently, so the script fails instead.
//
//   node scripts/gen-api.mjs          write the file
//   node scripts/gen-api.mjs --check  fail when the file differs
import { readFile, writeFile } from "node:fs/promises";
import openapiTS, {
  astToString,
  COMMENT_HEADER,
  tsLiteral,
  tsUnion,
} from "openapi-typescript";

const contract = new URL("../../backend/api/openapi.json", import.meta.url);
const target = new URL("../src/lib/api/schema.d.ts", import.meta.url);

// Every x-extensible-enum list in the document, once for each schema that has
// it.
function lists(node, found = []) {
  if (Array.isArray(node)) {
    for (const item of node) lists(item, found);
  } else if (node !== null && typeof node === "object") {
    const values = node["x-extensible-enum"];
    if (Array.isArray(values)) found.push(values);
    for (const value of Object.values(node)) lists(value, found);
  }
  return found;
}

const document = JSON.parse(await readFile(contract, "utf8"));
const ast = await openapiTS(document, {
  transform(schema) {
    const values = schema["x-extensible-enum"];
    if (!Array.isArray(values)) return undefined;
    return tsUnion(values.map((v) => tsLiteral(v)));
  },
});
const output = COMMENT_HEADER + astToString(ast);

// Each list must appear in the output as many times as in the document, as the
// union the transform writes ("a" | "b"; one value is just "a").
const wanted = new Map();
for (const values of lists(document)) {
  const union = values.map((v) => JSON.stringify(v)).join(" | ");
  wanted.set(union, (wanted.get(union) ?? 0) + 1);
}
const missed = [...wanted].filter(
  ([union, count]) => output.split(`: ${union};`).length - 1 < count,
);
if (missed.length > 0) {
  const names = missed.map(([union]) => union.slice(0, 60)).join("; ");
  console.error(`x-extensible-enum lists that did not become unions: ${names}`);
  process.exit(1);
}

if (process.argv.includes("--check")) {
  const current = await readFile(target, "utf8").catch(() => "");
  if (current !== output) {
    console.error("src/lib/api/schema.d.ts is stale: run pnpm gen:api");
    process.exit(1);
  }
} else {
  await writeFile(target, output);
}
