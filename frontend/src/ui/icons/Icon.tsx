// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The product icon component. Renders vendored Lucide artwork (lucide.ts)
 * inside the standard 24x24 stroke wrapper. stroke=currentColor: icons take
 * the surrounding text color, so semantic color slots theme them for free.
 * Decorative by default (aria-hidden) — interactive parents carry the label.
 */

import { ICON_BODIES, type IconName } from "./lucide";

export type { IconName };

interface IconProps {
  name: IconName;
  /** Rendered square size in px (default 16 — toolbar/table density). */
  size?: number;
  /** Stroke width override; Lucide default is 2. */
  strokeWidth?: number;
  className?: string;
  title?: string;
}

export function Icon({ name, size = 16, strokeWidth = 2, className, title }: IconProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title ? <title>{title}</title> : null}
      {/* Static vendored artwork only — never user data (tooltip-security rule). */}
      <g dangerouslySetInnerHTML={{ __html: ICON_BODIES[name] }} />
    </svg>
  );
}
