type ResumeWork = { href: string; title: string };
const key = (workspace: string) =>
  `hrs:resume:${encodeURIComponent(workspace)}`;

function workingLocation(href: string) {
  if (
    !href.startsWith("/") ||
    href.startsWith("//") ||
    href.includes("\\") ||
    href.length > 16_000
  )
    return false;
  try {
    const url = new URL(href, "http://hrs.local");
    if (
      url.origin !== "http://hrs.local" ||
      [...url.searchParams.keys()].some((name) =>
        /token|password|secret|api.?key|signature/i.test(name),
      )
    )
      return false;
    return (
      /^\/books\/[^/]+$/.test(url.pathname) ||
      /^\/studies\/[^/]+\/canvas$/.test(url.pathname) ||
      (["/studies", "/arguments", "/timeline", "/analysis"].includes(
        url.pathname,
      ) &&
        !!url.searchParams.get("topic")) ||
      (url.pathname === "/search" && !!url.searchParams.get("q"))
    );
  } catch {
    return false;
  }
}

/** Only an explicit local continue link can reopen a retained workspace location. */
export function readResumeWork(workspace: string): ResumeWork | undefined {
  if (!workspace) return undefined;
  try {
    const value = JSON.parse(localStorage.getItem(key(workspace)) || "null");
    if (
      value?.workspace === workspace &&
      typeof value.href === "string" &&
      typeof value.title === "string" &&
      workingLocation(value.href)
    )
      return { href: value.href, title: value.title };
  } catch {
    /* Local view history is optional. */
  }
  return undefined;
}

export function rememberResumeWork(
  workspace: string,
  href: string,
  title: string,
) {
  if (!workspace || !workingLocation(href)) return undefined;
  const value = { href, title };
  try {
    localStorage.setItem(
      key(workspace),
      JSON.stringify({ workspace, ...value }),
    );
  } catch {
    /* Storage failure must not interrupt reading or review. */
  }
  return value;
}
