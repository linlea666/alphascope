import { getTranslations } from "next-intl/server";
import { Panel } from "./ui";

export async function ApiErrorState({ detail }: { detail?: string }) {
  const t = await getTranslations("common");
  return (
    <Panel>
      <div className="py-10 text-center">
        <div className="text-base font-semibold text-down">{t("apiError")}</div>
        <p className="mt-2 text-sm text-muted">{t("apiErrorHint")}</p>
        {detail && <p className="num mt-3 text-xs text-muted">{detail}</p>}
      </div>
    </Panel>
  );
}
