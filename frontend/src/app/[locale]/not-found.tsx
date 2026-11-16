import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/routing";

export default async function NotFound() {
  const t = await getTranslations("common");
  return (
    <div className="py-24 text-center">
      <h1 className="text-lg font-semibold">{t("notFound")}</h1>
      <Link href="/" className="mt-4 inline-block text-sm text-accent hover:underline">
        {t("backHome")}
      </Link>
    </div>
  );
}
