import assert from "node:assert/strict";
import test from "node:test";
import {
  canvasTransform,
  mergeAnswers,
  placementKind,
} from "../electrical_design/web/app.js";

test("saving room edits preserves answers in other rooms", () => {
  const original = [
    {
      answer_item_id: "a",
      q_id: "lighting",
      space_id: "room-a",
      selected: "needed",
      quantity: 1,
    },
    {
      answer_item_id: "b",
      q_id: "lighting",
      space_id: "room-b",
      selected: "needed",
      quantity: 3,
    },
    {
      answer_item_id: "c",
      q_id: "pump",
      scope_type: "building",
      selected: "needed",
    },
  ];
  const drafts = new Map([
    [
      "room-a:lighting",
      {
        ...original[0],
        quantity: "2",
        input_W: "12",
        power_source: "nameplate",
      },
    ],
  ]);
  const result = mergeAnswers(original, drafts, new Set(["room-a:lighting"]));
  assert.deepEqual(
    result.find((a) => a.answer_item_id === "b"),
    original[1],
  );
  assert.deepEqual(
    result.find((a) => a.answer_item_id === "c"),
    original[2],
  );
  assert.equal(result.find((a) => a.answer_item_id === "a").quantity, 2);
  assert.equal(result.find((a) => a.answer_item_id === "a").input_W, 12);
});

test("outlet requirements never serialize socket capacity as consumption", () => {
  const drafts = new Map([
    [
      "r:general_receptacle",
      {
        answer_item_id: "x",
        q_id: "general_receptacle",
        space_id: "r",
        selected: "needed",
        quantity: "2",
        input_W: "3680",
        power_source: "socket rating",
      },
    ],
  ]);
  const [answer] = mergeAnswers([], drafts, new Set(drafts.keys()));
  assert.equal(answer.quantity, 2);
  assert.equal(answer.input_W, undefined);
  assert.equal(answer.power_source, undefined);
});

test("unneeded equipment drops its consumption fields", () => {
  const drafts = new Map([
    [
      "r:lighting",
      {
        answer_item_id: "x",
        q_id: "lighting",
        space_id: "r",
        selected: "not_needed",
        quantity: "1",
        input_W: 12,
      },
    ],
  ]);
  assert.equal(
    mergeAnswers([], drafts, new Set(drafts.keys()))[0].input_W,
    undefined,
  );
});

test("drawing maps absolute IFC coordinates with world Y pointing up", () => {
  const t = canvasTransform({ min: [100, 200, 0], max: [106, 204, 3] });
  assert.deepEqual(t.point(100, 204), [t.left, t.top]);
  assert.deepEqual(t.point(106, 200), [
    t.left + t.roomWidth,
    t.top + t.roomHeight,
  ]);
  assert.equal(canvasTransform({ min: [0, 0, 0], max: [0, 4, 3] }), null);
  assert.equal(canvasTransform(null), null);
});

test("device marker categories recognize writer aliases and IFC classes", () => {
  assert.equal(placementKind({ device_type: "light_fixture" }), "light");
  assert.equal(placementKind({ ifc_class: "IfcOutlet" }), "outlet");
  assert.equal(placementKind({ device_type: "switch" }), "switch");
  assert.equal(placementKind({ device_type: "appliance" }), "other");
});

test("advanced multi-answer requirements are preserved by the basic editor", () => {
  const original = [
    {
      answer_item_id: "a",
      q_id: "lighting",
      space_id: "r",
      selected: "needed",
      quantity: 1,
    },
    {
      answer_item_id: "b",
      q_id: "lighting",
      space_id: "r",
      selected: "needed",
      quantity: 2,
    },
  ];
  const drafts = new Map([["r:lighting", { ...original[0], multiple: true }]]);
  assert.deepEqual(
    mergeAnswers(original, drafts, new Set(["r:lighting"])),
    original,
  );
});
