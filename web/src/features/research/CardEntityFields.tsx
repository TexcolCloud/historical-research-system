import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { eventNatureLabels } from "./EventNature";
import type { components } from "@/client/schema";
import { useEffect, useState } from "react";
import {
  DateEditor,
  DeferredFields,
  References,
  SourceEditor,
  emptyDate,
  emptyValue,
  newId,
  type Draft,
} from "./CardFields";
type Mention = components["schemas"]["CardOnlyMention"];
type Event = NonNullable<Draft["events"]>[number];
type Relation = NonNullable<Draft["relations"]>[number];

function MentionLocations({
  value,
  evidence,
  onChange,
}: {
  value: Mention;
  evidence: Draft["evidence"];
  onChange: (change: Partial<Mention>) => void;
}) {
  const [literal, setLiteral] = useState("");
  const [matches, setMatches] = useState<
    Array<{ evidenceId: string; location: Mention["locations"][number] }>
  >([]);
  const [searched, setSearched] = useState(false);
  useEffect(() => {
    setMatches([]);
    setSearched(false);
  }, [value.name, evidence]);
  function locate() {
    const text = literal || value.name;
    const found: typeof matches = [];
    if (text)
      for (const span of evidence) {
        let at = span.quote.indexOf(text);
        // ponytail: show 25 exact matches; users narrow the literal for longer lists.
        while (at >= 0 && found.length < 25) {
          const start = span.start + Array.from(span.quote.slice(0, at)).length;
          const location = {
            article_id: span.article_id,
            start,
            end: start + Array.from(text).length,
            quote: text,
          };
          if (
            !found.some(
              (item) =>
                item.location.article_id === location.article_id &&
                item.location.start === start &&
                item.location.end === location.end,
            )
          )
            found.push({ evidenceId: span.id, location });
          at = span.quote.indexOf(text, at + 1);
        }
        if (found.length >= 25) break;
      }
    setMatches(found);
    setSearched(true);
  }
  return (
    <div className="research-edit-group">
      <p>原文称呼位置</p>
      {value.locations.map((location, index) => (
        <p key={`${location.article_id}:${location.start}:${location.end}`}>
          {location.quote} · 字符 {location.start}–{location.end}
          <Button
            type="button"
            variant="ghost"
            onClick={() =>
              onChange({
                locations: value.locations.filter((_, at) => at !== index),
              })
            }
          >
            移除此处位置
          </Button>
        </p>
      ))}
      <label>
        原文中的称呼
        <Input
          aria-label={`定位 ${value.name || "新实体"} 的原文称呼`}
          value={literal}
          placeholder={value.name || "输入原文中的完整称呼"}
          onChange={(event) => {
            setLiteral(event.target.value);
            setMatches([]);
            setSearched(false);
          }}
        />
      </label>
      <Button
        type="button"
        variant="outline"
        disabled={!(literal || value.name)}
        onClick={locate}
      >
        查找精确位置
      </Button>
      {searched && !matches.length && (
        <p>当前引文未找到这个称呼，请核对原文文字并补充对应引文。</p>
      )}
      {matches.map(({ evidenceId, location }) => (
        <Button
          key={`${location.article_id}:${location.start}:${location.end}`}
          type="button"
          variant="outline"
          disabled={value.locations.some(
            (old) =>
              old.article_id === location.article_id &&
              old.start === location.start &&
              old.end === location.end,
          )}
          onClick={() =>
            onChange({
              locations: [...value.locations, location],
              evidence_ids: [...new Set([...value.evidence_ids, evidenceId])],
            })
          }
        >
          使用位置 {location.start}–{location.end}：{location.quote}
        </Button>
      ))}
      {matches.length >= 25 && (
        <p>仅显示前 25 处，请补全更具体的称呼后重新查找。</p>
      )}
    </div>
  );
}

