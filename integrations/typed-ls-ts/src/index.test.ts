import { strict as assert } from "node:assert";
import { test } from "node:test";

import { add } from "./index.js";

test("add returns the sum of two numbers", () => {
  assert.equal(add(2, 3), 5);
  assert.equal(add(-4, 4), 0);
  assert.equal(add(0.5, 0.25), 0.75);
});
