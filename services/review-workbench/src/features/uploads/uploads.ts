import Uppy from "@uppy/core";
import Tus from "@uppy/tus";
import GoldenRetriever from "@uppy/golden-retriever";
import { z } from "zod";
import zhCN from "@uppy/locales/lib/zh_CN";
import {
  requestUpload,
  type UploadRequest,
  type UploadSession,
  type UploadIdentity,
} from "@/client/api.ts";

const recoverySchema = z.object({
  hrsUpload: z.object({
    grants: z.record(
      z.string(),
      z.object({
        session_id: z.string(),
        book_id: z.string(),
        token: z.string(),
        endpoint: z.string(),
        expires_at: z.string(),
      }),
    ),
    identities: z.record(
      z.string(),
      z.object({ requestId: z.string(), token: z.string() }),
    ),
  }),
});

export function createBookUploader(
  create: (
    request: UploadRequest,
    identity: UploadIdentity,
  ) => Promise<UploadSession> = requestUpload,
) {
  const uppy = new Uppy({
    id: "hrs-books-v2",
    locale: zhCN,
    restrictions: {
      allowedFileTypes: [".pdf"],
      maxFileSize: 256 * 1024 * 1024,
    },
  }).use(Tus, {
    endpoint: "/uploads/",
    limit: 2,
    chunkSize: 8 * 1024 * 1024,
    allowedMetaFields: ["session_id", "filename"],
    retryDelays: [0, 1000, 3000, 5000],
    removeFingerprintOnSuccess: true,
    fingerprint: async (_file, options) =>
      `hrs-v2:${options.metadata?.session_id}`,
  });
  const grants = new Map<string, UploadSession>();
  const identities = new Map<string, UploadIdentity>();
  const persist = () =>
    uppy.emit("restore:plugin-data-changed", {
      hrsUpload: {
        grants: Object.fromEntries(grants),
        identities: Object.fromEntries(identities),
      },
    });
  uppy.on("restored", (data) => {
    const saved = recoverySchema.safeParse(data);
    if (!saved.success) return;
    for (const [id, grant] of Object.entries(saved.data.hrsUpload.grants))
      grants.set(id, grant);
    for (const [id, identity] of Object.entries(
      saved.data.hrsUpload.identities,
    ))
      identities.set(id, identity);
  });
  uppy.use(GoldenRetriever, { expires: 24 * 60 * 60 * 1000 });
  async function prepare(ids: string[]) {
    for (const id of ids) {
      const file = uppy.getFile(id);
      if (!file) continue;
      let grant = grants.get(id);
      if (!grant) {
        let identity = identities.get(id);
        if (!identity) {
          identity = {
            requestId: crypto.randomUUID(),
            token: crypto.randomUUID() + crypto.randomUUID(),
          };
          identities.set(id, identity);
          persist();
        }
        grant = await create(
          { filename: file.name || "未命名.pdf", byte_length: file.size || 0 },
          identity,
        );
        grants.set(id, grant);
        persist();
      }
      uppy.setFileMeta(id, {
        session_id: grant.session_id,
        filename: file.name,
      });
      uppy.setFileState(id, {
        tus: {
          ...file.tus,
          endpoint: grant.endpoint,
          headers: { Authorization: `Bearer ${grant.token}` },
        },
      });
      uppy.emit("preprocess-complete", uppy.getFile(id));
    }
  }
  uppy.addPreProcessor(prepare);
  uppy.on("file-removed", (file) => {
    grants.delete(file.id);
    identities.delete(file.id);
    persist();
  });
  function cancelBooks(bookIds: string[]) {
    for (const [fileId, grant] of grants) {
      if (bookIds.includes(grant.book_id) && uppy.getFile(fileId))
        uppy.removeFile(fileId);
    }
  }
  return { uppy, prepare, cancelBooks };
}
