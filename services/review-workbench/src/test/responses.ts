import { afterEach } from "vitest";
import { client } from "@/client/api.ts";

const disposers: Array<() => void> = [];
afterEach(() => {
  for (const dispose of disposers.splice(0)) dispose();
});

/** Exercise the generated client's URL/JSON behavior without an external server. */
export function respondWith(handler: (request: Request) => unknown) {
  const middleware = {
    onRequest: ({ request }: { request: Request }) =>
      Response.json(handler(request)),
  };
  client.use(middleware);
  disposers.push(() => client.eject(middleware));
}
