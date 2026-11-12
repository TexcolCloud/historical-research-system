import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import OriginalPage from "@/features/library/OriginalPage";

type SourceEvidence = {
  id: string;
  pages: number[];
  reading_target?: { run_id: string; physical_page: number } | null;
};
export default function EvidenceOriginal({
  evidence,
  selected,
  offset = 0,
}: {
  evidence: SourceEvidence[];
  selected?: string;
  offset?: number;
}) {
  // Proof IDs are card-local; the choice also binds the exact retained source.
  const located = evidence
    .map((item, index) => ({ ...item, ordinal: index + offset + 1 }))
    .filter((item) => item.reading_target && item.pages.length)
    .map((item, index) => ({
      ...item,
      sourceKey: JSON.stringify([index, item.id, item.reading_target]),
    }));
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  useEffect(() => {
    if (selected) {
      setChoice(located.find((item) => item.id === selected)?.sourceKey || "");
      setOpen(true);
    }
  }, [selected, evidence]);
  const current =
    located.find((item) => item.sourceKey === choice) || located[0];
  const [pageChoice, setPageChoice] = useState<{
    source: string;
    page: number;
  }>();
  const page =
    pageChoice?.source === current?.sourceKey
      ? pageChoice?.page
      : current?.reading_target?.physical_page;
  return (
    <section className="research-original-comparison" aria-label="卡片原件对照">
      <div className="research-evidence-heading">
        <h3>原件对照</h3>
        <Button
          variant="outline"
          aria-expanded={open}
          disabled={!current}
          onClick={() => setOpen(!open)}
        >
          {open ? "收起原件" : "对照原件"}
        </Button>
      </div>
      {!current ? (
        <p>尚无可靠原件位置，请保留卡片并核对来源。</p>
      ) : (
        open && (
          <>
            <div className="research-original-controls">
              <label>
                证据{" "}
                <select
                  aria-label="原件证据"
                  value={current.sourceKey}
                  onChange={(event) => setChoice(event.target.value)}
                >
                  {located.map((item) => (
                    <option value={item.sourceKey} key={item.sourceKey}>
                      证据 {item.ordinal}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                PDF 页{" "}
                <select
                  aria-label="原件页码"
                  value={page}
                  onChange={(event) =>
                    setPageChoice({
                      source: current.sourceKey,
                      page: Number(event.target.value),
                    })
                  }
                >
                  {current.pages.map((value) => (
                    <option value={value} key={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <OriginalPage
              viewId={current.sourceKey}
              runId={current.reading_target!.run_id}
              page={page ?? current.reading_target!.physical_page}
            />
            <p className="research-provenance">
              整页原图；具体引文请与下方证据对照。
            </p>
          </>
        )
      )}
    </section>
  );
}