export function MentionFields({
  value,
  evidence,
  onChange,
  removeBlocked = [],
  cardOnly = false,
  articleId,
}: {
  value: Mention[];
  evidence: Draft["evidence"];
  onChange: (value: Mention[]) => void;
  removeBlocked?: string[];
  cardOnly?: boolean;
  articleId: string;
}) {
  function patch(index: number, change: Partial<Mention>) {
    onChange(
      value.map((item, at) => (at === index ? { ...item, ...change } : item)),
    );
  }
  return (
    <DeferredFields summary="实体记载" editorKey="mentions">
      {() => (
        <>
          {value.map((item, index) => (
            <DeferredFields
              key={item.id}
              summary={`实体 ${index + 1} · ${item.name || "新增实体"}`}
              initiallyOpen={!item.name}
              editorKey={`mention:${item.id}`}
            >
              {() => (
                <fieldset className="research-edit-group">
                  <legend>{item.name || `实体 ${index + 1}`}</legend>
                  <label>
                    名称
                    <Input
                      value={item.name}
                      required
                      onChange={(event) =>
                        patch(index, { name: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    类型
                    <select
                      value={item.kind}
                      onChange={(event) =>
                        patch(index, {
                          kind: event.target.value as Mention["kind"],
                        })
                      }
                    >
                      <option value="person">人物</option>
                      <option value="organization">机构</option>
                      <option value="place">地点</option>
                      {cardOnly && <option value="document">文献</option>}
                      <option value="event">事件提及</option>
                    </select>
                  </label>
                  <label>
                    身份依据
                    <textarea
                      rows={3}
                      required
                      value={item.identity_basis}
                      onChange={(event) =>
                        patch(index, { identity_basis: event.target.value })
                      }
                    />
                  </label>
                  <References
                    label="实体依据"
                    value={item.evidence_ids}
                    evidence={evidence}
                    onChange={(evidence_ids) => patch(index, { evidence_ids })}
                  />
                  {cardOnly && (
                    <MentionLocations
                      value={item}
                      evidence={evidence.filter(
                        (span) => span.article_id === articleId,
                      )}
                      onChange={(next) => patch(index, next)}
                    />
                  )}
                  {(item.aliases ?? []).map((alias, at) => (
                    <fieldset className="research-edit-group" key={at}>
                      <legend>来源别名 {at + 1}</legend>
                      <Input
                        aria-label={`实体 ${index + 1} 别名 ${at + 1}`}
                        value={alias.name}
                        required
                        onChange={(event) =>
                          patch(index, {
                            aliases: (item.aliases ?? []).map((old, a) =>
                              a === at
                                ? { ...old, name: event.target.value }
                                : old,
                            ),
                          })
                        }
                      />
                      <References
                        label="别名依据"
                        value={alias.evidence_ids}
                        evidence={evidence}
                        onChange={(evidence_ids) =>
                          patch(index, {
                            aliases: (item.aliases ?? []).map((old, a) =>
                              a === at ? { ...old, evidence_ids } : old,
                            ),
                          })
                        }
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() =>
                          patch(index, {
                            aliases: (item.aliases ?? []).filter(
                              (_, a) => a !== at,
                            ),
                          })
                        }
                      >
                        移除此来源别名
                      </Button>
                    </fieldset>
                  ))}
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      patch(index, {
                        aliases: [
                          ...(item.aliases ?? []),
                          { name: "", evidence_ids: [] },
                        ],
                      })
                    }
                  >
                    添加来源别名
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={removeBlocked.includes(item.id)}
                    onClick={() =>
                      onChange(value.filter((_, at) => at !== index))
                    }
                  >
                    移除实体 {index + 1}
                  </Button>
                  {removeBlocked.includes(item.id) && (
                    <p className="research-provenance">
                      需要先调整引用此实体的事件、关系或身份关联。
                    </p>
                  )}
                </fieldset>
              )}
            </DeferredFields>
          ))}
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              const id = newId("mention");
              onChange([
                ...value,
                {
                  id,
                  kind: "person",
                  name: "",
                  identity_basis: "",
                  evidence_ids: [],
                  identity_key: id,
                  aliases: [],
                  locations: [],
                },
              ]);
            }}
          >
            添加实体记载
          </Button>
        </>
      )}
    </DeferredFields>
  );
}

