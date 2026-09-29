const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../public/sw.js'), 'utf8');

function worker(windows = [], overrides = {}) {
  const events = {}, shown = [], opened = [];
  const clients = { matchAll: async () => windows, openWindow: async url => opened.push(url), ...overrides };
  const self = {
    location: { origin: 'https://bambuddy.example' },
    addEventListener: (name, handler) => { events[name] = handler; },
    registration: { showNotification: async (...args) => shown.push(args) },
  };
  vm.runInNewContext(source, { self, clients, URL });
  return { events, shown, opened };
}

test('actual app worker displays visible push payload', async () => {
  const w = worker();
  let pending;
  w.events.push({ data: { json: () => ({ title: 'Test', body: 'Arrived' }) }, waitUntil: p => { pending = p; } });
  await pending;
  assert.equal(w.shown[0][0], 'Test');
  assert.equal(w.shown[0][1].body, 'Arrived');
  assert.equal(w.shown[0][1].icon, '/img/android-chrome-192x192.png');
  assert.equal(w.shown[0][1].badge, '/img/notification-badge.png');
});

test('Android badge is a dedicated 96px transparent PNG and is precached', () => {
  const badge = fs.readFileSync(path.join(__dirname, '../public/img/notification-badge.png'));
  assert.deepEqual([...badge.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  assert.equal(badge.readUInt32BE(16), 96);
  assert.equal(badge.readUInt32BE(20), 96);
  assert.equal(badge[25], 6, 'RGBA preserves the background and letter cutouts');
  const assets = vm.runInNewContext(source + '\nSTATIC_ASSETS', {
    self: { addEventListener() {} },
  });
  assert.ok(assets.includes('/img/notification-badge.png'));
});

test('malformed and missing data still produce a visible notification', async () => {
  for (const data of [undefined, { json: () => null }, { json: () => { throw Error(); } }]) {
    const w = worker();
    let pending;
    w.events.push({ data, waitUntil: p => { pending = p; } });
    await pending;
    assert.equal(w.shown[0][0], 'Bambuddy');
  }
});

test('click rejects external origins and API/action URLs', async () => {
  for (const url of ['https://evil.example', '//evil.example', '/api/v1/printers', '/api%2Fv1%2Fprinters', 'javascript:alert(1)']) {
    const w = worker();
    let pending;
    w.events.notificationclick({ notification: { data: { url }, close() {} }, waitUntil: p => { pending = p; } });
    await pending;
    assert.deepEqual(w.opened, ['https://bambuddy.example/']);
  }
});

test('click focuses before navigating only an exact same-origin window', async () => {
  const navigation = [];
  const w = worker([
    { url: 'https://evil.example/https://bambuddy.example', focus: () => assert.fail('foreign window') },
    { url: 'https://bambuddy.example/settings', navigate: async url => { navigation.push(url); return {}; }, focus: async () => navigation.push('focus') },
  ]);
  let pending;
  w.events.notificationclick({ notification: { data: { url: '/queue' }, close() {} }, waitUntil: p => { pending = p; } });
  await pending;
  assert.deepEqual(navigation, ['focus', 'https://bambuddy.example/queue']);
  assert.equal(w.opened.length, 0);
});

async function click(w, url = '/') {
  let pending;
  w.events.notificationclick({ notification: { data: { url }, close() {} }, waitUntil: p => { pending = p; } });
  await pending;
}

test('click focuses an already matching page without reloading it', async () => {
  let focused = false;
  const w = worker([{
    url: 'https://bambuddy.example/',
    focus: async () => { focused = true; },
    navigate: () => assert.fail('unnecessary navigation'),
  }]);
  await click(w);
  assert.ok(focused);
  assert.deepEqual(w.opened, []);
});

test('click opens the app when existing clients cannot be focused or navigated', async () => {
  for (const failure of ['focus', 'navigate', 'null']) {
    const w = worker([{
      url: 'https://bambuddy.example/settings',
      focus: async () => { if (failure === 'focus') throw Error('suspended client'); },
      navigate: async () => { if (failure === 'navigate') throw Error('closed client'); return null; },
    }]);
    await click(w);
    assert.deepEqual(w.opened, ['https://bambuddy.example/']);
  }
});

test('click tries another window when the first matching client is stale', async () => {
  let focused = false;
  const w = worker([
    { url: 'https://bambuddy.example/', focus: async () => { throw Error('closed'); } },
    { url: 'https://bambuddy.example/', focus: async () => { focused = true; } },
  ]);
  await click(w);
  assert.ok(focused);
  assert.deepEqual(w.opened, []);
});

test('click opens the app if listing windows fails', async () => {
  const w = worker([], { matchAll: async () => { throw Error('unavailable'); } });
  await click(w);
  assert.deepEqual(w.opened, ['https://bambuddy.example/']);
});

const registrationSource = fs.readFileSync(path.join(__dirname, '../public/sw-register.js'), 'utf8');

async function openSpoolBuddy(registrations, pathname = '/spoolbuddy') {
  const effects = { unregistered: [], deleted: [], reloads: 0 };
  const regs = registrations.map(({ subscription, lookupFails, unregisters = true }, i) => ({
    pushManager: {
      getSubscription: async () => {
        if (lookupFails) throw new Error('Subscription lookup unavailable');
        return subscription ?? null;
      },
    },
    unregister: async () => {
      effects.unregistered.push(i);
      return unregisters;
    },
  }));
  vm.runInNewContext(registrationSource, {
    navigator: { serviceWorker: { getRegistrations: async () => regs } },
    location: { pathname, reload: () => { effects.reloads++; } },
    caches: { keys: async () => ['bambuddy-cache'], delete: async name => effects.deleted.push(name) },
    console: { warn() {} },
  });
  await new Promise(resolve => setImmediate(resolve));
  return effects;
}

test('opening SpoolBuddy preserves push registrations and their caches without reloading', async () => {
  for (const pathname of ['/spoolbuddy', '/spoolbuddy/settings']) {
    assert.deepEqual(await openSpoolBuddy([{ subscription: {} }], pathname), {
      unregistered: [], deleted: [], reloads: 0,
    });
  }
});

test('SpoolBuddy still removes unsubscribed workers and clears their caches', async () => {
  assert.deepEqual(await openSpoolBuddy([{}, {}]), {
    unregistered: [0, 1], deleted: ['bambuddy-cache'], reloads: 1,
  });
});

test('mixed registrations retain push and do not repeatedly reload SpoolBuddy', async () => {
  assert.deepEqual(await openSpoolBuddy([{ subscription: {} }, {}]), {
    unregistered: [1], deleted: [], reloads: 1,
  });
  assert.deepEqual(await openSpoolBuddy([{ subscription: {} }]), {
    unregistered: [], deleted: [], reloads: 0,
  });
});

test('a failed subscription lookup preserves that registration while cleaning up others', async () => {
  assert.deepEqual(await openSpoolBuddy([{ lookupFails: true }, {}]), {
    unregistered: [1], deleted: [], reloads: 1,
  });
});

test('no registrations or unsuccessful unregistration does not reload or clear caches', async () => {
  assert.deepEqual(await openSpoolBuddy([]), { unregistered: [], deleted: [], reloads: 0 });
  assert.deepEqual(await openSpoolBuddy([{ unregisters: false }]), {
    unregistered: [0], deleted: [], reloads: 0,
  });
});
