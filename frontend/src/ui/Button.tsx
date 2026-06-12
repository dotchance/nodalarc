// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Button primitives. `Button` for labeled actions, `IconButton` for compact
 * icon-only actions (window chrome, row actions). Both carry focus-visible
 * rings and the matte console treatment; variants map to semantic slots,
 * never ad-hoc colors.
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, type IconName } from "./icons/Icon";

type ButtonVariant = "default" | "primary" | "danger" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** Optional leading icon. */
  icon?: IconName;
  active?: boolean;
  children?: ReactNode;
}

export function Button({ variant = "default", icon, active, className, children, ...rest }: ButtonProps) {
  const cls = [
    "ui-btn",
    `ui-btn--${variant}`,
    active ? "ui-btn--on" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} {...rest}>
      {icon && <Icon name={icon} size={14} />}
      {children}
    </button>
  );
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName;
  /** Accessible name — required: icon-only controls must self-describe. */
  label: string;
  size?: number;
  active?: boolean;
}

export function IconButton({ icon, label, size = 14, active, className, ...rest }: IconButtonProps) {
  const cls = ["ui-iconbtn", active ? "ui-iconbtn--on" : "", className ?? ""].filter(Boolean).join(" ");
  return (
    <button className={cls} aria-label={label} title={label} {...rest}>
      <Icon name={icon} size={size} />
    </button>
  );
}
