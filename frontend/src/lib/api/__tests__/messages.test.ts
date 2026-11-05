import { describe, expect, it } from "vitest";
import en from "../../../../messages/en.json";
import zhTW from "../../../../messages/zh-TW.json";
import type { FieldErrorCode, ProblemCode, ProblemTypeSlug } from "../errors";

// Compile-time half (pnpm lint): every problem type, code and field error code
// the contract declares has a message. A value added to openapi.yaml without a
// translation fails tsc here instead of showing a vaguer message.
type Covers<Key extends string> = Record<Key, string>;
type Problems = {
  types: Covers<ProblemTypeSlug>;
  codes: Covers<ProblemCode>;
  fields: Covers<FieldErrorCode>;
};
// Covers is only as strict as the keys: if gen-api.mjs stopped turning
// x-extensible-enum into unions, they would widen to string (or, for the
// slugs, never) and every messages file would pass. Fail tsc instead.
type ListsValues<Key> = [Key] extends [never]
  ? false
  : string extends Key
    ? false
    : true;
const contractListsValues: [
  ListsValues<ProblemTypeSlug>,
  ListsValues<ProblemCode>,
  ListsValues<FieldErrorCode>,
] = [true, true, true];
const enProblems: Problems = en.problems;
const zhTWProblems: Problems = zhTW.problems;

describe("problem messages", () => {
  it("translates the contract's values in every locale", () => {
    expect(contractListsValues).toEqual([true, true, true]);
    expect(Object.keys(zhTWProblems.types).sort()).toEqual(
      Object.keys(enProblems.types).sort(),
    );
    expect(Object.keys(zhTWProblems.codes).sort()).toEqual(
      Object.keys(enProblems.codes).sort(),
    );
    expect(Object.keys(zhTWProblems.fields).sort()).toEqual(
      Object.keys(enProblems.fields).sort(),
    );
  });
});
