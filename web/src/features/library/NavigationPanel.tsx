import NavigationReader from "./NavigationReader";
import NavigationEditingWorkspace from "./NavigationEditingWorkspace";

export default function NavigationPanel({
  published = false,
  reviewOnly = false,
  ...props
}: {
  runId: string;
  bookId: string;
  bookState?: string;
  reviewOnly?: boolean;
  entryId?: string;
  published?: boolean;
}) {
  return published && !reviewOnly ? (
    <NavigationReader bookId={props.bookId} bookState={props.bookState} />
  ) : (
    <NavigationEditingWorkspace {...props} reviewOnly={reviewOnly} />
  );
}
