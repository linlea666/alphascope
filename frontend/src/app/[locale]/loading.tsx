export default function Loading() {
  return (
    <div className="space-y-4" aria-busy="true">
      <div className="h-7 w-48 animate-pulse rounded bg-panel-2" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 animate-pulse rounded-lg bg-panel" />
        ))}
      </div>
      <div className="h-96 animate-pulse rounded-lg bg-panel" />
    </div>
  );
}
