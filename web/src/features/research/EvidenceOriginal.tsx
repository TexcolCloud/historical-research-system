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
}: {
  evidence: SourceEvidence[];
  selected?: string;
}) {
  const located = evidence.filter(
    (item) => item.reading_target && item.pages.length,
  );
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  useEffect(() => {
    if (selected) {
      setChoice(selected);
      setOpen(true);
    }
  }, [selected]);
  const current = located.find((item) => item.id === choice) || located[0];
  const [page, setPage] = useState<number>();
  useEffect(() => setPage(undefined), [current?.id]);
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
                  value={current.id}
                  onChange={(event) => setChoice(event.target.value)}
                >
                  {located.map((item, index) => (
                    <option value={item.id} key={item.id}>
                      证据 {index + 1}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                PDF 页{" "}
                <select
                  aria-label="原件页码"
                  value={page ?? current.reading_target!.physical_page}
                  onChange={(event) => setPage(Number(event.target.value))}
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
