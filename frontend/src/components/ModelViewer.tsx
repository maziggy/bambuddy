import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import JSZip from 'jszip';
import { Loader2, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import { Button } from './Button';

interface BuildVolume {
  x: number;
  y: number;
  z: number;
}

interface ModelViewerProps {
  url: string;
  buildVolume?: BuildVolume;
  filamentColors?: string[];
  className?: string;
}

interface MeshData {
  vertices: number[];
  triangles: number[];
  extruder: number; // Per-mesh extruder index for coloring
}

interface ObjectData {
  id: string;
  meshes: MeshData[];
  defaultExtruder: number; // Default extruder for object (used if mesh doesn't have specific one)
}

interface BuildItem {
  objectId: string;
  transform: THREE.Matrix4;
  extruder?: number; // Can override object's extruder
}

// Parse 3MF transform - keep in 3MF coordinate space (Z-up)
function parseTransform3MF(transformStr: string | null): THREE.Matrix4 {
  const matrix = new THREE.Matrix4();
  if (!transformStr) {
    return matrix; // Identity matrix
  }

  // 3MF transform is a 3x4 affine matrix in row-major order:
  // "m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32"
  // Where (m30, m31, m32) is the translation vector
  const values = transformStr.trim().split(/\s+/).map(parseFloat);
  if (values.length >= 12) {
    // Three.js Matrix4.set takes row-major order arguments:
    // set(n11, n12, n13, n14, n21, n22, n23, n24, n31, n32, n33, n34, n41, n42, n43, n44)
    // 3MF row-major: m00, m01, m02, m10, m11, m12, m20, m21, m22, m30, m31, m32
    matrix.set(
      values[0], values[1], values[2], values[9],   // m00, m01, m02, tx
      values[3], values[4], values[5], values[10],  // m10, m11, m12, ty
      values[6], values[7], values[8], values[11],  // m20, m21, m22, tz
      0, 0, 0, 1
    );
  }
  return matrix;
}

// Alias for backwards compatibility
const parseTransform = parseTransform3MF;

async function parseMeshFromDoc(doc: Document, defaultExtruder: number = 0): Promise<MeshData[]> {
  const meshes: MeshData[] = [];
  const meshElements = doc.getElementsByTagName('mesh');

  for (let j = 0; j < meshElements.length; j++) {
    const meshEl = meshElements[j];
    const vertices: number[] = [];
    const triangles: number[] = [];

    const vertexElements = meshEl.getElementsByTagName('vertex');
    for (let k = 0; k < vertexElements.length; k++) {
      const v = vertexElements[k];
      vertices.push(
        parseFloat(v.getAttribute('x') || '0'),
        parseFloat(v.getAttribute('y') || '0'),
        parseFloat(v.getAttribute('z') || '0')
      );
    }

    const triangleElements = meshEl.getElementsByTagName('triangle');
    for (let k = 0; k < triangleElements.length; k++) {
      const t = triangleElements[k];
      triangles.push(
        parseInt(t.getAttribute('v1') || '0'),
        parseInt(t.getAttribute('v2') || '0'),
        parseInt(t.getAttribute('v3') || '0')
      );
    }

    if (vertices.length > 0 && triangles.length > 0) {
      meshes.push({ vertices, triangles, extruder: defaultExtruder });
    }
  }
  return meshes;
}

async function parse3MF(arrayBuffer: ArrayBuffer): Promise<{ objects: Map<string, ObjectData>; buildItems: BuildItem[] }> {
  const zip = await JSZip.loadAsync(arrayBuffer);
  const objects = new Map<string, ObjectData>();
  const buildItems: BuildItem[] = [];
  const parser = new DOMParser();

  // Helper to load and parse a model file from the zip
  async function loadModelFile(path: string): Promise<Document | null> {
    // Normalize path (remove leading slash)
    const normalizedPath = path.startsWith('/') ? path.slice(1) : path;
    const file = zip.files[normalizedPath];
    if (!file) return null;
    const content = await file.async('string');
    return parser.parseFromString(content, 'application/xml');
  }

  // Parse model_settings.config to get extruder assignments
  // Maps: object ID -> default extruder, and (object ID, part ID) -> part-specific extruder
  const extruderMapById = new Map<string, number>();
  const partExtruderMap = new Map<string, number>(); // Key: "objectId:partId"
  const modelSettingsFile = zip.files['Metadata/model_settings.config'];
  if (modelSettingsFile) {
    try {
      const content = await modelSettingsFile.async('string');
      const doc = parser.parseFromString(content, 'application/xml');
      const objectElements = doc.getElementsByTagName('object');
      for (let i = 0; i < objectElements.length; i++) {
        const objEl = objectElements[i];
        const objectId = objEl.getAttribute('id');
        if (!objectId) continue;

        // Find object-level extruder
        const directMetadata = Array.from(objEl.children).filter(
          (el) => el.tagName === 'metadata' && el.getAttribute('key') === 'extruder'
        );
        if (directMetadata.length > 0) {
          const extruderVal = directMetadata[0].getAttribute('value');
          if (extruderVal) {
            extruderMapById.set(objectId, Math.max(0, parseInt(extruderVal, 10) - 1));
          }
        }

        // Find part-level extruders
        const partElements = objEl.getElementsByTagName('part');
        for (let j = 0; j < partElements.length; j++) {
          const partEl = partElements[j];
          const partId = partEl.getAttribute('id');
          if (!partId) continue;

          // Look for extruder in part's direct children
          const partMetadata = Array.from(partEl.children).filter(
            (el) => el.tagName === 'metadata' && el.getAttribute('key') === 'extruder'
          );
          if (partMetadata.length > 0) {
            const extruderVal = partMetadata[0].getAttribute('value');
            if (extruderVal) {
              partExtruderMap.set(`${objectId}:${partId}`, Math.max(0, parseInt(extruderVal, 10) - 1));
            }
          }
        }
      }
    } catch (e) {
      // Silently ignore model_settings.config parsing errors
    }
  }

  // Find the main 3D model file
  const mainModelPath = Object.keys(zip.files).find(
    (name) => name === '3D/3dmodel.model' || name.endsWith('/3dmodel.model')
  );

  if (!mainModelPath) {
    // Fallback: try to find any .model file
    const anyModelPath = Object.keys(zip.files).find((name) => name.endsWith('.model'));
    if (anyModelPath) {
      const doc = await loadModelFile(anyModelPath);
      if (doc) {
        const meshes = await parseMeshFromDoc(doc, 0);
        if (meshes.length > 0) {
          objects.set('1', { id: '1', meshes, defaultExtruder: 0 });
        }
      }
    }
    return { objects, buildItems };
  }

  const mainDoc = await loadModelFile(mainModelPath);
  if (!mainDoc) return { objects, buildItems };

  // Parse objects - Bambu Studio uses components to reference external files
  const objectElements = mainDoc.getElementsByTagName('object');
  for (let i = 0; i < objectElements.length; i++) {
    const objEl = objectElements[i];
    const objectId = objEl.getAttribute('id');
    if (!objectId) continue;

    // Get default extruder from model_settings.config map, falling back to attribute or default
    let defaultExtruder = extruderMapById.get(objectId) ?? -1;
    if (defaultExtruder < 0) {
      const extruderAttr = objEl.getAttribute('p:extruder') || objEl.getAttributeNS('http://schemas.microsoft.com/3dmanufacturing/production/2015/06', 'extruder') || '1';
      defaultExtruder = Math.max(0, parseInt(extruderAttr, 10) - 1);
    }

    const meshes: MeshData[] = [];

    // Check for direct mesh in this object
    const objMeshElements = objEl.getElementsByTagName('mesh');
    for (let j = 0; j < objMeshElements.length; j++) {
      const meshEl = objMeshElements[j];
      const vertices: number[] = [];
      const triangles: number[] = [];

      const vertexElements = meshEl.getElementsByTagName('vertex');
      for (let k = 0; k < vertexElements.length; k++) {
        const v = vertexElements[k];
        vertices.push(
          parseFloat(v.getAttribute('x') || '0'),
          parseFloat(v.getAttribute('y') || '0'),
          parseFloat(v.getAttribute('z') || '0')
        );
      }

      const triangleElements = meshEl.getElementsByTagName('triangle');
      for (let k = 0; k < triangleElements.length; k++) {
        const t = triangleElements[k];
        triangles.push(
          parseInt(t.getAttribute('v1') || '0'),
          parseInt(t.getAttribute('v2') || '0'),
          parseInt(t.getAttribute('v3') || '0')
        );
      }

      if (vertices.length > 0 && triangles.length > 0) {
        meshes.push({ vertices, triangles, extruder: defaultExtruder });
      }
    }

    // Check for component references (Bambu Studio style)
    const componentElements = objEl.getElementsByTagName('component');
    for (let j = 0; j < componentElements.length; j++) {
      const compEl = componentElements[j];
      // p:path attribute contains the external file reference
      const extPath = compEl.getAttribute('p:path') || compEl.getAttributeNS('http://schemas.microsoft.com/3dmanufacturing/production/2015/06', 'path');
      // objectid in component corresponds to part id in model_settings
      const compObjectId = compEl.getAttribute('objectid');

      if (extPath) {
        const extDoc = await loadModelFile(extPath);
        if (extDoc) {
          // Look up per-part extruder, falling back to object's default
          const partKey = compObjectId ? `${objectId}:${compObjectId}` : null;
          const compExtruder = partKey ? (partExtruderMap.get(partKey) ?? defaultExtruder) : defaultExtruder;

          const extMeshes = await parseMeshFromDoc(extDoc, compExtruder);

          // Apply component transform if present
          const compTransformStr = compEl.getAttribute('transform');
          const compTransform = parseTransform(compTransformStr);

          for (const mesh of extMeshes) {
            if (compTransformStr) {
              // Apply transform to vertices (in 3MF coordinate space, before Y/Z swap)
              const transformedVertices: number[] = [];
              for (let k = 0; k < mesh.vertices.length; k += 3) {
                const v = new THREE.Vector3(mesh.vertices[k], mesh.vertices[k + 1], mesh.vertices[k + 2]);
                v.applyMatrix4(compTransform);
                transformedVertices.push(v.x, v.y, v.z);
              }
              meshes.push({ vertices: transformedVertices, triangles: mesh.triangles, extruder: mesh.extruder });
            } else {
              meshes.push(mesh);
            }
          }
        }
      }
    }

    if (meshes.length > 0) {
      objects.set(objectId, { id: objectId, meshes, defaultExtruder });
    }
  }

  // Parse build items (placement on build plate)
  const buildElements = mainDoc.getElementsByTagName('build');
  if (buildElements.length > 0) {
    const itemElements = buildElements[0].getElementsByTagName('item');
    for (let i = 0; i < itemElements.length; i++) {
      const itemEl = itemElements[i];
      const objectId = itemEl.getAttribute('objectid');
      if (!objectId) continue;

      const transform = parseTransform(itemEl.getAttribute('transform'));
      buildItems.push({ objectId, transform });
    }
  }

  return { objects, buildItems };
}

function createGeometryFromMesh(mesh: MeshData): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();

  // Convert from 3MF Z-up to Three.js Y-up coordinate system
  // 3MF: X right, Y back, Z up -> Three.js: X right, Y up, Z forward
  const positions = new Float32Array(mesh.vertices.length);
  for (let i = 0; i < mesh.vertices.length; i += 3) {
    positions[i] = mesh.vertices[i];       // X stays X
    positions[i + 1] = mesh.vertices[i + 2]; // Y becomes Z (up)
    positions[i + 2] = mesh.vertices[i + 1]; // Z becomes Y
  }

  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(mesh.triangles);

  // Compute normals
  geometry.computeVertexNormals();

  return geometry;
}

