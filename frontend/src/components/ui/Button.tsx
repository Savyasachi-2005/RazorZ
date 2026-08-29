import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const styles: Record<Variant, string> = {
  primary:
    "bg-accent text-white hover:bg-emerald-500 disabled:bg-accent/40 shadow-[0_0_0_1px_rgba(22,163,74,0.35)]",
  secondary:
    "bg-surface-raised text-ink border border-line hover:bg-surface-hover hover:border-line-strong",
  danger:
    "bg-danger-soft text-danger-text border border-danger/40 hover:bg-rose-950",
  ghost: "bg-transparent text-ink-muted hover:text-ink hover:bg-surface-hover",
};

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  loading?: boolean;
  children: ReactNode;
};

export function Button({
  variant = "primary",
  loading = false,
  className = "",
  disabled,
  children,
  ...rest
}: Props) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors duration-fast focus-ring disabled:cursor-not-allowed disabled:opacity-60 ${styles[variant]} ${className}`}
      {...rest}
    >
      {loading ? (
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current/30 border-t-current" />
      ) : null}
      {children}
    </button>
  );
}
