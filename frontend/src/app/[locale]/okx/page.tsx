import { getTranslations } from "next-intl/server";
import { OkxBoard } from "@/components/OkxBoard";
import { PageHeader } from "@/components/ui";

export default async function OkxPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const t = await getTranslations("okxBoard");
  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <OkxBoard sp={await searchParams} basePath="/okx" />
    </div>
  );
}
