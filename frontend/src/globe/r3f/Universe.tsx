// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Universe: the scene root — the R3F Canvas, camera rig, and lighting. Children are
 * bodies (and, later, inter-body relay paths and the selection overlay). The scene
 * is a declarative function of data; R3F reconciles the underlying three.js objects
 * and their GPU lifecycle. The floating-origin camera rebase is added here when a
 * second body lands; Earth + LEO needs no rebase today.
 */

import type { ReactNode } from "react";
import { Canvas } from "@react-three/fiber";

export function Universe({ children }: { children?: ReactNode }) {
  return (
    <Canvas camera={{ position: [0, 0, 3], fov: 50, near: 0.01, far: 1000 }} dpr={[1, 2]}>
      <ambientLight intensity={0.45} />
      <directionalLight position={[5, 3, 5]} intensity={1.0} />
      {children}
    </Canvas>
  );
}
