import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { research, researchKeys, type ResearchPending } from "./api";

export function researchPendingHref(item: ResearchPending) {
  const params = new URLSearchParams({
    from: "pending",
    researchTask: item.id,
  });
  if (item.kind === "identity" && item.entity_id) {
    params.set("entity", item.entity_id);
    if (item.target_id) params.set("compare", item.target_id);
  } else if (item.card_id) params.set("card", item.card_id);
  else if (item.entity_id) params.set("entity", item.entity_id);
  else if (item.book_id)
    return `/books/${encodeURIComponent(item.book_id)}?tab=research`;
  return `/research?${params}`;
}

export function useResearchPendingNavigation() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const query = useQueryClient();
  const [notice, setNotice] = useState("");
  async function advance(resolved: { cardId?: string; entityId?: string }) {
    void query.invalidateQueries({ queryKey: researchKeys.pending });
    if (params.get("from") !== "pending") return;
    try {
      const pending = await research.pending();
      const next = pending.items.find(
        (item) =>
          !item.superseded &&
          item.id !== params.get("researchTask") &&
          (!resolved.cardId || item.card_id !== resolved.cardId) &&
          (!resolved.entityId || item.entity_id !== resolved.entityId),
      );
      navigate(next ? researchPendingHref(next) : "/reviews");
    } catch {
      setNotice(
        "决定已保存，下一项待办暂时无法读取。可返回待办继续；当前决定无需重复提交。",
      );
    }
  }
  return { advance, notice };
}
