import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import JSZip from 'jszip';
import { Loader2, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import { Button } from './Button';
import { getAuthToken } from '../api/client';

interface BuildVolume {
  x: number;
  y: number;
  z: number;
}

interface ModelViewerProps {
  url: string;
  fileType?: string;
  buildVolume?: BuildVolume;
  filamentColors?: string[];
  selectedPlateId?: number | null;
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
  plateId?: number | null;
}

interface BuildItem {
  objectId: string;
  transform: THREE.Matrix4;
  extruder?: number; // Can override object's extruder
  plateId?: number | null;
}

interface Parsed3MFData {
  objects: Map<string, ObjectData>;
  buildItems: BuildItem[];
  plateBounds: Map<number, { minX: number; minY: number; maxX: number; maxY: number }>;
  plateOffsets: Map<number, { offsetX: number; offsetY: number }>;
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

function parsePlateIdFromAttributes(element: Element): number | null {
  const plateAttribute = Array.from(element.attributes).find((attr) => {
    const name = attr.name.toLowerCase();
    return (
      name === 'plate_id' ||
      name === 'plater_id' ||
      name === 'plateid' ||
      name === 'platerid' ||
      name.endsWith(':plate_id') ||
      name.endsWith(':plater_id')
    );
  });

  if (!plateAttribute?.value) return null;
  const parsed = Number.parseInt(plateAttribute.value, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

async function parse3MF(arrayBuffer: ArrayBuffer): Promise<Parsed3MFData> {
  let zip: JSZip;
  try {
    zip = await JSZip.loadAsync(arrayBuffer);
  } catch {
    throw new Error('Unsupported file format');
  }
  const objects = new Map<string, ObjectData>();
  const buildItems: BuildItem[] = [];
  const plateBounds = new Map<number, { minX: number; minY: number; maxX: number; maxY: number }>();
  const plateOffsets = new Map<number, { offsetX: number; offsetY: number }>();
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
  const objectNameById = new Map<string, string>();
  const plateAssignmentsByObjectId = new Map<string, number>();
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

        // Find object-level extruder + name
        const directMetadata = Array.from(objEl.children).filter(
          (el) => el.tagName === 'metadata' && el.getAttribute('key') === 'extruder'
        );
        if (directMetadata.length > 0) {
          const extruderVal = directMetadata[0].getAttribute('value');
          if (extruderVal) {
            extruderMapById.set(objectId, Math.max(0, parseInt(extruderVal, 10) - 1));
          }
        }

        const nameMetadata = Array.from(objEl.children).find(
          (el) => el.tagName === 'metadata' && el.getAttribute('key') === 'name'
        );
        const objectName = nameMetadata?.getAttribute('value');
        if (objectName) {
          objectNameById.set(objectId, objectName);
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

      // Parse plate -> object assignments
      const plateElements = doc.getElementsByTagName('plate');
      for (let i = 0; i < plateElements.length; i++) {
        const plateEl = plateElements[i];
        let plateId: number | null = null;
        const metadataElements = plateEl.getElementsByTagName('metadata');
        let plateOffsetX = 0;
        let plateOffsetY = 0;
        for (let j = 0; j < metadataElements.length; j++) {
          const metaEl = metadataElements[j];
          const key = metaEl.getAttribute('key');
          if (key === 'plater_id' || key === 'plate_id') {
            const value = metaEl.getAttribute('value');
            if (value) {
              const parsed = Number.parseInt(value, 10);
              if (Number.isFinite(parsed)) {
                plateId = parsed;
              }
            }
          } else if (key === 'pos_x') {
            const value = metaEl.getAttribute('value');
            const parsed = value ? Number.parseFloat(value) : Number.NaN;
            if (Number.isFinite(parsed)) {
              plateOffsetX = parsed;
            }
          } else if (key === 'pos_y') {
            const value = metaEl.getAttribute('value');
            const parsed = value ? Number.parseFloat(value) : Number.NaN;
            if (Number.isFinite(parsed)) {
              plateOffsetY = parsed;
            }
          }
        }
        if (plateId == null) continue;
        if (plateOffsetX !== 0 || plateOffsetY !== 0) {
          plateOffsets.set(plateId, { offsetX: plateOffsetX, offsetY: plateOffsetY });
        }

        const modelInstances = plateEl.getElementsByTagName('model_instance');
        for (let j = 0; j < modelInstances.length; j++) {
          const instanceEl = modelInstances[j];
          const instanceMetadata = instanceEl.getElementsByTagName('metadata');
          for (let k = 0; k < instanceMetadata.length; k++) {
            const metaEl = instanceMetadata[k];
            if (metaEl.getAttribute('key') === 'object_id') {
              const value = metaEl.getAttribute('value');
              if (value) {
                plateAssignmentsByObjectId.set(value, plateId);
              }
            }
          }
        }
      }
    } catch {
      // Silently ignore model_settings.config parsing errors
    }
  }

  // Parse plate_*.json for plate assignments by object name (source-only / unsliced files)
  const plateAssignmentsByName = new Map<string, number>();
  const plateJsonNames = Object.keys(zip.files).filter(
    (name) => name.startsWith('Metadata/plate_') && name.endsWith('.json')
  );
  for (const name of plateJsonNames) {
    const match = name.match(/^Metadata\/plate_(\d+)\.json$/);
    if (!match) continue;
    const plateIndex = Number.parseInt(match[1], 10);
    if (!Number.isFinite(plateIndex)) continue;
    try {
      const payload = await zip.files[name].async('string');
      const json = JSON.parse(payload) as { bbox_objects?: Array<{ name?: string }>; bbox_all?: number[] };
      const objectsList = json.bbox_objects ?? [];
      for (const entry of objectsList) {
        if (entry?.name) {
          plateAssignmentsByName.set(entry.name, plateIndex);
        }
      }
      if (Array.isArray(json.bbox_all) && json.bbox_all.length >= 4) {
        const [minX, minY, maxX, maxY] = json.bbox_all;
        if ([minX, minY, maxX, maxY].every((value) => Number.isFinite(value))) {
          plateBounds.set(plateIndex, { minX, minY, maxX, maxY });
        }
      }
    } catch {
      // Ignore plate json parsing errors
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
    return { objects, buildItems, plateBounds, plateOffsets };
  }

  const mainDoc = await loadModelFile(mainModelPath);
  if (!mainDoc) return { objects, buildItems, plateBounds, plateOffsets };

  // Parse objects - Bambu Studio uses components to reference external files
  const objectElements = mainDoc.getElementsByTagName('object');
  for (let i = 0; i < objectElements.length; i++) {
    const objEl = objectElements[i];
    const objectId = objEl.getAttribute('id');
    if (!objectId) continue;

    const objectPlateId = parsePlateIdFromAttributes(objEl) ?? plateAssignmentsByObjectId.get(objectId) ?? null;

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
      objects.set(objectId, { id: objectId, meshes, defaultExtruder, plateId: objectPlateId });
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
      const itemPlateId = parsePlateIdFromAttributes(itemEl);
      const objectPlateId = objects.get(objectId)?.plateId ?? null;
      const objectName = objectNameById.get(objectId);
      const namePlateId = objectName ? plateAssignmentsByName.get(objectName) ?? null : null;
      buildItems.push({ objectId, transform, plateId: itemPlateId ?? objectPlateId ?? namePlateId ?? null });
    }
  }

  return { objects, buildItems, plateBounds, plateOffsets };
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

function disposeGroup(group: THREE.Group) {
  group.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      child.geometry.dispose();
      if (Array.isArray(child.material)) {
        for (const material of child.material) {
          material.dispose();
        }
      } else {
        child.material.dispose();
      }
    }
  });
}

function buildModelGroup(
  parsedData: Parsed3MFData,
  selectedPlateId: number | null,
  filamentColors?: string[],
): THREE.Group {
  const { objects, buildItems } = parsedData;
  const group = new THREE.Group();

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

  // Group geometries by extruder index (using per-mesh extruder)
  const geometriesByExtruder = new Map<number, THREE.BufferGeometry[]>();

  const hasPlateAssignments = buildItems.some((item) => item.plateId != null);
  const plateFilteredItems = selectedPlateId == null || !hasPlateAssignments
    ? buildItems
    : buildItems.filter((item) => item.plateId === selectedPlateId);
  const activeBuildItems = plateFilteredItems.length > 0 ? plateFilteredItems : buildItems;

  // If we have build items, use them for positioning
  if (activeBuildItems.length > 0) {
    for (const item of activeBuildItems) {
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

  return group;
}

export function ModelViewer({
  url,
  fileType,
  buildVolume = { x: 256, y: 256, z: 256 },
  filamentColors,
  selectedPlateId = null,
  className = '',
}: ModelViewerProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const modelGroupRef = useRef<THREE.Group | null>(null);
  const plateRef = useRef<THREE.Mesh | null>(null);
  const gridRef = useRef<THREE.GridHelper | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [parsedData, setParsedData] = useState<Parsed3MFData | null>(null);
  const [stlGeometry, setStlGeometry] = useState<THREE.BufferGeometry | null>(null);

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
    gridRef.current = gridHelper;

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
    plateRef.current = plate;

    // Animation loop - keep it simple for reliability
    let animationId: number;
    const animate = () => {
      animationId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    setLoading(true);
    setError(null);
    setParsedData(null);
    setStlGeometry(null);

    const normalizedType = (fileType || url.split('?')[0].split('.').pop() || '').toLowerCase();

    // Build auth headers for fetch
    const headers: HeadersInit = {};
    const token = getAuthToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    if (normalizedType === 'stl') {
      fetch(url, { headers })
        .then((res) => {
          if (!res.ok) throw new Error(t('modelViewer.errors.failedToLoad'));
          return res.arrayBuffer();
        })
        .then((buffer) => {
          const loader = new STLLoader();
          const geometry = loader.parse(buffer);
          geometry.computeVertexNormals();
          geometry.rotateX(-Math.PI / 2);
          setStlGeometry(geometry);
        })
        .catch((err) => {
          setError(err.message);
          setLoading(false);
        });
    } else if (normalizedType === '3mf') {
      fetch(url, { headers })
        .then((res) => {
          if (!res.ok) throw new Error(t('modelViewer.errors.failedToLoad'));
          return res.arrayBuffer();
        })
        .then(parse3MF)
        .then((parsed) => {
          if (parsed.objects.size === 0) {
            throw new Error(t('modelViewer.errors.noMeshes'));
          }
          setParsedData(parsed);
        })
        .catch((err) => {
          setError(err.message);
          setLoading(false);
        });
    } else {
      setError(t('modelViewer.errors.unsupportedFormat'));
      setLoading(false);
    }

    // Handle resize (window + container)
    const handleResize = () => {
      if (!container) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (w === 0 || h === 0) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', handleResize);
    const resizeObserver = new ResizeObserver(() => {
      handleResize();
    });
    resizeObserver.observe(container);

    return () => {
      window.removeEventListener('resize', handleResize);
      resizeObserver.disconnect();
      cancelAnimationFrame(animationId);
      controls.dispose();
      renderer.dispose();
      container.removeChild(renderer.domElement);
      modelGroupRef.current = null;
      plateRef.current = null;
      gridRef.current = null;
    };
  }, [url, buildVolume, fileType, t]);

  useEffect(() => {
    if (!sceneRef.current || !cameraRef.current || !controlsRef.current) return;
    if (!parsedData && !stlGeometry) return;

    if (modelGroupRef.current) {
      sceneRef.current.remove(modelGroupRef.current);
      disposeGroup(modelGroupRef.current);
    }

    const isStlModel = !!stlGeometry;
    const group = isStlModel
      ? (() => {
          const materialColor = filamentColors?.[0] || '#00ae42';
          const material = new THREE.MeshPhongMaterial({ color: new THREE.Color(materialColor), shininess: 30 });
          const mesh = new THREE.Mesh(stlGeometry!, material);
          const stlGroup = new THREE.Group();
          stlGroup.add(mesh);
          return stlGroup;
        })()
      : buildModelGroup(parsedData!, selectedPlateId ?? null, filamentColors);
    modelGroupRef.current = group;
    sceneRef.current.add(group);

    // Get bounding box to position model
    const box = new THREE.Box3().setFromObject(group);
    const center = box.getCenter(new THREE.Vector3());

    // Always place models on the build plate (Y=0)
    group.position.y = -box.min.y;

    const selectedPlateBounds = (!isStlModel && selectedPlateId != null && parsedData!.buildItems.length > 0)
      ? parsedData!.plateBounds.get(selectedPlateId)
      : undefined;
    const selectedPlateOffset = (!isStlModel && selectedPlateId != null)
      ? parsedData!.plateOffsets.get(selectedPlateId)
      : undefined;
    const shouldCenterOnPlate = isStlModel
      || parsedData!.buildItems.length === 0
      || (selectedPlateId != null && !selectedPlateBounds && !selectedPlateOffset);
    const centerOffsetX = shouldCenterOnPlate ? -center.x : 0;
    const centerOffsetZ = shouldCenterOnPlate ? -center.z : 0;

    let plateOffsetX = 0;
    let plateOffsetZ = 0;
    if (!isStlModel && selectedPlateId != null && parsedData!.buildItems.length > 0 && selectedPlateBounds) {
      const plateBox = new THREE.Box3().setFromObject(group);
      plateOffsetX = plateBox.min.x - selectedPlateBounds.minX;
      plateOffsetZ = plateBox.min.z - selectedPlateBounds.minY;
    }

    const plateCenterX = buildVolume.x / 2;
    const plateCenterZ = buildVolume.y / 2;

    if (!isStlModel && selectedPlateId != null && parsedData!.buildItems.length > 0 && selectedPlateBounds) {
      group.position.x = centerOffsetX - plateOffsetX;
      group.position.z = centerOffsetZ - plateOffsetZ;
    } else if (!isStlModel && selectedPlateId != null && selectedPlateOffset) {
      group.position.x = centerOffsetX + (plateCenterX - selectedPlateOffset.offsetX);
      group.position.z = centerOffsetZ + (plateCenterZ - selectedPlateOffset.offsetY);
    } else if (shouldCenterOnPlate) {
      group.position.x = centerOffsetX + plateCenterX;
      group.position.z = centerOffsetZ + plateCenterZ;
    } else {
      group.position.x = centerOffsetX;
      group.position.z = centerOffsetZ;
    }

    if (plateRef.current) {
      plateRef.current.position.x = plateCenterX;
      plateRef.current.position.z = plateCenterZ;
    }

    if (gridRef.current) {
      gridRef.current.position.x = plateCenterX;
      gridRef.current.position.z = plateCenterZ;
    }

    // Recalculate bounding box after positioning
    const finalBox = new THREE.Box3().setFromObject(group);
    const finalCenter = finalBox.getCenter(new THREE.Vector3());
    const finalSize = finalBox.getSize(new THREE.Vector3());

    // Adjust camera to fit model
    const maxDim = Math.max(finalSize.x, finalSize.y, finalSize.z);
    const cameraDistance = maxDim * 1.8;
    cameraRef.current.position.set(
      finalCenter.x + cameraDistance * 0.7,
      finalCenter.y + cameraDistance * 0.5,
      finalCenter.z + cameraDistance * 0.7
    );
    controlsRef.current.target.copy(finalCenter);
    controlsRef.current.update();

    setLoading(false);
  }, [parsedData, stlGeometry, selectedPlateId, filamentColors, buildVolume]);

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
