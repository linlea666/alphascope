/** Fire-and-forget confirmation shown by <Toaster /> (mounted once in the layout). */
export type ToastTone = "ok" | "error";

export function toast(message: string, tone: ToastTone = "ok") {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("as-toast", { detail: { message, tone } }));
}
