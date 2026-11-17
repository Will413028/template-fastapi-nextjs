// @vitest-environment node
// Every rule in .dependency-cruiser.cjs fails when it is broken. A rule whose path pattern matches nothing stays green
// forever, so each case builds a small src/ tree that breaks exactly one rule and asserts that rule reports it.
// A new rule without a case here fails "every rule has a case".
import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import {
  cruise,
  type IConfiguration,
  type IReporterOutput,
} from "dependency-cruiser";
import { afterEach, describe, expect, it } from "vitest";

const config = createRequire(import.meta.url)(
  "../../.dependency-cruiser.cjs",
) as IConfiguration;

type Tree = Record<string, string>;

// Files every case starts from: a feature with its public index and an internal file, and a route.
const base: Tree = {
  "src/features/f/index.ts": "export const f = 1;\n",
  "src/features/f/internal.ts": "export const internal = 1;\n",
  "src/app/page.ts":
    'import { f } from "../features/f/index";\nexport const page = f;\n',
  "src/lib/util.ts": "export const util = 1;\n",
};

const cases: Record<string, Tree> = {
  "no-circular": {
    "src/lib/a.ts": 'import { b } from "./b";\nexport const a = b;\n',
    "src/lib/b.ts": 'import { a } from "./a";\nexport const b = a;\n',
  },
  "shared-not-to-features-or-app": {
    "src/lib/util.ts":
      'import { f } from "../features/f/index";\nexport const util = f;\n',
  },
  "features-not-to-app": {
    "src/features/f/internal.ts":
      'import { page } from "../../app/page";\nexport const internal = page;\n',
  },
  "feature-public-api-from-outside": {
    "src/app/page.ts":
      'import { internal } from "../features/f/internal";\nexport const page = internal;\n',
  },
  "feature-public-api-between-features": {
    "src/features/g/index.ts":
      'import { internal } from "../f/internal";\nexport const g = internal;\n',
  },
};

const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0))
    rmSync(dir, { recursive: true, force: true });
});

async function violations(tree: Tree): Promise<string[]> {
  // realpath: on macOS the temp dir is a symlink (/var -> /private/var), and resolved paths would leave baseDir.
  const dir = mkdtempSync(join(realpathSync(tmpdir()), "depcruise-"));
  dirs.push(dir);
  for (const [path, body] of Object.entries(tree)) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), body);
  }
  const { tsConfig: _tsConfig, ...options } = config.options ?? {};
  const result: IReporterOutput = await cruise(["src"], {
    ...options,
    baseDir: dir,
    validate: true,
    ruleSet: { forbidden: config.forbidden },
  });
  if (typeof result.output === "string") throw new Error(result.output);
  return result.output.summary.violations.map((v) => v.rule.name);
}

describe("dependency rules", () => {
  it("every rule has a case", () => {
    expect(new Set((config.forbidden ?? []).map((rule) => rule.name))).toEqual(
      new Set(Object.keys(cases)),
    );
  });

  it("the base tree breaks no rule", async () => {
    expect(await violations(base)).toEqual([]);
  });

  it.each(Object.keys(cases))("%s fails on its violation", async (rule) => {
    expect(await violations({ ...base, ...cases[rule] })).toContain(rule);
  });
});
