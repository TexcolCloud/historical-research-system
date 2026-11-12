import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { readBooks } from "@/client/api";
import { research } from "@/features/research/api";
import ResearchEvidence from "@/features/research/ResearchEvidence";
import EvidenceOriginal from "@/features/research/EvidenceOriginal";
import { studies, studyKeys } from "./api";
import { StudyErrorState, useStudyTopic } from "./StudyTopicScope";
import type { StudyTopic } from "./types";
import { TopicNavigation } from "./TopicNavigation";

export function TopicsPage() {
  const scope = useStudyTopic();
  const cache = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const items = useQuery({
    queryKey: [...studyKeys.topics(archived), offset],
    queryFn: ({ signal }) =>
      studies.topics(archived, signal, { offset, limit: 30 }),
    staleTime: 30_000,
    retry: false,
  });
  const create = useMutation({
    mutationFn: () =>
      studies.createTopic({ title: title.trim(), description, book_ids: [] }),
    onSuccess: (topic) => {
      cache.setQueryData(studyKeys.topic(topic.id), topic);
      void cache.invalidateQueries({ queryKey: ["studies", "topics"] });
      scope.select(topic.id);
      setCreating(false);
      setTitle("");
      setDescription("");
      setOffset(0);
    },
  });
  return (
    <section className="study-page">
      {!scope.topicId && (
        <header className="study-page-heading">
          <div>
            <p className="study-eyebrow">研究资料</p>
            <h1>研究专题</h1>
            <p>围绕一个问题整理材料、论据和个人思考。</p>
          </div>
          <Button onClick={() => setCreating(true)}>新建专题</Button>
        </header>
      )}
      {creating && (
        <form
          className="study-editor"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <h2>创建研究专题</h2>
          <label>
            专题名称
            <input
              aria-label="专题名称"
              value={title}
              maxLength={300}
              required
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label>
            研究说明
            <textarea
              value={description}
              maxLength={60000}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          {create.isError && <p role="alert">{create.error.message}</p>}
          <div className="study-actions">
            <Button type="submit" disabled={!title.trim() || create.isPending}>
              {create.isPending ? "正在创建…" : "创建专题"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setCreating(false)}
            >
              取消
            </Button>
          </div>
        </form>
      )}
      <div
        className={scope.topicId ? "study-open-topic" : "study-topic-layout"}
      >
        {!scope.topicId && (
          <aside className="study-topic-list" aria-label="专题清单">
            <label className="study-check">
              <input
                type="checkbox"
                checked={archived}
                onChange={(event) => {
                  setArchived(event.target.checked);
                  setOffset(0);
                }}
              />
              查看已归档专题
            </label>
            {items.isError ? (
              <StudyErrorState
                error={items.error}
                retry={() => void items.refetch()}
              />
            ) : items.isPending ? (
              <p role="status">正在读取专题…</p>
            ) : items.data?.items.length ? (
              items.data.items.map((topic) => (
                <button
                  className="study-topic-item"
                  key={topic.id}
                  aria-pressed={scope.topicId === topic.id}
                  onClick={() => scope.select(topic.id)}
                >
                  <strong>{topic.title}</strong>
                  <span>{topic.description || "尚未填写研究说明"}</span>
                  <small>
                    资料 {topic.material_count ?? topic.materials.length} 项
                  </small>
                </button>
              ))
            ) : (
              <p className="study-empty">
                {archived ? "尚无归档专题。" : "创建第一个专题，开始整理材料。"}
              </p>
            )}
            {items.data && (
              <nav className="study-actions" aria-label="专题分页">
                <Button
                  variant="ghost"
                  disabled={!offset || items.isFetching}
                  onClick={() => setOffset(Math.max(0, offset - 30))}
                >
                  上一页专题
                </Button>
                <span>
                  {items.data.total ? offset + 1 : 0}–
                  {Math.min(offset + 30, items.data.total)} / {items.data.total}
                </span>
                <Button
                  variant="ghost"
                  disabled={offset + 30 >= items.data.total || items.isFetching}
                  onClick={() => setOffset(offset + 30)}
                >
                  下一页专题
                </Button>
              </nav>
            )}
          </aside>
        )}
        <div className="study-topic-detail">
          {!scope.topicId ? (
            <div className="study-empty">
              <h2>选择一个专题</h2>
              <p>每个专题可以拥有自己的选材、论据丛、时间线索和多张画布。</p>
            </div>
          ) : scope.topic.isError ? (
            <StudyErrorState
              error={scope.topic.error}
              retry={() => void scope.topic.refetch()}
            />
          ) : scope.topic.data ? (
            <TopicDetail
              key={scope.topic.data.id}
              topic={scope.topic.data}
              onSaved={(topic) => {
                cache.setQueryData(studyKeys.topic(topic.id), topic);
                void cache.invalidateQueries({
                  queryKey: ["studies", "topics"],
                });
              }}
            />
          ) : (
            <p role="status">正在打开专题…</p>
          )}
        </div>
      </div>
    </section>
  );
}

