// Hand-written types for occt-import-js (#2976) — the package ships none.
// Only the surface the STEP preview uses is declared.
declare module 'occt-import-js' {
  export interface OcctAttributeArray {
    array: number[];
  }

  export interface OcctMesh {
    name?: string;
    // Face colour as 0-1 RGB floats when the STEP file defines one.
    color?: [number, number, number];
    attributes: {
      position: OcctAttributeArray;
      normal?: OcctAttributeArray;
    };
    index: OcctAttributeArray;
  }

  export interface OcctImportResult {
    success: boolean;
    meshes: OcctMesh[];
  }

  export interface OcctInstance {
    ReadStepFile: (content: Uint8Array, params: unknown) => OcctImportResult;
  }

  export default function occtimportjs(options?: {
    locateFile?: (name: string) => string;
  }): Promise<OcctInstance>;
}
