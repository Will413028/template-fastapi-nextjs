// Step 1 (c) and (f): frontend/scripts/gen-api.mjs's transform, run on a spec given as an argument; prints the
// generated TypeScript. Runs from frontend/ so openapi-typescript resolves from its node_modules:
//   cd frontend && node --input-type=module -e "$(cat ../docs/plans/2026-11-08-fastapi-code-first/spike/gen_types.mjs)" <spec.json>
import { pathToFileURL } from "node:url";
import openapiTS, { astToString, tsLiteral, tsUnion } from "openapi-typescript";

const spec = pathToFileURL(process.argv[1]);
const ast = await openapiTS(spec, {
  transform(schema) {
    const values = schema["x-extensible-enum"];
    if (!Array.isArray(values)) return undefined;
    return tsUnion(values.map((v) => tsLiteral(v)));
  },
});
process.stdout.write(astToString(ast));
