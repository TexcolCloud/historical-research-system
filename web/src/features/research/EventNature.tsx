export const eventNatureLabels = {
  reported: "材料报告发生",
  planned: "计划或拟议事项",
  conditional: "附带条件的事项",
  negated: "原文否定发生",
  unknown: "发生性质尚未核对",
};

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