function TopicDetail({
  topic,
  onSaved,
}: {
  topic: StudyTopic;
  onSaved: (topic: StudyTopic) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [title, setTitle] = useState(topic.title);
  const [description, setDescription] = useState(topic.description);
  const [selected, setSelected] = useState("");
  const save = useMutation({
    mutationFn: (changes: {
      title?: string;
      description?: string;
      archived?: boolean;
    }) =>
      studies.updateTopic(topic.id, {
        expected_version: topic.version,
        ...changes,
      }),
    onSuccess: (value) => {
      onSaved(value);
      setEditing(false);
    },
  });
  const remove = useMutation({
    mutationFn: (material: string) =>
      studies.removeMaterial(topic.id, material, topic.version),
    onSuccess: onSaved,
  });
  return (
    <>
      <TopicNavigation
        topicId={topic.id}
        topicTitle={topic.title}
        active="materials"
      />
      <div className="study-detail-heading">
        <div>
          <p className="study-eyebrow">
            {topic.archived ? "已归档专题" : "当前专题"}
          </p>
          <h2>{topic.title}</h2>
          <p className="study-preserve">
            {topic.description || "补充研究说明，让选材围绕同一个方向。"}
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => {
            setTitle(topic.title);
            setDescription(topic.description);
            setEditing(!editing);
          }}
        >
          编辑专题
        </Button>
      </div>
      {editing && (
        <form
          className="study-editor"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate({ title: title.trim(), description });
          }}
        >
          <label>
            专题名称
            <input
              aria-label="修改专题名称"
              value={title}
              required
              maxLength={300}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label>
            研究说明
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <div className="study-actions">
            <Button type="submit" disabled={save.isPending || !title.trim()}>
              保存专题
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setEditing(false)}
            >
              取消
            </Button>
          </div>
        </form>
      )}
      {save.isError && <p role="alert">{save.error.message}</p>}
      <div className="study-detail-heading">
        <h3>专题材料 · {topic.materials.length}</h3>
        <Button
          variant="outline"
          disabled={topic.archived}
          onClick={() => setPicking(!picking)}
        >
          添加史料卡
        </Button>
      </div>
      {picking && <MaterialPicker topic={topic} onSaved={onSaved} />}
      {remove.isError && <p role="alert">{remove.error.message}</p>}
      <ul className="study-material-list">
        {topic.materials.map((material) => (
          <li key={material.id}>
            <button
              className="study-text-button"
              onClick={() => setSelected(material.id)}
            >
              {material.title}
            </button>
            <span className="study-badge">v{material.card_version}</span>
            {!material.source_current && (
              <span className="study-warning">来源有更新，引用保留</span>
            )}
            <Button
              variant="ghost"
              disabled={remove.isPending || topic.archived}
              onClick={() => remove.mutate(material.id)}
            >
              移出专题
            </Button>
          </li>
        ))}
      </ul>
      {!topic.materials.length && (
        <p className="study-empty">
          添加已核验史料卡后，可按其原文建立论据和线索。原书范围可在全文检索中选择。
        </p>
      )}
      {selected && (
        <MaterialPreview
          topicId={topic.id}
          materialId={selected}
          onClose={() => setSelected("")}
        />
      )}
      <Button
        variant="ghost"
        disabled={save.isPending}
        onClick={() => save.mutate({ archived: !topic.archived })}
      >
        {topic.archived ? "恢复专题" : "归档专题"}
      </Button>
    </>
  );
}

