// @vitest-environment happy-dom
import { beforeEach, expect, test, vi } from "vitest";
import { createBookUploader } from "./uploads.ts";
beforeEach(() => localStorage.clear());

test("deleting one book removes only its upload from the retry queue", async () => {
  const create = vi.fn().mockImplementation(async (request) => ({
    book_id: request.filename,
    session_id: request.filename,
    token: "fixture",
    endpoint: "/uploads/",
    expires_at: "2099-01-01T00:00:00Z",
  }));
  const { uppy, prepare, cancelBooks } = createBookUploader(create);
  const first = uppy.addFile({
    name: "deleted.pdf",
    data: new Blob(["%PDF-1.4"]),
  });
  const second = uppy.addFile({
    name: "retained.pdf",
    data: new Blob(["%PDF-1.4"]),
  });
  await prepare([first, second]);
  cancelBooks(["deleted.pdf"]);
  expect(uppy.getFile(first)).toBeUndefined();
  expect(uppy.getFile(second)).toBeDefined();
  uppy.destroy();
});

test("retrying preparation keeps the same book and scoped upload authorization", async () => {
  const grant = {
    session_id: "a",
    book_id: "b",
    token: "secret",
    endpoint: "/uploads/",
    expires_at: "2099-01-01T00:00:00Z",
  };
  const create = vi.fn().mockResolvedValue(grant);
  const { uppy, prepare } = createBookUploader(create);
  const id = uppy.addFile({
    name: "书.pdf",
    type: "application/pdf",
    data: new Blob(["%PDF-1.4"]),
  });
  await prepare([id]);
  await prepare([id]);
  expect(create).toHaveBeenCalledTimes(1);
  expect(uppy.getFile(id).meta.session_id).toBe("a");
  expect(uppy.getFile(id).tus?.headers).toEqual({
    Authorization: "Bearer secret",
  });
  expect(uppy.getFile(id).meta).not.toHaveProperty("token");
  uppy.destroy();
});

test("a lost permit response retries with the same request identity", async () => {
  const create = vi
    .fn()
    .mockRejectedValueOnce(new Error("connection lost"))
    .mockResolvedValue({
      session_id: "a",
      book_id: "b",
      token: "secret",
      endpoint: "/uploads/",
      expires_at: "2099-01-01T00:00:00Z",
    });
  const { uppy, prepare } = createBookUploader(create);
  const id = uppy.addFile({ name: "书.pdf", data: new Blob(["%PDF-1.4"]) });
  await expect(prepare([id])).rejects.toThrow("connection lost");
  await prepare([id]);
  expect(create.mock.calls[0][1]).toMatchObject({
    requestId: expect.any(String),
    token: expect.any(String),
  });
  expect(create.mock.calls[0][1]).toEqual(create.mock.calls[1][1]);
  uppy.destroy();
});

test("restored upload credentials reuse the existing server session", async () => {
  const create = vi.fn();
  const { uppy, prepare } = createBookUploader(create);
  const id = uppy.addFile({ name: "恢复.pdf", data: new Blob(["%PDF-1.4"]) });
  uppy.emit("restored", {
    hrsUpload: {
      grants: {
        [id]: {
          session_id: "saved",
          book_id: "book",
          token: "scoped",
          endpoint: "/uploads/",
          expires_at: "2099-01-01T00:00:00Z",
        },
      },
      identities: {},
    },
  });
  await prepare([id]);
  expect(create).not.toHaveBeenCalled();
  expect(uppy.getFile(id).tus?.headers).toEqual({
    Authorization: "Bearer scoped",
  });
  uppy.destroy();
});
