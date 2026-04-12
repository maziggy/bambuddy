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

    const PLUGIN_KEY = 'prettygcode';
    const API_BASE = '/api/v1';
    const ASSETS_BASE = `${API_BASE}/plugins/${PLUGIN_KEY}/assets`;

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
            // OctoPrint file download path
            url = url.replace(
                /^downloads\/files\/local\/__bambuddy_file_(\d+)$/,
                API_BASE + '/library/files/$1/download'
            );
            // OctoPrint plugin static asset path
            url = url.replace(
                /^plugin\/prettygcode\/static\//,
                ASSETS_BASE + '/'
            );

            // Inject auth header for Bambuddy API calls
            if (url.startsWith(API_BASE)) {
                var hdrs = authHeaders();
                init = init || {};
                init.headers = Object.assign({}, hdrs, init.headers || {});
            }

            resource = (typeof resource === 'string') ? url
                     : Object.assign({}, resource, { url: url });
        }

        var promise = _originalFetch(resource, init);

        // Tee GCode file downloads so we can parse layer data for sync + nozzle animation.
        // prettygcode.js calls loadGcode() which fetches the rewritten URL — intercept here.
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
            url = url.replace(/^plugin\/prettygcode\/static\//, ASSETS_BASE + '/');
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
        var wsUrl = proto + '//' + location.host + API_BASE + '/ws' +
                    (token ? '?token=' + encodeURIComponent(token) : '');

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
                Object.assign(printers[i], data, { id: printerId });
                found = true;
                break;
            }
        }
        if (!found) printers.push(Object.assign({ id: printerId }, data));

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
                    date: Math.floor(Date.now() / 1000),
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
        gcodeLayerMap = null;   // cleared here; re-populated when fetch() intercept fires
        lastFedLayer = -1;
        updateFilenameDisplay(filename);
        // Trigger prettygcode.js's updateJob by faking a job change
        if (viewModel && viewModel.fromCurrentData) {
            viewModel.fromCurrentData({
                job: {
                    file: {
                        path: '__bambuddy_file_' + fileId,
                        date: Date.now() / 1000 + Math.random(),  // force change detection
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
            apiFetch('/library/files?sort_by=updated_at&sort_dir=desc', {})
                .then(function (r) { return r.json(); })
                .then(function (files) {
                    if (!Array.isArray(files)) return;
                    allFiles = files.filter(function (f) {
                        return f.filename && f.filename.toLowerCase().endsWith('.gcode');
                    });
                    render(allFiles);
                })
                .catch(function () {
                    list.innerHTML = '<div style="color:#f88;padding:4px 6px;font-size:12px;">Failed to load files</div>';
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
    // 12. Settings
    // -------------------------------------------------------------------------
    function loadPluginSettings() {
        apiFetch('/plugins/' + PLUGIN_KEY + '/settings', {})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (s) {
                if (!s) return;
                if (s.dark_mode !== undefined) fakeSettings.plugins.prettygcode.darkMode(s.dark_mode);
            })
            .catch(function () {});
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

        loadPluginSettings();
        buildFilePicker();
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
                    printers.push({
                        id: p.id,
                        name: p.name,
                        model: p.model,
                        state: 'IDLE',
                        progress: 0,
                    });
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
    }

    // Run after all scripts have loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { setTimeout(init, 200); });
    } else {
        setTimeout(init, 200);
    }

    // -------------------------------------------------------------------------
    // Public API
    // -------------------------------------------------------------------------
    window.BambuddyPrettyGCode = {
        loadFile: loadFileById,
        getViewModel: function () { return viewModel; },
    };

})();