function MaterialPicker({
  topic,
  onSaved,
}: {
  topic: StudyTopic;
  onSaved: (topic: StudyTopic) => void;
}) {
  const books = useQuery({
    queryKey: ["platform", "books"],
    queryFn: ({ signal }) => readBooks(signal),
    staleTime: 30_000,
    retry: false,
  });
  const [bookId, setBook] = useState("");
  const [offset, setOffset] = useState(0);
  const cards = useQuery({
    queryKey: ["research", "cards", bookId, offset],
    queryFn: ({ signal }) => research.cards(bookId, offset, signal),
    enabled: !!bookId,
    staleTime: 30_000,
    retry: false,
  });
  const add = useMutation({
    mutationFn: (card: { id: string; version: number }) =>
      studies.addMaterial(topic.id, {
        expected_version: topic.version,
        card_id: card.id,
        card_version: card.version,
        evidence_ids: [],
      }),
    onSuccess: onSaved,
  });
  return (
    <div className="study-material-picker">
      <label>
        来源书籍
        <select
          aria-label="来源书籍"
          value={bookId}
          onChange={(event) => {
            setBook(event.target.value);
            setOffset(0);
          }}
        >
          <option value="">选择一本已处理书籍</option>
          {books.data?.map((book) => (
            <option key={book.id} value={book.id}>
              {book.title}
            </option>
          ))}
        </select>
      </label>
      {books.isError && (
        <StudyErrorState
          error={books.error}
          retry={() => void books.refetch()}
        />
      )}
      {cards.isError && (
        <StudyErrorState
          error={cards.error}
          retry={() => void cards.refetch()}
        />
      )}
      {bookId && cards.isPending && <p role="status">正在读取史料卡…</p>}
      {add.isError && <p role="alert">{add.error.message}</p>}
      {cards.data?.items.map((card) => {
        const present = topic.materials.some(
          (material) =>
            material.card_id === card.id &&
            material.card_version === card.version,
        );
        const usable =
          ["verified", "human_accepted"].includes(card.state) &&
          card.source_current !== false;
        return (
          <div className="study-picker-row" key={card.id}>
            <span>{card.title}</span>
            <Button
              variant="outline"
              disabled={present || !usable || add.isPending}
              onClick={() => add.mutate(card)}
            >
              {present ? "已加入" : usable ? "加入专题" : "待核验"}
            </Button>
          </div>
        );
      })}
      {cards.data && !cards.data.items.length && (
        <p>本书尚无可加入的史料卡。</p>
      )}
      {cards.data && (
        <nav className="study-actions" aria-label="选材分页">
          <Button
            variant="ghost"
            disabled={!offset || cards.isFetching}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            上一页史料卡
          </Button>
          <span>
            {offset + 1}–{Math.min(offset + 50, cards.data.total)} /{" "}
            {cards.data.total}
          </span>
          <Button
            variant="ghost"
            disabled={offset + 50 >= cards.data.total || cards.isFetching}
            onClick={() => setOffset(offset + 50)}
          >
            下一页史料卡
          </Button>
        </nav>
      )}
    </div>
  );
}

export function MaterialPreview({
  topicId,
  materialId,
  evidenceId,
  onClose,
}: {
  topicId: string;
  materialId: string;
  evidenceId?: string;
  onClose: () => void;
}) {
  const material = useQuery({
    queryKey: studyKeys.material(topicId, materialId),
    queryFn: ({ signal }) => studies.material(topicId, materialId, signal),
    retry: false,
    staleTime: 30_000,
  });
  const evidence = (material.data?.evidence ?? []).filter(
    (span) => !evidenceId || span.id === evidenceId,
  );
  return (
    <aside className="study-source-preview" aria-label="所选材料依据">
      <Button variant="ghost" onClick={onClose}>
        收起依据
      </Button>
      {material.isError ? (
        <StudyErrorState
          error={material.error}
          retry={() => void material.refetch()}
        />
      ) : material.data ? (
        <>
          <h3>{material.data.title}</h3>
          <p>
            引用版本 {material.data.card_version}
            {!material.data.source_current &&
              " · 原引用保留，当前来源需重新核对"}
          </p>
          <p>{material.data.source_citation || "书目信息尚未完整提供。"}</p>
          <p className="study-muted">
            {material.data.printed_pages
              ? `印刷页码：${material.data.printed_pages}`
              : "印刷页码未提供；下方 PDF 页码用于定位原件。"}
          </p>
          {evidenceId && !evidence.length && (
            <p role="alert">
              该条引用不在保留的材料快照中，请重新核对建议与来源。
            </p>
          )}
          <ResearchEvidence
            evidence={evidence}
            bookId={material.data.book_id}
            selected={evidenceId}
            historical={!material.data.source_current}
          />
          <EvidenceOriginal evidence={evidence} />
        </>
      ) : (
        <p role="status">正在读取原文依据…</p>
      )}
    </aside>
  );
}
