// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { afterEach, describe, expect, it } from "vitest";
import * as THREE from "three";
import { TrailBatch } from "../Trails";
import { clearPositions, setBodyFrame, setNodeLocalPosition } from "../positions";

describe("TrailBatch", () => {
  afterEach(() => {
    clearPositions();
    setBodyFrame("earth", null);
  });

  it("clears satellite slot ownership and GPU objects on session reset", () => {
    const parent = new THREE.Group();
    setBodyFrame("earth", parent);
    setNodeLocalPosition("sat-a", "earth", 1, 0, 0);
    setNodeLocalPosition("sat-b", "earth", 2, 0, 0);

    const batch = new TrailBatch();
    batch.update(parent, ["sat-a", "sat-b"]);

    expect(batch.satelliteSlotCount()).toBe(2);
    expect(parent.children).toHaveLength(1);

    batch.resetSession();

    expect(batch.satelliteSlotCount()).toBe(0);
    expect(parent.children).toHaveLength(0);

    setNodeLocalPosition("sat-c", "earth", 3, 0, 0);
    batch.update(parent, ["sat-c"]);

    expect(batch.satelliteSlotCount()).toBe(1);
    expect(parent.children).toHaveLength(1);
    batch.dispose();
  });
});
