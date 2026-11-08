import type { components } from "@/client/schema";
import type { ResearchCard } from "./api";

export function isFrozenTarget(
  relation: components["schemas"]["CrossCardRelation"],
  target: ResearchCard | undefined,
) {
  return Boolean(
    target &&
    target.id === relation.target_card_id &&
    target.version === relation.target_card_version &&
    target.publication_sha256 === relation.target_publication_sha256,
  );
}