export function EventFields({
  value,
  mentions,
  evidence,
  onChange,
  removeBlocked = [],
}: {
  value: Event[];
  mentions: Mention[];
  evidence: Draft["evidence"];
  onChange: (value: Event[]) => void;
  removeBlocked?: string[];
}) {
  function patch(index: number, change: Partial<Event>) {
    onChange(
      value.map((item, at) => (at === index ? { ...item, ...change } : item)),
    );
  }
  function roles(index: number, next: NonNullable<Event["participant_roles"]>) {
    const item = value[index];
    const legacy = (item.participant_ids ?? []).filter(
      (id) =>
        !(item.participant_roles ?? []).some((role) => role.mention_id === id),
    );
    patch(index, {
      participant_roles: next,
      participant_ids: [
        ...new Set([...legacy, ...next.map((role) => role.mention_id)]),
      ],
    });
  }
  return (
    <DeferredFields summary="事件及参与者角色" editorKey="events">
      {() => (
        <>
          {value.map((item, index) => (
            <DeferredFields
              key={item.id}
              summary={`事件 ${index + 1} · ${item.name || "新增事件"}`}
              initiallyOpen={!item.name}
              editorKey={`event:${item.id}`}
            >
              {() => (
                <fieldset className="research-edit-group">
                  <legend>{item.name || `事件 ${index + 1}`}</legend>
                  <label>
                    事件名称
                    <Input
                      value={item.name}
                      required
                      onChange={(event) =>
                        patch(index, { name: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    具体行为
                    <Input
                      value={item.action}
                      onChange={(event) =>
                        patch(index, { action: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    事件描述
                    <textarea
                      rows={3}
                      required
                      value={item.description}
                      onChange={(event) =>
                        patch(index, { description: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    事件发生性质
                    <select
                      value={item.event_status ?? "unknown"}
                      onChange={(event) =>
                        patch(index, {
                          event_status: event.target
                            .value as Event["event_status"],
                        })
                      }
                    >
                      {Object.entries(eventNatureLabels).map(([key, label]) => (
                        <option value={key} key={key}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    发生性质依据
                    <textarea
                      required
                      rows={2}
                      value={item.status_note ?? ""}
                      onChange={(event) =>
                        patch(index, { status_note: event.target.value })
                      }
                    />
                  </label>
                  <p className="research-provenance">
                    材料报告、计划、条件与否定分别记录；“材料报告”不表示已得到独立互证。
                  </p>
                  <DateEditor
                    label="事项时间"
                    value={item.date ?? emptyDate()}
                    evidence={evidence}
                    onChange={(date) => patch(index, { date })}
                  />
                  <SourceEditor
                    label="发生地点"
                    value={item.place ?? emptyValue()}
                    evidence={evidence}
                    onChange={(place) => patch(index, { place })}
                  />
                  <References
                    label="事件依据"
                    value={item.evidence_ids}
                    evidence={evidence}
                    onChange={(evidence_ids) => patch(index, { evidence_ids })}
                  />
                  {(item.participant_roles ?? []).map((role, at) => (
                    <fieldset className="research-edit-group" key={at}>
                      <legend>参与者 {at + 1}</legend>
                      <label>
                        人物或机构
                        <select
                          value={role.mention_id}
                          onChange={(event) =>
                            roles(
                              index,
                              (item.participant_roles ?? []).map((old, r) =>
                                r === at
                                  ? { ...old, mention_id: event.target.value }
                                  : old,
                              ),
                            )
                          }
                        >
                          {mentions.map((mention) => (
                            <option key={mention.id} value={mention.id}>
                              {mention.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        角色
                        <Input
                          aria-label={`事件 ${index + 1} 参与者 ${at + 1} 角色`}
                          placeholder="例如：发函者、收函者、参与者"
                          value={role.role}
                          required
                          onChange={(event) =>
                            roles(
                              index,
                              (item.participant_roles ?? []).map((old, r) =>
                                r === at
                                  ? { ...old, role: event.target.value }
                                  : old,
                              ),
                            )
                          }
                        />
                      </label>
                      <label>
                        角色说明
                        <Input
                          value={role.description}
                          required
                          onChange={(event) =>
                            roles(
                              index,
                              (item.participant_roles ?? []).map((old, r) =>
                                r === at
                                  ? { ...old, description: event.target.value }
                                  : old,
                              ),
                            )
                          }
                        />
                      </label>
                      <References
                        label="参与依据"
                        value={role.evidence_ids}
                        evidence={evidence}
                        onChange={(evidence_ids) =>
                          roles(
                            index,
                            (item.participant_roles ?? []).map((old, r) =>
                              r === at ? { ...old, evidence_ids } : old,
                            ),
                          )
                        }
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() =>
                          roles(
                            index,
                            (item.participant_roles ?? []).filter(
                              (_, r) => r !== at,
                            ),
                          )
                        }
                      >
                        移除参与角色
                      </Button>
                    </fieldset>
                  ))}
                  {(item.participant_ids ?? [])
                    .filter(
                      (id) =>
                        !(item.participant_roles ?? []).some(
                          (role) => role.mention_id === id,
                        ),
                    )
                    .map((id) => (
                      <p className="research-provenance" key={id}>
                        {mentions.find((mention) => mention.id === id)?.name ??
                          "旧参与者记载"}
                        ：旧卡尚未区分角色。
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() =>
                            roles(index, [
                              ...(item.participant_roles ?? []),
                              {
                                mention_id: id,
                                role: "",
                                description: "",
                                evidence_ids: [],
                              },
                            ])
                          }
                        >
                          补充此参与角色
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() =>
                            patch(index, {
                              participant_ids: (
                                item.participant_ids ?? []
                              ).filter((value) => value !== id),
                            })
                          }
                        >
                          移除此参与者
                        </Button>
                      </p>
                    ))}
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!mentions.length}
                    onClick={() =>
                      roles(index, [
                        ...(item.participant_roles ?? []),
                        {
                          mention_id: mentions[0].id,
                          role: "",
                          description: "",
                          evidence_ids: [],
                        },
                      ])
                    }
                  >
                    添加参与者角色
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={removeBlocked.includes(item.id)}
                    onClick={() =>
                      onChange(value.filter((_, at) => at !== index))
                    }
                  >
                    移除事件 {index + 1}
                  </Button>
                  {removeBlocked.includes(item.id) && (
                    <p className="research-provenance">
                      需要先调整引用此事件的关系或身份关联。
                    </p>
                  )}
                </fieldset>
              )}
            </DeferredFields>
          ))}
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              const id = newId("event");
              onChange([
                ...value,
                {
                  id,
                  name: "",
                  description: "",
                  date: emptyDate(),
                  participant_ids: [],
                  evidence_ids: [],
                  identity_key: id,
                  action: "",
                  place: emptyValue(),
                  participant_roles: [],
                  event_status: "unknown",
                  status_note: "本次尚未核对该事项的发生性质。",
                },
              ]);
            }}
          >
            添加事件
          </Button>
        </>
      )}
    </DeferredFields>
  );
}

export function RelationFields({
  value,
  nodes,
  evidence,
  onChange,
}: {
  value: Relation[];
  nodes: Array<{ id: string; name: string }>;
  evidence: Draft["evidence"];
  onChange: (value: Relation[]) => void;
}) {
  function patch(index: number, change: Partial<Relation>) {
    onChange(
      value.map((item, at) => (at === index ? { ...item, ...change } : item)),
    );
  }
  return (
    <DeferredFields summary="本卡关系与依据" editorKey="relations">
      {() => (
        <>
          {value.map((item, index) => (
            <DeferredFields
              key={item.id}
              summary={`关系 ${index + 1} · ${item.predicate || "新增关系"}`}
              initiallyOpen={!item.predicate}
              editorKey={`relation:${item.id}`}
            >
              {() => (
                <fieldset className="research-edit-group">
                  <legend>关系 {index + 1}</legend>
                  {(["subject_id", "object_id"] as const).map((field) => (
                    <label key={field}>
                      {field === "subject_id" ? "关系起点" : "关系终点"}
                      <select
                        value={item[field]}
                        onChange={(event) =>
                          patch(index, { [field]: event.target.value })
                        }
                      >
                        {nodes.map((node) => (
                          <option value={node.id} key={node.id}>
                            {node.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label>
                    关系陈述
                    <Input
                      required
                      value={item.predicate}
                      onChange={(event) =>
                        patch(index, { predicate: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    关系作用
                    <select
                      value={item.role}
                      onChange={(event) =>
                        patch(index, {
                          role: event.target.value as Relation["role"],
                        })
                      }
                    >
                      <option value="support">支持</option>
                      <option value="limit">限制</option>
                      <option value="counter">反证</option>
                      <option value="background">背景</option>
                      <option value="association">关联</option>
                    </select>
                  </label>
                  <label>
                    说明
                    <textarea
                      required
                      rows={3}
                      value={item.reason}
                      onChange={(event) =>
                        patch(index, { reason: event.target.value })
                      }
                    />
                  </label>
                  <References
                    label="关系依据"
                    value={item.evidence_ids}
                    evidence={evidence}
                    onChange={(evidence_ids) => patch(index, { evidence_ids })}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() =>
                      onChange(value.filter((_, at) => at !== index))
                    }
                  >
                    移除本卡关系
                  </Button>
                </fieldset>
              )}
            </DeferredFields>
          ))}
          <Button
            type="button"
            variant="outline"
            disabled={nodes.length < 2}
            onClick={() =>
              onChange([
                ...value,
                {
                  id: newId("relation"),
                  subject_id: nodes[0].id,
                  object_id: nodes[1].id,
                  predicate: "",
                  role: "association",
                  reason: "",
                  evidence_ids: [],
                },
              ])
            }
          >
            添加本卡关系
          </Button>
        </>
      )}
    </DeferredFields>
  );
}
