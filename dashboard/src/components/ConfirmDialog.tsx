// Confirm dialog for destructive actions (cancel scan, sign out, etc.).
import { memo, useEffect, useId, useRef, useState } from "react";

interface Props {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export const ConfirmDialog = memo(function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onCancel,
}: Props) {
  const [confirming, setConfirming] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const onCancelRef = useRef(onCancel);
  const confirmingRef = useRef(confirming);
  const titleId = useId();
  const descriptionId = useId();

  onCancelRef.current = onCancel;
  confirmingRef.current = confirming;

  useEffect(() => {
    if (!open) {
      setConfirming(false);
      return;
    }
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !confirmingRef.current) {
        e.preventDefault();
        onCancelRef.current();
        return;
      }
      if (e.key !== "Tab") return;

      const focusable = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
      if (!focusable.length) {
        e.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeElement = document.activeElement;
      const focusIsOutside = !dialogRef.current?.contains(activeElement);
      if (
        e.shiftKey &&
        (activeElement === first || activeElement === dialogRef.current || focusIsOutside)
      ) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (activeElement === last || focusIsOutside)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      const previousFocus = previousFocusRef.current;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [open]);

  if (!open) return null;

  const handleConfirm = () => {
    setConfirming(true);
    onConfirm();
  };

  return (
    <div
      className="confirm-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && !confirming) onCancel();
      }}
    >
      <div
        ref={dialogRef}
        className="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        aria-busy={confirming}
        tabIndex={-1}
      >
        <h3 id={titleId}>{title}</h3>
        <p id={descriptionId}>{message}</p>
        <div className="confirm-actions">
          <button
            ref={cancelRef}
            className="button secondary"
            onClick={onCancel}
            disabled={confirming}
          >
            Cancel
          </button>
          <button
            className={danger ? "button danger" : "button"}
            onClick={handleConfirm}
            disabled={confirming}
          >
            {confirming ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
});
