/// <reference lib="webworker" />
// STEP triangulation worker (#2976).
//
// OpenCascade compiled to WASM does the triangulation. It runs in a worker
// for two reasons: parsing a real assembly takes seconds and would freeze
// the UI thread, and — decisive — the emscripten/embind glue generates its
// invoker functions with `new Function(...)`, which the app's nonce-strict
// CSP rightly blocks on the document. Per CSP3 a dedicated worker is
// governed by the policy delivered with this script's own response, so the
// backend relaxes 'unsafe-eval' for exactly this asset and nothing else
// (see security_headers_middleware in backend/app/main.py).
import occtimportjs from 'occt-import-js';
import type { OcctInstance } from 'occt-import-js';
import wasmUrl from 'occt-import-js/dist/occt-import-js.wasm?url';

export interface StepWorkerRequest {
  /** Correlation id echoed on the response — the worker is shared. */
  id: number;
  buffer: ArrayBuffer;
}

export interface StepWorkerMesh {
  positions: Float32Array;
  normals: Float32Array | null;
  indices: Uint32Array | null;
  color: [number, number, number] | null;
}

export type StepWorkerResponse =
  | { id: number; ok: true; meshes: StepWorkerMesh[] }
  | { id: number; ok: false; reason: 'no-meshes' | 'error' };

// The ~7 MB wasm instance is expensive to initialise — created once and kept
// for the worker's lifetime. A failed init is not cached so a later preview
// retries from scratch.
let instancePromise: Promise<OcctInstance> | null = null;

function getInstance(): Promise<OcctInstance> {
  if (!instancePromise) {
    instancePromise = occtimportjs({ locateFile: () => wasmUrl }).catch((err: unknown) => {
      instancePromise = null;
      throw err;
    });
  }
  return instancePromise;
}

self.onmessage = async (event: MessageEvent<StepWorkerRequest>) => {
  const { id } = event.data;
  try {
    const occt = await getInstance();
    const result = occt.ReadStepFile(new Uint8Array(event.data.buffer), null);
    if (!result.success || result.meshes.length === 0) {
      self.postMessage({ id, ok: false, reason: 'no-meshes' } satisfies StepWorkerResponse);
      return;
    }
    const meshes = result.meshes.map(
      (mesh): StepWorkerMesh => ({
        positions: new Float32Array(mesh.attributes.position.array),
        normals: mesh.attributes.normal ? new Float32Array(mesh.attributes.normal.array) : null,
        indices: mesh.index ? new Uint32Array(mesh.index.array) : null,
        color: mesh.color ?? null,
      })
    );
    // Transfer the typed-array buffers instead of structured-cloning them —
    // large assemblies are tens of MB of vertex data.
    const transfers = meshes.flatMap((mesh) => {
      const buffers = [mesh.positions.buffer];
      if (mesh.normals) buffers.push(mesh.normals.buffer);
      if (mesh.indices) buffers.push(mesh.indices.buffer);
      return buffers;
    });
    self.postMessage({ id, ok: true, meshes } satisfies StepWorkerResponse, transfers);
  } catch {
    self.postMessage({ id, ok: false, reason: 'error' } satisfies StepWorkerResponse);
  }
};
