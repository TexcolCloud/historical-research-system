export const eventNatureLabels = {
  reported: "材料报告发生",
  planned: "计划或拟议事项",
  conditional: "附带条件的事项",
  negated: "原文否定发生",
  unknown: "发生性质尚未核对",
};

export function EventParticipants({
  value,
}: {
  value: Pick<ResearchTimelineItem, "participants" | "participant_count">;
}) {
  const participants = value.participants ?? [];
  if (!participants.length) return null;
  const label = (participant: (typeof participants)[number]) =>
    `${participant.name}（${relationLabel(participant.role)}）`;
  return (
    <div className="research-event-participants">
      <p>
        相关对象：
        {participants.slice(0, 3).map((participant, index) => (
          <span key={`${participant.entity_id}:${index}`}>
            {label(participant)}{" "}
          </span>
        ))}
      </p>
      {participants.length > 3 && (
        <details>
          <summary>展开其余相关对象</summary>
          <ul>
            {participants.slice(3).map((participant, index) => (
              <li key={`${participant.entity_id}:${index}`}>
                {label(participant)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {(value.participant_count ?? 0) > participants.length && (
        <p>
          共 {value.participant_count} 个相关对象，完整记载请查阅出处史料卡。
        </p>
      )}
    </div>
  );
}

export default function EventNature({
  value,
}: {
  value: { event_status?: string; status_note?: string };
}) {
  const nature = value.event_status as
    keyof typeof eventNatureLabels | undefined;
  return (
    <div className="research-event-nature">
      <span
        className="research-status"
        data-state={nature === "reported" ? "reported" : "pending"}
      >
        {(nature && eventNatureLabels[nature]) || eventNatureLabels.unknown}
      </span>
      {value.status_note && (
        <p className="research-provenance">{value.status_note}</p>
      )}
    </div>
  );
}
import { relationLabel } from "./relation-labels";
import type { ResearchTimelineItem } from "./api";
