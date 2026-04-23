/**
 * slider-shim.js
 * Minimal jQuery plugin shim for the bootstrap-slider API used by prettygcode.js.
 *
 * Supports the subset used by PrettyGCode:
 *   $(el).slider(opts)              — init
 *   $(el).slider("setValue", v)     — set value
 *   $(el).slider("setMax", v)       — update max
 *   $(el).on("slide", fn)           — fires with event.value
 *   $(el).on("slideStart", fn)
 *   $(el).on("slideStop", fn)
 */
(function ($) {
    'use strict';

    $.fn.slider = function (optsOrCmd, cmdArg1, cmdArg2, cmdArg3) {
        return this.each(function () {
            var $el = $(this);
            var data = $el.data('_pgslider');

            // ---------- init ----------
            if (!data || typeof optsOrCmd === 'object') {
                var opts = $.extend({
                    id: null,
                    orientation: 'horizontal',
                    reversed: false,
                    min: 0,
                    max: 100,
                    value: 0,
                }, typeof optsOrCmd === 'object' ? optsOrCmd : {});

                // Build the DOM
                var isVertical = opts.orientation === 'vertical';
                var trackHtml =
                    '<div class="slider' + (isVertical ? ' slider-vertical' : '') + '"' +
                    (opts.id ? ' id="' + opts.id + '"' : '') + '>' +
                    '<div class="slider-track"><div class="slider-selection"></div></div>' +
                    '<div class="slider-handle round">0</div>' +
                    '</div>';
                $el.html(trackHtml);

                var $slider = $el.find('.slider');
                var $handle = $el.find('.slider-handle');
                var $selection = $el.find('.slider-selection');
                var isDragging = false;

                data = {
                    opts: opts,
                    $slider: $slider,
                    $handle: $handle,
                    $selection: $selection,
                };
                $el.data('_pgslider', data);

                function pct(v) {
                    var range = data.opts.max - data.opts.min;
                    if (range === 0) return 0;
                    var p = (v - data.opts.min) / range * 100;
                    return opts.reversed ? 100 - p : p;
                }

                function updateUI(val) {
                    var p = pct(val);
                    $handle.text(val);
                    if (isVertical) {
                        $handle.css({ top: p + '%', bottom: '' });
                        $selection.css({ height: (100 - p) + '%', top: p + '%' });
                    } else {
                        $handle.css({ left: p + '%' });
                        $selection.css({ width: p + '%' });
                    }
                }

                data.updateUI = updateUI;
                updateUI(opts.value);
                data.opts.value = opts.value;

                // Mouse interaction
                function getValueFromEvent(e) {
                    var offset = $slider.offset();
                    var range = data.opts.max - data.opts.min;
                    var p;
                    if (isVertical) {
                        var h = $slider.height();
                        p = (e.pageY - offset.top) / h;
                    } else {
                        var w = $slider.width();
                        p = (e.pageX - offset.left) / w;
                    }
                    p = Math.max(0, Math.min(1, p));
                    if (opts.reversed) p = 1 - p;
                    return Math.round(data.opts.min + p * range);
                }

                $slider.on('mousedown', function (e) {
                    isDragging = true;
                    var val = getValueFromEvent(e);
                    data.opts.value = val;
                    updateUI(val);
                    var ev = $.Event('slideStart'); ev.value = val;
                    $el.trigger(ev);
                    e.preventDefault();
                });

                $(document).on('mousemove.pgslider_' + $el.attr('id'), function (e) {
                    if (!isDragging) return;
                    var val = getValueFromEvent(e);
                    data.opts.value = val;
                    updateUI(val);
                    var ev = $.Event('slide'); ev.value = val;
                    $el.trigger(ev);
                });

                $(document).on('mouseup.pgslider_' + $el.attr('id'), function (e) {
                    if (!isDragging) return;
                    isDragging = false;
                    var val = getValueFromEvent(e);
                    data.opts.value = val;
                    updateUI(val);
                    var ev = $.Event('slideStop'); ev.value = val;
                    $el.trigger(ev);
                });

                return;
            }

            // ---------- commands ----------
            if (optsOrCmd === 'setValue') {
                data.opts.value = cmdArg1;
                data.updateUI(cmdArg1);
                // prettygcode.js calls slider('setValue', N, false, true) after loading
                // — the third arg means "trigger the slide event so listeners update state"
                if (cmdArg3) {
                    var ev = $.Event('slide'); ev.value = cmdArg1; $el.trigger(ev);
                }
            } else if (optsOrCmd === 'setMax') {
                data.opts.max = cmdArg1;
                data.updateUI(data.opts.value);
            }
        });
    };
}(jQuery));