export function ModelViewer({ url, buildVolume = { x: 256, y: 256, z: 256 }, filamentColors, className = '' }: ModelViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1a1a);
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000);
    camera.position.set(150, 150, 150);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controlsRef.current = controls;

    // Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);

    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
    directionalLight.position.set(100, 100, 100);
    scene.add(directionalLight);

    const directionalLight2 = new THREE.DirectionalLight(0xffffff, 0.4);
    directionalLight2.position.set(-100, 50, -100);
    scene.add(directionalLight2);

    // Grid - use the larger dimension for the grid size
    const gridSize = Math.max(buildVolume.x, buildVolume.y);
    const gridDivisions = Math.ceil(gridSize / 16);
    const gridHelper = new THREE.GridHelper(gridSize, gridDivisions, 0x444444, 0x333333);
    scene.add(gridHelper);

    // Build plate indicator
    const plateGeometry = new THREE.PlaneGeometry(buildVolume.x, buildVolume.y);
    const plateMaterial = new THREE.MeshBasicMaterial({
      color: 0x00ae42,
      transparent: true,
      opacity: 0.15,
      side: THREE.DoubleSide,
    });
    const plate = new THREE.Mesh(plateGeometry, plateMaterial);
    plate.rotation.x = -Math.PI / 2;
    plate.position.y = -0.5; // Slightly below Y=0 so models sit on top
    scene.add(plate);

    // Animation loop - keep it simple for reliability
    let animationId: number;
    const animate = () => {
      animationId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    // Load 3MF
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error('Failed to load file');
        return res.arrayBuffer();
      })
      .then(parse3MF)
      .then(({ objects, buildItems }) => {
        if (objects.size === 0) {
          throw new Error('No meshes found in 3MF file');
        }

        // Create materials for each extruder color
        const getMaterial = (extruder: number): THREE.MeshPhongMaterial => {
          const defaultColor = '#00ae42';
          const colorStr = filamentColors?.[extruder] || defaultColor;
          // Convert hex color string to THREE.js color
          const color = new THREE.Color(colorStr);
          return new THREE.MeshPhongMaterial({
            color,
            shininess: 30,
            flatShading: false,
          });
        };

        const group = new THREE.Group();
        // Group geometries by extruder index (using per-mesh extruder)
        const geometriesByExtruder = new Map<number, THREE.BufferGeometry[]>();

        // If we have build items, use them for positioning
        if (buildItems.length > 0) {
          for (const item of buildItems) {
            const objectData = objects.get(item.objectId);
            if (!objectData) continue;

            for (const meshData of objectData.meshes) {
              // Use mesh's extruder, or item override, or object default
              const extruder = item.extruder ?? meshData.extruder;

              // Apply build transform to vertices in 3MF space BEFORE coordinate conversion
              const transformedVertices: number[] = [];
              for (let k = 0; k < meshData.vertices.length; k += 3) {
                const v = new THREE.Vector3(
                  meshData.vertices[k],
                  meshData.vertices[k + 1],
                  meshData.vertices[k + 2]
                );
                v.applyMatrix4(item.transform);
                transformedVertices.push(v.x, v.y, v.z);
              }
              // Now create geometry with coordinate conversion
              const geometry = createGeometryFromMesh({
                vertices: transformedVertices,
                triangles: meshData.triangles,
                extruder: extruder,
              });

              if (!geometriesByExtruder.has(extruder)) {
                geometriesByExtruder.set(extruder, []);
              }
              geometriesByExtruder.get(extruder)!.push(geometry);
            }
          }
        } else {
          // Fallback: just add all objects without transforms
          for (const objectData of objects.values()) {
            for (const meshData of objectData.meshes) {
              // Use per-mesh extruder
              const extruder = meshData.extruder;
              const geometry = createGeometryFromMesh(meshData);
              if (!geometriesByExtruder.has(extruder)) {
                geometriesByExtruder.set(extruder, []);
              }
              geometriesByExtruder.get(extruder)!.push(geometry);
            }
          }
        }

        // Create meshes for each extruder group
        for (const [extruder, geometries] of geometriesByExtruder) {
          if (geometries.length === 0) continue;

          const mergedGeometry = geometries.length === 1
            ? geometries[0]
            : mergeGeometries(geometries, false);

          if (mergedGeometry) {
            const material = getMaterial(extruder);
            const mesh = new THREE.Mesh(mergedGeometry, material);
            group.add(mesh);
          }

          // Dispose individual geometries if merged
          if (geometries.length > 1) {
            for (const geom of geometries) {
              geom.dispose();
            }
          }
        }

        // Get bounding box to position model
        const box = new THREE.Box3().setFromObject(group);
        const center = box.getCenter(new THREE.Vector3());

        // Always place models on the build plate (Y=0)
        group.position.y = -box.min.y;

        // For models without build transforms, also center X/Z
        if (buildItems.length === 0) {
          group.position.x = -center.x;
          group.position.z = -center.z;
        }

        scene.add(group);

        // Recalculate bounding box after positioning
        const finalBox = new THREE.Box3().setFromObject(group);
        const finalCenter = finalBox.getCenter(new THREE.Vector3());
        const finalSize = finalBox.getSize(new THREE.Vector3());

        // Adjust camera to fit model
        const maxDim = Math.max(finalSize.x, finalSize.y, finalSize.z);
        const cameraDistance = maxDim * 1.8;
        camera.position.set(
          finalCenter.x + cameraDistance * 0.7,
          finalCenter.y + cameraDistance * 0.5,
          finalCenter.z + cameraDistance * 0.7
        );
        controls.target.copy(finalCenter);
        controls.update();

        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });

    // Handle resize
    const handleResize = () => {
      if (!container) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animationId);
      controls.dispose();
      renderer.dispose();
      container.removeChild(renderer.domElement);
    };
  }, [url, buildVolume, filamentColors]);

  const resetView = () => {
    if (cameraRef.current && controlsRef.current) {
      cameraRef.current.position.set(150, 150, 150);
      controlsRef.current.target.set(0, 50, 0);
      controlsRef.current.update();
    }
  };

  const zoom = (factor: number) => {
    if (cameraRef.current) {
      cameraRef.current.position.multiplyScalar(factor);
    }
  };

  return (
    <div className={`relative ${className}`}>
      <div ref={containerRef} className="w-full h-full min-h-[400px]" />

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
          <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/80">
          <p className="text-red-400">{error}</p>
        </div>
      )}

      {!loading && !error && (
        <div className="absolute bottom-4 right-4 flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => zoom(0.8)}>
            <ZoomIn className="w-4 h-4" />
          </Button>
          <Button variant="secondary" size="sm" onClick={() => zoom(1.25)}>
            <ZoomOut className="w-4 h-4" />
          </Button>
          <Button variant="secondary" size="sm" onClick={resetView}>
            <RotateCcw className="w-4 h-4" />
          </Button>
        </div>
      )}
    </div>
  );
}
