import { useState, useEffect } from 'react';
import { ChevronDown, ChevronRight, Play, Copy, Loader2, ExternalLink, AlertCircle, CheckCircle } from 'lucide-react';
import { Card, CardContent } from './Card';
import { Button } from './Button';

interface OpenAPISchema {
  paths: Record<string, Record<string, EndpointSpec>>;
  components?: {
    schemas?: Record<string, SchemaSpec>;
  };
}

interface EndpointSpec {
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: ParameterSpec[];
  requestBody?: {
    content?: {
      'application/json'?: {
        schema?: SchemaSpec;
      };
    };
  };
  responses?: Record<string, ResponseSpec>;
}

interface ParameterSpec {
  name: string;
  in: 'path' | 'query' | 'header';
  required?: boolean;
  description?: string;
  schema?: {
    type?: string;
    default?: unknown;
    enum?: string[];
  };
}

interface SchemaSpec {
  type?: string;
  properties?: Record<string, SchemaSpec>;
  required?: string[];
  items?: SchemaSpec;
  $ref?: string;
  allOf?: SchemaSpec[];
  anyOf?: SchemaSpec[];
  oneOf?: SchemaSpec[];
  default?: unknown;
  description?: string;
  enum?: string[];
  example?: unknown;
}

interface ResponseSpec {
  description?: string;
  content?: {
    'application/json'?: {
      schema?: SchemaSpec;
    };
  };
}

interface APIResponse {
  status: number;
  statusText: string;
  headers: Record<string, string>;
  body: unknown;
  duration: number;
}

const METHOD_COLORS: Record<string, string> = {
  get: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  post: 'bg-green-500/20 text-green-400 border-green-500/30',
  put: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  patch: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  delete: 'bg-red-500/20 text-red-400 border-red-500/30',
};

function resolveRef(schema: OpenAPISchema, ref: string): SchemaSpec {
  // Parse $ref like "#/components/schemas/PrinterCreate"
  const parts = ref.replace('#/', '').split('/');
  let current: unknown = schema;
  for (const part of parts) {
    current = (current as Record<string, unknown>)[part];
  }
  return current as SchemaSpec;
}

function getSchemaExample(schema: OpenAPISchema, spec: SchemaSpec, depth = 0): unknown {
  if (depth > 5) return '...';

  if (spec.$ref) {
    return getSchemaExample(schema, resolveRef(schema, spec.$ref), depth + 1);
  }

  if (spec.allOf) {
    const merged: Record<string, unknown> = {};
    for (const sub of spec.allOf) {
      const subExample = getSchemaExample(schema, sub, depth + 1);
      if (typeof subExample === 'object' && subExample !== null) {
        Object.assign(merged, subExample);
      }
    }
    return merged;
  }

  if (spec.example !== undefined) return spec.example;
  if (spec.default !== undefined) return spec.default;

  switch (spec.type) {
    case 'string':
      if (spec.enum) return spec.enum[0];
      return 'string';
    case 'integer':
    case 'number':
      return 0;
    case 'boolean':
      return false;
    case 'array':
      return spec.items ? [getSchemaExample(schema, spec.items, depth + 1)] : [];
    case 'object':
      if (spec.properties) {
        const obj: Record<string, unknown> = {};
        for (const [key, propSpec] of Object.entries(spec.properties)) {
          obj[key] = getSchemaExample(schema, propSpec, depth + 1);
        }
        return obj;
      }
      return {};
    default:
      return null;
  }
}

interface EndpointItemProps {
  path: string;
  method: string;
  spec: EndpointSpec;
  schema: OpenAPISchema;
  apiKey: string;
}

