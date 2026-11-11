import { lazy, Suspense, useLayoutEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import EntityLibrary from "./EntityLibrary";
import NetworkFilters from "./NetworkFilters";
import NetworkEvidence from "./NetworkEvidence";
import EntityTimeline from "./EntityTimeline";
import {
  networkOptions,
  networkParams,
  networkState,
  timelineOptions,
} from "./network-query";
import type { ResearchTimelineItem } from "./api";

const ResearchGraph = lazy(() => import("./ResearchGraph"));

/** URL owns the scope and selection; cached queries retain the source-bound return path. */
export default function EntityWorkspace() {
  const [params, setParams] = useSearchParams();
  const state = networkState(params);
  const evidencePane = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (evidencePane.current) evidencePane.current.scrollTop = 0;
  }, [state.selection.kind, state.selection.id]);
  const cache = useQueryClient();
  const graph = useQuery({
    ...networkOptions(state.entityId, state.hops, state.limit, state.scope),
    enabled: !!state.entityId && state.display === "graph",
  });
  const timeline = useQuery({
    ...timelineOptions(
      state.entityId,
      state.dateBasis,
      state.offset,
      state.scope,
    ),
    enabled:
      !!state.entityId &&
      (state.display === "timeline" ||
        state.selection.kind === "event" ||
        (state.selection.kind === "document" &&
          state.dateBasis === "formation")),
  });
  function change(changes: Record<string, string>) {
    setParams(networkParams(params, changes));
  }
  function center(id: string, display = "graph") {
    change({
      entity: id,
      selected: "",
      selection: "",
      timeline_offset: "",
      display,
    });
  }
  function changeScope(changes: Record<string, string>) {
    change({
      ...changes,
      selected: "",
      selection: "",
      timeline_offset: "",
      ...(Object.hasOwn(changes, "book_id") ? { offset: "" } : {}),
    });
  }
  function selectItem(item: ResearchTimelineItem) {
    change({
      selected: item.id,
      selection: item.item_kind === "document" ? "document" : "event",
    });
  }
  function followEndpoint(id: string) {
    const node = graph.data?.nodes.find((node) => node.id === id);
    if (node?.kind === "card")
      change({ selected: node.card_id || node.id, selection: "document" });
    else center(id);
  }
  const selectedItem = timeline.data?.items.find(
    (item) => item.id === state.selection.id,
  );
  const rootName =
    graph.data?.nodes.find((node) => node.id === state.entityId)?.name ||
    timeline.data?.entity_name;
  return (
    <section className="entity-workspace" aria-label="实体查阅工作区">
      <header className="network-workspace-heading">
        <div>
          <Link to="/research" className="research-source-link">
            ← 实体库
          </Link>
          <h1>关系与时间线</h1>
          <p>围绕实体查阅关联记载，沿每条依据回到原文。</p>
        </div>
        <NetworkFilters
          scope={state.scope}
          predicates={
            graph.data?.edges.map((edge) => edge.predicate || edge.kind) ?? []
          }
          onChange={changeScope}
        />
      </header>
      <div className="network-workspace-grid">
        <aside className="network-entity-index" aria-label="查找与选择实体">
          <EntityLibrary
            compact
            bookId={state.scope.bookId}
            onSelect={(id) => center(id, state.display)}
          />
        </aside>
        <div className="network-workspace-main">
          <header className="network-view-toolbar">
            <Tabs
              value={state.display}
              onValueChange={(display) => change({ display })}
            >
              <TabsList aria-label="实体查阅视图">
                <TabsTrigger
                  value="graph"
                  id="network-graph-tab"
                  aria-controls="network-graph-panel"
                >
                  关系网络
                </TabsTrigger>
                <TabsTrigger
                  value="timeline"
                  id="network-timeline-tab"
                  aria-controls="network-timeline-panel"
                >
                  时间线
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <Button
              variant="ghost"
              disabled={
                !state.entityId || graph.isFetching || timeline.isFetching
              }
              onClick={() => {
                void cache.invalidateQueries({ queryKey: ["research"] });
              }}
            >
              刷新当前资料
            </Button>
          </header>
          {!state.entityId ? (
            <div className="workspace-empty">
              <h2>从一个实体开始</h2>
              <p>
                在左侧选择人物、机构、地点、文献或事件。关系与时间线均来自已核验史料卡。
              </p>
            </div>
          ) : (
            <>
              <div className="network-focus-heading">
                <span>当前关注</span>
                <strong>{rootName || "所选实体"}</strong>
                <p>单击仅查看依据；选择“以此为中心”后再展开关联。</p>
              </div>
              <div
                role="tabpanel"
                id="network-graph-panel"
                aria-labelledby="network-graph-tab"
                hidden={state.display !== "graph"}
              >
                <Suspense fallback={<p role="status">正在打开关系网络…</p>}>
                  <ResearchGraph
                    entityId={state.entityId}
                    viewKey={JSON.stringify({
                      entity: state.entityId,
                      scope: state.scope,
                    })}
                    data={graph.data}
                    onEntity={center}
                    onCard={(id) =>
                      change({ selected: id, selection: "document" })
                    }
                    selectedNodeId={
                      state.selection.kind === "entity"
                        ? state.selection.id
                        : undefined
                    }
                    selectedEdgeId={
                      state.selection.kind === "relation"
                        ? state.selection.id
                        : undefined
                    }
                    onSelectNode={(node) =>
                      change({
                        selected:
                          node.kind === "card"
                            ? node.card_id || node.id
                            : node.canonical_id || node.id,
                        selection: node.kind === "card" ? "document" : "entity",
                      })
                    }
                    onSelectEdge={(edge) =>
                      change({ selected: edge.id, selection: "relation" })
                    }
                    hops={state.hops}
                    onHopsChange={(hops) => change({ hops: String(hops) })}
                    loading={graph.isPending}
                    error={graph.error?.message}
                    onRetry={() => void graph.refetch()}
                  />
                </Suspense>
                {graph.data?.truncated && state.limit < 200 && (
                  <Button
                    variant="outline"
                    onClick={() =>
                      change({
                        network_limit: String(
                          state.limit === 30
                            ? 60
                            : state.limit === 60
                              ? 100
                              : 200,
                        ),
                      })
                    }
                  >
                    继续加载关联
                  </Button>
                )}
              </div>
              <div
                role="tabpanel"
                id="network-timeline-panel"
                aria-labelledby="network-timeline-tab"
                hidden={state.display !== "timeline"}
              >
                <label className="network-date-basis">
                  日期依据
                  <select
                    aria-label="日期依据"
                    value={state.dateBasis}
                    onChange={(event) =>
                      change({
                        date_basis: event.target.value,
                        timeline_offset: "",
                        selected: "",
                        selection: "",
                      })
                    }
                  >
                    <option value="event">材料叙述的事项日期</option>
                    <option value="formation">文献形成日期</option>
                  </select>
                </label>
                <EntityTimeline
                  entityId={state.entityId}
                  onEntity={center}
                  onCard={(id) =>
                    change({ selected: id, selection: "document" })
                  }
                  data={timeline.data}
                  selectedEventId={state.selection.id}
                  onSelectEvent={selectItem}
                  onViewInNetwork={(item) =>
                    item.entity_id
                      ? center(item.entity_id)
                      : change({
                          display: "graph",
                          selected: item.card_id,
                          selection: "document",
                        })
                  }
                  loading={timeline.isPending}
                  error={timeline.error?.message}
                  onRetry={() => void timeline.refetch()}
                />
                {timeline.data && (
                  <nav className="research-pagination" aria-label="事项分页">
                    <Button
                      variant="outline"
                      disabled={!state.offset || timeline.isFetching}
                      onClick={() =>
                        change({
                          timeline_offset: String(
                            Math.max(0, state.offset - 20),
                          ),
                          selected: "",
                          selection: "",
                        })
                      }
                    >
                      上一页事项
                    </Button>
                    <span>
                      {timeline.data.total ? state.offset + 1 : 0}–
                      {Math.min(state.offset + 20, timeline.data.total)} /{" "}
                      {timeline.data.total} 项 · 未定年{" "}
                      {timeline.data.undated_total} 项
                    </span>
                    <Button
                      variant="outline"
                      disabled={
                        state.offset + 20 >= timeline.data.total ||
                        timeline.isFetching
                      }
                      onClick={() =>
                        change({
                          timeline_offset: String(state.offset + 20),
                          selected: "",
                          selection: "",
                        })
                      }
                    >
                      下一页事项
                    </Button>
                  </nav>
                )}
              </div>
            </>
          )}
        </div>
        <aside
          ref={evidencePane}
          className="network-evidence-pane"
          aria-label="所选记录的原文依据"
        >
          <NetworkEvidence
            key={`${state.selection.kind}:${state.selection.id}`}
            selection={state.selection}
            item={selectedItem}
            itemPending={timeline.isPending}
            itemError={timeline.error?.message}
            bookId={state.scope.bookId}
            onCenter={followEndpoint}
            onTimeline={(id) => center(id, "timeline")}
            onClear={() => change({ selected: "", selection: "" })}
            onReload={() => {
              void cache.invalidateQueries({ queryKey: ["research"] });
            }}
          />
        </aside>
      </div>
    </section>
  );
}
