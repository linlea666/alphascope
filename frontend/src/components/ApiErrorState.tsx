import { getTranslations } from "next-intl/server";
import { Panel } from "./ui";

/** status 0 = the API could not be reached; 422 = the query was rejected; anything else = the server failed. */
export async function ApiErrorState({ status, detail }: { status?: number; detail?: string }) {
  const t = await getTranslations("common");
  const [title, hint] =
    status === undefined || status === 0
      ? [t("apiError"), t("apiErrorHint")]
      : status === 422
        ? [t("apiBadQuery"), t("apiBadQueryHint")]
        : [t("apiServerError"), t("apiServerErrorHint")];
  return (
    <Panel>
      <div className="py-10 text-center">
        <div className="text-base font-semibold text-down">{title}</div>
        <p className="mt-2 text-sm text-muted">{hint}</p>
        {detail && <p className="num mt-3 text-xs text-muted">{detail}</p>}
        {status !== 422 && (
          <a href="" className="mt-4 inline-block rounded border border-border px-3 py-1 text-sm text-muted hover:text-text">
            {t("retry")}
          </a>
        )}
      </div>
    </Panel>
  );
}