function EndpointItem({ path, method, spec, schema, apiKey }: EndpointItemProps) {
  const [expanded, setExpanded] = useState(false);
  const [params, setParams] = useState<Record<string, string>>({});
  const [bodyText, setBodyText] = useState('');
  const [response, setResponse] = useState<APIResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  // Initialize params with defaults
  useEffect(() => {
    if (expanded && spec.parameters) {
      const defaults: Record<string, string> = {};
      for (const param of spec.parameters) {
        if (param.schema?.default !== undefined) {
          defaults[param.name] = String(param.schema.default);
        }
      }
      setParams(prev => ({ ...defaults, ...prev }));
    }
  }, [expanded, spec.parameters]);

  // Initialize body with example
  useEffect(() => {
    if (expanded && spec.requestBody?.content?.['application/json']?.schema && !bodyText) {
      const bodySchema = spec.requestBody.content['application/json'].schema;
      const example = getSchemaExample(schema, bodySchema);
      setBodyText(JSON.stringify(example, null, 2));
    }
  }, [expanded, spec.requestBody, schema, bodyText]);

  // Check for missing required parameters
  const getMissingParams = () => {
    const missing: string[] = [];
    for (const param of spec.parameters || []) {
      if (param.in === 'path' || param.required) {
        const value = params[param.name];
        if (value === undefined || value === '') {
          missing.push(param.name);
        }
      }
    }
    return missing;
  };

  const missingParams = getMissingParams();

  const executeRequest = async () => {
    if (missingParams.length > 0) {
      setResponse({
        status: 0,
        statusText: 'Validation Error',
        headers: {},
        body: `Missing required parameters: ${missingParams.join(', ')}`,
        duration: 0,
      });
      return;
    }

    setLoading(true);
    setResponse(null);

    try {
      // Build URL with path and query params
      let url = path;
      const queryParams = new URLSearchParams();

      for (const param of spec.parameters || []) {
        const value = params[param.name];
        if (value !== undefined && value !== '') {
          if (param.in === 'path') {
            url = url.replace(`{${param.name}}`, encodeURIComponent(value));
          } else if (param.in === 'query') {
            queryParams.append(param.name, value);
          }
        }
      }

      const queryString = queryParams.toString();
      // OpenAPI paths already include /api/v1 prefix
      const fullUrl = `${url}${queryString ? `?${queryString}` : ''}`;

      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };

      if (apiKey) {
        headers['X-API-Key'] = apiKey;
      }

      const options: RequestInit = {
        method: method.toUpperCase(),
        headers,
      };

      if (['post', 'put', 'patch'].includes(method) && bodyText) {
        options.body = bodyText;
      }

      const startTime = performance.now();
      const res = await fetch(fullUrl, options);
      const duration = Math.round(performance.now() - startTime);

      const responseHeaders: Record<string, string> = {};
      res.headers.forEach((value, key) => {
        responseHeaders[key] = value;
      });

      let body: unknown;
      const contentType = res.headers.get('content-type');
      if (contentType?.includes('application/json')) {
        body = await res.json();
      } else {
        body = await res.text();
      }

      setResponse({
        status: res.status,
        statusText: res.statusText,
        headers: responseHeaders,
        body,
        duration,
      });
    } catch (err) {
      setResponse({
        status: 0,
        statusText: 'Network Error',
        headers: {},
        body: err instanceof Error ? err.message : 'Unknown error',
        duration: 0,
      });
    } finally {
      setLoading(false);
    }
  };

  const copyResponse = async () => {
    if (response) {
      const text = typeof response.body === 'string'
        ? response.body
        : JSON.stringify(response.body, null, 2);
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // Fallback for non-HTTPS
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    }
  };

  const pathParams = (spec.parameters || []).filter(p => p.in === 'path');
  const queryParamsSpec = (spec.parameters || []).filter(p => p.in === 'query');
  const hasBody = ['post', 'put', 'patch'].includes(method) && spec.requestBody;

  return (
    <div className="border border-bambu-dark-tertiary rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 p-3 hover:bg-bambu-dark-tertiary/50 transition-colors text-left"
      >
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-bambu-gray flex-shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-bambu-gray flex-shrink-0" />
        )}
        <span className={`px-2 py-0.5 text-xs font-mono font-semibold uppercase rounded border ${METHOD_COLORS[method] || 'bg-gray-500/20 text-gray-400'}`}>
          {method}
        </span>
        <code className="text-sm text-white font-mono flex-1 truncate">{path}</code>
        {spec.summary && (
          <span className="text-sm text-bambu-gray truncate max-w-[40%]">{spec.summary}</span>
        )}
      </button>

      {expanded && (
        <div className="border-t border-bambu-dark-tertiary p-4 space-y-4 bg-bambu-dark/50">
          {spec.description && (
            <p className="text-sm text-bambu-gray">{spec.description}</p>
          )}

          {/* Path Parameters */}
          {pathParams.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium text-white">Path Parameters</h4>
              <div className="space-y-2">
                {pathParams.map(param => (
                  <div key={param.name} className="flex items-center gap-2">
                    <label className="text-sm text-bambu-gray w-32 flex-shrink-0">
                      {param.name}
                      {param.required && <span className="text-red-400 ml-1">*</span>}
                    </label>
                    <input
                      type="text"
                      value={params[param.name] || ''}
                      onChange={(e) => setParams(p => ({ ...p, [param.name]: e.target.value }))}
                      placeholder={param.description || param.schema?.type || 'value'}
                      className="flex-1 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm font-mono focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Query Parameters */}
          {queryParamsSpec.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium text-white">Query Parameters</h4>
              <div className="space-y-2">
                {queryParamsSpec.map(param => (
                  <div key={param.name} className="flex items-center gap-2">
                    <label className="text-sm text-bambu-gray w-32 flex-shrink-0">
                      {param.name}
                      {param.required && <span className="text-red-400 ml-1">*</span>}
                    </label>
                    {param.schema?.enum ? (
                      <select
                        value={params[param.name] || ''}
                        onChange={(e) => setParams(p => ({ ...p, [param.name]: e.target.value }))}
                        className="flex-1 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                      >
                        <option value="">-- Select --</option>
                        {param.schema.enum.map(opt => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={params[param.name] || ''}
                        onChange={(e) => setParams(p => ({ ...p, [param.name]: e.target.value }))}
                        placeholder={param.description || param.schema?.type || 'value'}
                        className="flex-1 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm font-mono focus:border-bambu-green focus:outline-none"
                      />
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Request Body */}
          {hasBody && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium text-white">Request Body</h4>
              <textarea
                value={bodyText}
                onChange={(e) => setBodyText(e.target.value)}
                rows={8}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm font-mono focus:border-bambu-green focus:outline-none resize-y"
                placeholder="JSON request body..."
              />
            </div>
          )}

          {/* Execute Button */}
          <div className="flex items-center gap-2">
            <Button onClick={executeRequest} disabled={loading}>
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              Execute
            </Button>
            {missingParams.length > 0 && (
              <span className="text-xs text-yellow-400 flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                Fill in: {missingParams.join(', ')}
              </span>
            )}
          </div>

          {/* Response */}
          {response && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-white flex items-center gap-2">
                  Response
                  <span className={`px-2 py-0.5 text-xs rounded ${
                    response.status >= 200 && response.status < 300
                      ? 'bg-green-500/20 text-green-400'
                      : response.status >= 400
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-yellow-500/20 text-yellow-400'
                  }`}>
                    {response.status} {response.statusText}
                  </span>
                  <span className="text-xs text-bambu-gray">{response.duration}ms</span>
                </h4>
                <Button variant="secondary" size="sm" onClick={copyResponse}>
                  {copied ? (
                    <CheckCircle className="w-3 h-3 text-green-400" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </Button>
              </div>
              <pre className="p-3 bg-bambu-dark rounded-lg text-sm font-mono text-white overflow-auto max-h-96 border border-bambu-dark-tertiary">
                {typeof response.body === 'string'
                  ? response.body
                  : JSON.stringify(response.body, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface APIBrowserProps {
  apiKey?: string;
}

export function APIBrowser({ apiKey = '' }: APIBrowserProps) {
  const [schema, setSchema] = useState<OpenAPISchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    async function fetchSchema() {
      try {
        const res = await fetch('/openapi.json');
        if (!res.ok) throw new Error('Failed to fetch OpenAPI schema');
        const data = await res.json();
        setSchema(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchSchema();
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (error || !schema) {
    return (
      <Card>
        <CardContent className="py-8">
          <div className="text-center text-red-400">
            <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>Failed to load API schema</p>
            <p className="text-sm text-bambu-gray mt-1">{error}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // Group endpoints by tag
  const endpointsByTag: Record<string, Array<{ path: string; method: string; spec: EndpointSpec }>> = {};

  for (const [path, methods] of Object.entries(schema.paths)) {
    for (const [method, spec] of Object.entries(methods)) {
      if (method === 'parameters') continue; // Skip path-level parameters

      const tags = spec.tags || ['Other'];
      for (const tag of tags) {
        if (!endpointsByTag[tag]) {
          endpointsByTag[tag] = [];
        }
        endpointsByTag[tag].push({ path, method, spec });
      }
    }
  }

  // Filter endpoints based on search
  const filteredTags = Object.entries(endpointsByTag)
    .map(([tag, endpoints]) => {
      if (!searchQuery) return { tag, endpoints };

      const filtered = endpoints.filter(({ path, method, spec }) => {
        const searchLower = searchQuery.toLowerCase();
        return (
          path.toLowerCase().includes(searchLower) ||
          method.toLowerCase().includes(searchLower) ||
          (spec.summary?.toLowerCase() || '').includes(searchLower) ||
          (spec.description?.toLowerCase() || '').includes(searchLower)
        );
      });

      return { tag, endpoints: filtered };
    })
    .filter(({ endpoints }) => endpoints.length > 0)
    .sort((a, b) => a.tag.localeCompare(b.tag));

  const toggleTag = (tag: string) => {
    setExpandedTags(prev => {
      const next = new Set(prev);
      if (next.has(tag)) {
        next.delete(tag);
      } else {
        next.add(tag);
      }
      return next;
    });
  };

  const expandAll = () => {
    setExpandedTags(new Set(filteredTags.map(t => t.tag)));
  };

  const collapseAll = () => {
    setExpandedTags(new Set());
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search endpoints..."
            className="w-full max-w-md px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={expandAll}>
            Expand All
          </Button>
          <Button variant="secondary" size="sm" onClick={collapseAll}>
            Collapse All
          </Button>
          <a
            href="/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-sm text-bambu-green hover:underline"
          >
            <ExternalLink className="w-4 h-4" />
            Swagger UI
          </a>
        </div>
      </div>

      {/* Endpoint count */}
      <p className="text-sm text-bambu-gray">
        {filteredTags.reduce((acc, t) => acc + t.endpoints.length, 0)} endpoints in {filteredTags.length} categories
      </p>

      {/* Endpoints by Tag */}
      <div className="space-y-3">
        {filteredTags.map(({ tag, endpoints }) => (
          <Card key={tag}>
            <button
              onClick={() => toggleTag(tag)}
              className="w-full flex items-center justify-between p-4 hover:bg-bambu-dark-tertiary/30 transition-colors text-left"
            >
              <div className="flex items-center gap-2">
                {expandedTags.has(tag) ? (
                  <ChevronDown className="w-5 h-5 text-bambu-gray" />
                ) : (
                  <ChevronRight className="w-5 h-5 text-bambu-gray" />
                )}
                <h3 className="text-base font-semibold text-white capitalize">{tag.replace(/-/g, ' ')}</h3>
                <span className="text-xs bg-bambu-dark-tertiary px-2 py-0.5 rounded-full text-bambu-gray">
                  {endpoints.length}
                </span>
              </div>
            </button>

            {expandedTags.has(tag) && (
              <CardContent className="pt-0 space-y-2">
                {endpoints.map(({ path, method, spec }) => (
                  <EndpointItem
                    key={`${method}-${path}`}
                    path={path}
                    method={method}
                    spec={spec}
                    schema={schema}
                    apiKey={apiKey}
                  />
                ))}
              </CardContent>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
