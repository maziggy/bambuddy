/**
 * bambuddy_adapter.js
 * Bridges OctoPrint-PrettyGCode to Bambuddy's API.
 *
 * Load this BEFORE prettygcode.js. It provides:
 *   - OCTOPRINT_VIEWMODELS shim
 *   - Minimal KnockoutJS observable shim (ko.observable)
 *   - fetch() + XHR interceptors for path rewriting
 *   - Bambuddy WebSocket → fromCurrentData bridge
 *   - File picker backed by Bambuddy's library API
 *   - Settings load/save via plugin settings endpoint
 *
 * What works:
 *   - Full 3D GCode visualisation
 *   - Dark mode and all dat.GUI settings
 *   - File selection from Bambuddy's file library
 *   - Print progress highlight (% based)
 *   - Auto-load currently printing file
 *
 * What doesn't work (Bambu hardware limitation):
 *   - Live nozzle animation during printing — Bambu printers do not expose
 *     GCode serial echo logs (Send: G1 X...), so PrintHeadSimulator has no input.
 */

(function () {
    'use strict';

    const API_BASE = '/api/v1';
    const VIEWER_BASE = '/gcode-viewer'; // static assets now served from here

    // -------------------------------------------------------------------------
    // Auth helper
    // -------------------------------------------------------------------------
    function authHeaders() {
        const token = localStorage.getItem('auth_token');
        return token ? { Authorization: 'Bearer ' + token } : {};
    }

    function apiFetch(path, opts) {
        return fetch(API_BASE + path, {
            ...opts,
            headers: { ...authHeaders(), ...(opts && opts.headers) },
            cache: 'no-store',
        });
    }

    // -------------------------------------------------------------------------
    // 1. Minimal KnockoutJS shim  (ko.observable / ko.computed)
    // -------------------------------------------------------------------------
    window.ko = {
        observable: function (initial) {
            var _val = initial;
            var _subs = [];
            var obs = function (newVal) {
                if (arguments.length > 0) {
                    _val = newVal;
                    _subs.forEach(function (cb) { try { cb(newVal); } catch (e) {} });
                }
                return _val;
            };
            obs.subscribe = function (cb) {
                _subs.push(cb);
                return { dispose: function () { _subs = _subs.filter(function (s) { return s !== cb; }); } };
            };
            obs.peek = function () { return _val; };
            return obs;
        },
        computed: function (fn) {
            var obs = window.ko.observable(null);
            try { obs(fn()); } catch (e) {}
            return obs;
        },
        pureComputed: function (fn) { return window.ko.computed(fn); },
        mapping: { fromJS: function (obj) { return obj; } },
    };

    // -------------------------------------------------------------------------
    // 2. OCTOPRINT_VIEWMODELS registration shim
    // -------------------------------------------------------------------------
    window.OCTOPRINT_VIEWMODELS = [];

    // -------------------------------------------------------------------------
    // 3. Fake OctoPrint settings / printer profile / login viewmodels
    // -------------------------------------------------------------------------
    var fakeSettings = {
        webcam: {
            streamUrl: ko.observable(''),
            flipH: ko.observable(false),
            flipV: ko.observable(false),
            rotate90: ko.observable(false),
        },
        plugins: {
            prettygcode: {
                darkMode: ko.observable(false),
            },
        },
    };

    // Bed sizes for common Bambu models (mm)
    var BAMBU_BED_SIZES = {
        'X1':       { width: 256, depth: 256, height: 256 },
        'X1C':      { width: 256, depth: 256, height: 256 },
        'X1E':      { width: 256, depth: 256, height: 256 },
        'P1S':      { width: 256, depth: 256, height: 256 },
        'P1P':      { width: 256, depth: 256, height: 256 },
        'A1':       { width: 300, depth: 300, height: 300 },
        'A1 Mini':  { width: 180, depth: 180, height: 180 },
    };
    var DEFAULT_BED = { width: 256, depth: 256, height: 256 };

    var currentBed = Object.assign({}, DEFAULT_BED);

    function makeFakeProfileData(bed) {
        return {
            volume: {
                width:       ko.observable(bed.width),
                depth:       ko.observable(bed.depth),
                height:      ko.observable(bed.height),
                origin:      ko.observable('lowerleft'),
                formFactor:  ko.observable('rectangular'),
                // Make custom_box a function so prettygcode.js uses width()/depth()/height()
                custom_box:  function () { return false; },
            },
        };
    }

    var fakePrinterProfiles = {
        currentProfileData: ko.observable(makeFakeProfileData(currentBed)),
    };

    var fakeLoginState = {
        isUser:  ko.observable(true),
        isAdmin: ko.observable(false),
    };

    var fakeControl = {};

    // -------------------------------------------------------------------------
    // 4. fetch() interceptor — rewrite OctoPrint paths to Bambuddy
    // -------------------------------------------------------------------------
    var _originalFetch = window.fetch.bind(window);
    window.fetch = function (resource, init) {
        var url = (typeof resource === 'string') ? resource
                : (resource && resource.url) ? resource.url
                : null;

        if (url) {
            // Normalize: strip scheme+host so regexes work on the path regardless
            // of whether the browser resolved a relative URL to absolute.
            var path = url.replace(/^https?:\/\/[^\/]+/, '');
            // Also strip the viewer's own path prefix — the browser resolves relative URLs
            // like 'downloads/files/local/...' to '/gcode-viewer/downloads/...' because
            // the page is served from /gcode-viewer/. The regexes below expect bare paths.
            path = path.replace(/^\/gcode-viewer(?=\/|$)/, '');

            var newPath = path;

            // OctoPrint file download  →  Bambuddy library download
            newPath = newPath.replace(
                /^\/?downloads\/files\/local\/__bambuddy_file_(\d+)$/,
                API_BASE + '/library/files/$1/download'
            );
            // OctoPrint plugin static assets  →  gcode-viewer static files
            newPath = newPath.replace(
                /^\/?plugin\/prettygcode\/static\//,
                VIEWER_BASE + '/'
            );

            if (newPath !== path) {
                url = newPath;
                resource = url; // always pass as string after rewriting
            }

            // Inject auth header for all Bambuddy API calls
            if (url.startsWith(API_BASE)) {
                var hdrs = authHeaders();
                init = init || {};
                init.headers = Object.assign({}, hdrs, init.headers || {});
            }
        }

        var promise = _originalFetch(resource, init);

        // Tee GCode downloads to build the layer map for sync + nozzle animation
        if (url && url.match(/\/library\/files\/\d+\/download/)) {
            promise = promise.then(function (response) {
                var clone = response.clone();
                clone.text().then(function (text) {
                    gcodeLayerMap = parseGcodeLayerMap(text);
                    lastFedLayer = -1;
                    console.log('[PrettyGCode] Parsed ' + gcodeLayerMap.layerOffsets.length +
                                ' layers for sync (' + Math.round(gcodeLayerMap.totalBytes / 1024) + ' KB)');
                }).catch(function (e) {
                    console.warn('[PrettyGCode] GCode layer parse failed:', e);
                });
                return response;
            });
        }

        return promise;
    };

    // -------------------------------------------------------------------------
    // 5. XHR interceptor — rewrite OctoPrint paths (used by THREE.OBJLoader etc.)
    // -------------------------------------------------------------------------
    var _origXHROpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
        if (typeof url === 'string') {
            // Strip host if absolute, then rewrite OctoPrint static asset paths
            var path = url.replace(/^https?:\/\/[^\/]+/, '');
            path = path.replace(/^\/?plugin\/prettygcode\/static\//, VIEWER_BASE + '/');
            url = path;
        }
        var args = Array.prototype.slice.call(arguments);
        args[1] = url;
        return _origXHROpen.apply(this, args);
    };

    // -------------------------------------------------------------------------
    // 6. GCode layer parser
    //
    // Builds a layer map from the raw GCode text so we can:
    //   a) Map layer_num → byte offset in file (drives prettygcode's filepos sync
    //      and layer highlight, same as if OctoPrint were reporting filepos)
    //   b) Extract a set of G0/G1 commands per layer to feed the PrintHeadSimulator
    //      as synthetic "Send: G1 X... Y... Z..." entries, animating the nozzle model.
    //
    // Layer detection mirrors prettygcode.js: a new layer starts on the first extrusion
    // at a Z position we haven't extruded at before.
    // -------------------------------------------------------------------------
    function parseGcodeLayerMap(text) {
        var lines = text.split('\n');
        var layerOffsets = [];  // layerOffsets[i] = byte pos in file where layer i starts
        var layerCmds = [];     // layerCmds[i]    = array of ' G1 X... Y... Z...' strings
        var byteOffset = 0;
        var x = 0, y = 0, z = 0, e = 0;
        var relative = false, relativeE = false;
        var currentLayerZ = null;
        var curCmds = [];

        for (var i = 0; i < lines.length; i++) {
            var raw = lines[i];
            // +1 for the \n that was consumed by split
            var lineBytes = raw.length + 1;

            var cmd = raw.replace(/;.*$/, '').trim();
            if (!cmd) { byteOffset += lineBytes; continue; }

            var parts = cmd.split(/\s+/);
            var g = parts[0].toUpperCase();

            if (g === 'G90') { relative = false; relativeE = false; }
            else if (g === 'G91') { relative = true; relativeE = true; }
            else if (g === 'M82') { relativeE = false; }
            else if (g === 'M83') { relativeE = true; }
            else if (g === 'G92') {
                // coordinate reset
                for (var p = 1; p < parts.length; p++) {
                    var k0 = parts[p][0].toUpperCase();
                    var v0 = parseFloat(parts[p].slice(1));
                    if (!isNaN(v0)) {
                        if (k0 === 'X') x = v0;
                        else if (k0 === 'Y') y = v0;
                        else if (k0 === 'Z') z = v0;
                        else if (k0 === 'E') e = v0;
                    }
                }
            } else if (g === 'G0' || g === 'G1') {
                var nx = x, ny = y, nz = z, ne = e;
                var hasE = false;
                for (var p = 1; p < parts.length; p++) {
                    if (!parts[p]) continue;
                    var k1 = parts[p][0].toUpperCase();
                    var v1 = parseFloat(parts[p].slice(1));
                    if (isNaN(v1)) continue;
                    if (k1 === 'X') nx = relative ? x + v1 : v1;
                    else if (k1 === 'Y') ny = relative ? y + v1 : v1;
                    else if (k1 === 'Z') nz = relative ? z + v1 : v1;
                    else if (k1 === 'E') { ne = relativeE ? e + v1 : v1; hasE = true; }
                }

                // New layer: first extrusion at a new Z (same logic as prettygcode.js)
                if (hasE && ne > e && nz !== currentLayerZ) {
                    currentLayerZ = nz;
                    if (curCmds.length > 0) layerCmds.push(curCmds);
                    else if (layerOffsets.length > 0) layerCmds.push([]); // gap layer
                    curCmds = [];
                    layerOffsets.push(byteOffset);
                }

                // Record movement commands for nozzle sim (keep arrays small — max 500/layer)
                if ((hasE || nz !== z) && curCmds.length < 500) {
                    curCmds.push(' G1 X' + nx.toFixed(3) +
                                      ' Y' + ny.toFixed(3) +
                                      ' Z' + nz.toFixed(3));
                }

                x = nx; y = ny; z = nz; e = ne;
            }

            byteOffset += lineBytes;
        }
        if (curCmds.length > 0) layerCmds.push(curCmds);

        return {
            layerOffsets: layerOffsets,
            layerCmds:    layerCmds,
            totalBytes:   byteOffset,
        };
    }

    // -------------------------------------------------------------------------
    // 8. State
    // -------------------------------------------------------------------------
    var viewModel = null;
    var currentFileId = null;
    var currentFilename = null;
    var currentFileDate = 0; // stable epoch — only changes when a new file is loaded
    var ws = null;
    var wsReconnectTimer = null;
    var printers = [];            // [{id, name, model, state, progress, subtask_name}]
    var selectedPrinterId = null;
    var gcodeLayerMap = null;     // parsed layer data: {layerOffsets, layerCmds, totalBytes}
    var lastFedLayer = -1;        // last layer_num whose commands we fed to printHeadSim

    // -------------------------------------------------------------------------
    // 9. Bambuddy WebSocket
    // -------------------------------------------------------------------------
    function connectWebSocket() {
        var token = localStorage.getItem('auth_token');
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Do NOT put the token in the URL — it would appear in server logs.
        // The WebSocket endpoint is currently unauthenticated server-side;
        // all sensitive calls go through authenticated fetch() instead.
        var wsUrl = proto + '//' + location.host + API_BASE + '/ws';

        ws = new WebSocket(wsUrl);

        ws.onopen = function () {
            console.log('[PrettyGCode] Connected to Bambuddy WebSocket');
        };

        ws.onmessage = function (event) {
            try {
                var msg = JSON.parse(event.data);
                if (msg.type === 'printer_status') {
                    handlePrinterStatus(msg.printer_id, msg.data);
                }
            } catch (e) {}
        };

        ws.onclose = function () {
            clearTimeout(wsReconnectTimer);
            wsReconnectTimer = setTimeout(connectWebSocket, 3000);
        };

        ws.onerror = function () {
            ws.close();
        };
    }

    function bambuStateToOctoState(bambuState) {
        var map = {
            RUNNING:  'Printing',
            PAUSE:    'Paused',
            FAILED:   'Error',
            FINISH:   'Operational',
            IDLE:     'Operational',
        };
        return map[bambuState] || 'Operational';
    }

    function handlePrinterStatus(printerId, data) {
        // Update printer list entry
        var found = false;
        for (var i = 0; i < printers.length; i++) {
            if (printers[i].id === printerId) {
                // Allowlist to prevent prototype pollution from crafted WS messages
                var allowed2 = ['name', 'state', 'progress', 'layer_num', 'subtask_name', 'gcode_file', 'camera_url', 'model'];
                allowed2.forEach(function (k) { if (k in data) printers[i][k] = data[k]; });
                found = true;
                break;
            }
        }
        if (!found) {
            // Only copy known, safe keys — avoids prototype pollution from a crafted WS message
            var allowed = ['name', 'state', 'progress', 'layer_num', 'subtask_name', 'gcode_file', 'camera_url', 'model'];
            var entry = { id: printerId };
            allowed.forEach(function (k) { if (k in data) entry[k] = data[k]; });
            printers.push(entry);
        }

        updatePrinterSelector();

        // Only feed data for the selected printer
        if (selectedPrinterId !== null && printerId !== selectedPrinterId) return;
        if (selectedPrinterId === null && printers.length > 0) {
            selectedPrinterId = printers[0].id;
        }

        if (!viewModel) return;

        var printer = null;
        for (var j = 0; j < printers.length; j++) {
            if (printers[j].id === printerId) { printer = printers[j]; break; }
        }
        if (!printer) return;

        // Update bed size from printer model
        var bedKey = (printer.model || '').toUpperCase();
        for (var modelName in BAMBU_BED_SIZES) {
            if (bedKey.indexOf(modelName.toUpperCase()) !== -1) {
                currentBed = BAMBU_BED_SIZES[modelName];
                break;
            }
        }
        // Replace the entire profile data so the subscribe() fires
        fakePrinterProfiles.currentProfileData(makeFakeProfileData(currentBed));

        // Auto-load currently printing file if it changed
        var subtask = printer.subtask_name || printer.gcode_file || '';
        if (subtask && subtask !== currentFilename) {
            currentFilename = subtask;
            tryAutoLoadPrintingFile(subtask);
        }

        // Update webcam URL
        if (printer.camera_url) {
            fakeSettings.webcam.streamUrl(printer.camera_url);
        }

        feedCurrentData(printer);
    }

    function feedCurrentData(printer) {
        if (!viewModel || !viewModel.fromCurrentData) return;
        var octoState = bambuStateToOctoState(printer.state || 'IDLE');
        var isPrinting = octoState === 'Printing' || octoState === 'Paused';

        // --- Layer sync via filepos -------------------------------------------
        // prettygcode.js calls gcodeProxy.syncGcodeObjToFilePos(curPrintFilePos) each
        // animation frame when printing + syncToProgress is on.  Pass the byte offset
        // of the current layer so the highlight advances correctly.
        var filepos = null;
        var logs = [];

        if (gcodeLayerMap && isPrinting) {
            // Bambu layer_num is 1-based; our layerOffsets array is 0-based.
            var layerIdx = Math.max(0, (printer.layer_num || 1) - 1);
            layerIdx = Math.min(layerIdx, gcodeLayerMap.layerOffsets.length - 1);
            filepos = gcodeLayerMap.layerOffsets[layerIdx] || 0;

            // --- Nozzle animation via synthetic Send: commands -------------------
            // PrintHeadSimulator.addCommand() expects "Send: G1 X... Y... Z..." entries.
            // Feed the movement commands for the current layer once per layer change.
            // The simulator interpolates them over real time, animating the nozzle model.
            if (layerIdx !== lastFedLayer && gcodeLayerMap.layerCmds[layerIdx]) {
                lastFedLayer = layerIdx;
                var cmds = gcodeLayerMap.layerCmds[layerIdx];
                // PrintHeadSimulator buffer is capped at 1000; feed at most 400 commands
                // so there's room for the sim to drain before more arrive.
                logs = cmds.slice(0, 400).map(function (c) { return 'Send:' + c; });
            }
        }

        viewModel.fromCurrentData({
            job: {
                file: {
                    path: currentFileId ? ('__bambuddy_file_' + currentFileId) : null,
                    date: currentFileDate,
                },
                estimatedPrintTime: null,
            },
            state: {
                text: octoState,
                flags: { printing: octoState === 'Printing', paused: octoState === 'Paused' },
            },
            progress: {
                filepos: filepos,
                completion: (printer.progress || 0) / 100,
                printTime: null,
            },
            currentZ: null,
            logs: logs,
        });
    }

    // -------------------------------------------------------------------------
    // 8. Auto-load file when printer starts printing
    // -------------------------------------------------------------------------
    function tryAutoLoadPrintingFile(filename) {
        // Search the library for a matching .gcode file
        apiFetch('/library/files?sort_by=updated_at&sort_dir=desc', {})
            .then(function (r) { return r.json(); })
            .then(function (files) {
                if (!Array.isArray(files)) return;
                var match = files.find(function (f) {
                    return f.filename === filename ||
                           f.filename === filename + '.gcode' ||
                           f.filename.replace(/\.gcode$/, '') === filename.replace(/\.gcode$/, '');
                });
                if (match) loadFileById(match.id, match.filename, match.file_size);
            })
            .catch(function () {});
    }

    // -------------------------------------------------------------------------
    // 9. File loading
    // -------------------------------------------------------------------------
    function loadFileById(fileId, filename, fileSize) {
        currentFileId = fileId;
        currentFilename = filename;
        currentFileDate = Date.now(); // new stable date so prettygcode loads exactly once
        gcodeLayerMap = null;   // cleared here; re-populated when fetch() intercept fires
        lastFedLayer = -1;
        stopPlayback(true);
        updateFilenameDisplay(filename);
        // Enable play button once a file is loaded
        var playBtn = document.getElementById('bb-play-btn');
        if (playBtn) playBtn.disabled = false;
        // Trigger prettygcode.js's updateJob — date must match currentFileDate exactly
        // so subsequent feedCurrentData calls don't re-trigger the download
        if (viewModel && viewModel.fromCurrentData) {
            viewModel.fromCurrentData({
                job: {
                    file: {
                        path: '__bambuddy_file_' + fileId,
                        date: currentFileDate,
                    },
                    estimatedPrintTime: null,
                },
                state: { text: 'Operational', flags: { printing: false } },
                progress: { filepos: null, completion: 0 },
                currentZ: null,
                logs: [],
            });
        }
    }

    function updateFilenameDisplay(filename) {
        var el = document.getElementById('bb-current-file');
        if (el) el.textContent = filename || '— no file loaded —';
    }

    // -------------------------------------------------------------------------
    // 10. File picker
    // -------------------------------------------------------------------------
    function buildFilePicker() {
        var container = document.getElementById('bb-file-picker');
        if (!container) return;

        var input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Search .gcode files…';
        input.className = 'bb-search';
        input.style.cssText = 'width:100%;padding:4px 8px;background:#333;border:1px solid #555;color:#fff;border-radius:4px;margin-bottom:4px;box-sizing:border-box;';

        var list = document.createElement('div');
        list.style.cssText = 'max-height:180px;overflow-y:auto;';

        container.appendChild(input);
        container.appendChild(list);

        var allFiles = [];

        function render(files) {
            list.innerHTML = '';
            if (!files.length) {
                list.innerHTML = '<div style="color:#888;padding:4px 6px;font-size:12px;">No .gcode files found in library</div>';
                return;
            }
            files.forEach(function (f) {
                var row = document.createElement('div');
                row.textContent = f.filename;
                row.title = f.filename;
                row.style.cssText = 'padding:4px 6px;cursor:pointer;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-radius:3px;';
                row.addEventListener('mouseenter', function () { row.style.background = '#444'; });
                row.addEventListener('mouseleave', function () { row.style.background = ''; });
                row.addEventListener('click', function () {
                    loadFileById(f.id, f.filename, f.file_size);
                    // Close picker
                    container.classList.toggle('bb-open', false);
                });
                list.appendChild(row);
            });
        }

        function loadFiles() {
            list.innerHTML = '<div style="color:#aaa;padding:4px 6px;font-size:12px;">Loading files…</div>';
            // include_root=false returns files from ALL folders, not just root level
            apiFetch('/library/files?include_root=false', {})
                .then(function (r) { return r.json(); })
                .then(function (files) {
                    if (!Array.isArray(files)) {
                        list.innerHTML = '<div style="color:#f88;padding:4px 6px;font-size:12px;">Failed to load files</div>';
                        return;
                    }
                    allFiles = files.filter(function (f) {
                        return f.filename && f.filename.toLowerCase().endsWith('.gcode');
                    });
                    render(allFiles);
                })
                .catch(function () {
                    list.innerHTML = '<div style="color:#f88;padding:4px 6px;font-size:12px;">Failed to load files — check auth token</div>';
                });
        }

        input.addEventListener('input', function () {
            var q = input.value.toLowerCase();
            render(q ? allFiles.filter(function (f) { return f.filename.toLowerCase().indexOf(q) !== -1; }) : allFiles);
        });

        loadFiles();
    }

    // -------------------------------------------------------------------------
    // 11. Printer selector
    // -------------------------------------------------------------------------
    function updatePrinterSelector() {
        var sel = document.getElementById('bb-printer-select');
        if (!sel) return;
        var current = sel.value;
        sel.innerHTML = '';
        printers.forEach(function (p) {
            var opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = (p.name || ('Printer ' + p.id)) + (p.state ? ' [' + p.state + ']' : '');
            sel.appendChild(opt);
        });
        if (current) sel.value = current;
        if (!sel.value && printers.length) {
            sel.value = printers[0].id;
            selectedPrinterId = printers[0].id;
        }
    }

    // -------------------------------------------------------------------------
    // 13. Initialise after DOM + scripts are ready
    // -------------------------------------------------------------------------
    function init() {
        // Find the ViewModel registration that prettygcode.js pushed
        var reg = null;
        for (var i = 0; i < window.OCTOPRINT_VIEWMODELS.length; i++) {
            if (window.OCTOPRINT_VIEWMODELS[i].construct) {
                reg = window.OCTOPRINT_VIEWMODELS[i];
                break;
            }
        }

        if (!reg) {
            console.error('[PrettyGCode] No ViewModel found in OCTOPRINT_VIEWMODELS');
            return;
        }

        try {
            viewModel = new reg.construct([
                fakeSettings,
                fakeLoginState,
                fakePrinterProfiles,
                fakeControl,
            ]);
        } catch (e) {
            console.error('[PrettyGCode] ViewModel constructor failed:', e);
            return;
        }

        if (viewModel.onAfterBinding) {
            try { viewModel.onAfterBinding(); } catch (e) {}
        }

        // Trigger tab activation — this calls onTabChange which initialises the Three.js scene
        if (viewModel.onTabChange) {
            try { viewModel.onTabChange('#tab_plugin_prettygcode', ''); } catch (e) {
                console.error('[PrettyGCode] onTabChange failed:', e);
            }
        }

        connectWebSocket();

        // Wire up printer selector
        var sel = document.getElementById('bb-printer-select');
        if (sel) {
            sel.addEventListener('change', function () {
                selectedPrinterId = parseInt(sel.value, 10) || null;
            });
        }

        // Load initial printer list
        apiFetch('/printers', {})
            .then(function (r) { return r.json(); })
            .then(function (list) {
                if (!Array.isArray(list)) return;
                list.forEach(function (p) {
                    // Find existing entry (WS may have pushed one before API returned)
                    var existing = null;
                    for (var i = 0; i < printers.length; i++) {
                        if (printers[i].id === p.id) { existing = printers[i]; break; }
                    }
                    if (existing) {
                        // Fill in name/model that WS status messages don't carry
                        if (p.name)  existing.name  = p.name;
                        if (p.model) existing.model = p.model;
                    } else {
                        printers.push({ id: p.id, name: p.name, model: p.model, state: 'IDLE', progress: 0 });
                    }
                });
                updatePrinterSelector();
                // Try to get bed size from first printer model
                if (list.length > 0 && list[0].model) {
                    var m = list[0].model.toUpperCase();
                    for (var modelName in BAMBU_BED_SIZES) {
                        if (m.indexOf(modelName.toUpperCase()) !== -1) {
                            currentBed = BAMBU_BED_SIZES[modelName];
                            fakePrinterProfiles.currentProfileData(makeFakeProfileData(currentBed));
                            break;
                        }
                    }
                }
                if (list.length > 0) selectedPrinterId = list[0].id;
            })
            .catch(function () {});

        console.log('[PrettyGCode] Bambuddy adapter initialised');

        // Wire up playback controls
        var playBtn = document.getElementById('bb-play-btn');
        var speedSel = document.getElementById('bb-play-speed');
        if (playBtn) {
            playBtn.addEventListener('click', function () {
                if (isPlaying) stopPlayback();
                else startPlayback();
            });
        }
        if (speedSel) {
            speedSel.addEventListener('change', function () {
                layersPerTick = parseInt(speedSel.value, 10) || 1;
                // Restart if already playing so speed takes effect immediately
                if (isPlaying) { stopPlayback(); startPlayback(); }
            });
        }
    }

    // -------------------------------------------------------------------------
    // 14. Playback engine
    // -------------------------------------------------------------------------
    var isPlaying = false;
    var playInterval = null;
    var layersPerTick = 1;   // layers advanced per 50 ms tick
    var TICK_MS = 50;        // ~20 fps

    function getSlider() { return $('#myslider-vertical'); }

    function startPlayback() {
        var $sl = getSlider();
        if (!$sl.length) return;
        var data = $sl.data('_pgslider');
        if (!data) return;

        var max = data.opts.max || 0;
        if (max === 0) return;

        // Restart from beginning if already at the end
        var cur = data.opts.value || 0;
        if (cur >= max) cur = 0;

        // Suppress live-print sync while playing
        var evStart = $.Event('slideStart'); evStart.value = cur; $sl.trigger(evStart);

        _setSliderLayer($sl, cur);

        isPlaying = true;
        _updatePlayBtn();

        playInterval = setInterval(function () {
            var d = getSlider().data('_pgslider');
            if (!d) { stopPlayback(); return; }
            var next = (d.opts.value || 0) + layersPerTick;
            if (next >= d.opts.max) {
                next = d.opts.max;
                _setSliderLayer(getSlider(), next);
                stopPlayback(/* skipEvStop */ false);
                return;
            }
            _setSliderLayer(getSlider(), next);
        }, TICK_MS);
    }

    function stopPlayback(skipEvStop) {
        if (playInterval) { clearInterval(playInterval); playInterval = null; }
        isPlaying = false;
        _updatePlayBtn();
        if (!skipEvStop) {
            var $sl = getSlider();
            if ($sl.length) {
                var d = $sl.data('_pgslider');
                var evStop = $.Event('slideStop');
                evStop.value = d ? d.opts.value : 0;
                $sl.trigger(evStop);
            }
        }
    }

    function _setSliderLayer($sl, layer) {
        $sl.slider('setValue', layer);
        var ev = $.Event('slide'); ev.value = layer; $sl.trigger(ev);
        $sl.find('.slider-handle').text(layer);
    }

    function _updatePlayBtn() {
        var btn = document.getElementById('bb-play-btn');
        if (btn) btn.textContent = isPlaying ? '⏸' : '▶';
    }

    // Run after all scripts have loaded.
    // buildFilePicker() runs immediately at DOM-ready — independent of viewmodel
    // init so the file picker is always functional even if prettygcode fails.
    // init() (viewmodel + 3D canvas) runs 200 ms later to let prettygcode.js
    // finish its own synchronous setup first.
    function onDomReady() {
        // Wire file-picker button — MUST be here (not an inline <script>) because
        // the CSP on this page allows script-src 'self' but NOT 'unsafe-inline',
        // so inline <script> blocks are blocked by the browser.
        var fileBtn = document.getElementById('bb-file-btn');
        var picker  = document.getElementById('bb-file-picker');
        if (fileBtn && picker) {
            fileBtn.addEventListener('click', function (e) {
                picker.classList.toggle('bb-open');
                e.stopPropagation();
            });
            // Clicking outside the picker closes it
            document.addEventListener('click', function () {
                picker.classList.remove('bb-open');
            });
            // Clicks inside the picker don't close it
            picker.addEventListener('click', function (e) {
                e.stopPropagation();
            });
        }

        buildFilePicker();
        setTimeout(init, 200);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onDomReady);
    } else {
        onDomReady();
    }

    // -------------------------------------------------------------------------
    // Public API
    // -------------------------------------------------------------------------
    window.BambuddyPrettyGCode = {
        loadFile: loadFileById,
        getViewModel: function () { return viewModel; },
        play: startPlayback,
        stop: stopPlayback,
    };

})();
